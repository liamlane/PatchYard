"""Outgoing email (SMTP): account requests, approvals, password resets, invites and alert digests.

Settings live under settings "email" (Admin → Email). Nothing is sent until an admin has
saved the SMTP details and turned email on; "Send test email" checks them and records the
result, so the Admin panel can show whether email actually works.

Every send runs in a worker thread with a timeout, so a slow or dead mail server never
blocks the web app. Failures are logged and kept (last_error) for the Admin panel.
"""
import asyncio, html, logging, re, smtplib, ssl, time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, formatdate
from . import settings
from .env import env

log = logging.getLogger("patchyard.mail")
STATUS = {"last_ok": None, "last_error": None, "last_error_at": None, "sent": 0, "failed": 0}
ADDR_RE = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_BG: set = set()


def cfg() -> dict:
    return settings.get("email", {}) or {}


def configured() -> bool:
    c = cfg()
    return bool(c.get("host") and c.get("from_addr"))


def enabled() -> bool:
    """Email is on and has what it needs."""
    return bool(cfg().get("enabled")) and configured()


def site_url() -> str:
    """The address people use to reach Patchyard, for links in emails (never taken from a request's Host header)."""
    u = (cfg().get("site_url") or "").strip().rstrip("/")
    if not u and env("DOMAIN", ""):
        u = f"https://{env('DOMAIN')}"
    return u


def _send_sync(c: dict, to: list[str], subject: str, text: str, html_body: str | None, reply_to: str | None = None):
    msg = EmailMessage()
    msg["From"] = formataddr((c.get("from_name") or "Patchyard", c["from_addr"]))
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=c["from_addr"].split("@")[-1])
    msg["Auto-Submitted"] = "auto-generated"
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    port, sec, timeout = int(c.get("port") or 587), c.get("security") or "starttls", 20
    ctx = ssl.create_default_context()
    if c.get("allow_invalid_cert"):
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    if sec == "ssl":
        s = smtplib.SMTP_SSL(c["host"], port, timeout=timeout, context=ctx)
    else:
        s = smtplib.SMTP(c["host"], port, timeout=timeout)
    try:
        s.ehlo()
        if sec == "starttls":
            s.starttls(context=ctx)
            s.ehlo()
        if c.get("username"):
            s.login(c["username"], c.get("password") or "")
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            s.close()


def _explain(e: Exception) -> str:
    """Turn smtplib/socket errors into something an admin can act on."""
    t = f"{type(e).__name__}: {e}"
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return f"the server refused the username or password ({e.smtp_code}). For Microsoft 365 or Gmail use an app password."
    if isinstance(e, smtplib.SMTPNotSupportedError) or "STARTTLS" in t:
        return f"{t}. Try 'SSL/TLS' on port 465, or STARTTLS on 587."
    if isinstance(e, ssl.SSLError):
        return f"TLS failed ({e}). Check the security setting matches the port (465 = SSL/TLS, 587 = STARTTLS)."
    if isinstance(e, (TimeoutError, OSError)) and not isinstance(e, smtplib.SMTPException):
        return f"couldn't connect ({e}). Check the host and port, and that this server may send on that port (many hosts block 25)."
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return f"the server refused the recipient(s): {', '.join(e.recipients)}"
    if isinstance(e, smtplib.SMTPSenderRefused):
        return f"the server refused the From address ({e.smtp_code} {e.smtp_error!r}). It usually has to be the account you log in with, or a domain it's allowed to send for."
    return t


async def send(to, subject: str, text: str, html_body: str | None = None, *, force=False, cfg_override: dict | None = None,
               reply_to: str | None = None) -> tuple[bool, str]:
    """Send one email. Returns (ok, error). force=True sends even when email is turned off (the test button)."""
    c = cfg_override or cfg()
    to = [t for t in ([to] if isinstance(to, str) else list(to)) if t and ADDR_RE.match(t)]
    if not to:
        return False, "no valid recipient"
    if not (c.get("host") and c.get("from_addr")):
        return False, "email isn't set up (Admin → Email)"
    if not force and not c.get("enabled"):
        return False, "email is turned off"
    try:
        await asyncio.wait_for(asyncio.to_thread(_send_sync, c, to, subject, text, html_body, reply_to), 45)
    except Exception as e:  # noqa: BLE001 - every failure is reported, none may crash a request
        err = _explain(e) if not isinstance(e, asyncio.TimeoutError) else "the mail server didn't answer within 45 s"
        STATUS.update(last_error=err, last_error_at=time.time(), failed=STATUS["failed"] + 1)
        log.warning("mail to %s failed: %s", to, err)
        return False, err
    STATUS.update(last_ok=time.time(), sent=STATUS["sent"] + 1)
    return True, ""


def send_bg(to, subject: str, text: str, html_body: str | None = None, **kw):
    """Fire and forget (the request that caused it doesn't wait for the mail server)."""
    if not enabled():
        return
    t = asyncio.ensure_future(send(to, subject, text, html_body, **kw))
    _BG.add(t)
    t.add_done_callback(_BG.discard)


# ------------------------------------------------------------------ message bodies

def render(title: str, paras: list[str], button: tuple[str, str] | None = None, foot: str = "") -> tuple[str, str]:
    """(text, html) for a short message: a few paragraphs and an optional button link."""
    text = "\n\n".join(paras)
    if button:
        text += f"\n\n{button[0]}: {button[1]}"
    if foot:
        text += f"\n\n--\n{foot}"
    esc = html.escape
    body = "".join(f"<p style='margin:0 0 14px'>{esc(p).replace(chr(10), '<br>')}</p>" for p in paras)
    if button:
        body += (f"<p style='margin:22px 0'><a href='{esc(button[1], quote=True)}' style='background:#1f5fa8;color:#fff;"
                 f"text-decoration:none;padding:10px 18px;border-radius:8px;display:inline-block'>{esc(button[0])}</a></p>"
                 f"<p style='font-size:12px;color:#667'>{esc(button[1])}</p>")
    h = (f"<!doctype html><html><body style='margin:0;background:#eef2f6;padding:24px;font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;color:#1d2733'>"
         f"<div style='max-width:560px;margin:0 auto;background:#fff;border-radius:12px;padding:26px 28px;border:1px solid #dbe2ea'>"
         f"<div style='font-weight:700;font-size:17px;margin-bottom:16px;color:#1f5fa8'>Patchyard</div>"
         f"<h2 style='font-size:19px;margin:0 0 14px'>{esc(title)}</h2>{body}"
         + (f"<p style='font-size:12px;color:#667;border-top:1px solid #e3e8ee;padding-top:12px;margin-top:20px'>{esc(foot)}</p>" if foot else "")
         + "</div></body></html>")
    return text, h


def admin_recipients() -> list[str]:
    from . import users
    out = [u.get("email") for u in users.all_users().values() if u.get("role") == "admin" and not u.get("disabled") and u.get("email")]
    out += [a for a in cfg().get("admin_to") or [] if a]
    return sorted({a.lower(): a for a in out if ADDR_RE.match(a)}.values())
