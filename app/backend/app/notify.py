"""Who gets told what: emails for accounts and alerts, and the per-user disk watcher.

Emails (all optional, Admin → Email → "Send emails for"):
  requests        new account request -> admins
  accounts        request approved/declined -> the person; invite links; "set your password" links
  password_reset  "Forgot password?" on the sign-in page
  alerts          alert digests to users who opted in (Settings → Account), at most one per 10 minutes each

Disk: lab folders (configs, uploaded files, syslog, snapshots) count towards a user's
max_disk_mb. New data is refused at the limit; a lab that keeps growing past it (a device
writing into a bind-mounted folder) is paused and its owner told.
"""
import asyncio, logging, os, time
from . import mailer, users, labs, auth, audit

log = logging.getLogger("patchyard.notify")


def _on(kind: str) -> bool:
    return mailer.enabled() and bool((mailer.cfg().get("notify") or {}).get(kind, True))


def resets_possible() -> bool:
    return _on("password_reset") and bool(mailer.site_url())


def _foot() -> str:
    u = mailer.site_url()
    return f"Patchyard{' · ' + u if u else ''}"


# ------------------------------------------------------------------ account emails

def request_received(body: dict):
    if not _on("requests"):
        return
    to = mailer.admin_recipients()
    if not to:
        return
    name, user, email = str(body.get("full_name", "")), str(body.get("username", "")), str(body.get("email", ""))
    org, reason = str(body.get("org", "")), str(body.get("reason", ""))
    url = mailer.site_url()
    text, html_body = mailer.render(f"Account request from {name}", [
        f"{name} ({email}) asked for an account as “{user}”" + (f", from {org}." if org else "."),
        f"What for:\n{reason}",
        "Approve or decline it in Admin → Requests."], ("Review the request", url) if url else None, _foot())
    mailer.send_bg(to, f"Patchyard: account request from {name}", text, html_body, reply_to=email if mailer.ADDR_RE.match(email) else None)


def account_decision(username: str, full_name: str, email: str, approved: bool):
    if not email or not _on("accounts"):
        return
    url = mailer.site_url()
    first = (full_name or username).split(" ")[0]
    if approved:
        text, html_body = mailer.render("Your account is ready", [
            f"Hi {first},", f"Your Patchyard account “{username}” has been approved. Sign in with the username and password you chose.",
            "New here? The ? button in the top bar opens the how-to guide."], ("Sign in", url) if url else None, _foot())
        mailer.send_bg(email, "Your Patchyard account is ready", text, html_body)
    else:
        text, html_body = mailer.render("About your account request", [
            f"Hi {first},", "Sorry, your request for a Patchyard account wasn't approved this time.",
            "If you think that's a mistake, reply to the person who runs the server."], None, _foot())
        mailer.send_bg(email, "Your Patchyard account request", text, html_body)


async def send_invite(to: str, inv: dict) -> tuple[bool, str]:
    url = mailer.site_url()
    if not mailer.enabled():
        return False, "email isn't set up or is turned off (Admin → Email); copy the link instead"
    if not url:
        return False, "set the site address in Admin → Email so links point at this server; copy the link instead"
    days = max(1, round((inv["expires"] - time.time()) / 86400))
    who = auth.current()["name"]
    text, html_body = mailer.render("You're invited to Patchyard", [
        f"{who} invited you to a Patchyard network lab server.",
        f"Open the link to pick a username and password. It works once and expires in {days} day{'s' if days != 1 else ''}."],
        ("Create your account", f"{url}/invite/{inv['token']}"), _foot())
    return await mailer.send(to, "You're invited to Patchyard", text, html_body)


async def send_reset(name: str, u: dict, ip: str = ""):
    token = users.create_reset(name)
    first = (u.get("full_name") or name).split(" ")[0]
    text, html_body = mailer.render("Reset your password", [
        f"Hi {first},", f"Someone (hopefully you) asked to reset the password for “{name}”" + (f" from {ip}." if ip else "."),
        "The link works once, for an hour. If you didn't ask, ignore this email: your password hasn't changed."],
        ("Choose a new password", f"{mailer.site_url()}/reset/{token}"), _foot())
    ok, err = await mailer.send(u["email"], "Reset your Patchyard password", text, html_body)
    if not ok:
        log.warning("reset email for %s: %s", name, err)


async def send_welcome_reset(name: str, reset: bool = False) -> tuple[bool, str]:
    """A 'set your password' link for a user an admin created, or a reset link an admin sends."""
    u = users.get(name)
    if not u:
        return False, f"no user {name}"
    if not u.get("email"):
        return False, f"{name} has no email address: add one under Details"
    if not mailer.enabled():
        return False, "email isn't set up or is turned off (Admin → Email)"
    if not mailer.site_url():
        return False, "set the site address in Admin → Email first"
    token = users.create_reset(name)
    first = (u.get("full_name") or name).split(" ")[0]
    if reset:
        title, paras = "Set a new password", [f"Hi {first},", f"An admin sent you a link to set a new password for “{name}”. It works once, for an hour."]
    else:
        title, paras = "Your Patchyard account", [f"Hi {first},", f"An account “{name}” was made for you. Choose your password with the link below (it works once, for an hour)."]
    text, html_body = mailer.render(title, paras, ("Choose a password", f"{mailer.site_url()}/reset/{token}"), _foot())
    return await mailer.send(u["email"], f"Patchyard: {title.lower()}", text, html_body)


def security_notice(name: str, title: str, text: str):
    """Tell someone about a change to their account's security (2FA on/off/reset)."""
    u = users.get(name) or {}
    if not u.get("email") or not mailer.enabled():
        return
    first = (u.get("full_name") or name).split(" ")[0]
    t, h = mailer.render(title, [f"Hi {first},", text], ("Open Patchyard", mailer.site_url()) if mailer.site_url() else None, _foot())
    mailer.send_bg(u["email"], f"Patchyard: {title.lower()}", t, h)


# ------------------------------------------------------------------ alert digests

DIGEST_EVERY = 600
_PENDING: dict[str, list] = {}
_LAST: dict[str, float] = {}


def _recipients(lab: str) -> list[str]:
    """Users who opted in and may see this lab: its owner, and admins."""
    owner = auth.lab_owner(lab)
    out = []
    for n, u in users.all_users().items():
        if u.get("disabled") or not u.get("alert_email") or not u.get("email"):
            continue
        if n == owner or u.get("role") == "admin":
            out.append(n)
    return out


async def _flush(now: float):
    for name, evs in list(_PENDING.items()):
        if not evs or now - _LAST.get(name, 0) < DIGEST_EVERY:
            continue
        u = users.get(name) or {}
        if not u.get("email"):
            _PENDING.pop(name, None)
            continue
        fired = [e for e in evs if e["kind"] == "fired"]
        if not fired:
            _PENDING.pop(name, None)
            continue
        lines = [f"• [{e.get('severity')}] {e.get('lab')}/{e.get('node')}: {e.get('rule')}: {e.get('message')}" for e in fired[:40]]
        more = f"\n…and {len(fired) - 40} more" if len(fired) > 40 else ""
        text, html_body = mailer.render(f"{len(fired)} alert{'s' if len(fired) != 1 else ''} in your labs",
                                        ["\n".join(lines) + more, "You get at most one of these every 10 minutes. Turn them off in Settings → Account."],
                                        ("Open Patchyard", mailer.site_url()) if mailer.site_url() else None, _foot())
        _PENDING.pop(name, None)
        _LAST[name] = now
        await mailer.send(u["email"], f"Patchyard: {len(fired)} alert{'s' if len(fired) != 1 else ''}", text, html_body)


async def alert_digest_loop():
    from . import alerts
    last = alerts.events_after(-1)[-1]["eid"] if alerts.events_after(-1) else 0
    while True:
        await asyncio.sleep(60)
        try:
            evs = alerts.events_after(last)
            if evs:
                last = evs[-1]["eid"]
            if not _on("alerts"):
                _PENDING.clear()
                continue
            for e in evs:
                if e["kind"] != "fired" or e.get("severity") not in ("warning", "critical"):
                    continue
                for n in _recipients(e.get("lab", "")):
                    _PENDING.setdefault(n, []).append(e)
            await _flush(time.time())
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            log.warning("alert digest: %s", e)


# ------------------------------------------------------------------ disk usage

_SIZE: dict[str, tuple[float, int]] = {}      # lab -> (when measured, bytes)
SIZE_TTL = 60


def _dir_size(p) -> int:
    total = 0
    stack = [str(p)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def lab_size(name: str, fresh: bool = False) -> int:
    hit = _SIZE.get(name)
    now = time.time()
    if hit and not fresh and now - hit[0] < SIZE_TTL:
        return hit[1]
    n = _dir_size(labs.lab_dir(name))
    _SIZE[name] = (now, n)
    return n


def disk_used(owner: str, fresh: bool = False) -> int:
    fa = auth.first_admin()
    return sum(lab_size(l["name"], fresh) for l in labs.list_labs() if (l.get("owner") or fa) == owner)


async def disk_watch_loop():
    """Pause labs whose owner is well over their disk allowance (a device filling a shared folder)."""
    from . import netinfo
    from .util import run, cname
    while True:
        await asyncio.sleep(120)
        try:
            if not users.any_users():
                continue
            running = {}
            for (lab, node), c in (await netinfo.containers()).items():
                if c["state"] == "running":
                    running.setdefault(lab, []).append(node)
            owners = {}
            fa = auth.first_admin()
            for l in labs.list_labs():
                if l["name"] in running:
                    owners.setdefault(l.get("owner") or fa, []).append(l["name"])
            for owner, mine in owners.items():
                q = users.quota(owner)
                if not q.get("max_disk_mb"):
                    continue
                used = await asyncio.to_thread(disk_used, owner, True)
                if used <= q["max_disk_mb"] * 1024 * 1024 * 1.1:
                    continue
                for lab in mine:
                    await run("docker", "pause", *[cname(lab, n) for n in running[lab]])
                audit.record("disk.pause", owner, f"{used / 1048576:.0f} MB of {q['max_disk_mb']} MB; paused {', '.join(mine)}", who="system")
                u = users.get(owner) or {}
                if u.get("email") and mailer.enabled():
                    text, html_body = mailer.render("Your labs were paused", [
                        f"Your labs use {used / 1048576:.0f} MB, over your {q['max_disk_mb']} MB allowance, so {', '.join(mine)} "
                        f"{'was' if len(mine) == 1 else 'were'} paused.",
                        "Delete files on lab servers, old snapshots or labs you no longer need, then press Resume network."], None, _foot())
                    mailer.send_bg(u["email"], "Patchyard: labs paused (disk limit)", text, html_body)
        except Exception as e:  # noqa: BLE001
            log.warning("disk watch: %s", e)
