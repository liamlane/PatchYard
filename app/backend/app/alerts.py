"""Alerting: rules from Settings, evaluated server-side so alerts fire with the browser closed.

* After every metrics sample: device, link, CPU, memory, throughput, error/drop rules.
* Every health interval: BGP/OSPF/LDP state (vtysh JSON) and ping probes (host ping run
  inside the source device's namespace, like an IP SLA probe).
A condition must hold for the rule's `for_s` before it fires and is resolved automatically
when it clears. Labs get a grace period after they first come up, so convergence after a
deploy doesn't page you. History lives in SQLite next to the metrics.
"""
import asyncio, collections, logging, re, sqlite3, time
from . import settings, metrics, netinfo, labs, monitor
from .util import run

log = logging.getLogger("netlab.alerts")
SEV = {"info": 0, "warning": 1, "critical": 2}
PENDING: dict[str, float] = {}
ACTIVE: dict[str, int] = {}
LAB_SEEN: dict[str, float] = {}
PROTO: dict[tuple, dict] = {}
PROBE: dict[str, dict] = {}
PREV: dict[tuple, tuple] = {}
EVENTS = collections.deque(maxlen=500)
_eid = 0


def _db():
    c = sqlite3.connect(metrics.DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, rule_id TEXT, rule TEXT,
                 severity TEXT, lab TEXT, node TEXT, subject TEXT, message TEXT, state TEXT, started REAL, resolved REAL, acked INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS alerts_state ON alerts(state, started)")
    return c


def _event(kind, row):
    global _eid
    _eid += 1
    EVENTS.append({"eid": _eid, "kind": kind, "ts": time.time(), **row})


def _deployed_labs(ctrs) -> dict:
    """netlab-managed labs with at least one container: {name: lab dict}."""
    names = {lab for (lab, _n) in ctrs}
    out = {}
    for name in names:
        try:
            out[name] = labs.load(name)
        except (FileNotFoundError, ValueError, KeyError):
            pass   # CLI-deployed containerlab lab, not managed by netlab
    return out


def _in_scope(r, lab, node=None):
    return (not r.get("lab") or r["lab"] == lab) and (not r.get("node") or node is None or r["node"] == node)


def conditions(ctrs, deployed) -> list[tuple]:
    st = settings.load()
    if not st["alerts"]["enabled"]:
        return []
    rules = [r for r in st["alerts"]["rules"] if r["enabled"]]
    grace = st["alerts"]["grace_s"]
    now = time.time()
    out = []
    for name, lab in deployed.items():
        LAB_SEEN.setdefault(name, now)
        settled = now - LAB_SEEN[name] >= grace
        live = metrics.LIVE.get(name, {}).get("nodes", {})
        linkrates = metrics.live_for(lab)["links"] if live else {}
        bridges = {n["id"] for n in lab["nodes"] if (n.get("raw") or {}).get("kind") == "bridge"}
        for r in rules:
            t, rid = r["type"], r["id"]
            if t == "container_down" and settled:
                for n in lab["nodes"]:
                    if n["id"] in bridges or not _in_scope(r, name, n["id"]):
                        continue
                    c = ctrs.get((name, n["id"]))
                    if not c or c["state"] != "running":
                        out.append((f"{rid}|{name}|{n['id']}", r, name, n["id"], n["id"], f"{n['id']} is {c['state'] if c else 'missing'}"))
            elif t == "link_down" and settled:
                for l in lab["links"]:
                    if l.get("admin") == "down" or not (_in_scope(r, name, l["a"]) or _in_scope(r, name, l["b"])):
                        continue
                    sa = live.get(l["a"], {}).get("ifaces", {}).get(l["a_if"], {}).get("state")
                    sb = live.get(l["b"], {}).get("ifaces", {}).get(l["b_if"], {}).get("state")
                    if "down" in (sa, sb) or "lowerlayerdown" in (sa, sb):
                        out.append((f"{rid}|{name}|{l['id']}", r, name, l["a"], f"{l['a']} {l['a_if']} ↔ {l['b']} {l['b_if']}",
                                    f"link {l['a']} {l['a_if']} ↔ {l['b']} {l['b_if']} is down"))
            elif t in ("cpu_high", "mem_high"):
                for node, v in live.items():
                    if not _in_scope(r, name, node):
                        continue
                    val = v.get("cpu") if t == "cpu_high" else (v.get("mem") or 0) / 2**20
                    if val is not None and val > r["threshold"]:
                        what = f"CPU {val:.0f}%" if t == "cpu_high" else f"memory {val:.0f} MB"
                        out.append((f"{rid}|{name}|{node}", r, name, node, node, f"{node} {what} (threshold {r['threshold']:g})"))
            elif t == "link_util":
                for l in lab["links"]:
                    rt = linkrates.get(l["id"])
                    if rt and _in_scope(r, name, l["a"]) and rt["total_bps"] > r["threshold"] * 1e6:
                        out.append((f"{rid}|{name}|{l['id']}", r, name, l["a"], f"{l['a']}–{l['b']}",
                                    f"{l['a']}–{l['b']} at {rt['total_bps'] / 1e6:.1f} Mb/s (threshold {r['threshold']:g})"))
            elif t in ("errors_rising", "drops_rising"):
                k = "errors" if t == "errors_rising" else "drops"
                for node, v in live.items():
                    if not _in_scope(r, name, node):
                        continue
                    for ifn, i in v.get("ifaces", {}).items():
                        prev = PREV.get((name, node, ifn, k))
                        if prev is not None and i.get(k, 0) - prev >= max(1, r["threshold"]):
                            out.append((f"{rid}|{name}|{node}|{ifn}", r, name, node, f"{node} {ifn}", f"{node} {ifn}: +{i[k] - prev} {k} in the last sample"))
            elif t in ("bgp_down", "ospf_down", "ldp_down") and settled:
                proto = t.split("_")[0]
                for (ln, node), p in PROTO.items():
                    if ln != name or not _in_scope(r, name, node):
                        continue
                    for row in p.get(proto, []):
                        up = row.get("up") if proto != "ospf" else row.get("full")
                        if not up:
                            peer = row.get("peer") or row.get("router_id") or row.get("id")
                            vrf = f" vrf {row['vrf']}" if row.get("vrf") and row["vrf"] != "default" else ""
                            out.append((f"{rid}|{name}|{node}|{peer}{vrf}", r, name, node, f"{node} → {peer}{vrf}",
                                        f"{proto.upper()} {node} → {peer}{vrf} is {row.get('state') or 'down'}"))
            elif t in ("probe_loss", "probe_rtt") and r.get("lab") == name:
                res = PROBE.get(rid)
                if res:
                    bad = res["loss"] > r["threshold"] if t == "probe_loss" else (res["rtt"] is None or res["rtt"] > r["threshold"])
                    if bad:
                        out.append((f"{rid}|{name}|{r['node']}|{r['target']}", r, name, r["node"], f"{r['node']} → {r['target']}",
                                    f"probe {r['node']} → {r['target']}: {res['loss']:g}% loss, RTT {res['rtt'] if res['rtt'] is not None else '–'} ms"))
    # remember counters for the *_rising rules
    for name in deployed:
        for node, v in metrics.LIVE.get(name, {}).get("nodes", {}).items():
            for ifn, i in v.get("ifaces", {}).items():
                PREV[(name, node, ifn, "errors")] = i.get("errors", 0)
                PREV[(name, node, ifn, "drops")] = i.get("drops", 0)
    for name in list(LAB_SEEN):
        if name not in deployed:
            del LAB_SEEN[name]
    return out


def _apply(conds):
    now = time.time()
    seen = set()
    c = None
    try:
        for key, r, lab, node, subject, msg in conds:
            seen.add(key)
            PENDING.setdefault(key, now)
            if key in ACTIVE or now - PENDING[key] < r["for_s"]:
                continue
            c = c or _db()
            cur = c.execute("INSERT INTO alerts(key, rule_id, rule, severity, lab, node, subject, message, state, started) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (key, r["id"], r["name"], r["severity"], lab, node, subject, msg, "firing", now))
            ACTIVE[key] = cur.lastrowid
            _event("fired", {"id": cur.lastrowid, "rule": r["name"], "severity": r["severity"], "lab": lab, "node": node, "message": msg})
        for key in [k for k in PENDING if k not in seen]:
            del PENDING[key]
        for key in [k for k in ACTIVE if k not in seen]:
            c = c or _db()
            aid = ACTIVE.pop(key)
            c.execute("UPDATE alerts SET state='resolved', resolved=? WHERE id=?", (now, aid))
            row = c.execute("SELECT rule, severity, lab, node, message FROM alerts WHERE id=?", (aid,)).fetchone()
            if row:
                _event("resolved", {"id": aid, "rule": row[0], "severity": row[1], "lab": row[2], "node": row[3], "message": row[4]})
        if c:
            c.commit()
    finally:
        if c:
            c.close()


async def evaluate(ctrs=None):
    ctrs = ctrs if ctrs is not None else await netinfo.containers()
    conds = conditions(ctrs, _deployed_labs(ctrs))
    await asyncio.to_thread(_apply, conds)


def restore_active():
    """After a restart, reattach still-firing alerts so they resolve normally."""
    c = _db()
    for aid, key in c.execute("SELECT id, key FROM alerts WHERE state='firing'"):
        ACTIVE[key] = aid
        PENDING[key] = 0
    c.close()


async def _probe(lab, r, ctrs):
    c = ctrs.get((lab, r["node"]))
    if not c or not c["pid"]:
        PROBE.pop(r["id"], None)
        return
    rc, out, _ = await run("nsenter", "-t", str(c["pid"]), "-n", "ping", "-n", "-c", "5", "-i", "0.2", "-W", "1", r["target"], timeout=15)
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", out)
    PROBE[r["id"]] = {"ts": time.time(), "loss": float(loss.group(1)) if loss else 100.0, "rtt": float(rtt.group(1)) if rtt else None}


async def health_loop():
    while True:
        try:
            st = settings.load()
            rules = [r for r in st["alerts"]["rules"] if r["enabled"]] if st["alerts"]["enabled"] else []
            want = {r["type"] for r in rules}
            ctrs = await netinfo.containers()
            deployed = _deployed_labs(ctrs)
            if want & {"bgp_down", "ospf_down", "ldp_down"}:
                sem = asyncio.Semaphore(4)

                async def one(lab, node):
                    async with sem:
                        p = await monitor.protocols(lab, node, ["ospf", "bgp", "ldp"])
                    PROTO[(lab, node)] = {k: p[k]["rows"] for k in ("ospf", "bgp", "ldp")}
                jobs = [one(name, n["id"]) for name, lab in deployed.items() for n in lab["nodes"]
                        if n["type"] == "router" and ctrs.get((name, n["id"]), {}).get("state") == "running"]
                live_keys = {(name, n["id"]) for name, lab in deployed.items() for n in lab["nodes"]}
                for k in [k for k in PROTO if k not in live_keys]:
                    del PROTO[k]
                await asyncio.gather(*jobs, return_exceptions=True)
            for r in rules:
                if r["type"] in ("probe_loss", "probe_rtt") and r.get("lab") in deployed:
                    await _probe(r["lab"], r, ctrs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("health poll failed: %s", e)
        await asyncio.sleep(float(settings.get("monitoring.health_interval", 30)))


# ---------- API helpers ----------
def _rows(q, args=()):
    c = _db()
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(q, args)]
    c.close()
    return rows


def list_alerts(state: str = "", limit: int = 200):
    if state in ("firing", "resolved"):
        return _rows("SELECT * FROM alerts WHERE state=? ORDER BY started DESC LIMIT ?", (state, limit))
    return _rows("SELECT * FROM alerts ORDER BY (state='firing') DESC, started DESC LIMIT ?", (limit,))


def summary():
    rows = _rows("SELECT severity, lab, node, acked FROM alerts WHERE state='firing'")
    by_sev = {s: 0 for s in SEV}
    nodes: dict = {}
    for r in rows:
        if not r["acked"]:
            by_sev[r["severity"]] += 1
        cur = nodes.setdefault(r["lab"], {}).get(r["node"])
        if cur is None or SEV[r["severity"]] > SEV[cur]:
            nodes[r["lab"]][r["node"]] = r["severity"]
    return {"firing": len(rows), "unacked": sum(by_sev.values()), "by_severity": by_sev, "nodes": nodes, "last_event": _eid}


def events_after(eid: int):
    return [e for e in EVENTS if e["eid"] > eid]


def ack(aid: int | None):
    c = _db()
    if aid is None:
        c.execute("UPDATE alerts SET acked=1 WHERE state='firing'")
    else:
        c.execute("UPDATE alerts SET acked=1 WHERE id=?", (aid,))
    c.commit(); c.close()


def clear_resolved():
    c = _db()
    n = c.execute("DELETE FROM alerts WHERE state='resolved'").rowcount
    c.commit(); c.close()
    return n
