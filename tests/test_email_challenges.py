"""0.13: SMTP email (settings, test, account emails, password reset), audit log, disk quota, challenges."""
import asyncio, re, socket, socketserver, threading, time
import pytest


class _SMTP(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib (no auth, no TLS)."""
    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 test ESMTP")
        mail = {"to": [], "data": ""}
        while True:
            line = self.rfile.readline().decode(errors="replace").strip()
            if not line:
                return
            cmd = line.upper()
            if cmd.startswith(("EHLO", "HELO")):
                w("250-test"); w("250 SIZE 10000000")
            elif cmd.startswith("MAIL FROM"):
                mail["from"] = line; w("250 ok")
            elif cmd.startswith("RCPT TO"):
                mail["to"].append(re.search(r"<(.*)>", line).group(1)); w("250 ok")
            elif cmd == "DATA":
                w("354 go")
                buf = []
                while True:
                    l = self.rfile.readline().decode(errors="replace")
                    if l.rstrip("\r\n") == ".":
                        break
                    buf.append(l)
                mail["data"] = "".join(buf)
                self.server.box.append(dict(mail))
                mail = {"to": [], "data": ""}
                w("250 queued")
            elif cmd == "QUIT":
                w("221 bye"); return
            else:
                w("250 ok")


@pytest.fixture
def smtp():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    srv.daemon_threads = True
    srv.box = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _wait(box, n, t=5):
    end = time.time() + t
    while len(box) < n and time.time() < end:
        time.sleep(0.05)
    return box


def _body(m):
    import email, email.policy
    msg = email.message_from_string(m["data"], policy=email.policy.default)
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            return part.get_content()
    return ""


def test_email_flow(client, smtp):
    from app import users, auth, settings as st
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear()
    try:
        assert client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"}).status_code == 200
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        client.patch("/api/auth/profile", json={"email": "boss@example.com", "full_name": "The Boss"})
        anon = TestClient(app)
        assert anon.get("/api/auth/state").json()["forgot"] is False          # email not set up yet
        s = client.get("/api/settings").json(); s.pop("_meta")
        s["email"].update(enabled=True, host="127.0.0.1", port=smtp.server_address[1], security="none", username="",
                          password="sekret", from_addr="lab@example.com", site_url="https://lab.example.com")
        r = client.put("/api/settings", json=s)
        assert r.status_code == 200, r.text
        pub = r.json()["email"]
        assert pub["password"] == "" and pub["password_set"] and not pub["working"]
        assert st.load()["email"]["password"] == "sekret"
        # saving again with the blank password keeps it
        s = client.get("/api/settings").json(); s.pop("_meta")
        client.put("/api/settings", json=s)
        assert st.load()["email"]["password"] == "sekret"
        # the test button marks it working
        r = client.post("/api/admin/email/test", json={"to": "boss@example.com"}).json()
        assert r["ok"], r
        assert r["settings"]["working"] and _wait(smtp.box, 1)[0]["to"] == ["boss@example.com"]
        assert "outgoing email works" in _body(smtp.box[0])
        # changing the server makes it "not tested" again
        s = client.get("/api/settings").json(); s.pop("_meta"); s["email"]["from_name"] = "Lab"; s["email"]["port"] = 2
        assert client.put("/api/settings", json=s).json()["email"]["working"] is False
        s["email"]["port"] = smtp.server_address[1]; client.put("/api/settings", json=s)
        assert client.post("/api/admin/email/test", json={"to": "boss@example.com"}).json()["ok"]
        # a failing server reports why
        bad = socket.socket(); bad.bind(("127.0.0.1", 0)); port = bad.getsockname()[1]; bad.close()
        s["email"]["port"] = port; client.put("/api/settings", json=s)
        r = client.post("/api/admin/email/test", json={"to": "boss@example.com"}).json()
        assert not r["ok"] and "connect" in r["error"]
        s["email"]["port"] = smtp.server_address[1]; client.put("/api/settings", json=s)
        smtp.box.clear()
        # account request -> admins emailed; approval -> requester emailed
        anon.post("/api/auth/request", json={"username": "dave", "password": "dave-pass-123", "full_name": "Dave Smith",
                                             "email": "dave@example.com", "reason": "Studying for the CCNA", "agree": True})
        m = _wait(smtp.box, 1)[0]
        assert m["to"] == ["boss@example.com"] and "Dave Smith" in _body(m)
        rid = client.get("/api/admin/requests").json()[0]["id"]
        client.post(f"/api/admin/requests/{rid}/approve", json={})
        m = _wait(smtp.box, 2)[1]
        assert m["to"] == ["dave@example.com"] and "approved" in _body(m)
        # forgot password: same answer for unknown accounts, a link for known ones
        assert anon.get("/api/auth/state").json()["forgot"] is True
        assert anon.post("/api/auth/forgot", json={"login": "nobody"}).json() == {"ok": True}
        assert anon.post("/api/auth/forgot", json={"login": "DAVE@example.com"}).json() == {"ok": True}
        m = _wait(smtp.box, 3)[2]
        link = re.search(r"https://lab\.example\.com/reset/(\S+)", _body(m)).group(1)
        assert anon.get(f"/api/auth/reset/{link}").json()["user"] == "dave"
        assert anon.post(f"/api/auth/reset/{link}", json={"password": "short"}).status_code == 400
        r = anon.post(f"/api/auth/reset/{link}", json={"password": "new-dave-pass-1"})
        assert r.status_code == 200 and r.json()["user"]["name"] == "dave"
        assert anon.post(f"/api/auth/reset/{link}", json={"password": "again-dave-pass"}).status_code == 400    # single use
        assert users.verify("dave", "new-dave-pass-1")
        # invite by email
        inv = client.post("/api/admin/invites", json={"email": "erin@example.com", "days": 2}).json()
        assert inv["emailed"] is True
        m = _wait(smtp.box, 4)[3]
        assert m["to"] == ["erin@example.com"] and f"/invite/{inv['token']}" in _body(m)
        # admin sends a reset link
        assert client.post("/api/admin/users/dave/send-reset").json()["ok"]
        assert "/reset/" in _body(_wait(smtp.box, 5)[4])
        # audit log saw it all
        acts = {r["action"] for r in client.get("/api/admin/audit").json()}
        assert {"login", "email.test", "request.new", "request.approve", "password.reset", "invite.create", "user.send_reset"} <= acts
        assert anon.get("/api/admin/audit").status_code == 403
    finally:
        s = client.get("/api/settings").json(); s.pop("_meta", None); s["email"]["enabled"] = False; s["email"]["password_clear"] = True
        client.put("/api/settings", json=s)
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear()


def test_challenge_normalise_and_builtin(client):
    from app import challenges, labs
    with pytest.raises(ValueError):
        challenges.normalise({"checks": [{"type": "ios_show", "node": "r1", "command": "conf t", "match": "x"}]}, set())
    with pytest.raises(ValueError):
        challenges.normalise({"checks": [{"type": "nope", "node": "r1"}]}, set())
    c = challenges.normalise({"title": "", "checks": [{"type": "route", "node": "r1", "prefix": "192.168.3.7/24", "points": 500},
                                                      {"type": "ping", "node": "pc1", "target": "8.8.8.8", "expect": "blocked"}]}, {"r1"})
    assert c["title"] == "Challenge" and c["checks"][0]["prefix"] == "192.168.3.0/24" and c["checks"][0]["points"] == 100
    assert "cannot ping 8.8.8.8" in challenges.describe(c["checks"][1])
    # the built-ins start from their templates with the task left undone
    ids = {x["id"] for x in client.get("/api/challenges").json()["builtin"]}
    assert ids == {"ospf-finish", "office-dhcp"}
    d = client.post("/api/challenges/builtin/ospf-finish/start", json={"name": "t-ch1"}).json()
    assert d["challenge"]["checks"] and d["owner"]
    r3 = (labs.lab_dir("t-ch1") / "configs" / "r3" / "frr.conf").read_text()
    assert "router ospf" not in r3 and "10.0.13.2/30" in r3
    assert "router ospf" in (labs.lab_dir("t-ch1") / "configs" / "r1" / "frr.conf").read_text()
    d = client.post("/api/challenges/builtin/office-dhcp/start", json={"name": "t-ch2"}).json()
    srv = next(n for n in d["nodes"] if n["id"] == "srv1")
    assert srv["props"]["services"]["dhcp"]["enabled"] is False
    # nothing deployed: every check fails with a reason, score 0
    async def none(): return {}
    from app import netinfo
    orig = netinfo.containers
    netinfo.containers = none
    try:
        r = client.post("/api/labs/t-ch1/challenge/check").json()
    finally:
        netinfo.containers = orig
    assert r["pct"] == 0 and all("isn't running" in x["detail"] for x in r["results"])
    assert client.get("/api/labs/t-ch1/challenge/result").json()["max"] == r["max"]
    # the challenge survives a PUT from the editor, and a bad check is refused
    lab = client.get("/api/labs/t-ch1").json()
    lab["challenge"]["checks"].append({"type": "service", "node": "srv1", "service": "http"})
    assert client.put("/api/labs/t-ch1", json=lab).status_code == 200
    assert len(client.get("/api/labs/t-ch1").json()["challenge"]["checks"]) == 7
    lab["challenge"]["checks"].append({"type": "ios_show", "node": "r1", "command": "reload", "match": "x"})
    assert client.put("/api/labs/t-ch1", json=lab).status_code == 400
    # publish to everyone, start a copy, unpublish
    m = client.post("/api/admin/challenges", json={"lab": "t-ch1", "desc": "OSPF practice"}).json()
    assert any(p["id"] == m["id"] for p in client.get("/api/challenges").json()["published"])
    d = client.post(f"/api/challenges/published/{m['id']}/start", json={"name": "t-ch3"}).json()
    assert d["challenge"]["title"] == "Finish the OSPF triangle"
    assert "router ospf" not in (labs.lab_dir("t-ch3") / "configs" / "r3" / "frr.conf").read_text()
    client.delete(f"/api/admin/challenges/{m['id']}")
    assert not client.get("/api/challenges").json()["published"]
    for n in ("t-ch1", "t-ch2", "t-ch3"):
        client.delete(f"/api/labs/{n}")


def test_disk_quota_and_caches(client, tmp):
    from app import users, auth, notify, labs, netinfo, util
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear()
    try:
        client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        users.create("fay", "fay-pass-1234")
        fay = TestClient(app)
        fay.post("/api/auth/login", json={"username": "fay", "password": "fay-pass-1234"})
        assert fay.post("/api/labs", json={"name": "t-fay"}).status_code == 200
        client.patch("/api/admin/users/fay", json={"quota": {"max_disk_mb": 1}})
        (labs.lab_dir("t-fay") / "configs").mkdir(parents=True, exist_ok=True)
        (labs.lab_dir("t-fay") / "configs" / "big.bin").write_bytes(b"x" * (1024 * 1024 + 10))
        notify._SIZE.clear()
        r = fay.post("/api/labs/t-fay/snapshots", json={"label": "x"})
        assert r.status_code == 403 and "1 MB" in r.json()["detail"]
        assert next(u for u in client.get("/api/admin/users").json() if u["name"] == "fay")["disk_mb"] >= 1
        # oversized uploads are refused before they're read
        r = fay.post("/api/labs/import-zip?name=t-big", content=b"x" * 10, headers={"content-length": str(60 * 1024 * 1024)})
        assert r.status_code in (403, 413)
    finally:
        labs.delete("t-fay")
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear()
    # docker ps is cached and shared, and a container change invalidates it
    calls = []

    async def fake_run(*a, **k):
        calls.append(a)
        return 0, "", ""
    orig = netinfo.run
    netinfo.run = fake_run
    try:
        async def go():
            netinfo.invalidate()
            await asyncio.gather(*(netinfo.containers() for _ in range(10)))
            await netinfo.containers()
            n1 = len(calls)
            util.GEN[0] += 1
            await netinfo.containers()
            return n1, len(calls)
        n1, n2 = asyncio.run(go())
    finally:
        netinfo.run = orig
    assert n1 == 1 and n2 == 2
