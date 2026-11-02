"""Brute-force protection: security log, automatic bans with escalation, manual bans, allowlist."""
import pytest


def test_bans(client):
    from app import users, auth, security, settings as st
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear(); security._FAILS.clear()
    security.BANS.unlink(missing_ok=True); security.FILE.unlink(missing_ok=True)
    cur = st.load(); cur["security"]["require_2fa"] = "off"; st.save(cur)      # independent of what earlier tests left on
    try:
        client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        bad = TestClient(app, client=("198.51.100.7", 40000))
        for i in range(9):
            assert bad.post("/api/auth/login", json={"username": "boss", "password": f"guess{i}"}).status_code in (401, 429)
        assert bad.get("/api/auth/state").status_code == 200                      # 9 failures: not yet
        bad.post("/api/auth/login", json={"username": "boss", "password": "guess9"})
        r = bad.get("/api/auth/state")
        assert r.status_code == 403 and "blocked" in r.json()["detail"]
        # even the right password from a banned address gets nothing
        assert bad.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"}).status_code == 403
        # the admin sees it
        d = client.get("/api/admin/security").json()
        assert d["bans"][0]["key"] == "198.51.100.7" and d["bans"][0]["active"] and d["bans"][0]["strikes"] == 1
        kinds = [e["kind"] for e in d["events"]]
        assert "ban.auto" in kinds and "blocked" in kinds and kinds.count("login.fail") >= 8
        assert d["summary"]["failed_logins"] >= 8 and d["summary"]["top"][0][0] == "198.51.100.7"
        # unban; a second offence bans for twice as long
        assert client.post("/api/admin/security/unban", json={"key": "198.51.100.7"}).json()["ok"]
        assert bad.get("/api/auth/state").status_code == 200
        auth._FAILS.clear()
        for i in range(10):
            bad.post("/api/auth/login", json={"username": "nobody", "password": f"x{i}"})
        b = next(x for x in client.get("/api/admin/security").json()["bans"] if x["key"] == "198.51.100.7")
        assert b["active"] and b["strikes"] == 2 and 110 * 60 < b["until"] - b["created"] <= 120 * 60 + 5
        client.post("/api/admin/security/unban", json={"key": "198.51.100.7"})
        # invalid API tokens count too
        bot = TestClient(app, client=("198.51.100.8", 40000))
        for _ in range(10):
            bot.get("/api/labs", headers={"Authorization": "Bearer pyt_guess"})
        assert bot.get("/api/labs").status_code == 403
        # allowlisted addresses are never banned, and aren't locked out of their own account
        s = client.get("/api/settings").json(); s.pop("_meta"); s["security"]["allowlist"] = ["192.0.2.0/24"]
        assert client.put("/api/settings", json=s).status_code == 200
        office = TestClient(app, client=("192.0.2.50", 40000))
        auth._FAILS.clear()
        for i in range(25):
            office.post("/api/auth/login", json={"username": "boss", "password": f"typo{i}"})
        auth._FAILS.pop("ip:192.0.2.50", None)
        assert office.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"}).json()["ok"]
        # manual bans: CIDR, not loopback, not too wide
        assert client.post("/api/admin/security/ban", json={"ip": "203.0.113.0/24", "minutes": 0, "reason": "scanner"}).json()["ok"]
        assert TestClient(app, client=("203.0.113.77", 1)).get("/api/auth/state").status_code == 403
        assert client.post("/api/admin/security/ban", json={"ip": "127.0.0.1"}).status_code == 400
        assert client.post("/api/admin/security/ban", json={"ip": "10.0.0.0/4"}).status_code == 400
        assert not [b for b in client.get("/api/admin/security").json()["bans"] if b["key"] == "203.0.113.0/24"][0].get("until")
        # loopback is exempt (the reverse proxy): never banned however much it fails
        lo = TestClient(app, client=("127.0.0.1", 1))
        auth._FAILS.clear()
        for i in range(15):
            lo.post("/api/auth/login", json={"username": "x", "password": "y"})
        assert lo.get("/api/auth/state").status_code == 200
    finally:
        cur = st.load(); cur["security"]["allowlist"] = []; cur["security"]["require_2fa"] = "auto"; st.save(cur)
        security.BANS.unlink(missing_ok=True)
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear(); security._FAILS.clear()
