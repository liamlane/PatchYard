"""netlab API + static web UI."""
import asyncio, logging, os, re
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import labs, devices, metrics, monitor, impair, labmgmt, templates, settings, alerts, live, traffic, auth, hardware, reach
from .version import VERSION, COMMIT
from .capture import capture_for
from .console import console
from .util import run, check_name, container_states, cname

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="netlab", version=VERSION)
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
    for m in ("mpls_router", "mpls_iptunnel", "vrf", "8021q", "bridge", "sch_netem", "dummy"):
        await run("modprobe", m)
    tasks = []
    if os.environ.get("NETLAB_METRICS", "1") != "0":
        alerts.restore_active()
        tasks = [asyncio.create_task(metrics.collector()), asyncio.create_task(alerts.health_loop())]
    yield
    for t in tasks:
        t.cancel()


app.router.lifespan_context = lifespan


def node_of(lab: dict, nid: str) -> dict:
    n = next((n for n in lab["nodes"] if n["id"] == nid), None)
    if not n:
        raise HTTPException(404, f"device '{nid}' not in lab")
    return n


# ---------------- labs ----------------
@app.get("/api/labs")
async def api_list():
    return labs.list_labs()


@app.post("/api/labs")
async def api_create(body: dict):
    return labs.create(check_name(body["name"]))


@app.post("/api/labs/import")
async def api_import(body: dict):
    return labs.import_clab(body["path"])


@app.get("/api/labs/{lab}")
async def api_get(lab: str):
    return labs.load(lab)


@app.put("/api/labs/{lab}")
async def api_put(lab: str, body: dict):
    if body.get("name") != lab:
        raise HTTPException(400, "lab name mismatch")
    old = labs.load(lab)
    for k in ("workdir", "imported_from", "raw_kinds", "mgmt_network", "mgmt_subnet", "template", "cloned_from"):
        if k in old:
            body[k] = old[k]
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
        raise HTTPException(400, "VLAN config is only available on netlab switches")
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
        raise HTTPException(400, "subinterfaces are only available on netlab routers")
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
    return await asyncio.to_thread(templates.create, tid, check_name(body.get("name", "")))


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
    d = labs.load(lab)
    await devices.save_configs(d)
    return labmgmt.clone(d, check_name(body.get("name", "")))


@app.get("/api/labs/{lab}/export")
async def api_export(lab: str):
    d = labs.load(lab)
    await devices.save_configs(d)
    data = labmgmt.export_zip(d)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="netlab-{lab}.zip"'})


@app.post("/api/labs/import-zip")
async def api_import_zip(name: str, request: Request):
    return labmgmt.import_zip(check_name(name), await request.body())


# ---------------- version, settings, auth ----------------
@app.get("/api/version")
async def api_version():
    return {"version": VERSION, "commit": COMMIT}


@app.get("/api/settings")
async def api_settings():
    s = settings.public()
    s["_meta"] = {"rule_types": settings.RULE_TYPES, "severities": settings.SEVERITIES, "data": str(settings.DATA), "version": VERSION}
    return s


@app.put("/api/settings")
async def api_settings_put(body: dict):
    body.pop("_meta", None)
    return settings.save(body)


@app.get("/api/auth/state")
async def api_auth_state(request: Request):
    return {"enabled": auth.enabled(), "logged_in": auth.valid_session(request.cookies.get(auth.COOKIE)),
            "password_set": bool(settings.load()["security"]["password_hash"])}


def _set_cookie(resp: JSONResponse, request: Request):
    tok, age = auth.make_session()
    resp.set_cookie(auth.COOKIE, tok, max_age=age, httponly=True, samesite="lax",
                    secure=request.headers.get("x-forwarded-proto") == "https")
    return resp


@app.post("/api/auth/login")
async def api_login(body: dict, request: Request):
    stored = settings.load()["security"]["password_hash"]
    if not stored or not auth.check_password(str(body.get("password", "")), stored):
        await asyncio.sleep(1.0)
        raise HTTPException(401, "wrong password")
    return _set_cookie(JSONResponse({"ok": True}), request)


@app.post("/api/auth/logout")
async def api_logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(auth.COOKIE)
    return r


@app.post("/api/auth/password")
async def api_password(body: dict, request: Request):
    """Set or change the admin password and turn login on (or off with enable=false)."""
    cur = settings.load()
    stored = cur["security"]["password_hash"]
    if stored and auth.enabled() and not auth.valid_session(request.cookies.get(auth.COOKIE)):
        raise HTTPException(401, "login required")
    if stored and not auth.check_password(str(body.get("current", "")), stored):
        raise HTTPException(403, "current password is wrong")
    if body.get("enable") is False:
        cur["security"]["auth_enabled"] = False
        return settings.save(cur, keep_secret=False) and {"ok": True, "enabled": False}
    new = str(body.get("new", ""))
    if len(new) < 8:
        raise HTTPException(400, "use at least 8 characters")
    cur["security"]["password_hash"] = auth.hash_password(new)
    cur["security"]["auth_enabled"] = True
    settings.save(cur, keep_secret=False)
    return _set_cookie(JSONResponse({"ok": True, "enabled": True}), request)


# ---------------- alerts ----------------
@app.get("/api/alerts")
async def api_alerts(state: str = "", limit: int = 200):
    return await asyncio.to_thread(alerts.list_alerts, state, max(1, min(limit, 1000)))


@app.get("/api/alerts/summary")
async def api_alert_summary(after: int = 0):
    s = await asyncio.to_thread(alerts.summary)
    s["events"] = alerts.events_after(after)
    return s


@app.post("/api/alerts/ack")
async def api_alert_ack(body: dict):
    await asyncio.to_thread(alerts.ack, int(body["id"]) if body.get("id") is not None else None)
    return {"ok": True}


@app.delete("/api/alerts/resolved")
async def api_alert_clear():
    return {"deleted": await asyncio.to_thread(alerts.clear_resolved)}


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
UI = Path(os.environ.get("NETLAB_UI", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if UI.exists():
    app.mount("/assets", StaticFiles(directory=UI / "assets"), name="assets")

    UI_ROOT = UI.resolve()

    @app.get("/{path:path}")
    async def spa(path: str):
        if path.startswith(("api/", "ws/")):
            raise HTTPException(404, "no such API route")
        f = (UI_ROOT / path).resolve()
        ok = path and f.is_file() and f.is_relative_to(UI_ROOT)  # block ../ traversal
        return FileResponse(f if ok else UI_ROOT / "index.html")
