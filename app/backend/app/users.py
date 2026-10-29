"""User accounts and invites (multi-user Patchyard).

Stored in <home>/users.json next to settings.json:
  {"users":   {name: {"hash", "role": "admin"|"user", "created", "last_login", "disabled",
                       "gen": int (bumped to revoke sessions), "internet": bool, "quota": {..overrides}}},
   "invites": {sha256(token): {"created", "expires", "by", "role", "note", "used_by", "used_at"}}}

Invite tokens are only shown once (when created); the file keeps their hash.
A single-user install (one admin, login on) is the same thing with one user in it.
"""
import hashlib, json, os, re, secrets, threading, time
from . import settings

FILE = settings.FILE.parent / "users.json"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,23}$")
RESERVED = {"admin-api", "api", "root", "system", "patchyard", "netlab", "invite", "login", "logout"}
QUOTA_KEYS = ("max_labs", "max_running_labs", "max_devices", "device_memory_mb", "device_cpus")
_LOCK = threading.RLock()


class UserError(ValueError):
    pass


def _empty():
    return {"users": {}, "invites": {}}


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text())
    except (OSError, ValueError):
        d = _empty()
    d.setdefault("users", {})
    d.setdefault("invites", {})
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
    return {"name": name, "role": u.get("role", "user"), "created": u.get("created"), "last_login": u.get("last_login"),
            "disabled": bool(u.get("disabled")), "internet": bool(u.get("internet")), "quota": u.get("quota") or {}}


def create(name: str, password: str, role: str = "user", internet: bool = False) -> dict:
    name = check_name(name)
    check_password(password)
    if role not in ("admin", "user"):
        raise UserError("role must be admin or user")
    with _LOCK:
        d = _load()
        if name in d["users"]:
            raise UserError(f"user {name} already exists")
        d["users"][name] = {"hash": _hash(password), "role": role, "created": time.time(), "gen": 1,
                            "internet": bool(internet or role == "admin"), "quota": {}}
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

DEFAULT_QUOTA = {"max_labs": 5, "max_running_labs": 1, "max_devices": 20, "device_memory_mb": 512, "device_cpus": 1.0}


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


def redeem(token: str, name: str, password: str) -> dict:
    with _LOCK:
        i = check_invite(token)
        u = create(name, password, role=i["role"])
        d = _load()
        inv = d["invites"][_th(token)]
        inv["used_by"], inv["used_at"] = u["name"], time.time()
        _save(d)
        return u
