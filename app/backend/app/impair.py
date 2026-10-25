"""Link fault injection: admin down/up, flap, and netem impairments (delay, jitter,
loss, rate, duplicate, corrupt) applied with the host's `tc` inside each link end's
network namespace (nsenter). Settings live on the link in lab.json and are re-applied
after every deploy. netem shapes egress, so "both" applies it on each end.
"""
import asyncio
from .util import run, check_if
from . import netinfo

PRESETS = {
    "clean":        {"label": "Clean (no impairment)"},
    "broadband":    {"label": "Home broadband", "delay": 18, "jitter": 3, "loss": 0.1, "rate": 80000},
    "4g":           {"label": "4G mobile", "delay": 55, "jitter": 20, "loss": 0.8, "rate": 25000},
    "3g":           {"label": "3G mobile", "delay": 150, "jitter": 50, "loss": 2, "rate": 2000},
    "satellite":    {"label": "Satellite (GEO)", "delay": 300, "jitter": 15, "loss": 0.5, "rate": 15000},
    "leo":          {"label": "Satellite (LEO)", "delay": 30, "jitter": 12, "loss": 0.3, "rate": 100000},
    "lossy-wifi":   {"label": "Lossy Wi-Fi", "delay": 6, "jitter": 12, "loss": 4, "rate": 30000},
    "congested-wan": {"label": "Congested WAN", "delay": 80, "jitter": 30, "loss": 2, "rate": 2000},
    "intercontinental": {"label": "Intercontinental (LDN-SYD)", "delay": 140, "jitter": 8, "loss": 0.2},
    "dying-link":   {"label": "Dying link (errors)", "delay": 10, "loss": 15, "corrupt": 2, "duplicate": 1},
}
FIELDS = ("delay", "jitter", "loss", "rate", "duplicate", "corrupt")


def clean(cfg: dict) -> dict:
    c = {}
    for k in FIELDS:
        v = cfg.get(k)
        if v in (None, "", 0, "0"):
            continue
        v = float(v)
        lim = {"delay": 10000, "jitter": 5000, "loss": 100, "rate": 10_000_000, "duplicate": 100, "corrupt": 100}[k]
        if not 0 <= v <= lim:
            raise ValueError(f"{k} {v} out of range 0-{lim}")
        c[k] = v
    if c.get("jitter") and not c.get("delay"):
        raise ValueError("jitter needs a delay")
    d = cfg.get("dir", "both")
    if d not in ("both", "a2b", "b2a"):
        raise ValueError("dir must be both, a2b or b2a")
    c["dir"] = d
    if cfg.get("preset"):
        c["preset"] = str(cfg["preset"])[:40]
    return c


def netem_args(c: dict) -> list[str]:
    a = []
    if c.get("delay"):
        a += ["delay", f"{c['delay']:g}ms"]
        if c.get("jitter"):
            a += [f"{c['jitter']:g}ms", "distribution", "normal"]
    if c.get("loss"):
        a += ["loss", f"{c['loss']:g}%"]
    if c.get("duplicate"):
        a += ["duplicate", f"{c['duplicate']:g}%"]
    if c.get("corrupt"):
        a += ["corrupt", f"{c['corrupt']:g}%"]
    if c.get("rate"):
        a += ["rate", f"{int(c['rate'])}kbit"]
    return a


async def _ends(labd, link):
    ctr = await netinfo.containers()
    ends = []
    for side in ("a", "b"):
        node, ifn = link[side], check_if(link[f"{side}_if"])
        c = ctr.get((labd["name"], node))
        ends.append({"side": side, "node": node, "if": ifn, "pid": c["pid"] if c and c["state"] == "running" else None})
    return ends


async def _ns(pid, *cmd):
    return await run("nsenter", "-t", str(pid), "-n", *cmd, timeout=10)


async def apply(labd, link, cfg: dict | None) -> dict:
    """cfg None/empty -> remove impairment."""
    c = clean(cfg or {})
    args = netem_args(c)
    out, ok = [], True
    for e in await _ends(labd, link):
        if not e["pid"]:
            out.append(f"{e['node']} {e['if']}: not running (host bridge or stopped), skipped")
            continue
        egress = c["dir"] == "both" or (c["dir"] == "a2b" and e["side"] == "a") or (c["dir"] == "b2a" and e["side"] == "b")
        if args and egress:
            rc, o, err = await _ns(e["pid"], "tc", "qdisc", "replace", "dev", e["if"], "root", "netem", *args)
        else:
            rc, o, err = await _ns(e["pid"], "tc", "qdisc", "del", "dev", e["if"], "root")
            if rc and ("No such file" in err or "Cannot delete qdisc with handle of zero" in err or "Invalid handle" in err):
                rc, err = 0, ""
        ok &= rc == 0
        out.append(f"{e['node']} {e['if']}: " + ("netem " + " ".join(args) if args and egress else "no impairment") + (f"  ERROR {err.strip()}" if rc else ""))
    return {"ok": ok, "output": "\n".join(out), "impair": c if args else None}


async def set_state(labd, link, up: bool) -> dict:
    out, ok = [], True
    for e in await _ends(labd, link):
        if not e["pid"]:
            continue
        rc, o, err = await _ns(e["pid"], "ip", "link", "set", e["if"], "up" if up else "down")
        ok &= rc == 0
        out.append(f"{e['node']} {e['if']}: {'up' if up else 'down'}" + (f"  ERROR {err.strip()}" if rc else ""))
    return {"ok": ok, "output": "\n".join(out)}


async def show(labd, link) -> dict:
    out = []
    for e in await _ends(labd, link):
        if not e["pid"]:
            out.append(f"{e['node']} {e['if']}: not visible from the host")
            continue
        rc, o, err = await _ns(e["pid"], "tc", "-s", "qdisc", "show", "dev", e["if"])
        out.append(f"== {e['node']} {e['if']}\n{(o or err).strip()}")
    return {"output": "\n".join(out)}


async def reapply_all(labd) -> list[str]:
    """After deploy: restore admin-down links and impairments saved in lab.json."""
    notes = []
    for l in labd.get("links", []):
        if l.get("impair"):
            r = await apply(labd, l, l["impair"])
            notes.append(f"{l['a']}-{l['b']}: impairment {'restored' if r['ok'] else 'FAILED'}")
        if l.get("admin") == "down":
            r = await set_state(labd, l, False)
            notes.append(f"{l['a']}-{l['b']}: kept admin down")
    return notes


FLAPS: dict[str, asyncio.Task] = {}


def flap(labd, link, seconds: float):
    key = f"{labd['name']}:{link['id']}"
    if key in FLAPS and not FLAPS[key].done():
        raise ValueError("this link is already flapping")

    async def go():
        await set_state(labd, link, False)
        await asyncio.sleep(seconds)
        await set_state(labd, link, True)
    FLAPS[key] = asyncio.create_task(go())
