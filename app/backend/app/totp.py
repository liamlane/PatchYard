"""Two-factor sign-in: TOTP (RFC 6238, the codes authenticator apps show) and recovery codes.

Secrets are 160-bit base32 strings kept in users.json; recovery codes are stored as SHA-256
hashes and each works once. A code is accepted for the current 30 s step and one step either
side (clock drift), and never twice: the last used step is remembered per user.
"""
import base64, hashlib, hmac, secrets, struct, time
from urllib.parse import quote

STEP, DIGITS = 30, 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    h = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(n).zfill(DIGITS)


def check(secret: str, code: str, last_step: int = -1, now: float | None = None) -> int | None:
    """The time step the code matched (to remember against replay), or None."""
    code = "".join(c for c in str(code or "") if c.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    cur = int((now if now is not None else time.time()) // STEP)
    for st in (cur - 1, cur, cur + 1):
        if st > last_step and hmac.compare_digest(code_at(secret, st), code):
            return st
    return None


def uri(secret: str, account: str, issuer: str = "Patchyard") -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def recovery_codes(n: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(n)]


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().replace(" ", "").encode()).hexdigest()
