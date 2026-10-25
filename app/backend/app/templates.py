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


TEMPLATES = {
    "ospf-triangle": {"title": "OSPF triangle", "fn": ospf_triangle, "devices": 5,
                      "desc": "Three routers in area 0 with loopback router IDs, a PC and a DNS/web server. Fail a link and watch OSPF reroute."},
    "router-on-a-stick": {"title": "Router-on-a-stick", "fn": router_on_a_stick, "devices": 4,
                          "desc": "802.1Q trunk to a router with VLAN 10/20 subinterfaces, access ports, management SVI."},
    "campus-stp": {"title": "Campus with STP", "fn": campus_stp, "devices": 8,
                   "desc": "Three switches in a loop with STP (sw1 root), two VLANs, inter-VLAN routing and an intranet server."},
    "isp-mpls": {"title": "Dual-ISP MPLS L3VPN", "fn": isp_mpls, "devices": 27,
                 "desc": "The Phase 2 lab: route-reflector pairs, LDP, per-customer VRFs, INTERNET VRF hub-and-spoke, public DNS and web."},
}


def list_templates():
    return [{"id": k, "title": v["title"], "desc": v["desc"], "devices": v["devices"]} for k, v in TEMPLATES.items()]


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
