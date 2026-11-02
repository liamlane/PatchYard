"""Build a lab from NetBox: devices, cables and IP addresses become a working topology.

Reads (read-only API token): dcim/devices (optionally one site or tag), dcim/cables,
dcim/interfaces, ipam/ip-addresses. Device roles decide the type: anything with
router/firewall/switch/server/pc in its role or device-type slug maps to that device;
everything else becomes a server. Cabled interfaces become ports (in NetBox's order) and
routers get their interface addresses. Admins only: the server makes the HTTP requests.
"""
import ipaddress, json, re, urllib.parse, urllib.request, urllib.error
from . import labs

TYPES = (("firewall", "firewall"), ("router", "router"), ("switch", "switch"), ("server", "server"),
         ("pc", "pc"), ("workstation", "pc"), ("laptop", "pc"), ("desktop", "pc"))


class NetboxError(ValueError):
    pass


def _get(base: str, token: str, path: str, params: dict | None = None) -> list[dict]:
    out, url = [], f"{base}/api/{path}/?" + urllib.parse.urlencode({"limit": 1000, **(params or {})})
    for _ in range(50):
        req = urllib.request.Request(url, headers={"Authorization": f"Token {token}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise NetboxError(f"NetBox answered {e.code} for {path}" + (": check the token" if e.code in (401, 403) else ""))
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise NetboxError(f"couldn't reach NetBox ({e})")
        out += d.get("results", [])
        url = d.get("next")
        if not url:
            break
    return out


def _kind(dev: dict) -> str:
    hay = " ".join(str(x).lower() for x in ((dev.get("role") or dev.get("device_role") or {}).get("slug"),
                                              (dev.get("device_type") or {}).get("slug"), dev.get("name")))
    return next((t for k, t in TYPES if k in hay), "server")


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")[:31]
    return s or "dev"


def build(name: str, base: str, token: str, site: str = "", tag: str = "", fetch=_get) -> dict:
    labs.check_name(name)
    if (labs.lab_dir(name) / "lab.json").exists():
        raise FileExistsError(f"lab '{name}' already exists")
    base = base.strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(/[\w./-]*)?", base):
        raise NetboxError("NetBox URL must look like https://netbox.example.com")
    params = {}
    if site:
        params["site"] = site
    if tag:
        params["tag"] = tag
    devs = fetch(base, token, "dcim/devices", params)
    if not devs:
        raise NetboxError("no devices matched")
    if len(devs) > 60:
        raise NetboxError(f"{len(devs)} devices matched: narrow it down with a site or tag (60 at most)")
    by_id = {d["id"]: d for d in devs}
    ids = {}
    for d in devs:
        s = _slug(d.get("name") or f"dev{d['id']}")
        while s in ids.values():
            s = (s + "x")[:31]
        ids[d["id"]] = s
    ifaces = {}
    for d in devs:
        for i in fetch(base, token, "dcim/interfaces", {"device_id": d["id"]}):
            ifaces[i["id"]] = {**i, "_dev": d["id"]}
    addrs = {}
    for a in fetch(base, token, "ipam/ip-addresses", {"device_id": ",".join(str(i) for i in by_id)} if len(by_id) < 40 else {}):
        if a.get("assigned_object_type") == "dcim.interface" and a.get("assigned_object_id") in ifaces:
            addrs.setdefault(a["assigned_object_id"], []).append(a["address"])
    ports, links, report = {}, [], []

    def port(iid):
        i = ifaces[iid]
        m = ports.setdefault(i["_dev"], {})
        if iid not in m:
            m[iid] = f"eth{len(m) + 1}"
        return m[iid]
    for c in fetch(base, token, "dcim/cables", {"device_id": ",".join(str(i) for i in by_id)} if len(by_id) < 40 else {}):
        ta = [t for t in (c.get("a_terminations") or []) if t.get("object_type") == "dcim.interface"]
        tb = [t for t in (c.get("b_terminations") or []) if t.get("object_type") == "dcim.interface"]
        if not ta and c.get("termination_a_type") == "dcim.interface":            # NetBox < 3.3
            ta, tb = [{"object_id": c["termination_a_id"]}], [{"object_id": c["termination_b_id"]}]
        if not ta or not tb:
            continue
        a, b = ta[0]["object_id"], tb[0]["object_id"]
        if a not in ifaces or b not in ifaces:
            continue
        ia, ib = ifaces[a], ifaces[b]
        media = "fibre" if re.search(r"(sfp|smf|mmf|fiber|fibre|lc|sc)", f"{c.get('type') or ''} {ia.get('type', {}).get('value', '')}", re.I) else "copper"
        links.append({"id": f"l{len(links) + 1}", "a": ids[ia["_dev"]], "a_if": port(a), "b": ids[ib["_dev"]], "b_if": port(b), "media": media})
        report.append(f"cable {by_id[ia['_dev']]['name']} {ia['name']} ↔ {by_id[ib['_dev']]['name']} {ib['name']}")
    nodes = []
    kinds = {did: _kind(d) for did, d in by_id.items()}
    for j, (did, d) in enumerate(by_id.items()):
        t = kinds[did]
        props = {}
        nid = ids[did]
        if t == "router":
            L = ["frr defaults traditional", f"hostname {nid}", "service integrated-vtysh-config", "!"]
            for iid, eth in sorted((ports.get(did) or {}).items(), key=lambda x: int(x[1][3:])):
                L += [f"interface {eth}", f" description [{ifaces[iid]['name']}] {ifaces[iid].get('description') or ''}".rstrip()]
                L += [f" ip address {a}" for a in addrs.get(iid, []) if ipaddress.ip_interface(a).version == 4]
                L += ["exit", "!"]
            labs.write_inplace(labs.lab_dir(name) / "configs" / nid / "frr.conf", "\n".join(L) + "\n")
        elif t in ("pc", "server"):
            first = next((a for iid in (ports.get(did) or {}) for a in addrs.get(iid, [])), None)
            props = {"ip_mode": "static", "ip": first, "gateway": ""} if first else {"ip_mode": "dhcp"}
        elif t == "firewall":
            fi = {}
            for iid, eth in (ports.get(did) or {}).items():
                a = next(iter(addrs.get(iid, [])), None)
                if a:
                    fi[eth] = {"ip": a, "zone": "inside", "description": ifaces[iid]["name"]}
            props = {"interfaces": fi}
        nodes.append({"id": nid, "type": t, "x": 160 + 220 * (j % 6), "y": 80 + 200 * (j // 6), "props": props})
    lab = {"name": name, "mgmt_network": labs._d("mgmt_network"), "mgmt_subnet": labs._d("mgmt_subnet"), "nodes": nodes, "links": links,
           "imported_from": f"netbox:{base}", "annotations": [{"id": "ann-nb", "kind": "note", "x": 160, "y": -120, "w": 320, "h": 100, "color": "yellow",
                                                              "text": f"Imported from NetBox ({len(nodes)} devices, {len(links)} cables). Real interface names are in the port descriptions."}]}
    labs.save(lab)
    return {"lab": labs.load(name), "report": report, "devices": len(nodes), "links": len(links)}
