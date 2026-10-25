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
    assert client.get("/api/auth/state").json()["enabled"] is False
    assert client.post("/api/auth/password", json={"new": "short"}).status_code == 400
    r = client.post("/api/auth/password", json={"new": "correct-horse-1"})
    assert r.status_code == 200 and auth.enabled()
    client.cookies.clear()
    assert client.get("/api/labs").status_code == 401
    assert client.get("/api/version").status_code == 200
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "correct-horse-1"}).status_code == 200
    assert client.get("/api/labs").status_code == 200
    assert "password_hash" not in client.get("/api/settings").json()["security"]
    s = client.get("/api/settings").json(); client.put("/api/settings", json=s)
    assert auth.enabled(), "saving settings must not drop the password"
    auth.reset()
    client.cookies.clear()
    assert not auth.enabled() and client.get("/api/labs").status_code == 200


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
