"""Troubleshooting tickets: Patchyard quietly breaks something realistic in a working lab and
hands you a user's complaint. Fix it, press Check, and see how long it took.

How a ticket is made (the lab must be deployed and working):
  1. pick two hosts that can reach each other now;
  2. walk the path between them (trace.walk) and list the faults that would break it on
     devices along the way: a shut interface, a wrong mask, a wrong gateway, a switch port in
     the wrong VLAN, a missing OSPF network, an ACL that denies the traffic, a lost static
     route, a stopped service;
  3. apply one (or two, for "hard"), remembering how to undo it.
The fault is kept in <lab folder>/ticket.json, outside lab.json, so the lab itself doesn't give
it away. Check runs the same engine as challenges. Giving up reveals and undoes the fault.
"""
import asyncio, ipaddress, json, random, time
from . import labs, netinfo, hardware, devices, cfgstate, trace, challenges


FILE = "ticket.json"
SYMPTOM = {
    "ping": "{who}: “I can't reach {what} since this morning. It was fine yesterday.”",
    "http": "{who}: “{what} won't load. Everyone on my floor has the same problem.”",
}


def _path(lab) -> str:
    return labs.workdir(lab) / FILE


def load(lab: dict) -> dict | None:
    try:
        return json.loads(_path(lab).read_text())
    except (OSError, ValueError):
        return None


def _save(lab: dict, t: dict | None):
    p = _path(lab)
    if t is None:
        p.unlink(missing_ok=True)
    else:
        p.write_text(json.dumps(t))


def public(t: dict | None) -> dict | None:
    if not t:
        return None
    out = {k: t.get(k) for k in ("id", "created", "level", "symptom", "src", "target", "solved", "solved_at", "minutes", "gave_up", "hints_used", "checks_run")}
    out["hints"] = t["hints"][:t.get("hints_used", 0)]
    out["hints_left"] = len(t["hints"]) - t.get("hints_used", 0)
    if t.get("solved") or t.get("gave_up"):
        out["faults"] = [f["reveal"] for f in t["faults"]]
    return out


async def _ios(lab, node, cmds):
    from . import ios
    return await ios.run_batch(ios.Env(lab["name"], node), ["configure terminal"] + cmds + ["end"])


def _mask(plen: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{plen}").netmask)


async def candidates(lab: dict, fwd: dict) -> list[dict]:
    """Faults that would break this path, with how to apply and undo each."""
    nodes = {n["id"]: n for n in lab["nodes"]}
    out = []
    for i, h in enumerate(fwd["hops"]):
        n = nodes.get(h["node"]) or {}
        nid, t, p = n.get("id"), n.get("type"), n.get("props") or {}
        if n.get("raw"):
            continue
        # router: shut the egress port, wrong mask on it, drop an OSPF network, add an ACL, drop a static route
        if t == "router" and h.get("out"):
            ifn = next((x["if"] for x in hardware.ports(n) or [] if x["name"] == h["out"]), h["out"])
            disp = h["out"]
            out.append({"kind": "shutdown", "node": nid, "apply": ("ios", [f"interface {disp}", " shutdown"]),
                        "undo": ("ios", [f"interface {disp}", " no shutdown"]), "reveal": f"{nid} {disp} was administratively shut down",
                        "hint": f"Look at {nid}: one of its interfaces is down. `show ip interface brief`."})
            st = await cfgstate.state(lab, n)
            port = next((x for x in st.get("ports", []) if x.get("if") == ifn), None)
            addr = (port or {}).get("cfg", {}).get("addrs", [None])[0] if port else None
            if addr and "/" in addr:
                ip, plen = addr.split("/")
                plen = int(plen)
                if plen <= 29:
                    wrong = plen + 2
                    out.append({"kind": "mask", "node": nid, "apply": ("ios", [f"interface {disp}", f" ip address {ip} {_mask(wrong)}"]),
                                "undo": ("ios", [f"interface {disp}", f" ip address {ip} {_mask(plen)}"]),
                                "reveal": f"{nid} {disp} had the wrong mask: /{wrong} instead of /{plen}",
                                "hint": f"Compare the subnet masks on both ends of {nid}'s {disp} link."})
            for net in (st.get("ospf") or {}).get("networks", []) if isinstance(st.get("ospf"), dict) else []:
                pfx, area = (net.get("prefix"), net.get("area")) if isinstance(net, dict) else (None, None)
                if pfx and addr and ipaddress.ip_interface(addr).ip in ipaddress.ip_network(pfx, strict=False):
                    wn = ipaddress.ip_network(pfx, strict=False)
                    wild = str(ipaddress.IPv4Address(int(wn.hostmask)))
                    out.append({"kind": "ospf", "node": nid, "apply": ("ios", ["router ospf 1", f" no network {wn.network_address} {wild} area {area}"]),
                                "undo": ("ios", ["router ospf 1", f" network {wn.network_address} {wild} area {area}"]),
                                "reveal": f"{nid} lost its OSPF network statement for {wn} (area {area})",
                                "hint": f"Check OSPF neighbours and `show ip protocols` on {nid}."})
            if i > 0 and h.get("in"):
                acl = f"TKT{random.randint(100, 199)}"
                dst = fwd["dst"]
                out.append({"kind": "acl", "node": nid,
                            "apply": ("ios", [f"ip access-list extended {acl}", f" deny ip any host {dst}", " permit ip any any", " exit",
                                              f"interface {h['in']}", f" ip access-group {acl} in"]),
                            "undo": ("ios", [f"interface {h['in']}", f" no ip access-group {acl} in", " exit", f"no ip access-list extended {acl}"]),
                            "reveal": f"{nid} had access list {acl} inbound on {h['in']}, denying traffic to {dst}",
                            "hint": f"Something on {nid} is filtering. `show access-lists` and `show ip interface`."})
            if h.get("protocol") == "static" and h.get("route") and h.get("via") and h["via"] != "directly connected":
                rn = ipaddress.ip_network(h["route"])
                out.append({"kind": "static", "node": nid, "apply": ("ios", [f"no ip route {rn.network_address} {rn.netmask} {h['via']}"]),
                            "undo": ("ios", [f"ip route {rn.network_address} {rn.netmask} {h['via']}"]),
                            "reveal": f"{nid} lost its static route to {rn} via {h['via']}",
                            "hint": f"`show ip route` on {nid}: is there still a way to {fwd['dst']}?"})
        # hosts: wrong gateway
        if t in ("pc", "server") and i == 0 and p.get("ip_mode") == "static" and p.get("gateway"):
            gw = ipaddress.ip_address(p["gateway"])
            wrong = str(gw + 1 if int(gw) % 256 < 250 else gw - 1)
            out.append({"kind": "gateway", "node": nid, "apply": ("host", {**p, "gateway": wrong}), "undo": ("host", dict(p)),
                        "reveal": f"{nid} had the wrong default gateway ({wrong} instead of {p['gateway']})",
                        "hint": f"Start at {nid} itself: `ipconfig /all`."})
        # switches along the way: the port towards the next hop in the wrong VLAN
        for sw in h.get("l2") or []:
            sn = nodes.get(sw["node"]) or {}
            sp = (sn.get("props") or {}).get("ports") or {}
            for side in ("in", "out"):
                ifn = next((x["if"] for x in hardware.ports(sn) or [] if x["name"] == sw[side]), sw[side])
                c = sp.get(ifn) or {}
                if c.get("mode", "access") == "access":
                    cur = int(c.get("vlan") or 1)
                    wrong = 999 if cur != 999 else 998
                    out.append({"kind": "vlan", "node": sn["id"], "apply": ("switch", ifn, {**c, "mode": "access", "vlan": wrong}),
                                "undo": ("switch", ifn, dict(c) if c else None),
                                "reveal": f"{sn['id']} {sw[side]} was moved to VLAN {wrong} (it belongs in VLAN {cur})",
                                "hint": f"`show vlan brief` on {sn['id']}."})
                    break
    return out


async def _apply(lab: dict, f: dict, which: str):
    act = f[which]
    kind = act[0]
    if kind == "ios":
        r = await _ios(lab, f["node"], act[1])
        return r["ok"], "; ".join(r["errors"])
    if kind == "host":
        props = act[1]
        labs.patch_props(lab["name"], f["node"], lambda pr: pr.update(gateway=props.get("gateway", "")))
        r = await devices.ipconfig(lab["name"], f["node"], {"mode": props.get("ip_mode", "static"), "ip": props.get("ip", ""),
                                                             "gateway": props.get("gateway", ""), "dns": props.get("dns", "")})
        return r["ok"], r.get("output", "")
    if kind == "switch":
        ifn, cfg = act[1], act[2]

        def fn(pr):
            ports = pr.setdefault("ports", {})
            if cfg is None:
                ports.pop(ifn, None)
            else:
                ports[ifn] = cfg
        labs.patch_props(lab["name"], f["node"], fn)
        fresh = labs.load(lab["name"])
        r = await devices.apply_switch(fresh, next(n for n in fresh["nodes"] if n["id"] == f["node"]))
        return True, str(r)[:200]
    return False, "unknown fault"


async def _breaks(lab: dict, probe: dict, wait: float = 12) -> bool:
    """True once the user's traffic really fails (routing may take a few seconds to notice)."""
    end = time.time() + wait
    while True:
        ctrs = await netinfo.containers()
        try:
            ok, _ = await asyncio.wait_for(challenges._one(lab, probe, ctrs), 20)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            return True
        if time.time() >= end:
            return False
        await asyncio.sleep(2)


async def create(lab: dict, level: str = "normal", src: str | None = None, target: str | None = None) -> dict:
    if load(lab) and not (load(lab).get("solved") or load(lab).get("gave_up")):
        raise ValueError("finish or give up the open ticket first")
    from . import reach
    ctrs = await netinfo.containers()
    hosts = [n for n in lab["nodes"] if n["type"] in ("pc", "server") and (ctrs.get((lab["name"], n["id"])) or {}).get("state") == "running"]
    if len(hosts) < 2:
        raise ValueError("deploy a lab with at least two PCs or servers that can reach each other")
    pairs = []
    if src and target:
        pairs = [(src, target)]
    else:
        r = await reach.check(lab)
        for s, row in r["results"].items():
            for d, v in row.items():
                if v is not None:
                    pairs.append((s, d))
        random.shuffle(pairs)
    if not pairs:
        raise ValueError("nothing can reach anything yet: fix the lab first, then ask for a ticket")
    for s, d in pairs[:8]:
        try:
            res = await trace.explain(lab, s, d, "icmp")
        except ValueError:
            continue
        if not res["ok"]:
            continue
        cands = await candidates(lab, res["forward"])
        if not cands:
            continue
        random.shuffle(cands)
        n = 2 if level == "hard" and len(cands) > 1 else 1
        probe = {"id": "probe", "type": "ping", "node": s, "target": res["forward"]["dst"], "points": 1, "label": "probe"}
        picked, used = [], set()
        for c in cands:
            if c["node"] + c["kind"] in used:
                continue
            ok, err = await _apply(lab, c, "apply")
            if not ok:                       # half-applied (an ACL made but not attached): take it back out
                await _apply(lab, c, "undo")
                continue
            if n == 1 and not await _breaks(lab, probe):
                await _apply(lab, c, "undo")  # a redundant path (or a mask that still fits) hid it: try another
                continue
            picked.append(c)
            used.add(c["node"] + c["kind"])
            if len(picked) == n:
                break
        if picked and n > 1 and (len(picked) < n or not await _breaks(lab, probe)):
            for c in reversed(picked):
                await _apply(lab, c, "undo")
            picked = []
        if not picked:
            continue
        dst_node = next((x for x in lab["nodes"] if x["id"] == d), None)
        web = dst_node and dst_node["type"] == "server" and ((dst_node.get("props") or {}).get("services", {}).get("http") or {}).get("enabled")
        what = f"{d} ({res['forward']['dst']})"
        checks = [{"id": "reach", "type": "ping", "node": s, "target": res["forward"]["dst"], "points": 1, "label": f"{s} can reach {d} again"}]
        kind = "http" if web else "ping"
        if web:
            checks.append({"id": "web", "type": "http", "node": s, "url": f"http://{res['forward']['dst']}", "points": 1, "label": f"{s} can open {d}'s web page"})
        t = {"id": random.randint(1000, 9999), "created": time.time(), "level": level, "src": s, "target": d,
             "symptom": SYMPTOM[kind].format(who=f"The user at {s}", what=what), "faults": picked, "checks": checks,
             "hints": ["Start with the path explainer from the user's device: it shows where the packet stops."] + [c["hint"] for c in picked],
             "hints_used": 0, "checks_run": 0}
        _save(lab, t)
        return public(t)
    raise ValueError("couldn't find a fault to inject on any working path (the lab may only have hosts on one switch)")


async def check(lab: dict) -> dict:
    t = load(lab)
    if not t:
        raise ValueError("no open ticket")
    ctrs = await netinfo.containers()
    results = []
    for k in t["checks"]:
        try:
            ok, detail = await asyncio.wait_for(challenges._one(lab, k, ctrs), 40)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, str(e)
        results.append({"label": k["label"], "ok": ok, "detail": detail})
    t["checks_run"] = t.get("checks_run", 0) + 1
    if all(r["ok"] for r in results) and not t.get("solved"):
        t["solved"], t["solved_at"] = True, time.time()
        t["minutes"] = round((t["solved_at"] - t["created"]) / 60, 1)
    _save(lab, t)
    return {"ticket": public(t), "results": results}


def hint(lab: dict) -> dict:
    t = load(lab)
    if not t:
        raise ValueError("no open ticket")
    t["hints_used"] = min(len(t["hints"]), t.get("hints_used", 0) + 1)
    _save(lab, t)
    return public(t)


async def give_up(lab: dict) -> dict:
    t = load(lab)
    if not t:
        raise ValueError("no open ticket")
    notes = []
    for f in reversed(t["faults"]):
        ok, err = await _apply(lab, f, "undo")
        notes.append(f["reveal"] + ("" if ok else f" (undo failed: {err}; redeploy to reset)"))
    t["gave_up"] = True
    _save(lab, t)
    return {"ticket": public(t), "undone": notes}


def close(lab: dict):
    _save(lab, None)
