"""Audit log: who did what, for admins (Admin → Audit log).

One JSON object per line in <home>/audit.log, rotated at 5 MB (one old file kept).
Records sign-ins, account and invite changes, settings changes and lab lifecycle events.
"""
import json, logging, os, threading, time
from collections import deque
from . import settings

FILE = settings.FILE.parent / "audit.log"
MAX = 5 * 1024 * 1024
_LOCK = threading.Lock()


def record(action: str, target: str = "", detail: str = "", who: str | None = None, ip: str = ""):
    if who is None:
        from . import auth
        w = auth.CURRENT.get()
        who = w["name"] if w else ""
    row = {"ts": round(time.time(), 3), "who": who, "action": action, "target": str(target)[:120], "detail": str(detail)[:300], "ip": ip}
    try:
        with _LOCK:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            if FILE.exists() and FILE.stat().st_size > MAX:
                os.replace(FILE, FILE.with_suffix(".log.1"))
            with open(FILE, "a") as f:
                f.write(json.dumps(row) + "\n")
            os.chmod(FILE, 0o600)
    except OSError as e:
        logging.getLogger("patchyard").warning("audit log: %s", e)


def tail(limit: int = 500, who: str = "", q: str = "") -> list[dict]:
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
                    if who and r.get("who") != who:
                        continue
                    if q and q.lower() not in json.dumps(r).lower():
                        continue
                    out.append(r)
        except OSError:
            continue
    return list(reversed(out))
