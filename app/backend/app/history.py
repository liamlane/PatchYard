"""Time machine: what the network looked like at any moment of the last day.

While a lab is deployed, every `history.interval_s` (default 30 s) Patchyard records each
router's selected routes, its OSPF/BGP/LDP neighbour states and every port's up/down state.
A record is only written when something changed, so the history is a list of change points:
"at 14:02:31, r2 lost 3 routes and its OSPF neighbour 10.255.0.1 went Init". Each one can be
compared with the one before it, or with the network now.

Stored in <lab folder>/history.jsonl, kept for `history.keep_hours` (default 24) and at most
500 change points.
"""
import asyncio, json, logging, time
from . import labs, netinfo, settings, devices, baseline

FILE = "history.jsonl"
MAX = 500
log = logging.getLogger("patchyard.history")
LAST: dict[str, dict] = {}            # lab -> last recorded state (without ts)
SEEN: dict[str, float] = {}           # lab -> last time a sample was taken (unchanged or not)


def _p(lab: dict):
    return labs.workdir(lab) / FILE


async def sample(lab: dict) -> dict:
    b = await baseline.capture(lab, with_reach=False)
    st = await devices.link_status_fast(lab)
    ifaces = {f"{n} {i}": v for n, row in (st.get("ifaces") or {}).items() for i, v in row.items() if i != "lo" and i != "eth0"}
    return {"routes": b["routes"], "neighbours": b["neighbours"], "ifaces": ifaces, "reach": {}}


def load(lab: dict) -> list[dict]:
    try:
        rows = [json.loads(ln) for ln in _p(lab).read_text().splitlines() if ln.strip()]
    except (OSError, ValueError):
        return []
    cut = time.time() - float(settings.get("history.keep_hours", 24)) * 3600
    return [r for r in rows if r.get("ts", 0) >= cut][-MAX:]


def _write_all(lab: dict, rows: list[dict]):
    p = _p(lab)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(p)


async def record(lab: dict) -> bool:
    """Take a sample; append it if it differs from the last change point. True if written."""
    now = time.time()
    s = await sample(lab)
    SEEN[lab["name"]] = now
    last = LAST.get(lab["name"])
    if last is None:
        rows = load(lab)
        last = {k: v for k, v in rows[-1].items() if k != "ts"} if rows else None
    if last == s:
        return False
    LAST[lab["name"]] = s
    p = _p(lab)
    with open(p, "a") as f:
        f.write(json.dumps({"ts": round(now, 1), **s}) + "\n")
    if p.stat().st_size > 8 * 1024 * 1024 or sum(1 for _ in open(p)) > MAX + 50:
        _write_all(lab, load(lab))
    return True


def _ifdiff(a: dict, b: dict) -> list[dict]:
    return [{"port": k, "before": a.get(k) or "absent", "after": b.get(k) or "absent"}
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def _counts(d: dict) -> dict:
    return {"routes": len(d["routes"]), "neighbours": len(d["neighbours"]), "ports": len(d["ifaces"])}


def timeline(lab: dict) -> dict:
    rows = load(lab)
    out = []
    for i, r in enumerate(rows):
        if i == 0:
            out.append({"i": i, "ts": r["ts"], "first": True, "routes": sum(len(v) for v in r["routes"].values()),
                        "ports_up": sum(1 for v in r["ifaces"].values() if v == "up")})
            continue
        d = {**baseline.diff(rows[i - 1], r), "ifaces": _ifdiff(rows[i - 1]["ifaces"], r["ifaces"])}
        out.append({"i": i, "ts": r["ts"], **_counts(d),
                    "summary": _summary(d)})
    return {"points": out, "sampled": SEEN.get(lab["name"]), "interval": settings.get("history.interval_s", 30),
            "enabled": settings.get("history.enabled", True)}


def _summary(d: dict) -> str:
    bits = []
    lost = [r for r in d["routes"] if r["change"] == "lost"]
    gained = [r for r in d["routes"] if r["change"] == "gained"]
    moved = [r for r in d["routes"] if r["change"] == "moved"]
    if lost:
        bits.append(f"{len(lost)} route{'s' if len(lost) > 1 else ''} lost")
    if gained:
        bits.append(f"{len(gained)} gained")
    if moved:
        bits.append(f"{len(moved)} moved")
    for n in d["neighbours"][:2]:
        bits.append(f"{n['router']} {n['neighbour']} {n['before']} → {n['after']}")
    if len(d["neighbours"]) > 2:
        bits.append(f"{len(d['neighbours']) - 2} more neighbour changes")
    downs = [p["port"] for p in d["ifaces"] if p["after"] != "up"]
    ups = [p["port"] for p in d["ifaces"] if p["after"] == "up"]
    if downs:
        bits.append("down: " + ", ".join(downs[:3]) + (" …" if len(downs) > 3 else ""))
    if ups:
        bits.append("up: " + ", ".join(ups[:3]) + (" …" if len(ups) > 3 else ""))
    return "; ".join(bits) or "no visible change"


def point(lab: dict, i: int) -> dict:
    rows = load(lab)
    if not 0 <= i < len(rows):
        raise ValueError("that moment is no longer in the history")
    r = rows[i]
    prev = rows[i - 1] if i else {"routes": {}, "neighbours": {}, "ifaces": {}, "reach": {}}
    return {"ts": r["ts"], "prev_ts": rows[i - 1]["ts"] if i else None,
            "changes": {**baseline.diff(prev, r), "ifaces": _ifdiff(prev["ifaces"], r["ifaces"])},
            "state": {"routes": r["routes"], "neighbours": r["neighbours"], "ifaces": r["ifaces"]}}


async def compare_now(lab: dict, i: int) -> dict:
    rows = load(lab)
    if not 0 <= i < len(rows):
        raise ValueError("that moment is no longer in the history")
    now = await sample(lab)
    r = rows[i]
    return {"ts": r["ts"], "now": time.time(), "changes": {**baseline.diff(r, now), "ifaces": _ifdiff(r["ifaces"], now["ifaces"])}}


def at(lab: dict, ts: float) -> int | None:
    """Index of the change point in force at time ts (for "show me the network when the alert fired")."""
    rows = load(lab)
    best = None
    for i, r in enumerate(rows):
        if r["ts"] <= ts:
            best = i
    return best


async def loop():
    while True:
        await asyncio.sleep(10)
        try:
            if not settings.get("history.enabled", True):
                continue
            every = max(10, int(settings.get("history.interval_s", 30)))
            ctrs = await netinfo.containers()
            running = {l for (l, _n), c in ctrs.items() if c["state"] == "running"}
            for name in sorted(running):
                if time.time() - SEEN.get(name, 0) < every:
                    continue
                try:
                    lab = labs.load(name)
                except Exception:  # noqa: BLE001 - not one of ours (or deleted mid-loop)
                    SEEN[name] = time.time()
                    continue
                await record(lab)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("time machine: %s", e)
