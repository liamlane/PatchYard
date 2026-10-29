"""Router layer-3 services on top of FRR: access lists, NAT, DHCP relay and VRFs.

FRR has none of the first three, so they live in the router's props and are applied
from outside the container (the router image stays stock FRR):

  props.acls   {name: {"type": "standard"|"extended", "entries": [{"seq": 10, "text": "permit 10.0.0.0 0.0.0.255"}]}}
  props.ifcfg  {ifn: {"acl_in", "acl_out", "nat": "inside"|"outside", "helpers": [ip], "vrf": name}}
  props.nat    {"pools": {name: {"start", "end", "mask"}}, "rules": [rule]}
               rule: {"type": "list", "acl", "interface"|"pool", "overload"} |
                     {"type": "static", "local", "global"} |
                     {"type": "static-port", "proto", "local", "lport", "global"|"interface", "gport"}
  props.vrfs   {name: {"rd", "import": [rt], "export": [rt], "table": int, "style": "definition"|"ip"}}

ACLs and NAT become an nftables ruleset loaded into the router's network namespace with
the app's own nft (nsenter -n), so packet counters can be read back for `show access-lists`.
ACLs are stateless and end in an implicit deny, like IOS: a denied packet gets an ICMP
administratively-prohibited reply ("U" in an IOS ping). DHCP relay is dnsmasq in relay-only
mode, also started in the router's namespace. VRFs are Linux VRF devices, which FRR picks up.
"""
import ipaddress, json, os, re, tempfile

# ------------------------------------------------------------------ ACL parsing

TCP_PORTS = {"bgp": 179, "chargen": 19, "cmd": 514, "daytime": 13, "discard": 9, "domain": 53, "echo": 7, "exec": 512,
             "finger": 79, "ftp": 21, "ftp-data": 20, "gopher": 70, "hostname": 101, "ident": 113, "irc": 194, "klogin": 543,
             "kshell": 544, "login": 513, "lpd": 515, "nntp": 119, "pop2": 109, "pop3": 110, "smtp": 25, "sunrpc": 111,
             "tacacs": 49, "talk": 517, "telnet": 23, "time": 37, "uucp": 540, "whois": 43, "www": 80, "ssh": 22, "https": 443}
UDP_PORTS = {"biff": 512, "bootpc": 68, "bootps": 67, "discard": 9, "dnsix": 195, "domain": 53, "echo": 7, "isakmp": 500,
             "mobile-ip": 434, "nameserver": 42, "netbios-dgm": 138, "netbios-ns": 137, "netbios-ss": 139, "non500-isakmp": 4500,
             "ntp": 123, "pim-auto-rp": 496, "rip": 520, "snmp": 161, "snmptrap": 162, "sunrpc": 111, "syslog": 514,
             "tacacs": 49, "talk": 517, "tftp": 69, "time": 37, "who": 513, "xdmcp": 177}
PROTOS = {"ip": None, "tcp": 6, "udp": 17, "icmp": 1, "igmp": 2, "gre": 47, "esp": 50, "ahp": 51, "eigrp": 88, "ospf": 89,
          "pim": 103, "nos": 94, "ipinip": 4, "pcp": 108}
ICMP_TYPES = {"echo": 8, "echo-reply": 0, "unreachable": 3, "time-exceeded": 11, "ttl-exceeded": 11, "redirect": 5,
              "parameter-problem": 12, "source-quench": 4, "timestamp-request": 13, "timestamp-reply": 14}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")


class AclError(ValueError):
    pass


def acl_kind(name: str) -> str | None:
    """Numbered ACLs carry their type in the number (1-99/1300-1999 standard, 100-199/2000-2699 extended)."""
    if str(name).isdigit():
        n = int(name)
        if 1 <= n <= 99 or 1300 <= n <= 1999:
            return "standard"
        if 100 <= n <= 199 or 2000 <= n <= 2699:
            return "extended"
        raise AclError(f"% Invalid access list number {n}")
    if not NAME_RE.match(str(name)):
        raise AclError(f"% Invalid access list name '{name}'")
    return None


def _ip(t):
    try:
        return str(ipaddress.IPv4Address(t))
    except ValueError:
        raise AclError(f"% Invalid IP address '{t}'")


def _addr(toks, i, standard=False):
    """any | host A | A W (standard ACLs: A alone means host A). -> ((ip, wildcard) or None, next i)"""
    if i >= len(toks):
        raise AclError("% Incomplete command.")
    t = toks[i].lower()
    if t == "any":
        return None, i + 1
    if t == "host":
        if i + 1 >= len(toks):
            raise AclError("% Incomplete command.")
        return (_ip(toks[i + 1]), "0.0.0.0"), i + 2
    a = _ip(toks[i])
    if i + 1 < len(toks) and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", toks[i + 1]):
        return (a, _ip(toks[i + 1])), i + 2
    if standard:
        return (a, "0.0.0.0"), i + 1
    raise AclError("% Incomplete command: give a wildcard mask, 'host A.B.C.D' or 'any'")


def _port(t, proto):
    if t.isdigit() and 0 <= int(t) <= 65535:
        return int(t)
    names = TCP_PORTS if proto == "tcp" else UDP_PORTS
    if t.lower() in names:
        return names[t.lower()]
    raise AclError(f"% Invalid port '{t}' for {proto}")


def _portop(toks, i, proto):
    """eq P [P..] | neq P | gt P | lt P | range P P -> (op, [ports]) or None"""
    if i >= len(toks) or proto not in ("tcp", "udp"):
        return None, i
    op = toks[i].lower()
    if op not in ("eq", "neq", "gt", "lt", "range"):
        return None, i
    i += 1
    if op == "range":
        if i + 1 >= len(toks):
            raise AclError("% Incomplete command.")
        a, b = _port(toks[i], proto), _port(toks[i + 1], proto)
        if a > b:
            raise AclError("% Invalid port range")
        return ("range", [a, b]), i + 2
    ports = []
    while i < len(toks) and (toks[i].isdigit() or toks[i].lower() in (TCP_PORTS if proto == "tcp" else UDP_PORTS)) and (op == "eq" or not ports):
        if toks[i].lower() in ("any", "host"):
            break
        # an IP address after the source port is the destination, not a port
        ports.append(_port(toks[i], proto))
        i += 1
    if not ports:
        raise AclError("% Incomplete command: port missing")
    return (op, ports), i


def parse_entry(kind: str, text: str) -> dict:
    """One ACE ('permit tcp any host 10.0.0.1 eq 23') -> dict. Raises AclError."""
    toks = text.split()
    if not toks:
        raise AclError("% Incomplete command.")
    act = toks[0].lower()
    if act == "remark":
        return {"action": "remark", "text": " ".join(toks[1:])[:100]}
    if act not in ("permit", "deny"):
        raise AclError("% Invalid input: expected permit, deny or remark")
    e = {"action": act, "proto": "ip", "src": None, "dst": None, "sport": None, "dport": None, "icmp": None,
         "established": False, "log": False}
    if kind == "standard":
        e["src"], i = _addr(toks, 1, standard=True)
    else:
        if len(toks) < 2:
            raise AclError("% Incomplete command.")
        p = toks[1].lower()
        if p.isdigit() and 0 <= int(p) <= 255:
            e["proto"] = int(p)
        elif p in PROTOS:
            e["proto"] = p
        else:
            raise AclError(f"% Invalid protocol '{toks[1]}'")
        e["src"], i = _addr(toks, 2)
        e["sport"], i = _portop(toks, i, e["proto"])
        e["dst"], i = _addr(toks, i)
        e["dport"], i = _portop(toks, i, e["proto"])
        if e["proto"] == "icmp" and i < len(toks) and toks[i].lower() in ICMP_TYPES:
            e["icmp"] = toks[i].lower(); i += 1
    while i < len(toks):
        t = toks[i].lower()
        if t == "established" and e["proto"] == "tcp":
            e["established"] = True
        elif t in ("log", "log-input"):
            e["log"] = True
        else:
            raise AclError(f"% Invalid input detected at '{toks[i]}'")
        i += 1
    return e


def normalise_acls(acls: dict) -> dict:
    out = {}
    for name, a in (acls or {}).items():
        kind = acl_kind(name) or a.get("type")
        if kind not in ("standard", "extended"):
            raise AclError(f"% access list {name}: type must be standard or extended")
        ents, seen = [], set()
        for e in a.get("entries") or []:
            seq = int(e["seq"])
            if not 1 <= seq <= 2147483647 or seq in seen:
                raise AclError(f"% access list {name}: bad or duplicate sequence {seq}")
            seen.add(seq)
            parse_entry(kind, e["text"])
            ents.append({"seq": seq, "text": " ".join(str(e["text"]).split())})
        out[str(name)] = {"type": kind, "entries": sorted(ents, key=lambda x: x["seq"])}
    return out


def add_entry(acls: dict, name: str, text: str, seq: int | None = None, kind: str | None = None) -> dict:
    """Append (or insert at seq) an ACE; returns the new acls dict."""
    acls = {k: {"type": v["type"], "entries": list(v["entries"])} for k, v in (acls or {}).items()}
    k = acl_kind(name) or kind or (acls.get(name) or {}).get("type")
    if not k:
        raise AclError(f"% access list {name} doesn't exist; create it with 'ip access-list standard|extended {name}'")
    cur = acls.setdefault(str(name), {"type": k, "entries": []})
    if cur["type"] != k:
        raise AclError(f"% {name} is a {cur['type']} access list")
    parse_entry(k, text)
    text = " ".join(text.split())
    ents = cur["entries"]
    if any(e["text"] == text for e in ents) and seq is None:
        return acls                                   # IOS ignores an exact duplicate
    if seq is None:
        seq = (max([e["seq"] for e in ents] or [0]) // 10 + 1) * 10
    if any(e["seq"] == seq for e in ents):
        raise AclError(f"% Duplicate sequence number {seq}")
    ents.append({"seq": int(seq), "text": text})
    ents.sort(key=lambda x: x["seq"])
    return acls


def _wild_text(a):
    if a is None:
        return "any"
    ip, w = a
    return f"host {ip}" if w == "0.0.0.0" else f"{ip} {w}"


def show_entry(kind, text) -> str:
    """IOS 'show access-lists' wording."""
    e = parse_entry(kind, text)
    if e["action"] == "remark":
        return f"remark {e['text']}"
    if kind == "standard":
        if e["src"] is None:
            body = "any"
        elif e["src"][1] == "0.0.0.0":
            body = e["src"][0]
        else:
            body = f"{e['src'][0]}, wildcard bits {e['src'][1]}"
        return f"{e['action']} {body}" + (" log" if e["log"] else "")
    return text


# ------------------------------------------------------------------ nftables

def _wild_match(field, a):
    """(ip, wildcard) -> nft match on ip saddr/daddr."""
    if a is None:
        return None
    ip, w = a
    wi = int(ipaddress.IPv4Address(w))
    mask = (~wi) & 0xFFFFFFFF
    net = int(ipaddress.IPv4Address(ip)) & mask
    inv = (~mask) & 0xFFFFFFFF
    if (inv + 1) & inv == 0:                       # contiguous wildcard -> prefix
        plen = 32 - inv.bit_length()
        return f"{field} {ipaddress.IPv4Address(net)}" + ("" if plen == 32 else f"/{plen}")
    return f"{field} & {ipaddress.IPv4Address(mask)} == {ipaddress.IPv4Address(net)}"


def _port_match(proto, dirn, op):
    if not op:
        return None
    kind, ports = op
    f = f"{proto} {dirn}"
    if kind == "eq":
        return f"{f} {ports[0]}" if len(ports) == 1 else f"{f} {{ {', '.join(map(str, ports))} }}"
    if kind == "neq":
        return f"{f} != {ports[0]}"
    if kind == "gt":
        return f"{f} > {ports[0]}"
    if kind == "lt":
        return f"{f} < {ports[0]}"
    return f"{f} {ports[0]}-{ports[1]}"


def entry_match(kind, text) -> str:
    e = parse_entry(kind, text)
    m = []
    p = e["proto"]
    if p != "ip":
        num = p if isinstance(p, int) else PROTOS[p]
        m.append(f"meta l4proto {num}")
    for field, a in (("ip saddr", e["src"]), ("ip daddr", e["dst"])):
        x = _wild_match(field, a)
        if x:
            m.append(x)
    if p in ("tcp", "udp"):
        for dirn, op in (("sport", e["sport"]), ("dport", e["dport"])):
            x = _port_match(p, dirn, op)
            if x:
                m.append(x)
    if e["icmp"]:
        m.append(f"icmp type {ICMP_TYPES[e['icmp']]}")
    if e["established"]:
        m.append("tcp flags & (ack | rst) != 0")
    return " ".join(m)


def _q(names):
    names = sorted(names)
    return f'"{names[0]}"' if len(names) == 1 else "{ " + ", ".join(f'"{n}"' for n in names) + " }"


def _cid(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", str(name))


def ruleset(props: dict, if_addrs: dict | None = None, drop_only: bool = False) -> str:
    """props -> nft script for the router's namespace. if_addrs: {ifn: first IPv4} for
    'interface X' NAT forms (resolved when applied)."""
    acls = normalise_acls(props.get("acls") or {})
    ifcfg = props.get("ifcfg") or {}
    nat = normalise_nat(props.get("nat") or {}, acls)
    if_addrs = if_addrs or {}
    deny = "drop" if drop_only else "reject with icmp type admin-prohibited"
    L = ["# generated by Patchyard: router ACLs and NAT", "add table ip nl_acl", "delete table ip nl_acl",
         "add table ip nl_nat", "delete table ip nl_nat", "table ip nl_acl {"]
    used = {c.get(k) for c in ifcfg.values() for k in ("acl_in", "acl_out")} - {None, ""}
    for name in sorted(used):
        if name not in acls:
            continue                       # IOS: an access-group naming a missing ACL permits everything
        a = acls[name]
        L.append(f"  chain f_{_cid(name)} {{")
        for e in a["entries"]:
            if e["text"].split()[0].lower() == "remark":
                continue
            act = parse_entry(a["type"], e["text"])["action"]
            verdict = "return" if act == "permit" else deny
            m = entry_match(a["type"], e["text"])
            L.append(f'    {m + " " if m else ""}counter {verdict} comment "acl:{name}:{e["seq"]}"')
        L.append(f'    counter {deny} comment "acl:{name}:implicit"')
        L.append("  }")
    # inbound ACLs run before NAT (prerouting, ahead of DNAT and conntrack) so they see the
    # addresses as they arrive, like IOS; that also covers traffic to the router itself.
    # Outbound ACLs run on routed traffic before source NAT (IOS would see the translated source).
    inp, fwd = [], []
    for ifn in sorted(ifcfg):
        c = ifcfg[ifn]
        if c.get("acl_in") in acls:
            inp.append(f'    iifname "{ifn}" jump f_{_cid(c["acl_in"])}')
    for ifn in sorted(ifcfg):
        c = ifcfg[ifn]
        if c.get("acl_out") in acls:
            fwd.append(f'    oifname "{ifn}" jump f_{_cid(c["acl_out"])}')
    L += ["  chain inbound {", "    type filter hook prerouting priority -300; policy accept;", *inp, "  }",
          "  chain forward {", "    type filter hook forward priority 0; policy accept;", *fwd, "  }", "}"]
    ins = [i for i, c in ifcfg.items() if c.get("nat") == "inside"]
    outs = [i for i, c in ifcfg.items() if c.get("nat") == "outside"]
    pre, post, chains = [], [], []
    if ins and outs:
        for r in nat["rules"]:
            if r["type"] == "static":
                pre.append(f'    iifname {_q(outs)} ip daddr {r["global"]} counter dnat to {r["local"]} comment "nat:static:{r["local"]}"')
                post.append(f'    oifname {_q(outs)} ip saddr {r["local"]} counter snat to {r["global"]} comment "nat:static:{r["local"]}"')
            elif r["type"] == "static-port":
                g = r.get("global") or if_addrs.get(r.get("interface", ""))
                if not g:
                    continue
                pre.append(f'    iifname {_q(outs)} ip daddr {g} {r["proto"]} dport {r["gport"]} counter dnat to {r["local"]}:{r["lport"]} comment "nat:port:{r["gport"]}"')
                post.append(f'    oifname {_q(outs)} ip saddr {r["local"]} {r["proto"]} sport {r["lport"]} counter snat to {g}:{r["gport"]} comment "nat:port:{r["gport"]}"')
        for k, r in enumerate(nat["rules"]):
            if r["type"] != "list" or r["acl"] not in acls:
                continue
            a = acls[r["acl"]]
            if r.get("interface"):
                target, oif = "masquerade", [r["interface"]]
            else:
                pl = nat["pools"].get(r["pool"])
                if not pl:
                    continue
                target = f'snat to {pl["start"]}-{pl["end"]}' + ("" if r.get("overload") else " persistent")
                oif = outs
            ch = f"n_{k}_{_cid(r['acl'])}"
            body = []
            for e in a["entries"]:
                if e["text"].split()[0].lower() == "remark":
                    continue
                act = parse_entry(a["type"], e["text"])["action"]
                m = entry_match(a["type"], e["text"])
                body.append(f'    {m + " " if m else ""}counter {target if act == "permit" else "return"} comment "nat:{r["acl"]}:{e["seq"]}"')
            chains += [f"  chain {ch} {{", *body, "  }"]
            post.append(f"    iifname {_q(ins)} oifname {_q(oif)} jump {ch}")
    L += ["table ip nl_nat {", *chains,
          "  chain prerouting {", "    type nat hook prerouting priority dstnat; policy accept;", *pre, "  }",
          "  chain postrouting {", "    type nat hook postrouting priority srcnat; policy accept;", *post, "  }", "}"]
    return "\n".join(L) + "\n"


def static_globals(props: dict) -> list[str]:
    """Addresses the router must answer ARP for on its outside interface (static NAT)."""
    nat = props.get("nat") or {}
    return sorted({r["global"] for r in nat.get("rules") or [] if r.get("type") in ("static", "static-port") and r.get("global")})


# ------------------------------------------------------------------ NAT model

def normalise_nat(nat: dict, acls: dict | None = None) -> dict:
    pools = {}
    for name, p in (nat.get("pools") or {}).items():
        if not NAME_RE.match(name):
            raise AclError(f"% bad pool name '{name}'")
        s, e = ipaddress.IPv4Address(p["start"]), ipaddress.IPv4Address(p["end"])
        if int(e) < int(s):
            raise AclError(f"% pool {name}: end address is below the start")
        mask = p.get("mask") or "255.255.255.0"
        net = ipaddress.IPv4Network(f"{s}/{mask}", strict=False)
        if e not in net:
            raise AclError(f"% pool {name}: start and end are not in the same {mask} network")
        pools[name] = {"start": str(s), "end": str(e), "mask": str(net.netmask)}
    rules = []
    for r in nat.get("rules") or []:
        t = r.get("type")
        if t == "list":
            acl_kind(r["acl"])
            x = {"type": "list", "acl": str(r["acl"]), "overload": bool(r.get("overload"))}
            if r.get("interface"):
                x["interface"] = r["interface"]; x["overload"] = True
            elif r.get("pool"):
                x["pool"] = r["pool"]
            else:
                raise AclError("% NAT rule needs an interface or a pool")
        elif t == "static":
            x = {"type": "static", "local": _ip(r["local"]), "global": _ip(r["global"])}
        elif t == "static-port":
            if r.get("proto") not in ("tcp", "udp"):
                raise AclError("% static port NAT: tcp or udp")
            lp, gp = int(r["lport"]), int(r["gport"])
            if not (1 <= lp <= 65535 and 1 <= gp <= 65535):
                raise AclError("% ports must be 1-65535")
            x = {"type": "static-port", "proto": r["proto"], "local": _ip(r["local"]), "lport": lp, "gport": gp}
            if r.get("interface"):
                x["interface"] = r["interface"]
            else:
                x["global"] = _ip(r["global"])
        else:
            raise AclError(f"% unknown NAT rule type {t}")
        rules.append(x)
    return {"pools": pools, "rules": rules}


def nat_lines(nat: dict, ifname) -> list[str]:
    """props.nat -> IOS global config lines. ifname: linux -> IOS name."""
    L = []
    for name, p in sorted((nat.get("pools") or {}).items()):
        L.append(f"ip nat pool {name} {p['start']} {p['end']} netmask {p['mask']}")
    for r in nat.get("rules") or []:
        if r["type"] == "list":
            tgt = f"interface {ifname(r['interface'])} overload" if r.get("interface") else f"pool {r['pool']}" + (" overload" if r.get("overload") else "")
            L.append(f"ip nat inside source list {r['acl']} {tgt}")
        elif r["type"] == "static":
            L.append(f"ip nat inside source static {r['local']} {r['global']}")
        else:
            g = f"interface {ifname(r['interface'])}" if r.get("interface") else r["global"]
            L.append(f"ip nat inside source static {r['proto']} {r['local']} {r['lport']} {g} {r['gport']}")
    return L


def acl_lines(acls: dict) -> list[str]:
    """props.acls -> IOS config: numbered as access-list lines, named as ip access-list blocks."""
    L = []
    for name in sorted(acls, key=lambda n: (not str(n).isdigit(), int(n) if str(n).isdigit() else 0, str(n))):
        a = acls[name]
        if str(name).isdigit():
            L += [f"access-list {name} {e['text']}" for e in a["entries"]]
        else:
            L.append(f"ip access-list {a['type']} {name}")
            L += [f" {e['seq']} {e['text']}" if e["text"].split()[0] != "remark" else f" {e['text']}" for e in a["entries"]]
        L.append("!")
    return L


def iface_lines(c: dict) -> list[str]:
    L = []
    if c.get("vrf"):
        L.append(f" vrf forwarding {c['vrf']}")
    for h in c.get("helpers") or []:
        L.append(f" ip helper-address {h}")
    if c.get("acl_in"):
        L.append(f" ip access-group {c['acl_in']} in")
    if c.get("acl_out"):
        L.append(f" ip access-group {c['acl_out']} out")
    if c.get("nat"):
        L.append(f" ip nat {c['nat']}")
    return L


def vrf_lines(vrfs: dict) -> list[str]:
    L = []
    for name, v in sorted((vrfs or {}).items()):
        if v.get("style") == "ip":
            L.append(f"ip vrf {name}")
            if v.get("rd"):
                L.append(f" rd {v['rd']}")
            L += [f" route-target import {x}" for x in v.get("import", [])] + [f" route-target export {x}" for x in v.get("export", [])]
        else:
            L.append(f"vrf definition {name}")
            if v.get("rd"):
                L.append(f" rd {v['rd']}")
            L.append(" !")
            L.append(" address-family ipv4")
            L += [f"  route-target import {x}" for x in v.get("import", [])] + [f"  route-target export {x}" for x in v.get("export", [])]
            L.append(" exit-address-family")
        L.append("!")
    return L


RD_RE = re.compile(r"^(\d+:\d+|\d+\.\d+\.\d+\.\d+:\d+)$")


def check_rd(v: str) -> str:
    if not RD_RE.match(v or ""):
        raise AclError(f"% Invalid RD/route-target '{v}' (use ASN:nn or A.B.C.D:nn)")
    return v


def vrf_table(vrfs: dict, name: str) -> int:
    if name in vrfs and vrfs[name].get("table"):
        return int(vrfs[name]["table"])
    used = {int(v.get("table") or 0) for v in vrfs.values()}
    t = 1001
    while t in used:
        t += 1
    return t


# ------------------------------------------------------------------ show helpers

def counters(nft_json: str) -> dict:
    out = {}
    try:
        items = json.loads(nft_json).get("nftables", [])
    except ValueError:
        return out
    for it in items:
        r = it.get("rule")
        if not r or not str(r.get("comment", "")).startswith(("acl:", "nat:")):
            continue
        for e in r.get("expr", []):
            if "counter" in e:
                c = out.setdefault(r["comment"], [0, 0])
                c[0] += e["counter"].get("packets", 0)
                c[1] += e["counter"].get("bytes", 0)
    return out


def show_access_lists(acls: dict, cnt: dict, only: str | None = None) -> str:
    L = []
    for name in sorted(acls, key=lambda n: (not str(n).isdigit(), int(n) if str(n).isdigit() else 0, str(n))):
        if only and str(only) != str(name):
            continue
        a = acls[name]
        kind = "Standard" if a["type"] == "standard" else "Extended"
        L.append(f"{kind} IP access list {name}")
        for e in a["entries"]:
            if e["text"].split()[0] == "remark":
                continue
            hits = cnt.get(f"acl:{name}:{e['seq']}", [0])[0] + cnt.get(f"nat:{name}:{e['seq']}", [0])[0]
            L.append(f"    {e['seq']} {show_entry(a['type'], e['text'])}" + (f" ({hits} match{'es' if hits != 1 else ''})" if hits else ""))
    return "\n".join(L) + ("\n" if L else "")


CT_RE = re.compile(r"^(\w+)\s+\d+\s+(?:\d+\s+)?(?:[A-Z_]+\s+)?src=(\S+) dst=(\S+)(?: sport=(\d+) dport=(\d+))?.*?src=(\S+) dst=(\S+)(?: sport=(\d+) dport=(\d+))?")


def nat_translations(conntrack_text: str, props: dict) -> list[dict]:
    """conntrack -L output -> IOS translation rows (inside global/local, outside local/global)."""
    rows = []
    for ln in conntrack_text.splitlines():
        m = CT_RE.match(ln.strip())
        if not m:
            continue
        proto, osrc, odst, osp, odp, rsrc, rdst, rsp, rdp = m.groups()
        if rdst != osrc:                                 # source NAT: inside local osrc -> inside global rdst
            rows.append({"pro": proto, "ig": f"{rdst}:{rdp}" if rdp else rdst, "il": f"{osrc}:{osp}" if osp else osrc,
                         "ol": f"{odst}:{odp}" if odp else odst, "og": f"{odst}:{odp}" if odp else odst})
        elif rsrc != odst:                               # destination NAT (outside -> static global)
            rows.append({"pro": proto, "ig": f"{odst}:{odp}" if odp else odst, "il": f"{rsrc}:{rsp}" if rsp else rsrc,
                         "ol": f"{osrc}:{osp}" if osp else osrc, "og": f"{osrc}:{osp}" if osp else osrc})
    for r in (props.get("nat") or {}).get("rules") or []:
        if r.get("type") == "static":
            rows.append({"pro": "---", "ig": r["global"], "il": r["local"], "ol": "---", "og": "---"})
    return rows


def show_translations(rows: list[dict]) -> str:
    L = [f"{'Pro':<5}{'Inside global':<22}{'Inside local':<22}{'Outside local':<22}Outside global"]
    for r in rows:
        L.append(f"{r['pro'][:4]:<5}{r['ig']:<22}{r['il']:<22}{r['ol']:<22}{r['og']}")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ DHCP relay

def relay_args(props: dict, if_addrs: dict, pidfile: str) -> list[str] | None:
    """dnsmasq in relay-only mode for every interface with ip helper-address."""
    args = []
    for ifn, c in sorted((props.get("ifcfg") or {}).items()):
        helpers = c.get("helpers") or []
        if not helpers or not if_addrs.get(ifn):
            continue
        args.append(f"--interface={ifn}")
        for h in helpers:
            args.append(f"--dhcp-relay={if_addrs[ifn]},{h}")
    if not args:
        return None
    return ["dnsmasq", "--port=0", "--no-resolv", "--no-hosts", "--conf-file=/dev/null", "--bind-dynamic", "--user=root",
            "--group=root", f"--pid-file={pidfile}", f"--log-facility={pidfile[:-4]}.log", "--log-dhcp", "--except-interface=lo"] + args


# ------------------------------------------------------------------ apply

class NS:
    """How to run things in a router's network namespace (the app's binaries, the router's netns)."""

    def __init__(self, pid: int | None = None, netns: str | None = None):
        self.pid, self.netns = pid, netns

    def argv(self, *a):
        if self.netns:
            return ["ip", "netns", "exec", self.netns, *a]
        return ["nsenter", "-t", str(self.pid), "-n", *a]

    async def run(self, *a, timeout=30):
        from .util import run
        return await run(*self.argv(*a), timeout=timeout)


async def if_addrs(ns: NS) -> dict:
    rc, out, _ = await ns.run("ip", "-j", "-4", "addr")
    res = {}
    try:
        for i in json.loads(out) if rc == 0 else []:
            a = [x["local"] for x in i.get("addr_info", []) if x.get("family") == "inet" and x.get("label", "").find(":nat") < 0]
            if a:
                res[i["ifname"]] = a[0]
    except ValueError:
        pass
    return res


def has_l3(props: dict) -> bool:
    return bool(props.get("acls") or props.get("ifcfg") or (props.get("nat") or {}).get("rules") or props.get("vrfs"))


async def apply(ns: NS, props: dict, relay_id: str) -> list[str]:
    """Make the namespace match props. Returns problems (empty when all went through)."""
    errs = []
    vrfs = props.get("vrfs") or {}
    ifcfg = props.get("ifcfg") or {}
    # VRFs first: interfaces must be in their VRF before routes and NAT are evaluated
    for name, v in vrfs.items():
        rc, _, _ = await ns.run("ip", "link", "show", name)
        if rc:
            rc, o, e = await ns.run("ip", "link", "add", name, "type", "vrf", "table", str(vrf_table(vrfs, name)))
            if rc:
                errs.append(f"vrf {name}: {(e or o).strip()} (is the 'vrf' kernel module loaded on the host?)")
                continue
        await ns.run("ip", "link", "set", name, "up")
    rc, out, _ = await ns.run("ip", "-j", "link")
    try:
        links = {l["ifname"]: l for l in json.loads(out)} if rc == 0 else {}
    except ValueError:
        links = {}
    for ifn, l in links.items():
        want = (ifcfg.get(ifn) or {}).get("vrf")
        have = l.get("master")
        if want and have != want and want in links:
            await ns.run("ip", "link", "set", ifn, "master", want)
        elif not want and have in vrfs:
            await ns.run("ip", "link", "set", ifn, "nomaster")
    addrs = await if_addrs(ns)
    # ACL + NAT ruleset
    try:
        text = ruleset(props, addrs)
    except (AclError, ValueError) as e:
        return errs + [str(e)]
    for drop_only in (False, True):
        # older kernels can't send ICMP rejects from prerouting: fall back to silent drops
        if drop_only:
            text = ruleset(props, addrs, drop_only=True)
        with tempfile.NamedTemporaryFile("w", suffix=".nft", delete=False) as f:
            f.write(text)
        try:
            rc, o, e = await ns.run("nft", "-f", f.name)
        finally:
            os.unlink(f.name)
        if rc == 0:
            break
        if drop_only or "reject" not in (e or o) and "Operation not supported" not in (e or o):
            errs.append(f"nft: {(e or o).strip()[:300]}")
            break
    # static NAT globals: the router answers ARP for them on the outside interface
    rc, out, _ = await ns.run("ip", "-j", "-4", "addr")
    try:
        cur = [(i["ifname"], x["local"], x.get("prefixlen", 32), x.get("label", "")) for i in (json.loads(out) if rc == 0 else [])
               for x in i.get("addr_info", [])]
    except ValueError:
        cur = []
    want_g = set(static_globals(props))
    outs = sorted(i for i, c in ifcfg.items() if c.get("nat") == "outside" and i in links)
    for ifn, ip, _, label in cur:
        if label.endswith(":nat") and (ip not in want_g or not outs):
            await ns.run("ip", "addr", "del", f"{ip}/32", "dev", ifn)
    have_g = {ip for _, ip, _, _ in cur}
    for g in sorted(want_g - have_g):
        if not outs:
            break
        ga = ipaddress.IPv4Address(g)
        dev = next((i for i, ip, plen, lab in cur if i in outs and not lab.endswith(":nat") and plen < 32
                    and ga in ipaddress.IPv4Network(f"{ip}/{plen}", strict=False)), outs[0])
        label = f"{dev}:nat"
        await ns.run("ip", "addr", "add", f"{g}/32", "dev", dev, *(["label", label] if len(label) <= 15 else []))
    # DHCP relay
    pidfile = f"/run/netlab-relay-{relay_id}.pid"
    stop_relay(pidfile)
    args = relay_args(props, addrs, pidfile)
    if args:
        rc, o, e = await ns.run(*args)
        if rc:
            errs.append(f"dhcp relay: {(e or o).strip()[:200]}")
    return errs


def stop_relay(pidfile: str):
    try:
        pid = int(open(pidfile).read().strip())
        os.kill(pid, 15)
    except (OSError, ValueError):
        pass
    try:
        os.unlink(pidfile)
    except OSError:
        pass


async def apply_node(labd: dict, node: dict) -> list[str]:
    """Apply a lab router's L3 services if it's running (no-op otherwise)."""
    from . import netinfo
    if node.get("type") != "router" or node.get("raw"):
        return []
    props = node.get("props") or {}
    c = (await netinfo.containers()).get((labd["name"], node["id"]))
    if not c or c["state"] != "running" or not c["pid"]:
        return []
    if not has_l3(props) and not os.path.exists(f"/run/netlab-relay-{labd['name']}-{node['id']}.pid"):
        return []
    return await apply(NS(pid=c["pid"]), props, f"{labd['name']}-{node['id']}")


async def reapply_all(labd: dict) -> list[str]:
    notes = []
    for n in labd.get("nodes", []):
        for e in await apply_node(labd, n):
            notes.append(f"{n['id']}: {e}")
    return notes


def stop_lab(lab: str):
    import glob
    for f in glob.glob(f"/run/netlab-relay-{lab}-*.pid"):
        stop_relay(f)
