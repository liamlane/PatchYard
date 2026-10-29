import asyncio, time
import pytest
from app import settings, alerts, metrics, auth, traffic, live, labs


def test_settings_roundtrip_and_validation(client):
    s = client.get("/api/settings").json()
    assert s["monitoring"]["retention_hours"] == 48 and "password_hash" not in s["security"]
    s["monitoring"]["interval"] = 1          # clamped to 2
    s["lab_defaults"]["frr_image"] = "quay.io/frrouting/frr:10.1.0"
    out = client.put("/api/settings", json=s).json()
    assert out["monitoring"]["interval"] == 2 and settings.get("lab_defaults.frr_image").endswith("10.1.0")
    bad = client.get("/api/settings").json(); bad["alerts"]["rules"].append({"type": "nope", "severity": "warning"})
    assert client.put("/api/settings", json=bad).status_code == 400
    s["lab_defaults"]["frr_image"] = "quay.io/frrouting/frr:10.0.0"; client.put("/api/settings", json=s)


def test_new_lab_uses_settings_defaults(client):
    client.post("/api/labs", json={"name": "t-defaults"})
    lab = labs.load("t-defaults"); lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}]
    labs.save(lab)
    assert "quay.io/frrouting/frr:10.0.0" in labs.generate(labs.load("t-defaults")).read_text()


def _fake_live(lab, cpu=95.0, state="up", errors=0):
    metrics.LIVE[lab] = {"ts": time.time(), "nodes": {
        "r1": {"state": "running", "cpu": cpu, "mem": 50 * 2**20, "ifaces": {"eth1": {"state": state, "errors": errors, "drops": 0, "rx_bps": 0, "tx_bps": 0}}},
        "r2": {"state": "running", "cpu": 1.0, "mem": 50 * 2**20, "ifaces": {"eth1": {"state": "up", "errors": 0, "drops": 0, "rx_bps": 0, "tx_bps": 0}}}}}


def test_alert_engine_fire_and_resolve(client):
    client.post("/api/labs", json={"name": "t-alert"})
    lab = labs.load("t-alert")
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}, {"id": "r2", "type": "router", "x": 0, "y": 0, "props": {}}]
    lab["links"] = [{"id": "l1", "a": "r1", "a_if": "eth1", "b": "r2", "b_if": "eth1", "media": "copper"}]
    labs.save(lab)
    s = settings.load(); s["alerts"]["grace_s"] = 0
    for r in s["alerts"]["rules"]:
        r["for_s"] = 0
    settings.save(s)
    ctrs = {("t-alert", "r1"): {"state": "running", "pid": 1, "id": "a", "name": "x"}, ("t-alert", "r2"): {"state": "exited", "pid": None, "id": "b", "name": "y"}}
    _fake_live("t-alert", cpu=95, state="down")
    alerts.PROTO[("t-alert", "r1")] = {"ospf": [{"router_id": "10.255.255.2", "full": False, "state": "Init"}], "bgp": [], "ldp": []}
    asyncio.run(alerts.evaluate(ctrs))
    types = {a["rule_id"] for a in alerts.list_alerts("firing")}
    assert {"r-container", "r-link", "r-cpu", "r-ospf"} <= types, types
    summ = client.get("/api/alerts/summary").json()
    assert summ["by_severity"]["critical"] >= 2 and summ["nodes"]["t-alert"]["r2"] == "critical"
    assert any(e["kind"] == "fired" for e in summ["events"])
    # errors rising
    _fake_live("t-alert", cpu=95, state="down", errors=5)
    asyncio.run(alerts.evaluate(ctrs))
    assert any(a["rule_id"] == "r-err" for a in alerts.list_alerts("firing"))
    # everything recovers
    ctrs[("t-alert", "r2")] = {"state": "running", "pid": 2, "id": "b", "name": "y"}
    _fake_live("t-alert", cpu=5, state="up", errors=5)
    alerts.PROTO[("t-alert", "r1")]["ospf"][0]["full"] = True
    asyncio.run(alerts.evaluate(ctrs))
    assert not [a for a in alerts.list_alerts("firing") if a["lab"] == "t-alert"]
    assert client.post("/api/alerts/ack", json={}).json()["ok"]
    assert client.delete("/api/alerts/resolved").json()["deleted"] >= 4


def test_alert_grace_and_for(client):
    s = settings.load(); s["alerts"]["grace_s"] = 3600; settings.save(s)
    alerts.LAB_SEEN.clear()
    ctrs = {("t-alert", "r1"): {"state": "running", "pid": 1, "id": "a", "name": "x"}, ("t-alert", "r2"): {"state": "exited", "pid": None, "id": "b", "name": "y"}}
    _fake_live("t-alert", cpu=5)
    asyncio.run(alerts.evaluate(ctrs))
    assert not [a for a in alerts.list_alerts("firing") if a["rule_id"] == "r-container"], "grace period must suppress"
    s["alerts"]["grace_s"] = 120; settings.save(s)


def test_probe_rule_validation(client):
    s = client.get("/api/settings").json()
    s["alerts"]["rules"].append({"type": "probe_rtt", "severity": "warning", "threshold": 50})
    assert client.put("/api/settings", json=s).status_code == 400


def test_auth_flow(client):
    from app import users
    assert client.get("/api/auth/state").json()["enabled"] is False
    assert client.post("/api/auth/password", json={"new": "short"}).status_code == 400
    r = client.post("/api/auth/password", json={"new": "correct-horse-1"})       # first account = admin
    assert r.status_code == 200 and auth.enabled() and users.get("admin")["role"] == "admin"
    client.cookies.clear()
    assert client.get("/api/labs").status_code == 401
    assert client.get("/api/version").status_code == 200
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "correct-horse-1"}).status_code == 200   # username defaults to the admin
    assert client.get("/api/labs").status_code == 200
    assert "password_hash" not in client.get("/api/settings").json()["security"]
    s = client.get("/api/settings").json(); client.put("/api/settings", json=s)
    assert auth.enabled(), "saving settings must not drop the login"
    assert client.post("/api/labs", json={"name": "t-x"}, headers={"origin": "https://evil.example"}).status_code == 403
    users.FILE.unlink()
    client.cookies.clear()
    assert not auth.enabled() and client.get("/api/labs").status_code == 200


def test_multi_user_isolation(client):
    from app import users
    from fastapi.testclient import TestClient
    from app.main import app
    try:
        assert client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"}).status_code == 200
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        client.post("/api/labs", json={"name": "t-boss"})
        inv = client.post("/api/admin/invites", json={"days": 1}).json()
        assert client.get("/api/admin/invites").json()[0]["state"] == "open"
        alice = TestClient(app)
        assert alice.get(f"/api/invite/{inv['token']}").json()["role"] == "user"
        assert alice.post(f"/api/invite/{inv['token']}", json={"username": "alice", "password": "short"}).status_code == 400
        r = alice.post(f"/api/invite/{inv['token']}", json={"username": "alice", "password": "alice-pass-123"})
        assert r.status_code == 200 and r.json()["user"]["role"] == "user"
        assert TestClient(app).post(f"/api/invite/{inv['token']}", json={"username": "eve", "password": "eve-pass-1234"}).status_code == 400
        # alice sees only her labs; the admin's lab is invisible, not forbidden
        assert alice.post("/api/labs", json={"name": "t-alice"}).status_code == 200
        assert [l["name"] for l in alice.get("/api/labs").json()] == ["t-alice"]
        assert alice.get("/api/labs/t-boss").status_code == 404
        assert alice.get("/api/labs/t-boss/status").status_code == 404
        assert alice.post("/api/labs/t-boss/deploy").status_code == 404
        assert alice.get("/api/labs/t-alice").json()["owner"] == "alice"
        assert {l["name"] for l in client.get("/api/labs").json()} >= {"t-boss", "t-alice"}
        # admin-only things
        assert alice.get("/api/admin/users").status_code == 403
        assert alice.put("/api/settings", json={}).status_code == 403
        assert alice.post("/api/labs/import", json={"path": "/etc/passwd"}).status_code == 403
        assert "sandbox" not in alice.get("/api/settings").json()
        # can't take over: owner/trusted in a PUT are ignored, raw definitions and odd images refused
        lab = alice.get("/api/labs/t-alice").json()
        lab.update(owner="boss", trusted=True)
        lab["nodes"] = [{"id": "x1", "type": "pc", "x": 0, "y": 0, "props": {"image": "evil/image:latest"}}]
        assert alice.put("/api/labs/t-alice", json=lab).status_code == 403
        lab["nodes"] = [{"id": "x1", "type": "pc", "x": 0, "y": 0, "props": {}, "raw": {"kind": "linux", "image": "alpine:3.20", "privileged": True}}]
        assert alice.put("/api/labs/t-alice", json=lab).status_code == 403
        lab["nodes"] = [{"id": "x1", "type": "pc", "x": 0, "y": 0, "props": {}}]
        assert alice.put("/api/labs/t-alice", json=lab).status_code == 200
        d = alice.get("/api/labs/t-alice").json()
        assert d["owner"] == "alice" and not d.get("trusted")
        # quotas
        client.patch("/api/admin/users/alice", json={"quota": {"max_labs": 1, "max_devices": 2}})
        assert alice.post("/api/labs", json={"name": "t-alice2"}).status_code == 403
        lab = alice.get("/api/labs/t-alice").json()
        lab["nodes"] = [{"id": f"p{i}", "type": "pc", "x": 0, "y": 0, "props": {}} for i in range(3)]
        assert alice.put("/api/labs/t-alice", json=lab).status_code == 403
        # disabling logs her out; deleting needs her labs handled
        client.patch("/api/admin/users/alice", json={"disabled": True})
        assert alice.get("/api/labs").status_code == 401
        assert client.delete("/api/admin/users/alice").status_code == 409
        assert client.delete("/api/admin/users/alice?delete_labs=true").json()["deleted_labs"] == ["t-alice"]
        assert client.patch("/api/admin/users/boss", json={"disabled": True}).status_code == 400
    finally:
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()


def test_traffic_validation():
    async def fake(): return {}
    traffic.netinfo.containers = fake
    with pytest.raises(ValueError):
        asyncio.run(traffic.start({"name": "t-alert"}, {"src": "r1", "dst": "r2"}))


def test_live_link_requires_running(monkeypatch):
    async def fake(): return {("t-alert", "r1"): {"state": "running", "pid": 99999999, "id": "a", "name": "x"}}
    monkeypatch.setattr(live.netinfo, "containers", fake)
    lab = labs.load("t-alert")
    r = asyncio.run(live.add_link(lab, lab["links"][0]))
    assert not r["ok"] and "r2 is not running" in r["output"]


def test_sandbox_generation_and_checks(client, monkeypatch):
    import yaml
    from app import sandbox, settings as st, users, templates, proxy
    monkeypatch.setenv("PATCHYARD_SANDBOX", "1")
    st._cache = None
    try:
        users.create("carol", "carol-pass-123")
        lab = templates.create("small-office", "t-sbx")
        lab["owner"] = "carol"; labs.save(lab)
        lab = labs.ensure_subnet(labs.load("t-sbx"))
        assert lab["sandbox_subnet"].startswith("172.30.")
        y = yaml.safe_load(open(labs.generate(lab)).read())
        assert y["mgmt"] == {"network": "py-t-sbx", "ipv4-subnet": lab["sandbox_subnet"], "ipv4-gw": sandbox.gateway(lab["sandbox_subnet"]),
                             "external-access": False}
        for nid, n in y["topology"]["nodes"].items():
            assert n["privileged"] is False and n["cap-add"] == ["NET_ADMIN", "NET_RAW"], nid
            assert n["memory"] == "512MB" and n["cpu"] == 1.0 and n["env"]["PATCHYARD_PROXY"].endswith(":3128")
            assert not any(c.startswith("apk ") for c in n.get("exec", [])), nid
            assert not any("sysctl" in c or "/proc/sys" in c for c in n.get("exec", [])), nid
        # a second lab gets a different subnet
        templates.create("ospf-triangle", "t-sbx2")
        l2 = labs.ensure_subnet(labs.load("t-sbx2"))
        assert l2["sandbox_subnet"] != lab["sandbox_subnet"]
        # isp template: bridge renamed per lab, sysctls moved out of exec
        isp = templates.create("isp-mpls", "t-sbx-isp")
        assert all(b.startswith(labs.bridge_prefix(isp)) for b in labs.bridges(isp))
        isp = labs.ensure_subnet(labs.load("t-sbx-isp"))
        labs.generate(isp)
        assert "net.mpls.platform_labels=1048575" in labs.SANDBOX_SYSCTLS[("t-sbx-isp", "isp1-pe-a")]
        # raw definitions a user may not set
        imgs = sandbox.allowed_images()
        for bad in ({"kind": "linux", "image": "alpine:3.20", "privileged": True}, {"kind": "linux", "image": "evil:1"},
                    {"kind": "linux", "image": "alpine:3.20", "binds": ["/etc:/host-etc"]}, {"kind": "linux", "image": "alpine:3.20", "binds": ["../x:/y"]},
                    {"kind": "vr-sros"}):
            with pytest.raises(ValueError):
                sandbox.check_raw(bad, "x", imgs)
        sandbox.check_raw({"kind": "linux", "image": "alpine:3.20", "binds": ["configs/x/a:/a:ro"], "exec": ["ip a"]}, "x", imgs)
        assert "--privileged" not in sandbox.docker_args({"privileged": False, "cap-add": ["NET_ADMIN"], "memory": "512MB"})
        assert ["--memory", "512m"] == sandbox.docker_args({"privileged": False, "memory": "512MB"})[-2:]
        # package proxy allow-list
        st_now = st.load(); st_now["sandbox"]["proxy_allow"] = ["dl-cdn.alpinelinux.org"]; st.save(st_now)
        assert proxy.allowed_host("dl-cdn.alpinelinux.org") and proxy.allowed_host("eu.dl-cdn.alpinelinux.org")
        assert not proxy.allowed_host("evil.org") and not proxy.allowed_host("dl-cdn.alpinelinux.org.evil.org")
        assert proxy._from_lab("172.30.0.10") and not proxy._from_lab("8.8.8.8")
    finally:
        users.FILE.unlink(missing_ok=True)
        st._cache = None
