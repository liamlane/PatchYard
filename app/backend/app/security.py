"""Brute-force protection: a security event log, automatic IP bans, manual bans and an allowlist.

Events (Admin → Security): failed sign-ins and two-factor codes, invalid API tokens, reset and
invite tokens that don't exist, rate limits hit, bans and unbans. One JSON object per line in
<home>/security.log, rotated at 5 MB (one old file kept).

Automatic bans: an address with `security.ban_threshold` weighted failures inside
`security.ban_window_minutes` is banned for `security.ban_minutes`; each repeat doubles it
(at most a week). A banned address gets 403 on every API call and WebSocket, the sign-in page
included. Manual bans take an address or a CIDR, for a time or for good.

Never banned: loopback, and anything on the allowlist (`security.allowlist`, addresses or
CIDRs). If every request seems to come from 127.0.0.1 the reverse proxy's X-Forwarded-For
isn't being trusted: bans would hit everyone, so loopback is always exempt and the admin page
says so. Lost access? `docker exec patchyard python -m app.security unban ADDRESS`.
"""
import ipaddress, json, logging, os, sys, threading, time
from collections import deque
from . import settings

FILE = settings.FILE.parent / "security.log"
BANS = settings.FILE.parent / "bans.json"
MAX = 5 * 1024 * 1024
WEEK = 7 * 24 * 60
_LOCK = threading.Lock()
_FAILS: dict[str, deque] = {}                 # ip -> recent (time, weight)
_SEEN: deque = deque(maxlen=500)              # recent client addresses (proxy check)
_BLOCKED_LOGGED: dict[str, float] = {}        # ip -> last time a blocked request was logged
_CACHE = {"mt": -1.0, "bans": {}}
log = logging.getLogger("patchyard.security")


# ------------------------------------------------------------------ settings

def _cfg(k, d):
    return settings.get(f"security.{k}", d)


def _nets(xs) -> list:
    out = []
    for x in xs or []:
        try:
            out.append(ipaddress.ip_network(str(x).strip(), strict=False))
        except ValueError:
            pass
    return out


def _addr(ip: str):
    try:
        return ipaddress.ip_address((ip or "").split("%")[0])
    except ValueError:
        return None


def exempt(ip: str) -> bool:
    a = _addr(ip)
    if a is None:
        return True                           # no usable address (tests, unix sockets): never ban
    if a.is_loopback:
        return True
    return any(a in n for n in _nets(_cfg("allowlist", [])))


# ------------------------------------------------------------------ event log

def event(kind: str, ip: str = "", user: str = "", detail: str = "", weight: int = 1):
    """Record a security event; weighted ones count towards an automatic ban of the address."""
    row = {"ts": round(time.time(), 3), "kind": kind, "ip": ip or "", "user": str(user or "")[:60], "detail": str(detail)[:300]}
    _write(row)
    if weight and ip and _cfg("ban_enabled", True) and not exempt(ip):
        now = time.time()
        win = float(_cfg("ban_window_minutes", 15)) * 60
        q = _FAILS.setdefault(ip, deque(maxlen=1000))
        q.append((now, weight))
        while q and now - q[0][0] > win:
            q.popleft()
        if sum(w for _, w in q) >= int(_cfg("ban_threshold", 10)) and not banned(ip):
            _auto_ban(ip, kind)
            q.clear()
    if len(_FAILS) > 5000:                    # keep the table small under a spray from many addresses
        for k in list(_FAILS)[:1000]:
            _FAILS.pop(k, None)


def _write(row: dict):
    try:
        with _LOCK:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            if FILE.exists() and FILE.stat().st_size > MAX:
                os.replace(FILE, FILE.with_suffix(".log.1"))
            with open(FILE, "a") as f:
                f.write(json.dumps(row) + "\n")
            os.chmod(FILE, 0o600)
    except OSError as e:
        log.warning("security log: %s", e)


def tail(limit: int = 500, ip: str = "", kind: str = "") -> list[dict]:
    """Newest first."""
    out: deque = deque(maxlen=limit)
    for f in (FILE.with_suffix(".log.1"), FILE):
        try:
            with open(f) as fh:
                for ln in fh:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if ip and not r.get("ip", "").startswith(ip):
                        continue
                    if kind and not r.get("kind", "").startswith(kind):
                        continue
                    out.append(r)
        except OSError:
            continue
    return list(reversed(out))


def summary(hours: float = 24) -> dict:
    since = time.time() - hours * 3600
    rows = [r for r in tail(5000) if r["ts"] >= since]
    by_ip: dict[str, int] = {}
    for r in rows:
        if r["kind"] in ("login.fail", "2fa.fail", "token.invalid", "reset.invalid", "invite.invalid"):
            by_ip[r["ip"]] = by_ip.get(r["ip"], 0) + 1
    seen = {ip for _, ip in _SEEN}
    return {"hours": hours, "failed_logins": sum(1 for r in rows if r["kind"] == "login.fail"),
            "failures": sum(by_ip.values()), "auto_bans": sum(1 for r in rows if r["kind"] == "ban.auto"),
            "top": sorted(by_ip.items(), key=lambda x: -x[1])[:10],
            "active_bans": len([b for b in list_bans() if b["active"]]),
            # every request from loopback: the proxy's X-Forwarded-For isn't trusted, so bans can't work
            "proxy_problem": bool(seen) and all((_addr(ip) or _addr("127.0.0.1")).is_loopback for ip in seen)}


def seen(ip: str):
    if ip:
        _SEEN.append((time.time(), ip))


# ------------------------------------------------------------------ bans

def _load() -> dict:
    try:
        mt = BANS.stat().st_mtime
    except OSError:
        return {}
    if mt != _CACHE["mt"]:
        try:
            _CACHE["bans"], _CACHE["mt"] = json.loads(BANS.read_text()), mt
        except (OSError, ValueError):
            return {}
    return _CACHE["bans"]


def _save(d: dict):
    BANS.parent.mkdir(parents=True, exist_ok=True)
    tmp = BANS.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(BANS)
    _CACHE["mt"] = -1.0


def _key(ip_or_net: str) -> str:
    n = ipaddress.ip_network(str(ip_or_net).strip(), strict=False)
    if n.num_addresses == 1:
        return str(n.network_address)
    return str(n)


def banned(ip: str) -> dict | None:
    """The ban that covers this address, if any (and it hasn't expired)."""
    d = _load()
    if not d:
        return None
    a = _addr(ip)
    if a is None or exempt(ip):
        return None
    now = time.time()
    for k, b in d.items():
        if b.get("until") and b["until"] < now:
            continue
        if not b.get("active", True):
            continue
        try:
            if a in ipaddress.ip_network(k, strict=False):
                return {"key": k, **b}
        except ValueError:
            continue
    return None


def blocked_hit(ip: str, path: str):
    """A banned address tried something: log it at most once a minute per address."""
    now = time.time()
    if now - _BLOCKED_LOGGED.get(ip, 0) > 60:
        _BLOCKED_LOGGED[ip] = now
        _write({"ts": round(now, 3), "kind": "blocked", "ip": ip, "user": "", "detail": path[:200]})
        if len(_BLOCKED_LOGGED) > 5000:
            _BLOCKED_LOGGED.clear()


def ban(ip_or_net: str, minutes: float | None, reason: str = "", by: str = "") -> dict:
    k = _key(ip_or_net)
    net = ipaddress.ip_network(k, strict=False)
    if any(a.is_loopback for a in (net.network_address, net.broadcast_address)):
        raise ValueError("loopback can't be banned (it's the proxy and the server itself)")
    if net.prefixlen < (8 if net.version == 4 else 32):
        raise ValueError("that range is too wide to ban")
    with _LOCK:
        d = dict(_load())
        old = d.get(k) or {}
        d[k] = {"created": time.time(), "until": time.time() + minutes * 60 if minutes else None, "reason": str(reason)[:200],
                "by": by, "strikes": old.get("strikes", 0) + (0 if by else 1), "active": True}
        _save(d)
    _write({"ts": round(time.time(), 3), "kind": "ban.auto" if not by else "ban", "ip": k, "user": by,
            "detail": f"{'for good' if not minutes else f'{int(minutes)} min'}: {reason}"})
    return d[k]


def _auto_ban(ip: str, why: str):
    old = _load().get(_key(ip)) or {}
    strikes = int(old.get("strikes", 0))
    minutes = min(WEEK, float(_cfg("ban_minutes", 60)) * (2 ** strikes))
    ban(ip, minutes, f"{_cfg('ban_threshold', 10)} failures in {_cfg('ban_window_minutes', 15)} min (last: {why})"
        + (f", repeat offence #{strikes + 1}" if strikes else ""))
    log.warning("banned %s for %d min", ip, minutes)
    try:
        from . import audit
        audit.record("security.ban", ip, f"automatic, {int(minutes)} min", who="system", ip=ip)
        if _cfg("ban_email", True):
            from . import mailer
            to = mailer.admin_recipients() if mailer.enabled() else []
            if to:
                t, h = mailer.render(f"Address {ip} banned", [
                    f"{ip} was banned for {int(minutes)} minutes after repeated failed sign-ins or bad tokens ({why}).",
                    "Admin → Security shows what it tried, and lets you lift the ban or make it permanent."], None, "")
                mailer.send_bg(to, f"Patchyard: {ip} banned", t, h)
    except Exception as e:  # noqa: BLE001 - a notification must never break the request
        log.warning("ban notice: %s", e)


def unban(key: str, by: str = "") -> bool:
    with _LOCK:
        d = dict(_load())
        k = _key(key)
        if k not in d:
            return False
        d[k] = {**d[k], "active": False, "until": time.time()}       # keep the strike count for escalation
        _save(d)
    _FAILS.pop(k, None)
    _write({"ts": round(time.time(), 3), "kind": "unban", "ip": k, "user": by, "detail": ""})
    return True


def list_bans() -> list[dict]:
    now = time.time()
    out = []
    for k, b in sorted(_load().items(), key=lambda x: -x[1].get("created", 0)):
        active = b.get("active", True) and (not b.get("until") or b["until"] > now)
        out.append({"key": k, **b, "active": active})
    return out[:500]


def prune():
    """Forget bans that ended more than 30 days ago (their strike counts with them)."""
    with _LOCK:
        d = dict(_load())
        cut = time.time() - 30 * 86400
        keep = {k: b for k, b in d.items() if not b.get("until") or b["until"] > cut}
        if len(keep) != len(d):
            _save(keep)


if __name__ == "__main__" and len(sys.argv) > 1:
    cmd = sys.argv[1]
    if cmd == "unban" and len(sys.argv) > 2:
        print("unbanned" if unban(sys.argv[2], "cli") else "no such ban")
    elif cmd == "bans":
        for b in list_bans():
            if b["active"]:
                print(f"{b['key']:<20} until {time.strftime('%Y-%m-%d %H:%M', time.localtime(b['until'])) if b.get('until') else 'for good':<17} {b.get('reason', '')}")
    elif cmd == "ban" and len(sys.argv) > 2:
        ban(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None, "from the command line", "cli")
        print("banned")
    else:
        print("usage: python -m app.security bans | ban ADDRESS [MINUTES] | unban ADDRESS")
        sys.exit(2)
