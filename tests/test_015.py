"""0.15: ISP/internet plan, path explainer (in real namespaces), IOS import, NetBox import,
baseline diff, VoIP MOS, API tokens, idle stop, continuous tests."""
import asyncio, os, shutil, subprocess, time
import pytest

NS = shutil.which("ip") and os.geteuid() == 0


def sh(c):
    subprocess.run(c, shell=True, check=True, capture_output=True)


@pytest.fixture
def netlab_ns():
    """pc1 -- r1 -- r2 -- srv1 in network namespaces (r1 has an inbound ACL in the lab props)."""
    if not NS:
        pytest.skip("needs root and iproute2")
    names = ["pt-pc1", "pt-r1", "pt-r2", "pt-srv1"]
    for n in names:
        subprocess.run(f"ip netns del {n}", shell=True, capture_output=True)
        sh(f"ip netns add {n}")
        sh(f"ip -n {n} link set lo up")
    def veth(a, ai, b, bi):
        sh(f"ip link add tmpa type veth peer name tmpb")
        sh(f"ip link set tmpa netns {a}"); sh(f"ip link set tmpb netns {b}")
        sh(f"ip -n {a} link set tmpa name {ai}"); sh(f"ip -n {b} link set tmpb name {bi}")
        sh(f"ip -n {a} link set {ai} up"); sh(f"ip -n {b} link set {bi} up")
    veth("pt-pc1", "eth1", "pt-r1", "eth1")
    veth("pt-r1", "eth2", "pt-r2", "eth1")
    veth("pt-r2", "eth2", "pt-srv1", "eth1")
    for ns, i, a in (("pt-pc1", "eth1", "192.168.1.10/24"), ("pt-r1", "eth1", "192.168.1.1/24"), ("pt-r1", "eth2", "10.0.12.1/30"),
                     ("pt-r2", "eth1", "10.0.12.2/30"), ("pt-r2", "eth2", "192.168.2.1/24"), ("pt-srv1", "eth1", "192.168.2.10/24")):
        sh(f"ip -n {ns} addr add {a} dev {i}")
    sh("ip -n pt-pc1 route add default via 192.168.1.1")
    sh("ip -n pt-srv1 route add default via 192.168.2.1")
    sh("ip -n pt-r1 route add 192.168.2.0/24 via 10.0.12.2")
    sh("ip -n pt-r2 route add 192.168.1.0/24 via 10.0.12.1")
    for n in ("pt-r1", "pt-r2"):
        sh(f"ip netns exec {n} sysctl -qw net.ipv4.ip_forward=1")
    procs = {n: subprocess.Popen(["ip", "netns", "exec", f"pt-{n}", "sleep", "300"]) for n in ("pc1", "r1", "r2", "srv1")}
    time.sleep(0.3)
    yield {n: p.pid for n, p in procs.items()}
    for p in procs.values():
        p.kill()
    for n in names:
        subprocess.run(f"ip netns del {n}", shell=True, capture_output=True)


def _lab(acl=None):
    r1 = {"acls": {"BLOCK": {"type": "extended", "entries": [{"seq": 10, "text": "deny tcp any host 192.168.2.10 eq www"},
                                                            {"seq": 20, "text": "permit ip any any"}]}}}
    if acl:
        r1["ifcfg"] = {"eth1": {"acl_in": "BLOCK"}}
    return {"name": "t-trace", "nodes": [{"id": "pc1", "type": "pc", "props": {}}, {"id": "r1", "type": "router", "props": r1},
                                         {"id": "r2", "type": "router", "props": {}}, {"id": "srv1", "type": "server", "props": {}}],
            "links": [{"id": "l1", "a": "pc1", "a_if": "eth1", "b": "r1", "b_if": "eth1"}, {"id": "l2", "a": "r1", "a_if": "eth2", "b": "r2", "b_if": "eth1"},
                      {"id": "l3", "a": "r2", "a_if": "eth2", "b": "srv1", "b_if": "eth1"}]}


def test_path_explainer(netlab_ns, monkeypatch):
    from app import trace, netinfo
    async def ctrs():
        return {("t-trace", n): {"state": "running", "pid": pid, "id": n, "name": n} for n, pid in netlab_ns.items()}
    monkeypatch.setattr(netinfo, "containers", ctrs)
    r = asyncio.run(trace.explain(_lab(), "pc1", "srv1", "icmp"))
    f = r["forward"]
    assert r["ok"] and f["result"] == "delivered" and [h["node"] for h in f["hops"]] == ["pc1", "r1", "r2", "srv1"]
    assert f["hops"][1]["via"] == "10.0.12.2" and f["hops"][1]["route"] == "192.168.2.0/24"
    assert r["reply"]["result"] == "delivered"
    # an inbound ACL on r1 drops web traffic, but not ping
    r = asyncio.run(trace.explain(_lab(acl=True), "pc1", "192.168.2.10", "tcp", 80))
    assert not r["ok"] and r["forward"]["result"] == "dropped" and "BLOCK line 10" in r["forward"]["reason"]
    assert asyncio.run(trace.explain(_lab(acl=True), "pc1", "192.168.2.10", "icmp"))["ok"]
    # no route
    sh("ip -n pt-r2 route del 192.168.1.0/24")
    r = asyncio.run(trace.explain(_lab(), "pc1", "srv1", "icmp"))
    assert r["forward"]["result"] == "delivered" and r["reply"]["result"] == "no-route" and not r["ok"]
    sh("ip -n pt-r1 link set eth2 down")
    r = asyncio.run(trace.explain(_lab(), "pc1", "srv1", "icmp"))
    assert r["forward"]["result"] in ("dropped", "no-route")


def test_isp_plan():
    from app import isp
    lab = {"name": "t", "nodes": [{"id": "inet1", "type": "internet", "props": {}}, {"id": "isp1", "type": "isp", "props": {"ports": {"eth2": {"mode": "bgp", "customer_as": 65010}}}},
                                  {"id": "isp2", "type": "isp", "props": {"ports": {"eth2": {"routed": "29"}, "eth3": {"mode": "dhcp"}}}},
                                  {"id": "solo", "type": "isp", "props": {"uplink": "real"}},
                                  {"id": "r1", "type": "router", "props": {}}, {"id": "pc1", "type": "pc", "props": {}}, {"id": "r9", "type": "router", "props": {}}],
           "links": [{"id": "a", "a": "isp1", "a_if": "eth1", "b": "inet1", "b_if": "eth1"}, {"id": "b", "a": "isp2", "a_if": "eth1", "b": "inet1", "b_if": "eth2"},
                     {"id": "c", "a": "r1", "a_if": "eth1", "b": "isp1", "b_if": "eth2"}, {"id": "d", "a": "r1", "a_if": "eth2", "b": "isp2", "b_if": "eth2"},
                     {"id": "e", "a": "pc1", "a_if": "eth1", "b": "isp2", "b_if": "eth3"}, {"id": "f", "a": "r9", "a_if": "eth1", "b": "solo", "b_if": "eth1"}]}
    pl = isp.plan(lab)
    assert pl["inet1"]["hosts"] and not pl["isp1"]["hosts"] and pl["solo"]["hosts"]
    assert pl["isp1"]["asn"] != pl["isp2"]["asn"] and pl["isp1"]["pool"] != pl["isp2"]["pool"]
    assert pl["isp1"]["upstream"] == "100.100.0.1"
    p = pl["isp2"]["ports"]
    assert p[0]["cust_ip"].startswith("81.20.") and p[0]["routed"].endswith("/29") and p[1]["mode"] == "dhcp" and p[1]["range"][0]
    conf = isp.frr_conf(lab, lab["nodes"][1], pl)
    assert "neighbor 100.100.0.1 remote-as 64496" in conf and "neighbor 81.10.0.18 default-originate" in conf
    assert "default-originate" in isp.frr_conf(lab, lab["nodes"][0], pl)
    assert "1.1.1.1/32" in isp.frr_conf(lab, lab["nodes"][3], pl) and "1.1.1.1" not in isp.frr_conf(lab, lab["nodes"][1], pl)
    sol = pl["solo"]
    assert "masquerade" in isp.NAT_NFT and "nat.nft" in isp.script(sol) and "no-resolv" not in isp.dnsmasq_conf(sol)
    h = isp.customer_help(pl["isp1"], pl["isp1"]["ports"][0], lab)
    assert "router bgp 65010" in h["ios"] and " neighbor 81.10.0.17 remote-as 64501" in h["ios"]
    with pytest.raises(ValueError):
        isp.validate({"id": "x", "props": {"pool": "10.0.0.0/30"}})
    # r1 is dual-homed (isp2 eth2 is static, so only isp1 is BGP): default customer AS follows the router
    lab2 = {**lab, "nodes": [dict(n) for n in lab["nodes"]]}
    lab2["nodes"][2] = {"id": "isp2", "type": "isp", "props": {"ports": {"eth2": {"mode": "bgp"}}}}
    lab2["nodes"][1] = {"id": "isp1", "type": "isp", "props": {"ports": {"eth2": {"mode": "bgp"}}}}
    p2 = isp.plan(lab2)
    assert p2["isp1"]["ports"][0]["customer_as"] == p2["isp2"]["ports"][0]["customer_as"]
    # clashes between providers are refused
    with pytest.raises(ValueError, match="both use AS"):
        isp.validate_lab({"nodes": [{"id": "a", "type": "isp", "props": {"asn": "64501"}}, {"id": "b", "type": "isp", "props": {"asn": "64501", "pool": "81.99.0.0/16"}}], "links": []})
    with pytest.raises(ValueError, match="overlaps"):
        isp.validate_lab({"nodes": [{"id": "a", "type": "isp", "props": {"pool": "81.10.0.0/16"}}, {"id": "b", "type": "isp", "props": {"asn": "64999", "pool": "81.10.4.0/22"}}], "links": []})
    isp.validate_lab({"nodes": [{"id": "i1", "type": "internet", "props": {}}, {"id": "i2", "type": "internet", "props": {}}], "links": []})
    isp.validate({"id": "i", "type": "internet", "props": {"pool": "203.0.113.0/24"}})
    with pytest.raises(ValueError, match="public-style"):
        isp.validate({"id": "i", "type": "internet", "props": {"sites": [{"name": "intranet.lab", "ip": "10.1.1.1"}]}})
    isp.validate({"id": "i", "type": "internet", "props": {"sites": [{"name": "shop.example.org", "ip": "198.18.2.10"}], "ports": {"eth1": None}}})


def test_isp_lab_saves(client):
    lab = {"name": "t-isp", "nodes": [{"id": "isp1", "type": "isp", "x": 0, "y": 0, "props": {}}, {"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}],
           "links": [{"id": "l1", "a": "r1", "a_if": "eth1", "b": "isp1", "b_if": "eth1"}]}
    client.post("/api/labs", json={"name": "t-isp"})
    assert client.put("/api/labs/t-isp", json=lab).status_code == 200
    r = client.get("/api/labs/t-isp/nodes/isp1/isp").json()
    assert r["hosts"] and r["ports"][0]["help"]["ios"][1].startswith(" ip address 81.10.0.10")
    y = client.get("/api/labs/t-isp/clab.yml").text
    assert "isp1:" in y and "/etc/netlab-isp" in y
    client.delete("/api/labs/t-isp")


IOS_R1 = """hostname R1
interface GigabitEthernet0/0
 description link to R2
 ip address 10.0.12.1 255.255.255.252
interface GigabitEthernet0/1
 ip address 192.168.1.1 255.255.255.0
 ip access-group 101 in
 ip nat inside
interface GigabitEthernet0/2
 ip address 203.0.113.2 255.255.255.252
 ip nat outside
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
router ospf 1
 router-id 1.1.1.1
 passive-interface GigabitEthernet0/1
 network 10.0.12.0 0.0.0.3 area 0
 network 192.168.1.0 0.0.0.255 area 0
ip route 0.0.0.0 0.0.0.0 203.0.113.1
access-list 1 permit 192.168.1.0 0.0.0.255
access-list 101 permit ip any any
ip nat inside source list 1 interface GigabitEthernet0/2 overload
snmp-server community public RO
crypto key generate rsa
some weird command
"""
IOS_R2 = """hostname R2
interface GigabitEthernet0/0
 ip address 10.0.12.2 255.255.255.252
interface GigabitEthernet0/1
 description to SW1
 ip address 192.168.2.1 255.255.255.0
router ospf 1
 network 0.0.0.0 255.255.255.255 area 0
"""
IOS_SW = """hostname SW1
vlan 10
 name USERS
interface GigabitEthernet1/0/1
 switchport mode access
 switchport access vlan 10
interface GigabitEthernet1/0/24
 description uplink to R2
 switchport mode trunk
interface Vlan10
 ip address 192.168.2.2 255.255.255.0
ip default-gateway 192.168.2.1
"""


def test_ios_import(client):
    from app import labs
    r = client.post("/api/import/ios", json={"name": "t-ios", "configs": [IOS_R1, IOS_R2, IOS_SW]})
    assert r.status_code == 200, r.text
    d = r.json()
    lab = d["lab"]
    types = {n["id"]: n["type"] for n in lab["nodes"]}
    assert types == {"r1": "router", "r2": "router", "sw1": "switch"}
    pairs = {tuple(sorted((l["a"], l["b"]))) for l in lab["links"]}
    assert ("r1", "r2") in pairs and ("r2", "sw1") in pairs
    r1 = next(n for n in lab["nodes"] if n["id"] == "r1")["props"]
    assert r1["acls"]["1"]["type"] == "standard" and r1["nat"]["rules"][0]["overload"]
    assert any(c.get("nat") == "inside" for c in r1["ifcfg"].values())
    conf = (labs.lab_dir("t-ios") / "configs" / "r1" / "frr.conf").read_text()
    assert "network 10.0.12.0/30 area 0" in conf and "ospf router-id 1.1.1.1" in conf and "ip route 0.0.0.0/0 203.0.113.1" in conf
    assert "[Gi0/0] link to R2" in conf and "interface lo" in conf
    sw = next(n for n in lab["nodes"] if n["id"] == "sw1")["props"]
    assert {"id": 10, "name": "USERS"} in sw["vlans"] and sw["svi"]["ip"] == "192.168.2.2/24"
    assert any("some weird command" in x for x in d["report"]) and not any("snmp" in x for x in d["report"])
    client.delete("/api/labs/t-ios")


def test_netbox_import():
    from app import netbox, labs
    data = {
        "dcim/devices": [{"id": 1, "name": "CORE-R1", "role": {"slug": "core-router"}, "device_type": {"slug": "isr4331"}},
                         {"id": 2, "name": "ACC-SW1", "role": {"slug": "access-switch"}, "device_type": {"slug": "c9200"}},
                         {"id": 3, "name": "SRV-01", "role": {"slug": "server"}, "device_type": {"slug": "r640"}}],
        "dcim/interfaces": {1: [{"id": 11, "name": "Gi0/0/0"}, {"id": 12, "name": "Gi0/0/1"}], 2: [{"id": 21, "name": "Gi1/0/1"}, {"id": 22, "name": "Gi1/0/2"}],
                            3: [{"id": 31, "name": "eno1"}]},
        "dcim/cables": [{"id": 100, "a_terminations": [{"object_type": "dcim.interface", "object_id": 11}], "b_terminations": [{"object_type": "dcim.interface", "object_id": 21}]},
                        {"id": 101, "a_terminations": [{"object_type": "dcim.interface", "object_id": 31}], "b_terminations": [{"object_type": "dcim.interface", "object_id": 22}]}],
        "ipam/ip-addresses": [{"address": "192.168.10.1/24", "assigned_object_type": "dcim.interface", "assigned_object_id": 11},
                              {"address": "192.168.10.20/24", "assigned_object_type": "dcim.interface", "assigned_object_id": 31}],
    }

    def fake(base, token, path, params=None):
        if path == "dcim/interfaces":
            return data[path][params["device_id"]]
        return data[path]
    r = netbox.build("t-nb", "https://netbox.example.com", "x", fetch=fake)
    lab = r["lab"]
    assert {n["id"]: n["type"] for n in lab["nodes"]} == {"core-r1": "router", "acc-sw1": "switch", "srv-01": "server"}
    assert len(lab["links"]) == 2
    assert next(n for n in lab["nodes"] if n["id"] == "srv-01")["props"]["ip"] == "192.168.10.20/24"
    assert "ip address 192.168.10.1/24" in (labs.lab_dir("t-nb") / "configs" / "core-r1" / "frr.conf").read_text()
    with pytest.raises(ValueError):
        netbox.build("t-nb2", "file:///etc/passwd", "x", fetch=fake)
    labs.delete("t-nb")


def test_baseline_diff_and_mos():
    from app import baseline
    from app.traffic import mos
    a = {"routes": {"r1": {"default:10.0.0.0/24": "ospf via 10.0.12.2", "default:10.9.0.0/24": "static via 1.1.1.1"}},
         "neighbours": {"r1": {"OSPF 2.2.2.2": "Full"}}, "reach": {"pc1→srv1": True, "pc1→pc2": False}}
    b = {"routes": {"r1": {"default:10.0.0.0/24": "ospf via 10.0.13.2", "default:10.8.0.0/24": "connected via -"}},
         "neighbours": {"r1": {"OSPF 2.2.2.2": "Init"}}, "reach": {"pc1→srv1": False, "pc1→pc2": True}}
    d = baseline.diff(a, b)
    assert {x["change"] for x in d["routes"]} == {"moved", "lost", "gained"}
    assert d["neighbours"][0]["after"] == "Init" and d["broken"] == ["pc1→srv1"] and d["fixed"] == ["pc1→pc2"]
    assert mos(20, 1, 0)[1] >= 4.3 and mos(600, 60, 10)[1] < 3


def test_api_tokens_idle_and_watch(client):
    from app import users, auth, watch, settings as st, alerts
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear()
    try:
        client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        tok = client.post("/api/auth/tokens", json={"label": "ci"}).json()["token"]
        assert tok.startswith("pyt_") and client.get("/api/auth/tokens").json()[0]["label"] == "ci"
        bot = TestClient(app)
        assert bot.get("/api/labs", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        assert bot.get("/api/labs", headers={"Authorization": "Bearer pyt_nope"}).status_code == 401
        assert bot.post("/api/auth/tokens", json={"label": "x"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403
        tid = client.get("/api/auth/tokens").json()[0]["id"]
        client.delete(f"/api/auth/tokens/{tid}")
        assert bot.get("/api/labs", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        # activity tracking
        client.post("/api/labs", json={"name": "t-idle"})
        watch.ACTIVE.pop("t-idle", None)
        client.get("/api/labs/t-idle")
        assert time.time() - watch.ACTIVE["t-idle"] < 5
        # failing network tests become alert conditions
        watch.FAILING[("t-idle", "c1")] = {"label": "pc1 can reach srv1", "node": "pc1", "detail": "no reply"}
        conds = watch.conditions({"t-idle": {}})
        assert conds[0][1]["name"] == "Network test failed" and "pc1 can reach srv1" in conds[0][5]
        watch.FAILING.clear()
        client.delete("/api/labs/t-idle")
    finally:
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear()
