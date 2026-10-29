"""netlab firewall: a zone-based stateful firewall built on nftables (Alpine container).

Model (node props):
  interfaces  {ethN: {ip: "10.0.0.1/24", zone: "inside", shutdown: bool, description}}
  zones       [{name, level}]   level 0-100; with policy "levels", traffic from a higher
                                level to a lower one is allowed (ASA security-level style)
  policy      "levels" | "deny"  what happens to traffic no rule matches
  rules       [{id, from, to, proto, src, dst, ports, action, comment, enabled}]  first match wins
  nat         [{type: "masquerade", from, to} | {type: "dnat", zone, proto, port, to_ip, to_port}]
  routes      [{prefix, via}];  gateway
  allow_ping  answer ping on the firewall's own addresses

Return traffic is always allowed (stateful: ct state established,related). Port forwards
(dnat) are allowed through automatically, like a static NAT plus its ACL entry.
Everything is regenerated into /etc/netlab/fw.nft + fw.sh and applied with `nft -f`.
"""
import ipaddress, json, re
from . import hardware

ZONE_RE = re.compile(r"^[a-z][a-z0-9-]{0,14}$")
PROTOS = ("any", "tcp", "udp", "icmp")
DEFAULT = {"zones": [{"name": "inside", "level": 100}, {"name": "dmz", "level": 50}, {"name": "outside", "level": 0}],
           "policy": "levels", "rules": [], "nat": [], "routes": [], "interfaces": {}, "allow_ping": True, "gateway": ""}


def _cidr(v, what):
    try:
        return str(ipaddress.ip_network(v, strict=False)) if "/" in v else str(ipaddress.ip_address(v))
    except ValueError:
        raise ValueError(f"{what}: '{v}' isn't an IPv4 address or network")


def _ports(v: str) -> str:
    v = str(v or "").replace(" ", "")
    if not v:
        return ""
    for part in v.split(","):
        a, _, b = part.partition("-")
        if not a.isdigit() or (b and not b.isdigit()) or not 1 <= int(a) <= 65535 or (b and not int(a) <= int(b) <= 65535):
            raise ValueError(f"bad port list '{v}' (use 80,443 or 1000-2000)")
    return v


def normalise(props: dict) -> dict:
    """Validate and fill defaults. Raises ValueError with a readable message."""
    p = {**DEFAULT, **(props or {})}
    zones, seen = [], set()
    for z in p["zones"]:
        name = str(z.get("name", "")).lower()
        if not ZONE_RE.match(name):
            raise ValueError(f"zone name '{name}': lowercase letters, digits, dashes, up to 15")
        if name in seen:
            raise ValueError(f"zone '{name}' twice")
        seen.add(name)
        lvl = int(z.get("level", 0))
        if not 0 <= lvl <= 100:
            raise ValueError(f"zone {name}: level must be 0-100")
        zones.append({"name": name, "level": lvl})
    p["zones"] = zones
    ifs = {}
    for ifn, c in (p.get("interfaces") or {}).items():
        if not re.fullmatch(r"eth[1-9]\d*", ifn):
            raise ValueError(f"bad interface {ifn}")
        c = dict(c or {})
        if c.get("ip"):
            ipaddress.ip_interface(c["ip"])
            if "/" not in c["ip"]:
                raise ValueError(f"{ifn}: give the address as a.b.c.d/len")
        if c.get("zone") and c["zone"] not in seen:
            raise ValueError(f"{ifn}: unknown zone {c['zone']}")
        ifs[ifn] = {"ip": c.get("ip", ""), "zone": c.get("zone", ""), "shutdown": bool(c.get("shutdown")),
                    "description": str(c.get("description", ""))[:120]}
    p["interfaces"] = ifs
    if p["policy"] not in ("levels", "deny"):
        raise ValueError("policy must be levels or deny")
    rules = []
    for i, r in enumerate(p.get("rules") or []):
        for k in ("from", "to"):
            if r.get(k, "any") != "any" and r[k] not in seen:
                raise ValueError(f"rule {i + 1}: unknown zone {r[k]}")
        proto = r.get("proto", "any")
        if proto not in PROTOS:
            raise ValueError(f"rule {i + 1}: protocol must be any, tcp, udp or icmp")
        ports = _ports(r.get("ports", ""))
        if ports and proto not in ("tcp", "udp"):
            raise ValueError(f"rule {i + 1}: ports need tcp or udp")
        if r.get("action", "accept") not in ("accept", "drop", "reject"):
            raise ValueError(f"rule {i + 1}: action must be accept, drop or reject")
        rules.append({"id": str(r.get("id") or f"r{i + 1}")[:20], "from": r.get("from", "any"), "to": r.get("to", "any"),
                      "proto": proto, "src": _cidr(r["src"], f"rule {i + 1} source") if r.get("src", "any") not in ("", "any") else "any",
                      "dst": _cidr(r["dst"], f"rule {i + 1} destination") if r.get("dst", "any") not in ("", "any") else "any",
                      "ports": ports, "action": r.get("action", "accept"), "comment": str(r.get("comment", ""))[:80],
                      "enabled": r.get("enabled", True) is not False})
    p["rules"] = rules
    nat = []
    for i, n in enumerate(p.get("nat") or []):
        if n.get("type") == "masquerade":
            for k in ("from", "to"):
                if n.get(k) not in seen:
                    raise ValueError(f"NAT {i + 1}: unknown zone {n.get(k)}")
            nat.append({"type": "masquerade", "from": n["from"], "to": n["to"]})
        elif n.get("type") == "dnat":
            if n.get("zone") not in seen:
                raise ValueError(f"port forward {i + 1}: unknown zone {n.get('zone')}")
            if n.get("proto") not in ("tcp", "udp"):
                raise ValueError(f"port forward {i + 1}: protocol must be tcp or udp")
            port, to_port = int(n.get("port") or 0), int(n.get("to_port") or n.get("port") or 0)
            if not (1 <= port <= 65535 and 1 <= to_port <= 65535):
                raise ValueError(f"port forward {i + 1}: ports must be 1-65535")
            nat.append({"type": "dnat", "zone": n["zone"], "proto": n["proto"], "port": port,
                        "to_ip": str(ipaddress.ip_address(n.get("to_ip", ""))), "to_port": to_port, "comment": str(n.get("comment", ""))[:80]})
        else:
            raise ValueError(f"NAT {i + 1}: type must be masquerade or dnat")
    p["nat"] = nat
    p["routes"] = [{"prefix": _cidr(r["prefix"], "route"), "via": str(ipaddress.ip_address(r["via"]))} for r in p.get("routes") or []]
    p["gateway"] = str(ipaddress.ip_address(p["gateway"])) if p.get("gateway") else ""
    p["allow_ping"] = bool(p.get("allow_ping", True))
    return p


def _zone_ifs(p, zone):
    return sorted(i for i, c in p["interfaces"].items() if c.get("zone") == zone)


def _set(names):
    return "{ " + ", ".join(f'"{n}"' for n in names) + " }"


def ruleset(props: dict) -> str:
    p = normalise(props)
    L = ["# generated by Patchyard - edit the firewall in the web UI (Config tab)", "flush ruleset", "",
         "table inet netlab {",
         "  chain input {",
         "    type filter hook input priority 0; policy drop;",
         '    iifname "lo" accept',
         "    ct state established,related accept",
         "    ct state invalid drop",
         '    iifname "eth0" accept comment "management network"']
    if p["allow_ping"]:
        L.append('    icmp type echo-request accept comment "answer ping"')
    L += ["  }", "", "  chain forward {", "    type filter hook forward priority 0; policy drop;",
          "    ct state established,related accept", "    ct state invalid drop",
          '    iifname "eth0" drop comment "never route via management"', '    oifname "eth0" drop']
    if any(n["type"] == "dnat" for n in p["nat"]):
        L.append('    ct status dnat accept comment "port forwards"')
    for r in p["rules"]:
        if not r["enabled"]:
            continue
        m = []
        for k, kw in (("from", "iifname"), ("to", "oifname")):
            if r[k] != "any":
                ifs = _zone_ifs(p, r[k])
                if not ifs:
                    m = None
                    break
                m.append(f"{kw} {_set(ifs)}")
        if m is None:
            continue                     # zone has no interfaces yet: rule can't match
        if r["src"] != "any":
            m.append(f"ip saddr {r['src']}")
        if r["dst"] != "any":
            m.append(f"ip daddr {r['dst']}")
        if r["proto"] in ("tcp", "udp"):
            m.append(f"{r['proto']} dport {{ {r['ports']} }}" if r["ports"] else f"meta l4proto {r['proto']}")
        elif r["proto"] == "icmp":
            m.append("meta l4proto icmp")
        act = "reject" if r["action"] == "reject" else r["action"]
        L.append(f'    {" ".join(m)} counter {act} comment "{r["id"]}"'.replace("     ", "    "))
    if p["policy"] == "levels":
        lv = {z["name"]: z["level"] for z in p["zones"]}
        for a in p["zones"]:
            for b in p["zones"]:
                if a["name"] != b["name"] and lv[a["name"]] > lv[b["name"]]:
                    ia, ib = _zone_ifs(p, a["name"]), _zone_ifs(p, b["name"])
                    if ia and ib:
                        L.append(f'    iifname {_set(ia)} oifname {_set(ib)} counter accept comment "level:{a["name"]}>{b["name"]}"')
    L += ['    counter comment "default-drop"', "  }", "", "  chain output {", "    type filter hook output priority 0; policy accept;", "  }", "}"]
    pre, post = [], []
    for n in p["nat"]:
        if n["type"] == "masquerade":
            ia, ib = _zone_ifs(p, n["from"]), _zone_ifs(p, n["to"])
            if ia and ib:
                post.append(f'    iifname {_set(ia)} oifname {_set(ib)} counter masquerade comment "nat:{n["from"]}>{n["to"]}"')
        else:
            iz = _zone_ifs(p, n["zone"])
            if iz:
                pre.append(f'    iifname {_set(iz)} {n["proto"]} dport {n["port"]} counter dnat to {n["to_ip"]}:{n["to_port"]} comment "pf:{n["port"]}"')
    if pre or post:
        L += ["", "table ip netlab_nat {",
              "  chain prerouting {", "    type nat hook prerouting priority dstnat;", *pre, "  }",
              "  chain postrouting {", "    type nat hook postrouting priority srcnat;", *post, "  }", "}"]
    return "\n".join(L) + "\n"


def script(node: dict) -> str:
    p = normalise(node.get("props") or {})
    L = ["#!/bin/sh", f"# generated by Patchyard for {node['id']} - edit in the web UI (Config tab)",
         "command -v nft >/dev/null || { echo 'nftables missing'; exit 1; }",
         "sysctl -w net.ipv4.ip_forward=1 >/dev/null",
         "ip route del default 2>/dev/null"]
    for ifn, c in sorted(p["interfaces"].items(), key=lambda x: int(x[0][3:])):
        L.append(f"if [ -e /sys/class/net/{ifn} ]; then")
        L.append(f"  ip -4 addr flush dev {ifn}")
        if c["ip"]:
            L.append(f"  ip addr add {c['ip']} dev {ifn}")
        L.append(f"  ip link set {ifn} {'down' if c['shutdown'] else 'up'}")
        L.append("fi")
    for r in p["routes"]:
        L.append(f"ip route replace {r['prefix']} via {r['via']} || echo 'route {r['prefix']} via {r['via']}: next hop not reachable yet'")
    if p["gateway"]:
        L.append(f"ip route replace default via {p['gateway']} || echo 'default gateway {p['gateway']} not reachable yet'")
    L += ["nft -f /etc/netlab/fw.nft && echo 'firewall rules loaded'"]
    return "\n".join(L) + "\n"


def counters(nft_json: str) -> dict:
    """`nft -j list ruleset` -> {comment: {"packets", "bytes"}} for rules netlab wrote."""
    out = {}
    try:
        items = json.loads(nft_json).get("nftables", [])
    except ValueError:
        return out
    for it in items:
        r = it.get("rule")
        if not r or not r.get("comment"):
            continue
        for e in r.get("expr", []):
            if "counter" in e:
                c = e["counter"]
                out[r["comment"]] = {"packets": c.get("packets", 0), "bytes": c.get("bytes", 0)}
    return out


def port_label(node, ifn):
    return hardware.display(node, ifn)
