"""Continuous network tests and idle-lab auto-stop.

Network tests: a lab's challenge checks can run on a timer (Challenge panel → "Run these
checks every N minutes as network tests"). A failing check raises an alert ("Network test
failed: pc1 can reach srv1") that resolves when it passes again, so it goes to the bell,
email digests and alert channels like any other alert.

Idle auto-stop: on a shared server, a running lab nobody has touched for
users.idle_stop_minutes is saved (running configs) and destroyed, and its owner is emailed.
Any API call or console on the lab counts as activity (a browser tab only polls while it's
visible).
"""
import asyncio, logging, time
from . import labs, netinfo, settings, users, auth, challenges, audit

log = logging.getLogger("patchyard.watch")
FAILING: dict[tuple[str, str], dict] = {}            # (lab, check id) -> {"label", "node", "detail"}
LAST_RUN: dict[str, float] = {}
TEST_RULE = {"id": "r-test", "name": "Network test failed", "type": "test_failed", "severity": "warning", "for_s": 0}
ACTIVE: dict[str, float] = {}                         # lab -> last activity
STARTED = time.time()


def touch(lab: str):
    ACTIVE[lab] = time.time()


async def _run_tests(lab: dict, ctrs: dict):
    ch = lab.get("challenge") or {}
    ids = {k.get("id") for k in ch.get("checks") or []}
    for key in [key for key in FAILING if key[0] == lab["name"] and key[1] not in ids]:
        del FAILING[key]                              # a check that was edited out
    for k in ch.get("checks") or []:
        try:
            ok, detail = await asyncio.wait_for(challenges._one(lab, k, ctrs), 40)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, str(e)
        key = (lab["name"], k["id"])
        if ok:
            FAILING.pop(key, None)
        else:
            FAILING[key] = {"label": k.get("label") or challenges.describe(k), "node": k.get("node", ""), "detail": detail}


async def tests_loop():
    while True:
        await asyncio.sleep(30)
        try:
            ctrs = await netinfo.containers()
            running = {l for (l, _n), c in ctrs.items() if c["state"] == "running"}
            for k in [k for k in FAILING if k[0] not in running]:
                del FAILING[k]
            for item in labs.list_labs():
                name = item["name"]
                if name not in running or not item.get("challenge"):
                    for k in [k for k in FAILING if k[0] == name]:
                        del FAILING[k]                # challenge removed
                    continue
                d = labs.load(name)
                every = int((d.get("challenge") or {}).get("watch") or 0)
                if not every:
                    for k in [k for k in FAILING if k[0] == name]:
                        del FAILING[k]
                    continue
                if time.time() - LAST_RUN.get(name, 0) < every * 60:
                    continue
                LAST_RUN[name] = time.time()
                await _run_tests(d, ctrs)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("network tests: %s", e)


def conditions(deployed: dict) -> list[tuple]:
    """Alert conditions for failing network tests (alerts.conditions adds these)."""
    out = []
    for (lab, cid), w in FAILING.items():
        if lab in deployed:
            out.append((f"r-test|{lab}|{cid}", TEST_RULE, lab, w["node"], w["label"], f"network test failed: {w['label']} ({w['detail']})"))
    return out


async def idle_loop(stop):
    """stop(lab_name) -> coroutine that saves configs and destroys the lab."""
    from . import mailer, notify
    while True:
        await asyncio.sleep(120)
        try:
            mins = int(settings.get("users.idle_stop_minutes", 0) or 0)
            if not mins or not users.any_users():
                continue
            ctrs = await netinfo.containers()
            running = {l for (l, _n), c in ctrs.items() if c["state"] in ("running", "paused")}
            fa = auth.first_admin()
            for item in labs.list_labs():
                name = item["name"]
                if name not in running:
                    continue
                owner = item.get("owner") or fa
                u = users.get(owner) or {}
                if u.get("role") == "admin" and not settings.get("users.idle_stop_admins", False):
                    continue
                last = ACTIVE.get(name, STARTED)
                if time.time() - last < mins * 60:
                    continue
                log.info("stopping idle lab %s (%s)", name, owner)
                await stop(name)
                ACTIVE.pop(name, None)
                audit.record("lab.idle_stop", name, f"idle {mins} min", who="system")
                if u.get("email") and mailer.enabled():
                    t, h = mailer.render("Your lab was stopped", [
                        f"{name} hadn't been used for {mins} minutes, so Patchyard saved its running configs and stopped it to free the server for others.",
                        "Press Deploy to start it again: your configs come back with it."],
                        ("Open Patchyard", mailer.site_url()) if mailer.site_url() else None, notify._foot())
                    mailer.send_bg(u["email"], f"Patchyard: {name} stopped (idle)", t, h)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("idle stop: %s", e)


async def demo_loop(end):
    """Demo sessions end when the visitor signs out (main.api_logout), goes idle, or runs out of time."""
    while True:
        await asyncio.sleep(60)
        try:
            await sweep_demo(end)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("demo sweep: %s", e)


async def sweep_demo(end, now: float | None = None):
    now = now or time.time()
    idle = float(settings.get("demo.idle_minutes", 20)) * 60
    life = float(settings.get("demo.session_minutes", 60)) * 60
    for name, u in users.demo_users().items():
        if not settings.get("demo.enabled"):                    # the demo was switched off: end what's left
            await end(name, "demo switched off")
            continue
        seen = auth.DEMO_SEEN.get(name, max(STARTED, u.get("created") or 0))
        if now - (u.get("created") or now) > life + 60:        # the session cookie has expired too
            await end(name, "time up")
        elif now - seen > idle:
            await end(name, "idle")
