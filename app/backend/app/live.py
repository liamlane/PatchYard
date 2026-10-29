"""Live editing of a running lab: add/remove devices and cables without a redeploy.

Devices are started the way containerlab starts a `linux` node (same name, labels,
mgmt network, binds, privileged, exec commands), so `containerlab destroy` still finds
and removes them. Cables are veth pairs created in the host namespace, moved into each
container and renamed (exactly what containerlab does at deploy). After a cable lands,
the device on each end gets a per-type hook so the new port works straight away:
switch ports join the bridge with their VLAN config, PCs/servers get their address,
routers get MPLS input enabled. The lab file is the source of truth, so a later
redeploy produces the same result.
"""
import secrets, shlex
from pathlib import Path
from . import labs, netinfo, settings, devices, hardware, impair, l3, sandbox
from .util import run, dsh, cname, check_if


async def _ctr(lab, node):
    return (await netinfo.containers()).get((lab, node))


async def add_node(labd: dict, node_id: str) -> dict:
    node = next((n for n in labd["nodes"] if n["id"] == node_id), None)
    if not node:
        raise ValueError(f"{node_id} not in lab")
    if node.get("raw"):
        raise ValueError("imported devices need a redeploy")
    c = await _ctr(labd["name"], node_id)
    if c and c["state"] == "running":
        return {"ok": True, "output": f"{node_id} already running"}
    if c:
        await run("docker", "rm", "-f", c["name"])
    labd = labs.ensure_subnet(labd)
    topo = labs.generate(labd)              # writes per-node files and the updated topology
    spec = labs.node_spec(labd, node)
    wd = labs.workdir(labd)
    net = (sandbox.mgmt(labd)["network"] if sandbox.active(labd) else
           labd.get("mgmt_network") or settings.get("lab_defaults.mgmt_network"))
    args = ["docker", "run", "-d", "--name", cname(labd["name"], node_id), "--hostname", node_id, *sandbox.docker_args(spec),
            "--network", net,
            "--label", f"containerlab={labd['name']}", "--label", f"clab-node-name={node_id}",
            "--label", "clab-node-kind=linux", "--label", f"clab-topo-file={topo}",
            "--label", f"clab-node-lab-dir={wd / ('clab-' + labd['name']) / node_id}", "--label", "netlab-live=1"]
    for b in spec.get("binds", []):
        src, _, dst = b.partition(":")
        p = Path(src) if Path(src).is_absolute() else (wd / src)
        args += ["-v", f"{p}:{dst}"]
    args.append(spec["image"])
    if spec.get("cmd"):
        args += shlex.split(spec["cmd"])
    rc, out, err = await run(*args, timeout=600)
    if rc:
        return {"ok": False, "output": (err or out).strip()}
    log = [f"started {cname(labd['name'], node_id)}"]
    for cmd in spec.get("exec", []):
        rc, o, e = await run("docker", "exec", cname(labd["name"], node_id), *shlex.split(cmd), timeout=300)
        if rc and "eth1" not in (o + e):      # eth1 failures are expected until a cable is added
            log.append(f"exec '{cmd}': {(e or o).strip()[:200]}")
    log += await sandbox.fixups(labd, {node_id})
    return {"ok": True, "output": "\n".join(log)}


async def remove_node(lab: str, node_id: str) -> dict:
    rc, out, err = await run("docker", "rm", "-f", cname(lab, node_id))
    l3.stop_relay(f"/run/netlab-relay-{lab}-{node_id}.pid")
    return {"ok": rc == 0 or "No such container" in err, "output": (out + err).strip() or f"removed {node_id}"}


def _bridge_ids(labd):
    return {n["id"] for n in labd["nodes"] if (n.get("raw") or {}).get("kind") == "bridge"}


async def _post_link_hook(labd, node_id, ifn):
    node = next((n for n in labd["nodes"] if n["id"] == node_id), None)
    if not node:
        return ""
    lab, t, p = labd["name"], node["type"], node.get("props", {})
    if t == "firewall" and not node.get("raw"):
        rc, o, e = await dsh(lab, node_id, "sh /etc/netlab/fw.sh")
        return f"{node_id}: {ifn} addressed and in its zone" + ("" if rc == 0 else f" ({(e or o).strip()[:120]})")
    if t == "switch" and not node.get("raw"):
        rc, o, e = await dsh(lab, node_id, "sh /usr/local/bin/netlab-switch")
        return f"{node_id}: {ifn} joined the bridge" + ("" if rc == 0 else f" (switch script: {(e or o).strip()[:120]})")
    if t == "router":
        c = await _ctr(lab, node_id)
        if c and c.get("pid"):
            await run("nsenter", "-t", str(c["pid"]), "-n", "sh", "-c",
                      f"[ -e /proc/sys/net/mpls/conf/{ifn}/input ] && echo 1 > /proc/sys/net/mpls/conf/{ifn}/input; true")
        cmds = ["true"]
        for si in p.get("subifs", []) or []:
            if si["parent"] == ifn:
                name = labs.subif_name(si)
                cmds.append(f"ip link show {name} >/dev/null 2>&1 || ip link add link {ifn} name {name} type vlan id {int(si['vlan'])}; ip link set {name} up")
        await dsh(lab, node_id, "; ".join(cmds) + "; true")
        errs = await l3.apply_node(labd, node)        # VRF membership, ACLs, NAT, relay on the new port
        return f"{node_id}: {ifn} up (FRR picks it up automatically)" + (f" ({'; '.join(errs)[:160]})" if errs else "")
    if t in ("pc", "server") and ifn == "eth1" and p.get("ip_mode") in ("static", "dhcp"):
        if p.get("ip_mode") == "static" and not p.get("ip"):
            return ""
        r = await devices.ipconfig(lab, node_id, {"mode": p["ip_mode"], "ip": p.get("ip", ""), "gateway": p.get("gateway", ""), "dns": p.get("dns", "")})
        return f"{node_id}: eth1 addressed" + ("" if r["ok"] else f" ({r['output'][:120]})")
    return ""


async def add_link(labd: dict, link: dict) -> dict:
    ctrs = await netinfo.containers()
    bridges = _bridge_ids(labd)
    mtu = str(settings.get("lab_defaults.link_mtu", 9500))
    tag = secrets.token_hex(3)
    tmp = {"a": f"nla{tag}", "b": f"nlb{tag}"}
    ends = []
    for s in ("a", "b"):
        node, ifn = link[s], check_if(link[f"{s}_if"])
        if node in bridges:
            ends.append({"s": s, "node": node, "if": ifn, "bridge": True, "pid": None})
            continue
        c = ctrs.get((labd["name"], node))
        if not c or c["state"] != "running" or not c["pid"]:
            return {"ok": False, "output": f"{node} is not running - deploy it first"}
        rc, out, _ = await run("nsenter", "-t", str(c["pid"]), "-n", "ip", "link", "show", ifn)
        if rc == 0:
            return {"ok": False, "output": f"{node} already has {ifn}"}
        ends.append({"s": s, "node": node, "if": ifn, "bridge": False, "pid": c["pid"]})
    rc, out, err = await run("ip", "link", "add", tmp["a"], "mtu", mtu, "type", "veth", "peer", "name", tmp["b"], "mtu", mtu)
    if rc:
        return {"ok": False, "output": f"veth create failed: {err.strip()}"}
    log = []
    try:
        for e in ends:
            t = tmp[e["s"]]
            if e["bridge"]:
                # host bridge end keeps a host-side name like containerlab's (e.g. bi-dns1)
                for cmd in (["ip", "link", "set", t, "name", e["if"]], ["ip", "link", "set", e["if"], "master", e["node"]], ["ip", "link", "set", e["if"], "up"]):
                    rc, o, er = await run(*cmd)
                    if rc:
                        raise RuntimeError(f"{' '.join(cmd)}: {er.strip()}")
                continue
            for cmd in (["ip", "link", "set", t, "netns", str(e["pid"])],
                        ["nsenter", "-t", str(e["pid"]), "-n", "ip", "link", "set", t, "name", e["if"]],
                        ["nsenter", "-t", str(e["pid"]), "-n", "ip", "link", "set", e["if"], "up"]):
                rc, o, er = await run(*cmd)
                if rc:
                    raise RuntimeError(f"{' '.join(cmd)}: {er.strip()}")
    except RuntimeError as ex:
        await run("ip", "link", "del", tmp["a"])
        return {"ok": False, "output": str(ex)}
    nodes = {n["id"]: n for n in labd["nodes"]}
    lbl = lambda s: hardware.display(nodes[link[s]], link[f"{s}_if"]) if link[s] in nodes else link[f"{s}_if"]
    log.append(f"wired {link['a']} {lbl('a')} ↔ {link['b']} {lbl('b')}")
    for e in ends:
        if not e["bridge"]:
            h = await _post_link_hook(labd, e["node"], e["if"])
            if h:
                log.append(h)
    if link.get("impair") or hardware.link_kbit(labd, link):
        r = await impair.apply(labd, link, link.get("impair"))
        log.append(r["output"].splitlines()[0] if r["output"] else "")
    return {"ok": True, "output": "\n".join(x for x in log if x)}


async def remove_link(labd: dict, a: str, a_if: str, b: str, b_if: str) -> dict:
    """Deleting one end of a veth removes both ends."""
    ctrs = await netinfo.containers()
    for node, ifn in ((a, a_if), (b, b_if)):
        check_if(ifn)
        if node in _bridge_ids(labd):
            rc, o, e = await run("ip", "link", "del", ifn)
        else:
            c = ctrs.get((labd["name"], node))
            if not c or not c["pid"]:
                continue
            rc, o, e = await run("nsenter", "-t", str(c["pid"]), "-n", "ip", "link", "del", ifn)
        if rc == 0 or "Cannot find device" in e:
            return {"ok": True, "output": f"removed {a} {a_if} ↔ {b} {b_if}"}
    return {"ok": True, "output": "already gone"}


async def power(labd: dict, node_id: str, on: bool) -> dict:
    """Power a device off or on inside a running lab, like the switch on the back of a box.

    Off removes the container: running config that wasn't saved (write memory / Save
    configs) is lost, as on real hardware. On starts it again from the lab file and
    re-plugs every cable whose other end is up, then re-applies link speed, impairments
    and admin-down state."""
    node = next((n for n in labd["nodes"] if n["id"] == node_id), None)
    if not node:
        raise ValueError(f"{node_id} not in lab")
    if node.get("raw"):
        raise ValueError("imported devices can't be power-cycled; redeploy the lab")
    if not on:
        r = await remove_node(labd["name"], node_id)
        labs.patch_props(labd["name"], node_id, lambda p: p.__setitem__("power", "off"))
        return {"ok": r["ok"], "output": f"{node_id} powered off" if r["ok"] else r["output"]}
    labs.patch_props(labd["name"], node_id, lambda p: p.pop("power", None))
    labd = labs.load(labd["name"])
    r = await add_node(labd, node_id)
    if not r["ok"]:
        return r
    log = [f"{node_id} powered on"]
    ctrs = await netinfo.containers()
    bridges = _bridge_ids(labd)
    for l in labd["links"]:
        if node_id not in (l["a"], l["b"]):
            continue
        other = l["b"] if l["a"] == node_id else l["a"]
        oc = ctrs.get((labd["name"], other))
        if other not in bridges and (not oc or oc["state"] != "running"):
            log.append(f"{other} is off: cable to it stays unplugged")
            continue
        x = await add_link(labd, l)
        if not x["ok"]:
            log.append(f"cable to {other}: {x['output']}")
        elif l.get("admin") == "down":
            await impair.set_state(labd, l, False)
    for e in await l3.apply_node(labs.load(labd["name"]), node):
        log.append(e)
    return {"ok": True, "output": "\n".join(log)}
