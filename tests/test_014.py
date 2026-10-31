"""0.14: two-factor sign-in, alert channels, challenge leaderboard, new built-in challenges."""
import asyncio, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


def test_totp_rfc6238():
    from app import totp
    # RFC 6238 test vector (SHA1, secret "12345678901234567890"), last 6 digits
    import base64
    sec = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(sec, 59 // 30) == "287082"
    assert totp.code_at(sec, 1111111109 // 30) == "081804"
    assert totp.check(sec, "287082", now=59) == 1
    assert totp.check(sec, "287082", last_step=1, now=59) is None          # no replay
    assert totp.check(sec, "000000", now=59) is None
    assert totp.uri(sec, "bob").startswith("otpauth://totp/Patchyard%3Abob?secret=")


def test_two_factor_flow(client):
    from app import users, auth, totp, settings as st
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear()
    try:
        client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        users.create("gil", "gil-pass-1234")
        gil = TestClient(app)
        assert gil.post("/api/auth/login", json={"username": "gil", "password": "gil-pass-1234"}).json()["ok"]
        # enrol
        r = gil.post("/api/auth/2fa/setup").json()
        assert r["uri"].startswith("otpauth://totp/") and len(r["secret"]) == 32
        assert gil.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 400
        codes = gil.post("/api/auth/2fa/enable", json={"code": totp.code_at(r["secret"], int(time.time() // 30))}).json()["recovery_codes"]
        assert len(codes) == 10 and gil.get("/api/auth/state").json()["user"]["two_factor"]
        # sign in again: password -> ticket -> code
        g2 = TestClient(app)
        r2 = g2.post("/api/auth/login", json={"username": "gil", "password": "gil-pass-1234"}).json()
        assert r2["two_factor"] and not r2["ok"] and "ticket" in r2
        assert g2.get("/api/labs").status_code == 401                         # no session yet
        assert g2.post("/api/auth/2fa/login", json={"ticket": r2["ticket"], "code": "123456"}).status_code == 401
        assert g2.post("/api/auth/2fa/login", json={"ticket": "2fa.gil.1.1.x", "code": "123456"}).status_code == 401
        ok = g2.post("/api/auth/2fa/login", json={"ticket": r2["ticket"], "code": codes[0]}).json()
        assert ok["ok"] and ok["recovery_left"] == 9
        assert g2.get("/api/labs").status_code == 200
        g3 = TestClient(app)
        t3 = g3.post("/api/auth/login", json={"username": "gil", "password": "gil-pass-1234"}).json()["ticket"]
        assert g3.post("/api/auth/2fa/login", json={"ticket": t3, "code": codes[0]}).status_code == 401   # recovery codes work once
        # a password-reset link doesn't get past two-factor
        tok = users.create_reset("gil")
        r = TestClient(app).post(f"/api/auth/reset/{tok}", json={"password": "gil-new-pass-1"}).json()
        assert r == {"ok": True, "sign_in": True}
        # policy: everyone must have it -> a user without it is limited to /api/auth until they set it up
        users.create("hal", "hal-pass-1234")
        hal = TestClient(app)
        hal.post("/api/auth/login", json={"username": "hal", "password": "hal-pass-1234"})
        s = client.get("/api/settings").json(); s.pop("_meta"); s["security"]["require_2fa"] = "everyone"
        assert client.put("/api/settings", json=s).status_code == 200
        r = hal.get("/api/labs")
        assert r.status_code == 403 and "2fa-setup-required" in r.json()["detail"]
        assert hal.get("/api/auth/state").json()["user"]["must_setup_2fa"] is True
        g4 = TestClient(app)
        t4 = g4.post("/api/auth/login", json={"username": "gil", "password": "gil-new-pass-1"}).json()["ticket"]
        assert g4.post("/api/auth/2fa/login", json={"ticket": t4, "code": codes[1]}).json()["ok"]
        assert g4.post("/api/auth/2fa/disable", json={"password": "gil-new-pass-1"}).status_code == 403
        assert client.get("/api/labs").status_code == 403          # the admin (no 2FA yet) is held at setup too
        # admins can remove it (lost phone)
        cur = st.load(); cur["security"]["require_2fa"] = "off"; st.save(cur)
        assert client.patch("/api/admin/users/gil", json={"reset_2fa": True}).status_code == 200
        assert not users.has_2fa("gil")
        acts = {r["action"] for r in client.get("/api/admin/audit").json()}
        assert {"2fa.enable", "2fa.fail", "2fa.reset"} <= acts
    finally:
        cur = st.load(); cur["security"]["require_2fa"] = "auto"; st.save(cur)
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear()


class _Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.server.got.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(n).decode()})
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture
def hook():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    srv.got = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def test_alert_channels(client, hook):
    from app import channels, settings as st
    base = f"http://127.0.0.1:{hook.server_address[1]}"
    s = client.get("/api/settings").json(); s.pop("_meta")
    s["channels"]["ntfy_server"] = base
    s["channels"]["items"] = [
        {"id": "push", "name": "Phone", "type": "ntfy", "url": "lab-alerts-x7", "token": "tk", "min_severity": "critical", "quiet": {"start": "22:00", "end": "07:00", "critical": True}},
        {"id": "team", "name": "Teams", "type": "teams", "url": f"{base}/teams", "min_severity": "warning", "resolved": False},
        {"id": "slack", "name": "Slack", "type": "slack", "url": f"{base}/slack", "min_severity": "warning", "quiet": {"start": "00:00", "end": "23:59", "critical": False}},
        {"id": "hook", "name": "JSON", "type": "webhook", "url": f"{base}/hook", "min_severity": "info", "labs": ["only-this"]}]
    r = client.put("/api/settings", json=s)
    assert r.status_code == 200, r.text
    pub = {c["id"]: c for c in r.json()["channels"]["items"]}
    assert pub["push"]["token"] == "" and pub["push"]["token_set"]
    assert {c["id"]: c for c in st.load()["channels"]["items"]}["push"]["token"] == "tk"
    s = client.get("/api/settings").json(); s.pop("_meta"); client.put("/api/settings", json=s)        # blank token kept
    assert {c["id"]: c for c in st.load()["channels"]["items"]}["push"]["token"] == "tk"
    # routing
    noon = time.mktime(time.strptime("2026-11-02 12:00", "%Y-%m-%d %H:%M"))
    crit = {"kind": "fired", "severity": "critical", "rule": "Device stopped", "rule_id": "r-container", "lab": "lab1", "node": "r1", "message": "r1 stopped", "ts": noon}
    asyncio.run(channels.route(crit, now=noon))
    paths = sorted(g["path"] for g in hook.got)
    assert paths == ["/lab-alerts-x7", "/teams"]                 # slack: quiet all day for non-critical? critical blocked (critical False); hook: other lab only
    ntfy = next(g for g in hook.got if g["path"] == "/lab-alerts-x7")
    assert ntfy["headers"]["Priority"] == "urgent" and ntfy["headers"]["Authorization"] == "Bearer tk" and "r1 stopped" in ntfy["body"]
    card = json.loads(next(g for g in hook.got if g["path"] == "/teams")["body"])
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    hook.got.clear()
    asyncio.run(channels.route({**crit, "kind": "resolved", "lab": "only-this", "severity": "warning", "duration": 75}, now=noon))
    assert sorted(g["path"] for g in hook.got) == ["/hook"]       # teams: no resolved; ntfy: critical only; slack: quiet
    assert json.loads(hook.got[0]["body"])["event"] == "resolved"
    # per-rule routing
    hook.got.clear()
    s = client.get("/api/settings").json(); s.pop("_meta")
    s["alerts"]["rules"][0]["channels"] = ["team"]; client.put("/api/settings", json=s)
    asyncio.run(channels.route(crit, now=noon))
    assert [g["path"] for g in hook.got] == ["/teams"]
    # quiet hours wrap midnight
    assert channels._quiet({"start": "22:00", "end": "07:00"}, "UTC", time.mktime(time.gmtime(0)) - time.timezone + 23 * 3600)
    # test button
    hook.got.clear()
    assert client.post("/api/admin/channels/test", json={"id": "team"}).json()["ok"]
    assert "Test from Patchyard" in hook.got[0]["body"]
    # rate limit: the 21st alert in 10 minutes is held back
    hook.got.clear(); channels._SENT.clear(); channels._HELD.clear()
    for i in range(25):
        asyncio.run(channels.route({**crit, "message": f"m{i}"}, now=noon + i))
    assert len(hook.got) == 20
    s["channels"]["items"] = []; s["alerts"]["rules"][0]["channels"] = []; client.put("/api/settings", json=s)


def test_leaderboard_and_new_builtins(client):
    from app import challenges, labs, netinfo
    for cid, node in (("vlan-fix", "sw1"), ("nat-edge", "r1"), ("bgp-peer", "r4")):
        d = client.post(f"/api/challenges/builtin/{cid}/start", json={"name": f"t-{cid}"}).json()
        assert d["challenge"]["origin"] == f"builtin/{cid}" and d["challenge"]["started"]
    sw = next(n for n in labs.load("t-vlan-fix")["nodes"] if n["id"] == "sw1")["props"]
    assert sw["vlans"] == [{"id": 1, "name": "default"}] and not sw.get("ports")
    r1 = next(n for n in labs.load("t-nat-edge")["nodes"] if n["id"] == "r1")["props"]
    assert not r1.get("nat") and not r1.get("acls")
    assert "router bgp" not in (labs.lab_dir("t-bgp-peer") / "configs" / "r4" / "frr.conf").read_text()
    assert "router bgp" in (labs.lab_dir("t-bgp-peer") / "configs" / "r1" / "frr.conf").read_text()

    # score the lab with every check "passing", as if the network were right
    async def fake_one(lab, k, ctrs):
        return True, "ok"
    async def none():
        return {}
    orig1, orig2 = challenges._one, netinfo.containers
    challenges._one, netinfo.containers = fake_one, none
    try:
        r = client.post("/api/labs/t-vlan-fix/challenge/check").json()
        assert r["pct"] == 100 and r["leaderboard"] is True
        lb = client.get("/api/challenges/builtin/vlan-fix/leaderboard").json()
        assert lb[0]["best"] == 100 and lb[0]["completed"] and lb[0]["minutes"] is not None
        assert client.get("/api/challenges").json()["scores"]["builtin/vlan-fix"]["completed"] == 1
        # an edited copy can't post scores
        lab = client.get("/api/labs/t-nat-edge").json()
        lab["challenge"]["checks"] = [{"type": "ping", "node": "pc1", "target": "1.1.1.1"}]
        client.put("/api/labs/t-nat-edge", json=lab)
        assert client.post("/api/labs/t-nat-edge/challenge/check").json()["leaderboard"] is False
        assert client.get("/api/challenges/builtin/nat-edge/leaderboard").json() == []
    finally:
        challenges._one, netinfo.containers = orig1, orig2
        challenges.SCORES.unlink(missing_ok=True)
        for n in ("t-vlan-fix", "t-nat-edge", "t-bgp-peer"):
            labs.delete(n)
