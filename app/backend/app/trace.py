"""Path explainer: why does (or doesn't) a packet get from A to B?

Walks the path hop by hop in the running lab:
  * each L3 device's own kernel decides the next hop (`ip route get` in its namespace),
    with the route's protocol from its routing table;
  * the switches in between are found from the cables and their VLAN/port settings;
  * router ACLs (inbound on the way in, outbound on the way out), router NAT and firewall
    zone rules/NAT are evaluated from the device's configuration, so the answer names the
    exact entry ("OUTSIDE-IN line 80: deny ip any any");
  * interfaces that are down or shut stop the walk where they are.
The reply direction is traced too, because asymmetric or missing return routes are a
classic cause of "ping fails one way".
"""
import asyncio, ipaddress, json, re
from . import netinfo, l3, firewall, hardware
from .util import run, dexec

L3 = ("router", "firewall", "pc", "server", "isp", "internet")


async def _addrs(pid: int) -> dict:
    """{ifname: [ip_interface,...]} in a namespace."""
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "addr", "show", timeout=6)
    res = {}
    try:
        for i in json.loads(out) if rc == 0 and out.strip() else []:
            res[i["ifname"]] = [ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}") for a in i.get("addr_info", []) if a.get("family") == "inet"]
            res[i["ifname"] + "#state"] = i.get("operstate", "UNKNOWN")
    except (ValueError, KeyError):
        pass
    return res


async def _route(pid: int, dst: str, src: str | None = None) -> dict | None:
    args = ["nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "route", "get", dst] + (["from", src] if src else [])
    rc, out, err = await run(*args, timeout=6)
    if rc:
        if src:
            return await _route(pid, dst)            # 'from' fails for addresses the box doesn't own
        return {"error": (err or out).strip()}
    try:
        r = json.loads(out)[0]
    except (ValueError, IndexError):
        return None
    # protocol and prefix of the matching route
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "route", "show", "table", "main", timeout=6)
    best = None
    try:
        for e in json.loads(out) if rc == 0 else []:
            d = e.get("dst", "")
            net = ipaddress.ip_network("0.0.0.0/0" if d == "default" else (d if "/" in d else f"{d}/32"), strict=False)
            if ipaddress.ip_address(dst) in net and (best is None or net.prefixlen > best[0].prefixlen):
                best = (net, e.get("protocol", "kernel"), e.get("type", "unicast"))
    except (ValueError, KeyError):
        pass
    r["prefix"] = str(best[0]) if best else ""
    r["protocol"] = {"kernel": "connected", "boot": "static (kernel)", "186": "bgp", "188": "ospf", "196": "static", "zebra": "static"}.get(
        best[1], best[1]) if best else ""
    return r


def _ace_match(kind, text, pkt) -> bool:
    e = l3.parse_entry(kind, text)
    if e["action"] == "remark":
        return False

    def addr_ok(a, ip):
        if a is None:
            return True
        net_ip, w = a
        wi = int(ipaddress.IPv4Address(w))
        return (int(ipaddress.IPv4Address(ip)) & ~wi & 0xFFFFFFFF) == (int(ipaddress.IPv4Address(net_ip)) & ~wi & 0xFFFFFFFF)

    def port_ok(op, port):
        if not op:
            return True
        if port is None:
            return False
        k, ps = op
        return {"eq": port in ps, "neq": port != ps[0], "gt": port > ps[0], "lt": port < ps[0], "range": ps[0] <= port <= ps[-1]}[k]
    if e["proto"] not in ("ip", pkt["proto"]) and not (isinstance(e["proto"], int) and e["proto"] == {"icmp": 1, "tcp": 6, "udp": 17}.get(pkt["proto"])):
        return False
    if e["established"] and not (pkt.get("reply") and pkt["proto"] == "tcp"):
        return False                                  # only replies (ACK set) are "established"
    if e["icmp"] and e["icmp"] != ("echo-reply" if pkt.get("reply") else "echo"):
        return False
    return addr_ok(e["src"], pkt["src"]) and addr_ok(e["dst"], pkt["dst"]) and port_ok(e["sport"], pkt.get("sport")) and port_ok(e["dport"], pkt.get("dport"))


def acl_verdict(acls: dict, name: str, pkt: dict) -> tuple[bool, str]:
    a = (acls or {}).get(name)
    if not a:
        return True, f"access list {name} doesn't exist (IOS permits everything)"
    for e in a["entries"]:
        try:
            if _ace_match(a["type"], e["text"], pkt):
                return e["text"].startswith("permit"), f"{name} line {e['seq']}: {e['text']}"
        except l3.AclError:
            continue
    return False, f"{name}: no entry matched (implicit deny at the end)"


def _fw_verdict(props: dict, in_if: str, out_if: str, pkt: dict) -> tuple[bool, str]:
    p = firewall.normalise(props)
    zone = lambda i: (p["interfaces"].get(i) or {}).get("zone")
    zi, zo = zone(in_if), zone(out_if)
    for r in p["rules"]:
        if not r["enabled"]:
            continue
        if r["from"] != "any" and r["from"] != zi or r["to"] != "any" and r["to"] != zo:
            continue
        if r["proto"] != "any" and r["proto"] != pkt["proto"]:
            continue
        if r["src"] != "any" and ipaddress.ip_address(pkt["src"]) not in ipaddress.ip_network(r["src"], strict=False):
            continue
        if r["dst"] != "any" and ipaddress.ip_address(pkt["dst"]) not in ipaddress.ip_network(r["dst"], strict=False):
            continue
        if r["ports"] and pkt["proto"] in ("tcp", "udp"):
            ok = False
            for part in r["ports"].split(","):
                a, _, b = part.partition("-")
                if pkt.get("dport") is not None and int(a) <= pkt["dport"] <= int(b or a):
                    ok = True
            if not ok:
                continue
        return r["action"] == "accept", f"rule {r['id']}: {r['action']} {r['from']}→{r['to']} {r['proto']}{(' ' + r['ports']) if r['ports'] else ''}"
    if any(n["type"] == "dnat" and n["zone"] == zi and n["proto"] == pkt["proto"] and int(n["port"]) == pkt.get("dport") for n in p["nat"]):
        return True, f"port forward on {zi}"
    if p["policy"] == "levels" and zi and zo:
        lv = {z["name"]: z["level"] for z in p["zones"]}
        if lv.get(zi, 0) > lv.get(zo, 0):
            return True, f"security levels: {zi} ({lv[zi]}) → {zo} ({lv[zo]}) is allowed"
        return False, f"security levels: {zi} ({lv.get(zi)}) → {zo} ({lv.get(zo)}) needs a rule (none matched)"
    return False, f"no rule matched {zi or '?'}→{zo or '?'} (default drop)"


def _l2_path(lab: dict, a: tuple, b_node: str) -> list[dict]:
    """Switches crossed between (node, if) and node b, found from the cables."""
    nodes = {n["id"]: n for n in lab["nodes"]}
    adj = {}
    for l in lab["links"]:
        adj.setdefault(l["a"], []).append((l["a_if"], l["b"], l["b_if"]))
        adj.setdefault(l["b"], []).append((l["b_if"], l["a"], l["a_if"]))
    start = next(((n2, i2) for i1, n2, i2 in adj.get(a[0], []) if i1 == a[1]), None)
    if not start:
        return []
    seen, q = {start[0]}, [(start, [])]
    while q:
        (node, inif), path = q.pop(0)
        if node == b_node:
            return path
        nd = nodes.get(node, {})
        if nd.get("type") != "switch" and not (nd.get("raw") or {}).get("kind") == "bridge":
            continue
        for oif, n2, i2 in adj.get(node, []):
            if n2 in seen or oif == inif:
                continue
            seen.add(n2)
            pp = (nd.get("props") or {}).get("ports") or {}
            def desc(i):
                c = pp.get(i) or {}
                return f"trunk ({c.get('allowed') or 'all'})" if c.get("mode") == "trunk" else f"VLAN {c.get('vlan') or 1}"
            q.append(((n2, i2), path + [{"node": node, "in": hardware.display(nd, inif), "out": hardware.display(nd, oif),
                                         "in_mode": desc(inif), "out_mode": desc(oif)}]))
    return []


async def resolve(lab: dict, src: str, target: str) -> str:
    """Target as an IP: an address, a device name (its first lab address) or a host name (resolved on the source)."""
    t = target.strip()
    try:
        return str(ipaddress.IPv4Address(t))
    except ValueError:
        pass
    ids = {n["id"] for n in lab["nodes"]}
    if t in ids:
        c = (await netinfo.containers()).get((lab["name"], t))
        if c and c.get("pid"):
            for i, a in sorted((await _addrs(c["pid"])).items()):
                if not i.endswith("#state") and i not in ("lo", "eth0") and a:
                    return str(a[0].ip)
        raise ValueError(f"{t} has no lab address (is it running?)")
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", t):
        raise ValueError("give an address, a device or a host name")
    rc, out, _ = await dexec(lab["name"], src, "sh", "-c", f"getent hosts {t} 2>/dev/null || nslookup {t} 2>/dev/null", timeout=10)
    for ip in re.findall(r"(\d+\.\d+\.\d+\.\d+)", out):
        if not ip.startswith("127.") and ip != "0.0.0.0":
            return ip
    raise ValueError(f"{src} can't resolve {t}")


async def walk(lab: dict, src: str, dst_ip: str, proto: str = "icmp", dport: int | None = None, max_hops: int = 24,
               reply: bool = False, sport: int | None = None) -> dict:
    ctrs = await netinfo.containers()
    nodes = {n["id"]: n for n in lab["nodes"]}
    pids = {nid: c["pid"] for (l, nid), c in ctrs.items() if l == lab["name"] and c["state"] == "running" and c.get("pid")}
    if src not in pids:
        raise ValueError(f"{src} isn't running")
    addr = dict(zip(pids, await asyncio.gather(*(_addrs(p) for p in pids.values()))))
    owner = {}
    for nid, ifs in addr.items():
        for i, aa in ifs.items():
            if i.endswith("#state") or i == "eth0":
                continue
            for a in aa:
                owner.setdefault(str(a.ip), (nid, i))
    hops, cur, in_if, src_ip = [], src, None, None
    pkt = {"proto": proto, "dst": dst_ip, "dport": dport, "sport": sport if sport is not None else (40000 if proto in ("tcp", "udp") else None),
           "reply": reply}
    result, reason = "no-route", ""
    seen = set()
    for _ in range(max_hops):
        n = nodes.get(cur, {})
        hop = {"node": cur, "type": n.get("type"), "in": hardware.display(n, in_if) if in_if else None, "notes": []}
        if (cur, in_if) in seen:
            hop["verdict"] = "loop"
            hops.append(hop)
            result, reason = "loop", f"routing loop: the packet came back to {cur}"
            break
        seen.add((cur, in_if))
        if owner.get(dst_ip, (None,))[0] == cur:
            if n.get("type") == "router" and in_if and n.get("props", {}).get("ifcfg", {}).get(in_if, {}).get("acl_in"):
                ok, why = acl_verdict(n["props"].get("acls"), n["props"]["ifcfg"][in_if]["acl_in"], pkt)
                hop["notes"].append(f"inbound ACL: {why}")
                if not ok:
                    hop["verdict"] = "drop"; hops.append(hop)
                    result, reason = "dropped", f"{cur} drops it on the way in: {why}"
                    break
            hop["verdict"] = "deliver"
            hops.append(hop)
            result, reason = "delivered", f"{dst_ip} is {cur} {hardware.display(n, owner[dst_ip][1]) if owner[dst_ip][1] != 'lo' else 'loopback'}"
            break
        pid = pids.get(cur)
        if not pid:
            hop["verdict"] = "down"; hops.append(hop)
            result, reason = "dropped", f"{cur} isn't running"
            break
        if src_ip is None:
            r0 = await _route(pid, dst_ip)
            src_ip = (r0 or {}).get("prefsrc")
            pkt["src"] = src_ip or "0.0.0.0"
        r = await _route(pid, dst_ip, None)
        if not r or r.get("error") or not r.get("dev"):
            hop["verdict"] = "no-route"; hop["notes"].append((r or {}).get("error") or "no route")
            hops.append(hop)
            result, reason = "no-route", f"{cur} has no route to {dst_ip}" + (" (no default gateway)" if n.get("type") in ("pc", "server") else "")
            break
        out_if, gw = r["dev"], r.get("gateway")
        hop.update(out=hardware.display(n, out_if), via=gw or "directly connected", route=r.get("prefix"), protocol=r.get("protocol"))
        if r.get("type") in ("blackhole", "unreachable", "prohibit"):
            hop["verdict"] = "drop"; hops.append(hop)
            result, reason = "dropped", f"{cur} has a {r['type']} route for {r.get('prefix')}"
            break
        if out_if == "eth0":
            hop["verdict"] = "mgmt"; hops.append(hop)
            result, reason = "left-lab", f"{cur} sends it out of its management interface (default route via the management network)"
            break
        if addr.get(cur, {}).get(out_if + "#state") == "DOWN":
            hop["verdict"] = "drop"; hops.append(hop)
            result, reason = "dropped", f"{cur} {hardware.display(n, out_if)} is down"
            break
        p = n.get("props") or {}
        # router ACLs and NAT
        if n.get("type") == "router" and not n.get("raw"):
            cfg = p.get("ifcfg") or {}
            for where, iface, key in (("inbound", in_if, "acl_in"), ("outbound", out_if, "acl_out")):
                name = (cfg.get(iface) or {}).get(key) if iface else None
                if name:
                    ok, why = acl_verdict(p.get("acls"), name, pkt)
                    hop["notes"].append(f"{where} ACL on {hardware.display(n, iface)}: {why}")
                    if not ok:
                        hop["verdict"] = "drop"; hops.append(hop)
                        result, reason = "dropped", f"{cur} drops it: {why}"
                        break
            else:
                if in_if and (cfg.get(in_if) or {}).get("nat") == "inside" and (cfg.get(out_if) or {}).get("nat") == "outside":
                    for rule in (p.get("nat") or {}).get("rules") or []:
                        if rule["type"] == "list" and acl_verdict(p.get("acls"), rule["acl"], {**pkt, "dst": pkt["dst"]})[0]:
                            new = str((addr.get(cur, {}).get(out_if) or [None])[0].ip) if rule.get("interface") else f"pool {rule.get('pool')}"
                            hop["notes"].append(f"NAT: source {pkt['src']} → {new} (list {rule['acl']}{', overload' if rule.get('overload') or rule.get('interface') else ''})")
                            if rule.get("interface"):
                                pkt["src"] = new
                            break
                    else:
                        hop["notes"].append("NAT: inside→outside, but no NAT rule matched: the source stays private")
                hop["verdict"] = "forward"
            if hop.get("verdict") == "drop":
                break
        elif n.get("type") == "firewall" and not n.get("raw") and in_if:
            ok, why = (True, "return traffic of an allowed connection (stateful)") if reply else _fw_verdict(p, in_if, out_if, pkt)
            hop["notes"].append(why)
            if not ok:
                hop["verdict"] = "drop"; hops.append(hop)
                result, reason = "dropped", f"firewall {cur} drops it: {why}"
                break
            fw = firewall.normalise(p)
            zi, zo = (fw["interfaces"].get(in_if) or {}).get("zone"), (fw["interfaces"].get(out_if) or {}).get("zone")
            if any(x["type"] == "masquerade" and x["from"] == zi and x["to"] == zo for x in fw["nat"]):
                new = str((addr.get(cur, {}).get(out_if) or [None])[0].ip)
                hop["notes"].append(f"NAT: source {pkt['src']} → {new} (PAT {zi}→{zo})")
                pkt["src"] = new
            hop["verdict"] = "forward"
        else:
            hop["verdict"] = "forward" if cur != src else "send"
        nh = gw or dst_ip
        nxt = owner.get(nh)
        if not nxt:
            hops.append(hop)
            result, reason = "no-neighbour", f"next hop {nh} isn't any running device's address (ARP would fail)"
            break
        hop["l2"] = _l2_path(lab, (cur, out_if), nxt[0])
        hops.append(hop)
        cur, in_if = nxt
    else:
        result, reason = "too-long", f"gave up after {max_hops} hops"
    return {"src": src, "dst": dst_ip, "proto": proto, "dport": dport, "result": result, "reason": reason, "hops": hops,
            "src_ip": src_ip, "final_src": pkt.get("src")}


async def explain(lab: dict, src: str, target: str, proto: str = "icmp", dport: int | None = None) -> dict:
    if proto not in ("icmp", "tcp", "udp"):
        raise ValueError("protocol must be icmp, tcp or udp")
    if src not in {n["id"] for n in lab["nodes"]}:
        raise ValueError(f"no device {src}")
    dst = await resolve(lab, src, target)
    fwd = await walk(lab, src, dst, proto, dport)
    back = None
    if fwd["result"] == "delivered" and fwd["final_src"] and fwd["final_src"] != "0.0.0.0":
        last = fwd["hops"][-1]["node"]
        back = await walk(lab, last, fwd["final_src"], proto, 40000 if proto in ("tcp", "udp") else None, reply=True, sport=dport)
    return {"target": target, "forward": fwd, "reply": back,
            "ok": fwd["result"] == "delivered" and (back is None or back["result"] == "delivered")}
