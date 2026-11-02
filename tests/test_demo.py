"""Demo sign-in: one throwaway account per visitor, its own labs, deleted on sign-out, idle or time-up."""
import asyncio, time


def test_demo_sessions(client, monkeypatch):
    from app import users, auth, watch, labs, sandbox, settings as st
    from fastapi.testclient import TestClient
    from app.main import app, _end_demo
    auth._FAILS.clear()
    try:
        client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
        client.post("/api/auth/login", json={"username": "boss", "password": "admin-pass-123"})
        anon = TestClient(app)
        assert anon.get("/api/auth/state").json()["demo"] is None
        assert anon.post("/api/auth/demo").status_code == 404                    # off by default
        s = client.get("/api/settings").json(); s.pop("_meta")
        s["demo"].update(enabled=True, password="tryme", max_sessions=2)
        s["security"]["require_2fa"] = "off"            # the test pretends to be a sandboxed (public) server below
        assert client.put("/api/settings", json=s).status_code == 200
        monkeypatch.setattr(sandbox, "enabled", lambda: False)                  # never offered without the sandbox
        assert anon.get("/api/auth/state").json()["demo"] is None and anon.post("/api/auth/demo").status_code == 403
        monkeypatch.setattr(sandbox, "enabled", lambda: True)
        d = anon.get("/api/auth/state").json()["demo"]
        assert d["username"] == "demo" and d["password"] == "tryme" and not d["full"]

        # two visitors sign in with the same demo login at once: each gets an account of their own
        a, b = TestClient(app), TestClient(app)
        ra = a.post("/api/auth/login", json={"username": "demo", "password": "tryme"}).json()
        rb = b.post("/api/auth/demo").json()
        na, nb = ra["user"]["name"], rb["user"]["name"]
        assert na != nb and na.startswith("demo-") and ra["user"]["demo"]
        me = a.get("/api/auth/state").json()["user"]
        assert me["demo"] and me["demo_ends"] > time.time() and not me["must_setup_2fa"]
        # third one: full
        assert TestClient(app).post("/api/auth/demo").status_code == 503
        assert anon.get("/api/auth/state").json()["demo"]["full"]
        # wrong demo password is just a failed login
        assert TestClient(app).post("/api/auth/login", json={"username": "demo", "password": "nope"}).status_code == 401

        # labs are separate, demo limits apply, account changes are refused
        assert a.post("/api/labs", json={"name": f"{na}-x"}).status_code == 200
        assert b.get(f"/api/labs/{na}-x").status_code == 404
        assert users.quota(na)["max_devices"] == 8
        for m, u, body in (("post", "/api/auth/password", {"old": "x", "new": "y" * 12}), ("patch", "/api/auth/profile", {"full_name": "Eve"}),
                           ("post", "/api/auth/tokens", {"label": "t"}), ("post", "/api/auth/2fa/setup", {}), ("post", "/api/import/ios", {"name": "zz", "configs": []}),
                           ("get", "/api/admin/users", None)):
            r = getattr(a, m)(u, json=body) if body is not None else getattr(a, m)(u)
            assert r.status_code == 403, (u, r.status_code)

        # sign-out throws the account and its labs away
        a.post("/api/auth/logout")
        for _ in range(50):
            if not users.get(na):
                break
            time.sleep(0.1)
        assert not users.get(na) and not (labs.lab_dir(f"{na}-x") / "lab.json").exists()
        assert a.get("/api/labs").status_code == 401

        # idle: the sweep ends sessions nobody has used
        r = client.get("/api/admin/demo"); assert r.status_code == 200 and r.json()[0]["name"] == nb, r.text
        auth.DEMO_SEEN[nb] = time.time() - 3600
        asyncio.run(watch.sweep_demo(_end_demo))
        assert not users.get(nb) and b.get("/api/labs").status_code == 401
        audit = client.get("/api/admin/audit").json()
        rows = audit if isinstance(audit, list) else audit.get("rows", [])
        assert any(r.get("action") == "demo.end" for r in rows)
    finally:
        cur = st.load(); cur["demo"]["enabled"] = False; cur["security"]["require_2fa"] = "auto"; st.save(cur)
        users.FILE.unlink(missing_ok=True)
        client.cookies.clear()
        auth._FAILS.clear()
