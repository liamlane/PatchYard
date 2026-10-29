"""Background metrics collector with history (SQLite).

Every INTERVAL seconds it samples every running containerlab container (any lab, CLI
or web-app deployed): interface byte/packet/drop/error counters and CPU/memory. Raw
counters are stored; rates are computed on read, so a missed cycle never invents data.
Retention defaults to 48 h (NETLAB_METRICS_RETENTION_H).
"""
import asyncio, logging, os, sqlite3, time
from pathlib import Path
from . import netinfo
from .env import env, home

log = logging.getLogger("netlab.metrics")
from . import settings


def interval() -> float:
    return float(settings.get("monitoring.interval", 10))


def retention_h() -> float:
    return float(settings.get("monitoring.retention_hours", 48))
DB = Path(env("METRICS_DB", Path(env("DATA", f"{home()}/labs")).parent / "metrics.db"))
NCPU = os.cpu_count() or 1

# live cache: lab -> {"ts", "nodes": {node: {"cpu", "mem", "state", "ifaces": {if: {...rates, state}}}}}
LIVE: dict[str, dict] = {}
_PREV: dict[tuple, dict] = {}   # (lab, node) -> {"ts", "ifaces": counters, "cpu_usec"}


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript("""
      CREATE TABLE IF NOT EXISTS iface(lab TEXT, node TEXT, iface TEXT, ts REAL,
        rxb INTEGER, txb INTEGER, rxp INTEGER, txp INTEGER, rxd INTEGER, txd INTEGER, rxe INTEGER, txe INTEGER);
      CREATE INDEX IF NOT EXISTS iface_k ON iface(lab, node, iface, ts);
      CREATE TABLE IF NOT EXISTS node(lab TEXT, node TEXT, ts REAL, cpu_usec INTEGER, mem INTEGER);
      CREATE INDEX IF NOT EXISTS node_k ON node(lab, node, ts);
    """)
    return c


def _rate(cur, prev, dt):
    if prev is None or dt <= 0 or cur < prev:   # first sample or counter reset
        return 0.0
    return (cur - prev) / dt


async def sample_once() -> None:
    ts = time.time()
    ctrs = await netinfo.containers()
    rows_if, rows_node, live = [], [], {}
    for (lab, node), c in ctrs.items():
        lab_live = live.setdefault(lab, {"ts": ts, "nodes": {}})
        if c["state"] != "running" or not c["pid"]:
            lab_live["nodes"][node] = {"state": c["state"], "cpu": None, "mem": None, "ifaces": {}}
            continue
        ifs = netinfo.iface_counters(c["pid"])
        cg = netinfo.cgroup_stats(c["pid"])
        prev = _PREV.get((lab, node))
        dt = ts - prev["ts"] if prev else 0
        cpu = None
        if cg["cpu_usec"] is not None and prev and prev.get("cpu_usec") is not None and dt > 0:
            cpu = max(0.0, (cg["cpu_usec"] - prev["cpu_usec"]) / (dt * 1e6) * 100)
        nif = {}
        for name, v in ifs.items():
            p = (prev or {}).get("ifaces", {}).get(name, {})
            nif[name] = {"state": v["state"],
                         "rx_bps": _rate(v["rxb"], p.get("rxb"), dt) * 8, "tx_bps": _rate(v["txb"], p.get("txb"), dt) * 8,
                         "rx_pps": _rate(v["rxp"], p.get("rxp"), dt), "tx_pps": _rate(v["txp"], p.get("txp"), dt),
                         "drops": v["rxd"] + v["txd"], "errors": v["rxe"] + v["txe"],
                         "rxb": v["rxb"], "txb": v["txb"]}
            rows_if.append((lab, node, name, ts, v["rxb"], v["txb"], v["rxp"], v["txp"], v["rxd"], v["txd"], v["rxe"], v["txe"]))
        lab_live["nodes"][node] = {"state": "running", "cpu": cpu, "cpu_ncpu": NCPU, "mem": cg["mem"], "ifaces": nif}
        rows_node.append((lab, node, ts, cg["cpu_usec"], cg["mem"]))
        _PREV[(lab, node)] = {"ts": ts, "ifaces": ifs, "cpu_usec": cg["cpu_usec"]}
    LIVE.clear()
    LIVE.update(live)
    for k in [k for k in _PREV if k not in ctrs]:
        del _PREV[k]

    def write():
        c = _db()
        with c:
            c.executemany("INSERT INTO iface VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows_if)
            c.executemany("INSERT INTO node VALUES (?,?,?,?,?)", rows_node)
        c.close()
    if rows_if or rows_node:
        await asyncio.to_thread(write)
    return ctrs


async def _prune():
    def p():
        cut = time.time() - retention_h() * 3600
        c = _db()
        with c:
            c.execute("DELETE FROM iface WHERE ts < ?", (cut,))
            c.execute("DELETE FROM node WHERE ts < ?", (cut,))
        c.close()
    await asyncio.to_thread(p)


async def collector():
    n = 0
    while True:
        try:
            ctrs = await sample_once()
            from . import alerts   # late import: alerts imports metrics
            await alerts.evaluate(ctrs)
            if n % max(1, int(600 / interval())) == 0:
                await _prune()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # keep collecting whatever happens
            log.warning("metrics sample failed: %s", e)
        n += 1
        await asyncio.sleep(interval())


def _bucketed(rows, rng, compute):
    """rows: ordered raw samples; compute(prev, cur, dt) -> tuple of values.
    Returns ~300 points: [ts, *values] averaged per bucket."""
    step = max(interval(), rng / 300)
    out, bucket, bstart = [], [], None
    for prev, cur in zip(rows, rows[1:]):
        dt = cur[0] - prev[0]
        if dt <= 0 or dt > interval() * 6:   # gap (lab down / collector stopped): break the line
            if bucket:
                out.append([bstart] + [sum(x) / len(bucket) for x in zip(*bucket)]); bucket = []
            out.append([cur[0], None])
            bstart = None
            continue
        vals = compute(prev, cur, dt)
        if bstart is None or cur[0] - bstart >= step:
            if bucket:
                out.append([bstart] + [sum(x) / len(bucket) for x in zip(*bucket)])
            bucket, bstart = [], cur[0]
        bucket.append(vals)
    if bucket:
        out.append([bstart] + [sum(x) / len(bucket) for x in zip(*bucket)])
    return {"step": step, "points": out}


def history_iface(lab, node, iface, rng):
    def q():
        c = _db()
        rows = c.execute("SELECT ts, rxb, txb, rxp, txp, rxd+txd, rxe+txe FROM iface WHERE lab=? AND node=? AND iface=? AND ts>=? ORDER BY ts",
                         (lab, node, iface, time.time() - rng - interval())).fetchall()
        c.close()
        return rows

    def comp(p, c, dt):
        d = [max(0, c[i] - p[i]) for i in range(1, 7)]
        return (d[0] * 8 / dt, d[1] * 8 / dt, d[2] / dt, d[3] / dt, d[4], d[5])
    return q, comp, ["rx_bps", "tx_bps", "rx_pps", "tx_pps", "drops", "errors"]


def history_node(lab, node, rng):
    def q():
        c = _db()
        rows = c.execute("SELECT ts, cpu_usec, mem FROM node WHERE lab=? AND node=? AND ts>=? AND cpu_usec IS NOT NULL ORDER BY ts",
                         (lab, node, time.time() - rng - interval())).fetchall()
        c.close()
        return rows

    def comp(p, c, dt):
        return (max(0.0, (c[1] - p[1]) / (dt * 1e6) * 100), c[2] or 0)
    return q, comp, ["cpu_pct", "mem_bytes"]


async def history(lab, node, iface=None, rng=3600):
    rng = max(60, min(int(rng), int(retention_h() * 3600)))
    q, comp, cols = history_iface(lab, node, iface, rng) if iface else history_node(lab, node, rng)
    rows = await asyncio.to_thread(q)
    res = _bucketed(rows, rng, comp)
    res["columns"] = ["ts"] + cols
    res["range"] = rng
    return res


def live_for(labd: dict) -> dict:
    """Live rates for one lab, plus per-link totals for the canvas overlay."""
    lv = LIVE.get(labd["name"], {"ts": None, "nodes": {}})
    links = {}
    for l in labd.get("links", []):
        a = lv["nodes"].get(l["a"], {}).get("ifaces", {}).get(l["a_if"])
        b = lv["nodes"].get(l["b"], {}).get("ifaces", {}).get(l["b_if"])
        src = a or b
        if not src:
            continue
        # a->b traffic = a's tx (or b's rx when a is a host bridge we cannot see)
        ab = a["tx_bps"] if a else b["rx_bps"]
        ba = a["rx_bps"] if a else b["tx_bps"]
        links[l["id"]] = {"ab_bps": ab, "ba_bps": ba, "total_bps": ab + ba,
                          "drops": (a or {}).get("drops", 0) + (b or {}).get("drops", 0)}
    return {"ts": lv["ts"], "interval": interval(), "nodes": lv["nodes"], "links": links}
