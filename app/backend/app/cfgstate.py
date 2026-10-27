"""What the Config tab shows: a device's current configuration as structured data.

Routers: parsed from FRR's running config (or the saved frr.conf when the device isn't
running) plus kernel state per port. Switches, firewalls and hosts: their props (the
source of truth for those devices) plus live port state.
"""
import json, re
from . import hardware, labs, netinfo
from .util import dexec, run


def parse_frr(text: str) -> dict:
    """FRR running-config -> {hostname, interfaces, ospf, bgp, static, ldp}."""
    cfg = {"hostname": "", "interfaces": {}, "ospf": None, "bgp": None, "static": [], "ldp": None, "other": []}
    ctx, sub = None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        st = line.strip()
        if not st or st == "!" or st.startswith(("frr version", "frr defaults", "Building configuration", "Current configuration",
                                                 "service integrated", "log ", "end", "ip forwarding", "no ip forwarding", "no ipv6 forwarding")):
            continue
        top = not line.startswith(" ")
        if top:
            sub = None
            if st == "exit":
                ctx = None
                continue
            w = st.split()
            if w[0] == "hostname":
                cfg["hostname"] = w[1]; ctx = None
            elif w[0] == "interface":
                ctx = ("if", w[1])
                cfg["interfaces"].setdefault(w[1], {"addrs": [], "vrf": w[3] if len(w) > 3 and w[2] == "vrf" else ""})
            elif w[:2] == ["router", "ospf"]:
                ctx = ("ospf",)
                if cfg["ospf"] is None and "vrf" not in w:
                    cfg["ospf"] = {"router_id": "", "networks": [], "passive_default": False, "redistribute": [], "default_originate": "", "areas": []}
                elif "vrf" in w:
                    ctx = ("skip",)
            elif w[:2] == ["router", "bgp"]:
                if "vrf" in w:
                    ctx = ("skip",)
                else:
                    ctx = ("bgp",)
                    cfg["bgp"] = {"asn": int(w[2]), "router_id": "", "neighbors": {}, "networks": [], "redistribute": []}
            elif w[:2] == ["mpls", "ldp"]:
                ctx = ("ldp",)
                cfg["ldp"] = {"router_id": "", "transport": "", "interfaces": []}
            elif w[:2] == ["ip", "route"]:
                ctx = None
                r = {"prefix": w[2], "via": w[3] if len(w) > 3 else "", "distance": None, "vrf": ""}
                rest = w[4:]
                if rest and rest[0].isdigit():
                    r["distance"] = int(rest[0])
                if "vrf" in rest:
                    r["vrf"] = rest[rest.index("vrf") + 1]
                cfg["static"].append(r)
            else:
                ctx = ("other",)
                cfg["other"].append(st)
            continue
        if ctx is None or ctx[0] in ("skip",):
            continue
        w = st.split()
        if ctx[0] == "other":
            cfg["other"].append(line)
        elif ctx[0] == "if":
            i = cfg["interfaces"][ctx[1]]
            if w[:2] == ["ip", "address"]:
                i["addrs"].append(w[2])
            elif w[0] == "description":
                i["description"] = st.split(" ", 1)[1]
            elif st == "shutdown":
                i["shutdown"] = True
            elif w[:3] == ["ip", "ospf", "area"]:
                i["ospf_area"] = w[3]
            elif w[:3] == ["ip", "ospf", "cost"]:
                i["ospf_cost"] = int(w[3])
            elif st.startswith("ip ospf passive"):
                i["ospf_passive"] = True
            elif w[:3] == ["ip", "ospf", "network"]:
                i["ospf_network"] = w[3]
            elif w[0] == "bandwidth":
                i["bandwidth_mbit"] = int(w[1])
        elif ctx[0] == "ospf":
            o = cfg["ospf"]
            if w[:2] == ["ospf", "router-id"]:
                o["router_id"] = w[2]
            elif w[0] == "network" and "area" in w:
                o["networks"].append({"prefix": w[1], "area": w[w.index("area") + 1]})
            elif st == "passive-interface default":
                o["passive_default"] = True
            elif w[0] == "redistribute":
                o["redistribute"].append(w[1])
            elif w[0] == "default-information":
                o["default_originate"] = "always" if "always" in w else "yes"
            elif w[0] == "area":
                o["areas"].append(st)
        elif ctx[0] == "bgp":
            b = cfg["bgp"]
            if w[0] == "address-family":
                sub = " ".join(w[1:])
                continue
            if st == "exit-address-family":
                sub = None
                continue
            if w[:2] == ["bgp", "router-id"]:
                b["router_id"] = w[2]
            elif w[0] == "neighbor" and len(w) >= 3:
                n = b["neighbors"].setdefault(w[1], {"ip": w[1], "remote_as": "", "update_source": "", "description": "", "afs": []})
                if w[2] == "remote-as":
                    n["remote_as"] = w[3]
                elif w[2] == "update-source":
                    n["update_source"] = w[3]
                elif w[2] == "description":
                    n["description"] = " ".join(w[3:])
                elif w[2] == "activate" and sub:
                    n["afs"].append(sub)
            elif w[0] == "network" and (sub in (None, "ipv4 unicast")):
                b["networks"].append(w[1])
            elif w[0] == "redistribute" and (sub in (None, "ipv4 unicast")):
                b["redistribute"].append(w[1])
        elif ctx[0] == "ldp":
            l = cfg["ldp"]
            if w[0] == "router-id":
                l["router_id"] = w[1]
            elif w[:2] == ["discovery", "transport-address"]:
                l["transport"] = w[2]
            elif w[0] == "interface":
                l["interfaces"].append(w[1])
    if cfg["bgp"]:
        cfg["bgp"]["neighbors"] = list(cfg["bgp"]["neighbors"].values())
    return cfg


def _peers(lab, node_id):
    nodes = {n["id"]: n for n in lab["nodes"]}
    out = {}
    for l in lab["links"]:
        for me, other in (("a", "b"), ("b", "a")):
            if l[me] == node_id and l[other] in nodes:
                out[l[f"{me}_if"]] = {"node": l[other], "port": hardware.display(nodes[l[other]], l[f"{other}_if"]),
                                      "media": l.get("media", "copper"), "link": l["id"], "admin": l.get("admin", "up")}
    return out


async def _kernel(lab_name, node_id):
    c = (await netinfo.containers()).get((lab_name, node_id))
    if not c or c["state"] != "running" or not c["pid"]:
        return None
    rc, out, _ = await run("nsenter", "-t", str(c["pid"]), "-n", "ip", "-j", "addr", timeout=8)
    try:
        return {k["ifname"]: k for k in json.loads(out)} if rc == 0 else {}
    except ValueError:
        return {}


def _port_rows(node, peers, kern, extra=()):
    """Every port the device has (chassis) or uses (generic), with live state."""
    ports = hardware.ports(node)
    names = [p["if"] for p in ports] if ports else sorted({*peers, *[k for k in (kern or {}) if re.fullmatch(r"eth[1-9]\d*", k)]},
                                                           key=lambda x: int(x[3:]))
    pm = hardware.port_map(node)
    rows = []
    for ifn in list(names) + [x for x in extra if x not in names]:
        k = (kern or {}).get(ifn)
        p = pm.get(ifn.partition(".")[0])
        rows.append({"if": ifn, "name": hardware.display(node, ifn, long=True), "short": hardware.display(node, ifn),
                     "type": p["type"] if p else None, "media": p["media"] if p else None, "kbit": p["kbit"] if p else None,
                     "slot": p["slot"] if p else None, "peer": peers.get(ifn),
                     "admin": None if k is None else ("up" if "UP" in k.get("flags", []) else "down"),
                     "oper": None if k is None else ("up" if "LOWER_UP" in k.get("flags", []) or k.get("operstate") == "UP" else "down"),
                     "mac": (k or {}).get("address", ""),
                     "kernel_addrs": [f'{a["local"]}/{a["prefixlen"]}' for a in (k or {}).get("addr_info", []) if a.get("family") == "inet"]})
    return rows


async def state(lab: dict, node: dict) -> dict:
    nid, t = node["id"], node["type"]
    kern = await _kernel(lab["name"], nid)
    running = kern is not None
    peers = _peers(lab, nid)
    props = node.get("props") or {}
    base = {"id": nid, "type": t, "running": running, "chassis": props.get("chassis"), "props": props}
    if t == "router" and not node.get("raw"):
        if running:
            rc, out, err = await dexec(lab["name"], nid, "vtysh", "-c", "show running-config", timeout=20)
            text, source = out, "running"
        else:
            p = labs.frr_conf_path(lab, node)
            text, source = (p.read_text() if p and p.exists() else ""), "saved"
        cfg = parse_frr(text)
        subs = [labs.subif_name(s) for s in props.get("subifs", []) or []]
        loops = ["lo"] + [f"loop{int(n)}" for n in props.get("loopbacks", []) or []]
        rows = _port_rows(node, peers, kern, extra=subs + loops)
        for r in rows:
            r["cfg"] = cfg["interfaces"].get(r["if"], {"addrs": []})
            s = next((x for x in props.get("subifs", []) or [] if labs.subif_name(x) == r["if"]), None)
            if s:
                r["vlan"] = int(s["vlan"])
            r["ldp"] = bool(cfg["ldp"] and r["if"] in cfg["ldp"]["interfaces"])
        return {**base, "source": source, "hostname": cfg["hostname"] or nid, "ports": rows,
                "ospf": cfg["ospf"], "bgp": cfg["bgp"], "static": cfg["static"], "ldp": cfg["ldp"], "other": cfg["other"]}
    return {**base, "ports": _port_rows(node, peers, kern)}
