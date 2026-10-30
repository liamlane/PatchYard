"""Lab challenges: Packet Tracer-style activities with instructions and a Check button.

A challenge lives inside a lab (lab["challenge"]), so it travels with clone, export/import
and snapshots:
  {"title", "instructions", "feedback": "full"|"labels"|"score",
   "checks": [{"id", "label", "type", "points", ...type fields}]}

Checks run against the live lab, never against the design, so only what really works
scores. Every check type reads state from outside the devices (nsenter, vtysh JSON, the
IOS engine's show commands); none of them change anything.

Built-in challenges start from a template with something left for the player to do.
Admins can also publish any lab with a challenge to everyone on the server
(<home>/challenges/<id>.zip + .json); starting one gives the player their own copy.
"""
import asyncio, ipaddress, json, re, secrets, time
from pathlib import Path
from . import labs, settings, netinfo
from .util import run, check_name

TYPES = {
    "ping": "Ping succeeds (or is blocked)",
    "http": "Web page loads (and contains text)",
    "ios_show": "IOS show command output contains text",
    "ospf_full": "OSPF neighbours in FULL state",
    "bgp_up": "BGP sessions Established",
    "route": "Route in the routing table",
    "iface_ip": "Interface has an address",
    "dhcp": "Device got a DHCP address in a subnet",
    "service": "Server service is running",
}
SERVICES = ("dhcp", "dns", "http", "ftp", "tftp", "syslog", "ntp")
PROTOCOLS = ("any", "connected", "static", "ospf", "bgp")
MAX_CHECKS = 100
DIR = settings.FILE.parent / "challenges"
RESULT = "challenge-result.json"


class ChallengeError(ValueError):
    pass


def _s(v, n=200) -> str:
    return str(v if v is not None else "").strip()[:n]


def normalise(c: dict | None, node_ids: set[str]) -> dict | None:
    """Validate a challenge from the editor. Unknown devices are allowed (a device may be renamed later; the check then fails with a clear message)."""
    if not c:
        return None
    out = {"title": _s(c.get("title"), 120) or "Challenge", "instructions": str(c.get("instructions") or "")[:20000],
           "feedback": c.get("feedback") if c.get("feedback") in ("full", "labels", "score") else "full", "checks": []}
    seen = set()
    for i, k in enumerate((c.get("checks") or [])[:MAX_CHECKS]):
        t = k.get("type")
        if t not in TYPES:
            raise ChallengeError(f"check {i + 1}: unknown type {t!r}")
        cid = _s(k.get("id"), 20) or f"c{i + 1}"
        if not re.fullmatch(r"[\w-]{1,20}", cid) or cid in seen:
            cid = f"c{i + 1}-{secrets.token_hex(2)}"
        seen.add(cid)
        r = {"id": cid, "type": t, "label": _s(k.get("label"), 160), "points": max(1, min(100, int(float(k.get("points") or 1)))),
             "node": _s(k.get("node"), 31)}
        if not r["node"]:
            raise ChallengeError(f"check {i + 1}: pick a device")
        if t == "ping":
            r["target"] = _s(k.get("target"), 253)
            r["expect"] = "blocked" if k.get("expect") == "blocked" else "reach"
            if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", r["target"]):
                raise ChallengeError(f"check {i + 1}: ping needs an address or host name")
        elif t == "http":
            r["url"] = _s(k.get("url"), 300)
            r["match"] = _s(k.get("match"), 200)
            if not r["url"]:
                raise ChallengeError(f"check {i + 1}: give a URL")
        elif t == "ios_show":
            r["command"] = _s(k.get("command"), 120)
            if not re.match(r"(?i)^sh(o(w)?)?\s+\S", r["command"]):
                raise ChallengeError(f"check {i + 1}: only show commands can be checked")
            r["match"] = _s(k.get("match"), 300)
            r["regex"] = bool(k.get("regex"))
            r["expect"] = "absent" if k.get("expect") == "absent" else "contains"
            if not r["match"]:
                raise ChallengeError(f"check {i + 1}: give the text to look for")
            if r["regex"]:
                try:
                    re.compile(r["match"])
                except re.error as e:
                    raise ChallengeError(f"check {i + 1}: bad pattern ({e})")
        elif t in ("ospf_full", "bgp_up"):
            r["count"] = max(1, min(64, int(float(k.get("count") or 1))))
        elif t == "route":
            try:
                r["prefix"] = str(ipaddress.ip_network(_s(k.get("prefix"), 50), strict=False))
            except ValueError:
                raise ChallengeError(f"check {i + 1}: route needs a prefix like 192.168.3.0/24")
            r["protocol"] = k.get("protocol") if k.get("protocol") in PROTOCOLS else "any"
        elif t == "iface_ip":
            r["port"] = _s(k.get("port"), 15)
            try:
                r["cidr"] = str(ipaddress.ip_interface(_s(k.get("cidr"), 50)))
            except ValueError:
                raise ChallengeError(f"check {i + 1}: address must look like 10.0.12.1/30")
            if not re.fullmatch(r"[\w.-]{1,15}", r["port"]):
                raise ChallengeError(f"check {i + 1}: pick a port")
        elif t == "dhcp":
            r["port"] = _s(k.get("port") or "eth1", 15)
            try:
                r["subnet"] = str(ipaddress.ip_network(_s(k.get("subnet"), 50), strict=False))
            except ValueError:
                raise ChallengeError(f"check {i + 1}: subnet must look like 192.168.10.0/24")
        elif t == "service":
            r["service"] = k.get("service") if k.get("service") in SERVICES else "http"
        out["checks"].append(r)
    return out


def describe(k: dict) -> str:
    """Default wording for a check without a label."""
    t, n = k["type"], k.get("node", "?")
    return {
        "ping": lambda: f"{n} {'can' if k.get('expect') != 'blocked' else 'cannot'} ping {k.get('target')}",
        "http": lambda: f"{n} can open {k.get('url')}" + (f" (shows “{k['match']}”)" if k.get("match") else ""),
        "ios_show": lambda: f"{n}: `{k.get('command')}` {'shows' if k.get('expect') != 'absent' else 'does not show'} “{k.get('match')}”",
        "ospf_full": lambda: f"{n} has {k.get('count', 1)} OSPF neighbour(s) in FULL",
        "bgp_up": lambda: f"{n} has {k.get('count', 1)} BGP session(s) Established",
        "route": lambda: f"{n} has {({'ospf': 'an OSPF', 'bgp': 'a BGP', 'static': 'a static', 'connected': 'a connected'}).get(k.get('protocol'), 'a')} route to {k.get('prefix')}",
        "iface_ip": lambda: f"{n} {k.get('port')} is {k.get('cidr')}",
        "dhcp": lambda: f"{n} got a DHCP address in {k.get('subnet')}",
        "service": lambda: f"{n} runs {str(k.get('service', '')).upper()}",
    }[t]()


# ------------------------------------------------------------------ running checks

async def _addrs(pid: int, port: str) -> list[ipaddress.IPv4Interface]:
    rc, out, _ = await run("nsenter", "-t", str(pid), "-n", "ip", "-j", "-4", "addr", "show", "dev", port, timeout=6)
    res = []
    try:
        for i in json.loads(out) if rc == 0 and out.strip() else []:
            for a in i.get("addr_info", []):
                if a.get("family") == "inet":
                    res.append(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}"))
    except (ValueError, KeyError):
        pass
    return res


def _port_label(lab: dict, nid: str, port: str) -> str:
    from . import hardware
    n = next((x for x in lab["nodes"] if x["id"] == nid), None)
    p = next((x for x in (hardware.ports(n) or []) if x["if"] == port), None) if n else None
    return p["name"] if p else port


async def _one(lab: dict, k: dict, ctrs: dict) -> tuple[bool, str]:
    from . import devices, monitor
    name, nid, t = lab["name"], k["node"], k["type"]
    node = next((x for x in lab["nodes"] if x["id"] == nid), None)
    if not node:
        return False, f"there's no device called {nid} in this lab"
    c = ctrs.get((name, nid))
    if not c or c["state"] != "running":
        return False, f"{nid} isn't running (deploy the lab, or power it on)"
    pid = c.get("pid")
    if t == "ping":
        r = await devices.ping(name, nid, k["target"], count=2)
        reached = r["ok"]
        if k["expect"] == "blocked":
            return (not reached), ("no replies, as required" if not reached else f"{nid} can still reach {k['target']}")
        return reached, ("replies received" if reached else f"no reply from {k['target']}")
    if t == "http":
        r = await devices.browse(name, nid, k["url"])
        if not r.get("ok") or not (200 <= int(r.get("status") or 0) < 400):
            return False, r.get("error") or f"HTTP {r.get('status')}"
        if k.get("match") and k["match"].lower() not in (r.get("body") or "").lower():
            return False, f"the page loaded but doesn't show “{k['match']}”"
        return True, f"HTTP {r['status']}"
    if t == "ios_show":
        if node["type"] not in ("router", "switch") or node.get("raw"):
            return False, f"{nid} has no IOS CLI"
        from . import ios
        r = await ios.run_batch(ios.Env(name, nid), [k["command"]])
        text = r["transcript"].split("\n", 1)[1] if "\n" in r["transcript"] else ""
        found = bool(re.search(k["match"], text, re.I | re.M)) if k.get("regex") else k["match"].lower() in text.lower()
        if k["expect"] == "absent":
            return (not found), ("not present, as required" if not found else "still present")
        return found, ("found" if found else "not found in the output")
    if t in ("ospf_full", "bgp_up", "route"):
        if node["type"] != "router":
            return False, f"{nid} isn't a router"
        which = {"ospf_full": ["ospf"], "bgp_up": ["bgp"], "route": ["routes"]}[t]
        p = await monitor.protocols(name, nid, which)
        if t == "ospf_full":
            n = sum(1 for r in p["ospf"]["rows"] if r["full"])
            return n >= k["count"], f"{n} FULL (needs {k['count']})"
        if t == "bgp_up":
            n = len({r["peer"] for r in p["bgp"]["rows"] if r["up"]})
            return n >= k["count"], f"{n} Established (needs {k['count']})"
        want = ipaddress.ip_network(k["prefix"])
        proto = k.get("protocol", "any")
        rows = [r for r in p["routes"]["rows"] if r["selected"] and _net(r["prefix"]) == want]
        if proto != "any":
            rows = [r for r in rows if r["protocol"] == {"connected": "connected", "static": "static", "ospf": "ospf", "bgp": "bgp"}[proto]]
        if rows:
            return True, f"via {rows[0]['protocol']} {', '.join(rows[0]['nexthops'])}"
        anyp = [r["protocol"] for r in p["routes"]["rows"] if _net(r["prefix"]) == want]
        return False, (f"learnt only via {', '.join(sorted(set(anyp)))}" if anyp else "no such route")
    if t == "iface_ip":
        have = await _addrs(pid, k["port"])
        want = ipaddress.ip_interface(k["cidr"])
        label = _port_label(lab, nid, k["port"])
        if want in have:
            return True, f"{label} is {want}"
        return False, (f"{label} is {', '.join(map(str, have))}" if have else f"{label} has no IPv4 address")
    if t == "dhcp":
        have = await _addrs(pid, k["port"])
        net = ipaddress.ip_network(k["subnet"])
        ok = [a for a in have if a.ip in net]
        return bool(ok), (f"has {ok[0]}" if ok else (f"has {', '.join(map(str, have))}" if have else "no address yet"))
    if t == "service":
        if node["type"] != "server":
            return False, f"{nid} isn't a server"
        st = await devices.services_status(lab, node)
        on = bool(st["services"].get(k["service"]))
        return on, ("running" if on else "not running")
    return False, "unknown check"


def _net(p):
    try:
        return ipaddress.ip_network(p, strict=False)
    except ValueError:
        return None


async def check(lab: dict) -> dict:
    ch = lab.get("challenge") or {}
    checks = ch.get("checks") or []
    if not checks:
        raise ChallengeError("this lab has no challenge checks")
    ctrs = await netinfo.containers()
    sem = asyncio.Semaphore(6)

    async def one(k):
        async with sem:
            try:
                ok, detail = await asyncio.wait_for(_one(lab, k, ctrs), 40)
            except asyncio.TimeoutError:
                ok, detail = False, "timed out"
            except Exception as e:  # noqa: BLE001 - a broken check fails, it doesn't break the others
                ok, detail = False, f"couldn't check: {e}"
            return {"id": k["id"], "label": k.get("label") or describe(k), "ok": ok, "points": k["points"], "detail": detail}
    results = await asyncio.gather(*(one(k) for k in checks))
    got = sum(r["points"] for r in results if r["ok"])
    total = sum(r["points"] for r in results) or 1
    res = {"ts": time.time(), "score": got, "max": total, "pct": round(100 * got / total), "results": results}
    try:
        p = labs.workdir(lab) / RESULT
        prev = json.loads(p.read_text()) if p.exists() else {}
        res["best"] = max(res["pct"], int(prev.get("best", 0)))
        if res["pct"] == 100 and not prev.get("completed"):
            res["completed"] = time.time()
        elif prev.get("completed"):
            res["completed"] = prev["completed"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res))
    except OSError:
        pass
    return res


def last_result(lab: dict) -> dict | None:
    p = labs.workdir(lab) / RESULT
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ built-in challenges

def _strip_ospf(text: str) -> str:
    return re.sub(r"router ospf\n.*?exit\n!\n", "", text, flags=re.S)


BUILTIN = {
    "ospf-finish": {
        "title": "Finish the OSPF triangle", "level": "Beginner", "template": "ospf-triangle",
        "desc": "r3 was installed with its interfaces addressed but no routing. Bring it into OSPF area 0 so pc1 can reach the web server behind it.",
        "instructions": (
            "r3 has just been racked: its interfaces are addressed, but nobody configured routing.\n\n"
            "Your job:\n"
            "- Run OSPF process 1 on r3 with router ID 10.255.255.3.\n"
            "- Advertise its loopback, both transit links and the server LAN in area 0.\n"
            "- Don't send hellos towards the server LAN or the loopback (passive interfaces).\n\n"
            "Deploy the lab, then use r3's CLI tab. `show ip ospf neighbor` on r1 and r2 helps. "
            "When pc1's Web Browser opens http://www.lab, press Check."),
        "checks": [
            {"type": "ospf_full", "node": "r3", "count": 2, "points": 2},
            {"type": "ios_show", "node": "r3", "command": "show ip ospf", "match": "10.255.255.3", "label": "r3 uses router ID 10.255.255.3", "points": 1},
            {"type": "route", "node": "r1", "prefix": "192.168.3.0/24", "protocol": "ospf", "points": 2},
            {"type": "ios_show", "node": "r3", "command": "show running-config", "match": "passive-interface", "label": "r3 has passive interfaces", "points": 1},
            {"type": "ping", "node": "pc1", "target": "192.168.3.10", "points": 2},
            {"type": "http", "node": "pc1", "url": "http://www.lab", "match": "www.lab", "points": 2},
        ],
        "mutate": lambda nodes, links, files, ann: (files.__setitem__("r3", _strip_ospf(files["r3"])), ann.__setitem__(
            1, {**ann[1], "text": "Challenge: open the Challenge panel (flag button, top right) for the instructions."})),
    },
    "office-dhcp": {
        "title": "Get the office online", "level": "Beginner", "template": "small-office",
        "desc": "The office server's DHCP service is off and its pool is gone. Bring it back so every PC gets an address, DNS and a route out.",
        "instructions": (
            "Monday morning: nobody in the office can get on the network. The PCs are set to DHCP, but srv1 isn't handing out addresses.\n\n"
            "Your job, on srv1 → Services → DHCP:\n"
            "- Create a pool for 192.168.10.0/24 handing out .100 to .199.\n"
            "- Gateway 192.168.10.1 (the firewall), DNS 192.168.10.5 (srv1 itself).\n"
            "- Switch DHCP on.\n\n"
            "Then on each PC: Desktop → Command Prompt → `ipconfig /renew`. Check when everyone can browse intranet.office.lab "
            "and the internet site www.example.net."),
        "checks": [
            {"type": "service", "node": "srv1", "service": "dhcp", "points": 1},
            {"type": "dhcp", "node": "pc1", "subnet": "192.168.10.0/24", "points": 1},
            {"type": "dhcp", "node": "pc2", "subnet": "192.168.10.0/24", "points": 1},
            {"type": "dhcp", "node": "laptop1", "subnet": "192.168.10.0/24", "points": 1},
            {"type": "http", "node": "pc1", "url": "http://intranet.office.lab", "match": "intranet", "label": "pc1 resolves and opens intranet.office.lab", "points": 2},
            {"type": "http", "node": "pc2", "url": "http://www.example.net", "match": "example", "label": "pc2 reaches the internet through the firewall", "points": 2},
        ],
        "mutate": lambda nodes, links, files, ann: [n["props"]["services"].__setitem__("dhcp", {**n["props"]["services"]["dhcp"], "enabled": False, "pools": []})
                                                     for n in nodes if n["id"] == "srv1"],
    },
}


def list_builtin() -> list[dict]:
    from . import templates
    return [{"id": k, "kind": "builtin", "title": v["title"], "desc": v["desc"], "level": v["level"],
             "devices": templates.TEMPLATES[v["template"]]["devices"], "checks": len(v["checks"])} for k, v in BUILTIN.items()]


def start_builtin(cid: str, name: str) -> dict:
    from . import templates
    if cid not in BUILTIN:
        raise FileNotFoundError(f"no challenge {cid}")
    b = BUILTIN[cid]
    check_name(name)
    if (labs.lab_dir(name) / "lab.json").exists():
        raise FileExistsError(f"lab '{name}' already exists")
    nodes, links, files, ann = templates.TEMPLATES[b["template"]]["fn"](name)
    b["mutate"](nodes, links, files, ann)
    lab = {"name": name, "mgmt_network": labs._d("mgmt_network"), "mgmt_subnet": labs._d("mgmt_subnet"),
           "nodes": nodes, "links": links, "annotations": ann, "template": b["template"],
           "challenge": {"title": b["title"], "instructions": b["instructions"], "feedback": "full", "checks": b["checks"]}}
    for r, txt in files.items():
        labs.write_inplace(labs.lab_dir(name) / "configs" / r / "frr.conf", txt)
    labs.save(lab)
    return labs.load(name)


# ------------------------------------------------------------------ published challenges (admins → everyone)

def _meta_path(cid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", cid or ""):
        raise FileNotFoundError("no such challenge")
    return DIR / f"{cid}.json"


def list_published() -> list[dict]:
    out = []
    for f in sorted(DIR.glob("*.json")) if DIR.is_dir() else []:
        try:
            out.append({**json.loads(f.read_text()), "kind": "published"})
        except ValueError:
            continue
    return sorted(out, key=lambda x: -x.get("created", 0))


def publish(lab: dict, by: str, desc: str = "", level: str = "") -> dict:
    from . import labmgmt
    ch = lab.get("challenge") or {}
    if not ch.get("checks"):
        raise ChallengeError("add at least one check before publishing")
    cid = secrets.token_hex(6)
    DIR.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in lab.items() if k not in ("owner", "trusted", "sandbox_subnet", "workdir")}
    (DIR / f"{cid}.zip").write_bytes(labmgmt.export_zip(clean))
    meta = {"id": cid, "title": ch.get("title") or lab["name"], "desc": _s(desc, 400), "level": _s(level, 30), "by": by,
            "created": time.time(), "devices": len(lab.get("nodes", [])), "checks": len(ch["checks"]), "from_lab": lab["name"]}
    (DIR / f"{cid}.json").write_text(json.dumps(meta))
    return meta


def unpublish(cid: str):
    m = _meta_path(cid)
    m.unlink(missing_ok=True)
    m.with_suffix(".zip").unlink(missing_ok=True)


def start_published(cid: str, name: str) -> dict:
    from . import labmgmt
    m = _meta_path(cid)
    if not m.exists():
        raise FileNotFoundError("no such challenge")
    return labmgmt.import_zip(name, m.with_suffix(".zip").read_bytes())
