"""What this host can do, and whether a newer release exists.

Patchyard runs on any Linux box with Docker, but some features need kernel modules the
distribution may not ship or load: the UI greys those out instead of failing at deploy."""
import asyncio, json, os, platform, re, time, urllib.request
from .env import env
from .util import run
from .version import VERSION

# feature -> (kernel modules that must be present, what breaks without them)
FEATURES = {
    "bridge": (["bridge"], "switches (Linux-bridge engine)"),
    "vlan": (["8021q"], "router subinterfaces (router-on-a-stick) and VLAN trunks"),
    "netem": (["sch_netem"], "link speeds and fault injection (delay, loss, rate)"),
    "mpls": (["mpls_router", "mpls_iptunnel"], "MPLS/LDP and the dual-ISP MPLS L3VPN template"),
    "vrf": (["vrf"], "VRFs on routers"),
    "dummy": (["dummy"], "router Loopback1 and up"),
    "ovs": (["openvswitch"], "the Open vSwitch switch engine (RSTP, LACP) and the campus RSTP + LACP template"),
    "nftables": (["nf_tables"], "firewalls, router ACLs and NAT"),
}


def _module_present(m: str) -> bool:
    """Loaded, built into the kernel, or available to load (listed in modules.dep)."""
    if os.path.isdir(f"/sys/module/{m}"):
        return True
    rel = platform.release()
    rx = re.compile(rf"(^|/){re.escape(m)}\.ko(\.[gx]z|\.zst)?:?(\s|$)")
    for base in (f"/lib/modules/{rel}", f"/usr/lib/modules/{rel}"):
        for name in ("modules.builtin", "modules.dep"):
            try:
                with open(f"{base}/{name}") as f:
                    if any(rx.search(ln) for ln in f):
                        return True
            except OSError:
                pass
    return False


async def capabilities() -> dict:
    feats = {}
    for k, (mods, what) in FEATURES.items():
        missing = [m for m in mods if not _module_present(m)]
        feats[k] = {"ok": not missing, "missing": missing, "needed_for": what}
    rc, dv, _ = await run("docker", "version", "--format", "{{.Server.Version}}", timeout=10)
    rc2, cv, _ = await run("containerlab", "version", timeout=10)
    m = re.search(r"version:\s*(\S+)", cv or "")
    mem = 0
    try:
        with open("/proc/meminfo") as f:
            mem = int(next(l for l in f if l.startswith("MemTotal")).split()[1]) // 1024
    except (OSError, StopIteration, ValueError):
        pass
    return {"features": feats, "kernel": platform.release(), "arch": platform.machine(), "cpus": os.cpu_count(), "mem_mb": mem,
            "docker": dv.strip() if rc == 0 else None, "containerlab": m.group(1) if rc2 == 0 and m else None,
            "os": _os_name()}


def _os_name() -> str:
    for f in ("/host/etc/os-release", "/etc/os-release"):
        try:
            with open(f) as fh:
                d = dict(l.rstrip().split("=", 1) for l in fh if "=" in l)
            return d.get("PRETTY_NAME", "").strip('"')
        except OSError:
            continue
    return ""


_UPD = {"at": 0.0, "data": None}


def _vtuple(v: str):
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _fetch_latest(repo: str):
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": f"patchyard/{VERSION}"})
    with urllib.request.urlopen(req, timeout=8) as r:
        d = json.loads(r.read())
    return {"tag": d.get("tag_name", ""), "url": d.get("html_url", ""), "published": d.get("published_at", ""), "notes": (d.get("body") or "")[:4000]}


async def update_check(force=False) -> dict:
    """Latest GitHub release vs this build (cached for 6 hours; off with PATCHYARD_UPDATE_CHECK=0)."""
    repo = env("REPO", "liamlane/patchyard")
    if env("UPDATE_CHECK", "1") == "0":
        return {"enabled": False, "current": VERSION}
    if force or time.time() - _UPD["at"] > 6 * 3600 or _UPD["data"] is None:
        try:
            latest = await asyncio.to_thread(_fetch_latest, repo)
            _UPD["data"] = {"latest": latest, "error": None}
        except Exception as e:  # noqa: BLE001 - offline hosts are normal
            _UPD["data"] = {"latest": None, "error": str(e)[:200]}
        _UPD["at"] = time.time()
    d = _UPD["data"]
    cur, new = _vtuple(VERSION), _vtuple((d["latest"] or {}).get("tag", ""))
    return {"enabled": True, "repo": repo, "current": VERSION, **d,
            "available": bool(cur and new and new > cur)}
