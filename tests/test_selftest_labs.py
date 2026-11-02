"""The self-test's own labs must pass validation (it runs on real servers, where a typo costs a release)."""


def test_isp_selftest_lab_is_valid(client):
    from app import selftest, isp, labs
    client.post("/api/labs", json={"name": "t-st-isp"})
    lab = client.get("/api/labs/t-st-isp").json()
    lab.update(selftest.ISP_LAB)
    lab["challenge"] = {"title": "self-test", "instructions": "", "feedback": "full", "checks": selftest.ISP_CHECKS}
    r = client.put("/api/labs/t-st-isp", json=lab)
    assert r.status_code == 200, r.text
    saved = labs.load("t-st-isp")
    assert len(saved["challenge"]["checks"]) == len(selftest.ISP_CHECKS)
    plan = client.get("/api/labs/t-st-isp/nodes/isp1/isp").json()
    pc = next(p for p in plan["ports"] if p["peer"] == "pc1")
    r1 = next(p for p in plan["ports"] if p["peer"] == "r1")
    assert pc["mode"] == "dhcp" and pc["net"] == "81.10.0.8/29" and r1["if"] == "eth2"
    assert "ip route 0.0.0.0 0.0.0.0 81.10.0.17" in r1["help"]["ios"] and r1["help"]["ios"][0] == "interface Gi0/0/0"
    assert plan["transit"][0]["peer"] == "inet1"
    client.delete("/api/labs/t-st-isp")
