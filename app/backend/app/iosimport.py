"""Build a lab from real Cisco IOS / IOS-XE running-configs.

Paste or upload `show running-config` from each device. Patchyard reads:
  hostname; interfaces (address/mask, description, shutdown, dot1Q subinterfaces,
  switchport access/trunk, ip access-group, ip nat inside/outside, ip helper-address);
  vlans; router ospf (networks, router-id, passive-interface); static routes;
  router bgp (neighbors, networks); numbered and named ACLs; ip nat inside source rules.
Devices with switchports and no routed interfaces become switches; the rest become routers.

Cabling is worked out from addresses: two routed interfaces in the same subnet are joined;
three or more get a switch in the middle. Switch-to-switch and switch-to-router links come
from interface descriptions that name another device ("uplink to CORE-SW1").
Each device keeps its real interface names in descriptions, mapped to Patchyard ports in order.
Anything not understood is listed in the report, line by line, so nothing is silently lost.
"""
import ipaddress, re
from . import labs, l3

SKIP = re.compile(r"^(version|service|no service|boot|logging|aaa|clock|ntp|snmp-server|line|banner|end|!|crypto|license|spanning-tree mode|"
                  r"enable|username|ip domain|ip name-server|ip ssh|ip http|archive|memory|control-plane|redundancy|vtp|system mtu|diagnostic|errdisable|"
                  r"platform|transceiver|multilink|cdp|lldp|alias|call-home|subscriber|hw-module|login|security|dot1x)", re.I)


def _slug(h: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", h.lower()).strip("-")[:31]
    return s or "dev"


def _plen(mask: str) -> int:
    return ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen


def parse(text: str) -> dict:
    d = {"hostname": "", "ifaces": {}, "vlans": {}, "ospf": None, "bgp": None, "statics": [], "acls": {}, "nat": [], "unknown": [], "default_gw": None}
    cur, section = None, None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0:
            cur, section = None, None
            m = re.match(r"hostname (\S+)", s)
            if m:
                d["hostname"] = m.group(1)
                continue
            m = re.match(r"interface (\S+)", s, re.I)
            if m:
                name = m.group(1)
                cur = d["ifaces"].setdefault(name, {"name": name, "addrs": [], "desc": "", "shut": False, "switchport": None, "vlan": None,
                                                     "trunk": False, "allowed": None, "native": None, "dot1q": None, "acl_in": None, "acl_out": None,
                                                     "nat": None, "helpers": []})
                section = "if"
                continue
            m = re.match(r"vlan (\d+)$", s)
            if m:
                cur = d["vlans"].setdefault(int(m.group(1)), {"id": int(m.group(1)), "name": f"VLAN{int(m.group(1)):04d}"})
                section = "vlan"
                continue
            m = re.match(r"router ospf (\d+)", s)
            if m:
                d["ospf"] = {"pid": m.group(1), "networks": [], "rid": None, "passive": [], "passive_default": False}
                section = "ospf"
                continue
            m = re.match(r"router bgp (\d+)", s)
            if m:
                d["bgp"] = {"asn": int(m.group(1)), "neighbors": {}, "networks": [], "rid": None}
                section = "bgp"
                continue
            m = re.match(r"ip route (\S+) (\S+) (\S+)", s)
            if m:
                try:
                    pfx = ipaddress.ip_network(f"{m.group(1)}/{m.group(2)}", strict=False)
                    d["statics"].append((str(pfx), m.group(3)))
                except ValueError:
                    d["unknown"].append(s)
                continue
            m = re.match(r"ip default-gateway (\S+)", s)
            if m:
                d["default_gw"] = m.group(1)
                continue
            m = re.match(r"access-list (\d+) (.+)", s)
            if m:
                num, rest = m.group(1), m.group(2)
                kind = "standard" if int(num) < 100 or 1300 <= int(num) < 2000 else "extended"
                a = d["acls"].setdefault(num, {"type": kind, "entries": []})
                a["entries"].append({"seq": 10 * (len(a["entries"]) + 1), "text": rest})
                continue
            m = re.match(r"ip access-list (standard|extended) (\S+)", s)
            if m:
                cur = d["acls"].setdefault(m.group(2), {"type": m.group(1), "entries": []})
                section = "acl"
                continue
            m = re.match(r"ip nat inside source (.+)", s)
            if m:
                d["nat"].append(m.group(1))
                continue
            if SKIP.match(s) or s.startswith("ip routing") or s.startswith("ip cef") or s.startswith("ipv6"):
                section = "skip"
                continue
            d["unknown"].append(s)
            section = "unknown"
            continue
        # indented lines
        if section == "if":
            m = re.match(r"ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)( secondary)?", s)
            if m:
                cur["addrs"].append(f"{m.group(1)}/{_plen(m.group(2))}")
            elif s.startswith("description "):
                cur["desc"] = s[12:].strip()
            elif s == "shutdown":
                cur["shut"] = True
            elif re.match(r"switchport mode trunk", s):
                cur["switchport"], cur["trunk"] = True, True
            elif re.match(r"switchport mode access", s):
                cur["switchport"] = True
            elif m := re.match(r"switchport access vlan (\d+)", s):
                cur["switchport"], cur["vlan"] = True, int(m.group(1))
            elif m := re.match(r"switchport trunk allowed vlan (?:add )?(\S+)", s):
                cur["allowed"] = m.group(1)
            elif m := re.match(r"switchport trunk native vlan (\d+)", s):
                cur["native"] = int(m.group(1))
            elif s.startswith("switchport"):
                cur["switchport"] = True
            elif m := re.match(r"encapsulation dot1q (\d+)", s, re.I):
                cur["dot1q"] = int(m.group(1))
            elif m := re.match(r"ip access-group (\S+) (in|out)", s):
                cur["acl_" + m.group(2)] = m.group(1)
            elif m := re.match(r"ip nat (inside|outside)", s):
                cur["nat"] = m.group(1)
            elif m := re.match(r"ip helper-address (\S+)", s):
                cur["helpers"].append(m.group(1))
            elif s in ("no shutdown", "no ip address", "negotiation auto", "duplex auto", "speed auto") or s.startswith(("spanning-tree", "no ip ", "mtu", "load-interval", "media-type", "channel-group", "cdp", "ip ospf", "ipv6")):
                pass
            else:
                d["unknown"].append(f"{cur['name']}: {s}")
        elif section == "vlan":
            if s.startswith("name "):
                cur["name"] = s[5:].strip()[:32]
        elif section == "ospf":
            if m := re.match(r"network (\S+) (\S+) area (\S+)", s):
                wild = int(ipaddress.IPv4Address(m.group(2)))
                plen = 32 - wild.bit_length() if (wild + 1) & wild == 0 else None
                if plen is None:
                    d["unknown"].append(f"router ospf: {s} (non-contiguous wildcard)")
                else:
                    d["ospf"]["networks"].append((str(ipaddress.ip_network(f"{m.group(1)}/{plen}", strict=False)), m.group(3)))
            elif m := re.match(r"router-id (\S+)", s):
                d["ospf"]["rid"] = m.group(1)
            elif s == "passive-interface default":
                d["ospf"]["passive_default"] = True
            elif m := re.match(r"(no )?passive-interface (\S+)", s):
                d["ospf"]["passive"].append(("no " if m.group(1) else "") + m.group(2))
            elif not s.startswith(("log-adjacency", "auto-cost", "default-information", "redistribute", "area", "!")):
                d["unknown"].append(f"router ospf: {s}")
        elif section == "bgp":
            if m := re.match(r"neighbor (\S+) remote-as (\d+)", s):
                d["bgp"]["neighbors"].setdefault(m.group(1), {})["remote_as"] = int(m.group(2))
            elif m := re.match(r"neighbor (\S+) description (.+)", s):
                d["bgp"]["neighbors"].setdefault(m.group(1), {})["desc"] = m.group(2)
            elif m := re.match(r"network (\S+)(?: mask (\S+))?", s):
                net = ipaddress.ip_network(f"{m.group(1)}/{_plen(m.group(2))}" if m.group(2) else m.group(1), strict=False)
                d["bgp"]["networks"].append(str(net))
            elif m := re.match(r"bgp router-id (\S+)", s):
                d["bgp"]["rid"] = m.group(1)
            elif not s.startswith(("bgp log", "address-family", "exit-address-family", "neighbor", "no bgp", "!")):
                d["unknown"].append(f"router bgp: {s}")
        elif section == "acl":
            if s.startswith(("permit", "deny", "remark")):
                cur["entries"].append({"seq": 10 * (len(cur["entries"]) + 1), "text": s})
            elif m := re.match(r"(\d+) (permit|deny|remark)(.*)", s):
                cur["entries"].append({"seq": int(m.group(1)), "text": (m.group(2) + m.group(3)).strip()})
    return d


def _short(name: str) -> str:
    m = re.match(r"([A-Za-z]+)([\d/.]+)", name)
    if not m:
        return name
    t = m.group(1).lower()
    for full, ab in (("tengigabitethernet", "Te"), ("gigabitethernet", "Gi"), ("fastethernet", "Fa"), ("ethernet", "Eth"), ("serial", "Se"),
                     ("port-channel", "Po"), ("vlan", "Vlan"), ("loopback", "Lo")):
        if full.startswith(t) or t == ab.lower():
            return ab + m.group(2)
    return name


def build(name: str, configs: list[str]) -> dict:
    labs.check_name(name)
    devs, report = [], []
    for txt in configs:
        if not txt.strip():
            continue
        d = parse(txt)
        if not d["hostname"]:
            d["hostname"] = f"dev{len(devs) + 1}"
        routed = [i for i in d["ifaces"].values() if i["addrs"] and not i["name"].lower().startswith(("loopback", "vlan", "tunnel"))]
        sw = [i for i in d["ifaces"].values() if i["switchport"]]
        d["type"] = "switch" if sw and not routed else "router"
        d["id"] = _slug(d["hostname"])
        while d["id"] in {x["id"] for x in devs}:
            d["id"] += "x"
        devs.append(d)
    if not devs:
        raise ValueError("paste at least one running-config")
    by_host = {d["hostname"].lower(): d for d in devs}
    nodes, links, ports = [], [], {}

    def port(dev, ifname):
        """Next Patchyard port for one of the device's real interfaces."""
        m = ports.setdefault(dev["id"], {})
        if ifname not in m:
            m[ifname] = f"eth{len(m) + 1}"
        return m[ifname]
    # physical parents of subinterfaces get ports first, in config order
    subnets = {}
    for d in devs:
        if d["type"] != "router":
            continue
        for i in d["ifaces"].values():
            if not i["addrs"] or i["name"].lower().startswith(("loopback", "tunnel", "vlan")):
                continue
            phys = i["name"].split(".")[0]
            for a in i["addrs"]:
                net = ipaddress.ip_interface(a).network
                if net.prefixlen < 32:
                    subnets.setdefault((str(net), i["dot1q"] or 0), []).append((d, phys, i))
    seen_phys = set()
    lid = 0
    for (net, vlan), members in sorted(subnets.items()):
        devs_in = {m[0]["id"] for m in members}
        if len(devs_in) < 2:
            continue
        if len(members) == 2 and not vlan:
            (a, pa, _), (b, pb, _) = members
            if (a["id"], pa) in seen_phys or (b["id"], pb) in seen_phys:
                continue
            seen_phys |= {(a["id"], pa), (b["id"], pb)}
            lid += 1
            links.append({"id": f"l{lid}", "a": a["id"], "a_if": port(a, pa), "b": b["id"], "b_if": port(b, pb), "media": "copper"})
            report.append(f"cable {a['hostname']} {_short(pa)} ↔ {b['hostname']} {_short(pb)} ({net})")
        else:
            swid = _slug(f"sw-{net.split('/')[0].replace('.', '-')}")[:31]
            if swid not in {n["id"] for n in nodes}:
                nodes.append({"id": swid, "type": "switch", "props": {"vlans": [{"id": 1, "name": "default"}], "ports": {}}, "_auto": True})
            for d, phys, i in members:
                if (d["id"], phys) in seen_phys:
                    continue
                seen_phys.add((d["id"], phys))
                lid += 1
                swp = sum(1 for l in links if l["b"] == swid) + 1
                links.append({"id": f"l{lid}", "a": d["id"], "a_if": port(d, phys), "b": swid, "b_if": f"eth{swp}", "media": "copper"})
            report.append(f"switch {swid} for {net} ({len(members)} devices)")
    # descriptions naming another device -> cable (switch uplinks)
    for d in devs:
        for i in d["ifaces"].values():
            if not i["desc"] or i["name"].lower().startswith(("loopback", "vlan", "tunnel")) or "." in i["name"]:
                continue
            for h, other in by_host.items():
                if other is d or not re.search(rf"\b{re.escape(h)}\b", i["desc"].lower()):
                    continue
                if (d["id"], i["name"]) in seen_phys:
                    break
                back = next((j for j in other["ifaces"].values() if j["desc"] and re.search(rf"\b{re.escape(d['hostname'].lower())}\b", j["desc"].lower())
                             and (other["id"], j["name"]) not in seen_phys and "." not in j["name"]), None)
                if not back:
                    break
                seen_phys |= {(d["id"], i["name"]), (other["id"], back["name"])}
                lid += 1
                links.append({"id": f"l{lid}", "a": d["id"], "a_if": port(d, i["name"]), "b": other["id"], "b_if": port(other, back["name"]), "media": "copper"})
                report.append(f"cable {d['hostname']} {_short(i['name'])} ↔ {other['hostname']} {_short(back['name'])} (from descriptions)")
                break
    # devices
    for k, d in enumerate(devs):
        props = {"startup": []}
        if d["type"] == "router":
            frr = _frr(d, lambda ifn: port(d, ifn))
            props["frr_bootstrap"] = frr
            subifs = [{"parent": port(d, i["name"].split(".")[0]), "vlan": i["dot1q"]} for i in d["ifaces"].values() if i["dot1q"] and "." in i["name"]]
            if subifs:
                props["subifs"] = subifs
            loops = sorted({int(m.group(1)) for i in d["ifaces"].values() for m in [re.match(r"(?i)loopback(\d+)$", i["name"])] if m and int(m.group(1)) > 0})
            if loops:
                props["loopbacks"] = loops
            acls = {}
            for nm, a in d["acls"].items():
                try:
                    acls.update(l3.normalise_acls({nm: a}))
                except l3.AclError as e:
                    report.append(f"{d['hostname']}: access list {nm} skipped ({e})")
            if acls:
                props["acls"] = acls
            ifcfg = {}
            for i in d["ifaces"].values():
                if "." in i["name"] or i["name"].lower().startswith(("loopback", "vlan", "tunnel")):
                    continue
                c = {}
                if i["acl_in"] in acls:
                    c["acl_in"] = i["acl_in"]
                if i["acl_out"] in acls:
                    c["acl_out"] = i["acl_out"]
                if i["nat"]:
                    c["nat"] = i["nat"]
                if i["helpers"]:
                    c["helpers"] = i["helpers"][:4]
                if c:
                    ifcfg[port(d, i["name"])] = c
            if ifcfg:
                props["ifcfg"] = ifcfg
            rules = []
            for r in d["nat"]:
                m = re.match(r"list (\S+) interface (\S+)( overload)?", r)
                if m and m.group(1) in acls:
                    rules.append({"type": "list", "acl": m.group(1), "interface": port(d, m.group(2)), "overload": True})
                    continue
                m = re.match(r"static (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)$", r)
                if m:
                    rules.append({"type": "static", "local": m.group(1), "global": m.group(2)})
                    continue
                report.append(f"{d['hostname']}: ip nat inside source {r} skipped")
            if rules:
                try:
                    props["nat"] = l3.normalise_nat({"pools": {}, "rules": rules}, acls)
                except Exception as e:  # noqa: BLE001
                    report.append(f"{d['hostname']}: NAT skipped ({e})")
        else:
            vl = [{"id": 1, "name": "default"}] + [v for k2, v in sorted(d["vlans"].items()) if k2 != 1]
            pp = {}
            for i in d["ifaces"].values():
                if not i["switchport"]:
                    continue
                e = port(d, i["name"])
                pp[e] = {"mode": "trunk", "allowed": i["allowed"] or "1-4094", "native": i["native"] or 1} if i["trunk"] else {"mode": "access", "vlan": i["vlan"] or 1}
                if i["shut"]:
                    pp[e]["shutdown"] = True
            props.update(vlans=vl, ports=pp, stp=True)
            svi = next((i for i in d["ifaces"].values() if i["name"].lower().startswith("vlan") and i["addrs"]), None)
            if svi:
                props["svi"] = {"vlan": int(re.sub(r"\D", "", svi["name"]) or 1), "ip": svi["addrs"][0], "gateway": d["default_gw"] or ""}
        nodes.append({"id": d["id"], "type": d["type"], "props": props})
        for u in d["unknown"][:40]:
            report.append(f"{d['hostname']}: not imported: {u}")
        if len(d["unknown"]) > 40:
            report.append(f"{d['hostname']}: …and {len(d['unknown']) - 40} more lines not imported")
    # layout: routers on top, switches below, in rows
    rts = [n for n in nodes if n["type"] == "router"]
    sws = [n for n in nodes if n["type"] == "switch"]
    for row, group in ((0, rts), (1, sws)):
        for j, n in enumerate(group):
            n["x"], n["y"] = 160 + 240 * (j % 6), 80 + 220 * (row + 2 * (j // 6))
            n.pop("_auto", None)
    lab = {"name": name, "mgmt_network": labs._d("mgmt_network"), "mgmt_subnet": labs._d("mgmt_subnet"), "nodes": nodes, "links": links,
           "annotations": [{"id": "ann-import", "kind": "note", "x": 160, "y": -120, "w": 340, "h": 110, "color": "yellow",
                            "text": "Imported from IOS configs. Interface names from the originals are in each port's description. "
                                    "Add PCs and servers, then Deploy."}]}
    if (labs.lab_dir(name) / "lab.json").exists():
        raise FileExistsError(f"lab '{name}' already exists")
    for n in nodes:
        if n["type"] == "router":
            labs.write_inplace(labs.lab_dir(name) / "configs" / n["id"] / "frr.conf", n["props"].pop("frr_bootstrap"))
    labs.save(lab)
    return {"lab": labs.load(name), "report": report, "devices": len(nodes), "links": len(links)}


def _frr(d: dict, port) -> str:
    L = ["frr defaults traditional", f"hostname {d['hostname']}", "no ipv6 forwarding", "service integrated-vtysh-config", "!"]
    for i in d["ifaces"].values():
        n = i["name"]
        if n.lower().startswith("vlan") or n.lower().startswith("tunnel"):
            continue
        if n.lower().startswith("loopback"):
            num = int(re.sub(r"\D", "", n) or 0)
            ifn = "lo" if num == 0 else f"loop{num}"
        elif "." in n:
            ifn = f"{port(n.split('.')[0])}.{i['dot1q'] or n.split('.')[1]}"
        elif i["switchport"]:
            continue
        else:
            ifn = port(n)
        L.append(f"interface {ifn}")
        L.append(f" description {('[' + _short(n) + '] ' + i['desc']).strip()[:200]}")
        L += [f" ip address {a}" for a in i["addrs"]]
        if i["shut"]:
            L.append(" shutdown")
        L += ["exit", "!"]
    for pfx, nh in d["statics"]:
        L.append(f"ip route {pfx} {nh if re.match(r'^\d', nh) else 'blackhole' if nh.lower() == 'null0' else port(nh)}")
    if d["ospf"]:
        o = d["ospf"]
        L += ["!", "router ospf"] + ([f" ospf router-id {o['rid']}"] if o["rid"] else [])
        if o["passive_default"]:
            L.append(" passive-interface default")
        per_if = []
        for p in o["passive"]:
            neg = p.startswith("no ")
            nm = p[3:] if neg else p
            ifn = "lo" if nm.lower() in ("loopback0", "lo0") else port(nm) if not nm.lower().startswith("loopback") else f"loop{int(re.sub(r'\\D', '', nm))}"
            per_if.append(f"interface {ifn}\n {'no ' if neg else ''}ip ospf passive\nexit")   # FRR 10: per interface
        L += [f" network {n} area {a}" for n, a in o["networks"]] + ["exit"] + per_if
    if d["bgp"]:
        b = d["bgp"]
        L += ["!", f"router bgp {b['asn']}"] + ([f" bgp router-id {b['rid']}"] if b["rid"] else []) + [" no bgp ebgp-requires-policy"]
        for ip, n in b["neighbors"].items():
            if "remote_as" in n:
                L.append(f" neighbor {ip} remote-as {n['remote_as']}")
                if n.get("desc"):
                    L.append(f" neighbor {ip} description {n['desc'][:80]}")
        L += [" address-family ipv4 unicast"] + [f"  network {n}" for n in b["networks"]] + [" exit-address-family", "exit"]
    return "\n".join(L) + "\n!\n"
