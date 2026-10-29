"""netlab API + static web UI."""
import asyncio, logging, re, secrets
from contextlib import asynccontextmanager
from pathlib import Path
from .env import env
from fastapi import FastAPI, HTTPException, WebSocket, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import labs, devices, metrics, monitor, impair, labmgmt, templates, settings, alerts, live, traffic, auth, hardware, reach, cfgstate, l3, system, users, sandbox, proxy
from .version import VERSION, COMMIT
from .capture import capture_for
from .console import console
from .util import run, check_name, container_states, cname

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Patchyard", version=VERSION)
app.add_middleware(auth.AuthMiddleware)
LOCKS: dict[str, asyncio.Lock] = {}


def lock(lab):
    return LOCKS.setdefault(lab, asyncio.Lock())


@app.exception_handler(ValueError)
async def _ve(_: Request, e: ValueError):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(FileNotFoundError)
async def _fnf(_: Request, e: FileNotFoundError):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(FileExistsError)
async def _fe(_: Request, e: FileExistsError):
    return JSONResponse({"detail": str(e)}, status_code=409)


@asynccontextmanager
async def lifespan(_app):
    for m in ("mpls_router", "mpls_iptunnel", "vrf", "8021q", "bridge", "sch_netem", "dummy", "openvswitch"):
        await run("modprobe", m)
    tasks = []
    if env("METRICS", "1") != "0":
        alerts.restore_active()
        tasks = [asyncio.create_task(metrics.collector()), asyncio.create_task(alerts.health_loop())]
        tasks.append(asyncio.create_task(_restore_l3()))
        await proxy.start()
        fw = await sandbox.refresh_firewall()
        if fw:
            logging.getLogger("patchyard").error("lab firewall: %s", fw)
    yield
    await proxy.stop()
    for t in tasks:
        t.cancel()


async def _restore_l3():
    """DHCP relays run from the app; after an app restart, start them again for running labs."""
    await asyncio.sleep(5)
    for item in labs.list_labs():
        try:
            await l3.reapply_all(labs.load(item["name"]))
        except Exception as e:  # noqa: BLE001 - one bad lab mustn't stop the others
            logging.getLogger("netlab").warning("l3 restore %s: %s", item["name"], e)


app.router.lifespan_context = lifespan


def node_of(lab: dict, nid: str) -> dict:
    n = next((n for n in lab["nodes"] if n["id"] == nid), None)
    if not n:
        raise HTTPException(404, f"device '{nid}' not in lab")
    return n


# ---------------- labs ----------------
def _visible(items):
    me = auth.current()
    fa = auth.first_admin()
    for i in items:
        i["owner"] = i.get("owner") or fa
    return items if me["role"] == "admin" else [i for i in items if i["owner"] == me["name"]]


def my_labs() -> set | None:
    """Labs the caller may see; None = all (admins)."""
    if auth.is_admin():
        return None
    return {i["name"] for i in _visible(labs.list_labs())}


def quota_new_lab(owner: str | None = None):
    owner = owner or auth.current()["name"]
    q = users.quota(owner)
    if q["max_labs"]:
        mine = sum(1 for i in _visible(labs.list_labs()) if i["owner"] == owner)
        if mine >= q["max_labs"]:
            raise HTTPException(403, f"you have {mine} labs, the limit is {q['max_labs']}: delete one first")


def quota_devices(lab: dict):
    q = users.quota(lab.get("owner") or auth.first_admin())
    if q["max_devices"] and len(lab.get("nodes", [])) > q["max_devices"]:
        raise HTTPException(403, f"a lab can have at most {q['max_devices']} devices on this server")


async def quota_running(lab: dict):
    owner = lab.get("owner") or auth.first_admin()
    q = users.quota(owner)
    if not q["max_running_labs"]:
        return
    from . import netinfo
    running = {l for (l, _n), c in (await netinfo.containers()).items() if c["state"] == "running"}
    mine = {i["name"] for i in labs.list_labs() if (i.get("owner") or auth.first_admin()) == owner}
    busy = (running & mine) - {lab["name"]}
    if len(busy) >= q["max_running_labs"]:
        raise HTTPException(403, f"you can run {q['max_running_labs']} lab(s) at a time: destroy {', '.join(sorted(busy))} first")


def own_new(lab_name: str, owner: str | None = None):
    """Stamp the owner on a lab that was just created."""
    d = labs.load(lab_name)
    d["owner"] = owner or auth.current()["name"]
    labs.save(d)
    return d


@app.get("/api/labs")
async def api_list():
    return _visible(labs.list_labs())


@app.post("/api/labs")
async def api_create(body: dict):
    quota_new_lab()
    labs.create(check_name(body["name"]))
    return own_new(body["name"])


@app.post("/api/labs/import")
async def api_import(body: dict):
    d = labs.import_clab(body["path"])
    return own_new(d["name"])


@app.get("/api/labs/{lab}")
async def api_get(lab: str):
    return labs.load(lab)


@app.put("/api/labs/{lab}")
async def api_put(lab: str, body: dict):
    if body.get("name") != lab:
        raise HTTPException(400, "lab name mismatch")
    old = labs.load(lab)
    for k in ("workdir", "imported_from", "raw_kinds", "mgmt_network", "mgmt_subnet", "template", "cloned_from",
              "owner", "trusted", "sandbox_subnet"):
        body.pop(k, None)
        if k in old:
            body[k] = old[k]
    if not auth.is_admin():
        _check_user_lab(body, old)
    quota_devices(body)
    # props changed on the server (IOS CLI, power) after the copy the browser edited win
    seen = int(body.pop("_rev", 0) or 0)
    newer = {n for (l, n), r in labs.NODE_REV.items() if l == lab and r > seen}
    if newer:
        oldp = {n["id"]: n.get("props", {}) for n in old["nodes"]}
        for n in body.get("nodes", []):
            if n["id"] in newer and n["id"] in oldp:
                n["props"] = oldp[n["id"]]
    labs.save(body)
    return {"ok": True, **labs.rev_info(lab)}


def _check_user_lab(new: dict, old: dict | None):
    """What a normal user may not do to a lab: change imported (raw) device definitions or pick other images."""
    images = sandbox.allowed_images()
    oldraw = {n["id"]: n.get("raw") for n in (old or {}).get("nodes", [])}
    for n in new.get("nodes", []):
        if n.get("raw") and n.get("raw") != oldraw.get(n["id"]):
            try:
                sandbox.check_raw(n["raw"], n["id"], images)
            except ValueError as e:
                raise HTTPException(403, str(e))
        img = (n.get("props") or {}).get("image")
        if img and img not in images:
            raise HTTPException(403, f"{n['id']}: image {img} isn't on this server's allowed list")


@app.delete("/api/labs/{lab}")
async def api_delete(lab: str):
    states = await container_states(lab)
    if states:
        raise HTTPException(409, "destroy the lab before deleting it")
    labs.delete(lab)
    return {"ok": True}


@app.post("/api/labs/{lab}/deploy")
async def api_deploy(lab: str):
    async with lock(lab):
        d = labs.load(lab)
        saved = await devices.save_configs(d)
        await capture_for(lab).stop()
        if settings.get("snapshots.auto_before_deploy") and (labs.workdir(d) / "configs").is_dir():
            labmgmt.snapshot(d, "auto: before deploy", auto=True)
            labmgmt.prune_auto(lab, int(settings.get("snapshots.keep_auto", 20)))
        alerts.LAB_SEEN.pop(lab, None)          # restart the alert grace period
        for n in d["nodes"]:                    # a deploy powers everything on
            if (n.get("props") or {}).get("power"):
                labs.patch_props(lab, n["id"], lambda pr: pr.pop("power", None))
        d = labs.load(lab)
        quota_devices(d)
        await quota_running(d)
        d = labs.ensure_subnet(d)
        if sandbox.active(d):
            bad = [b for b in labs.bridges(d) if not b.startswith(labs.bridge_prefix(d))]
            if bad:
                raise HTTPException(400, f"host bridges must be named {labs.bridge_prefix(d)}… on this server: {', '.join(bad)}")
            fw = await sandbox.refresh_firewall()
            if fw:
                raise HTTPException(500, f"lab firewall: {fw}")
        p = labs.generate(d)
        for br in labs.bridges(d):
            rc, _, _ = await run("ip", "link", "show", br)
            if rc:
                await run("ip", "link", "add", br, "type", "bridge")
            await run("ip", "link", "set", br, "up")
            rc, _, _ = await run("iptables", "-C", "DOCKER-USER", "-i", br, "-j", "ACCEPT")
            if rc:
                await run("iptables", "-I", "DOCKER-USER", "-i", br, "-j", "ACCEPT")
        rc, out, err = await run("containerlab", "deploy", "-t", str(p), "--reconfigure",
                                 cwd=str(labs.workdir(d)), timeout=900)
        notes = await impair.reapply_all(d) if rc == 0 else []
        if rc == 0:
            notes += await sandbox.fixups(d)
            notes += await l3.reapply_all(d)
        tail = ("\n" + "\n".join(notes)) if notes else ""
        return {"ok": rc == 0, "saved_configs": saved["saved"], "save_failed": saved["failed"], "output": (out + err)[-8000:] + tail}


@app.post("/api/labs/{lab}/destroy")
async def api_destroy(lab: str):
    async with lock(lab):
        d = labs.load(lab)
        saved = await devices.save_configs(d)
        await capture_for(lab).stop()
        p = labs.topo_path(d)
        if not p.exists():
            labs.generate(d)
        rc, out, err = await run("containerlab", "destroy", "-t", str(p), "--cleanup",
                                 cwd=str(labs.workdir(d)), timeout=600)
        l3.stop_lab(lab)
        if sandbox.enabled():
            await run("docker", "network", "rm", f"py-{lab}")
        for n in d["nodes"]:                    # nothing is "powered off" in a destroyed lab
            if (n.get("props") or {}).get("power"):
                labs.patch_props(lab, n["id"], lambda pr: pr.pop("power", None))
        return {"ok": rc == 0, "saved_configs": saved["saved"], "save_failed": saved["failed"], "output": (out + err)[-8000:]}


@app.get("/api/labs/{lab}/status")
async def api_status(lab: str):
    d = labs.load(lab)
    st = await devices.link_status_fast(d)
    st["capturing"] = capture_for(lab).running
    st["power_off"] = [n["id"] for n in d["nodes"] if (n.get("props") or {}).get("power") == "off"]
    st.update(labs.rev_info(lab))
    return st


@app.post("/api/labs/{lab}/save-configs")
async def api_save_configs(lab: str):
    return await devices.save_configs(labs.load(lab))


@app.post("/api/labs/{lab}/pause")
async def api_pause(lab: str):
    names = [cname(lab, n) for n, s in (await container_states(lab)).items() if s == "running"]
    if names:
        await run("docker", "pause", *names)
    return {"paused": len(names)}


@app.post("/api/labs/{lab}/resume")
async def api_resume(lab: str):
    names = [cname(lab, n) for n, s in (await container_states(lab)).items() if s == "paused"]
    if names:
        await run("docker", "unpause", *names)
    return {"resumed": len(names)}


# ---------------- devices ----------------
@app.get("/api/labs/{lab}/nodes/{node}/interfaces")
async def api_ifaces(lab: str, node: str):
    return await devices.interfaces(check_name(lab), check_name(node))


@app.get("/api/labs/{lab}/nodes/{node}/running-config")
async def api_running(lab: str, node: str):
    return {"config": await devices.running_config(check_name(lab), check_name(node))}


@app.post("/api/labs/{lab}/nodes/{node}/config")
async def api_config(lab: str, node: str, body: dict):
    return await devices.apply_config(check_name(lab), check_name(node), body.get("commands", []))


@app.post("/api/labs/{lab}/nodes/{node}/ipconfig")
async def api_ipconfig(lab: str, node: str, body: dict):
    d = labs.load(lab)
    n = node_of(d, node)
    props = n.setdefault("props", {})
    props.update({"ip_mode": body.get("mode", "static"), "ip": body.get("ip", ""),
                  "gateway": body.get("gateway", ""), "dns": body.get("dns", "")})
    labs.save(d)
    return await devices.ipconfig(lab, node, body)


@app.post("/api/labs/{lab}/nodes/{node}/services")
async def api_services(lab: str, node: str, body: dict):
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] != "server":
        raise HTTPException(400, "services are only available on servers")
    if n.get("raw"):
        raise HTTPException(400, "imported device: edit its service files in the lab folder")
    n.setdefault("props", {})["services"] = body
    labs.save(d)
    return await devices.apply_services(d, n)


@app.post("/api/labs/{lab}/nodes/{node}/switch")
async def api_switch(lab: str, node: str, body: dict):
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] != "switch" or n.get("raw"):
        raise HTTPException(400, "VLAN config is only available on Patchyard switches")
    props = n.setdefault("props", {})
    prio = int(body.get("stp_priority") or 32768)
    if not 0 <= prio <= 61440 or prio % 4096:
        raise HTTPException(400, "STP priority must be 0-61440 in steps of 4096")
    props.update({"ports": body.get("ports", {}), "stp": bool(body.get("stp")), "stp_priority": prio,
                  "vlans": body.get("vlans", []), "svi": body.get("svi", {})})
    labs.switch_script(n)            # validate before saving
    labs.save(d)
    if (await container_states(lab)).get(node) != "running":
        labs.write_node_files(d, n)
        return {"ok": True, "output": "Saved. Applied at the next deploy."}
    return await devices.apply_switch(d, n)


@app.get("/api/labs/{lab}/nodes/{node}/switch")
async def api_switch_show(lab: str, node: str):
    return await devices.switch_show(check_name(lab), check_name(node))


@app.post("/api/labs/{lab}/nodes/{node}/subif")
async def api_subif(lab: str, node: str, body: dict):
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] != "router" or n.get("raw"):
        raise HTTPException(400, "subinterfaces are only available on Patchyard routers")
    parent, vlan, add = body["parent"], labs.vlan_id(body["vlan"]), body.get("action", "add") == "add"
    hit = next((s for s in n.setdefault("props", {}).get("subifs", []) if s["parent"] == parent and int(s["vlan"]) == vlan), None)
    name = labs.subif_name(hit or {"parent": parent, "vlan": vlan})
    subs = [s for s in n.setdefault("props", {}).get("subifs", []) if not (s["parent"] == parent and int(s["vlan"]) == vlan)]
    if add:
        subs.append({"parent": parent, "vlan": vlan})
    n["props"]["subifs"] = subs
    labs.router_subif_exec(n["props"])  # validate
    labs.save(d)
    if (await container_states(lab)).get(node) != "running":
        return {"ok": True, "output": "Saved. Created at the next deploy."}
    return await devices.subif(lab, node, parent, vlan, add, name)


@app.post("/api/labs/{lab}/nodes/{node}/browse")
async def api_browse(lab: str, node: str, body: dict):
    return await devices.browse(check_name(lab), check_name(node), body.get("url", ""))


@app.post("/api/labs/{lab}/nodes/{node}/ping")
async def api_ping(lab: str, node: str, body: dict):
    return await devices.ping(check_name(lab), check_name(node), body["dst"], int(body.get("count", 1)))


@app.websocket("/ws/console/{lab}/{node}")
async def ws_console(ws: WebSocket, lab: str, node: str, mode: str = "sh"):
    if mode == "ios":
        from . import ios
        return await ios.serve(ws, lab, node)
    await console(ws, lab, node, mode)


# ---------------- simulation ----------------
@app.post("/api/labs/{lab}/sim/start")
async def sim_start(lab: str):
    n = await capture_for(lab).start(labs.load(lab))
    return {"links": n}


@app.post("/api/labs/{lab}/sim/stop")
async def sim_stop(lab: str):
    await capture_for(lab).stop()
    return {"ok": True}


@app.post("/api/labs/{lab}/sim/clear")
async def sim_clear(lab: str):
    capture_for(lab).clear()
    return {"ok": True}


@app.get("/api/labs/{lab}/sim/events")
async def sim_events(lab: str, after: int = 0):
    c = capture_for(lab)
    return {"running": c.running, "events": c.list(after)}


@app.get("/api/labs/{lab}/sim/events/{eid}")
async def sim_event(lab: str, eid: int):
    e = capture_for(lab).get(eid)
    if not e:
        raise HTTPException(404, "event no longer in buffer")
    return e


@app.post("/api/labs/{lab}/sim/pdu")
async def sim_pdu(lab: str, body: dict):
    return await devices.ping(check_name(lab), check_name(body["src"]), body["dst"], 1)


# ---------------- monitoring ----------------
@app.get("/api/labs/{lab}/metrics/live")
async def api_live(lab: str):
    return metrics.live_for(labs.load(lab))


@app.get("/api/labs/{lab}/metrics/history")
async def api_history(lab: str, node: str, iface: str | None = None, range: int = 3600):
    check_name(lab); check_name(node)
    if iface:
        devices.check_if(iface)
    return await metrics.history(lab, node, iface, range)


@app.get("/api/labs/{lab}/nodes/{node}/protocols")
async def api_protocols(lab: str, node: str, which: str = ""):
    return await monitor.protocols(check_name(lab), check_name(node), [w for w in which.split(",") if w] or None)


@app.get("/api/labs/{lab}/health")
async def api_health(lab: str):
    return await monitor.health(labs.load(lab), await container_states(lab))


# ---------------- link fault injection ----------------
def link_of(lab: dict, lid: str) -> dict:
    l = next((l for l in lab["links"] if l["id"] == lid), None)
    if not l:
        raise HTTPException(404, f"link '{lid}' not in lab")
    return l


@app.get("/api/impair/presets")
async def api_presets():
    return impair.PRESETS


@app.post("/api/labs/{lab}/links/{lid}/impair")
async def api_impair(lab: str, lid: str, body: dict):
    d = labs.load(lab); l = link_of(d, lid)
    cfg = body.get("impair")
    c = impair.clean(cfg or {})
    has = any(k in c for k in impair.FIELDS)
    l["impair"] = c if has else None
    if not has:
        l.pop("impair", None)
    labs.save(d)
    if not (await container_states(lab)):
        return {"ok": True, "output": "Saved. Applied at the next deploy.", "impair": l.get("impair")}
    return await impair.apply(d, l, c if has else None)


@app.post("/api/labs/{lab}/links/{lid}/state")
async def api_link_state(lab: str, lid: str, body: dict):
    d = labs.load(lab); l = link_of(d, lid)
    up = bool(body.get("up", True))
    if up:
        l.pop("admin", None)
    else:
        l["admin"] = "down"
    labs.save(d)
    if not (await container_states(lab)):
        return {"ok": True, "output": "Saved. Applied at the next deploy."}
    return await impair.set_state(d, l, up)


@app.post("/api/labs/{lab}/links/{lid}/flap")
async def api_link_flap(lab: str, lid: str, body: dict):
    d = labs.load(lab); l = link_of(d, lid)
    secs = float(body.get("seconds", 10))
    if not 1 <= secs <= 600:
        raise HTTPException(400, "flap time must be 1-600 s")
    impair.flap(d, l, secs)
    return {"ok": True, "output": f"{l['a']} {l['a_if']} - {l['b']} {l['b_if']} down for {secs:g} s"}


@app.get("/api/labs/{lab}/links/{lid}/tc")
async def api_link_tc(lab: str, lid: str):
    d = labs.load(lab)
    return await impair.show(d, link_of(d, lid))


# ---------------- lab management ----------------
@app.get("/api/templates")
async def api_templates():
    return templates.list_templates()


@app.post("/api/templates/{tid}")
async def api_template_create(tid: str, body: dict):
    quota_new_lab()
    me = auth.current()["name"]
    d = await asyncio.to_thread(templates.create, tid, check_name(body.get("name", "")))
    d = own_new(d["name"], me)
    quota_devices(d)
    return d


@app.get("/api/labs/{lab}/snapshots")
async def api_snaps(lab: str):
    labs.load(lab)
    return labmgmt.list_snapshots(lab)


@app.post("/api/labs/{lab}/snapshots")
async def api_snap_take(lab: str, body: dict):
    d = labs.load(lab)
    saved = await devices.save_configs(d)
    snap = labmgmt.snapshot(d, body.get("label", ""))
    return {"snapshot": snap, "saved_configs": saved}


@app.get("/api/labs/{lab}/snapshots/{sid}/diff")
async def api_snap_diff(lab: str, sid: str, against: str = "", live: int = 1):
    d = labs.load(lab)
    if live and not against:
        await devices.save_configs(d)
    return labmgmt.diff(d, sid, against or None)


@app.post("/api/labs/{lab}/snapshots/{sid}/restore")
async def api_snap_restore(lab: str, sid: str):
    async with lock(lab):
        d = labs.load(lab)
        await devices.save_configs(d)
        safety = labmgmt.snapshot(d, f"before restore of {sid}", auto=True)
        r = labmgmt.restore(d, sid)
        r["safety_snapshot"] = safety
        r["running"] = bool(await container_states(lab))
        return r


@app.delete("/api/labs/{lab}/snapshots/{sid}")
async def api_snap_delete(lab: str, sid: str):
    labmgmt.delete_snapshot(check_name(lab), sid)
    return {"ok": True}


@app.post("/api/labs/{lab}/clone")
async def api_clone(lab: str, body: dict):
    quota_new_lab()
    d = labs.load(lab)
    await devices.save_configs(d)
    c = labmgmt.clone(d, check_name(body.get("name", "")))
    nd = labs.load(c["lab"]["name"])
    nd.pop("sandbox_subnet", None)
    if not auth.is_admin():
        nd.pop("trusted", None)
    labs.save(nd)
    c["lab"] = own_new(nd["name"])
    return c


@app.get("/api/labs/{lab}/export")
async def api_export(lab: str):
    d = labs.load(lab)
    await devices.save_configs(d)
    data = labmgmt.export_zip(d)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="patchyard-{lab}.zip"'})


@app.post("/api/labs/import-zip")
async def api_import_zip(name: str, request: Request):
    quota_new_lab()
    d = labmgmt.import_zip(check_name(name), await request.body())
    if not auth.is_admin():
        try:
            for k in ("trusted", "workdir", "imported_from"):
                d.pop(k, None)
            _check_user_lab(d, None)
            quota_devices(d)
        except HTTPException:
            labs.delete(d["name"])
            raise
    d.pop("sandbox_subnet", None)
    labs.save(d)
    return own_new(d["name"])


# ---------------- version, settings, auth ----------------
@app.get("/api/version")
async def api_version():
    return {"version": VERSION, "commit": COMMIT, "name": "Patchyard"}


@app.get("/api/system")
async def api_system():
    caps = await system.capabilities()
    if not auth.is_admin():            # users only need to know which features work
        caps = {"features": caps["features"]}
    caps["sandbox"] = sandbox.enabled()
    return caps


@app.get("/api/system/update")
async def api_update(force: bool = False):
    return await system.update_check(force)


@app.get("/api/settings")
async def api_settings():
    s = settings.public()
    s["_meta"] = {"rule_types": settings.RULE_TYPES, "severities": settings.SEVERITIES, "data": str(settings.DATA), "version": VERSION,
                  "admin": auth.is_admin()}
    if not auth.is_admin():          # users see what the UI needs, not the server's internals
        s = {k: v for k, v in s.items() if k in ("general", "lab_defaults", "notifications", "_meta")}
        s["_meta"]["data"] = ""
    return s


@app.put("/api/settings")
async def api_settings_put(body: dict):
    body.pop("_meta", None)
    was = settings.get("sandbox.enabled")
    r = settings.save(body)
    if settings.get("sandbox.enabled") != was:
        await (proxy.start() if settings.get("sandbox.enabled") else proxy.stop())
        await sandbox.refresh_firewall()
    return r


@app.get("/api/auth/state")
async def api_auth_state(request: Request):
    who = auth.session_user(request.cookies.get(auth.COOKIE)) if auth.enabled() else auth.current()
    if who:
        u = users.get(who["name"]) or {}
        who = {**who, "full_name": u.get("full_name", ""), "email": u.get("email", "")}
    return {"enabled": auth.enabled(), "logged_in": who is not None, "user": who,
            "multi_user": len(users.all_users()) > 1, "sandbox": sandbox.enabled(),
            "requests": auth.enabled() and bool(settings.get("users.allow_requests", True))}


def _set_cookie(resp: JSONResponse, request: Request, name: str):
    tok, age = auth.make_session(name)
    resp.set_cookie(auth.COOKIE, tok, max_age=age, httponly=True, samesite="lax", path="/",
                    secure=request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https")
    return resp


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers already swaps in X-Forwarded-For when the request came from the local proxy
    return request.client.host if request.client else ""


@app.post("/api/auth/login")
async def api_login(body: dict, request: Request):
    name = str(body.get("username") or "").strip().lower() or auth.first_admin()
    ip = _client_ip(request)
    if auth.throttled(f"ip:{ip}", limit=20) or auth.throttled(f"user:{name}"):
        raise HTTPException(429, "too many failed logins: wait 15 minutes")
    u = await asyncio.to_thread(users.verify, name, str(body.get("password", "")))
    if not u:
        auth.failed(f"ip:{ip}", f"user:{name}")
        await asyncio.sleep(1.0)
        st = await asyncio.to_thread(users.request_status, name, str(body.get("password", "")))
        if st == "pending":
            raise HTTPException(403, "your account request is waiting for an admin to approve it")
        if st == "declined":
            raise HTTPException(403, "your account request was declined")
        raise HTTPException(401, "wrong username or password")
    users.touch_login(name)
    return _set_cookie(JSONResponse({"ok": True, "user": {"name": name, "role": u.get("role", "user")}}), request, name)


@app.post("/api/auth/logout")
async def api_logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE, path="/")
    return r


@app.post("/api/auth/password")
async def api_password(body: dict, request: Request):
    """Change your own password (or, on an install with no accounts yet, create the admin)."""
    if not users.any_users():
        u = users.create(str(body.get("username") or "admin"), str(body.get("new", "")), role="admin")
        return _set_cookie(JSONResponse({"ok": True, "enabled": True}), request, u["name"])
    who = auth.signed_in()
    if not who:
        raise HTTPException(401, "login required")
    me = who["name"]
    if not await asyncio.to_thread(users.verify, me, str(body.get("current", ""))):
        raise HTTPException(403, "current password is wrong")
    users.set_password(me, str(body.get("new", "")))
    return _set_cookie(JSONResponse({"ok": True, "enabled": True}), request, me)


@app.patch("/api/auth/profile")
async def api_profile(body: dict):
    """Change your own name and email."""
    who = auth.signed_in()
    if not who or not users.get(who["name"]):
        raise HTTPException(401, "login required")
    u = users.update(who["name"], **{k: body[k] for k in ("full_name", "email") if k in body})
    return {"ok": True, "full_name": u["full_name"], "email": u["email"]}


@app.post("/api/auth/request")
async def api_account_request(body: dict, request: Request):
    """Ask for an account from the sign-in page; admins approve it in Admin → Requests."""
    if not auth.enabled() or not settings.get("users.allow_requests", True):
        raise HTTPException(404, "account requests are turned off on this server")
    ip = _client_ip(request)
    if auth.throttled(f"req:{ip}", limit=3, window=3600):
        raise HTTPException(429, "too many requests from your address: try again in an hour")
    auth.failed(f"req:{ip}")                     # every attempt counts towards the limit
    if str(body.get("website") or ""):           # hidden field: only bots fill it in
        return {"ok": True}
    if not body.get("agree"):
        raise HTTPException(400, "please accept the terms of use")
    await asyncio.to_thread(users.request_account, str(body.get("username", "")), str(body.get("password", "")),
                            str(body.get("full_name", "")), str(body.get("email", "")), str(body.get("org", "")),
                            str(body.get("reason", "")), ip)
    return {"ok": True}


@app.get("/api/invite/{token}")
async def api_invite_check(token: str):
    i = users.check_invite(token)
    return {"ok": True, "role": i["role"], "expires": i["expires"]}


@app.post("/api/invite/{token}")
async def api_invite_redeem(token: str, body: dict, request: Request):
    ip = _client_ip(request)
    if auth.throttled(f"inv:{ip}", limit=10):
        raise HTTPException(429, "too many attempts: wait 15 minutes")
    try:
        u = users.redeem(token, str(body.get("username", "")), str(body.get("password", "")),
                         full_name=str(body.get("full_name", "")), email=str(body.get("email", "")))
    except users.UserError:
        auth.failed(f"inv:{ip}")
        raise
    users.touch_login(u["name"])
    return _set_cookie(JSONResponse({"ok": True, "user": {"name": u["name"], "role": u["role"]}}), request, u["name"])


# ---------------- admin: users, invites, all labs ----------------
@app.get("/api/admin/users")
async def api_admin_users():
    from . import netinfo
    ls = _visible(labs.list_labs())
    running = {l for (l, _n), c in (await netinfo.containers()).items() if c["state"] == "running"}
    out = []
    for n, u in users.all_users().items():
        mine = [l["name"] for l in ls if l["owner"] == n]
        out.append({**users.public(n, u), "labs": len(mine), "running": sorted(set(mine) & running), "effective_quota": users.quota(n)})
    return out


@app.post("/api/admin/users")
async def api_admin_user_create(body: dict):
    pw = str(body.get("password") or "") or secrets.token_urlsafe(12)
    u = users.create(str(body.get("username", "")), pw, role=str(body.get("role") or "user"), internet=bool(body.get("internet")),
                     full_name=str(body.get("full_name") or ""), email=str(body.get("email") or ""))
    return {**u, "password": pw}


@app.patch("/api/admin/users/{name}")
async def api_admin_user_update(name: str, body: dict):
    kw = {k: body[k] for k in ("role", "disabled", "internet", "quota", "full_name", "email") if k in body}
    if name == auth.current()["name"] and (kw.get("disabled") or kw.get("role") == "user"):
        raise HTTPException(400, "you can't disable or demote yourself")
    u = users.update(name, **kw)
    if body.get("reset_password"):
        pw = secrets.token_urlsafe(12)
        users.set_password(name, pw)
        u["password"] = pw
    if "internet" in kw or "role" in kw:
        await sandbox.refresh_firewall()
    return u


@app.delete("/api/admin/users/{name}")
async def api_admin_user_delete(name: str, delete_labs: bool = False):
    if name == auth.current()["name"]:
        raise HTTPException(400, "you can't delete yourself")
    owned = [l["name"] for l in _visible(labs.list_labs()) if l["owner"] == name]
    if owned and not delete_labs:
        raise HTTPException(409, f"{name} owns {len(owned)} lab(s): {', '.join(owned)}. Delete them too, or move them first.")
    for l in owned:
        if await container_states(l):
            await api_destroy(l)
        labs.delete(l)
    users.delete(name)
    return {"ok": True, "deleted_labs": owned}


@app.post("/api/admin/labs/{lab}/owner")
async def api_admin_lab_owner(lab: str, body: dict):
    to = str(body.get("owner", ""))
    if not users.get(to):
        raise HTTPException(404, f"no user {to}")
    d = labs.load(lab)
    d["owner"] = to
    if "trusted" in body:
        d["trusted"] = bool(body["trusted"])
    labs.save(d)
    await sandbox.refresh_firewall()
    return {"ok": True}


@app.get("/api/admin/requests")
async def api_admin_requests():
    return users.list_requests()


@app.post("/api/admin/requests/{rid}/approve")
async def api_admin_request_approve(rid: str, body: dict | None = None):
    u = users.approve_request(rid, auth.current()["name"], internet=bool((body or {}).get("internet")))
    if u.get("internet"):
        await sandbox.refresh_firewall()
    return u


@app.post("/api/admin/requests/{rid}/decline")
async def api_admin_request_decline(rid: str):
    users.decline_request(rid, auth.current()["name"])
    return {"ok": True}


@app.get("/api/admin/invites")
async def api_admin_invites():
    return users.list_invites()


@app.post("/api/admin/invites")
async def api_admin_invite_create(body: dict):
    return users.create_invite(auth.current()["name"], role=str(body.get("role") or "user"),
                               days=float(body.get("days") or 7), note=str(body.get("note") or ""))


@app.delete("/api/admin/invites/{iid}")
async def api_admin_invite_delete(iid: str):
    users.revoke_invite(iid)
    return {"ok": True}


# ---------------- alerts ----------------
@app.get("/api/alerts")
async def api_alerts(state: str = "", limit: int = 200):
    return await asyncio.to_thread(alerts.list_alerts, state, max(1, min(limit, 1000)), my_labs())


@app.get("/api/alerts/summary")
async def api_alert_summary(after: int = 0):
    only = my_labs()
    s = await asyncio.to_thread(alerts.summary, only)
    s["events"] = alerts.events_after(after, only)
    if auth.is_admin() and auth.enabled():
        s["requests"] = users.pending_count()
    return s


@app.post("/api/alerts/ack")
async def api_alert_ack(body: dict):
    await asyncio.to_thread(alerts.ack, int(body["id"]) if body.get("id") is not None else None, my_labs())
    return {"ok": True}


@app.delete("/api/alerts/resolved")
async def api_alert_clear():
    return {"deleted": await asyncio.to_thread(alerts.clear_resolved, my_labs())}


@app.post("/api/labs/{lab}/nodes/{node}/rename-files")
async def api_rename_files(lab: str, node: str, body: dict):
    """A renamed device keeps its saved config: move configs/<old> to configs/<new>."""
    d = labs.load(lab)
    old, new = check_name(node), check_name(body.get("to", ""))
    if (await container_states(lab)).get(old) or (await container_states(lab)).get(new):
        raise HTTPException(409, "destroy the lab before renaming devices")
    src, dst = labs.workdir(d) / "configs" / old, labs.workdir(d) / "configs" / new
    if not src.is_dir():
        return {"ok": True, "moved": False}
    if dst.exists():
        raise HTTPException(409, f"configs/{new} already exists")
    src.rename(dst)
    f = dst / "frr.conf"
    if f.exists():
        f.write_text(re.sub(rf"^hostname {re.escape(old)}$", f"hostname {new}", f.read_text(), flags=re.M))
    return {"ok": True, "moved": True}


@app.post("/api/labs/{lab}/nodes/{node}/firewall")
async def api_firewall(lab: str, node: str, body: dict):
    from . import firewall
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] != "firewall" or n.get("raw"):
        raise HTTPException(400, "not a Patchyard firewall")
    keep = {k: v for k, v in (n.get("props") or {}).items() if k not in firewall.DEFAULT}
    n["props"] = {**keep, **firewall.normalise(body)}
    labs.save(d)
    labs.REV[lab] = labs.REV.get(lab, 0) + 1
    labs.NODE_REV[(lab, node)] = labs.REV[lab]
    if (await container_states(lab)).get(node) != "running":
        labs.write_node_files(d, n)
        return {"ok": True, "output": "Saved. Applied at the next deploy.", "props": n["props"]}
    r = await devices.apply_firewall(d, n)
    return {**r, "props": n["props"]}


@app.get("/api/labs/{lab}/nodes/{node}/firewall")
async def api_firewall_status(lab: str, node: str):
    d = labs.load(lab)
    return await devices.firewall_status(d, node_of(d, node))


@app.get("/api/labs/{lab}/nodes/{node}/server/syslog")
async def api_server_syslog(lab: str, node: str, lines: int = 300):
    d = labs.load(lab)
    return {"lines": devices.syslog_tail(d, node_of(d, node), max(10, min(2000, lines)))}


@app.delete("/api/labs/{lab}/nodes/{node}/server/syslog")
async def api_server_syslog_clear(lab: str, node: str):
    d = labs.load(lab)
    node_of(d, node)
    f = labs.workdir(d) / "configs" / node / "syslog" / "syslog.log"
    if f.exists():
        labs.write_inplace(f, "")
    return {"ok": True}


SERVER_DIRS = ("tftp", "ftp")
FILE_RE = re.compile(r"[\w.@+-]{1,128}")


def _server_file(lab: str, node: str, kind: str, name: str | None = None):
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] != "server" or kind not in SERVER_DIRS:
        raise HTTPException(404, "no such file area")
    base = labs.workdir(d) / "configs" / node / kind
    if name is None:
        return d, n, base
    if not FILE_RE.fullmatch(name) or name.startswith("."):
        raise HTTPException(400, "bad file name (letters, digits, . _ - @ +)")
    return d, n, base / name


@app.get("/api/labs/{lab}/nodes/{node}/server/{kind}")
async def api_server_files(lab: str, node: str, kind: str):
    d, n, _ = _server_file(lab, node, kind)
    return devices.server_files(d, n, kind)


@app.get("/api/labs/{lab}/nodes/{node}/server/{kind}/{name}")
async def api_server_file_get(lab: str, node: str, kind: str, name: str):
    _, _, f = _server_file(lab, node, kind, name)
    if not f.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(f, filename=name, media_type="application/octet-stream")


@app.put("/api/labs/{lab}/nodes/{node}/server/{kind}/{name}")
async def api_server_file_put(lab: str, node: str, kind: str, name: str, request: Request):
    _, _, f = _server_file(lab, node, kind, name)
    data = await request.body()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "files up to 20 MB")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    f.chmod(0o666)
    return {"ok": True, "size": len(data)}


@app.delete("/api/labs/{lab}/nodes/{node}/server/{kind}/{name}")
async def api_server_file_delete(lab: str, node: str, kind: str, name: str):
    _, _, f = _server_file(lab, node, kind, name)
    if f.is_file():
        f.unlink()
    return {"ok": True}


@app.get("/api/labs/{lab}/nodes/{node}/server-status")
async def api_server_status(lab: str, node: str):
    d = labs.load(lab)
    n = node_of(d, node)
    return {**(await devices.services_status(d, n)), "leases": devices.dhcp_leases(d, n)}


@app.get("/api/labs/{lab}/nodes/{node}/state")
async def api_node_state(lab: str, node: str):
    d = labs.load(lab)
    return await cfgstate.state(d, node_of(d, node))


@app.post("/api/labs/{lab}/nodes/{node}/ios")
async def api_node_ios(lab: str, node: str, body: dict):
    """Apply Config-tab forms as IOS commands (same engine as the CLI tab)."""
    from . import ios
    d = labs.load(lab)
    n = node_of(d, node)
    if n["type"] not in ("router", "switch") or n.get("raw"):
        raise HTTPException(400, "IOS commands are for Patchyard routers and switches")
    cmds = [str(c) for c in body.get("commands", [])][:400]
    if n["type"] == "router" and (await container_states(lab)).get(node) != "running":
        raise HTTPException(409, "deploy the lab (and power the router on) to change its configuration")
    return await ios.run_batch(ios.Env(lab, node), cmds)


@app.post("/api/labs/{lab}/reachability")
async def api_reachability(lab: str):
    return await reach.check(labs.load(lab))


# ---------------- hardware ----------------
@app.get("/api/hardware")
async def api_hardware():
    return hardware.catalogue()


@app.post("/api/labs/{lab}/nodes/{node}/power")
async def api_power(lab: str, node: str, body: dict):
    async with lock(lab):
        d = labs.load(lab)
        node_of(d, node)
        if not await container_states(lab):
            raise HTTPException(400, "deploy the lab first")
        return await live.power(d, check_name(node), bool(body.get("on")))


# ---------------- live editing ----------------
@app.post("/api/labs/{lab}/live/nodes/{node}")
async def api_live_add_node(lab: str, node: str):
    async with lock(lab):
        return await live.add_node(labs.load(lab), check_name(node))


@app.delete("/api/labs/{lab}/live/nodes/{node}")
async def api_live_del_node(lab: str, node: str):
    return await live.remove_node(check_name(lab), check_name(node))


@app.post("/api/labs/{lab}/live/links/{lid}")
async def api_live_add_link(lab: str, lid: str):
    d = labs.load(lab)
    return await live.add_link(d, link_of(d, lid))


@app.post("/api/labs/{lab}/live/unlink")
async def api_live_del_link(lab: str, body: dict):
    d = labs.load(lab)
    return await live.remove_link(d, check_name(body["a"]), body["a_if"], check_name(body["b"]), body["b_if"])


# ---------------- traffic generator ----------------
@app.post("/api/labs/{lab}/traffic")
async def api_traffic_start(lab: str, body: dict):
    return await traffic.start(labs.load(lab), body)


@app.get("/api/labs/{lab}/traffic")
async def api_traffic_list(lab: str):
    return traffic.list_jobs(check_name(lab))


@app.post("/api/labs/{lab}/traffic/{jid}/stop")
async def api_traffic_stop(lab: str, jid: str):
    return traffic.stop(jid)


# ---------------- static UI ----------------
UI = Path(env("UI", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if UI.exists():
    app.mount("/assets", StaticFiles(directory=UI / "assets"), name="assets")

    UI_ROOT = UI.resolve()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"])
    async def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404, "no such API route")
        f = (UI_ROOT / path).resolve()
        ok = path and f.is_file() and f.is_relative_to(UI_ROOT)  # block ../ traversal
        return FileResponse(f if ok else UI_ROOT / "index.html")
