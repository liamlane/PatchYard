"""Ready-to-deploy topology templates with working configs, laid-out canvases and notes."""
from . import labs
from .labs import lab_dir, write_inplace


def _frr(name, lo, ifaces, ospf_nets, passive=("lo",), extra=""):
    """ifaces: [(if, ip/len, description, p2p)]"""
    t = f"frr defaults traditional\nhostname {name}\n!\ninterface lo\n ip address {lo}/32\nexit\n!\n"
    for ifn, ip, desc, p2p in ifaces:
        t += f"interface {ifn}\n description {desc}\n ip address {ip}\n" + (" ip ospf network point-to-point\n" if p2p else "") + "exit\n!\n"
    t += f"router ospf\n ospf router-id {lo}\n" + "".join(f" passive-interface {p}\n" for p in passive)
    t += "".join(f" network {n} area 0\n" for n in ospf_nets) + "exit\n!\n" + extra
    return t


def _node(i, t, x, y, **props):
    return {"id": i, "type": t, "x": x, "y": y, "props": props}


def _link(i, a, ai, b, bi, media="copper"):
    return {"id": i, "a": a, "a_if": ai, "b": b, "b_if": bi, "media": media}


def _pc(ip, gw, dns=""):
    return {"ip_mode": "static", "ip": ip, "gateway": gw, "dns": dns}


def _page(title, body):
    return (f"<!doctype html><meta charset=utf-8><title>{title}</title><body style='font:16px system-ui;margin:40px;"
            f"background:#0f1b26;color:#e7eef5'><h1 style='color:#f2c14e'>{title}</h1><p>{body}</p></body>\n")


def ospf_triangle(name):
    nodes = [_node("r1", "router", 420, 120), _node("r2", "router", 200, 360), _node("r3", "router", 640, 360),
             _node("pc1", "pc", 420, -60, **_pc("192.168.1.10/24", "192.168.1.1", "192.168.3.10")),
             _node("srv1", "server", 860, 360, **_pc("192.168.3.10/24", "192.168.3.1"),
                   services={"dns": {"enabled": True, "records": [{"name": "www.lab", "ip": "192.168.3.10"}]},
                             "http": {"enabled": True, "index_html": _page("www.lab", "Served by srv1 behind r3. Reached across OSPF area 0.")}})]
    links = [_link("l1", "r1", "eth1", "r2", "eth1", "fibre"), _link("l2", "r1", "eth2", "r3", "eth1", "fibre"),
             _link("l3", "r2", "eth2", "r3", "eth2", "fibre"), _link("l4", "r1", "eth3", "pc1", "eth1"),
             _link("l5", "r3", "eth3", "srv1", "eth1")]
    files = {
        "r1": _frr("r1", "10.255.255.1", [("eth1", "10.0.12.1/30", "TO-R2", 1), ("eth2", "10.0.13.1/30", "TO-R3", 1), ("eth3", "192.168.1.1/24", "PC LAN", 0)],
                   ["10.255.255.1/32", "10.0.12.0/30", "10.0.13.0/30", "192.168.1.0/24"], ("lo", "eth3")),
        "r2": _frr("r2", "10.255.255.2", [("eth1", "10.0.12.2/30", "TO-R1", 1), ("eth2", "10.0.23.1/30", "TO-R3", 1)],
                   ["10.255.255.2/32", "10.0.12.0/30", "10.0.23.0/30"]),
        "r3": _frr("r3", "10.255.255.3", [("eth1", "10.0.13.2/30", "TO-R1", 1), ("eth2", "10.0.23.2/30", "TO-R2", 1), ("eth3", "192.168.3.1/24", "SERVER LAN", 0)],
                   ["10.255.255.3/32", "10.0.13.0/30", "10.0.23.0/30", "192.168.3.0/24"], ("lo", "eth3")),
    }
    ann = [{"id": "ann-area", "kind": "area", "x": 140, "y": 80, "w": 620, "h": 400, "text": "OSPF area 0", "color": "blue"},
           {"id": "ann-note", "kind": "note", "x": 820, "y": 60, "w": 260, "h": 120, "color": "yellow",
            "text": "Try: PC1 Web Browser → www.lab. Then take r1–r3 down (right-click the cable) and watch OSPF reroute via r2."}]
    return nodes, links, files, ann


def router_on_a_stick(name):
    sw = {"vlans": [{"id": 1, "name": "default"}, {"id": 10, "name": "USERS"}, {"id": 20, "name": "SERVERS"}],
          "ports": {"eth1": {"mode": "trunk", "native": 1, "allowed": "10,20"}, "eth2": {"mode": "access", "vlan": 10},
                    "eth3": {"mode": "access", "vlan": 20}},
          "stp": True, "stp_priority": 4096, "svi": {"vlan": 10, "ip": "192.168.110.2/24", "gateway": "192.168.110.1"}}
    nodes = [_node("r1", "router", 400, 60, subifs=[{"parent": "eth1", "vlan": 10}, {"parent": "eth1", "vlan": 20}]),
             _node("sw1", "switch", 400, 260, **sw),
             _node("pc1", "pc", 230, 450, **_pc("192.168.110.10/24", "192.168.110.1")),
             _node("pc2", "pc", 570, 450, **_pc("192.168.120.10/24", "192.168.120.1"))]
    links = [_link("l1", "r1", "eth1", "sw1", "eth1"), _link("l2", "pc1", "eth1", "sw1", "eth2"), _link("l3", "pc2", "eth1", "sw1", "eth3")]
    files = {"r1": _frr("r1", "10.255.255.1", [("eth1.10", "192.168.110.1/24", "VLAN 10 USERS", 0), ("eth1.20", "192.168.120.1/24", "VLAN 20 SERVERS", 0)],
                        ["10.255.255.1/32", "192.168.110.0/24", "192.168.120.0/24"], ("lo", "eth1.10", "eth1.20"))}
    ann = [{"id": "ann-v10", "kind": "area", "x": 150, "y": 400, "w": 170, "h": 150, "text": "VLAN 10 USERS", "color": "green"},
           {"id": "ann-v20", "kind": "area", "x": 490, "y": 400, "w": 170, "h": 150, "text": "VLAN 20 SERVERS", "color": "purple"},
           {"id": "ann-note", "kind": "note", "x": 640, "y": 40, "w": 260, "h": 110, "color": "yellow",
            "text": "Trunk r1 eth1 ↔ sw1 eth1 carries VLAN 10 and 20 tagged. PC1 ↔ PC2 routes via r1's subinterfaces."}]
    return nodes, links, files, ann


def campus_stp(name):
    def swp(ports, prio, svi):
        return {"vlans": [{"id": 1, "name": "default"}, {"id": 10, "name": "USERS"}, {"id": 20, "name": "SERVERS"}],
                "ports": ports, "stp": True, "stp_priority": prio,
                "svi": {"vlan": 10, "ip": svi, "gateway": "192.168.110.1"}}
    tr = {"mode": "trunk", "native": 1, "allowed": "10,20"}
    nodes = [_node("r1", "router", 500, -40, subifs=[{"parent": "eth1", "vlan": 10}, {"parent": "eth1", "vlan": 20}]),
             _node("sw1", "switch", 500, 140, **swp({"eth1": tr, "eth2": tr, "eth3": tr}, 4096, "192.168.110.2/24")),
             _node("sw2", "switch", 280, 340, **swp({"eth1": tr, "eth2": tr, "eth3": {"mode": "access", "vlan": 10}, "eth4": {"mode": "access", "vlan": 20}}, 32768, "192.168.110.3/24")),
             _node("sw3", "switch", 720, 340, **swp({"eth1": tr, "eth2": tr, "eth3": {"mode": "access", "vlan": 10}, "eth4": {"mode": "access", "vlan": 20}}, 32768, "192.168.110.4/24")),
             _node("pc1", "pc", 160, 540, **_pc("192.168.110.10/24", "192.168.110.1", "192.168.120.20")),
             _node("pc2", "pc", 380, 540, **_pc("192.168.120.10/24", "192.168.120.1", "192.168.120.20")),
             _node("pc3", "pc", 620, 540, **_pc("192.168.110.11/24", "192.168.110.1", "192.168.120.20")),
             _node("srv1", "server", 840, 540, **_pc("192.168.120.20/24", "192.168.120.1"),
                   services={"dns": {"enabled": True, "records": [{"name": "www.campus.lab", "ip": "192.168.120.20"}]},
                             "http": {"enabled": True, "index_html": _page("www.campus.lab", "Campus intranet on srv1, VLAN 20.")}})]
    links = [_link("l1", "r1", "eth1", "sw1", "eth1", "fibre"), _link("l2", "sw1", "eth2", "sw2", "eth1", "fibre"),
             _link("l3", "sw1", "eth3", "sw3", "eth1", "fibre"), _link("l4", "sw2", "eth2", "sw3", "eth2", "fibre"),
             _link("l5", "sw2", "eth3", "pc1", "eth1"), _link("l6", "sw2", "eth4", "pc2", "eth1"),
             _link("l7", "sw3", "eth3", "pc3", "eth1"), _link("l8", "sw3", "eth4", "srv1", "eth1")]
    files = {"r1": _frr("r1", "10.255.255.1", [("eth1.10", "192.168.110.1/24", "VLAN 10 USERS", 0), ("eth1.20", "192.168.120.1/24", "VLAN 20 SERVERS", 0)],
                        ["10.255.255.1/32", "192.168.110.0/24", "192.168.120.0/24"], ("lo", "eth1.10", "eth1.20"))}
    ann = [{"id": "ann-core", "kind": "area", "x": 380, "y": -90, "w": 260, "h": 330, "text": "Core / distribution (STP root, priority 4096)", "color": "blue"},
           {"id": "ann-acc", "kind": "area", "x": 100, "y": 290, "w": 860, "h": 360, "text": "Access layer", "color": "green"},
           {"id": "ann-note", "kind": "note", "x": 780, "y": 0, "w": 270, "h": 130, "color": "yellow",
            "text": "sw2–sw3 closes a loop. STP blocks one port: Show VLANs/STP on sw2 or sw3. Take sw1–sw2 down and watch the blocked port start forwarding (~30 s)."}]
    return nodes, links, files, ann


def isp_mpls(name):
    """Phase 2 lab, generated into the new lab's folder, then laid out."""
    from . import tpl_isp
    root = lab_dir(name)
    tpl_isp.build(str(root), name, force=True)
    lab = labs.import_clab(str(root / "isp-lab.clab.yml"), name=name)
    pos = {"inet-r1": (900, 20), "br-inet": (900, -130), "dns1": (760, -260), "dns2": (900, -260), "web1": (1040, -260)}
    for n, base in ((1, 60), (2, 1140)):
        pos[f"isp{n}-core1"] = (base + 180, 200); pos[f"isp{n}-core2"] = (base + 480, 200)
        for i, t in enumerate("abc"):
            x = base + 330 * i
            pos[f"isp{n}-pe-{t}"] = (x, 400); pos[f"c{n}{t}-r1"] = (x, 580); pos[f"c{n}{t}-web"] = (x, 740)
    # the INET bridge lives on the host: give it (and its host-side ports) names unique to this lab
    bp = labs.bridge_prefix(lab)
    tag = bp[2:7]
    for nd in lab["nodes"]:
        if nd["id"] == "br-inet":
            nd["id"] = bp
    for l in lab["links"]:
        for s_, o in (("a", "b"), ("b", "a")):
            if l[s_] == "br-inet":
                l[s_], l[f"{s_}_if"] = bp, f"b{tag}-{l[o]}"[:15]
    pos[bp] = pos.pop("br-inet")
    for nd in lab["nodes"]:
        if nd["id"] in pos:
            nd["x"], nd["y"] = pos[nd["id"]]
    infra = lambda n: n.startswith("isp") or n == "inet-r1"
    for l in lab["links"]:
        if infra(l["a"]) and infra(l["b"]):
            l["media"] = "fibre"
    lab["annotations"] = [
        {"id": "ann-isp1", "kind": "area", "x": 0, "y": 140, "w": 800, "h": 700, "text": "ISP-1 AS65001 · 81.10.0.0/16", "color": "blue"},
        {"id": "ann-isp2", "kind": "area", "x": 1080, "y": 140, "w": 800, "h": 700, "text": "ISP-2 AS65002 · 92.20.0.0/16", "color": "orange"},
        {"id": "ann-inet", "kind": "area", "x": 700, "y": -310, "w": 480, "h": 400, "text": "INET AS65000 · 1.1.1.0/28", "color": "purple"},
        {"id": "ann-note", "kind": "note", "x": 1220, "y": -260, "w": 300, "h": 130, "color": "yellow",
         "text": "RR pair per ISP, MPLS L3VPN, per-customer VRFs, INTERNET VRF hub-and-spoke. Try: c1a-web → c2a.isp2.lab, then fail isp1-core1's INET link."}]
    labs.save(lab)
    return lab


def firewall_edge(name):
    """Zone firewall at the edge: inside LAN behind PAT, a DMZ web server published with a
    port forward, and an 'internet' with a web/DNS server. Uses hardware models."""
    fw = {"chassis": "fw-8", "policy": "levels", "allow_ping": True, "gateway": "203.0.113.1",
          "zones": [{"name": "inside", "level": 100}, {"name": "dmz", "level": 50}, {"name": "outside", "level": 0}],
          "interfaces": {"eth1": {"ip": "203.0.113.2/30", "zone": "outside", "description": "to ISP"},
                         "eth2": {"ip": "192.168.1.1/24", "zone": "inside", "description": "inside LAN"},
                         "eth3": {"ip": "172.16.1.1/24", "zone": "dmz", "description": "DMZ"}},
          "rules": [{"id": "r1", "from": "inside", "to": "outside", "proto": "tcp", "src": "any", "dst": "any", "ports": "25",
                     "action": "reject", "comment": "no SMTP out", "enabled": True}],
          "nat": [{"type": "masquerade", "from": "inside", "to": "outside"}, {"type": "masquerade", "from": "dmz", "to": "outside"},
                  {"type": "dnat", "zone": "outside", "proto": "tcp", "port": 80, "to_ip": "172.16.1.10", "to_port": 80, "comment": "publish web1"}]}
    sw = {"chassis": "switch-24", "modules": {"1/1": "up-4gsfp"}, "stp": True,
          "vlans": [{"id": 1, "name": "default"}], "svi": {"vlan": 1, "ip": "192.168.1.2/24", "gateway": "192.168.1.1"}}
    dns = {"dns": {"enabled": True, "records": [{"name": "www.example.net", "ip": "198.51.100.10"}, {"name": "shop.example.net", "ip": "203.0.113.2"}]},
           "http": {"enabled": True, "index_html": _page("www.example.net", "An internet web server. You reached it through the firewall's PAT.")}}
    nodes = [_node("isp1", "router", 640, -60, chassis="router-2s"),
             _node("inet", "server", 960, -60, services=dns, **_pc("198.51.100.10/24", "198.51.100.1")),
             _node("fw1", "firewall", 640, 160, **fw),
             _node("web1", "server", 960, 160, services={"http": {"enabled": True, "index_html": _page("shop.example.net", "DMZ web server, published by a port forward on fw1.")}},
                   **_pc("172.16.1.10/24", "172.16.1.1", "198.51.100.10")),
             _node("sw1", "switch", 640, 380, **sw),
             _node("pc1", "pc", 460, 580, **_pc("192.168.1.10/24", "192.168.1.1", "198.51.100.10")),
             _node("pc2", "pc", 820, 580, **_pc("192.168.1.11/24", "192.168.1.1", "198.51.100.10"))]
    links = [_link("l1", "isp1", "eth1", "fw1", "eth1"), _link("l2", "isp1", "eth2", "inet", "eth1"),
             _link("l3", "fw1", "eth2", "sw1", "eth1"), _link("l4", "fw1", "eth3", "web1", "eth1"),
             _link("l5", "sw1", "eth2", "pc1", "eth1"), _link("l6", "sw1", "eth3", "pc2", "eth1")]
    files = {"isp1": "frr defaults traditional\nhostname isp1\n!\ninterface eth1\n description to customer fw1\n ip address 203.0.113.1/30\nexit\n!\n"
                     "interface eth2\n description internet\n ip address 198.51.100.1/24\nexit\n!\n"}
    ann = [{"id": "ann-in", "kind": "area", "x": 400, "y": 320, "w": 560, "h": 380, "text": "inside · 192.168.1.0/24 · level 100", "color": "green"},
           {"id": "ann-dmz", "kind": "area", "x": 900, "y": 110, "w": 220, "h": 170, "text": "dmz · level 50", "color": "orange"},
           {"id": "ann-out", "kind": "area", "x": 580, "y": -120, "w": 540, "h": 180, "text": "outside / internet", "color": "red"},
           {"id": "ann-note", "kind": "note", "x": 120, "y": 120, "w": 300, "h": 170, "color": "yellow",
            "text": "Try: PC1 browser → www.example.net (PAT out). From inet, browse http://shop.example.net (port forward to web1). "
                    "Ping pc1 from inet: blocked. Watch hits under fw1 → Config → Rules / NAT."}]
    return nodes, links, files, ann


# ---------------------------------------------------------------- 0.11 templates (hardware models)
def _rtr(name, lo, ifaces, nets=(), passive=("lo",), ospf_extra=(), extra="", lo_area="0"):
    """ifaces: [(if, ip/len, description, opts)] opts: set of 'p2p', 'bw=N'. nets: [(prefix, area)]."""
    t = f"frr defaults traditional\nhostname {name}\nlog syslog informational\n!\ninterface lo\n ip address {lo}/32\nexit\n!\n"
    for ifn, ip, desc, opts in ifaces:
        t += f"interface {ifn}\n description {desc}\n ip address {ip}\n"
        for o in opts:
            if o == "p2p":
                t += " ip ospf network point-to-point\n"
            elif o.startswith("bw="):
                t += f" bandwidth {o[3:]}\n"
        t += "exit\n!\n"
    if nets:
        t += f"router ospf\n ospf router-id {lo}\n" + "".join(f" passive-interface {x}\n" for x in passive)
        t += f" network {lo}/32 area {lo_area}\n" + "".join(f" network {n} area {a}\n" for n, a in nets)
        t += "".join(f" {x}\n" for x in ospf_extra) + "exit\n!\n"
    return t + extra


def _host(i, t, x, y, ip="dhcp", gw="", dns="", model=None, **kw):
    model = model or ("server" if t == "server" else "pc")
    nic = {"pc": "nic-fe", "laptop": "nic-ge", "server": "nic-ge"}[model]
    ipp = {"ip_mode": "dhcp"} if ip == "dhcp" else _pc(ip, gw, dns)
    return _node(i, t, x, y, chassis=model, modules={"0": nic}, **ipp, **kw)


def _web(title, body, records=None, **more):
    svc = {"http": {"enabled": True, "index_html": _page(title, body)}, **more}
    if records:
        svc["dns"] = {"enabled": True, "records": records}
    return svc


def ospf_multiarea(name):
    """Backbone plus two non-backbone areas: ABR summarisation and a stub area."""
    nodes = [_node("core1", "router", 440, -40, chassis="router-3s"),
             _node("abr1", "router", 200, 160, chassis="router-3s"), _node("abr2", "router", 680, 160, chassis="router-3s"),
             _node("r3", "router", 200, 380, chassis="router-2s"), _node("r4", "router", 680, 380, chassis="router-2s"),
             _host("pc1", "pc", 200, 580, "10.1.10.10/24", "10.1.10.1", "10.2.20.10"),
             _host("srv1", "server", 680, 580, "10.2.20.10/24", "10.2.20.1",
                   services=_web("www.ospf.lab", "srv1 in stub area 2, reached through two ABRs.", [{"name": "www.ospf.lab", "type": "A", "ip": "10.2.20.10"}]))]
    links = [_link("l1", "core1", "eth1", "abr1", "eth1"), _link("l2", "core1", "eth2", "abr2", "eth1"),
             _link("l3", "abr1", "eth2", "r3", "eth1"), _link("l4", "abr2", "eth2", "r4", "eth1"),
             _link("l5", "r3", "eth2", "pc1", "eth1"), _link("l6", "r4", "eth2", "srv1", "eth1")]
    files = {
        "core1": _rtr("core1", "10.255.0.1", [("eth1", "10.0.1.1/30", "TO-ABR1", {"p2p"}), ("eth2", "10.0.2.1/30", "TO-ABR2", {"p2p"})],
                      [("10.0.1.0/30", 0), ("10.0.2.0/30", 0)]),
        "abr1": _rtr("abr1", "10.255.0.2", [("eth1", "10.0.1.2/30", "TO-CORE1", {"p2p"}), ("eth2", "10.1.12.1/30", "TO-R3", {"p2p"})],
                     [("10.0.1.0/30", 0), ("10.1.12.0/30", 1)], ospf_extra=["area 1 range 10.1.0.0/16"]),
        "abr2": _rtr("abr2", "10.255.0.3", [("eth1", "10.0.2.2/30", "TO-CORE1", {"p2p"}), ("eth2", "10.2.13.1/30", "TO-R4", {"p2p"})],
                     [("10.0.2.0/30", 0), ("10.2.13.0/30", 2)], ospf_extra=["area 2 range 10.2.0.0/16", "area 2 stub"]),
        "r3": _rtr("r3", "10.1.255.3", [("eth1", "10.1.12.2/30", "TO-ABR1", {"p2p"}), ("eth2", "10.1.10.1/24", "PC LAN", set())],
                   [("10.1.12.0/30", 1), ("10.1.10.0/24", 1)], ("lo", "eth2"), lo_area="1"),
        "r4": _rtr("r4", "10.2.255.4", [("eth1", "10.2.13.2/30", "TO-ABR2", {"p2p"}), ("eth2", "10.2.20.1/24", "SERVER LAN", set())],
                   [("10.2.13.0/30", 2), ("10.2.20.0/24", 2)], ("lo", "eth2"), ["area 2 stub"], lo_area="2"),
    }
    ann = [{"id": "ann-a0", "kind": "area", "x": 120, "y": -100, "w": 680, "h": 330, "text": "Area 0 (backbone)", "color": "blue"},
           {"id": "ann-a1", "kind": "area", "x": 100, "y": 300, "w": 220, "h": 360, "text": "Area 1 · 10.1.0.0/16", "color": "green"},
           {"id": "ann-a2", "kind": "area", "x": 580, "y": 300, "w": 220, "h": 360, "text": "Area 2 (stub) · 10.2.0.0/16", "color": "orange"},
           {"id": "ann-note", "kind": "note", "x": 860, "y": -60, "w": 290, "h": 150, "color": "yellow",
            "text": "core1: show ip route ospf → one O IA summary per area (area range on the ABRs). r4 is in a stub area: it only gets a default route from abr2. "
                    "Try removing the range on abr1 and watch core1's table grow."}]
    return nodes, links, files, ann


def bgp_multi_as(name):
    """Three ASes: iBGP over loopbacks with an OSPF underlay inside AS65001, eBGP between ASes."""
    def bgp(asn, rid, nbrs, nets, ibgp=None):
        t = f"router bgp {asn}\n bgp router-id {rid}\n no bgp ebgp-requires-policy\n"
        for ip, ras, desc in nbrs:
            t += f" neighbor {ip} remote-as {ras}\n neighbor {ip} description {desc}\n"
        if ibgp:
            t += f" neighbor {ibgp} update-source lo\n"
        t += " !\n address-family ipv4 unicast\n" + "".join(f"  network {n}\n" for n in nets)
        if ibgp:
            t += f"  neighbor {ibgp} next-hop-self\n"
        return t + " exit-address-family\nexit\n!\n"
    nodes = [_node("r1", "router", 200, 120, chassis="router-3s"), _node("r2", "router", 200, 400, chassis="router-3s"),
             _node("r3", "router", 620, 120, chassis="router-2s"), _node("r4", "router", 620, 400, chassis="router-3s"),
             _host("pc1", "pc", -40, 120, "192.168.1.10/24", "192.168.1.1", "192.168.4.10"),
             _host("srv1", "server", 860, 400, "192.168.4.10/24", "192.168.4.1",
                   services=_web("www.as65003.lab", "srv1 in AS65003. Which way did you come?", [{"name": "www.as65003.lab", "type": "A", "ip": "192.168.4.10"}]))]
    links = [_link("l1", "r1", "eth1", "r2", "eth1"), _link("l2", "r1", "eth2", "r3", "eth1"), _link("l3", "r2", "eth2", "r4", "eth1"),
             _link("l4", "r3", "eth2", "r4", "eth2"), _link("l5", "r1", "eth3", "pc1", "eth1"), _link("l6", "r4", "eth3", "srv1", "eth1")]
    files = {
        "r1": _rtr("r1", "10.255.1.1", [("eth1", "10.1.12.1/30", "iBGP/OSPF TO-R2", {"p2p"}), ("eth2", "172.16.13.1/30", "eBGP AS65002 r3", set()),
                                         ("eth3", "192.168.1.1/24", "CUSTOMER LAN", set())], [("10.1.12.0/30", 0)],
                   extra=bgp(65001, "10.255.1.1", [("10.255.1.2", 65001, "iBGP r2"), ("172.16.13.2", 65002, "r3")], ["192.168.1.0/24"], "10.255.1.2")),
        "r2": _rtr("r2", "10.255.1.2", [("eth1", "10.1.12.2/30", "iBGP/OSPF TO-R1", {"p2p"}), ("eth2", "172.16.24.1/30", "eBGP AS65003 r4", set())],
                   [("10.1.12.0/30", 0)],
                   extra=bgp(65001, "10.255.1.2", [("10.255.1.1", 65001, "iBGP r1"), ("172.16.24.2", 65003, "r4")], [], "10.255.1.1")),
        "r3": _rtr("r3", "10.255.2.3", [("eth1", "172.16.13.2/30", "eBGP AS65001 r1", set()), ("eth2", "172.16.34.1/30", "eBGP AS65003 r4", set())],
                   extra=bgp(65002, "10.255.2.3", [("172.16.13.1", 65001, "r1"), ("172.16.34.2", 65003, "r4")], ["10.255.2.3/32"])),
        "r4": _rtr("r4", "10.255.3.4", [("eth1", "172.16.24.2/30", "eBGP AS65001 r2", set()), ("eth2", "172.16.34.2/30", "eBGP AS65002 r3", set()),
                                         ("eth3", "192.168.4.1/24", "SERVER LAN", set())],
                   extra=bgp(65003, "10.255.3.4", [("172.16.24.1", 65001, "r2"), ("172.16.34.1", 65002, "r3")], ["192.168.4.0/24", "10.255.3.4/32"])),
    }
    ann = [{"id": "ann-as1", "kind": "area", "x": -100, "y": 60, "w": 420, "h": 420, "text": "AS65001 · iBGP + OSPF", "color": "blue"},
           {"id": "ann-as2", "kind": "area", "x": 540, "y": 60, "w": 200, "h": 140, "text": "AS65002 (transit)", "color": "purple"},
           {"id": "ann-as3", "kind": "area", "x": 540, "y": 340, "w": 420, "h": 150, "text": "AS65003", "color": "green"},
           {"id": "ann-note", "kind": "note", "x": 820, "y": 60, "w": 300, "h": 160, "color": "yellow",
            "text": "r1: show ip bgp → 192.168.4.0/24 via r2 (AS path 65003) beats r3 (65002 65003). Try a route-map setting local-preference 200 "
                    "on r1's neighbor 172.16.13.2, or shut r2–r4 and traceroute from pc1."}]
    return nodes, links, files, ann


def serial_wan(name):
    """Hub-and-spoke WAN over T1 serial modules, OSPF with bandwidth-based cost, a central
    server for TFTP backups and syslog."""
    se = {"chassis": "router-2s", "modules": {"0/1": "nim-2t"}}
    nodes = [_node("hq", "router", 440, 100, **se), _node("br1", "router", 160, 360, **se), _node("br2", "router", 720, 360, **se),
             _host("srv1", "server", 440, -100, "10.10.0.10/24", "10.10.0.1",
                   services=_web("hq.wan.lab", "Head office server, across a 1.544 Mbit/s T1.", [{"name": "hq.wan.lab", "type": "A", "ip": "10.10.0.10"}],
                                 tftp={"enabled": True}, syslog={"enabled": True}, ntp={"enabled": True, "stratum": 4})),
             _host("pc1", "pc", 160, 560, "10.11.0.10/24", "10.11.0.1", "10.10.0.10"),
             _host("pc2", "pc", 720, 560, "10.12.0.10/24", "10.12.0.1", "10.10.0.10")]
    links = [_link("l1", "hq", "eth101", "br1", "eth101", "serial"), _link("l2", "hq", "eth102", "br2", "eth101", "serial"),
             _link("l3", "hq", "eth1", "srv1", "eth1"), _link("l4", "br1", "eth1", "pc1", "eth1"), _link("l5", "br2", "eth1", "pc2", "eth1")]
    files = {
        "hq": _rtr("hq", "10.255.0.1", [("eth1", "10.10.0.1/24", "HQ LAN", set()), ("eth101", "10.0.0.1/30", "T1 TO-BR1", {"p2p", "bw=1544"}),
                                         ("eth102", "10.0.0.5/30", "T1 TO-BR2", {"p2p", "bw=1544"})],
                   [("10.10.0.0/24", 0), ("10.0.0.0/30", 0), ("10.0.0.4/30", 0)], ("lo", "eth1")),
        "br1": _rtr("br1", "10.255.0.11", [("eth1", "10.11.0.1/24", "BRANCH 1 LAN", set()), ("eth101", "10.0.0.2/30", "T1 TO-HQ", {"p2p", "bw=1544"})],
                    [("10.11.0.0/24", 0), ("10.0.0.0/30", 0)], ("lo", "eth1")),
        "br2": _rtr("br2", "10.255.0.12", [("eth1", "10.12.0.1/24", "BRANCH 2 LAN", set()), ("eth101", "10.0.0.6/30", "T1 TO-HQ", {"p2p", "bw=1544"})],
                    [("10.12.0.0/24", 0), ("10.0.0.4/30", 0)], ("lo", "eth1")),
    }
    ann = [{"id": "ann-hq", "kind": "area", "x": 340, "y": -160, "w": 220, "h": 360, "text": "Head office · 10.10.0.0/24", "color": "blue"},
           {"id": "ann-note", "kind": "note", "x": 840, "y": -120, "w": 300, "h": 170, "color": "yellow",
            "text": "Serial links run at T1 speed (1.544 Mbit/s). On br1: copy running-config tftp://10.10.0.10/br1-confg, then logging host 10.10.0.10 "
                    "and shut/no shut an interface: watch srv1 → Services → SYSLOG. show interfaces serial0/1/0 shows the bandwidth."}]
    return nodes, links, files, ann


def campus_rstp_lacp(name):
    """Open vSwitch campus: two distribution switches joined by an LACP port-channel,
    dual-homed access switches, RSTP, and a router-on-a-stick for inter-VLAN routing."""
    tr = {"mode": "trunk", "native": 1, "allowed": "10,20"}
    vl = [{"id": 1, "name": "default"}, {"id": 10, "name": "USERS"}, {"id": 20, "name": "SERVERS"}]

    def sw(model, prio, ports, svi, mods=None, channels=None):
        p = {"chassis": model, "engine": "ovs", "stp": True, "stp_priority": prio, "vlans": vl, "ports": ports,
             "svi": {"vlan": 10, "ip": svi, "gateway": "192.168.10.1"}}
        if mods:
            p["modules"] = mods
        if channels:
            p["channels"] = channels
        return p
    po = {"eth1": {"channel": 1}, "eth2": {"channel": 1}, "po1": tr}
    nodes = [_node("r1", "router", 440, -120, chassis="router-3s", subifs=[{"parent": "eth3", "vlan": 10}, {"parent": "eth3", "vlan": 20}]),
             _node("dist1", "switch", 260, 60, **sw("switch-dist", 4096, {**po, "eth3": tr, "eth4": tr, "eth5": tr}, "192.168.10.2/24", channels={"1": {"mode": "active"}})),
             _node("dist2", "switch", 620, 60, **sw("switch-dist", 8192, {**po, "eth3": tr, "eth4": tr}, "192.168.10.3/24", channels={"1": {"mode": "active"}})),
             _node("acc1", "switch", 260, 300, **sw("switch-24", 32768, {"eth101": tr, "eth102": tr, "eth1": {"mode": "access", "vlan": 10}},
                                                     "192.168.10.4/24", {"1/1": "up-4gsfp"})),
             _node("acc2", "switch", 620, 300, **sw("switch-24", 32768, {"eth101": tr, "eth102": tr, "eth1": {"mode": "access", "vlan": 10},
                                                                       "eth2": {"mode": "access", "vlan": 20}}, "192.168.10.5/24", {"1/1": "up-4gsfp"})),
             _host("pc1", "pc", 260, 500, "192.168.10.10/24", "192.168.10.1", "192.168.20.10"),
             _host("pc2", "pc", 540, 500, "192.168.10.11/24", "192.168.10.1", "192.168.20.10"),
             _host("srv1", "server", 760, 500, "192.168.20.10/24", "192.168.20.1",
                   services=_web("intranet.campus.lab", "Campus intranet in VLAN 20.", [{"name": "intranet.campus.lab", "type": "A", "ip": "192.168.20.10"}]))]
    links = [_link("l1", "dist1", "eth1", "dist2", "eth1", "fibre"), _link("l2", "dist1", "eth2", "dist2", "eth2", "fibre"),
             _link("l3", "dist1", "eth3", "acc1", "eth101", "fibre"), _link("l4", "dist2", "eth3", "acc1", "eth102", "fibre"),
             _link("l5", "dist1", "eth4", "acc2", "eth101", "fibre"), _link("l6", "dist2", "eth4", "acc2", "eth102", "fibre"),
             _link("l7", "r1", "eth3", "dist1", "eth5", "fibre"), _link("l8", "acc1", "eth1", "pc1", "eth1"),
             _link("l9", "acc2", "eth1", "pc2", "eth1"), _link("l10", "acc2", "eth2", "srv1", "eth1")]
    files = {"r1": _rtr("r1", "10.255.255.1", [("eth3.10", "192.168.10.1/24", "VLAN 10 USERS", set()), ("eth3.20", "192.168.20.1/24", "VLAN 20 SERVERS", set())])}
    ann = [{"id": "ann-dist", "kind": "area", "x": 160, "y": 0, "w": 580, "h": 140, "text": "Distribution · Po1 LACP · RSTP root dist1 (4096)", "color": "blue"},
           {"id": "ann-note", "kind": "note", "x": 860, "y": -120, "w": 300, "h": 170, "color": "yellow",
            "text": "Open vSwitch engine (needs the openvswitch kernel module; deploy/setup.sh loads it). dist1: show etherchannel summary, show spanning-tree. "
                    "Pull one Po1 cable: traffic keeps flowing. Fail dist1–acc1: RSTP unblocks the dist2 uplink in a couple of seconds."}]
    return nodes, links, files, ann


def small_office(name):
    """A small office behind a firewall: one server doing DHCP, DNS, web, TFTP, FTP, syslog and
    NTP for DHCP clients, PAT out to an 'internet' web server."""
    fw = {"chassis": "fw-8", "policy": "levels", "allow_ping": True, "gateway": "203.0.113.1",
          "zones": [{"name": "inside", "level": 100}, {"name": "outside", "level": 0}],
          "interfaces": {"eth1": {"ip": "203.0.113.2/30", "zone": "outside", "description": "to ISP"},
                         "eth2": {"ip": "192.168.10.1/24", "zone": "inside", "description": "office LAN"}},
          "rules": [], "nat": [{"type": "masquerade", "from": "inside", "to": "outside"}]}
    office = {
        "dhcp": {"enabled": True, "domain": "office.lab", "tftp": "192.168.10.5", "lease": "8h", "reservations": [],
                 "pools": [{"name": "office", "start": "192.168.10.100", "end": "192.168.10.199", "mask": "255.255.255.0",
                            "gateway": "192.168.10.1", "dns": "192.168.10.5"}]},
        "dns": {"enabled": True, "records": [{"name": "srv1.office.lab", "type": "A", "ip": "192.168.10.5"},
                                             {"name": "intranet.office.lab", "type": "CNAME", "target": "srv1.office.lab"},
                                             {"name": "www.example.net", "type": "A", "ip": "198.51.100.10"}]},
        "http": {"enabled": True, "index_html": _page("intranet.office.lab", "Office intranet on srv1. Files: ftp 192.168.10.5 (cisco / cisco).")},
        "tftp": {"enabled": True}, "ftp": {"enabled": True, "users": [{"user": "cisco", "password": "cisco"}, {"user": "staff", "password": "office"}]},
        "syslog": {"enabled": True}, "ntp": {"enabled": True, "stratum": 3}}
    nodes = [_node("isp1", "router", 440, -260, chassis="router-2s"),
             _host("inet", "server", 760, -260, "198.51.100.10/24", "198.51.100.1",
                   services=_web("www.example.net", "An internet web server, reached through the office firewall's PAT.")),
             _node("fw1", "firewall", 440, -60, **fw),
             _node("sw1", "switch", 440, 140, chassis="switch-24", modules={"1/1": "up-4gsfp"}, stp=True,
                   vlans=[{"id": 1, "name": "default"}], svi={"vlan": 1, "ip": "192.168.10.2/24", "gateway": "192.168.10.1"}),
             _host("srv1", "server", 760, 140, "192.168.10.5/24", "192.168.10.1", "192.168.10.5", services=office),
             _host("pc1", "pc", 200, 360), _host("pc2", "pc", 440, 360), _host("laptop1", "pc", 680, 360, model="laptop")]
    links = [_link("l1", "isp1", "eth1", "fw1", "eth1"), _link("l2", "isp1", "eth2", "inet", "eth1"), _link("l3", "fw1", "eth2", "sw1", "eth1"),
             _link("l4", "sw1", "eth2", "srv1", "eth1"), _link("l5", "sw1", "eth3", "pc1", "eth1"), _link("l6", "sw1", "eth4", "pc2", "eth1"),
             _link("l7", "sw1", "eth5", "laptop1", "eth1")]
    files = {"isp1": "frr defaults traditional\nhostname isp1\n!\ninterface eth1\n description to office fw1\n ip address 203.0.113.1/30\nexit\n!\n"
                     "interface eth2\n description internet\n ip address 198.51.100.1/24\nexit\n!\n"}
    ann = [{"id": "ann-lan", "kind": "area", "x": 120, "y": 80, "w": 800, "h": 360, "text": "Office LAN · 192.168.10.0/24 · DHCP from srv1", "color": "green"},
           {"id": "ann-note", "kind": "note", "x": 920, "y": -120, "w": 300, "h": 190, "color": "yellow",
            "text": "PCs get addresses from srv1 (see srv1 → Services → DHCP → Leases). Try: browse intranet.office.lab and www.example.net, "
                    "ipconfig /all, ftp 192.168.10.5 (cisco/cisco), nslookup intranet.office.lab. On sw1: logging host 192.168.10.5."}]
    return nodes, links, files, ann



def nat_acl_edge(name):
    """CCNA-style branch edge: PAT for the LAN, a web server published with static port NAT,
    and an extended ACL on the outside interface (only the published port, return traffic
    and ping replies get in)."""
    r1 = {"chassis": "router-2s",
          "acls": {"1": {"type": "standard", "entries": [{"seq": 10, "text": "permit 192.168.1.0 0.0.0.255"}]},
                   "OUTSIDE-IN": {"type": "extended", "entries": [
                       {"seq": 10, "text": "remark published web server and return traffic only"},
                       {"seq": 20, "text": "permit tcp any host 203.0.113.2 eq www"},
                       {"seq": 30, "text": "permit tcp any any established"},
                       {"seq": 40, "text": "permit udp any eq domain any"},
                       {"seq": 50, "text": "permit icmp any any echo-reply"},
                       {"seq": 60, "text": "permit icmp any any unreachable"},
                       {"seq": 70, "text": "permit icmp any any time-exceeded"},
                       {"seq": 80, "text": "deny ip any any log"}]}},
          "ifcfg": {"eth1": {"nat": "outside", "acl_in": "OUTSIDE-IN"}, "eth2": {"nat": "inside"}},
          "nat": {"pools": {}, "rules": [{"type": "list", "acl": "1", "interface": "eth1", "overload": True},
                                         {"type": "static-port", "proto": "tcp", "local": "192.168.1.20", "lport": 80, "interface": "eth1", "gport": 80}]}}
    dns = [{"name": "www.example.net", "type": "A", "ip": "198.51.100.10"}, {"name": "shop.branch.example", "type": "A", "ip": "203.0.113.2"}]
    nodes = [_node("isp1", "router", 440, -220, chassis="router-2s"),
             _host("inet", "server", 760, -220, "198.51.100.10/24", "198.51.100.1",
                   services=_web("www.example.net", "An internet web server, reached through r1's PAT.", dns)),
             _node("r1", "router", 440, -20, **r1),
             _node("sw1", "switch", 440, 180, chassis="switch-24", modules={"1/1": "up-4gsfp"}, stp=True,
                   vlans=[{"id": 1, "name": "default"}], svi={"vlan": 1, "ip": "192.168.1.2/24", "gateway": "192.168.1.1"}),
             _host("web1", "server", 760, 180, "192.168.1.20/24", "192.168.1.1", "198.51.100.10",
                   services=_web("shop.branch.example", "Branch web server, published on r1's outside address with static port NAT.")),
             _host("pc1", "pc", 260, 380, "192.168.1.10/24", "192.168.1.1", "198.51.100.10"),
             _host("pc2", "pc", 620, 380, "192.168.1.11/24", "192.168.1.1", "198.51.100.10")]
    links = [_link("l1", "isp1", "eth1", "r1", "eth1"), _link("l2", "isp1", "eth2", "inet", "eth1"), _link("l3", "r1", "eth2", "sw1", "eth1"),
             _link("l4", "sw1", "eth2", "web1", "eth1"), _link("l5", "sw1", "eth3", "pc1", "eth1"), _link("l6", "sw1", "eth4", "pc2", "eth1")]
    files = {"isp1": "frr defaults traditional\nhostname isp1\n!\ninterface eth1\n description to branch r1\n ip address 203.0.113.1/30\nexit\n!\n"
                     "interface eth2\n description internet\n ip address 198.51.100.1/24\nexit\n!\n",
             "r1": "frr defaults traditional\nhostname r1\n!\ninterface eth1\n description to ISP (NAT outside)\n ip address 203.0.113.2/30\nexit\n!\n"
                   "interface eth2\n description branch LAN (NAT inside)\n ip address 192.168.1.1/24\nexit\n!\nip route 0.0.0.0/0 203.0.113.1\n!\n"}
    ann = [{"id": "ann-lan", "kind": "area", "x": 160, "y": 120, "w": 780, "h": 360, "text": "Branch LAN · 192.168.1.0/24 · NAT inside", "color": "green"},
           {"id": "ann-out", "kind": "area", "x": 340, "y": -280, "w": 600, "h": 160, "text": "Internet · NAT outside", "color": "red"},
           {"id": "ann-note", "kind": "note", "x": 980, "y": -140, "w": 300, "h": 190, "color": "yellow",
            "text": "PCs browse www.example.net through PAT (r1: show ip nat translations). From inet, browse http://shop.branch.example: static NAT to web1. "
                    "Ping r1 from inet: denied by OUTSIDE-IN (show access-lists for the hit counts). Remove 'permit tcp any any established' and watch browsing break."}]
    return nodes, links, files, ann


def dhcp_relay(name):
    """One DHCP server for every VLAN: router-on-a-stick with ip helper-address on each
    user subinterface, and a pool per VLAN on the server."""
    pools = [{"name": "users", "start": "10.10.10.100", "end": "10.10.10.199", "mask": "255.255.255.0", "gateway": "10.10.10.1", "dns": "10.10.99.10"},
             {"name": "guests", "start": "10.10.20.100", "end": "10.10.20.199", "mask": "255.255.255.0", "gateway": "10.10.20.1", "dns": "10.10.99.10"}]
    srv = {"dhcp": {"enabled": True, "lease": "4h", "domain": "campus.lab", "reservations": [], "pools": pools},
           "dns": {"enabled": True, "records": [{"name": "srv1.campus.lab", "type": "A", "ip": "10.10.99.10"},
                                                {"name": "intranet.campus.lab", "type": "CNAME", "target": "srv1.campus.lab"}]},
           "http": {"enabled": True, "index_html": _page("intranet.campus.lab", "Every VLAN gets its address from this one server, relayed by r1.")},
           "syslog": {"enabled": True}}
    tr = {"mode": "trunk", "native": 1, "allowed": "10,20,99"}
    sw = {"chassis": "switch-24", "modules": {"1/1": "up-4gsfp"}, "stp": True,
          "vlans": [{"id": 1, "name": "default"}, {"id": 10, "name": "USERS"}, {"id": 20, "name": "GUESTS"}, {"id": 99, "name": "SERVERS"}],
          "ports": {"eth1": tr, "eth2": {"mode": "access", "vlan": 99}, "eth3": {"mode": "access", "vlan": 10}, "eth4": {"mode": "access", "vlan": 10},
                    "eth5": {"mode": "access", "vlan": 20}},
          "svi": {"vlan": 99, "ip": "10.10.99.2/24", "gateway": "10.10.99.1"}}
    r1 = {"chassis": "router-3s", "subifs": [{"parent": "eth1", "vlan": 10}, {"parent": "eth1", "vlan": 20}, {"parent": "eth1", "vlan": 99}],
          "ifcfg": {"eth1.10": {"helpers": ["10.10.99.10"]}, "eth1.20": {"helpers": ["10.10.99.10"]}}}
    nodes = [_node("r1", "router", 440, -40, **r1), _node("sw1", "switch", 440, 160, **sw),
             _host("srv1", "server", 760, 160, "10.10.99.10/24", "10.10.99.1", "10.10.99.10", services=srv),
             _host("pc1", "pc", 160, 380), _host("pc2", "pc", 380, 380), _host("laptop1", "pc", 620, 380, model="laptop")]
    links = [_link("l1", "r1", "eth1", "sw1", "eth1"), _link("l2", "sw1", "eth2", "srv1", "eth1"), _link("l3", "sw1", "eth3", "pc1", "eth1"),
             _link("l4", "sw1", "eth4", "pc2", "eth1"), _link("l5", "sw1", "eth5", "laptop1", "eth1")]
    files = {"r1": _rtr("r1", "10.255.255.1", [("eth1.10", "10.10.10.1/24", "VLAN 10 USERS", set()), ("eth1.20", "10.10.20.1/24", "VLAN 20 GUESTS", set()),
                                                ("eth1.99", "10.10.99.1/24", "VLAN 99 SERVERS", set())])}
    ann = [{"id": "ann-u", "kind": "area", "x": 80, "y": 320, "w": 420, "h": 160, "text": "VLAN 10 USERS · 10.10.10.0/24", "color": "green"},
           {"id": "ann-g", "kind": "area", "x": 540, "y": 320, "w": 200, "h": 160, "text": "VLAN 20 GUESTS", "color": "orange"},
           {"id": "ann-s", "kind": "area", "x": 680, "y": 100, "w": 200, "h": 160, "text": "VLAN 99 SERVERS", "color": "purple"},
           {"id": "ann-note", "kind": "note", "x": 900, "y": -100, "w": 300, "h": 170, "color": "yellow",
            "text": "PCs in VLAN 10 and 20 get addresses from srv1 in VLAN 99: r1 relays their broadcasts (ip helper-address on Gi0/0/0.10 and .20). "
                    "srv1 → Services → DHCP shows a pool per VLAN and the leases. Remove a helper and that VLAN stops getting addresses."}]
    return nodes, links, files, ann



TEMPLATES = {
    "ospf-triangle": {"title": "OSPF triangle", "fn": ospf_triangle, "devices": 5,
                      "desc": "Three routers in area 0 with loopback router IDs, a PC and a DNS/web server. Fail a link and watch OSPF reroute."},
    "ospf-multiarea": {"title": "OSPF multi-area", "fn": ospf_multiarea, "devices": 7,
                       "desc": "Backbone and two areas: ABR summarisation with area range, a stub area, inter-area routes (O IA)."},
    "bgp-multi-as": {"title": "BGP between three ASes", "fn": bgp_multi_as, "devices": 6,
                     "desc": "iBGP over loopbacks with an OSPF underlay, eBGP to two neighbour ASes, AS-path best-path selection to tweak."},
    "serial-wan": {"title": "Serial WAN hub-and-spoke", "fn": serial_wan, "devices": 6,
                   "desc": "HQ and two branches over T1 serial modules, OSPF with bandwidth costs, a central TFTP/syslog/NTP server."},
    "router-on-a-stick": {"title": "Router-on-a-stick", "fn": router_on_a_stick, "devices": 4,
                          "desc": "802.1Q trunk to a router with VLAN 10/20 subinterfaces, access ports, management SVI."},
    "campus-stp": {"title": "Campus with STP", "fn": campus_stp, "devices": 8,
                   "desc": "Three switches in a loop with STP (sw1 root), two VLANs, inter-VLAN routing and an intranet server."},
    "campus-rstp-lacp": {"title": "Campus RSTP + LACP (OVS)", "fn": campus_rstp_lacp, "devices": 8,
                         "desc": "Open vSwitch campus: LACP port-channel between distribution switches, dual-homed access, RSTP, inter-VLAN routing."},
    "nat-acl-edge": {"title": "NAT and ACLs at the edge", "fn": nat_acl_edge, "devices": 7,
                     "desc": "Branch router with PAT, a web server published by static port NAT, and an extended ACL on the outside interface (CCNA style)."},
    "dhcp-relay": {"title": "Central DHCP with relay", "fn": dhcp_relay, "devices": 6,
                   "desc": "One server hands out addresses to every VLAN: router-on-a-stick with ip helper-address, a DHCP pool per VLAN."},
    "small-office": {"title": "Small office", "fn": small_office, "devices": 8,
                     "desc": "Firewall with PAT, one server doing DHCP, DNS, web, FTP, TFTP, syslog and NTP for DHCP clients, plus an internet web server."},
    "firewall-edge": {"title": "Firewall edge with DMZ", "fn": firewall_edge, "devices": 7,
                      "desc": "Zone firewall with PAT for the inside LAN, a DMZ web server published by a port forward, and an internet with web and DNS."},
    "isp-mpls": {"title": "Dual-ISP MPLS L3VPN", "fn": isp_mpls, "devices": 27,
                 "desc": "The Phase 2 lab: route-reflector pairs, LDP, per-customer VRFs, INTERNET VRF hub-and-spoke, public DNS and web."},
}


NEEDS = {"isp-mpls": ["mpls", "vrf"], "campus-rstp-lacp": ["ovs", "vlan"], "router-on-a-stick": ["vlan"], "campus-stp": ["vlan"],
         "firewall-edge": ["nftables"], "small-office": ["nftables"], "serial-wan": ["netem"],
         "nat-acl-edge": ["nftables"], "dhcp-relay": ["vlan"]}


def list_templates():
    return [{"id": k, "title": v["title"], "desc": v["desc"], "devices": v["devices"], "needs": NEEDS.get(k, [])} for k, v in TEMPLATES.items()]


def create(tid: str, name: str) -> dict:
    labs.check_name(name)
    if tid not in TEMPLATES:
        raise ValueError(f"unknown template '{tid}'")
    if (lab_dir(name) / "lab.json").exists():
        raise FileExistsError(f"lab '{name}' already exists")
    t = TEMPLATES[tid]
    if tid == "isp-mpls":
        return t["fn"](name)
    nodes, links, files, ann = t["fn"](name)
    lab = {"name": name, "mgmt_network": labs._d("mgmt_network"), "mgmt_subnet": labs._d("mgmt_subnet"),
           "nodes": nodes, "links": links, "annotations": ann, "template": tid}
    for r, txt in files.items():
        write_inplace(lab_dir(name) / "configs" / r / "frr.conf", txt)
    labs.save(lab)
    return lab
