"""Host-side view of lab containers: no `docker exec` per poll.

Interface counters come from /proc/<pid>/net/dev (the container's network namespace),
port state from the container's own sysfs via /proc/<pid>/root/sys/class/net, and
CPU / memory from the container's cgroup v2 files. One `docker ps` per cycle finds
containers by containerlab's labels, so lab names containing dashes are unambiguous.
"""
import asyncio, time
from pathlib import Path
from . import util
from .util import run

_PIDS: dict[str, int] = {}      # container id -> pid (stable while running)
TTL = 1.0                       # every poller (status, alerts, metrics, admin) shares one `docker ps` per second
_CACHE: dict = {"t": 0.0, "v": None, "task": None, "gen": -1}


def invalidate():
    """Forget the cached container list (util.run also does this after any docker/containerlab change)."""
    _CACHE["t"] = 0.0


async def containers() -> dict:
    """{(lab, node): {"id", "name", "state", "pid"}} for every containerlab container (cached ~1 s)."""
    now = time.monotonic()
    if _CACHE["v"] is not None and now - _CACHE["t"] < TTL and _CACHE["gen"] == util.GEN[0]:
        return {k: dict(v) for k, v in _CACHE["v"].items()}
    task = _CACHE["task"]
    gen = util.GEN[0]
    if task is None or task.done() or _CACHE.get("task_gen") != gen:
        task = _CACHE["task"] = asyncio.ensure_future(_containers())
        _CACHE["task_gen"], stamp = gen, now
    else:
        stamp = None
    res = await asyncio.shield(task)
    if stamp is not None and gen == util.GEN[0]:
        _CACHE["v"], _CACHE["t"], _CACHE["gen"] = res, stamp, gen
    return {k: dict(v) for k, v in res.items()}


async def _containers() -> dict:
    fmt = '{{.ID}}|{{.Names}}|{{.State}}|{{.Label "containerlab"}}|{{.Label "clab-node-name"}}'
    rc, out, _ = await run("docker", "ps", "-a", "--filter", "label=containerlab", "--format", fmt)
    res, need = {}, []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[3]:
            continue
        cid, name, state, lab, node = parts
        res[(lab, node)] = {"id": cid, "name": name, "state": state, "pid": None}
        if state == "running":
            if cid in _PIDS:
                res[(lab, node)]["pid"] = _PIDS[cid]
            else:
                need.append(cid)
    if need:
        rc, out, _ = await run("docker", "inspect", "-f", "{{.Id}}|{{.State.Pid}}", *need)
        for line in out.splitlines():
            full, _, pid = line.partition("|")
            for v in res.values():
                if full.startswith(v["id"]) and pid.isdigit() and int(pid):
                    _PIDS[v["id"]] = v["pid"] = int(pid)
    live = {v["id"] for v in res.values()}
    for cid in list(_PIDS):
        if cid not in live:
            del _PIDS[cid]
    return res


def iface_counters(pid: int) -> dict:
    """{ifname: {...counters..., "state"}} from the container's namespace."""
    out = {}
    try:
        lines = Path(f"/proc/{pid}/net/dev").read_text().splitlines()[2:]
    except OSError:
        return out
    for ln in lines:
        name, _, rest = ln.partition(":")
        name = name.strip()
        f = rest.split()
        if len(f) < 16 or name == "lo":
            continue
        out[name] = {"rxb": int(f[0]), "rxp": int(f[1]), "rxe": int(f[2]), "rxd": int(f[3]),
                     "txb": int(f[8]), "txp": int(f[9]), "txe": int(f[10]), "txd": int(f[11])}
        try:
            out[name]["state"] = Path(f"/proc/{pid}/root/sys/class/net/{name}/operstate").read_text().strip()
        except OSError:
            out[name]["state"] = "unknown"
    return out


def cgroup_stats(pid: int) -> dict:
    """{"cpu_usec": int, "mem": bytes} from cgroup v2, falling back to v1 (None when unavailable)."""
    try:
        lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    except OSError:
        return {"cpu_usec": None, "mem": None}
    cpu = mem = None
    v2 = next((l.split("::", 1)[1] for l in lines if l.startswith("0::")), None)
    if v2 is not None:
        base = Path("/sys/fs/cgroup") / v2.lstrip("/")
        try:
            cpu = next(int(l.split()[1]) for l in (base / "cpu.stat").read_text().splitlines() if l.startswith("usage_usec"))
            mem = int((base / "memory.current").read_text())
        except (OSError, StopIteration, ValueError):
            pass
    if cpu is None or mem is None:          # cgroup v1 / hybrid
        ctl = {}
        for l in lines:
            parts = l.split(":", 2)
            if len(parts) == 3:
                for c in parts[1].split(","):
                    ctl[c] = parts[2]
        try:
            if cpu is None and "cpuacct" in ctl:
                for d in ("cpuacct", "cpu,cpuacct"):
                    f = Path("/sys/fs/cgroup") / d / ctl["cpuacct"].lstrip("/") / "cpuacct.usage"
                    if f.exists():
                        cpu = int(f.read_text()) // 1000
                        break
            if mem is None and "memory" in ctl:
                mem = int((Path("/sys/fs/cgroup/memory") / ctl["memory"].lstrip("/") / "memory.usage_in_bytes").read_text())
        except (OSError, ValueError):
            pass
    return {"cpu_usec": cpu, "mem": mem}


def now() -> float:
    return time.time()
