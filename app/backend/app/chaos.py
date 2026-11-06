"""Chaos mode: break things at random for a while and score how well the network copes.

A run picks a cable every `every` seconds (± a third), and either takes it down or adds an
impairment (heavy loss, or delay with jitter) for a random 10-60 s, then puts it back. Every 15 s
it pings every PC/server from every other one. The score is the share of samples in which the
host pairs that could reach each other before the run still could: 100 means nobody noticed.

Cables can be excluded (the uplink you don't want to lose). Stopping, or the end of the run,
restores whatever is broken at that moment. One run per lab at a time.
"""
import asyncio, random, secrets, time
from . import impair, reach, hardware

JOBS: dict[str, dict] = {}            # lab -> job
SAMPLE_EVERY = 15


def public(j: dict | None) -> dict | None:
    return {k: v for k, v in j.items() if not k.startswith("_")} if j else None


def get(lab: str) -> dict | None:
    return public(JOBS.get(lab))


def _label(lab: dict, link: dict) -> str:
    nodes = {n["id"]: n for n in lab["nodes"]}
    return f"{link['a']} {hardware.display(nodes[link['a']], link['a_if'])} ↔ {link['b']} {hardware.display(nodes[link['b']], link['b_if'])}"


def start(lab: dict, minutes: float, every: float, kinds: list[str], exclude: list[str]) -> dict:
    cur = JOBS.get(lab["name"])
    if cur and cur["state"] == "running":
        raise ValueError("chaos is already running in this lab")
    kinds = [k for k in kinds if k in ("down", "loss", "delay")] or ["down"]
    links = [l for l in lab["links"] if l["id"] not in set(exclude) and l.get("admin") != "down"]   # leave cables you shut alone
    if not links:
        raise ValueError("no cables left to break (all excluded)")
    minutes = max(1.0, min(120.0, float(minutes)))
    every = max(15.0, min(600.0, float(every)))
    j = {"id": secrets.token_hex(4), "lab": lab["name"], "state": "running", "started": time.time(),
         "ends": time.time() + minutes * 60, "every": every, "kinds": kinds, "exclude": list(exclude),
         "events": [], "samples": [], "score": None, "baseline_pairs": None, "current": None}
    j["_task"] = asyncio.ensure_future(_run(j, lab, links))
    JOBS[lab["name"]] = j
    return public(j)


async def _restore(lab: dict, j: dict):
    cur = j.get("_fault")
    if not cur:
        return
    link, kind = cur
    try:
        if kind == "down":
            await impair.set_state(lab, link, True)
        else:
            await impair.apply(lab, link, link.get("impair"))           # back to whatever it had before
    finally:
        j["_fault"], j["current"] = None, None
        j["events"].append({"t": time.time(), "what": "restored", "link": _label(lab, link)})


def _pairs(rc: dict) -> set[str]:
    return {f"{s}→{d}" for s, row in rc["results"].items() for d, v in row.items() if v is not None}


async def _sampler(j: dict, lab: dict):
    base = None
    while True:
        rc = await reach.check(lab)
        ok = _pairs(rc)
        if base is None:
            base = ok
            j["baseline_pairs"] = len(base)
        if base:
            kept = len(base & ok)
            j["samples"].append({"t": time.time(), "ok": kept, "of": len(base), "broken": sorted(base - ok)[:10],
                                 "fault": j.get("current")})
            j["score"] = round(100 * sum(s["ok"] / s["of"] for s in j["samples"]) / len(j["samples"]), 1)
            j["samples"] = j["samples"][-400:]
        await asyncio.sleep(SAMPLE_EVERY)


async def _run(j: dict, lab: dict, links: list[dict]):
    sampler = asyncio.ensure_future(_sampler(j, lab))
    try:
        await asyncio.sleep(SAMPLE_EVERY + 2)            # a clean sample first, so the score has a baseline
        while time.time() < j["ends"] and j["state"] == "running":
            link = random.choice(links)
            kind = random.choice(j["kinds"])
            hold = random.randint(10, 60)
            if kind == "down":
                await impair.set_state(lab, link, False)
                what = f"down for {hold}s"
            elif kind == "loss":
                await impair.apply(lab, link, {"loss": 40, "dir": "both"})
                what = f"40% loss for {hold}s"
            else:
                await impair.apply(lab, link, {"delay": 300, "jitter": 100, "dir": "both"})
                what = f"300 ms delay ±100 ms for {hold}s"
            j["_fault"] = (link, kind)
            j["current"] = {"link": _label(lab, link), "what": what}
            j["events"].append({"t": time.time(), "what": what, "link": _label(lab, link)})
            await asyncio.sleep(min(hold, max(0, j["ends"] - time.time())))
            await _restore(lab, j)
            gap = j["every"] * random.uniform(0.67, 1.33)
            await asyncio.sleep(max(0, min(gap - hold, j["ends"] - time.time())))
        j["state"] = "done"
    except asyncio.CancelledError:
        j["state"] = "stopped"
    except Exception as e:  # noqa: BLE001
        j["state"], j["error"] = "failed", str(e)
    finally:
        sampler.cancel()
        try:
            await _restore(lab, j)
        except Exception:  # noqa: BLE001
            pass
        j["finished"] = time.time()


def stop(lab: str) -> dict | None:
    j = JOBS.get(lab)
    if j and j["state"] == "running" and j.get("_task"):
        j["_task"].cancel()
    return public(j)
