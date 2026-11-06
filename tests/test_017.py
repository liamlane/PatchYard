"""0.17: shared labs, hand-outs and the class view, showcase links, pcap export, time machine, chaos guard."""
import json, struct, time
import pytest


@pytest.fixture
def three(client):
    from app import users, auth, settings as st
    from fastapi.testclient import TestClient
    from app.main import app
    auth._FAILS.clear()
    cur = st.load(); cur["security"]["require_2fa"] = "off"; st.save(cur)
    client.post("/api/auth/password", json={"username": "boss", "new": "admin-pass-123"})
    users.create("tess", "tess-pass-1234"); users.create("bob", "bob-pass-12345"); users.create("cara", "cara-pass-1234")
    c = {}
    for n, pw in (("tess", "tess-pass-1234"), ("bob", "bob-pass-12345"), ("cara", "cara-pass-1234")):
        c[n] = TestClient(app)
        assert c[n].post("/api/auth/login", json={"username": n, "password": pw}).json()["ok"]
    yield c
    users.FILE.unlink(missing_ok=True)
    cur = st.load(); cur["security"]["require_2fa"] = "auto"; st.save(cur)
    auth._FAILS.clear()


def test_sharing_levels(three):
    t, b, c = three["tess"], three["bob"], three["cara"]
    assert t.post("/api/labs", json={"name": "t-share"}).status_code == 200
    assert b.get("/api/labs/t-share").status_code == 404
    assert t.put("/api/labs/t-share/shares", json={"shares": {"bob": "view", "cara": "edit"}}).json()["ok"]
    assert t.put("/api/labs/t-share/shares", json={"shares": {"nobody": "view"}}).status_code == 400
    mine = {l["name"]: l for l in b.get("/api/labs").json()}
    assert mine["t-share"]["access"] == "view" and "shares" not in mine["t-share"]
    lab = b.get("/api/labs/t-share").json()
    assert b.put("/api/labs/t-share", json=lab).status_code == 403            # view: no changes
    assert b.post("/api/labs/t-share/deploy").status_code == 403
    assert b.post("/api/labs/t-share/explain", json={"src": "x", "target": "y"}).status_code != 403   # read-only tools allowed
    lab = c.get("/api/labs/t-share").json()
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}]
    assert c.put("/api/labs/t-share", json=lab).status_code == 200            # edit: changes
    assert c.put("/api/labs/t-share/shares", json={"shares": {}}).status_code == 403   # but not who it's shared with
    assert c.delete("/api/labs/t-share").status_code == 403
    assert t.get("/api/labs/t-share/shares").json()["shares"] == {"bob": "view", "cara": "edit"}
    # a clone is yours alone
    assert c.post("/api/labs/t-share/clone", json={"name": "cara-copy"}).status_code == 200
    assert c.get("/api/labs/cara-copy/shares").json()["shares"] == {}
    t.delete("/api/labs/t-share"); c.delete("/api/labs/cara-copy")


def test_handout_and_class(three):
    from app import labs
    t, b = three["tess"], three["bob"]
    t.post("/api/labs", json={"name": "ospf-class"})
    lab = t.get("/api/labs/ospf-class").json()
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 0, "y": 0, "props": {}}]
    lab["challenge"] = {"title": "c", "instructions": "", "feedback": "full", "checks": [{"type": "ping", "node": "r1", "target": "1.1.1.1"}]}
    t.put("/api/labs/ospf-class", json=lab)
    r = t.post("/api/labs/ospf-class/handout", json={"users": ["bob", "cara", "ghost"]}).json()
    assert {m["user"] for m in r["made"]} == {"bob", "cara"} and any("ghost" in e for e in r["errors"])
    copy = labs.load("bob-ospf-class")
    assert copy["owner"] == "bob" and copy["shares"] == {"tess": "view"} and copy["handout_from"] == "ospf-class"
    assert {l["name"]: l["access"] for l in b.get("/api/labs").json()}["bob-ospf-class"] == "owner"
    (labs.workdir(copy) / "challenge-result.json").write_text(json.dumps({"ts": time.time(), "pct": 50, "best": 50,
                                                                           "results": [{"label": "a", "ok": True}, {"label": "b", "ok": False}]}))
    cls = t.get("/api/labs/ospf-class/class").json()
    row = next(x for x in cls if x["owner"] == "bob")
    assert row["score"] == 50 and row["passed"] == 1 and row["total"] == 2 and row["failing"] == ["b"] and not row["running"]
    assert t.put("/api/labs/bob-ospf-class", json=labs.load("bob-ospf-class")).status_code == 403   # the teacher watches, doesn't touch
    assert t.post("/api/labs/ospf-class/handout", json={"users": ["bob"]}).json()["errors"]            # already has one
    for n in ("ospf-class", "bob-ospf-class", "cara-ospf-class"):
        labs.delete(n)


def test_showcase_link(three, client):
    from app import labs
    from fastapi.testclient import TestClient
    from app.main import app
    t = three["tess"]
    t.post("/api/labs", json={"name": "t-show"})
    lab = t.get("/api/labs/t-show").json()
    lab["nodes"] = [{"id": "r1", "type": "router", "x": 5, "y": 6, "props": {"enable_secret": "x", "chassis": "router-2s"}}]
    t.put("/api/labs/t-show", json=lab)
    cfg = labs.workdir(labs.load("t-show")) / "configs" / "r1"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "frr.conf").write_text("hostname r1\nrouter bgp 1\n neighbor 10.0.0.2 password s3cret\n!\nsnmp-server community public RO\n")
    tok = t.post("/api/labs/t-show/showcase").json()["token"]
    anon = TestClient(app)
    v = anon.get(f"/api/showcase/{tok}").json()
    assert v["name"] == "t-show" and v["nodes"][0]["props"] == {"chassis": "router-2s"}
    assert "s3cret" not in v["configs"]["r1"] and "<hidden>" in v["configs"]["r1"] and "hostname r1" in v["configs"]["r1"]
    assert anon.get("/api/showcase/" + "x" * 22).status_code == 404
    t.delete("/api/labs/t-show/showcase")
    assert anon.get(f"/api/showcase/{tok}").status_code == 404
    labs.delete("t-show")


def test_pcap_export():
    from app.capture import Capture
    c = Capture("t-pcap")
    frame = bytes(range(60))
    c.events.append({"id": 1, "t": 1700000000.25, "link": "l1", "from": "a", "to": "b", "proto": "ARP", "info": "", "len": 60, "layers": [], "raw": frame})
    c.events.append({"id": 2, "t": 1700000001.5, "link": "l2", "from": "a", "to": "b", "proto": "ARP", "info": "", "len": 60, "layers": [], "raw": frame})
    data = c.pcap()
    magic, vmaj, vmin, _, _, snap, linktype = struct.unpack("<IHHiIII", data[:24])
    assert magic == 0xA1B2C3D4 and (vmaj, vmin) == (2, 4) and linktype == 1
    sec, usec, incl, orig = struct.unpack("<IIII", data[24:40])
    assert sec == 1700000000 and usec == 250000 and incl == orig == 60 and data[40:100] == frame
    assert len(c.pcap("l2")) == 24 + 16 + 60
    assert "raw" not in c.get(1) and "raw" not in c.list(0)[0]


def test_time_machine(tmp_path, monkeypatch):
    from app import history, labs
    lab = {"name": "t-tm"}
    monkeypatch.setattr(labs, "workdir", lambda l: tmp_path)
    states = [
        {"routes": {"r1": {"default:10.0.0.0/24": "connected via"}}, "neighbours": {"r1": {"OSPF 2.2.2.2": "Full"}}, "ifaces": {"r1 eth1": "up"}, "reach": {}},
        {"routes": {"r1": {"default:10.0.0.0/24": "connected via"}}, "neighbours": {"r1": {"OSPF 2.2.2.2": "Full"}}, "ifaces": {"r1 eth1": "up"}, "reach": {}},
        {"routes": {"r1": {}}, "neighbours": {"r1": {"OSPF 2.2.2.2": "Init"}}, "ifaces": {"r1 eth1": "down"}, "reach": {}},
    ]
    it = iter(states)

    async def fake(_lab):
        return json.loads(json.dumps(next(it)))
    monkeypatch.setattr(history, "sample", fake)
    history.LAST.pop("t-tm", None)
    import asyncio
    assert asyncio.run(history.record(lab)) is True
    assert asyncio.run(history.record(lab)) is False          # unchanged: nothing written
    assert asyncio.run(history.record(lab)) is True
    tl = history.timeline(lab)["points"]
    assert len(tl) == 2 and tl[0]["first"]
    assert "1 route lost" in tl[1]["summary"] and "OSPF 2.2.2.2 Full → Init" in tl[1]["summary"] and "down: r1 eth1" in tl[1]["summary"]
    p = history.point(lab, 1)
    assert p["changes"]["routes"][0]["change"] == "lost" and p["changes"]["ifaces"][0]["after"] == "down"
    assert history.at(lab, time.time() + 1) == 1 and history.at(lab, 0) is None


def test_chaos_needs_a_running_lab(three):
    t = three["tess"]
    t.post("/api/labs", json={"name": "t-chaos"})
    assert t.post("/api/labs/t-chaos/chaos", json={"minutes": 5}).status_code == 409
    assert t.get("/api/labs/t-chaos/chaos").json() == {}
    t.delete("/api/labs/t-chaos")
