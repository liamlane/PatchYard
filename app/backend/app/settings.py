"""Global settings, stored in <data parent>/settings.json (default /opt/netlab/settings.json).

Everything has a default, so a missing or partial file is fine. Environment variables
still override the matching value (NETLAB_FRR_IMAGE etc.) for container deployments.
"""
import copy, json, os, threading
from pathlib import Path
from .env import env as env_get, home

DATA = Path(env_get("DATA", f"{home()}/labs"))
FILE = Path(env_get("SETTINGS", DATA.parent / "settings.json"))
_LOCK = threading.Lock()

DEFAULT_RULES = [
    {"id": "r-container", "name": "Device stopped", "type": "container_down", "severity": "critical", "for_s": 0, "enabled": True},
    {"id": "r-link", "name": "Link down", "type": "link_down", "severity": "warning", "for_s": 10, "enabled": True},
    {"id": "r-bgp", "name": "BGP session down", "type": "bgp_down", "severity": "critical", "for_s": 30, "enabled": True},
    {"id": "r-ospf", "name": "OSPF neighbour lost", "type": "ospf_down", "severity": "critical", "for_s": 30, "enabled": True},
    {"id": "r-ldp", "name": "LDP session down", "type": "ldp_down", "severity": "warning", "for_s": 30, "enabled": True},
    {"id": "r-cpu", "name": "High CPU", "type": "cpu_high", "threshold": 80, "severity": "warning", "for_s": 60, "enabled": True},
    {"id": "r-err", "name": "Interface errors increasing", "type": "errors_rising", "threshold": 1, "severity": "warning", "for_s": 0, "enabled": True},
]

DEFAULTS = {
    "general": {"live_edit": True, "module_power_off": True, "cli_default": "ios"},
    "lab_defaults": {"frr_image": "quay.io/frrouting/frr:10.0.0", "pc_image": "ghcr.io/hellt/network-multitool",
                     "alpine_image": "alpine:3.20", "mgmt_network": "clab-mgmt", "mgmt_subnet": "172.31.255.0/24", "link_mtu": 9500,
                     "router_model": "router-3s", "switch_model": "switch-24"},
    "monitoring": {"interval": 10, "retention_hours": 48, "health_interval": 30},
    "snapshots": {"auto_before_deploy": True, "keep_auto": 20},
    "alerts": {"enabled": True, "grace_s": 120, "rules": DEFAULT_RULES},
    "notifications": {"toast_min_severity": "warning", "toast_resolved": True, "sound": False},
    "security": {"auth_enabled": False, "password_hash": "", "session_hours": 12},
    # multi-user: default limits for non-admin users (0 = unlimited) and images they may pick
    "users": {"quota": {"max_labs": 5, "max_running_labs": 1, "max_devices": 20, "device_memory_mb": 512, "device_cpus": 1.0},
              "allowed_images": [], "allow_requests": True},
    # public hosting: unprivileged lab devices, a management network per lab, and no internet
    # from labs except package downloads through Patchyard's proxy (users with 'internet' get full access)
    "sandbox": {"enabled": False, "mgmt_pool": "172.30.0.0/16", "lab_prefix": 26, "proxy_port": 3128,
                "proxy_allow": ["dl-cdn.alpinelinux.org"]},
}
ENV = {("lab_defaults", "frr_image"): "NETLAB_FRR_IMAGE", ("lab_defaults", "pc_image"): "NETLAB_PC_IMAGE",
       ("lab_defaults", "alpine_image"): "NETLAB_ALPINE_IMAGE", ("lab_defaults", "mgmt_network"): "NETLAB_MGMT_NETWORK",
       ("lab_defaults", "mgmt_subnet"): "NETLAB_MGMT_SUBNET", ("monitoring", "interval"): "NETLAB_METRICS_INTERVAL",
       ("monitoring", "retention_hours"): "NETLAB_METRICS_RETENTION_H", ("sandbox", "enabled"): "NETLAB_SANDBOX"}
SEVERITIES = ["info", "warning", "critical"]
RULE_TYPES = {
    "container_down": "Device stopped or missing", "link_down": "Link down (operational, not admin-down)",
    "bgp_down": "BGP session not Established", "ospf_down": "OSPF neighbour not Full", "ldp_down": "LDP session not Operational",
    "cpu_high": "CPU above threshold (%)", "mem_high": "Memory above threshold (MB)", "link_util": "Link throughput above threshold (Mb/s)",
    "errors_rising": "Interface errors increasing (per sample)", "drops_rising": "Interface drops increasing (per sample)",
    "probe_loss": "Ping probe loss above threshold (%)", "probe_rtt": "Ping probe RTT above threshold (ms)",
}

_cache = None


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load() -> dict:
    global _cache
    with _LOCK:
        if _cache is None:
            try:
                raw = json.loads(FILE.read_text())
            except (OSError, ValueError):
                raw = {}
            _cache = _merge(DEFAULTS, raw)
        s = copy.deepcopy(_cache)
    for (sec, key), env in ENV.items():
        v = env_get(env.replace("NETLAB_", "", 1))
        if v:
            d = DEFAULTS[sec][key]
            s[sec][key] = (v.lower() in ("1", "true", "yes", "on")) if isinstance(d, bool) else (type(d)(v) if not isinstance(d, str) else v)
    return s


def get(path: str, default=None):
    cur = load()
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def validate(s: dict) -> dict:
    s = _merge(DEFAULTS, s)
    s["security"].pop("password_set", None)
    m = s["monitoring"]
    m["interval"] = max(2, min(300, float(m["interval"])))
    m["retention_hours"] = max(1, min(24 * 30, float(m["retention_hours"])))
    m["health_interval"] = max(10, min(600, float(m["health_interval"])))
    s["lab_defaults"]["link_mtu"] = int(max(1280, min(9500, int(s["lab_defaults"]["link_mtu"]))))
    s["snapshots"]["keep_auto"] = int(max(1, min(500, int(s["snapshots"]["keep_auto"]))))
    from . import hardware
    for k, typ in (("router_model", "router"), ("switch_model", "switch")):
        v = s["lab_defaults"][k]
        if v and (v not in hardware.CHASSIS or hardware.CHASSIS[v]["type"] != typ):  # "" = generic
            raise ValueError(f"unknown {typ} model {v}")
    if s["general"]["cli_default"] not in ("ios", "frr"):
        raise ValueError("cli_default must be ios or frr")
    s["alerts"]["grace_s"] = max(0, min(3600, float(s["alerts"]["grace_s"])))
    if s["notifications"]["toast_min_severity"] not in SEVERITIES + ["none"]:
        raise ValueError("toast_min_severity must be info, warning, critical or none")
    rules = []
    for r in s["alerts"]["rules"]:
        if r.get("type") not in RULE_TYPES:
            raise ValueError(f"unknown alert type {r.get('type')}")
        if r.get("severity") not in SEVERITIES:
            raise ValueError(f"bad severity {r.get('severity')}")
        rr = {"id": str(r.get("id") or f"r-{len(rules)}")[:40], "name": str(r.get("name") or RULE_TYPES[r["type"]])[:80],
              "type": r["type"], "severity": r["severity"], "for_s": max(0, min(3600, float(r.get("for_s") or 0))),
              "enabled": bool(r.get("enabled", True)), "lab": str(r.get("lab") or "")[:31], "node": str(r.get("node") or "")[:31]}
        if r["type"] in ("cpu_high", "mem_high", "link_util", "errors_rising", "drops_rising", "probe_loss", "probe_rtt"):
            rr["threshold"] = max(0, float(r.get("threshold") or 0))
        if r["type"] in ("probe_loss", "probe_rtt"):
            if not r.get("lab") or not r.get("node") or not r.get("target"):
                raise ValueError("ping probes need a lab, a source device and a target")
            rr["target"] = str(r["target"])[:253]
        rules.append(rr)
    if len({r["id"] for r in rules}) != len(rules):
        raise ValueError("duplicate rule ids")
    s["alerts"]["rules"] = rules
    q = s["users"]["quota"]
    for k in ("max_labs", "max_running_labs", "max_devices", "device_memory_mb"):
        q[k] = int(max(0, min(100000, int(q.get(k) or 0))))
    q["device_cpus"] = max(0.0, min(64.0, float(q.get("device_cpus") or 0)))
    if q["device_memory_mb"] and q["device_memory_mb"] < 128:
        raise ValueError("device memory: at least 128 MB (0 = no limit)")
    s["users"]["allow_requests"] = bool(s["users"].get("allow_requests", True))
    s["users"]["allowed_images"] = [str(x).strip()[:200] for x in s["users"].get("allowed_images") or [] if str(x).strip()][:50]
    import ipaddress as _ip
    sb = s["sandbox"]
    sb["enabled"] = bool(sb["enabled"])
    pool = _ip.ip_network(sb["mgmt_pool"], strict=True)
    if pool.version != 4 or not pool.is_private:
        raise ValueError("sandbox management pool must be a private IPv4 network")
    sb["lab_prefix"] = int(sb["lab_prefix"])
    if not pool.prefixlen < sb["lab_prefix"] <= 28:
        raise ValueError("per-lab prefix must be longer than the pool's and at most /28")
    sb["proxy_port"] = int(sb["proxy_port"])
    sb["proxy_allow"] = [str(h).strip().lower() for h in sb.get("proxy_allow") or [] if str(h).strip()][:50]
    return s


def save(new: dict, keep_secret=True) -> dict:
    global _cache
    cur = load()
    s = validate(new)
    if keep_secret:   # the password hash is only changed through the auth endpoints
        s["security"]["password_hash"] = cur["security"]["password_hash"]
        if not s["security"]["password_hash"]:
            s["security"]["auth_enabled"] = False
    with _LOCK:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2))
        os.replace(tmp, FILE)
        _cache = s
    return public(s)


def public(s: dict | None = None) -> dict:
    s = copy.deepcopy(s or load())
    s["security"]["password_set"] = bool(s["security"].pop("password_hash", ""))
    return s
