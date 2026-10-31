"""User accounts and invites (multi-user Patchyard).

Stored in <home>/users.json next to settings.json:
  {"users":   {name: {"hash", "role": "admin"|"user", "created", "last_login", "disabled",
                       "gen": int (bumped to revoke sessions), "internet": bool, "quota": {..overrides}}},
   "invites": {sha256(token): {"created", "expires", "by", "role", "note", "used_by", "used_at"}},
   "requests": {id: {"username", "full_name", "email", "org", "reason", "hash", "created", "ip",
                     "state": "pending"|"declined", "decided", "by"}}}
Users also carry "full_name" and "email" (optional for accounts made before 0.12.1).

Invite tokens are only shown once (when created); the file keeps their hash.
A single-user install (one admin, login on) is the same thing with one user in it.
"""
import hashlib, hmac, json, os, re, secrets, threading, time
from . import settings

FILE = settings.FILE.parent / "users.json"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
RESERVED = {"admin-api", "api", "root", "system", "patchyard", "netlab", "invite", "login", "logout"}
QUOTA_KEYS = ("max_labs", "max_running_labs", "max_devices", "device_memory_mb", "device_cpus", "max_disk_mb")
_LOCK = threading.RLock()


class UserError(ValueError):
    pass


def _empty():
    return {"users": {}, "invites": {}, "requests": {}, "resets": {}}


_MEMO: dict = {"key": None, "d": None}


def _load() -> dict:
    """users.json, parsed once per change (it's read on every authenticated request)."""
    try:
        st = FILE.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and _MEMO["key"] == key:
        return json.loads(_MEMO["d"])                       # a private copy, callers may modify it
    try:
        raw = FILE.read_text()
        d = json.loads(raw)
        _MEMO["key"], _MEMO["d"] = key, raw
    except (OSError, ValueError):
        d = _empty()
    d.setdefault("users", {})
    d.setdefault("invites", {})
    d.setdefault("requests", {})
    d.setdefault("resets", {})
    if not d["users"]:
        _migrate(d)
    return d


def _migrate(d: dict):
    """Single-password installs (0.12 and older) become an 'admin' account with that password."""
    h = settings.load()["security"].get("password_hash") or ""
    if h:
        d["users"]["admin"] = {"hash": h, "role": "admin", "created": time.time(), "gen": 1, "internet": True}
        _save(d)


def _save(d: dict):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, FILE)


def check_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name) or name in RESERVED:
        raise UserError("username: 3-24 characters, lowercase letters, digits, _ and -, starting with a letter")
    return name


def check_password(pw: str) -> str:
    if len(pw or "") < 10:
        raise UserError("password: at least 10 characters")
    if len(pw) > 200:
        raise UserError("password: too long")
    return pw


EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f<>]")


def check_email(email: str, required=True) -> str:
    email = (email or "").strip()
    if not email and not required:
        return ""
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise UserError("email: enter a valid email address")
    return email


def check_text(v: str, label: str, lo: int, hi: int, required=True) -> str:
    v = " ".join(str(v or "").split())
    if not v and not required:
        return ""
    if CTRL_RE.search(v):
        raise UserError(f"{label}: < and > aren't allowed")
    if len(v) < lo or len(v) > hi:
        raise UserError(f"{label}: {lo}-{hi} characters")
    return v


def _email_taken(d: dict, email: str, skip_user: str = "", skip_req: str = "") -> bool:
    e = email.lower()
    return any((u.get("email") or "").lower() == e for n, u in d["users"].items() if n != skip_user) or \
        any(r["email"].lower() == e and r["state"] == "pending" for i, r in d["requests"].items() if i != skip_req)


def _hash(pw: str) -> str:
    from .auth import hash_password
    return hash_password(pw)


# ------------------------------------------------------------------ users

def all_users() -> dict:
    with _LOCK:
        return _load()["users"]


def get(name: str) -> dict | None:
    return all_users().get(name)


def any_users() -> bool:
    return bool(all_users())


def public(name: str, u: dict) -> dict:
    return {"name": name, "full_name": u.get("full_name", ""), "email": u.get("email", ""), "role": u.get("role", "user"), "created": u.get("created"), "last_login": u.get("last_login"),
            "disabled": bool(u.get("disabled")), "internet": bool(u.get("internet")), "quota": u.get("quota") or {},
            "alert_email": bool(u.get("alert_email")), "two_factor": bool((u.get("totp") or {}).get("secret")),
            "recovery_left": len(u.get("recovery") or []), "ntfy_topic": u.get("ntfy_topic", "")}


def create(name: str, password: str | None, role: str = "user", internet: bool = False,
           full_name: str = "", email: str = "", pw_hash: str | None = None) -> dict:
    name = check_name(name)
    if pw_hash is None:
        check_password(password)
    if role not in ("admin", "user"):
        raise UserError("role must be admin or user")
    full_name = check_text(full_name, "name", 2, 80, required=False)
    email = check_email(email, required=False)
    with _LOCK:
        d = _load()
        if name in d["users"]:
            raise UserError(f"user {name} already exists")
        if email and _email_taken(d, email):
            raise UserError("that email address already has an account")
        d["users"][name] = {"hash": pw_hash or _hash(password), "role": role, "created": time.time(), "gen": 1,
                            "internet": bool(internet or role == "admin"), "quota": {},
                            "full_name": full_name, "email": email}
        _save(d)
        return public(name, d["users"][name])


def verify(name: str, password: str) -> dict | None:
    from .auth import check_password as chk
    u = get((name or "").strip().lower())
    if not u or u.get("disabled"):
        chk(password or "", "pbkdf2$AAAAAAAAAAAAAAAAAAAAAA==$AAAA")      # same work either way
        return None
    return u if chk(password or "", u["hash"]) else None


def touch_login(name: str):
    with _LOCK:
        d = _load()
        if name in d["users"]:
            d["users"][name]["last_login"] = time.time()
            _save(d)


def set_password(name: str, password: str, bump=True):
    check_password(password)
    with _LOCK:
        d = _load()
        if name not in d["users"]:
            raise UserError(f"no user {name}")
        d["users"][name]["hash"] = _hash(password)
        if bump:
            d["users"][name]["gen"] = int(d["users"][name].get("gen", 1)) + 1     # log out other sessions
        _save(d)


def update(name: str, **kw) -> dict:
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        if not u:
            raise UserError(f"no user {name}")
        if "role" in kw:
            if kw["role"] not in ("admin", "user"):
                raise UserError("role must be admin or user")
            if u["role"] == "admin" and kw["role"] != "admin" and _admins(d) <= 1:
                raise UserError("keep at least one admin")
            u["role"] = kw["role"]
        if "disabled" in kw:
            if kw["disabled"] and u["role"] == "admin" and _admins(d, active=True) <= 1:
                raise UserError("can't disable the last admin")
            u["disabled"] = bool(kw["disabled"])
            u["gen"] = int(u.get("gen", 1)) + 1
        if "internet" in kw:
            u["internet"] = bool(kw["internet"])
        if "alert_email" in kw:
            u["alert_email"] = bool(kw["alert_email"])
        if "ntfy_topic" in kw:
            t = str(kw["ntfy_topic"] or "").strip()
            if t and not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", t):
                raise UserError("ntfy topic: 6-64 letters, digits, _ and - (pick something hard to guess)")
            u["ntfy_topic"] = t
        if "full_name" in kw:
            u["full_name"] = check_text(kw["full_name"], "name", 2, 80, required=False)
        if "email" in kw:
            e = check_email(kw["email"], required=False)
            if e and _email_taken(d, e, skip_user=name):
                raise UserError("that email address is used by another account")
            u["email"] = e
        if "quota" in kw:
            q = {}
            for k, v in (kw["quota"] or {}).items():
                if k not in QUOTA_KEYS:
                    raise UserError(f"unknown quota {k}")
                if v in (None, ""):
                    continue
                q[k] = float(v) if k == "device_cpus" else int(v)
                if q[k] < 0:
                    raise UserError(f"{k} can't be negative")
            u["quota"] = q
        _save(d)
        return public(name, u)


def delete(name: str):
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        if not u:
            return
        if u["role"] == "admin" and _admins(d) <= 1:
            raise UserError("can't delete the last admin")
        del d["users"][name]
        _save(d)


def _admins(d, active=False) -> int:
    return sum(1 for u in d["users"].values() if u.get("role") == "admin" and not (active and u.get("disabled")))


def session_gen(name: str) -> int | None:
    u = get(name)
    if not u or u.get("disabled"):
        return None
    return int(u.get("gen", 1))


# ------------------------------------------------------------------ quotas

DEFAULT_QUOTA = {"max_labs": 5, "max_running_labs": 1, "max_devices": 20, "device_memory_mb": 512, "device_cpus": 1.0, "max_disk_mb": 2048}


def quota(name: str) -> dict:
    """Effective limits for a user (0 = unlimited). Admins are unlimited."""
    u = get(name) or {}
    if u.get("role") == "admin" or not any_users():        # admins, and open single-user installs
        return {k: 0 for k in QUOTA_KEYS}
    base = {**DEFAULT_QUOTA, **(settings.get("users.quota", {}) or {})}   # settings first, then per-user overrides
    return {**{k: base.get(k, 0) for k in QUOTA_KEYS}, **(u.get("quota") or {})}


# ------------------------------------------------------------------ invites

def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite(by: str, role: str = "user", days: float = 7, note: str = "") -> dict:
    if role not in ("admin", "user"):
        raise UserError("role must be admin or user")
    days = max(0.05, min(90.0, float(days)))
    token = secrets.token_urlsafe(24)
    with _LOCK:
        d = _load()
        d["invites"][_th(token)] = {"created": time.time(), "expires": time.time() + days * 86400, "by": by, "role": role,
                                    "note": str(note)[:80], "used_by": None, "used_at": None}
        _save(d)
    return {"token": token, "expires": time.time() + days * 86400, "role": role}


def list_invites() -> list[dict]:
    with _LOCK:
        d = _load()
    now = time.time()
    out = []
    for h, i in d["invites"].items():
        out.append({"id": h[:12], **{k: i.get(k) for k in ("created", "expires", "by", "role", "note", "used_by", "used_at")},
                    "state": "used" if i.get("used_by") else ("expired" if i["expires"] < now else "open")})
    return sorted(out, key=lambda x: -x["created"])


def revoke_invite(short_id: str):
    with _LOCK:
        d = _load()
        for h in list(d["invites"]):
            if h.startswith(short_id) and len(short_id) >= 8:
                del d["invites"][h]
        _save(d)


def check_invite(token: str) -> dict:
    i = _load()["invites"].get(_th(token or ""))
    if not i or i.get("used_by") or i["expires"] < time.time():
        raise UserError("this invite link is invalid, used or expired")
    return i


def redeem(token: str, name: str, password: str, full_name: str = "", email: str = "") -> dict:
    with _LOCK:
        i = check_invite(token)
        check_text(full_name, "name", 2, 80)
        check_email(email)
        u = create(name, password, role=i["role"], full_name=full_name, email=email)
        d = _load()
        inv = d["invites"][_th(token)]
        inv["used_by"], inv["used_at"] = u["name"], time.time()
        _save(d)
        return u


# ------------------------------------------------------------------ account requests (from the sign-in page)

MAX_PENDING = 50
KEEP_DECLINED = 30 * 86400


def _prune(d: dict):
    now = time.time()
    for k in [k for k, r in d["requests"].items() if r["state"] == "declined" and now - (r.get("decided") or 0) > KEEP_DECLINED]:
        del d["requests"][k]


def request_account(username: str, password: str, full_name: str, email: str, org: str, reason: str, ip: str = "") -> dict:
    """Someone asks for an account; an admin approves or declines it in Admin → Requests."""
    username = check_name(username)
    check_password(password)
    full_name = check_text(full_name, "name", 2, 80)
    email = check_email(email)
    org = check_text(org, "organisation", 2, 80, required=False)
    reason = check_text(reason, "what you'll use it for", 10, 500)
    with _LOCK:
        d = _load()
        _prune(d)
        pending = [r for r in d["requests"].values() if r["state"] == "pending"]
        if len(pending) >= MAX_PENDING:
            raise UserError("too many requests are waiting: try again later")
        if username in d["users"] or any(r["username"] == username for r in pending):
            raise UserError("that username is taken: pick another")
        if _email_taken(d, email):
            raise UserError("that email address already has an account or a request waiting")
        rid = secrets.token_hex(6)
        d["requests"][rid] = {"username": username, "full_name": full_name, "email": email, "org": org, "reason": reason,
                              "hash": _hash(password), "created": time.time(), "ip": ip, "state": "pending"}
        _save(d)
    return {"ok": True}


def list_requests() -> list[dict]:
    with _LOCK:
        d = _load()
    out = [{"id": k, **{x: r.get(x) for x in ("username", "full_name", "email", "org", "reason", "created", "ip", "state", "decided", "by")}}
           for k, r in d["requests"].items()]
    return sorted(out, key=lambda r: (r["state"] != "pending", -r["created"]))


def pending_count() -> int:
    with _LOCK:
        return sum(1 for r in _load()["requests"].values() if r["state"] == "pending")


def get_request(rid: str) -> dict | None:
    return _load()["requests"].get(rid)


def approve_request(rid: str, by: str, internet: bool = False) -> dict:
    with _LOCK:
        d = _load()
        r = d["requests"].get(rid)
        if not r or r["state"] != "pending":
            raise UserError("that request isn't waiting any more")
        del d["requests"][rid]                 # out of the way of create()'s email check
        _save(d)
        try:
            return create(r["username"], None, role="user", internet=internet, full_name=r["full_name"], email=r["email"], pw_hash=r["hash"])
        except UserError:
            d = _load()
            d["requests"][rid] = r
            _save(d)
            raise


def decline_request(rid: str, by: str):
    with _LOCK:
        d = _load()
        r = d["requests"].get(rid)
        if not r:
            raise UserError("no such request")
        was = r["state"]
        if was == "pending":
            r.update(state="declined", decided=time.time(), by=by)
        else:
            del d["requests"][rid]             # declining again = forget it
        _save(d)
        return {"was": was, **{k: r.get(k) for k in ("username", "full_name", "email")}}


def request_status(username: str, password: str) -> str | None:
    """For the sign-in page: 'pending' or 'declined' if this username + password match a request."""
    from .auth import check_password as chk
    name = (username or "").strip().lower()
    with _LOCK:
        d = _load()
    for r in d["requests"].values():
        if r["username"] == name and r.get("hash") and chk(password or "", r["hash"]):
            return r["state"]
    return None


# ------------------------------------------------------------------ password reset by email

RESET_TTL = 3600


def find_user(login: str) -> str | None:
    """Username for a username or an email address (case-insensitive), or None."""
    login = (login or "").strip().lower()
    if not login:
        return None
    us = all_users()
    if login in us:
        return login
    return next((n for n, u in us.items() if (u.get("email") or "").lower() == login), None)


def create_reset(name: str) -> str:
    """A one-hour, single-use reset token for this user (older ones stop working)."""
    token = secrets.token_urlsafe(32)
    with _LOCK:
        d = _load()
        if name not in d["users"]:
            raise UserError(f"no user {name}")
        now = time.time()
        d["resets"] = {h: r for h, r in d["resets"].items() if r["user"] != name and r["expires"] > now}
        d["resets"][_th(token)] = {"user": name, "created": now, "expires": now + RESET_TTL}
        _save(d)
    return token


def check_reset(token: str) -> str:
    r = _load()["resets"].get(_th(token or ""))
    if not r or r["expires"] < time.time() or not get(r["user"]) or get(r["user"]).get("disabled"):
        raise UserError("this reset link is invalid or has expired: ask for a new one")
    return r["user"]


def use_reset(token: str, password: str) -> str:
    with _LOCK:
        name = check_reset(token)
        set_password(name, password)          # also signs out every other session
        d = _load()
        d["resets"].pop(_th(token), None)
        _save(d)
        return name


# ------------------------------------------------------------------ two-factor sign-in (TOTP)

def has_2fa(name: str) -> bool:
    return bool(((get(name) or {}).get("totp") or {}).get("secret"))


def totp_setup(name: str) -> str:
    """Start enrolment: a new secret, kept as pending until a code from the app confirms it."""
    from . import totp
    sec = totp.new_secret()
    with _LOCK:
        d = _load()
        if name not in d["users"]:
            raise UserError(f"no user {name}")
        d["users"][name]["totp_pending"] = {"secret": sec, "at": time.time()}
        _save(d)
    return sec


def totp_enable(name: str, code: str) -> list[str]:
    """Confirm enrolment with a code from the app; returns the recovery codes (shown once)."""
    from . import totp
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        pend = (u or {}).get("totp_pending") or {}
        if not pend or time.time() - pend.get("at", 0) > 900:
            raise UserError("start again: the setup expired")
        st = totp.check(pend["secret"], code)
        if st is None:
            raise UserError("that code didn't match: check the time on your phone and try the next code")
        codes = totp.recovery_codes()
        u["totp"] = {"secret": pend["secret"], "last_step": st, "enabled_at": time.time()}
        u["recovery"] = [totp.hash_code(c) for c in codes]
        u.pop("totp_pending", None)
        _save(d)
    return codes


def totp_disable(name: str):
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        if not u:
            raise UserError(f"no user {name}")
        for k in ("totp", "totp_pending", "recovery"):
            u.pop(k, None)
        u["gen"] = int(u.get("gen", 1)) + 1          # sign out everywhere
        _save(d)


def new_recovery_codes(name: str) -> list[str]:
    from . import totp
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        if not u or not (u.get("totp") or {}).get("secret"):
            raise UserError("two-factor sign-in isn't on")
        codes = totp.recovery_codes()
        u["recovery"] = [totp.hash_code(c) for c in codes]
        _save(d)
    return codes


def totp_verify(name: str, code: str) -> str | None:
    """'totp' or 'recovery' when the code is good (and uses it up), else None."""
    from . import totp
    with _LOCK:
        d = _load()
        u = d["users"].get(name)
        t = (u or {}).get("totp") or {}
        if not t.get("secret") or u.get("disabled"):
            return None
        st = totp.check(t["secret"], code, int(t.get("last_step", -1)))
        if st is not None:
            t["last_step"] = st
            _save(d)
            return "totp"
        h = totp.hash_code(code)
        rec = u.get("recovery") or []
        if any(hmac.compare_digest(h, r) for r in rec):
            u["recovery"] = [r for r in rec if not hmac.compare_digest(h, r)]
            _save(d)
            return "recovery"
    return None
