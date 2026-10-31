"""Alert channels: ntfy phone push, Slack, Microsoft Teams and generic JSON webhooks.

Admins define channels (Admin → Alert channels): each has a minimum severity, optional lab
filter, whether to send "resolved" messages, and quiet hours (critical alerts can still
break through). Alert rules can name the channels they go to; a rule with none goes to
every channel. Users may also get their own labs' alerts on their phone through ntfy, on
the server's ntfy server, when an admin allows it (Settings → Account → Phone).

Delivery runs in the background, one message per alert, at most 20 per channel per
10 minutes (then a single "N more" summary), so a flapping lab can't flood anyone.
"""
import asyncio, json, logging, time, urllib.request, urllib.error
from datetime import datetime
from . import settings, users, auth

log = logging.getLogger("patchyard.channels")
RANK = {"info": 0, "warning": 1, "critical": 2}
NTFY_PRIO = {"info": "default", "warning": "high", "critical": "urgent"}
WINDOW, BURST = 600, 20
_SENT: dict[str, list[float]] = {}
_HELD: dict[str, int] = {}
STATUS: dict[str, dict] = {}       # channel id -> {"ok", "at", "error"}


def _quiet(q: dict, tz: str, now: float | None = None) -> bool:
    if not q.get("start") or not q.get("end"):
        return False
    from zoneinfo import ZoneInfo
    t = datetime.fromtimestamp(now or time.time(), ZoneInfo(tz)).strftime("%H:%M")
    a, b = q["start"], q["end"]
    return (a <= t < b) if a < b else (t >= a or t < b)       # 22:00-07:00 wraps midnight


def wants(ch: dict, e: dict, rule_channels: list[str] | None, tz: str, now: float | None = None) -> bool:
    if not ch.get("enabled", True):
        return False
    if rule_channels and ch["id"] not in rule_channels:
        return False
    if RANK.get(e.get("severity"), 0) < RANK.get(ch.get("min_severity", "warning"), 1):
        return False
    if e.get("kind") == "resolved" and not ch.get("resolved", True):
        return False
    if ch.get("labs") and e.get("lab") not in ch["labs"]:
        return False
    if _quiet(ch.get("quiet") or {}, tz, now) and not (e.get("severity") == "critical" and (ch.get("quiet") or {}).get("critical", True)):
        return False
    return True


def _text(e: dict) -> tuple[str, str]:
    if e.get("kind") == "resolved":
        d = e.get("duration")
        return (f"Resolved: {e.get('rule')}", f"{e.get('lab')}/{e.get('node')}: {e.get('message')}" + (f" (lasted {d // 60} min {d % 60} s)" if d else ""))
    return (f"{str(e.get('severity', '')).upper()}: {e.get('rule')}", f"{e.get('lab')}/{e.get('node')}: {e.get('message')}")


def _site() -> str:
    from . import mailer
    return mailer.site_url()


def build(ch: dict, e: dict) -> tuple[str, bytes, dict]:
    """(url, body, headers) for one alert on one channel."""
    title, body = _text(e)
    t = ch["type"]
    if t == "ntfy":
        url = ch["url"] if ch["url"].startswith("http") else f"{settings.get('channels.ntfy_server', 'https://ntfy.sh')}/{ch['url']}"
        h = {"Title": title.encode("ascii", "replace").decode(), "Priority": NTFY_PRIO.get(e.get("severity"), "default"),
             "Tags": "white_check_mark" if e.get("kind") == "resolved" else ("rotating_light" if e.get("severity") == "critical" else "warning")}
        if _site():
            h["Click"] = _site()
        if ch.get("token"):
            h["Authorization"] = f"Bearer {ch['token']}"
        return url, body.encode(), h
    if t == "slack":
        icon = ":white_check_mark:" if e.get("kind") == "resolved" else (":rotating_light:" if e.get("severity") == "critical" else ":warning:")
        return ch["url"], json.dumps({"text": f"{icon} *{title}*\n{body}"}).encode(), {"Content-Type": "application/json"}
    if t == "teams":
        # Teams "Workflows" webhooks (the replacement for Office 365 connectors) take an Adaptive Card
        color = "Good" if e.get("kind") == "resolved" else ("Attention" if e.get("severity") == "critical" else "Warning")
        card = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
                "body": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "color": color, "wrap": True},
                         {"type": "TextBlock", "text": body, "wrap": True},
                         {"type": "TextBlock", "text": datetime.fromtimestamp(e.get("ts") or time.time()).strftime("%d %b %H:%M:%S"), "isSubtle": True, "size": "Small"}]}
        if _site():
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open Patchyard", "url": _site()}]
        return ch["url"], json.dumps({"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}).encode(), \
            {"Content-Type": "application/json"}
    payload = {"source": "patchyard", "event": e.get("kind"), "title": title, "text": body,
               **{k: e.get(k) for k in ("severity", "rule", "rule_id", "lab", "node", "message", "ts", "duration")}}
    h = {"Content-Type": "application/json"}
    if ch.get("token"):
        h["Authorization"] = f"Bearer {ch['token']}"
    return ch["url"], json.dumps(payload).encode(), h


def _post(url: str, data: bytes, headers: dict, timeout=10) -> tuple[bool, str]:
    req = urllib.request.Request(url, data=data, method="POST", headers={"User-Agent": "Patchyard", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as ex:
        return False, f"HTTP {ex.code} {ex.reason}"
    except Exception as ex:  # noqa: BLE001
        return False, f"{type(ex).__name__}: {ex}"


async def deliver(ch: dict, e: dict) -> tuple[bool, str]:
    url, data, h = build(ch, e)
    ok, msg = await asyncio.to_thread(_post, url, data, h)
    STATUS[ch["id"]] = {"ok": ok, "at": time.time(), "error": "" if ok else msg}
    if not ok:
        log.warning("channel %s: %s", ch.get("name"), msg)
    return ok, msg


def _allow(key: str, now: float) -> bool:
    xs = [t for t in _SENT.get(key, []) if now - t < WINDOW]
    _SENT[key] = xs
    if len(xs) >= BURST:
        _HELD[key] = _HELD.get(key, 0) + 1
        return False
    xs.append(now)
    return True


async def _flush_held(chs: dict, now: float):
    for key, n in list(_HELD.items()):
        xs = [t for t in _SENT.get(key, []) if now - t < WINDOW]
        if len(xs) >= BURST:
            continue
        _HELD.pop(key, None)
        ch = chs.get(key)
        if ch:
            await deliver(ch, {"kind": "fired", "severity": "warning", "rule": f"{n} more alert{'s' if n != 1 else ''} held back",
                               "lab": "patchyard", "node": "-", "message": f"over {BURST} in {WINDOW // 60} minutes on this channel; see the bell for all of them",
                               "ts": now})
            _SENT.setdefault(key, []).append(now)


def _user_channels(e: dict) -> list[dict]:
    """Personal ntfy topics of users who may see this lab (owner, admins)."""
    if not settings.get("channels.user_ntfy"):
        return []
    owner = auth.lab_owner(e.get("lab", ""))
    out = []
    for n, u in users.all_users().items():
        if u.get("disabled") or not u.get("ntfy_topic"):
            continue
        if n == owner or u.get("role") == "admin":
            out.append({"id": f"user:{n}", "name": f"{n} (phone)", "type": "ntfy", "url": u["ntfy_topic"], "min_severity": "warning",
                        "resolved": True, "enabled": True, "quiet": {}})
    return out


async def route(e: dict, now: float | None = None):
    st = settings.load()
    tz = st["channels"]["timezone"]
    rule = next((r for r in st["alerts"]["rules"] if r["id"] == e.get("rule_id")), {})
    rc = rule.get("channels") or None
    chs = {c["id"]: c for c in st["channels"]["items"] + _user_channels(e)}
    now = now or time.time()
    for c in chs.values():
        if c["id"].startswith("user:"):
            if not wants(c, e, None, tz, now):
                continue
        elif not wants(c, e, rc, tz, now):
            continue
        if _allow(c["id"], now):
            await deliver(c, e)
    await _flush_held(chs, now)


async def loop():
    from . import alerts
    evs = alerts.events_after(-1)
    last = evs[-1]["eid"] if evs else 0
    while True:
        await asyncio.sleep(3)
        try:
            evs = alerts.events_after(last)
            if not evs:
                continue
            last = evs[-1]["eid"]
            if not settings.get("channels.items") and not settings.get("channels.user_ntfy"):
                continue
            for e in evs:
                await route(e)
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # noqa: BLE001
            log.warning("alert channels: %s", ex)


async def test(ch: dict) -> tuple[bool, str]:
    return await deliver(ch, {"kind": "fired", "severity": "warning", "rule": "Test from Patchyard", "lab": "patchyard", "node": "-",
                              "message": "If you can see this, the channel works.", "ts": time.time()})
