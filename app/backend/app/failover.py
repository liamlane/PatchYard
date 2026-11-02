"""Failover test: fail a link on purpose and measure what it did to traffic and routing.

  1. continuous pings (5 a second) between chosen pairs of hosts, from their namespaces;
  2. routing tables of every router recorded;
  3. the link goes down; wait until every flow is back (or 60 s);
  4. routing tables recorded again; the link comes back up; wait until flows are back again;
  5. report: per flow, packets lost and outage on failure and on restore; per router, which
     prefixes changed next hop or disappeared.
Runs as a background job; the UI polls it.
"""
import asyncio, re, secrets, time
from . import netinfo, impair, monitor, hardware


JOBS: dict[str, dict] = {}
RATE = 0.2


async def _ip(pid: int) -> str | None:
    from .reach import _addr
    return await _addr(pid)


async def _routes(lab: dict, ctrs: dict) -> dict:
    out = {}
    rts = [n["id"] for n in lab["nodes"] if n["type"] in ("router", "isp", "internet") and (ctrs.get((lab["name"], n["id"])) or {}).get("state") == "running"]
    res = await asyncio.gather(*(monitor.protocols(lab["name"], r, ["routes"]) for r in rts), return_exceptions=True)
    for r, p in zip(rts, res):
        if isinstance(p, dict):
            out[r] = {row["prefix"]: (row["protocol"], tuple(row["nexthops"])) for row in p["routes"]["rows"] if row["selected"] and row["vrf"] == "default"}
    return out


def _route_diff(a: dict, b: dict) -> list[dict]:
    out = []
    for r in sorted(set(a) | set(b)):
        x, y = a.get(r, {}), b.get(r, {})
        lost = sorted(set(x) - set(y))
        new = sorted(set(y) - set(x))
        moved = sorted(p for p in set(x) & set(y) if x[p] != y[p])
        if lost or new or moved:
            out.append({"router": r, "lost": lost, "new": new,
                        "moved": [{"prefix": p, "from": f"{x[p][0]} {', '.join(x[p][1])}", "to": f"{y[p][0]} {', '.join(y[p][1])}"} for p in moved]})
    return out


def _gaps(lines: list[tuple[float, int]], t0: float, t1: float | None) -> dict:
    """Outage for an event at t0 (until t1): time between the last reply before and the first reply after."""
    before = [t for t, _ in lines if t <= t0]
    after = [t for t, _ in lines if t > t0 and (t1 is None or t < t1)]
    if not after:
        return {"recovered": False, "outage": None, "lost": None}
    last = before[-1] if before else t0
    first = after[0]
    outage = max(0.0, first - last - RATE)
    return {"recovered": True, "outage": round(outage, 2), "lost": max(0, round(outage / RATE))}


async def _ping(pid: int, dst: str, secs: int, sink: list):
    p = await asyncio.create_subprocess_exec("nsenter", "-t", str(pid), "-n", "ping", "-n", "-D", "-i", str(RATE), "-W", "1", "-w", str(secs), dst,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        while True:
            ln = await p.stdout.readline()
            if not ln:
                break
            m = re.match(r"\[(\d+\.\d+)\].*icmp_seq=(\d+)", ln.decode(errors="ignore"))
            if m and "bytes from" in ln.decode(errors="ignore"):
                sink.append((float(m.group(1)), int(m.group(2))))
    finally:
        if p.returncode is None:
            p.kill()
        await p.wait()


async def _wait_back(flows, since: float, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if all(any(t > since + 0.3 for t, _ in f["got"]) for f in flows):
            return True
        await asyncio.sleep(0.25)
    return False


async def run_test(job: dict, lab: dict, link: dict, pairs: list[tuple[str, str]]):
    try:
        ctrs = await netinfo.containers()
        pid = lambda n: (ctrs.get((lab["name"], n)) or {}).get("pid")
        flows = []
        for s, d in pairs:
            if pid(s) and pid(d):
                ip = await _ip(pid(d))
                if ip:
                    flows.append({"src": s, "dst": d, "ip": ip, "got": []})
        if not flows:
            raise ValueError("no running host pairs to measure")
        secs = 150
        job["state"], job["step"] = "running", "measuring normal traffic"
        tasks = [asyncio.create_task(_ping(pid(f["src"]), f["ip"], secs, f["got"])) for f in flows]
        await asyncio.sleep(3)
        broken = [f for f in flows if not f["got"]]
        if broken:
            job["notes"].append("no replies before the test (left out): " + ", ".join(f"{f['src']}→{f['dst']}" for f in broken))
            for f in broken:
                if len(broken) < len(flows):
                    tasks[flows.index(f)].cancel()
        flows = [f for f in flows if f["got"]] or flows   # wait only for flows that worked to begin with
        r0 = await _routes(lab, ctrs)
        job["step"] = "link down"
        t_down = time.time()
        await impair.set_state(lab, link, False)
        back = await _wait_back(flows, t_down, 60)
        await asyncio.sleep(2)
        r1 = await _routes(lab, ctrs)
        job["step"] = "link back up"
        t_up = time.time()
        await impair.set_state(lab, link, True)
        back2 = await _wait_back(flows, t_up, 60)
        await asyncio.sleep(3)
        r2 = await _routes(lab, ctrs)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        res = []
        for f in flows:
            res.append({"src": f["src"], "dst": f["dst"], "ip": f["ip"], "on_failure": _gaps(f["got"], t_down, t_up), "on_restore": _gaps(f["got"], t_up, None)})
        job["result"] = {"flows": res, "routes_on_failure": _route_diff(r0, r1), "routes_on_restore": _route_diff(r1, r2),
                         "all_recovered": back, "all_restored": back2, "down_at": t_down, "up_at": t_up}
        job["state"], job["step"] = "done", "finished"
    except Exception as e:  # noqa: BLE001
        job["state"], job["error"] = "failed", str(e)
        try:
            await impair.set_state(lab, link, True)
        except Exception:  # noqa: BLE001
            pass


def start(lab: dict, link_id: str, pairs: list | None) -> dict:
    link = next((l for l in lab["links"] if l["id"] == link_id), None)
    if not link:
        raise ValueError("no such cable")
    if any(j["lab"] == lab["name"] and j["state"] in ("starting", "running") for j in JOBS.values()):
        raise ValueError("a failover test is already running in this lab")
    if not pairs:
        hosts = [n["id"] for n in lab["nodes"] if n["type"] in ("pc", "server")]
        pairs = [(a, b) for i, a in enumerate(hosts) for b in hosts[i + 1:]][:8]
    nodes = {n["id"]: n for n in lab["nodes"]}
    jid = secrets.token_hex(4)
    job = {"id": jid, "lab": lab["name"], "link": link_id, "state": "starting", "step": "starting", "notes": [], "started": time.time(),
           "label": f"{link['a']} {hardware.display(nodes[link['a']], link['a_if'])} ↔ {link['b']} {hardware.display(nodes[link['b']], link['b_if'])}"}
    JOBS[jid] = job
    job["task"] = asyncio.ensure_future(run_test(job, lab, link, [tuple(p) for p in pairs][:12]))
    return public(job)


def public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k != "task"}


def list_jobs(lab: str) -> list[dict]:
    return [public(j) for j in sorted(JOBS.values(), key=lambda j: -j["started"]) if j["lab"] == lab][:10]
