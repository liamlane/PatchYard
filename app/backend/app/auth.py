"""Optional login. Off by default; turn it on in Settings > Security by setting a password.

Password: PBKDF2-SHA256 (240k rounds, random salt). Sessions: HMAC-signed cookie holding
an expiry, keyed by a secret generated once in the data folder. Covers every /api route
and the /ws consoles; the UI itself loads so it can show the login screen.

Locked out?  docker exec netlab python -m app.auth reset   (or set NETLAB_RESET_AUTH=1)
"""
import base64, hashlib, hmac, os, secrets, sys, time
from pathlib import Path
from . import settings

COOKIE = "netlab_session"
SECRET_FILE = settings.FILE.parent / "secret.key"
OPEN = ("/api/auth/", "/api/version")


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


def make_session() -> tuple[str, int]:
    hours = float(settings.get("security.session_hours", 12))
    exp = int(time.time() + hours * 3600)
    msg = str(exp).encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}", int(hours * 3600)


def valid_session(tok: str | None) -> bool:
    if not tok or "." not in tok:
        return False
    exp, sig = tok.split(".", 1)
    good = hmac.new(_secret(), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def enabled() -> bool:
    s = settings.load()["security"]
    return bool(s["auth_enabled"] and s["password_hash"]) and os.environ.get("NETLAB_RESET_AUTH") != "1"


def _cookie(scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode(errors="ignore").split(";"):
                name, _, val = part.strip().partition("=")
                if name == COOKIE:
                    return val
    return None


class AuthMiddleware:
    """Pure ASGI so it covers WebSockets as well as HTTP."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            guarded = path.startswith("/api/") or path.startswith("/ws/")
            if guarded and not path.startswith(OPEN) and enabled() and not valid_session(_cookie(scope)):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4401})
                    return
                body = b'{"detail":"login required"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def reset():
    s = settings.load()
    s["security"]["auth_enabled"] = False
    settings.save(s, keep_secret=True)
    raw = settings.load()
    raw["security"]["password_hash"] = ""
    settings.save(raw, keep_secret=False)
    print("netlab login disabled and password cleared. Set a new one in Settings > Security.")


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "reset":
    reset()
