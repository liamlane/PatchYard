import asyncio
import pytest
from app import hardware, ios, labs


R3 = {"id": "r1", "type": "router", "props": {"chassis": "router-3s", "modules": {"0/1": "nim-2t", "0/3": "nim-4ge"}}}


def test_chassis_ports_and_names():
    ps = {p["name"]: p for p in hardware.ports(R3)}
    assert ps["Gi0/0/0"]["if"] == "eth1" and ps["Gi0/0/3"]["media"] == "fibre"
    assert ps["Se0/1/1"]["if"] == "eth102" and ps["Se0/1/1"]["kbit"] == 1544
    assert ps["Gi0/3/0"]["if"] == "eth301"            # empty slot 0/2 doesn't shift slot 0/3
    sw = {"id": "s", "type": "switch", "props": {"chassis": "switch-fa24"}}
    assert [p["name"] for p in hardware.ports(sw)][-2:] == ["Gi0/1", "Gi0/2"]
    assert hardware.ports({"id": "g", "type": "router", "props": {}}) is None


@pytest.mark.parametrize("typed,linux", [("g0/0/0", "eth1"), ("Gi 0/0/3", "eth4"), ("GigabitEthernet0/3/2.10", "eth303.10"),
                                         ("s0/1/0", "eth101"), ("lo0", "lo"), ("loopback 5", "loop5"), ("e1", "eth1")])
def test_resolve(typed, linux):
    assert hardware.resolve(R3, typed) == linux


def test_resolve_rejects_missing_ports():
    with pytest.raises(ValueError):
        hardware.resolve(R3, "fa0/1")
    with pytest.raises(ValueError):
        hardware.resolve(R3, "gi0/2/0")             # slot 0/2 is empty


def test_display_and_validate():
    assert hardware.display(R3, "eth101") == "Se0/1/0" and hardware.display(R3, "eth1.10", long=True) == "GigabitEthernet0/0/0.10"
    hardware.validate(R3, [{"a": "r1", "a_if": "eth301", "b": "x", "b_if": "eth1"}])
    with pytest.raises(ValueError):
        hardware.validate(R3, [{"a": "r1", "a_if": "eth201", "b": "x", "b_if": "eth1"}])
    with pytest.raises(ValueError):
        hardware.validate({"id": "r", "type": "router", "props": {"chassis": "router-2s", "modules": {"0/1": "up-4te"}}})


def test_link_speed_is_slower_end():
    lab = {"nodes": [R3, {"id": "s", "type": "switch", "props": {"chassis": "switch-fa24"}}, {"id": "g", "type": "pc", "props": {}}],
           "links": [{"a": "r1", "a_if": "eth1", "b": "s", "b_if": "eth1"}, {"a": "r1", "a_if": "eth2", "b": "g", "b_if": "eth1"}]}
    assert hardware.link_kbit(lab, lab["links"][0]) == 100_000
    assert hardware.link_kbit(lab, lab["links"][1]) == 1_000_000


def test_masks():
    assert ios.m2len("255.255.255.252") == 30 and ios.w2len("0.0.0.3") == 30
    assert ios.len2mask(24) == "255.255.255.0" and ios.len2wild(30) == "0.0.0.3"
    with pytest.raises(ValueError):
        ios.m2len("255.0.255.0")
    assert ios.vlan_text(ios.vlan_set("10,11,12,20,30-31")) == "10-12,20,30-31"


def test_grammar_abbreviation_and_errors():
    g = ios.RT_GRAMMARS["priv"]
    assert match_ok(g, "sh ip int br") and match_ok(g, "conf t") and match_ok(g, "ping 10.0.0.1 repeat 5")
    cf = ios.RT_GRAMMARS["config"]
    assert match_ok(cf, "int gig 0/0/0") and match_ok(cf, "ip route 0.0.0.0 0.0.0.0 10.0.0.1")
    assert ios.match(cf, "router".split())[0] == "incomplete"
    assert ios.match(ios.RT_GRAMMARS["if"], "ip add 10.0.0.1 255.255.255.0".split())[0] == "ok"
    assert ios.match(g, "c t".split())[0] in ("ambiguous", "ok")


def match_ok(g, line):
    return ios.match(g, line.split())[0] == "ok"


class _S:
    host, node = "r1", R3
    def ios_props(self):
        return {"extra": ["no ip domain-lookup"]}
    tr_out = ios.Session.tr_out
    ifname = ios.Session.ifname


def test_frr_config_rendered_as_ios():
    frr = """frr version 10.0
frr defaults traditional
hostname r1
!
interface eth1
 ip address 10.0.0.1/30
 ip ospf area 0
exit
!
interface eth101.20
 ip address 10.20.0.1/24
exit
!
router ospf
 ospf router-id 1.1.1.1
 network 10.1.0.0/24 area 0
exit
!
router bgp 65001
 neighbor 10.0.0.2 update-source lo
 address-family ipv4 unicast
  network 192.0.2.0/24
 exit-address-family
exit
!
ip route 0.0.0.0/0 10.0.0.2
end
"""
    out = ios.ios_config(_S(), frr)
    for want in ("interface GigabitEthernet0/0/0", " ip address 10.0.0.1 255.255.255.252", " ip ospf 1 area 0",
                 "interface Serial0/1/0.20", "router ospf 1", " router-id 1.1.1.1", " network 10.1.0.0 0.0.0.255 area 0",
                 "update-source Loopback0", "  network 192.0.2.0 mask 255.255.255.0", "ip route 0.0.0.0 0.0.0.0 10.0.0.2", "no ip domain-lookup"):
        assert want in out, want
    assert "frr version" not in out and "\nexit" not in out


def test_server_side_props_win_over_stale_autosave(client):
    client.post("/api/labs", json={"name": "t-rev"})
    lab = labs.load("t-rev")
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {"chassis": "router-2s"}}]
    labs.save(lab)
    seen = client.get("/api/labs/t-rev/status").json()["rev"]
    labs.patch_props("t-rev", "r1", lambda p: p.__setitem__("loopbacks", [1]))     # e.g. IOS 'interface loopback 1'
    stale = {**lab, "_rev": seen}
    stale["nodes"][0]["x"] = 50
    client.put("/api/labs/t-rev", json=stale)
    n = labs.load("t-rev")["nodes"][0]
    assert n["x"] == 50 and n["props"]["loopbacks"] == [1]
    st = client.get("/api/labs/t-rev/status").json()
    assert st["rev"] > seen and "r1" in st["rev_nodes"]


def test_save_rejects_cable_on_missing_port(client):
    client.post("/api/labs", json={"name": "t-hw"})
    lab = labs.load("t-hw")
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {"chassis": "router-2s"}},
                    {"id": "r2", "type": "router", "x": 0, "y": 0, "props": {}}]
    lab["links"] = [{"id": "l1", "a": "r1", "a_if": "eth101", "b": "r2", "b_if": "eth1", "media": "copper"}]
    assert client.put("/api/labs/t-hw", json=lab).status_code == 400
    lab["nodes"][0]["props"]["modules"] = {"0/1": "nim-4ge"}
    assert client.put("/api/labs/t-hw", json=lab).status_code == 200
    assert client.get("/api/hardware").json()["chassis"]["router-2s"]["type"] == "router"


def test_router_exec_creates_loopbacks_and_numbered_subifs():
    props = {"loopbacks": [1], "subifs": [{"parent": "eth1", "vlan": 10, "num": 100}]}
    ex = labs.clab_node({"name": "x"}, {"id": "r", "type": "router", "props": props})["exec"]
    assert "ip link add loop1 type dummy" in ex
    assert "ip link add link eth1 name eth1.100 type vlan id 10" in ex


def test_pc_and_server_use_netlab_dhcp_hook():
    for t in ("pc", "server"):
        spec = labs.clab_node({"name": "x"}, {"id": "h", "type": t, "props": {"ip_mode": "dhcp"}})
        assert labs.DHCP_BIND in spec["binds"]
        assert any("-s /usr/local/bin/netlab-dhcp" in c for c in spec["exec"])
    assert "> /etc/resolv.conf" in labs.DHCP_SCRIPT and "mv " not in labs.DHCP_SCRIPT


def test_pipe_filters():
    cfg = "hostname r1\ninterface Gi0/0/0\n ip address 10.0.0.1 255.255.255.0\ninterface Gi0/0/1\n shutdown\nrouter ospf 1\n network 10.0.0.0 0.0.0.255 area 0"
    import re
    assert ios.pipe_filter(cfg, "include", re.compile("ip address")) == " ip address 10.0.0.1 255.255.255.0"
    assert ios.pipe_filter(cfg, "section", re.compile("^router")).splitlines() == ["router ospf 1", " network 10.0.0.0 0.0.0.255 area 0"]
    assert ios.pipe_filter(cfg, "count", re.compile("^interface")).endswith("= 2")
    assert ios.pipe_filter(cfg, "begin", re.compile("ospf")).startswith("router ospf")


def test_rename_carries_config_folder(client):
    client.post("/api/labs", json={"name": "t-ren"})
    lab = labs.load("t-ren")
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}]
    labs.save(lab); labs.generate(labs.load("t-ren"))
    assert client.post("/api/labs/t-ren/nodes/r1/rename-files", json={"to": "core1"}).json()["moved"]
    f = labs.workdir(labs.load("t-ren")) / "configs" / "core1" / "frr.conf"
    assert f.exists() and "hostname core1" in f.read_text()


def test_reachability_without_running_hosts(client):
    client.post("/api/labs", json={"name": "t-reach"})
    lab = labs.load("t-reach")
    lab["nodes"] = [{"id": "pc1", "type": "pc", "x": 0, "y": 0, "props": {}}, {"id": "pc2", "type": "pc", "x": 0, "y": 0, "props": {}}]
    labs.save(lab)
    r = client.post("/api/labs/t-reach/reachability").json()
    assert [h["id"] for h in r["hosts"]] == ["pc1", "pc2"] and r["tried"] == 0
