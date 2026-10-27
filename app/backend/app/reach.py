"""Reachability matrix: ping every PC/server from every other one (like a Packet
Tracer "check results" for connectivity). Pings run with the host's ping inside each
source device's network namespace, so nothing is installed in the lab images."""
import asyncio, json, re, time
from . import netinfo
from .util import run


async def _addr(pid: int):
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "addr", "show", "eth1", timeout=5)
    try:
        for i in json.loads(out) if rc == 0 else []:
            for a in i.get("addr_info", []):
                if a.get("family") == "inet":
                    return a["local"]
    except ValueError:
        pass
    return None


async def _ping(pid: int, dst: str):
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ping", "-n", "-c", "1", "-W", "1", dst, timeout=4)
    m = re.search(r"time[=<]([\d.]+)", out)
    return float(m.group(1)) if rc == 0 and m else None


async def check(labd: dict) -> dict:
    ctrs = await netinfo.containers()
    hosts = []
    for n in labd["nodes"]:
        if n["type"] not in ("pc", "server"):
            continue
        c = ctrs.get((labd["name"], n["id"]))
        pid = c["pid"] if c and c["state"] == "running" else None
        hosts.append({"id": n["id"], "type": n["type"], "pid": pid, "ip": await _addr(pid) if pid else None,
                      "state": c["state"] if c else "not deployed"})
    sem = asyncio.Semaphore(24)
    results = {h["id"]: {} for h in hosts}

    async def one(src, dst):
        async with sem:
            results[src["id"]][dst["id"]] = await _ping(src["pid"], dst["ip"])
    await asyncio.gather(*(one(s, d) for s in hosts for d in hosts
                           if s is not d and s["pid"] and d["ip"]))
    ok = sum(1 for r in results.values() for v in r.values() if v is not None)
    tried = sum(len(r) for r in results.values())
    return {"ts": time.time(), "hosts": [{k: v for k, v in h.items() if k != "pid"} for h in hosts],
            "results": results, "ok": ok, "tried": tried}
