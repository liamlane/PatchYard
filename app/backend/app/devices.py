"""Device actions behind the Config, Physical, Desktop and Services tabs."""
import asyncio, re
from . import labs, netinfo
from .util import dexec, dsh, check_ip, check_if, check_host, container_states, run, cname

PC_RC = r"""
# netlab PC command prompt: Windows-style helpers on top of the Linux tools
__dhcp() { h=/usr/local/bin/netlab-dhcp; [ -x "$h" ] || h=/tmp/netlab-dhcp; echo "$h"; }
ipconfig() {
  case "$1" in
    ""|"/?")
      [ "$1" = "/?" ] && { echo "ipconfig [/all | /release | /renew]"; return; }
      echo; echo "Ethernet adapter eth1:"; echo
      ip -4 addr show eth1 | sed -n "s/ *inet \([^/]*\)\/\([0-9]*\).*/   IPv4 Address. . . . . . . . . . . : \1\n   Prefix Length . . . . . . . . . . : \/\2/p"
      ip route | sed -n "s/^default via \([^ ]*\).*/   Default Gateway . . . . . . . . . : \1/p"; echo ;;
    /all)
      echo; echo "   Host Name . . . . . . . . . . . . : $(hostname)"; echo; echo "Ethernet adapter eth1:"; echo
      echo "   Physical Address. . . . . . . . . : $(cat /sys/class/net/eth1/address 2>/dev/null | tr a-f: A-F-)"
      echo "   DHCP Enabled. . . . . . . . . . . : $(pgrep udhcpc >/dev/null && echo Yes || echo No)"
      ip -4 addr show eth1 | sed -n "s/ *inet \([^/]*\)\/\([0-9]*\).*/   IPv4 Address. . . . . . . . . . . : \1\n   Prefix Length . . . . . . . . . . : \/\2/p"
      ip route | sed -n "s/^default via \([^ ]*\).*/   Default Gateway . . . . . . . . . : \1/p"
      sed -n "s/^nameserver /   DNS Servers . . . . . . . . . . . : /p" /etc/resolv.conf; echo ;;
    /release)
      pkill udhcpc 2>/dev/null; ip -4 addr flush dev eth1; ip route del default 2>/dev/null
      echo; echo "Ethernet adapter eth1: address released."; echo ;;
    /renew)
      echo "Requesting an address on eth1..."
      udhcpc -i eth1 -n -q -t 4 -T 2 -x "hostname:$(hostname)" -s "$(__dhcp)" 2>&1 | grep -E "lease|no lease|fail" ; ipconfig ;;
    *) echo "Error: unrecognized or incomplete command line."; echo "ipconfig [/all | /release | /renew]" ;;
  esac
}
alias tracert='traceroute -n'
alias cls='clear'
alias udhcpc='udhcpc -s "$(__dhcp)"'
arp() { [ "$1" = "-a" ] || { command arp "$@"; return; }; echo; echo "  Internet Address      Physical Address      Type"; ip -4 neigh show dev eth1 | awk '$3=="lladdr"{printf "  %-21s %-21s dynamic\n", $1, $4}'; echo; }
ftp() {
  [ -n "$1" ] || { echo "Usage: ftp SERVER"; return 1; }
  command -v curl >/dev/null || { echo "ftp needs curl on this PC image"; return 1; }
  __h=$1; echo "Trying to connect...$__h"
  printf "Username:"; read -r __u; printf "Password:"; stty -echo 2>/dev/null; read -r __p; stty echo 2>/dev/null; echo
  curl -s --max-time 6 -u "$__u:$__p" "ftp://$__h/" >/dev/null || { echo "%Error: login failed or no FTP service on $__h"; return 1; }
  echo "230- Logged in"; echo "(passive mode On)"
  while printf "ftp>"; read -r __c __a; do
    case "$__c" in
      dir|ls) curl -s -u "$__u:$__p" "ftp://$__h/" ;;
      get) [ -n "$__a" ] && curl -sf -u "$__u:$__p" -o "$__a" "ftp://$__h/$__a" && echo "Reading file $__a from $__h: [OK] $(wc -c < "$__a") bytes" || echo "%Error ftp://$__h/$__a (No such file or directory Or Permission denied)" ;;
      put) [ -f "$__a" ] && curl -sf -u "$__u:$__p" -T "$__a" "ftp://$__h/" && echo "Writing file $__a to $__h: [OK] $(wc -c < "$__a") bytes" || echo "%Error: no local file '$__a'" ;;
      delete) curl -sf -u "$__u:$__p" -Q "DELE $__a" "ftp://$__h/" >/dev/null && echo "Deleting file $__a from $__h: [OK]" || echo "%Error deleting $__a" ;;
      rename) set -- $__a; curl -sf -u "$__u:$__p" -Q "RNFR $1" -Q "RNTO $2" "ftp://$__h/" >/dev/null && echo "Renamed $1 to $2" || echo "%Error renaming" ;;
      quit|bye|exit) echo "221- Service closing control connection."; break ;;
      "") ;;
      *) echo "Commands: dir, get FILE, put FILE, delete FILE, rename OLD NEW, quit" ;;
    esac
  done
}
PS1='C:\> '
"""


async def interfaces(lab, node):
    rc, out, err = await dexec(lab, node, "ip", "addr")
    if rc:
        raise RuntimeError(err or out)
    ifs, cur = [], None
    for line in out.splitlines():
        m = re.match(r"^\d+:\s+([^:@]+)(?:@[^:]+)?:\s+<([^>]*)>.*?(?:state (\S+))?", line)
        if m:
            cur = {"name": m.group(1), "flags": m.group(2).split(","), "state": m.group(3) or "UNKNOWN",
                   "mac": "", "ipv4": []}
            ifs.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("link/ether"):
            cur["mac"] = s.split()[1]
        elif s.startswith("inet "):
            cur["ipv4"].append(s.split()[1])
    return [i for i in ifs if i["name"] != "eth0"]


async def running_config(lab, node):
    rc, out, err = await dexec(lab, node, "vtysh", "-c", "show running-config")
    if rc:
        raise RuntimeError(err or out)
    return out


async def apply_config(lab, node, commands: list[str]):
    cmds = [c.rstrip() for c in commands if c.strip()]
    for c in cmds:
        if "\n" in c:
            raise ValueError("one command per line")
    args = ["vtysh", "-c", "configure terminal"]
    for c in cmds:
        args += ["-c", c]
    rc, out, err = await dexec(lab, node, *args)
    return {"ok": rc == 0, "output": (out + err).strip()}


def strip_running(text: str) -> str:
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.startswith("Current configuration"):
            lines = lines[i + 1:]
            break
    return "\n".join(lines).strip() + "\n"


async def save_configs(lab: dict) -> dict:
    """Write each running router's running-config to its frr.conf on the host.
    Returns saved / failed {node: reason} / skipped (not running) - nothing is silently dropped."""
    states = await container_states(lab["name"])
    res = {"saved": [], "failed": {}, "skipped": []}
    for n in lab["nodes"]:
        p = labs.frr_conf_path(lab, n)
        if p is None:
            continue
        if states.get(n["id"]) != "running":
            res["skipped"].append(n["id"])
            continue
        try:
            cfg = strip_running(await running_config(lab["name"], n["id"]))
            if "hostname" not in cfg and "frr " not in cfg:
                raise RuntimeError("running-config looks empty, not overwriting the saved file")
            labs.write_inplace(p, cfg)
            res["saved"].append(n["id"])
        except Exception as e:  # report, never swallow
            res["failed"][n["id"]] = str(e).strip()[:300] or type(e).__name__
    return res


async def ipconfig(lab, node, body: dict):
    mode = body.get("mode", "static")
    script = ["ip addr flush dev eth1", "ip route del default 2>/dev/null"]
    if mode == "static":
        script.append(f"ip addr add {check_ip(body['ip'], cidr=True)} dev eth1")
        if body.get("gateway"):
            script.append(f"ip route replace default via {check_ip(body['gateway'])} dev eth1")
        if body.get("dns"):
            script.append(f"echo nameserver {check_ip(body['dns'])} > /etc/resolv.conf")
    else:
        # labs deployed before 0.10.1 don't have the hook mounted: install it under /tmp
        await dexec(lab, node, "sh", "-c", "cat > /tmp/netlab-dhcp && chmod +x /tmp/netlab-dhcp", input=labs.DHCP_SCRIPT.encode())
        script.append('udhcpc -i eth1 -n -q -t 4 -T 2 -x "hostname:$(hostname)" -s /tmp/netlab-dhcp')
    rc, out, err = await dsh(lab, node, "; ".join(script) + "; ip -4 addr show eth1; ip route", timeout=30)
    return {"ok": rc == 0, "output": (out + err).strip()}


async def apply_services(labd: dict, node: dict):
    labs.write_node_files(labd, node)
    rc, out, err = await dsh(labd["name"], node["id"], "[ -f /etc/netlab/svc/services.sh ] && sh /etc/netlab/svc/services.sh", timeout=120)
    if rc == 0:
        return {"ok": True, "output": (out + err).strip()}
    svc = node.get("props", {}).get("services", {})
    note = ("\nTFTP, Syslog and NTP need this server redeployed (it was started by an older version)."
            if any((svc.get(k) or {}).get("enabled") for k in ("tftp", "ftp", "syslog", "ntp")) else "")
    http_on = node.get("props", {}).get("services", {}).get("http", {}).get("enabled", True)
    web = ("nginx -c /etc/netlab/nginx.conf -s reload 2>/dev/null || nginx -c /etc/netlab/nginx.conf"
           if http_on else "nginx -c /etc/netlab/nginx.conf -s stop 2>/dev/null; true")
    script = f"mkdir -p /etc/netlab/svc; pkill dnsmasq; sleep 0.3; dnsmasq --conf-file=/etc/netlab/dnsmasq.conf; {web}"
    rc, out, err = await dsh(labd["name"], node["id"], script)
    return {"ok": rc == 0, "output": (out + err).strip() + note}


def server_files(labd: dict, node: dict, kind: str) -> list[dict]:
    d = labs.workdir(labd) / "configs" / node["id"] / kind
    if not d.is_dir():
        return []
    return [{"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime} for f in sorted(d.iterdir()) if f.is_file()]


SERVICE_PROCS = {"dns": "dnsmasq", "dhcp": "dnsmasq", "http": "nginx", "tftp": "in.tftpd", "ftp": "vsftpd", "syslog": "rsyslogd", "ntp": "chronyd"}


async def services_status(labd: dict, node: dict) -> dict:
    """Which service daemons are actually running in the server container."""
    c = (await netinfo.containers()).get((labd["name"], node["id"]))
    if not c or c["state"] != "running":
        return {"running": False, "services": {}}
    rc, out, _ = await dsh(labd["name"], node["id"], "ps -o comm= 2>/dev/null || ps -e -o comm=", timeout=10)
    procs = {l.strip() for l in out.splitlines()}
    return {"running": True, "services": {k: v in procs for k, v in SERVICE_PROCS.items()}}


def dhcp_leases(labd: dict, node: dict) -> list[dict]:
    f = labs.workdir(labd) / "configs" / node["id"] / "svc" / "dnsmasq.leases"
    return labs.leases(f.read_text()) if f.exists() else []


def syslog_tail(labd: dict, node: dict, lines: int = 300) -> list[str]:
    f = labs.workdir(labd) / "configs" / node["id"] / "syslog" / "syslog.log"
    if not f.exists():
        return []
    with open(f, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 200_000))
        data = fh.read().decode(errors="replace").splitlines()
    return data[-lines:]


async def browse(lab, node, url: str):
    if not re.match(r"^https?://[A-Za-z0-9.\-:/_%?=&#~+]*$", url):
        url = "http://" + url
    if not re.match(r"^https?://[A-Za-z0-9.\-:/_%?=&#~+]*$", url):
        raise ValueError("invalid URL")
    rc, out, err = await dexec(lab, node, "curl", "-s", "-L", "-m", "8", "-w", "\n__NETLAB_STATUS__%{http_code}", url, timeout=15)
    body, _, status = out.rpartition("\n__NETLAB_STATUS__")
    if rc and not body:
        return {"ok": False, "status": 0, "body": "", "error": err.strip() or f"Request timeout ({url})"}
    return {"ok": True, "status": int(status or 0), "body": body}


async def ping(lab, node, dst: str, count: int = 1):
    check_ip(dst) if re.match(r"^[0-9.]+$", dst) else check_host(dst)
    if dst.startswith("-"):
        raise ValueError("invalid destination")
    count = max(1, min(int(count), 20))
    rc, out, err = await dexec(lab, node, "ping", "-c", str(count), "-W", "2", dst, timeout=10 + count * 2)
    return {"ok": rc == 0, "output": (out + err).strip()}


async def link_status(lab: dict) -> dict:
    states = await container_states(lab["name"])
    script = 'for i in /sys/class/net/*; do echo "$(basename $i) $(cat $i/operstate)"; done'

    async def one(nid):
        if states.get(nid) != "running":
            return nid, {}
        rc, out, _ = await dsh(lab["name"], nid, script, timeout=8)
        return nid, dict(l.split(" ", 1) for l in out.splitlines() if " " in l)

    results = dict(await asyncio.gather(*(one(n["id"]) for n in lab["nodes"])))
    return {"nodes": states, "ifaces": results}


async def write_pc_rc(lab, node):
    await dexec(lab, node, "sh", "-c", "cat > /etc/netlab.rc", input=PC_RC.encode())
    await dexec(lab, node, "sh", "-c", "[ -x /usr/local/bin/netlab-dhcp ] || { cat > /tmp/netlab-dhcp && chmod +x /tmp/netlab-dhcp; }",
                input=labs.DHCP_SCRIPT.encode())


# ---------------- firewall ----------------
async def apply_firewall(labd: dict, node: dict):
    labs.write_node_files(labd, node)
    rc, out, err = await dsh(labd["name"], node["id"], "sh /etc/netlab/fw.sh 2>&1")
    return {"ok": rc == 0, "output": (out + err).strip()}


async def firewall_status(labd: dict, node: dict):
    from . import firewall
    c = (await netinfo.containers()).get((labd["name"], node["id"]))
    if not c or c["state"] != "running" or not c["pid"]:
        return {"running": False, "counters": {}, "conntrack": None, "ruleset": ""}
    rc, js, _ = await run("nsenter", "-t", str(c["pid"]), "-n", "nft", "-j", "list", "ruleset", timeout=10)
    rc2, txt, _ = await run("nsenter", "-t", str(c["pid"]), "-n", "nft", "list", "ruleset", timeout=10)
    ct = None
    try:
        ct = int(open(f"/proc/{c['pid']}/root/proc/sys/net/netfilter/nf_conntrack_count").read())
    except (OSError, ValueError):
        pass
    return {"running": True, "counters": firewall.counters(js) if rc == 0 else {}, "conntrack": ct, "ruleset": txt}


# ---------------- switching (802.1Q) ----------------
async def apply_switch(labd: dict, node: dict):
    """Rewrite the switch's VLAN script (bind-mounted, truncated in place) and run it live."""
    labs.write_node_files(labd, node)
    rc, out, err = await dsh(labd["name"], node["id"], "sh /usr/local/bin/netlab-switch; echo; bridge vlan show")
    return {"ok": rc == 0, "output": (out + err).strip()}


async def switch_show(lab, node):
    script = ("echo '== VLAN membership (PVID = access/native, Egress Untagged = untagged)'; bridge vlan show; "
              "echo; echo '== Port / STP state'; bridge link show; "
              "echo; echo '== MAC table'; bridge fdb show br br0 | grep -v permanent; "
              "echo; echo '== Management'; ip -br -4 addr show | grep -v '^eth0'")
    rc, out, err = await dsh(lab, node, script)
    return {"ok": rc == 0, "output": (out + err).strip()}


async def subif(lab, node, parent: str, vlan: int, add: bool, name: str | None = None):
    name = check_if(name or f"{check_if(parent)}.{labs.vlan_id(vlan)}")
    if add:
        script = f"ip link add link {parent} name {name} type vlan id {int(vlan)} && ip link set {name} up; ip -br link show {name}"
    else:
        script = f"ip link del {name}"
    rc, out, err = await dsh(lab, node, script)
    return {"ok": rc == 0, "output": (out + err).strip()}


async def link_status_fast(lab: dict) -> dict:
    """Container states and per-port operstate read from the host (no docker exec)."""
    ctr = await netinfo.containers()
    nodes, ifaces = {}, {}
    for n in lab["nodes"]:
        c = ctr.get((lab["name"], n["id"]))
        if not c:
            continue
        nodes[n["id"]] = c["state"]
        if c["state"] == "running" and c["pid"]:
            ifaces[n["id"]] = {k: v["state"] for k, v in netinfo.iface_counters(c["pid"]).items()}
        else:
            ifaces[n["id"]] = {}
    return {"nodes": nodes, "ifaces": ifaces}
