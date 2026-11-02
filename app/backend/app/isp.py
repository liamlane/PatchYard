"""ISP and Internet devices: drag one in, cable a router to it, and that router has an
internet connection.

  isp       a service provider edge. Each customer port hands out a WAN link:
              static  a /30 (ISP .1, you .2); you set the address and a default route
              dhcp    a /29 with DHCP (home broadband): PCs and firewalls just plug in
              bgp     a /30 and eBGP: the ISP sends you a default route, you advertise your prefixes
            optionally with a routed block (/29 or /28) behind your WAN address for servers and static NAT.
            ISPs cabled to each other peer with eBGP and carry each other's customers.
  internet  the internet core. ISPs cabled to it get a default route and reach each other
            through it. It hosts the "public" services every lab can use:
              resolvers 1.1.1.1, 8.8.8.8, 9.9.9.9 (DNS), web sites (example.com, …),
              whatismyip.example.com (shows your public address: handy for NAT),
              speedtest.example.org (iperf3 server).
            An ISP with no internet node hosts these itself, so a single ISP is a whole internet.

Optionally the node that hosts the services can be bridged to the real internet (masqueraded
out of its management interface); on a public server that only works for users an admin gave
"lab internet".

Both run on the FRR image with dnsmasq, nginx and iperf3 added at start. Their configuration
is generated from the lab (props + cables) on every deploy and by "Apply" on the ISP page.
"""
import ipaddress, json, re
from . import labs

PROVIDERS = ("isp", "internet")
MODES = ("static", "dhcp", "bgp")
RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
SERVICE_NET = "198.18.0.0/15"
DEFAULT_SITES = [
    {"name": "www.example.com", "aliases": ["example.com"], "ip": "198.18.1.10", "title": "Example Domain",
     "text": "You reached www.example.com through your ISP. The internet works!"},
    {"name": "www.example.net", "aliases": ["example.net"], "ip": "198.18.1.20", "title": "example.net",
     "text": "Another public web site on the lab internet."},
    {"name": "whatismyip.example.com", "aliases": ["ip.example.com"], "ip": "198.18.1.40", "kind": "myip", "title": "What is my IP", "text": ""},
    {"name": "speedtest.example.org", "aliases": [], "ip": "198.18.1.30", "kind": "speedtest", "title": "Speed test",
     "text": "iperf3 server: iperf3 -c speedtest.example.org"},
]
TRANSIT_NET = ipaddress.ip_network("100.100.0.0/16")
INTERNET_POOL = "203.0.113.0/24"
INTERNET_ASN = 64496


def is_provider(n: dict) -> bool:
    return n.get("type") in PROVIDERS and not n.get("raw")


def _ifnum(i: str) -> int:
    m = re.search(r"(\d+)$", i)
    return int(m.group(1)) if m else 0


def _providers(lab: dict) -> list[dict]:
    return sorted([n for n in lab["nodes"] if is_provider(n)], key=lambda n: (n["type"] != "internet", n["id"]))


def defaults(lab: dict, node: dict) -> dict:
    """ASN, pool and router ID for a provider (its own props win)."""
    p = node.get("props") or {}
    isps = [n for n in _providers(lab) if n["type"] == "isp"]
    k = next((i + 1 for i, n in enumerate(isps) if n["id"] == node["id"]), 1)
    if node["type"] == "internet":
        j = next((i for i, n in enumerate(n for n in _providers(lab) if n["type"] == "internet") if n["id"] == node["id"]), 0)
        return {"asn": int(p.get("asn") or INTERNET_ASN + j), "pool": p.get("pool") or (INTERNET_POOL if not j else f"100.127.{4 * j}.0/22"),
                "rid": f"10.255.201.{j + 1}"}
    return {"asn": int(p.get("asn") or 64500 + k), "pool": p.get("pool") or f"81.{(k * 10) % 250 or 10}.0.0/16", "rid": f"10.255.200.{k}"}


def validate_lab(lab: dict):
    """Providers must not share an AS number or overlapping customer pools."""
    seen_as, seen_pool = {}, []
    for n in _providers(lab):
        d = defaults(lab, n)
        if d["asn"] in seen_as:
            raise ValueError(f"{n['id']} and {seen_as[d['asn']]} both use AS {d['asn']}: give one of them another AS number")
        seen_as[d["asn"]] = n["id"]
        net = ipaddress.ip_network(d["pool"], strict=False)
        for other, onet in seen_pool:
            if net.overlaps(onet):
                raise ValueError(f"{n['id']} pool {net} overlaps {other}'s {onet}")
        seen_pool.append((n["id"], net))


def validate(node: dict):
    p = node.get("props") or {}
    if p.get("asn") not in (None, ""):
        a = int(p["asn"])
        if not 1 <= a <= 4294967294:
            raise ValueError(f"{node['id']}: ASN out of range")
    if p.get("pool"):
        net = ipaddress.ip_network(p["pool"], strict=True)
        longest = 24 if node.get("type") == "internet" else 22
        if net.version != 4 or not 8 <= net.prefixlen <= longest:
            raise ValueError(f"{node['id']}: the customer pool must be an IPv4 /8 to /{longest}")
    for i, c in (p.get("ports") or {}).items():
        c = c or {}
        if c.get("mode", "static") not in MODES:
            raise ValueError(f"{node['id']} {i}: mode must be static, dhcp or bgp")
        if c.get("routed") not in (None, "", "29", "28", 29, 28):
            raise ValueError(f"{node['id']} {i}: routed block must be /29 or /28")
        if c.get("customer_as") not in (None, ""):
            int(c["customer_as"])
    if p.get("uplink") not in (None, "simulated", "real"):
        raise ValueError(f"{node['id']}: uplink must be simulated or real")
    for s in p.get("sites") or []:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,120}[a-z0-9]", str(s.get("name", "")).lower()):
            raise ValueError(f"{node['id']}: bad site name {s.get('name')!r}")
        ip = ipaddress.ip_address(s.get("ip", ""))
        if ip.version != 4 or ip.is_private and ip not in ipaddress.ip_network(SERVICE_NET) or ip.is_loopback or ip.is_multicast or ip in TRANSIT_NET:
            raise ValueError(f"{node['id']}: site {s.get('name')} needs a public-style address (198.18.x.x is free for the lab), not {ip}")


def plan(lab: dict) -> dict:
    """Addressing and peering for every provider: {node_id: {...}}. Pure: the same lab gives the same plan."""
    provs = _providers(lab)
    pid = {n["id"]: n for n in provs}
    out = {}
    for n in provs:
        d = defaults(lab, n)
        out[n["id"]] = {"id": n["id"], "type": n["type"], **d, "ports": [], "transit": [], "hosts": False, "upstream": None,
                        "uplink": (n.get("props") or {}).get("uplink") or "simulated"}
    # provider <-> provider links (eBGP), numbered in a stable order
    tl = sorted([l for l in lab["links"] if l["a"] in pid and l["b"] in pid], key=lambda l: (min(l["a"], l["b"]), max(l["a"], l["b"]), l["id"]))
    for t, l in enumerate(tl):
        net = list(TRANSIT_NET.subnets(new_prefix=30))[t]
        lo, hi = (l["a"], l["a_if"]), (l["b"], l["b_if"])
        if lo[0] > hi[0]:
            lo, hi = hi, lo
        h = list(net.hosts())
        for (me, mif), (peer, pif), ip, pip in ((lo, hi, h[0], h[1]), (hi, lo, h[1], h[0])):
            out[me]["transit"].append({"if": mif, "peer": peer, "peer_if": pif, "ip": f"{ip}/30", "peer_ip": str(pip),
                                       "peer_as": out[peer]["asn"], "peer_type": pid[peer]["type"]})
    cust_order = sorted(n["id"] for n in lab["nodes"] if n["id"] not in pid)
    has_inet = {n["id"]: any(t["peer_type"] == "internet" for t in out[n["id"]]["transit"]) for n in provs}
    for n in provs:
        o = out[n["id"]]
        o["hosts"] = n["type"] == "internet" or not has_inet[n["id"]]
        props = n.get("props") or {}
        pool = ipaddress.ip_network(o["pool"])
        half = pool.network_address + pool.num_addresses // 2
        cust = sorted([l for l in lab["links"] if (l["a"] == n["id"]) != (l["b"] == n["id"]) and
                       (l["b"] if l["a"] == n["id"] else l["a"]) not in pid], key=lambda l: _ifnum(l["a_if"] if l["a"] == n["id"] else l["b_if"]))
        for l in cust:
            mine = l["a"] == n["id"]
            mif, peer, pif = (l["a_if"], l["b"], l["b_if"]) if mine else (l["b_if"], l["a"], l["a_if"])
            i = _ifnum(mif)
            c = (props.get("ports") or {}).get(mif) or {}
            mode = c.get("mode") or "static"
            base = pool.network_address + 8 * i
            if base + 8 > half:
                raise ValueError(f"{n['id']}: port {mif} is past the end of the customer pool {pool}")
            port = {"if": mif, "peer": peer, "peer_if": pif, "mode": mode}
            if mode == "dhcp":
                port.update(ip=f"{base + 1}/29", gw=str(base + 1), mask="255.255.255.248", range=[str(base + 2), str(base + 6)],
                            net=f"{base}/29")
            else:
                port.update(ip=f"{base + 1}/30", gw=str(base + 1), cust_ip=str(base + 2), mask="255.255.255.252", net=f"{base}/30")
            if mode == "bgp":
                # default AS follows the customer device, so a router dual-homed to two ISPs keeps one AS
                # and different customers never share one (they'd drop each other's routes as AS-path loops)
                port["customer_as"] = int(c.get("customer_as") or 65000 + 10 * (cust_order.index(peer) + 1))
            if str(c.get("routed") or "") in ("29", "28") and mode != "dhcp":
                size = 8 if str(c["routed"]) == "29" else 16
                rb = half + 16 * i
                if rb + size > pool.broadcast_address + 1:
                    raise ValueError(f"{n['id']}: no room for a routed block on {mif} in {pool}; use a bigger pool")
                port["routed"] = f"{rb}/{32 - (size.bit_length() - 1)}"
            o["ports"].append(port)
        o["dns"] = RESOLVERS[:2]
        up = next((t for t in o["transit"] if t["peer_type"] == "internet"), None)
        if up and n["type"] == "isp":
            o["upstream"] = up["peer_ip"]
        o["sites"] = sites(n) if o["hosts"] else []
    return out


def sites(node: dict) -> list[dict]:
    p = node.get("props") or {}
    extra = [{"name": s["name"].lower(), "aliases": [], "ip": s["ip"], "title": s.get("title") or s["name"], "text": s.get("text") or ""}
             for s in p.get("sites") or []]
    return DEFAULT_SITES + extra


# ------------------------------------------------------------------ generated files

def frr_conf(lab: dict, node: dict, pl: dict) -> str:
    o = pl[node["id"]]
    L = ["frr defaults traditional", f"hostname {node['id']}", "log syslog informational", "no ipv6 forwarding",
         "service integrated-vtysh-config", "!"]
    for t in sorted(o["transit"], key=lambda t: _ifnum(t["if"])):
        L += [f"interface {t['if']}", f" description transit to {t['peer']} (AS{t['peer_as']})", f" ip address {t['ip']}", "exit", "!"]
    for p in o["ports"]:
        L += [f"interface {p['if']}", f" description customer {p['peer']} {p['peer_if']} ({p['mode']})", f" ip address {p['ip']}", "exit", "!"]
    L += ["interface lo", f" ip address {o['rid']}/32"]
    if o["hosts"]:
        L += [f" ip address {r}/32" for r in RESOLVERS] + [f" ip address {s['ip']}/32" for s in o["sites"]]
    L += ["exit", "!", f"ip route {o['pool']} blackhole"]
    if o["hosts"]:
        L.append(f"ip route {SERVICE_NET} blackhole")
    for p in o["ports"]:
        if p.get("routed"):
            L.append(f"ip route {p['routed']} {p['cust_ip']}")
    L += ["!", f"router bgp {o['asn']}", f" bgp router-id {o['rid']}", " no bgp ebgp-requires-policy", " no bgp network import-check"]
    nbrs = [(t["peer_ip"], t["peer_as"], f"{t['peer']} transit") for t in o["transit"]]
    nbrs += [(p["cust_ip"], p["customer_as"], f"customer {p['peer']}") for p in o["ports"] if p["mode"] == "bgp"]
    for ip, asn, desc in nbrs:
        L += [f" neighbor {ip} remote-as {asn}", f" neighbor {ip} description {desc}"]
    L += [" !", " address-family ipv4 unicast", f"  network {o['pool']}"]
    if o["hosts"]:
        L.append(f"  network {SERVICE_NET}")
        L += [f"  network {r}/32" for r in RESOLVERS]
    for p in o["ports"]:
        if p.get("routed"):
            L.append(f"  network {p['routed']}")
    for t in o["transit"]:
        if node["type"] == "internet":
            L.append(f"  neighbor {t['peer_ip']} default-originate")
    for p in o["ports"]:
        if p["mode"] == "bgp":
            L.append(f"  neighbor {p['cust_ip']} default-originate")
    L += [" exit-address-family", "exit", "!"]
    return "\n".join(L) + "\n"


def _page(title: str, text: str, name: str) -> str:
    import html
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<body style='font:16px system-ui;margin:40px;background:#f5f7fa;color:#1d2733'><h1>{html.escape(title)}</h1>"
            f"<p>{html.escape(text)}</p><p style='color:#667;font-size:13px'>{html.escape(name)} · on the Patchyard lab internet</p></body>\n")


def nginx_conf(o: dict) -> str:
    L = ["worker_processes 1;", "pid /run/nginx-isp.pid;", "events { worker_connections 256; }", "http {",
         "  access_log off; error_log /dev/null;", "  default_type text/html;"]
    for s in o["sites"]:
        names = " ".join([s["name"]] + s.get("aliases", []))
        if s.get("kind") == "myip":
            body = "  location / { default_type text/plain; return 200 \"$remote_addr\\n\"; }"
        else:
            body = f"  root /etc/netlab-isp/www/{s['name']};"
        L += [f"  server {{ listen {s['ip']}:80; server_name {names}; {body} }}"]
    return "\n".join(L + ["}"]) + "\n"


def dnsmasq_conf(o: dict) -> str:
    L = ["# generated by Patchyard", "bind-dynamic", "no-hosts", "log-facility=/dev/null", "pid-file=/run/dnsmasq-isp.pid"]
    if o["hosts"]:
        L += [f"listen-address={r}" for r in RESOLVERS]
        for s in o["sites"]:
            for nm in [s["name"]] + s.get("aliases", []):
                L.append(f"host-record={nm},{s['ip']}")
        if o["uplink"] != "real":
            L.append("no-resolv")                       # the simulated internet only knows its own names
    else:
        L += ["port=0"]                                  # DHCP only; customers use the resolvers upstream
    dh = [p for p in o["ports"] if p["mode"] == "dhcp"]
    for p in dh:
        tag = p["if"].replace(".", "_")
        L += [f"interface={p['if']}", f"dhcp-range=set:{tag},{p['range'][0]},{p['range'][1]},{p['mask']},1h",
              f"dhcp-option=tag:{tag},3,{p['gw']}", f"dhcp-option=tag:{tag},6,{','.join(o['dns'])}"]
    if dh:
        L += ["dhcp-authoritative", "dhcp-leasefile=/etc/netlab-isp/leases"]
    return "\n".join(L) + "\n"


def script(o: dict) -> str:
    real = o["hosts"] and o["uplink"] == "real"
    return f"""#!/bin/sh
# generated by Patchyard: ISP/internet services ({o['type']} {o['id']})
cd /etc/netlab-isp
pkill -F /run/dnsmasq-isp.pid 2>/dev/null; pkill -F /run/nginx-isp.pid 2>/dev/null; pkill -x iperf3 2>/dev/null
{"ip route replace default via $(cat /etc/netlab-isp/mgmt-gw) dev eth0 2>/dev/null" if real else "ip route del default dev eth0 2>/dev/null"}
{"; ".join(f"ip addr add {a}/32 dev lo 2>/dev/null" for a in (RESOLVERS + [x['ip'] for x in o['sites']])) if o['hosts'] else 'true'}
nft delete table ip netlab_isp 2>/dev/null
{'nft -f /etc/netlab-isp/nat.nft' if real else 'true'}
sleep 1
{'dnsmasq -C /etc/netlab-isp/dnsmasq.conf' if (o['hosts'] or any(p['mode'] == 'dhcp' for p in o['ports'])) else 'true'}
{'nginx -c /etc/netlab-isp/nginx.conf' if o['hosts'] else 'true'}
{"iperf3 -s -D -B " + next((s['ip'] for s in o['sites'] if s.get('kind') == 'speedtest'), '198.18.1.30') if o['hosts'] else 'true'}
true
"""


NAT_NFT = """table ip netlab_isp {
  chain post { type nat hook postrouting priority 100; policy accept; oifname "eth0" masquerade; }
}
"""


def write_files(lab: dict, node: dict, pl: dict | None = None):
    pl = pl or plan(lab)
    o = pl[node["id"]]
    wd = labs.workdir(lab) / "configs" / node["id"]
    labs.write_inplace(wd / "frr.conf", frr_conf(lab, node, pl))
    for name in ("daemons", "vtysh.conf"):
        src = labs.workdir(lab) / "configs" / "common" / name
        if src.exists():
            labs.write_inplace(wd / name, src.read_text())
    d = wd / "isp"
    labs.write_inplace(d / "isp.sh", script(o))
    labs.write_inplace(d / "dnsmasq.conf", dnsmasq_conf(o))
    labs.write_inplace(d / "nginx.conf", nginx_conf(o))
    labs.write_inplace(d / "nat.nft", NAT_NFT)
    labs.write_inplace(d / "mgmt-gw", _mgmt_gw(lab) + "\n")
    for s in o["sites"]:
        if s.get("kind") != "myip":
            labs.write_inplace(d / "www" / s["name"] / "index.html", _page(s["title"], s["text"], s["name"]))
    labs.write_inplace(d / "plan.json", json.dumps(o, indent=2))


def _mgmt_gw(lab: dict) -> str:
    from . import sandbox, settings
    if sandbox.active(lab) and lab.get("sandbox_subnet"):
        return sandbox.gateway(lab["sandbox_subnet"])
    sn = lab.get("mgmt_subnet") or settings.get("lab_defaults.mgmt_subnet", "172.31.255.0/24")
    return str(next(ipaddress.ip_network(sn).hosts()))


def clab_node(lab: dict, node: dict, image: str, router_exec: list[str]) -> dict:
    o = (plan(lab).get(node["id"]) or {})
    real = o.get("hosts") and o.get("uplink") == "real"
    ex = [c for c in router_exec if not (real and c.startswith("ip route del default"))]
    return {"kind": "linux", "image": image, "binds": [f"configs/{node['id']}:/etc/frr", f"configs/{node['id']}/isp:/etc/netlab-isp"],
            # packages first, while the management default route still exists (private installs reach the mirrors directly)
            "exec": ["apk add --no-cache dnsmasq nginx iperf3 nftables"] + ex + ["sh /etc/netlab-isp/isp.sh"]}


async def apply(lab: dict, node_id: str | None = None) -> list[str]:
    """Regenerate and load the config of one provider (or all) in the running lab."""
    from .util import dexec
    from . import netinfo
    pl = plan(lab)
    ctrs = await netinfo.containers()
    notes = []
    for n in _providers(lab):
        if node_id and n["id"] != node_id:
            continue
        write_files(lab, n, pl)
        c = ctrs.get((lab["name"], n["id"]))
        if not c or c["state"] != "running":
            continue
        rc, o, e = await dexec(lab["name"], n["id"], "sh", "-c",
                               "python3 /usr/lib/frr/frr-reload.py --reload /etc/frr/frr.conf >/dev/null 2>&1 || vtysh -f /etc/frr/frr.conf >/dev/null; sh /etc/netlab-isp/isp.sh",
                               timeout=120)
        notes.append(f"{n['id']}: {'applied' if rc == 0 else (e or o).strip()[:200]}")
    return notes


def customer_help(o: dict, port: dict, lab: dict) -> dict:
    """What the customer has to configure, as IOS commands for a router (or plain words for a host)."""
    peer = next((n for n in lab["nodes"] if n["id"] == port["peer"]), {})
    from . import hardware
    pname = next((p["name"] for p in (hardware.ports(peer) or []) if p["if"] == port["peer_if"]), port["peer_if"])
    ios = []
    if port["mode"] == "dhcp":
        text = f"Set {port['peer']} {pname} to DHCP: it gets an address from {port['range'][0]}-{port['range'][1]}, gateway {port['gw']}, DNS {', '.join(o['dns'])}."
        if peer.get("type") not in ("pc", "server"):
            text += " Routers and firewalls in Patchyard have no DHCP client, so choose Static or BGP for this one; DHCP is for PCs and servers."
    else:
        text = f"{port['peer']} {pname}: {port['cust_ip']}/30 ({port['mask']}), gateway {port['gw']}, DNS {', '.join(o['dns'])}."
        ios = [f"interface {pname}", f" ip address {port['cust_ip']} {port['mask']}", " no shutdown", " exit"]
        if port["mode"] == "bgp":
            ios += [f"router bgp {port['customer_as']}", f" neighbor {port['gw']} remote-as {o['asn']}", " exit"]
        else:
            ios += [f"ip route 0.0.0.0 0.0.0.0 {port['gw']}"]
        if port.get("routed"):
            text += f" Routed block {port['routed']} is routed to you: use it for servers or static NAT."
    return {"text": text, "ios": ios, "peer_type": peer.get("type"), "peer_name": pname}
