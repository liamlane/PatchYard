"""Change rehearsal: take a baseline of the running network, make your change, compare.

A baseline records, for the running lab:
  * every router's selected routes (prefix → protocol and next hops),
  * OSPF/BGP/LDP neighbour states,
  * the reachability matrix (who can ping whom).
Compare takes the same picture now and lists exactly what the change did: routes gained,
lost or moved, neighbours that went down or came up, host pairs that broke or got fixed.
Stored in <lab folder>/baseline.json (one baseline per lab).
"""
import asyncio, json, time
from . import labs, netinfo, monitor, reach

FILE = "baseline.json"


async def capture(lab: dict, with_reach: bool = True) -> dict:
    ctrs = await netinfo.containers()
    rts = [n["id"] for n in lab["nodes"] if n["type"] in ("router", "isp", "internet") and not n.get("raw")
           and (ctrs.get((lab["name"], n["id"])) or {}).get("state") == "running"]
    protos = await asyncio.gather(*(monitor.protocols(lab["name"], r, ["routes", "ospf", "bgp", "ldp"]) for r in rts), return_exceptions=True)
    routes, nbrs = {}, {}
    for r, p in zip(rts, protos):
        if not isinstance(p, dict):
            continue
        routes[r] = {f"{row['vrf']}:{row['prefix']}": f"{row['protocol']} via {', '.join(row['nexthops'])}"
                     for row in p["routes"]["rows"] if row["selected"]}
        n = {}
        for row in p["ospf"]["rows"]:
            n[f"OSPF {row['router_id']}"] = "Full" if row["full"] else row["state"]
        for row in p["bgp"]["rows"]:
            n[f"BGP {row['peer']}" + (f" vrf {row['vrf']}" if row["vrf"] != "default" else "")] = row["state"]
        for row in p["ldp"]["rows"]:
            n[f"LDP {row['id']}"] = row["state"]
        nbrs[r] = n
    reach_ok = {}
    if with_reach:
        rc = await reach.check(lab)
        reach_ok = {f"{s}→{d}": v is not None for s, row in rc["results"].items() for d, v in row.items()}
    return {"ts": time.time(), "routes": routes, "neighbours": nbrs, "reach": reach_ok,
            "summary": {"routers": len(routes), "routes": sum(len(v) for v in routes.values()),
                        "reachable": sum(reach_ok.values()), "pairs": len(reach_ok)}}


def _p(lab):
    return labs.workdir(lab) / FILE


async def take(lab: dict) -> dict:
    b = await capture(lab)
    _p(lab).write_text(json.dumps(b))
    return {"ts": b["ts"], "summary": b["summary"]}


def get(lab: dict) -> dict | None:
    try:
        b = json.loads(_p(lab).read_text())
        return {"ts": b["ts"], "summary": b["summary"]}
    except (OSError, ValueError):
        return None


def diff(a: dict, b: dict) -> dict:
    routes = []
    for r in sorted(set(a["routes"]) | set(b["routes"])):
        x, y = a["routes"].get(r, {}), b["routes"].get(r, {})
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                vrf, _, pfx = k.partition(":")
                routes.append({"router": r, "prefix": pfx, "vrf": vrf, "before": x.get(k), "after": y.get(k),
                               "change": "gained" if k not in x else "lost" if k not in y else "moved"})
    nbrs = []
    for r in sorted(set(a["neighbours"]) | set(b["neighbours"])):
        x, y = a["neighbours"].get(r, {}), b["neighbours"].get(r, {})
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                nbrs.append({"router": r, "neighbour": k, "before": x.get(k) or "absent", "after": y.get(k) or "absent"})
    reachd = []
    for k in sorted(set(a["reach"]) | set(b["reach"])):
        if a["reach"].get(k) != b["reach"].get(k):
            reachd.append({"pair": k, "before": a["reach"].get(k), "after": b["reach"].get(k)})
    return {"routes": routes, "neighbours": nbrs, "reach": reachd,
            "broken": [x["pair"] for x in reachd if x["before"] and not x["after"]],
            "fixed": [x["pair"] for x in reachd if not x["before"] and x["after"]]}


async def compare(lab: dict) -> dict:
    try:
        a = json.loads(_p(lab).read_text())
    except (OSError, ValueError):
        raise ValueError("take a baseline first")
    b = await capture(lab)
    return {"baseline": {"ts": a["ts"], "summary": a["summary"]}, "now": {"ts": b["ts"], "summary": b["summary"]}, **diff(a, b)}
