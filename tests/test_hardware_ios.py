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


# ---------------- 0.11.0 ----------------
from app import firewall, cfgstate


def test_firewall_ruleset_and_validation():
    props = {"interfaces": {"eth1": {"ip": "203.0.113.2/30", "zone": "outside"}, "eth2": {"ip": "192.168.1.1/24", "zone": "inside"}},
             "rules": [{"id": "r1", "from": "inside", "to": "outside", "proto": "tcp", "ports": "25", "action": "reject"},
                       {"id": "r2", "from": "dmz", "to": "inside", "proto": "any", "action": "accept"}],       # dmz has no ports: skipped
             "nat": [{"type": "masquerade", "from": "inside", "to": "outside"},
                     {"type": "dnat", "zone": "outside", "proto": "tcp", "port": 8080, "to_ip": "192.168.1.10", "to_port": 80}]}
    rs = firewall.ruleset(props)
    assert 'iifname { "eth2" } oifname { "eth1" } tcp dport { 25 } counter reject comment "r1"' in rs
    assert 'comment "r2"' not in rs
    assert 'level:inside>outside' in rs and "masquerade" in rs and "dnat to 192.168.1.10:80" in rs
    for bad in ({"zones": [{"name": "Bad Zone", "level": 1}]}, {"rules": [{"proto": "icmp", "ports": "80"}]},
                {"interfaces": {"eth1": {"ip": "10.0.0.1"}}}, {"nat": [{"type": "dnat", "zone": "outside", "proto": "tcp", "port": 0, "to_ip": "1.1.1.1"}]}):
        with pytest.raises(ValueError):
            firewall.normalise(bad)
    sc = firewall.script({"id": "fw1", "props": {**props, "gateway": "203.0.113.1"}})
    assert "ip addr add 192.168.1.1/24 dev eth2" in sc and "ip route replace default via 203.0.113.1" in sc
    assert firewall.counters('{"nftables":[{"rule":{"comment":"r1","expr":[{"counter":{"packets":3,"bytes":180}}]}}]}') == {"r1": {"packets": 3, "bytes": 180}}


def test_ovs_switch_script():
    n = {"id": "sw1", "type": "switch", "props": {"engine": "ovs", "stp": True, "stp_priority": 4096,
         "ports": {"eth1": {"mode": "access", "vlan": 10}, "eth2": {"channel": 1}, "eth3": {"channel": 1},
                   "po1": {"mode": "trunk", "allowed": "10,20", "native": 99}}, "channels": {"1": {"mode": "active"}}}}
    s = labs.switch_script(n)
    assert "rstp_enable=true other_config:rstp-priority=4096" in s
    assert "add-bond br0 po1" in s and "lacp=active" in s and "trunks=10,20,99" in s
    assert "set port eth1 vlan_mode=access tag=10" in s
    assert "apk add --no-cache iproute2 openvswitch" in labs.clab_node({"name": "x"}, n)["exec"][0]


def test_parse_frr():
    txt = """hostname R1
!
interface eth1
 description core
 ip address 10.0.0.1/30
 ip ospf area 0
 ip ospf cost 50
exit
!
router ospf
 ospf router-id 1.1.1.1
 redistribute connected
 default-information originate always
exit
!
router bgp 65001
 neighbor 10.0.0.2 remote-as 65002
 neighbor 10.0.0.2 update-source lo
 address-family ipv4 unicast
  network 192.0.2.0/24
 exit-address-family
exit
!
ip route 0.0.0.0/0 10.0.0.2 200
"""
    c = cfgstate.parse_frr(txt)
    assert c["hostname"] == "R1" and c["interfaces"]["eth1"]["ospf_area"] == "0" and c["interfaces"]["eth1"]["ospf_cost"] == 50
    assert c["ospf"]["router_id"] == "1.1.1.1" and c["ospf"]["default_originate"] == "always" and c["ospf"]["redistribute"] == ["connected"]
    assert c["bgp"]["asn"] == 65001 and c["bgp"]["neighbors"][0]["update_source"] == "lo" and c["bgp"]["networks"] == ["192.0.2.0/24"]
    assert c["static"] == [{"prefix": "0.0.0.0/0", "via": "10.0.0.2", "distance": 200, "vrf": ""}]


def test_server_services_script():
    s = labs.services_script({"services": {"tftp": {"enabled": True}, "syslog": {"enabled": True}, "ntp": {"enabled": False}}})
    assert "in.tftpd -4 -l -c -s -u root -p /srv/tftp" in s and "rsyslogd -f /etc/netlab/svc/rsyslog.conf" in s and "chronyd" not in s.split("pkill chronyd")[1]
    spec = labs.clab_node({"name": "x"}, {"id": "srv", "type": "server", "props": {}})
    assert "configs/srv/tftp:/srv/tftp" in spec["binds"] and spec["exec"][-1] == "sh /etc/netlab/svc/services.sh"


class _FakeIO:
    async def vtysh(self, *c, timeout=30):
        return 0, ""
    async def sh(self, script, timeout=30):
        return 0, "", ""
    async def ns(self, *a, timeout=30):
        return 0, "", ""


class _FakeEnv(ios.Env):
    async def states(self):
        return {}

    async def apply_l3(self):
        from app import l3
        n = next(x for x in self.load()["nodes"] if x["id"] == self.node_id)
        l3.ruleset(n.get("props") or {}, {})          # must always render
        return []


def test_switch_ios_batch_edits_props(client):
    client.post("/api/labs", json={"name": "t-swios"})
    lab = labs.load("t-swios")
    lab["nodes"] = [{"id": "sw1", "type": "switch", "x": 0, "y": 0, "props": {"chassis": "switch-24", "engine": "ovs"}}]
    labs.save(lab)
    r = asyncio.run(ios.run_batch(_FakeEnv("t-swios", "sw1", _FakeIO()), [
        "configure terminal", "vlan 10", "name USERS", "exit", "interface range Gi1/0/1 - 2", "switchport mode access",
        "switchport access vlan 10", "exit", "interface Gi1/0/5", "channel-group 3 mode active", "exit",
        "interface port-channel 3", "switchport mode trunk", "switchport trunk allowed vlan 10,20", "end", "bogus"]))
    assert r["errors"] == ["bogus"], r
    p = labs.load("t-swios")["nodes"][0]["props"]
    assert p["ports"]["eth1"]["vlan"] == 10 and p["ports"]["eth2"]["mode"] == "access"
    assert p["ports"]["eth5"]["channel"] == 3 and p["ports"]["po3"]["mode"] == "trunk" and p["ports"]["po3"]["allowed"] == "10,20"
    assert any(v["name"] == "USERS" for v in p["vlans"])


def test_firewall_endpoint_and_template(client):
    r = client.post("/api/templates/firewall-edge", json={"name": "t-fwedge"})
    assert r.status_code == 200
    bad = client.post("/api/labs/t-fwedge/nodes/fw1/firewall", json={"zones": [{"name": "inside", "level": 300}]})
    assert bad.status_code == 400
    ok = client.post("/api/labs/t-fwedge/nodes/fw1/firewall", json={**labs.load("t-fwedge")["nodes"][2]["props"], "allow_ping": False}).json()
    assert ok["ok"] and ok["props"]["allow_ping"] is False and ok["props"]["chassis"] == "fw-8"


def test_server_dns_dhcp_ftp_config():
    p = {"services": {"dns": {"enabled": True, "records": [{"name": "srv.lab", "ip": "10.0.0.5"}, {"name": "www.lab", "type": "CNAME", "target": "srv.lab"}]},
                      "dhcp": {"enabled": True, "start": "10.0.0.100", "end": "10.0.0.150", "lease": "1h", "domain": "lab.local", "tftp": "10.0.0.5",
                               "reservations": [{"mac": "AA-BB-CC-00-11-22", "ip": "10.0.0.50", "name": "printer"}]},
                      "ftp": {"enabled": True, "users": [{"user": "admin", "password": "pw"}]}}}
    c = labs.dnsmasq_conf(p)
    for want in ("host-record=srv.lab,10.0.0.5", "cname=www.lab,srv.lab", "dhcp-range=set:p0,10.0.0.100,10.0.0.150,255.255.255.0,1h",
                 "dhcp-option=tag:p0,15,lab.local", "dhcp-option=tag:p0,150,10.0.0.5", "dhcp-host=aa:bb:cc:00:11:22,10.0.0.50,printer",
                 "dhcp-leasefile=/etc/netlab/svc/dnsmasq.leases"):
        assert want in c, want
    s = labs.services_script(p)
    assert "admin:pw" in s and "vsftpd /etc/netlab/svc/vsftpd.conf" in s and "cisco" not in s
    assert "configs/srv/ftp:/srv/ftp" in labs.clab_node({"name": "x"}, {"id": "srv", "type": "server", "props": {}})["binds"]
    for bad in ({"dns": {"enabled": True, "records": [{"name": "a.lab", "type": "CNAME", "target": "nowhere.lab"}]}},
                {"dhcp": {"enabled": True, "start": "10.0.0.1", "end": "10.0.0.9", "reservations": [{"mac": "nope", "ip": "10.0.0.5"}]}},
                {"dhcp": {"enabled": True, "start": "10.0.0.1", "end": "10.0.0.9", "lease": "forever"}},
                {"ftp": {"users": [{"user": "root", "password": "x"}]}},
                {"ftp": {"users": [{"user": "bob", "password": "a'b"}]}}):
        with pytest.raises(ValueError):
            labs.validate_server({"services": bad})
    assert labs.leases("1793100000 aa:bb:cc:00:11:22 10.0.0.101 pc1 01:aa\n0 aa:bb:cc:00:11:23 10.0.0.102 * *\n") == [
        {"expires": 1793100000, "mac": "aa:bb:cc:00:11:22", "ip": "10.0.0.101", "host": "pc1"},
        {"expires": 0, "mac": "aa:bb:cc:00:11:23", "ip": "10.0.0.102", "host": ""}]


def test_server_file_areas(client):
    client.post("/api/labs", json={"name": "t-files"})
    lab = labs.load("t-files")
    lab["nodes"] = [{"id": "srv", "type": "server", "x": 0, "y": 0, "props": {}}, {"id": "pc1", "type": "pc", "x": 0, "y": 0, "props": {}}]
    labs.save(lab)
    assert client.put("/api/labs/t-files/nodes/srv/server/ftp/r1-confg", content=b"hostname R1\n").json()["size"] == 12
    assert [f["name"] for f in client.get("/api/labs/t-files/nodes/srv/server/ftp").json()] == ["r1-confg"]
    assert client.get("/api/labs/t-files/nodes/srv/server/ftp/r1-confg").content == b"hostname R1\n"
    assert client.put("/api/labs/t-files/nodes/srv/server/ftp/..evil", content=b"x").status_code == 400
    assert client.put("/api/labs/t-files/nodes/srv/server/www/x", content=b"x").status_code == 404
    assert client.put("/api/labs/t-files/nodes/pc1/server/ftp/x", content=b"x").status_code == 404
    assert client.get("/api/labs/t-files/nodes/srv/server/syslog").json() == {"lines": []}
    client.delete("/api/labs/t-files/nodes/srv/server/ftp/r1-confg")
    assert client.get("/api/labs/t-files/nodes/srv/server/ftp").json() == []
    bad = labs.load("t-files")
    bad["nodes"][0]["props"] = {"services": {"dhcp": {"enabled": True, "start": "x", "end": "y"}}}
    assert client.put("/api/labs/t-files", json=bad).status_code == 400


def test_dhcp_pools():
    p = {"services": {"dhcp": {"enabled": True, "lease": "2h", "pools": [
        {"name": "users", "start": "10.10.0.100", "end": "10.10.0.199", "mask": "255.255.255.0", "gateway": "10.10.0.1", "dns": "10.20.0.5"},
        {"name": "voice", "start": "10.30.0.10", "end": "10.30.0.50", "mask": "255.255.255.0", "gateway": "10.30.0.1", "tftp": "10.20.0.5"}]}}}
    c = labs.dnsmasq_conf(p)
    for want in ("dhcp-range=set:p0,10.10.0.100,10.10.0.199,255.255.255.0,2h", "dhcp-option=tag:p0,3,10.10.0.1",
                 "dhcp-range=set:p1,10.30.0.10,10.30.0.50,255.255.255.0,2h", "dhcp-option=tag:p1,150,10.20.0.5"):
        assert want in c, want
    with pytest.raises(ValueError):
        labs.dnsmasq_conf({"services": {"dhcp": {"enabled": True, "pools": [{"name": "x", "start": "10.0.0.10", "end": "10.0.1.10"}]}}})
    with pytest.raises(ValueError):
        labs.dnsmasq_conf({"services": {"dhcp": {"enabled": True, "pools": [{"name": "x", "start": "10.0.0.10", "end": "10.0.0.20"},
                                                                          {"name": "x", "start": "10.0.1.10", "end": "10.0.1.20"}]}}})


from app import l3


def test_acl_parse_and_nft():
    assert l3.entry_match("standard", "permit 192.168.1.0 0.0.0.255") == "ip saddr 192.168.1.0/24"
    assert l3.entry_match("standard", "deny 10.0.0.5") == "ip saddr 10.0.0.5"
    assert l3.entry_match("extended", "deny tcp any host 10.0.0.1 eq telnet") == "meta l4proto 6 ip daddr 10.0.0.1 tcp dport 23"
    assert l3.entry_match("extended", "permit udp 10.0.0.0 0.0.255.255 any range 1000 2000") == "meta l4proto 17 ip saddr 10.0.0.0/16 udp dport 1000-2000"
    assert l3.entry_match("extended", "permit tcp any any eq www 443 established") == "meta l4proto 6 tcp dport { 80, 443 } tcp flags & (ack | rst) != 0"
    assert l3.entry_match("extended", "permit icmp any any echo-reply") == "meta l4proto 1 icmp type 0"
    assert l3.entry_match("extended", "permit ip 10.0.1.0 0.0.254.255 any") == "ip saddr & 255.255.1.0 == 10.0.1.0"
    assert l3.entry_match("extended", "permit ospf any any") == "meta l4proto 89"
    for bad in ("permit tcp any", "allow ip any any", "permit tcp any any eq nope", "permit ip 10.0.0.0 any", "permit ip any any bogus"):
        with pytest.raises(l3.AclError):
            l3.parse_entry("extended", bad)
    with pytest.raises(l3.AclError):
        l3.acl_kind("250")
    assert l3.acl_kind("10") == "standard" and l3.acl_kind("2100") == "extended" and l3.acl_kind("WEB") is None
    assert l3.show_entry("standard", "permit 192.168.1.0 0.0.0.255") == "permit 192.168.1.0, wildcard bits 0.0.0.255"
    acls = l3.add_entry({}, "1", "permit 10.0.0.0 0.255.255.255")
    acls = l3.add_entry(acls, "1", "deny any")
    acls = l3.add_entry(acls, "1", "permit host 1.1.1.1", seq=5)
    assert [e["seq"] for e in acls["1"]["entries"]] == [5, 10, 20]
    with pytest.raises(l3.AclError):
        l3.add_entry(acls, "1", "permit host 2.2.2.2", seq=10)


def test_l3_ruleset_and_config_lines():
    props = {"acls": {"1": {"type": "standard", "entries": [{"seq": 10, "text": "permit 192.168.1.0 0.0.0.255"}]},
                      "WEB": {"type": "extended", "entries": [{"seq": 10, "text": "remark web only"}, {"seq": 20, "text": "permit tcp any any eq 80"}]}},
             "ifcfg": {"eth1": {"nat": "inside", "acl_in": "WEB", "helpers": ["10.20.0.5"]}, "eth2": {"nat": "outside", "acl_out": "MISSING"}},
             "nat": {"pools": {"PUB": {"start": "203.0.113.10", "end": "203.0.113.20", "mask": "255.255.255.0"}},
                     "rules": [{"type": "list", "acl": "1", "interface": "eth2", "overload": True},
                               {"type": "static", "local": "192.168.1.10", "global": "203.0.113.5"},
                               {"type": "static-port", "proto": "tcp", "local": "192.168.1.20", "lport": 80, "interface": "eth2", "gport": 8080}]}}
    r = l3.ruleset(props, {"eth2": "203.0.113.2"})
    assert 'iifname "eth1" jump f_WEB' in r and "acl:WEB:implicit" in r and "MISSING" not in r     # a missing ACL permits all, as on IOS
    assert 'meta l4proto 6 tcp dport 80 counter return comment "acl:WEB:20"' in r and "remark" not in r
    assert 'ip saddr 192.168.1.0/24 counter masquerade comment "nat:1:10"' in r
    assert 'iifname "eth2" ip daddr 203.0.113.5 counter dnat to 192.168.1.10' in r
    assert 'ip daddr 203.0.113.2 tcp dport 8080 counter dnat to 192.168.1.20:80' in r
    assert l3.static_globals(props) == ["203.0.113.5"]
    lines = l3.nat_lines(l3.normalise_nat(props["nat"]), {"eth2": "Gi0/0/1"}.get)
    assert "ip nat inside source list 1 interface Gi0/0/1 overload" in lines and "ip nat pool PUB 203.0.113.10 203.0.113.20 netmask 255.255.255.0" in lines
    assert "ip nat inside source static tcp 192.168.1.20 80 interface Gi0/0/1 8080" in lines
    assert l3.iface_lines(props["ifcfg"]["eth1"]) == [" ip helper-address 10.20.0.5", " ip access-group WEB in", " ip nat inside"]
    assert l3.acl_lines(l3.normalise_acls(props["acls"]))[:2] == ["access-list 1 permit 192.168.1.0 0.0.0.255", "!"]
    args = l3.relay_args(props, {"eth1": "192.168.1.1"}, "/run/x.pid")
    assert "--dhcp-relay=192.168.1.1,10.20.0.5" in args and "--interface=eth1" in args and "--port=0" in args
    rows = l3.nat_translations("tcp      6 117 TIME_WAIT src=192.168.1.10 dst=8.8.8.8 sport=4000 dport=80 src=8.8.8.8 dst=203.0.113.2 sport=80 dport=4000 [ASSURED] mark=0 use=1", {})
    assert rows == [{"pro": "tcp", "ig": "203.0.113.2:4000", "il": "192.168.1.10:4000", "ol": "8.8.8.8:80", "og": "8.8.8.8:80"}]
    with pytest.raises(l3.AclError):
        l3.normalise_nat({"pools": {"P": {"start": "10.0.0.20", "end": "10.0.0.10"}}})


def test_router_ios_acl_nat_vrf_batch(client):
    client.post("/api/labs", json={"name": "t-l3"})
    lab = labs.load("t-l3")
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {"chassis": "router-3s"}}]
    labs.save(lab)
    r = asyncio.run(ios.run_batch(_FakeEnv("t-l3", "r1", _FakeIO()), [
        "configure terminal", "access-list 1 permit 192.168.1.0 0.0.0.255", "access-list 1 deny any",
        "ip access-list extended WEB", "permit tcp any host 10.0.0.1 eq 80", "15 deny ip any any", "no 15", "exit",
        "vrf definition CUST", "rd 65000:1", "address-family ipv4", "route-target both 65000:1", "exit-address-family", "exit",
        "interface Gi0/0/0", "ip nat inside", "ip access-group WEB in", "ip helper-address 10.20.0.5", "vrf forwarding CUST", "exit",
        "interface Gi0/0/1", "ip nat outside", "exit",
        "ip nat inside source list 1 interface Gi0/0/1 overload", "ip nat pool PUB 203.0.113.10 203.0.113.20 prefix-length 24",
        "ip nat inside source static tcp 192.168.1.10 80 203.0.113.5 8080", "access-list 250 permit any", "end"]))
    assert r["errors"] == ["access-list 250 permit any"], r
    p = labs.load("t-l3")["nodes"][0]["props"]
    assert [e["text"] for e in p["acls"]["WEB"]["entries"]] == ["permit tcp any host 10.0.0.1 eq 80"]
    assert len(p["acls"]["1"]["entries"]) == 2
    assert p["ifcfg"]["eth1"] == {"nat": "inside", "acl_in": "WEB", "helpers": ["10.20.0.5"], "vrf": "CUST"}
    assert p["ifcfg"]["eth2"] == {"nat": "outside"}
    assert p["vrfs"]["CUST"]["rd"] == "65000:1" and p["vrfs"]["CUST"]["import"] == ["65000:1"] and p["vrfs"]["CUST"]["table"] == 1001
    assert p["nat"]["pools"]["PUB"]["mask"] == "255.255.255.0" and len(p["nat"]["rules"]) == 2
    r = asyncio.run(ios.run_batch(_FakeEnv("t-l3", "r1", _FakeIO()), ["configure terminal", "no access-list 1", "no vrf definition CUST",
                                                                       "interface Gi0/0/0", "no ip access-group WEB in", "end"]))
    p = labs.load("t-l3")["nodes"][0]["props"]
    assert "1" not in p["acls"] and "CUST" not in p["vrfs"] and p["ifcfg"]["eth1"] == {"nat": "inside", "helpers": ["10.20.0.5"]}
