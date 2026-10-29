"""Accounts, sessions and access control.

Login: username + password (users.py). Sessions: HMAC-signed cookie "user.expiry.gen.sig",
keyed by a secret generated once in the data folder; bumping a user's "gen" (password
change, disable) revokes their sessions. Covers every /api route and the /ws consoles;
the UI itself loads so it can show the login and invite pages.

Access control (multi-user): every /api/labs/<lab>/... and /ws/console/<lab>/... request is
checked against the lab's owner (admins see everything); someone else's lab answers 404.
Admin-only: settings changes, importing from a host path, /api/admin/*.

No users at all (fresh install with --no-login, or tests) = open single-user mode where
everyone is "admin". Locked out:
  docker exec patchyard python -m app.auth reset-password admin    (prints a new password)
  docker exec -i patchyard python -m app.auth create-admin NAME    (reads the password from stdin)
"""
import base64, contextvars, hashlib, hmac, json, os, re, secrets, sys, time
from . import settings, users
from .env import env

COOKIE = "patchyard_session"
SECRET_FILE = settings.FILE.parent / "secret.key"
OPEN = ("/api/auth/", "/api/version", "/api/invite/")
ADMIN_ONLY = (("PUT", "/api/settings"), ("POST", "/api/labs/import"), ("*", "/api/admin/"))
LAB_PATH = re.compile(r"^/(?:api/labs|ws/console)/([a-z0-9][a-z0-9-]{0,30})(?:/|$)")
NOT_LABS = {"import", "import-zip"}

CURRENT: contextvars.ContextVar = contextvars.ContextVar("patchyard_user", default=None)


def current() -> dict:
    """{"name", "role"} of whoever is making this request."""
    return CURRENT.get() or {"name": "admin", "role": "admin"}


def is_admin() -> bool:
    return current()["role"] == "admin"


def _secret() -> bytes:
    try:
        return SECRET_FILE.read_bytes()
    except OSError:
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        s = secrets.token_bytes(32)
        SECRET_FILE.write_bytes(s)
        os.chmod(SECRET_FILE, 0o600)
        return s


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return f"pbkdf2${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, s, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(s), 240_000)
        return hmac.compare_digest(dk, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def _sig(msg: str) -> str:
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(name: str) -> tuple[str, int]:
    hours = float(settings.get("security.session_hours", 12))
    exp = int(time.time() + hours * 3600)
    gen = users.session_gen(name) or 0
    msg = f"{name}.{exp}.{gen}"
    return f"{msg}.{_sig(msg)}", int(hours * 3600)


def session_user(tok: str | None) -> dict | None:
    if not tok or tok.count(".") != 3:
        return None
    name, exp, gen, sig = tok.split(".")
    if not hmac.compare_digest(sig, _sig(f"{name}.{exp}.{gen}")) or not exp.isdigit() or int(exp) < time.time():
        return None
    u = users.get(name)
    if not u or u.get("disabled") or str(u.get("gen", 1)) != gen:
        return None
    return {"name": name, "role": u.get("role", "user")}


def valid_session(tok: str | None) -> bool:
    return session_user(tok) is not None


def enabled() -> bool:
    return users.any_users() and env("RESET_AUTH") != "1"


# ------------------------------------------------------------------ login throttling
_FAILS: dict[str, list[float]] = {}


def throttled(key: str, limit=8, window=900) -> bool:
    now = time.time()
    xs = [t for t in _FAILS.get(key, []) if now - t < window]
    _FAILS[key] = xs
    return len(xs) >= limit


def failed(*keys):
    for k in keys:
        _FAILS.setdefault(k, []).append(time.time())


# ------------------------------------------------------------------ lab ownership
_OWN: dict[str, tuple[float, str]] = {}


def lab_owner(lab: str) -> str | None:
    """Owner of a lab ('admin' for labs from before multi-user); None if it doesn't exist."""
    f = settings.DATA / lab / "lab.json"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    hit = _OWN.get(lab)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        owner = json.loads(f.read_text()).get("owner") or first_admin()
    except (OSError, ValueError):
        owner = first_admin()
    _OWN[lab] = (mt, owner)
    return owner


def first_admin() -> str:
    us = users.all_users()
    return next((n for n, u in sorted(us.items(), key=lambda x: x[1].get("created", 0)) if u.get("role") == "admin"), "admin")


def can_access(lab: str, who: dict | None = None) -> bool:
    who = who or current()
    if who["role"] == "admin":
        return True
    o = lab_owner(lab)
    return o is None or o == who["name"]          # None: doesn't exist yet -> the route gives 404/creates


# ------------------------------------------------------------------ middleware

def _cookie(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode(errors="ignore").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode(errors="ignore")
    return ""


class AuthMiddleware:
    """Pure ASGI so it covers WebSockets as well as HTTP."""

    def __init__(self, app):
        self.app = app

    async def _deny(self, scope, send, status, detail):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403})
            return
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        guarded = path.startswith("/api/") or path.startswith("/ws/")
        if not guarded:
            return await self.app(scope, receive, send)
        method = scope.get("method", "GET")
        # cross-site requests: browsers send Origin on POST/PUT/DELETE and WebSockets
        origin = _header(scope, b"origin")
        if origin and (method not in ("GET", "HEAD") or scope["type"] == "websocket"):
            host = _header(scope, b"x-forwarded-host") or _header(scope, b"host")
            if re.sub(r"^https?://", "", origin).split("/")[0] != host.split(",")[0].strip():
                return await self._deny(scope, send, 403, "cross-site request refused")
        who = None
        if enabled():
            who = session_user(_cookie(scope))
            if who is None and not path.startswith(OPEN):
                return await self._deny(scope, send, 401, "login required")
        else:
            who = {"name": first_admin(), "role": "admin"}
        token = CURRENT.set(who)
        try:
            if who and who["role"] != "admin":
                for m, pre in ADMIN_ONLY:
                    if (m == "*" or m == method) and (path == pre or (pre.endswith("/") and path.startswith(pre))):
                        return await self._deny(scope, send, 403, "admins only")
                mm = LAB_PATH.match(path)
                if mm and mm.group(1) not in NOT_LABS and not can_access(mm.group(1), who):
                    return await self._deny(scope, send, 404, f"lab '{mm.group(1)}' not found")
            return await self.app(scope, receive, send)
        finally:
            CURRENT.reset(token)


# ------------------------------------------------------------------ CLI

def _readpw() -> str:
    return sys.stdin.readline().rstrip("\n")


if __name__ == "__main__" and len(sys.argv) > 1:
    cmd = sys.argv[1]
    try:
        if cmd == "status":
            print("on" if enabled() else "off")
        elif cmd == "create-admin":
            name = sys.argv[2] if len(sys.argv) > 2 else "admin"
            if users.get(name):
                users.set_password(name, _readpw())
                users.update(name, role="admin", disabled=False)
                print(f"Password reset; {name} is an admin.")
            else:
                users.create(name, _readpw(), role="admin")
                print(f"Admin account {name} created. Login is on.")
        elif cmd in ("reset-password", "reset"):
            name = sys.argv[2] if len(sys.argv) > 2 else first_admin()
            if not users.get(name):
                raise SystemExit(f"no user {name}; create one with: python -m app.auth create-admin NAME")
            pw = secrets.token_urlsafe(12)
            users.set_password(name, pw)
            users.update(name, disabled=False)
            print(f"New password for {name}: {pw}")
        elif cmd == "set-password":           # kept for older install scripts: the first admin
            users_exist = users.any_users()
            if users_exist:
                users.set_password(first_admin(), _readpw())
            else:
                users.create("admin", _readpw(), role="admin")
            print("Patchyard login is on with the new password.")
        elif cmd == "list":
            for n, u in users.all_users().items():
                print(f"{n:24} {u.get('role'):6} {'disabled' if u.get('disabled') else ''}")
        else:
            raise SystemExit(f"unknown command {cmd}")
    except users.UserError as e:
        raise SystemExit(str(e))
