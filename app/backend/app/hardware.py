"""Device hardware: chassis models, slots, modules and the ports they provide.

A device with `props.chassis` has a fixed set of ports named the IOS way
(GigabitEthernet0/0/0, Fa0/1, Te1/1/1, Serial0/1/0 ...). Inside the container every
port is a Linux interface called ethN, because Linux interface names cannot contain
'/'. The mapping is fixed per chassis so it never moves when a module is added:

    on-board ports      eth1, eth2, ...            in the order the model lists them
    slot k (0-based)    eth{100*(k+1)+p+1}         p = port number within the module

Devices without a chassis ("generic", every lab made before 0.10) keep free-form
eth1, eth2 ... ports added as cables are plugged in.

Port types carry the cable media they accept and a speed. Speeds are enforced on
the link (netem rate on both ends) at the lower of the two ends; 10G is left
uncapped because a veth pair can't usefully go faster anyway.
"""
import re

PORT_TYPES = {
    "fa":     {"long": "FastEthernet",       "short": "Fa", "media": "copper", "kbit": 100_000,   "label": "100M copper"},
    "gi":     {"long": "GigabitEthernet",    "short": "Gi", "media": "copper", "kbit": 1_000_000, "label": "1G copper"},
    "gi-sfp": {"long": "GigabitEthernet",    "short": "Gi", "media": "fibre",  "kbit": 1_000_000, "label": "1G SFP"},
    "te":     {"long": "TenGigabitEthernet", "short": "Te", "media": "fibre",  "kbit": None,      "label": "10G SFP+"},
    "se":     {"long": "Serial",             "short": "Se", "media": "serial", "kbit": 1_544,     "label": "Serial T1 (1.544M, emulated)"},
}
MEDIA = ("copper", "fibre", "serial")

# onboard: (port type, name prefix, first number, count)
CHASSIS = {
    "router-2s": {"type": "router", "label": "Branch router, 2 slots",
                  "desc": "2× GE copper on board, two module slots. Small branch / CPE.",
                  "onboard": [("gi", "0/0/", 0, 2)], "slots": [{"id": "0/1", "family": "router"}, {"id": "0/2", "family": "router"}]},
    "router-3s": {"type": "router", "label": "ISR router, 3 slots",
                  "desc": "2× GE copper + 2× GE SFP on board, three module slots.",
                  "onboard": [("gi", "0/0/", 0, 2), ("gi-sfp", "0/0/", 2, 2)],
                  "slots": [{"id": "0/1", "family": "router"}, {"id": "0/2", "family": "router"}, {"id": "0/3", "family": "router"}]},
    "router-edge": {"type": "router", "label": "Edge router, 10G",
                    "desc": "4× 10GE SFP+ and 4× GE copper on board, two module slots. PE / aggregation.",
                    "onboard": [("te", "0/0/", 0, 4), ("gi", "0/0/", 4, 4)],
                    "slots": [{"id": "0/1", "family": "router"}, {"id": "0/2", "family": "router"}]},
    "switch-fa24": {"type": "switch", "label": "Fast Ethernet switch 24+2",
                    "desc": "24× FastEthernet + 2× GE copper uplinks. The classic lab access switch.",
                    "onboard": [("fa", "0/", 1, 24), ("gi", "0/", 1, 2)], "slots": []},
    "switch-8": {"type": "switch", "label": "Compact switch 8+2",
                 "desc": "8× GE copper + 2× GE SFP. Desk / small office.",
                 "onboard": [("gi", "1/0/", 1, 8), ("gi-sfp", "1/0/", 9, 2)], "slots": []},
    "switch-24": {"type": "switch", "label": "Access switch 24+4",
                  "desc": "24× GE copper, one uplink module slot.",
                  "onboard": [("gi", "1/0/", 1, 24)], "slots": [{"id": "1/1", "family": "uplink"}],
                  "default_modules": {"1/1": "up-4gsfp"}},
    "switch-48": {"type": "switch", "label": "Access switch 48+4",
                  "desc": "48× GE copper, one uplink module slot.",
                  "onboard": [("gi", "1/0/", 1, 48)], "slots": [{"id": "1/1", "family": "uplink"}],
                  "default_modules": {"1/1": "up-4gsfp"}},
    "switch-dist": {"type": "switch", "label": "Distribution switch 24× 10G",
                    "desc": "24× 10GE SFP+, one uplink module slot.",
                    "onboard": [("te", "1/0/", 1, 24)], "slots": [{"id": "1/1", "family": "uplink"}]},
}

CHASSIS["fw-8"] = {"type": "firewall", "label": "Firewall, 8 ports",
                   "desc": "8× GE copper (Gi0/0-Gi0/7). Zone-based stateful firewall with NAT.",
                   "onboard": [("gi", "0/", 0, 8)], "slots": []}
CHASSIS["fw-4sfp"] = {"type": "firewall", "label": "Firewall, 6 GE + 2 SFP",
                      "desc": "6× GE copper + 2× GE SFP (Gi0/6-7 fibre).",
                      "onboard": [("gi", "0/", 0, 6), ("gi-sfp", "0/", 6, 2)], "slots": []}

# ports: (port type, count). Router modules number from 0, switch uplinks from 1 (like IOS).
MODULES = {
    "nim-4ge":  {"family": "router", "label": "4× GE copper",       "ports": [("gi", 4)]},
    "nim-8ge":  {"family": "router", "label": "8× GE copper",       "ports": [("gi", 8)]},
    "nim-2sfp": {"family": "router", "label": "2× GE SFP",          "ports": [("gi-sfp", 2)]},
    "nim-1te":  {"family": "router", "label": "1× 10GE SFP+",       "ports": [("te", 1)]},
    "nim-2t":   {"family": "router", "label": "2× serial T1/E1",    "ports": [("se", 2)]},
    "up-4gsfp": {"family": "uplink", "label": "4× GE SFP uplink",   "ports": [("gi-sfp", 4)]},
    "up-2te":   {"family": "uplink", "label": "2× 10GE SFP+ uplink", "ports": [("te", 2)]},
    "up-4te":   {"family": "uplink", "label": "4× 10GE SFP+ uplink", "ports": [("te", 4)]},
    # host network cards (PCs and servers): one port, always the Linux eth1
    "nic-fe":   {"family": "nic", "label": "Fast Ethernet NIC (copper)", "ports": [("fa", 1)]},
    "nic-ge":   {"family": "nic", "label": "Gigabit NIC (copper)", "ports": [("gi", 1)]},
    "nic-sfp":  {"family": "nic", "label": "Gigabit SFP NIC (fibre)", "ports": [("gi-sfp", 1)]},
    "nic-10g":  {"family": "nic", "label": "10G SFP+ NIC", "ports": [("te", 1)]},
}
FAMILY_START = {"router": 0, "uplink": 1, "nic": 0}
DEFAULT_CHASSIS = {"router": "router-3s", "switch": "switch-24", "firewall": "fw-8", "pc": "pc", "server": "server"}
CHASSIS["pc"] = {"type": "pc", "label": "PC", "desc": "Desktop PC with one swappable network card (FastEthernet0 by default).",
                 "onboard": [], "slots": [{"id": "0", "family": "nic"}], "default_modules": {"0": "nic-fe"}}
CHASSIS["laptop"] = {"type": "pc", "label": "Laptop", "desc": "Laptop with one swappable network card (Gigabit copper by default).",
                     "onboard": [], "slots": [{"id": "0", "family": "nic"}], "default_modules": {"0": "nic-ge"}}
CHASSIS["server"] = {"type": "server", "label": "Server", "desc": "Rack server with one swappable network card (GigabitEthernet0 by default).",
                     "onboard": [], "slots": [{"id": "0", "family": "nic"}], "default_modules": {"0": "nic-ge"}}

# Pluggable optics: an SFP/SFP+ port takes a fibre transceiver (default) or a copper one
# (1000BASE-T / 10GBASE-T, like a GLC-T), so copper devices can plug into fibre switches.
TRANSCEIVERS = {"fibre": "fibre (SX/LR)", "copper": "copper (RJ45 SFP)"}
SFP_TYPES = ("gi-sfp", "te")


def catalogue() -> dict:
    return {"port_types": PORT_TYPES, "chassis": CHASSIS, "modules": MODULES, "defaults": DEFAULT_CHASSIS, "transceivers": TRANSCEIVERS}


def chassis_of(node: dict):
    if node.get("raw"):
        return None
    return CHASSIS.get((node.get("props") or {}).get("chassis") or "")


def _port(ifn, ptype, name, slot=None, module=None, sfp=None):
    t = PORT_TYPES[ptype]
    media, label = t["media"], t["label"]
    if ptype in SFP_TYPES and sfp == "copper":
        media, label = "copper", t["label"] + " with copper transceiver"
    return {"if": ifn, "name": t["short"] + name, "long": t["long"] + name, "type": ptype, "media": media, "label": label,
            "kbit": t["kbit"], "slot": slot, "module": module, "sfp": (sfp or "fibre") if ptype in SFP_TYPES else None}


def ports(node: dict) -> list[dict] | None:
    """Every physical port of a chassis device (cabled or not); None for generic devices."""
    ch = chassis_of(node)
    if not ch:
        return None
    out, i = [], 0
    sfp = (node.get("props") or {}).get("sfp") or {}
    for ptype, prefix, first, count in ch["onboard"]:
        for n in range(count):
            i += 1
            out.append(_port(f"eth{i}", ptype, f"{prefix}{first + n}", sfp=sfp.get(f"eth{i}")))
    mods = (node.get("props") or {}).get("modules") or {}
    for k, slot in enumerate(ch["slots"]):
        mid = mods.get(slot["id"])
        m = MODULES.get(mid or "")
        if not m:
            continue
        p = 0
        for ptype, count in m["ports"]:
            for _ in range(count):
                if m["family"] == "nic":                      # host NIC: FastEthernet0 = eth1
                    ifn, name = f"eth{p + 1}", f"{p}"
                else:
                    ifn, name = f"eth{100 * (k + 1) + p + 1}", f"{slot['id']}/{FAMILY_START[m['family']] + p}"
                out.append(_port(ifn, ptype, name, slot["id"], mid, sfp=sfp.get(ifn)))
                p += 1
    return out


def port_map(node: dict) -> dict:
    return {p["if"]: p for p in (ports(node) or [])}


def _loop_name(ifn):
    if ifn == "lo":
        return 0
    m = re.fullmatch(r"loop(\d+)", ifn)
    return int(m.group(1)) if m else None


def display(node: dict, ifn: str, long: bool = False) -> str:
    """eth101 -> Gi0/1/0 (or GigabitEthernet0/1/0); eth1.10 -> Gi0/0/0.10; lo -> Loopback0.
    Generic devices keep ethN on the canvas; long=True gives Ethernet N for the IOS CLI."""
    base, dot, sub = ifn.partition(".")
    lo = _loop_name(base)
    if lo is not None:
        return f"{'Loopback' if long else 'Lo'}{lo}"
    if base.startswith("vlan") and base[4:].isdigit():
        return f"Vlan{base[4:]}"
    p = port_map(node).get(base)
    if p:
        name = p["long"] if long else p["name"]
    elif long and re.fullmatch(r"eth\d+", base):
        name = f"Ethernet{base[3:]}"
    else:
        name = base
    return name + (f".{sub}" if dot else "")


_KINDS = [("tengigabitethernet", "te"), ("gigabitethernet", "gi"), ("fastethernet", "fa"), ("serial", "se"),
          ("loopback", "lo"), ("vlan", "vlan"), ("ethernet", "eth")]


def parse_ifname(text: str):
    """'g0/1/0', 'Gi 0/1/0', 'GigabitEthernet0/1/0.10', 'lo0', 'eth3' -> (kind, number, sub)."""
    s = text.replace(" ", "").lower()
    m = re.fullmatch(r"([a-z-]+)([0-9][0-9/]*)(?:\.(\d+))?", s)
    if not m:
        return None
    word, num, sub = m.groups()
    if word == "eth" and "/" not in num:
        return ("eth", num, sub)
    hits = {k for w, k in _KINDS if w.startswith(word)}
    if word.startswith("te"):
        hits = {"te"} if "tengigabitethernet".startswith(word) else set()
    if len(hits) != 1:
        return None
    return (hits.pop(), num, sub)


def resolve(node: dict, text: str) -> str:
    """IOS-style interface name -> Linux ifname on this device. Raises ValueError."""
    p = parse_ifname(text)
    if not p:
        raise ValueError(f"invalid interface '{text}'")
    kind, num, sub = p
    if kind == "lo":
        if not num.isdigit():
            raise ValueError(f"invalid loopback '{text}'")
        base = "lo" if int(num) == 0 else f"loop{int(num)}"
    elif kind == "vlan":
        if not num.isdigit() or not 1 <= int(num) <= 4094:
            raise ValueError(f"invalid VLAN interface '{text}'")
        base = f"vlan{int(num)}"
    elif kind == "eth":
        base = f"eth{int(num)}" if num.isdigit() else None
        if base is None:
            raise ValueError(f"invalid interface '{text}'")
        pm = port_map(node)
        if pm and base not in pm:
            raise ValueError(f"{node['id']} has no port {base}")
    else:
        short = PORT_TYPES["gi" if kind == "gi" else kind]["short"]
        want = f"{short}{num}"
        hit = [x for x in (ports(node) or []) if x["name"].lower() == want.lower()]
        if not hit:
            raise ValueError(f"{node['id']} has no interface {want}")
        base = hit[0]["if"]
    return base + (f".{int(sub)}" if sub else "")


def validate(node: dict, links: list[dict] | None = None):
    """Chassis/module sanity, and every cabled port must exist on the hardware."""
    props = node.get("props") or {}
    cid = props.get("chassis")
    if not cid:
        return
    ch = CHASSIS.get(cid)
    if not ch:
        raise ValueError(f"{node['id']}: unknown model '{cid}'")
    if ch["type"] != node["type"]:
        raise ValueError(f"{node['id']}: {ch['label']} is a {ch['type']}, not a {node['type']}")
    slots = {s["id"]: s for s in ch["slots"]}
    for sid, mid in (props.get("modules") or {}).items():
        if not mid:
            continue
        if sid not in slots:
            raise ValueError(f"{node['id']}: {ch['label']} has no slot {sid}")
        m = MODULES.get(mid)
        if not m:
            raise ValueError(f"{node['id']}: unknown module '{mid}'")
        if m["family"] != slots[sid]["family"]:
            raise ValueError(f"{node['id']}: {m['label']} doesn't fit slot {sid}")
    for ifn, kind in (props.get("sfp") or {}).items():
        if kind not in TRANSCEIVERS:
            raise ValueError(f"{node['id']}: unknown transceiver '{kind}'")
    if links is not None:
        pm = port_map(node)
        for l in links:
            for s in ("a", "b"):
                if l[s] == node["id"]:
                    ifn = l[f"{s}_if"]
                    if ifn not in pm:
                        raise ValueError(f"{node['id']}: cable on {ifn}, which this hardware doesn't have; unplug it first")


def end_kbit(node: dict, ifn: str):
    p = port_map(node).get(ifn)
    return p["kbit"] if p else None


def link_kbit(lab: dict, link: dict):
    """The speed the link runs at: the slower end. None = not capped."""
    nodes = {n["id"]: n for n in lab["nodes"]}
    v = [end_kbit(nodes[link[s]], link[f"{s}_if"]) for s in ("a", "b") if link[s] in nodes]
    v = [x for x in v if x]
    return min(v) if v else None


def fmt_speed(kbit) -> str:
    if not kbit:
        return "10 Gb/s"
    if kbit >= 1_000_000:
        return f"{kbit // 1_000_000} Gb/s"
    if kbit >= 1000:
        return f"{kbit / 1000:g} Mb/s"
    return f"{kbit} kb/s"
