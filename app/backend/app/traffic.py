"""Traffic generator: iperf3 between any two lab devices.

iperf3 is the netlab host's binary, run inside each device's network namespace with
nsenter, so it works on every device type without installing anything in the lab
images. Packets really cross the lab: the overlay, charts, impairments and alerts all
see them. One server per job on its own port; the server exits after the test.
"""
import asyncio, ipaddress, json, secrets, time
from . import netinfo
from .util import run, check_host, check_ip

JOBS: dict[str, dict] = {}
_ports = iter(range(5300, 5900))


def _port():
    global _ports
    try:
        return next(_ports)
    except StopIteration:
        _ports = iter(range(5300, 5900))
        return next(_ports)


async def node_ips(pid: int) -> list[str]:
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "addr", "show")
    try:
        data = json.loads(out)
    except ValueError:
        return []
    ips = []
    for i in data:
        if i.get("ifname") in ("lo", "eth0"):
            continue
        for a in i.get("addr_info", []):
            ips.append(a["local"])
    return ips


def public(j):
    return {k: v for k, v in j.items() if not k.startswith("_")}


async def start(labd: dict, body: dict) -> dict:
    src, dst = body.get("src"), body.get("dst")
    proto = "udp" if body.get("proto") == "udp" else "tcp"
    secs = int(max(1, min(3600, float(body.get("duration", 10)))))
    streams = int(max(1, min(16, int(body.get("streams", 1)))))
    rate_m = float(body.get("rate_mbps") or 0)
    if proto == "udp" and rate_m <= 0:
        rate_m = 10
    ctrs = await netinfo.containers()
    cs = ctrs.get((labd["name"], src))
    if not cs or not cs["pid"]:
        raise ValueError(f"{src} is not running")
    if not body.get("dst_ip") and not dst:
        raise ValueError("choose a destination")
    target = body.get("dst_ip")
    cd = ctrs.get((labd["name"], dst)) if dst else None
    if dst and (not cd or not cd["pid"]):
        raise ValueError(f"{dst} is not running")
    if not target:
        ips = await node_ips(cd["pid"])
        if not ips:
            raise ValueError(f"{dst} has no IPv4 address outside the management network")
        target = ips[0]
    try:
        check_ip(target)
    except ValueError:
        check_host(target)
    if not cd:
        raise ValueError("pick a destination device (it runs the iperf3 server)")
    port = _port()
    jid = secrets.token_hex(4)
    j = {"id": jid, "lab": labd["name"], "src": src, "dst": dst, "target": target, "proto": proto, "duration": secs,
         "streams": streams, "rate_mbps": rate_m or None, "reverse": bool(body.get("reverse")), "state": "starting",
         "started": time.time(), "result": None, "error": None}
    JOBS[jid] = j
    j["_task"] = asyncio.create_task(_run(j, cs["pid"], cd["pid"], port))
    return public(j)


async def _run(j, spid, dpid, port):
    srv = await asyncio.create_subprocess_exec("nsenter", "-t", str(dpid), "-n", "iperf3", "-s", "-1", "-p", str(port),
                                               stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    j["_srv"] = srv
    await asyncio.sleep(0.6)
    args = ["nsenter", "-t", str(spid), "-n", "iperf3", "-c", j["target"], "-p", str(port), "-t", str(j["duration"]),
            "-P", str(j["streams"]), "-J", "--connect-timeout", "3000"]
    if j["proto"] == "udp":
        args.append("-u")
    if j["rate_mbps"]:
        args += ["-b", f"{j['rate_mbps']:g}M"]
    if j["reverse"]:
        args.append("-R")
    j["state"] = "running"
    cli = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    j["_cli"] = cli
    try:
        out, err = await asyncio.wait_for(cli.communicate(), j["duration"] + 20)
    except asyncio.TimeoutError:
        cli.kill(); out, err = b"", b"timed out"
    except asyncio.CancelledError:
        cli.kill(); srv.kill(); j["state"] = "stopped"; return
    if srv.returncode is None:
        srv.kill()
    try:
        d = json.loads(out.decode() or "{}")
    except ValueError:
        d = {}
    if d.get("error") or not d:
        j["state"], j["error"] = "failed", d.get("error") or err.decode().strip()[:300] or "iperf3 failed"
        return
    end = d.get("end", {})
    if j["proto"] == "tcp":
        sent, rcvd = end.get("sum_sent", {}), end.get("sum_received", {})
        j["result"] = {"bps": rcvd.get("bits_per_second") or sent.get("bits_per_second"), "bytes": rcvd.get("bytes"),
                       "retransmits": sent.get("retransmits")}
    else:
        s = end.get("sum", {})
        j["result"] = {"bps": s.get("bits_per_second"), "bytes": s.get("bytes"), "jitter_ms": s.get("jitter_ms"),
                       "lost_percent": s.get("lost_percent"), "packets": s.get("packets")}
    j["state"] = "done"


def list_jobs(lab):
    now = time.time()
    out = []
    for j in sorted(JOBS.values(), key=lambda x: -x["started"]):
        if j["lab"] == lab:
            p = public(j)
            p["elapsed"] = min(j["duration"], now - j["started"])
            out.append(p)
    for jid in [k for k, v in JOBS.items() if now - v["started"] > 6 * 3600]:
        del JOBS[jid]
    return out[:30]


def stop(jid):
    j = JOBS.get(jid)
    if not j:
        raise FileNotFoundError("no such job")
    for k in ("_cli", "_srv"):
        p = j.get(k)
        if p and p.returncode is None:
            p.kill()
    if j.get("_task") and not j["_task"].done():
        j["_task"].cancel()
    j["state"] = "stopped"
    return public(j)
