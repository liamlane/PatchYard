import io, json, os, time, zipfile
from app import labs, impair, monitor, metrics


def test_templates_create_and_generate(client):
    ids = [t["id"] for t in client.get("/api/templates").json()]
    assert ids == ["ospf-triangle", "ospf-multiarea", "bgp-multi-as", "serial-wan", "router-on-a-stick", "campus-stp",
                   "campus-rstp-lacp", "nat-acl-edge", "dhcp-relay", "small-office", "firewall-edge", "isp-mpls"]
    for tid in ids:
        r = client.post(f"/api/templates/{tid}", json={"name": f"t-{tid}"})
        assert r.status_code == 200, r.text
        assert labs.generate(labs.load(f"t-{tid}")).exists()
    assert "name: t-isp-mpls" in labs.topo_path(labs.load("t-isp-mpls")).read_text()
    assert client.post("/api/templates/ospf-triangle", json={"name": "t-ospf-triangle"}).status_code == 409


def test_router_dir_bind_and_files(tmp):
    y = labs.topo_path(labs.load("t-ospf-triangle")).read_text()
    assert "configs/r1:/etc/frr" in y
    d = f"{tmp}/labs/t-ospf-triangle/configs/r1"
    assert {"frr.conf", "daemons", "vtysh.conf"} <= set(os.listdir(d))
    assert "ospf router-id 10.255.255.1" in open(f"{d}/frr.conf").read()


def test_switch_script_priority(tmp):
    s = open(f"{tmp}/labs/t-campus-stp/configs/sw1/switch.sh").read()
    assert "priority 4096" in s and "vlan_filtering 1" in s


def test_snapshot_diff_restore_clone_export_import(client, tmp):
    L = "t-ospf-triangle"
    s1 = client.post(f"/api/labs/{L}/snapshots", json={"label": "baseline"}).json()["snapshot"]
    p = f"{tmp}/labs/{L}/configs/r2/frr.conf"
    open(p, "a").write("ip route 0.0.0.0/0 10.0.12.1\n")
    d = client.get(f"/api/labs/{L}/snapshots/{s1['id']}/diff").json()
    assert any(c["path"].endswith("r2/frr.conf") and "+ip route 0.0.0.0/0" in c["diff"] for c in d["changes"])
    r = client.post(f"/api/labs/{L}/snapshots/{s1['id']}/restore").json()
    assert "ip route 0.0.0.0" not in open(p).read() and r["safety_snapshot"]["auto"]
    assert client.get(f"/api/labs/{L}/snapshots/..%2f..%2fetc/diff").status_code in (400, 404)
    assert client.post(f"/api/labs/{L}/clone", json={"name": "t-clone"}).json()["lab"]["name"] == "t-clone"
    z = client.get(f"/api/labs/{L}/export")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert client.post("/api/labs/import-zip?name=t-imp", content=z.content).status_code == 200
    bad = io.BytesIO(); zz = zipfile.ZipFile(bad, "w")
    zz.writestr("lab.json", json.dumps({"name": "x", "nodes": [], "links": []})); zz.writestr("configs/../../evil", "x"); zz.close()
    client.post("/api/labs/import-zip?name=t-evil", content=bad.getvalue())
    assert not os.path.exists(f"{tmp}/labs/evil") and not os.path.exists(f"{tmp}/evil")


def test_annotation_validation(client):
    lab = labs.load("t-ospf-triangle")
    lab["annotations"].append({"id": "bad id", "kind": "note"})
    assert client.put("/api/labs/t-ospf-triangle", json=lab).status_code == 400


def test_security_basics(client):
    assert client.get("/" + "..%2f" * 8 + "etc/passwd").text[:15] != "root:x:0:0:root"
    assert client.get("/api/nope").status_code == 404


def test_impair_args_and_persistence(client):
    assert impair.netem_args(impair.clean({"delay": 55, "jitter": 20, "loss": 0.8, "rate": 25000})) == \
        ["delay", "55ms", "20ms", "distribution", "normal", "loss", "0.8%", "rate", "25000kbit"]
    L = "t-ospf-triangle"
    r = client.post(f"/api/labs/{L}/links/l1/impair", json={"impair": {**impair.PRESETS["4g"], "preset": "4g"}}).json()
    assert "next deploy" in r["output"] and labs.load(L)["links"][0]["impair"]["delay"] == 55
    client.post(f"/api/labs/{L}/links/l1/impair", json={"impair": None})
    assert "impair" not in labs.load(L)["links"][0]


def test_history_bucketing(client):
    con = metrics._db(); now = time.time()
    rows = [("m", "r1", "eth1", now - 3600 + i * 10, i * 125000, i * 12500, i * 100, i * 10, 0, 0, 0, 0) for i in range(361)]
    rows = [r for r in rows if not (1800 < now - r[3] < 2400)]
    con.executemany("INSERT INTO iface VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows); con.commit()
    h = client.get("/api/labs/m/metrics/history?node=r1&iface=eth1&range=3600").json()
    vals = [p for p in h["points"] if p[1] is not None]
    assert vals and all(abs(p[1] - 100000) < 1 for p in vals)
    assert any(p[1] is None for p in h["points"])


def test_frr_parsers():
    rt = monitor.norm_routes({"C1A": {"0.0.0.0/0": [{"protocol": "bgp", "selected": True, "nexthops": [
        {"ip": "81.10.254.1", "vrf": "default", "labels": [80], "recursive": True, "active": True}]}]}})
    assert rt[0]["nexthops"][0] == "via 81.10.254.1 label 80 (recursive)"
    assert monitor.norm_ospf({"neighbors": {"1.1.1.1": [{"nbrState": "Full/-", "ifaceName": "eth1:x"}]}})[0]["full"]
    bg = monitor.norm_bgp({"default": {"ipv4Vpn": {"as": 1, "peers": {"a": {"state": "Established"}}}}})
    assert bg[0]["up"] and bg[0]["afi"] == "ipv4Vpn"


def test_env_names_and_system(client, monkeypatch):
    from app.env import env
    monkeypatch.setenv("NETLAB_FOO", "old"); assert env("FOO") == "old"
    monkeypatch.setenv("PATCHYARD_FOO", "new"); assert env("FOO") == "new"
    assert client.get("/api/version").json()["name"] == "Patchyard"
    caps = client.get("/api/system").json()
    assert {"bridge", "mpls", "ovs", "nftables"} <= set(caps["features"])
    t = {x["id"]: x for x in client.get("/api/templates").json()}
    assert t["isp-mpls"]["needs"] == ["mpls", "vrf"] and t["ospf-triangle"]["needs"] == []
