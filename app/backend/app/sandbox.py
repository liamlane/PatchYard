"""Public-hosting sandbox: what keeps one user's lab from touching the host or anyone else.

When settings sandbox.enabled is on (installer --public):
  * lab devices run unprivileged with only NET_ADMIN + NET_RAW (network config inside their
    own namespace), CPU/memory limits from the owner's quota; sysctls the old exec lines
    wrote from inside are set by Patchyard from outside instead (fixups());
  * every lab gets its own management network from sandbox.mgmt_pool (containerlab
    external-access off), so devices of different labs can't reach each other;
  * an nftables table on the host (ip patchyard) rejects traffic from lab management
    networks to anything but the package proxy, unless the lab's owner has internet access;
  * a small HTTP(S) proxy (proxy.py) lets devices fetch packages from sandbox.proxy_allow only.
Admins can mark a lab "trusted" to run it the old way (privileged, shared network).
"""
import ipaddress, json, re, shlex
from . import settings, users
from .util import run

SAFE_RAW_KEYS = {"kind", "image", "binds", "exec", "cmd", "env", "startup-delay", "group"}
DROP_KEYS = {"privileged", "cap-add", "devices", "network-mode", "pid-mode", "security-opts", "sysctls", "runtime", "ports",
             "volumes", "user", "entrypoint", "cgroup-parent", "cgroupns-mode", "shm-size", "tmpfs", "env-files", "certificate",
             "mgmt-net", "mgmt-ipv4", "mgmt-ipv6", "cpu-set", "restart-policy", "auto-remove", "dns", "labels"}
SYSCTL_RE = re.compile(r"^sysctl\s+-w\s+([\w.\-]+)=(\S+)$")
PROC_RE = re.compile(r"/proc/sys|sysctl ")


def enabled() -> bool:
    return bool(settings.get("sandbox.enabled", False))


def active(lab: dict) -> bool:
    return enabled() and not lab.get("trusted")


def pool() -> ipaddress.IPv4Network:
    return ipaddress.ip_network(settings.get("sandbox.mgmt_pool", "172.30.0.0/16"))


# ------------------------------------------------------------------ per-lab management network

def allocate(lab: dict, taken: set[str]) -> str:
    """A free subnet for this lab (kept in lab['sandbox_subnet'] once given)."""
    cur = lab.get("sandbox_subnet")
    if cur and ipaddress.ip_network(cur).subnet_of(pool()) and cur not in taken:
        return cur
    for sn in pool().subnets(new_prefix=int(settings.get("sandbox.lab_prefix", 26))):
        if str(sn) not in taken:
            return str(sn)
    raise ValueError("no free management subnets left in the sandbox pool (Settings → Sandbox)")


def gateway(subnet: str) -> str:
    return str(next(ipaddress.ip_network(subnet).hosts()))


def mgmt(lab: dict) -> dict:
    """containerlab mgmt block for a sandboxed lab."""
    return {"network": f"py-{lab['name']}", "ipv4-subnet": lab["sandbox_subnet"], "ipv4-gw": gateway(lab["sandbox_subnet"]),
            "external-access": False}


def proxy_url(lab: dict) -> str:
    return f"http://{gateway(lab['sandbox_subnet'])}:{int(settings.get('sandbox.proxy_port', 3128))}"


# ------------------------------------------------------------------ node specs

def _apk(cmd: str) -> str:
    """apk goes through the package proxy (only package mirrors are reachable)."""
    if cmd.startswith("apk "):
        return f'sh -c "http_proxy=$PATCHYARD_PROXY https_proxy=$PATCHYARD_PROXY {cmd}"'
    return cmd


def harden(lab: dict, nid: str, spec: dict, owner: str) -> tuple[dict, list[str]]:
    """Turn a containerlab node spec into its sandboxed form. Returns (spec, sysctls to set from outside)."""
    spec = {k: v for k, v in spec.items() if k not in DROP_KEYS}
    if spec.get("kind", "linux") == "bridge":
        return spec, []
    later, keep = [], []
    for c in spec.get("exec", []) or []:
        m = SYSCTL_RE.match(c.strip())
        if m:
            later.append(f"{m.group(1)}={m.group(2)}")
        elif PROC_RE.search(c) and "mpls" in c:
            continue                                     # ROUTER_EXEC's mpls loop: fixups() does it
        else:
            keep.append(_apk(c))
    spec["exec"] = keep
    spec["privileged"] = False
    spec["cap-add"] = ["NET_ADMIN", "NET_RAW"]
    spec["sysctls"] = {"net.ipv4.ip_forward": 1}
    env = dict(spec.get("env") or {})
    env["PATCHYARD_PROXY"] = proxy_url(lab)
    spec["env"] = env
    q = users.quota(owner)
    if q.get("device_memory_mb"):
        spec["memory"] = f"{int(q['device_memory_mb'])}MB"
    if q.get("device_cpus"):
        spec["cpu"] = float(q["device_cpus"])
    for b in spec.get("binds", []) or []:
        src = b.split(":")[0]
        if src.startswith("/") or ".." in src.split("/"):
            raise ValueError(f"{nid}: bind '{b}' reaches outside the lab folder")
    return spec, later


def check_raw(raw: dict, nid: str, images: set[str]):
    """Reject raw containerlab definitions a normal user may not set."""
    bad = sorted(set(raw) - SAFE_RAW_KEYS)
    if bad:
        raise ValueError(f"{nid}: '{', '.join(bad)}' can't be set on imported devices (admins only)")
    if raw.get("kind", "linux") not in ("linux", "bridge"):
        raise ValueError(f"{nid}: only linux and bridge devices can be imported")
    if raw.get("image") and raw["image"] not in images:
        raise ValueError(f"{nid}: image {raw['image']} isn't on this server's allowed list")
    for b in raw.get("binds", []) or []:
        src = str(b).split(":")[0]
        if src.startswith("/") or ".." in src.split("/"):
            raise ValueError(f"{nid}: bind '{b}' reaches outside the lab folder")


def allowed_images() -> set[str]:
    d = settings.get("lab_defaults", {})
    base = {d.get("frr_image"), d.get("pc_image"), d.get("alpine_image"), "quay.io/frrouting/frr:10.0.0",
            "ghcr.io/hellt/network-multitool", "alpine:3.20"}
    return {x for x in base | set(settings.get("users.allowed_images", []) or []) if x}


def docker_args(spec: dict) -> list[str]:
    """`docker run` flags matching a (hardened) spec, for devices started outside containerlab."""
    a = []
    if spec.get("privileged", True):
        a.append("--privileged")
    for c in spec.get("cap-add", []) or []:
        a += ["--cap-add", c]
    for k, v in (spec.get("sysctls") or {}).items():
        a += ["--sysctl", f"{k}={v}"]
    for k, v in (spec.get("env") or {}).items():
        a += ["-e", f"{k}={v}"]
    if spec.get("memory"):
        a += ["--memory", spec["memory"].lower().replace("mb", "m").replace("gb", "g")]
    if spec.get("cpu"):
        a += ["--cpus", str(spec["cpu"])]
    return a


# ------------------------------------------------------------------ sysctls from outside

ROUTER_SYSCTLS = ["net.mpls.platform_labels=1048575"]


async def fixups(labd: dict, only: set[str] | None = None) -> list[str]:
    """Set the sysctls a sandboxed device can't set itself (its /proc/sys is read-only)."""
    from . import netinfo, labs
    if not active(labd):
        return []
    ctrs = await netinfo.containers()
    notes = []
    for n in labd["nodes"]:
        if only and n["id"] not in only:
            continue
        c = ctrs.get((labd["name"], n["id"]))
        if not c or c["state"] != "running" or not c.get("pid"):
            continue
        sets = list(labs.SANDBOX_SYSCTLS.get((labd["name"], n["id"]), []))
        if n["type"] == "router":
            sets += ROUTER_SYSCTLS
        lines = [f"echo {shlex.quote(v)} > /proc/sys/{k.replace('.', '/')} 2>/dev/null" for k, v in (s.split("=", 1) for s in sets)]
        if n["type"] == "router":
            lines.append("for f in /proc/sys/net/mpls/conf/eth*/input; do [ -e $f ] && echo 1 > $f; done")
        if lines:
            rc, o, e = await run("nsenter", "-t", str(c["pid"]), "-n", "sh", "-c", "; ".join(lines) + "; true", timeout=20)
            if rc:
                notes.append(f"{n['id']}: sysctls: {(e or o).strip()[:120]}")
    return notes


# ------------------------------------------------------------------ host egress firewall

async def firewall(all_labs: list[dict]) -> str | None:
    """(Re)write the host nftables table that keeps lab devices off the internet."""
    if not enabled():
        await run("nft", "delete", "table", "ip", "patchyard")
        return None
    p = str(pool())
    port = int(settings.get("sandbox.proxy_port", 3128))
    full = []
    for lab in all_labs:
        sn = lab.get("sandbox_subnet")
        if not sn:
            continue
        u = users.get(lab.get("owner") or "") or {}
        if u.get("role") == "admin" or u.get("internet") or lab.get("trusted"):
            full.append(sn)
    elems = ("elements = { " + ", ".join(full) + " }") if full else ""
    text = f"""add table ip patchyard
delete table ip patchyard
table ip patchyard {{
  set full {{ type ipv4_addr; flags interval; {elems} }}
  chain lab_forward {{
    type filter hook forward priority -5; policy accept;
    ip saddr {p} ct state established,related accept
    ip saddr {p} ip daddr {p} accept
    ip saddr @full accept
    ip saddr {p} counter reject with icmp type admin-prohibited comment "lab egress"
  }}
  chain lab_input {{
    type filter hook input priority -5; policy accept;
    ip saddr {p} ct state established,related accept
    ip saddr {p} tcp dport {port} accept
    ip saddr {p} icmp type echo-request accept
    ip saddr {p} counter reject comment "lab to host"
    tcp dport {port} counter drop comment "package proxy is for labs only"
  }}
}}
"""
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as f:
        f.write(text)
    try:
        rc, o, e = await run("nft", "-f", f.name)
    finally:
        os.unlink(f.name)
    return None if rc == 0 else (e or o).strip()[:300]


async def refresh_firewall():
    from . import labs
    out = []
    for item in labs.list_labs():
        try:
            out.append(labs.load(item["name"]))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return await firewall(out)
