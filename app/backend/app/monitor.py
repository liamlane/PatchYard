"""Protocol views for FRR routers: routes, OSPF, BGP, LDP, BFD - parsed from vtysh JSON.

One `docker exec` runs every command; sections are split on a marker. Any daemon that
isn't running (e.g. ldpd=no in the Phase 1 lab) just yields an empty table with a note.
"""
import asyncio, json
from .util import dsh

SEP = "@@NETLAB@@"
CMDS = {
    "routes": "show ip route vrf all json",
    "ospf": "show ip ospf neighbor json",
    "bgp": "show bgp vrf all summary json",
    "ldp": "show mpls ldp neighbor json",
    "bfd": "show bfd peers json",
}


def _load(txt):
    txt = txt.strip()
    if not txt:
        return None, "no output"
    try:
        return json.loads(txt), None
    except ValueError:
        return None, txt.splitlines()[0][:200]


def _nh(n):
    s = []
    if n.get("ip"):
        s.append(f"via {n['ip']}")
    elif n.get("directlyConnected"):
        s.append("connected")
    if n.get("interfaceName"):
        s.append(n["interfaceName"])
    if n.get("vrf") and n.get("vrf") not in ("default",):
        s.append(f"[{n['vrf']}]")
    if n.get("labels"):
        s.append("label " + "/".join(str(x) for x in n["labels"]))
    if n.get("recursive"):
        s.append("(recursive)")
    if n.get("unreachable") or n.get("blackhole"):
        s.append("blackhole")
    return " ".join(s) or "-"


def norm_routes(d):
    out = []
    if not isinstance(d, dict):
        return out
    # `vrf all` -> {vrf: {prefix: [..]}}; single vrf -> {prefix: [..]}
    vrfs = d if all(isinstance(v, dict) for v in d.values()) else {"default": d}
    for vrf, table in vrfs.items():
        for prefix, entries in (table or {}).items():
            for e in entries if isinstance(entries, list) else []:
                nhs = [n for n in e.get("nexthops", []) if not n.get("duplicate")]
                out.append({"vrf": vrf, "prefix": prefix, "protocol": e.get("protocol", ""),
                            "selected": bool(e.get("selected")), "fib": any(n.get("fib") for n in nhs),
                            "distance": e.get("distance"), "metric": e.get("metric"),
                            "uptime": e.get("uptime", ""), "nexthops": [_nh(n) for n in nhs if n.get("active", True)] or ["-"]})
    return out


def norm_ospf(d):
    out = []
    nb = (d or {}).get("neighbors", {}) if isinstance(d, dict) else {}
    for rid, lst in nb.items():
        for n in lst if isinstance(lst, list) else [lst]:
            state = n.get("nbrState") or n.get("state") or ""
            out.append({"router_id": rid, "state": state, "full": state.lower().startswith("full"),
                        "priority": n.get("nbrPriority", n.get("priority")),
                        "address": n.get("ifaceAddress") or n.get("address", ""),
                        "iface": (n.get("ifaceName") or "").split(":")[0],
                        "uptime_ms": n.get("upTimeInMsec"), "dead_ms": n.get("routerDeadIntervalTimerDueMsec", n.get("deadTimeMsecs"))})
    return out


def norm_bgp(d):
    out = []
    if not isinstance(d, dict):
        return out
    vrfs = d if not any(k.startswith("ipv4") for k in d) else {"default": d}
    for vrf, afs in vrfs.items():
        for af, s in (afs or {}).items():
            if not isinstance(s, dict) or "peers" not in s:
                continue
            for peer, p in s["peers"].items():
                st = p.get("state", "")
                out.append({"vrf": vrf, "afi": af, "peer": peer, "remote_as": p.get("remoteAs"),
                            "local_as": s.get("as"), "state": st, "up": st == "Established",
                            "uptime": p.get("peerUptime", ""), "pfx_rcd": p.get("pfxRcd", p.get("prefixReceivedCount")),
                            "pfx_snt": p.get("pfxSnt"), "desc": p.get("desc", "")})
    return out


def norm_ldp(d):
    out = []
    lst = d.get("neighbors", []) if isinstance(d, dict) else []
    for n in lst:
        st = n.get("state", "")
        out.append({"id": n.get("neighborId"), "state": st, "up": st.upper() == "OPERATIONAL",
                    "transport": n.get("transportAddress", ""), "uptime": n.get("upTime", "")})
    return out


def norm_bfd(d):
    out = []
    for p in d if isinstance(d, list) else []:
        st = p.get("status", "")
        out.append({"peer": p.get("peer"), "iface": p.get("interface", ""), "vrf": p.get("vrf", "default"),
                    "state": st, "up": st == "up", "uptime": p.get("uptime")})
    return out


NORM = {"routes": norm_routes, "ospf": norm_ospf, "bgp": norm_bgp, "ldp": norm_ldp, "bfd": norm_bfd}


async def protocols(lab, node, which=None):
    keys = [k for k in CMDS if not which or k in which]
    script = f"; echo '{SEP}'; ".join(f"vtysh -c '{CMDS[k]}' 2>&1" for k in keys)
    rc, out, err = await dsh(lab, node, script, timeout=20)
    parts = out.split(SEP)
    res = {}
    for k, txt in zip(keys, parts + [""] * (len(keys) - len(parts))):
        d, note = _load(txt)
        res[k] = {"rows": NORM[k](d) if d is not None else [], "note": note}
    if rc and not out:
        res["error"] = (err or "exec failed").strip()
    return res


async def health(labd: dict, states: dict) -> dict:
    """Per-router protocol summary for the dashboard."""
    routers = [n["id"] for n in labd["nodes"] if n["type"] == "router" and states.get(n["id"]) == "running"]
    sem = asyncio.Semaphore(6)

    async def one(r):
        async with sem:
            p = await protocols(labd["name"], r, ["routes", "ospf", "bgp", "ldp"])
        o, b, l, rt = p["ospf"]["rows"], p["bgp"]["rows"], p["ldp"]["rows"], p["routes"]["rows"]
        return r, {"ospf": [sum(x["full"] for x in o), len(o)], "bgp": [sum(x["up"] for x in b), len(b)],
                   "ldp": [sum(x["up"] for x in l), len(l)], "routes": sum(1 for x in rt if x["selected"]),
                   "vrfs": len({x["vrf"] for x in rt})}
    return dict(await asyncio.gather(*(one(r) for r in routers)))
