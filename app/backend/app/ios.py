"""IOS-style CLI for netlab routers and switches.

Routers: each command is translated to FRR and run with vtysh in the context you are
in (interface, router ospf, router bgp ...). Output from FRR is translated back:
Linux port names become IOS names (eth101 -> GigabitEthernet0/1/0), prefixes become
address + mask. Anything this layer doesn't know is passed to FRR as typed (with
interface names translated), so FRR-only commands still work.

Switches: netlab switches are Linux bridges driven by the device's props, so switch
commands edit those props (the same ones the Config tab edits) and re-apply the
switch script. `show` output is built from props plus live kernel state.

Not everything IOS does exists here. Commands that would be accepted by IOS but have
no effect in netlab (line passwords, service ..., ip domain-lookup) are stored and shown
in the running config but change nothing; commands for features netlab doesn't have
(packet ACLs on interfaces, NAT, EtherChannel, routed switch ports) say so instead of
pretending.
"""
import asyncio, hashlib, ipaddress, json, re, time
from . import hardware

# --------------------------------------------------------------------------- helpers

def m2len(mask: str) -> int:
    n = int(ipaddress.IPv4Address(mask))
    b = f"{n:032b}"
    if "01" in b:
        raise ValueError(f"bad mask {mask}")
    return b.count("1")


def w2len(wild: str) -> int:
    n = int(ipaddress.IPv4Address(wild)) ^ 0xFFFFFFFF
    return m2len(str(ipaddress.IPv4Address(n)))


def len2mask(n: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{n}").netmask)


def len2wild(n: int) -> str:
    return str(ipaddress.IPv4Network(f"0.0.0.0/{n}").hostmask)


def classful(a: str) -> int:
    o = int(a.split(".")[0])
    return 8 if o < 128 else 16 if o < 192 else 24


def is_ip(s: str) -> bool:
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


def vlan_set(text: str) -> list[int]:
    out = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        a, b = int(a), int(b or a)
        if not (1 <= a <= b <= 4094):
            raise ValueError(f"bad VLAN list {text}")
        out += range(a, b + 1)
    return sorted(set(out))


def vlan_text(vs) -> str:
    vs, runs = sorted(set(vs)), []
    for v in vs:
        if runs and v == runs[-1][1] + 1:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs)


def pipe_filter(text: str, op: str, rx) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    if op == "include":
        return "\n".join(l for l in lines if rx.search(l))
    if op == "exclude":
        return "\n".join(l for l in lines if not rx.search(l))
    if op == "count":
        return f"Number of lines which match regexp = {sum(1 for l in lines if rx.search(l))}"
    if op == "begin":
        i = next((k for k, l in enumerate(lines) if rx.search(l)), len(lines))
        return "\n".join(lines[i:])
    out, take = [], False                       # section: matching top-level lines plus their children
    for l in lines:
        if l and not l[0].isspace():
            take = bool(rx.search(l))
        elif not l.strip():
            take = take and False
        if take:
            out.append(l)
    return "\n".join(out)


class CmdError(Exception):
    """Printed to the console as-is (IOS-style '% ...')."""


# --------------------------------------------------------------------------- grammar

PARAM_HELP = {"IF": "Interface name", "A.B.C.D": "IP address", "MASK": "IP subnet mask", "WILD": "Wildcard bits",
              "WORD": "Name", "LINE": "Text", "AREA": "OSPF area ID as a decimal or dotted value", "VLANS": "VLAN IDs, e.g. 10,20-30",
              "NUM": "Number", "HOST": "IP address"}


class GNode:
    __slots__ = ("kw", "params", "handler", "help")

    def __init__(self):
        self.kw, self.params, self.handler, self.help = {}, [], None, {}


class Grammar:
    def __init__(self, helps=None):
        self.root = GNode()
        self.helps = helps or {}

    def add(self, pattern: str, handler, **helps):
        node = self.root
        for tok in pattern.split():
            if tok[0].isupper() or tok[0] == "<":
                hit = next((c for p, c in node.params if p == tok), None)
                if not hit:
                    hit = GNode()
                    node.params.append((tok, hit))
                node = hit
            else:
                if tok not in node.kw:
                    node.kw[tok] = GNode()
                    node.help[tok] = helps.get(tok.replace("-", "_")) or self.helps.get(tok, "")
                node = node.kw[tok]
        node.handler = handler
        return self


def _consume(ptype: str, toks: list[str], i: int):
    t = toks[i]
    if ptype == "LINE":
        return len(toks) - i, " ".join(toks[i:])
    if ptype == "IF":
        if i + 1 < len(toks) and re.fullmatch(r"[A-Za-z-]+", t) and toks[i + 1][:1].isdigit():
            v = t + toks[i + 1]
            if hardware.parse_ifname(v) or _PO.fullmatch(v):
                return 2, v
        if hardware.parse_ifname(t) or t.lower() == "null0" or _PO.fullmatch(t):
            return 1, t
        return 0, None
    if ptype in ("A.B.C.D", "MASK", "WILD", "HOST"):
        return (1, t) if is_ip(t) else (0, None)
    if ptype == "AREA":
        return (1, t) if (t.isdigit() or is_ip(t)) else (0, None)
    if ptype == "VLANS":
        return (1, t) if re.fullmatch(r"[0-9][0-9,\-]*", t) else (0, None)
    if ptype == "WORD":
        return 1, t
    m = re.fullmatch(r"<(\d+)-(\d+)>", ptype)
    if m:
        if t.isdigit() and int(m.group(1)) <= int(t) <= int(m.group(2)):
            return 1, int(t)
        return 0, None
    return 0, None


_PO = re.compile(r"(?i)po(?:rt-channel)?(\d+)")


def match(g: Grammar, toks: list[str]):
    """-> ("ok", handler, args) | (kind, pos) with kind in invalid/ambiguous/incomplete."""
    best = ["invalid", 0]

    def fail(kind, pos):
        if pos > best[1] or (pos == best[1] and kind != "invalid"):
            best[0], best[1] = kind, pos
        return None

    def walk(node, i, args):
        if i == len(toks):
            return (node.handler, args) if node.handler else fail("incomplete", i)
        t = toks[i].lower()
        if t in node.kw:
            r = walk(node.kw[t], i + 1, args)
            if r:
                return r
        else:
            pre = [k for k in node.kw if k.startswith(t)]
            if len(pre) > 1:
                return fail("ambiguous", i)
            if len(pre) == 1:
                r = walk(node.kw[pre[0]], i + 1, args)
                if r:
                    return r
        for ptype, child in node.params:
            n, v = _consume(ptype, toks, i)
            if n:
                r = walk(child, i + n, args + [v])
                if r:
                    return r
        return fail("invalid", i)

    r = walk(g.root, 0, [])
    if r:
        return ("ok", r[0], r[1])
    return tuple(best)


def nodes_at(g: Grammar, toks: list[str]) -> list[GNode]:
    out = []

    def walk(node, i):
        if i == len(toks):
            out.append(node)
            return
        t = toks[i].lower()
        ks = [t] if t in node.kw else [k for k in node.kw if k.startswith(t)]
        if len(ks) == 1:
            walk(node.kw[ks[0]], i + 1)
        for ptype, child in node.params:
            n, _ = _consume(ptype, toks, i)
            if n and i + n <= len(toks):
                walk(child, i + n)
    walk(g.root, 0)
    return out


# --------------------------------------------------------------------------- device I/O

class DockerIO:
    """Runs things inside (or in the network namespace of) a lab device."""

    def __init__(self, lab: str, node: str):
        self.lab, self.node = lab, node

    async def _pid(self):
        from . import netinfo
        c = (await netinfo.containers()).get((self.lab, self.node))
        if not c or c["state"] != "running" or not c["pid"]:
            raise CmdError(f"% {self.node} is not running")
        return c["pid"]

    async def vtysh(self, *cmds, timeout=30):
        from .util import dexec
        args = ["vtysh"]
        for c in cmds:
            args += ["-c", c]
        rc, out, err = await dexec(self.lab, self.node, *args, timeout=timeout)
        return rc, (out + err)

    async def sh(self, script, timeout=30):
        from .util import dsh
        return await dsh(self.lab, self.node, script, timeout=timeout)

    async def ns(self, *argv, timeout=30):
        from .util import run
        return await run("nsenter", "-t", str(await self._pid()), "-n", *argv, timeout=timeout)

    async def ns_proc(self, *argv):
        return await asyncio.create_subprocess_exec("nsenter", "-t", str(await self._pid()), "-n", *argv,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)


class Env:
    """What a session needs from the rest of netlab (swapped out in tests)."""

    def __init__(self, lab: str, node: str, io=None):
        self.lab, self.node_id = lab, node
        self.io = io or DockerIO(lab, node)

    def load(self):
        from . import labs
        return labs.load(self.lab)

    def patch(self, fn):
        from . import labs
        return labs.patch_props(self.lab, self.node_id, fn)

    async def apply_switch(self):
        from . import devices
        d = self.load()
        n = next(x for x in d["nodes"] if x["id"] == self.node_id)
        return await devices.apply_switch(d, n)

    async def apply_l3(self):
        from . import l3
        d = self.load()
        n = next(x for x in d["nodes"] if x["id"] == self.node_id)
        return await l3.apply_node(d, n)

    async def states(self):
        from .util import container_states
        return await container_states(self.lab)

    async def power_cycle(self):
        from . import live
        r = await live.power(self.load(), self.node_id, False)
        if not r["ok"]:
            return r
        return await live.power(self.load(), self.node_id, True)


# --------------------------------------------------------------------------- session

HELPS = {
    "show": "Show running system information", "configure": "Enter configuration mode", "terminal": "Configure from the terminal",
    "enable": "Turn on privileged commands", "disable": "Turn off privileged commands", "exit": "Exit from the EXEC / current mode",
    "end": "Exit to privileged EXEC mode", "logout": "Exit from the EXEC", "ping": "Send echo messages", "traceroute": "Trace route to destination",
    "write": "Write running configuration to memory", "memory": "Write to NV memory", "copy": "Copy from one file to another",
    "running-config": "Current operating configuration", "startup-config": "Contents of startup configuration",
    "reload": "Halt and perform a cold restart", "interface": "Select an interface to configure", "interfaces": "Interface status and configuration",
    "ip": "IP configuration subcommands", "hostname": "Set system's network name", "router": "Enable a routing process",
    "ospf": "Open Shortest Path First (OSPF)", "bgp": "Border Gateway Protocol (BGP)", "route": "Establish static routes / IP routing table",
    "no": "Negate a command or set its defaults", "do": "To run exec commands in config mode", "description": "Interface specific description",
    "shutdown": "Shutdown the selected interface", "address": "Set the IP address of an interface", "network": "Enable routing on an IP network",
    "neighbor": "Specify a neighbor router", "area": "OSPF area parameters", "router-id": "Router-id for the process",
    "passive-interface": "Suppress routing updates on an interface", "brief": "Brief summary of IP status and configuration",
    "cdp": "CDP information (from the lab topology)", "neighbors": "Neighbor entries", "version": "System hardware and software status",
    "inventory": "Show the chassis and modules", "vlan": "VLAN commands", "switchport": "Set switching mode characteristics",
    "mode": "Set trunking mode of the interface", "access": "Set access mode characteristics of the interface",
    "trunk": "Set trunking characteristics of the interface", "allowed": "Set allowed VLAN characteristics when interface is in trunking mode",
    "native": "Set trunking native characteristics when interface is in trunking mode", "name": "Ascii name of the VLAN",
    "spanning-tree": "Spanning Tree Subsystem", "mac": "MAC configuration / table", "address-table": "MAC forwarding table",
    "encapsulation": "Set encapsulation type for an interface", "dot1q": "IEEE 802.1Q Virtual LAN", "mpls": "Configure MPLS / show MPLS",
    "secret": "Assign the privileged level secret", "password": "Assign a password", "banner": "Define a login banner", "motd": "Set Message of the Day banner",
    "line": "Configure a terminal line", "clock": "Display the system clock", "history": "Display the session command history",
    "arp": "ARP table", "summary": "Summarized information", "default-information": "Control distribution of default information",
    "redistribute": "Redistribute information from another routing protocol", "remote-as": "Specify a BGP neighbor",
    "update-source": "Source of routing updates", "address-family": "Enter Address Family command mode", "exit-address-family": "Exit from Address Family configuration mode",
    "range": "Interface range command", "default-gateway": "Specify default gateway (if not routing IP)", "status": "Show interface line status",
    "priority": "Set the bridge priority for the spanning tree", "repeat": "Specify repeat count", "source": "Specify source address or name",
    "vrf": "VPN Routing/Forwarding instance", "length": "Set number of lines on a screen", "startup": "", "protocols": "IP routing protocol process parameters",
    "ldp": "Label Distribution Protocol", "forwarding-table": "Show the label forwarding table", "secondary": "Make this IP address a secondary address",
    "mask": "Network mask", "bandwidth": "Set bandwidth informational parameter", "cost": "Interface cost",
    "channel-group": "Etherchannel/port bundling configuration", "duplex": "Configure duplex operation", "speed": "Configure speed operation",
    "portfast": "Spanning tree portfast options", "dynamic": "Dynamic entries", "detail": "Show detailed information", "unicast": "Address Family modifier",
    "vpnv4": "Address family", "ipv4": "Address family", "dot1q": "IEEE 802.1Q Virtual LAN", "redistribute": "Redistribute information from another routing protocol",
    "console": "Primary terminal line", "vty": "Virtual terminal", "aux": "Auxiliary line", "domain-lookup": "Enable IP Domain Name System hostname translation",
    "domain-name": "Define the default domain name", "routing": "Enable IP routing", "service": "Modify use of network based services",
    "logging": "Modify message logging facilities", "username": "Establish User Name Authentication",
    "access-list": "Add an access list entry", "access-lists": "List access lists", "access-group": "Specify access control for packets",
    "nat": "NAT configuration commands", "helper-address": "Specify a destination address for UDP broadcasts (DHCP relay)",
    "inside": "Inside address translation", "outside": "Outside address translation",
    "static": "Specify static local->global mapping", "pool": "Define pool of addresses", "overload": "Overload an address translation",
    "list": "Specify access list describing local addresses", "netmask": "Specify the network mask", "prefix-length": "Specify the prefix length",
    "permit": "Specify packets to forward", "deny": "Specify packets to reject", "remark": "Access list entry comment",
    "standard": "Standard Access List", "extended": "Extended Access List", "rd": "Specify Route Distinguisher",
    "route-target": "Specify Target VPN Extended Communities", "definition": "VRF definition", "forwarding": "Configure forwarding table",
    "translations": "Translation entries", "statistics": "Translation statistics", "clear": "Reset functions", "counters": "Clear access list counters",
    "translation": "Clear dynamic translation",
}
SUFFIX = {"config": "(config)", "if": "(config-if)", "subif": "(config-subif)", "if-range": "(config-if-range)", "ospf": "(config-router)",
          "bgp": "(config-router)", "bgp-af": "(config-router-af)", "line": "(config-line)", "vlan": "(config-vlan)", "svi": "(config-if)",
          "acl-std": "(config-std-nacl)", "acl-ext": "(config-ext-nacl)", "vrf": "(config-vrf)", "vrf-af": "(config-vrf-af)"}
ACCEPT_ONLY = ("service", "logging", "ntp", "ip domain-lookup", "ip domain-name", "ip domain lookup", "ip name-server", "ip ssh",
               "crypto", "username", "ip http", "cdp", "lldp", "clock", "snmp-server", "aaa", "vtp", "boot", "license", "ip cef",
               "ip routing", "ipv6 cef", "mpls label protocol", "errdisable", "archive")


class Session:
    def __init__(self, env: Env, send):
        self.env, self.send = env, send
        self.q: asyncio.Queue = asyncio.Queue()
        self.mode, self.priv = "exec", False
        self.ctx: list[str] = []             # FRR context lines for the current mode
        self.data: dict = {}                 # mode data (interfaces, vlan ids ...)
        self.history: list[str] = []
        self.host = env.node_id
        self.node = None
        self.typeahead = ""
        self.closed = False
        self.cur = None
        self.interactive = False
        self.capture = None
        self.refresh()
        self.kind = self.node["type"] if self.node else "router"
        self.G = SW_GRAMMARS if self.kind == "switch" else RT_GRAMMARS

    # ---- lab data
    def refresh(self):
        lab = self.env.load()
        self.lab = lab
        self.node = next((n for n in lab["nodes"] if n["id"] == self.env.node_id), None)
        if not self.node:
            raise CmdError(f"% {self.env.node_id} is not in the lab")
        return self.node

    @property
    def props(self):
        return self.node.get("props") or {}

    def ios_props(self):
        return dict(self.props.get("ios") or {})

    def patch(self, fn):
        self.node = self.env.patch(fn)
        self.lab = self.env.load()

    # ---- output
    async def w(self, text: str):
        if self.capture is not None:          # output going through "| include" etc.
            self.capture.append(text)
            return
        await self.send(text.replace("\r\n", "\n").replace("\n", "\r\n"))

    async def say(self, text: str):
        if text:
            await self.w(text.rstrip("\n") + "\n")

    def prompt(self) -> str:
        return f"{self.host}{SUFFIX.get(self.mode, '')}{'#' if self.priv else '>'}"

    def ifname(self, ifn: str) -> str:
        if ifn == "eth0":
            return "Management0"
        if re.fullmatch(r"po\d+", ifn):
            return f"Port-channel{ifn[2:]}"
        return hardware.display(self.node, ifn, long=True)

    def short(self, ifn: str) -> str:
        if re.fullmatch(r"po\d+", ifn):
            return f"Po{ifn[2:]}"
        return hardware.display(self.node, ifn)

    def resolve(self, text: str) -> str:
        if text.lower() == "null0":
            return "Null0"
        m = _PO.fullmatch(text.replace(" ", ""))
        if m:
            if self.kind != "switch" or self.props.get("engine") != "ovs":
                raise CmdError("% patchyard: port-channels need a switch on the Open vSwitch engine (Config → Global)")
            return f"po{int(m.group(1))}"
        try:
            return hardware.resolve(self.node, text)
        except ValueError as e:
            raise CmdError(f"% Invalid interface: {e}")

    def tr_out(self, text: str) -> str:
        """Linux port names -> IOS names in FRR / kernel output."""
        return re.sub(r"(?<![\w.:/-])(eth\d+(?:\.\d+)?|loop\d+|lo)(?![\w/-])", lambda m: self.ifname(m.group(1)), text)

    def tr_in(self, line: str) -> str:
        """IOS interface names typed in a passed-through command -> Linux names."""
        toks, out, i = line.split(), [], 0
        while i < len(toks):
            t = toks[i]
            if i + 1 < len(toks) and re.fullmatch(r"[A-Za-z-]+", t) and toks[i + 1][:1].isdigit() and not out[-1:] == ["area"]:
                try:
                    out.append(hardware.resolve(self.node, t + toks[i + 1])); i += 2; continue
                except ValueError:
                    pass
            if re.fullmatch(r"[A-Za-z-]+\d[\d/]*(\.\d+)?", t):
                try:
                    out.append(hardware.resolve(self.node, t)); i += 1; continue
                except ValueError:
                    pass
            out.append(t); i += 1
        return " ".join(out)

    # ---- input
    async def key(self) -> str:
        if self.typeahead:
            k, self.typeahead = self.typeahead[0], self.typeahead[1:]
            return k
        return await self.q.get()

    async def readline(self, prompt: str, echo=True, help_ok=True) -> str:
        self.interactive = True
        try:
            return await self._readline(prompt, echo, help_ok)
        finally:
            self.interactive = False

    async def _readline(self, prompt: str, echo=True, help_ok=True) -> str:
        buf, pos, hidx = "", 0, len(self.history)
        await self.w(prompt)
        esc = ""

        async def redraw():
            await self.send("\r\x1b[K" + prompt + (buf if echo else "") + (f"\x1b[{len(buf) - pos}D" if echo and pos < len(buf) else ""))

        while True:
            ch = await self.key()
            if self.closed:
                raise ConnectionError
            if esc or ch == "\x1b":
                esc += ch
                if esc in ("\x1b[A", "\x1bOA") and echo and help_ok:          # history up
                    esc = ""
                    if hidx > 0:
                        hidx -= 1; buf = self.history[hidx]; pos = len(buf); await redraw()
                elif esc in ("\x1b[B", "\x1bOB") and echo and help_ok:
                    esc = ""
                    if hidx < len(self.history):
                        hidx += 1; buf = self.history[hidx] if hidx < len(self.history) else ""; pos = len(buf); await redraw()
                elif esc in ("\x1b[C", "\x1bOC"):
                    esc = ""
                    if pos < len(buf):
                        pos += 1; await self.send("\x1b[C") if echo else None
                elif esc in ("\x1b[D", "\x1bOD"):
                    esc = ""
                    if pos > 0:
                        pos -= 1; await self.send("\x1b[D") if echo else None
                elif esc in ("\x1b[H", "\x1bOH", "\x1b[1~"):
                    esc = ""; pos = 0; await redraw()
                elif esc in ("\x1b[F", "\x1bOF", "\x1b[4~"):
                    esc = ""; pos = len(buf); await redraw()
                elif esc == "\x1b[3~":
                    esc = ""
                    if pos < len(buf):
                        buf = buf[:pos] + buf[pos + 1:]; await redraw()
                elif len(esc) > 1 and (esc[-1].isalpha() or esc[-1] == "~" or len(esc) > 6):
                    esc = ""
                continue
            if ch in ("\r", "\n"):
                await self.w("\n")
                return buf
            if ch in ("\x7f", "\b"):
                if pos > 0:
                    buf = buf[:pos - 1] + buf[pos:]; pos -= 1; await redraw()
            elif ch == "\x01":
                pos = 0; await redraw()
            elif ch == "\x05":
                pos = len(buf); await redraw()
            elif ch == "\x15":
                buf, pos = "", 0; await redraw()
            elif ch == "\x17":
                left = buf[:pos].rstrip()
                cut = left.rfind(" ") + 1
                buf, pos = buf[:cut] + buf[pos:], cut; await redraw()
            elif ch in ("\x03", "\x1a"):
                await self.w("^C\n" if ch == "\x03" else "^Z\n")
                return "\x03" if ch == "\x03" else "\x1a"
            elif ch == "\t" and help_ok and echo:
                nb = self.complete(buf[:pos])
                if nb is not None:
                    buf, pos = nb + buf[pos:], len(nb)
                await redraw()
            elif ch == "?" and help_ok and echo:
                await self.w("?\n")
                await self.w(self.help_text(buf[:pos]))
                await redraw()
            elif ch >= " ":
                buf = buf[:pos] + ch + buf[pos:]; pos += 1
                if pos == len(buf):
                    await self.send(ch if echo else "")
                else:
                    await redraw()

    # ---- help / completion
    def grammars(self):
        g = [self.G[self.mode if self.mode in self.G else "config"]]
        if self.mode not in ("exec", "config"):
            g.append(self.G["config"])
        if self.mode == "exec" and self.priv:
            g = [self.G["priv"]]
        return g

    def help_text(self, typed: str) -> str:
        toks = typed.split()
        partial = "" if (not typed or typed.endswith(" ")) else toks.pop()
        if toks[:1] in (["do"], ["d"]) and self.mode != "exec":
            toks = toks[1:]
            gs = [self.G["priv"]]
        elif toks[:1] == ["no"] and self.mode != "exec":
            toks = toks[1:]
            gs = self.grammars()
        else:
            gs = self.grammars()
        opts = {}
        cr = False
        for g in gs:
            for n in nodes_at(g, toks):
                for k in n.kw:
                    if k.startswith(partial.lower()):
                        opts.setdefault(k, n.help.get(k, ""))
                if not partial:
                    for p, _ in n.params:
                        if p == "IF":
                            opts.update(self.if_types())
                        else:
                            opts.setdefault(p, PARAM_HELP.get(p, "") if not p.startswith("<") else "")
                    cr = cr or bool(n.handler)
            if opts and g is gs[0] and self.mode != "exec":
                break
        if partial:
            if not opts:
                return "% Unrecognized command\n"
            return "  ".join(sorted(opts)) + "\n\n"
        if not opts and not cr:
            return "% Unrecognized command\n"
        width = max([len(k) for k in opts] + [4]) + 2
        lines = [f"  {k.ljust(width)}{v}" for k, v in sorted(opts.items())]
        if cr:
            lines.append("  <cr>")
        return "\n".join(lines) + "\n\n"

    def if_types(self) -> dict:
        desc = {"FastEthernet": "FastEthernet IEEE 802.3", "GigabitEthernet": "GigabitEthernet IEEE 802.3z",
                "TenGigabitEthernet": "Ten Gigabit Ethernet", "Serial": "Serial (emulated T1)", "Ethernet": "Ethernet (generic port)",
                "Loopback": "Loopback interface", "Vlan": "Catalyst Vlans"}
        kinds = {re.sub(r"[\d/]+$", "", p["long"]) for p in (hardware.ports(self.node) or [])} or {"Ethernet"}
        kinds |= {"Vlan"} if self.kind == "switch" else {"Loopback"}
        return {k: desc[k] for k in sorted(kinds)}

    def complete(self, typed: str):
        toks = typed.split()
        if not toks or typed.endswith(" "):
            return None
        partial = toks.pop()
        prefix = typed[: len(typed) - len(partial)]
        cands, wants_if = set(), False
        gs = [self.G["priv"]] if toks[:1] == ["do"] else self.grammars()
        if toks[:1] in (["do"], ["no"]) and self.mode != "exec":
            toks = toks[1:]
        for g in gs:
            for n in nodes_at(g, toks):
                cands |= {k for k in n.kw if k.startswith(partial.lower())}
                wants_if = wants_if or any(p == "IF" for p, _ in n.params)
        if len(cands) == 1:
            return prefix + cands.pop() + " "
        if not cands and wants_if and re.fullmatch(r"[A-Za-z-]+", partial):
            words = [w for w, _ in hardware._KINDS if w.startswith(partial.lower())]
            pretty = {"tengigabitethernet": "TenGigabitEthernet", "gigabitethernet": "GigabitEthernet", "fastethernet": "FastEthernet",
                      "serial": "Serial", "loopback": "Loopback", "vlan": "Vlan", "ethernet": "Ethernet"}
            if len(words) == 1:
                return prefix + pretty[words[0]]
        return None

    # ---- main loop
    async def init_host(self):
        if self.kind == "router":
            rc, out = await self.env.io.vtysh("show running-config")
            m = re.search(r"^hostname (\S+)", out, re.M)
            self.host = self.ios_props().get("hostname") or (m.group(1) if m else self.env.node_id)
        else:
            self.host = self.props.get("hostname") or self.env.node_id

    async def run(self):
        await self.init_host()
        await self.w("\nPress RETURN to get started.\n")
        while True:
            await self.readline("", echo=False, help_ok=False)
            banner = self.ios_props().get("banner")
            if banner:
                await self.say("\n" + banner + "\n")
            self.mode, self.priv, self.ctx, self.data = "exec", False, [], {}
            while True:
                line = await self.readline(self.prompt())
                if line == "\x1a" or (line == "\x03" and self.mode != "exec"):
                    if self.mode != "exec":
                        self.to_priv()
                    continue
                if line == "\x03" or not line.strip():
                    continue
                self.history.append(line)
                self.history = self.history[-200:]
                r = await self.guarded(self.execute(line.strip()))
                if r == "logout":
                    await self.w(f"\n\n{self.host} con0 is now available\n\n\n\n")
                    await self.w("Press RETURN to get started.\n")
                    break

    async def guarded(self, coro):
        """Run a command. Ctrl+C / Ctrl+^ (seen by the websocket reader) cancels it."""
        self.cur = asyncio.create_task(coro)
        try:
            return await self.cur
        except CmdError as e:
            await self.say(str(e))
        except asyncio.CancelledError:
            if self.closed:
                raise
            await self.w("\n")
        except Exception as e:  # never kill the console
            await self.say(f"% patchyard: {type(e).__name__}: {e}")
        finally:
            self.cur = None
        return None

    def feed(self, ch: str):
        if ch in ("\x03", "\x1e") and self.cur and not self.interactive:
            self.cur.cancel()
        else:
            self.q.put_nowait(ch)

    def to_priv(self):
        if self.mode != "exec":
            self.event("SYS-5-CONFIG_I", "Configured from console by console")
        self.mode, self.ctx, self.data = "exec", [], {}

    def event(self, code: str, msg: str):
        """Send an IOS-style syslog message to every 'logging host' (UDP 514, from the device)."""
        hosts = self.ios_props().get("logging_hosts") or []
        for h in hosts:
            asyncio.ensure_future(self.env.io.ns("logger", "--rfc3164", "-n", h, "-P", "514", "-d", "-t", f"{self.host}: %{code}", msg, timeout=5))

    # ---- dispatch
    PIPES = ("include", "exclude", "begin", "section", "count")

    async def execute(self, line: str):
        if "|" in line:
            left, _, right = line.partition("|")
            words = right.split(None, 1)
            ops = [o for o in self.PIPES if words and o.startswith(words[0].lower())]
            if not left.strip() or len(ops) != 1:
                return await self.parse_error(("invalid", len(left.split())), line)
            try:
                rx = re.compile(words[1].strip() if len(words) > 1 else "")
            except re.error:
                raise CmdError("% Invalid regular expression")
            self.capture = []
            try:
                await self.execute(left.strip())
            finally:
                text, self.capture = "".join(self.capture), None
            return await self.say(pipe_filter(text, ops[0], rx))
        toks = line.split()
        if self.mode == "exec":
            g = self.G["priv" if self.priv else "exec"]
            return await self.dispatch(g, toks, line)
        if toks[0].lower() == "do" and len(toks) > 1:
            return await self.dispatch(self.G["priv"], toks[1:], " ".join(toks[1:]), from_do=True)
        return await self.dispatch_config(toks, line)

    async def dispatch(self, g, toks, line, from_do=False):
        r = match(g, toks)
        if r[0] == "ok":
            return await r[1](self, r[2], False)
        if self.kind == "router" and "show".startswith(toks[0].lower()) and len(toks[0]) >= 2 and r[0] in ("invalid", "incomplete") and len(toks) > 1:
            return await self.pass_show(line)
        if self.kind == "router" and self.priv and r[0] == "invalid" and r[1] == 0:
            return await self.pass_exec(line)
        await self.parse_error(r, line)

    async def dispatch_config(self, toks, line, neg=False):
        self.cur_line = line
        mine = self.G[self.mode]
        r = match(mine, toks)
        if r[0] == "ok":
            return await r[1](self, r[2], neg)
        if self.mode != "config":
            r2 = match(self.G["config"], toks)
            if r2[0] == "ok" and not (toks[0].lower() == "no"):
                self.mode, self.ctx, self.data = "config", [], {}
                return await r2[1](self, r2[2], neg)
        if self.kind == "router" and r[0] in ("invalid", "incomplete"):
            return await self.passthrough(("no " if neg else "") + line, r)
        await self.parse_error(r, line)

    async def parse_error(self, r, line):
        kind, pos = r[0], r[1]
        if kind == "ambiguous":
            await self.say(f'% Ambiguous command:  "{line}"')
        elif kind == "incomplete":
            await self.say("% Incomplete command.\n")
        else:
            toks = line.split()
            col = len(self.prompt()) + (len(" ".join(toks[:pos])) + (1 if pos else 0))
            await self.say(" " * col + "^\n% Invalid input detected at '^' marker.\n")

    # ---- FRR plumbing (routers)
    async def frr(self, *lines, ctx=None) -> tuple[bool, str]:
        c = self.ctx if ctx is None else ctx
        rc, out = await self.env.io.vtysh("configure terminal", *c, *lines)
        out = "\n".join(l for l in out.splitlines() if l.strip())
        return rc == 0, out

    async def frr_do(self, *lines, ctx=None, quiet_ok=True):
        ok, out = await self.frr(*lines, ctx=ctx)
        if out:
            await self.say(self.tr_out(self.frr_msg(out)))
        return ok

    @staticmethod
    def frr_msg(out: str) -> str:
        return "\n".join(l if l.startswith("%") else f"% {l}" for l in out.splitlines())

    async def passthrough(self, line, r):
        translated = self.tr_in(line)
        ok, out = await self.frr(translated)
        if ok:
            if out:
                await self.say(self.tr_out(self.frr_msg(out)))
            return
        if "Unknown command" in out or "Command incomplete" in out or "Ambiguous" in out:
            if "incomplete" in out:
                return await self.say("% Incomplete command.\n")
            return await self.parse_error(r if r[0] != "incomplete" else ("invalid", r[1]), line)
        await self.say(self.tr_out(self.frr_msg(out)))

    async def pass_show(self, line):
        toks = line.split()
        toks[0] = "show"
        rc, out = await self.env.io.vtysh(self.tr_in(" ".join(toks)))
        if rc and ("Unknown command" in out or "Command incomplete" in out):
            return await self.parse_error(match(self.G["priv"], line.split()), line)
        await self.say(self.tr_out(out))

    async def pass_exec(self, line):
        rc, out = await self.env.io.vtysh(self.tr_in(line))
        if rc and ("Unknown command" in out or "Command incomplete" in out):
            return await self.parse_error(("invalid", 0), line)
        await self.say(self.tr_out(out))


# =========================================================================== handlers
# Every handler: async def h(s: Session, a: list, neg: bool)

# ---- exec / common
async def h_enable(s, a, neg):
    secret = s.ios_props().get("enable_secret")
    if secret:
        for _ in range(3):
            pw = await s.readline("Password: ", echo=False, help_ok=False)
            if hashlib.sha256(pw.encode()).hexdigest() == secret:
                break
        else:
            return await s.say("% Bad secrets\n")
    s.priv = True


async def h_disable(s, a, neg):
    s.priv = False


async def h_deny(s, a, neg):
    """IOS keeps the configuration to privileged EXEC."""
    await s.say("        ^\n% Invalid input detected at '^' marker.\n")


async def h_logout(s, a, neg):
    return "logout"


async def h_exit(s, a, neg):
    if s.mode == "exec":
        return "logout"
    if s.mode in ("if", "subif", "if-range", "ospf", "bgp", "line", "vlan", "svi", "acl-std", "acl-ext", "vrf"):
        s.mode, s.ctx, s.data = "config", [], {}
    elif s.mode == "vrf-af":
        s.mode = "vrf"
    elif s.mode == "bgp-af":
        s.mode, s.ctx = "bgp", [s.data.get("bgp_ctx") or s.ctx[0]]
    else:
        s.to_priv()


async def h_end(s, a, neg):
    s.to_priv()


async def h_conf_t(s, a, neg):
    await s.say("Enter configuration commands, one per line.  End with CNTL/Z.")
    s.mode, s.ctx, s.data = "config", [], {}


async def h_terminal_noop(s, a, neg):
    return None


async def h_show_history(s, a, neg):
    await s.say("\n".join("  " + h for h in s.history[-20:]))


async def h_show_clock(s, a, neg):
    await s.say(time.strftime("*%H:%M:%S.000 UTC %a %b %d %Y", time.gmtime()))


async def h_show_version(s, a, neg):
    ch = hardware.chassis_of(s.node)
    sw = ""
    if s.kind == "router":
        _, out = await s.env.io.vtysh("show version")
        sw = out.splitlines()[0] if out else "FRRouting"
    else:
        sw = "Linux bridge (802.1Q, 802.1D STP)"
    ps = hardware.ports(s.node) or []
    counts = {}
    for p in ps:
        k = hardware.PORT_TYPES[p["type"]]["long"]
        counts[k] = counts.get(k, 0) + 1
    lines = [f"Patchyard IOS-style CLI on {sw}", "",
             f"{s.host} is a Patchyard {s.kind}" + (f", model {ch['label']} ({s.props.get('chassis')})" if ch else " (generic, ports added as cabled)"),
             ""]
    lines += [f"{n} {k} interface{'s' if n > 1 else ''}" for k, n in counts.items()]
    lines += ["", "Commands are translated to " + ("FRR (vtysh)." if s.kind == "router" else "the switch's VLAN/STP settings."),
              "Use 'show running-config' to see what is stored."]
    await s.say("\n".join(lines))


async def h_show_inventory(s, a, neg):
    ch = hardware.chassis_of(s.node)
    if not ch:
        return await s.say(f'NAME: "Chassis", DESCR: "Patchyard {s.kind} (generic)"\nPID: generic\n')
    out = [f'NAME: "Chassis", DESCR: "{ch["label"]}"', f"PID: {s.props.get('chassis')}", ""]
    for sid, mid in sorted((s.props.get("modules") or {}).items()):
        if mid in hardware.MODULES:
            out += [f'NAME: "module {sid}", DESCR: "{hardware.MODULES[mid]["label"]}"', f"PID: {mid}", ""]
    await s.say("\n".join(out))


async def h_show_cdp(s, a, neg):
    st = await s.env.states()
    nodes = {n["id"]: n for n in s.lab["nodes"]}
    rows = []
    for l in s.lab["links"]:
        for me, peer in (("a", "b"), ("b", "a")):
            if l[me] != s.node["id"]:
                continue
            pn = nodes.get(l[peer])
            if not pn or st.get(pn["id"]) != "running" or l.get("admin") == "down":
                continue
            cap = {"router": "R", "switch": "S I", "pc": "H", "server": "H"}.get(pn["type"], "H")
            plat = (pn.get("props") or {}).get("chassis") or f"patchyard-{pn['type']}"
            rows.append((pn["id"], s.short(l[f"{me}_if"]), cap, plat, hardware.display(pn, l[f"{peer}_if"])))
    hdr = ("Capability Codes: R - Router, S - Switch, H - Host, I - IGMP\n"
           "(patchyard: built from the lab's cabling, not from CDP packets)\n\n"
           f"{'Device ID':<17}{'Local Intrfce':<18}{'Holdtme':<9}{'Capability':<12}{'Platform':<13}Port ID")
    body = [f"{d:<17}{li:<18}{'150':<9}{c:<12}{p[:12]:<13}{pi}" for d, li, c, p, pi in sorted(rows)]
    await s.say(hdr + "\n" + "\n".join(body) + f"\n\nTotal cdp entries displayed : {len(rows)}\n")


async def h_ping(s, a, neg, vrf=None):
    target, rest = a[0], a[1:] if len(a) > 1 else []
    count, size, src = 5, 100, None
    toks = " ".join(str(x) for x in rest).split()
    i = 0
    while i < len(toks):
        k = toks[i].lower()
        if "repeat".startswith(k) and i + 1 < len(toks):
            count = max(1, min(1000, int(toks[i + 1]))); i += 2
        elif "size".startswith(k) and i + 1 < len(toks):
            size = max(36, min(18024, int(toks[i + 1]))); i += 2
        elif "source".startswith(k) and i + 1 < len(toks):
            src = toks[i + 1] if is_ip(toks[i + 1]) else s.resolve(toks[i + 1]); i += 2
        else:
            raise CmdError("% Invalid input detected.")
    if not is_ip(target):
        raise CmdError("% Unrecognized host or address, or protocol not running.")
    await s.say(f"Type escape sequence to abort.\nSending {count}, {size}-byte ICMP Echos to {target}, timeout is 2 seconds:")
    if src:
        await s.say(f"Packet sent with a source address of {src if is_ip(src) else s.ifname(src)}")
    ok, times = 0, []
    args = ["ping", "-n", "-c", "1", "-W", "2", "-s", str(max(0, size - 28))]
    if vrf:
        args += ["-I", vrf]
    elif src:
        args += ["-I", src]
    for n in range(count):
        rc, out, _ = await s.env.io.ns(*args, target, timeout=5)
        m = re.search(r"time[=<]([\d.]+)", out)
        if rc == 0 and m:
            ok += 1; times.append(float(m.group(1))); await s.send("!")
        else:
            await s.send(".")
        if (n + 1) % 70 == 0:
            await s.w("\n")
    pct = ok * 100 // count
    rt = f", round-trip min/avg/max = {round(min(times))}/{round(sum(times) / len(times))}/{round(max(times))} ms" if times else ""
    await s.say(f"\nSuccess rate is {pct} percent ({ok}/{count}){rt}")


async def h_ping_vrf(s, a, neg):
    return await h_ping(s, a[1:], neg, vrf=a[0])


async def h_traceroute(s, a, neg, vrf=None):
    target = a[0]
    if not is_ip(target):
        raise CmdError("% Unrecognized host or address, or protocol not running.")
    await s.say(f"Type escape sequence to abort.\nTracing the route to {target}\nVRF info: (vrf in name/id, vrf out name/id)" if vrf else
                f"Type escape sequence to abort.\nTracing the route to {target}")
    args = ["traceroute", "-n", "-w", "2", "-q", "3", "-m", "30"] + (["-i", vrf] if vrf else []) + [target]
    p = await s.env.io.ns_proc(*args)
    try:
        while True:
            raw = await p.stdout.readline()
            if not raw:
                break
            ln = raw.decode(errors="replace").rstrip()
            m = re.match(r"\s*(\d+)\s+(.*)", ln)
            if not m:
                continue
            hop, rest = m.groups()
            ip, parts = None, []
            toks = rest.split()
            k = 0
            while k < len(toks):
                x = toks[k]
                if x == "*":
                    parts.append("*")
                elif is_ip(x) and ip is None:
                    ip = x
                elif re.fullmatch(r"[\d.]+", x) and k + 1 < len(toks) and toks[k + 1] == "ms":
                    parts.append(f"{round(float(x))} msec"); k += 1
                k += 1
            await s.say(f"  {hop:>2} {ip + ' ' if ip else ''}" + " ".join(parts))
    finally:
        if p.returncode is None:
            p.kill()
        await p.wait()


async def h_trace_vrf(s, a, neg):
    return await h_traceroute(s, a[1:], neg, vrf=a[0])


async def h_reload(s, a, neg):
    ans = await s.readline("Proceed with reload? [confirm]", help_ok=False)
    if ans.strip().lower() not in ("", "y", "yes"):
        return
    await s.say(f"\n%SYS-5-RELOAD: Reload requested by console. Reload Reason: Reload Command.\n(patchyard: powering {s.env.node_id} off and on; unsaved config is lost)")
    r = await s.env.power_cycle()
    await s.say(r.get("output", ""))
    s.refresh()
    s.priv = False
    s.to_priv()
    await s.say("\nPress RETURN to get started.")
    await s.readline("", echo=False, help_ok=False)


# ---- accepted-only config (stored, shown in show run, no effect)
async def h_accept(s, a, neg, line=None):
    text = line or " ".join(str(x) for x in a)
    pos = text[3:] if text.startswith("no ") else text
    def fn(p):
        ios = dict(p.get("ios") or {})
        extra = [x for x in ios.get("extra", []) if x not in (pos, f"no {pos}")]
        if neg and text.startswith("no "):
            pass                      # "no no ip domain-lookup" -> back to the default
        elif neg:
            pass                      # "no service x" -> just remove it
        else:
            extra.append(text)
        ios["extra"] = extra[-100:]
        p["ios"] = ios
    s.patch(fn)


def accepted(prefix):
    async def h(s, a, neg):
        await h_accept(s, a, neg, line=" ".join([prefix] + [str(x) for x in a]).strip())
    return h


async def h_logging_host(s, a, neg):
    h = str(a[0])
    def fn(p):
        ios = dict(p.get("ios") or {})
        hosts = [x for x in ios.get("logging_hosts", []) if x != h]
        if not neg:
            hosts.append(h)
        ios["logging_hosts"] = hosts[:8]
        p["ios"] = ios
    s.patch(fn)


async def _config_text(s, which: str) -> str:
    if s.kind == "switch":
        return switch_config(s)
    if which == "startup":
        rc, out, err = await s.env.io.sh("cat /etc/frr/frr.conf")
        return ios_config(s, out)
    rc, body = await s.env.io.vtysh("show running-config")
    return ios_config(s, body)


def _tftp_url(text: str):
    m = re.fullmatch(r"tftp:(?://([^/]+)/?(.*))?", text)
    return (m.group(1) or "", m.group(2) or "") if m else None


async def h_copy(s, a, neg):
    """copy running-config|startup-config tftp:[//host/file]  and  copy tftp:[//host/file] running-config"""
    import os, tempfile
    src, dst = str(a[0]), str(a[1])
    is_cfg = lambda x, name: len(x) >= 3 and name.startswith(x.lower())
    if is_cfg(src, "running-config") and is_cfg(dst, "startup-config"):
        return await (r_copy_rs(s, a, neg) if s.kind == "router" else sw_write(s, a, neg))
    up = _tftp_url(dst.lower()) if (is_cfg(src, "running-config") or is_cfg(src, "startup-config")) else None
    down = _tftp_url(src.lower()) if is_cfg(dst, "running-config") else None
    if up is None and down is None:
        raise CmdError("% patchyard: supported: copy running-config|startup-config tftp:, copy tftp: running-config, copy running-config startup-config")
    host, fname = up if up is not None else down
    if not host:
        host = (await s.readline("Address or name of remote host []? ", help_ok=False)).strip()
    if not is_ip(host):
        raise CmdError("% Error parsing filename (Invalid IP address)")
    default = f"{s.host.lower()}-confg"
    if up is not None:
        if not fname:
            fname = (await s.readline(f"Destination filename [{default}]? ", help_ok=False)).strip() or default
        text = await _config_text(s, "startup" if is_cfg(src, "startup-config") else "running")
        fd, path = tempfile.mkstemp(prefix="netlab-tftp-")
        with os.fdopen(fd, "w") as f:
            f.write(text)
        t0 = time.time()
        try:
            rc, out, err = await s.env.io.ns("tftp", "-m", "binary", host, "-c", "put", path, fname, timeout=30)
        finally:
            os.unlink(path)
        if rc or "timed out" in (out + err) or "Error" in (out + err):
            raise CmdError(f"%Error opening tftp://{host}/{fname} ({(out + err).strip() or 'Timed out'})")
        return await s.say(f"!!\n{len(text)} bytes copied in {time.time() - t0:.3f} secs")
    if not fname:
        fname = (await s.readline("Source filename []? ", help_ok=False)).strip()
    if not fname:
        raise CmdError("% No source filename")
    await s.readline("Destination filename [running-config]? ", help_ok=False)
    await s.say(f"Accessing tftp://{host}/{fname}...")
    fd, path = tempfile.mkstemp(prefix="netlab-tftp-"); os.close(fd)
    try:
        rc, out, err = await s.env.io.ns("tftp", "-m", "binary", host, "-c", "get", fname, path, timeout=30)
        text = open(path).read() if rc == 0 else ""
    finally:
        os.unlink(path)
    if rc or not text or "Error" in (out + err):
        raise CmdError(f"%Error opening tftp://{host}/{fname} ({(out + err).strip() or 'No such file'})")
    await s.say(f"Loading {fname} from {host}: !\n[OK - {len(text)} bytes]")
    skip = re.compile(r"^(Building configuration|Current configuration|version |end$|enable secret 9 )")
    s.mode, s.ctx, s.data = "config", [], {}
    applied = 0
    for ln in text.splitlines():
        if not ln.strip() or ln.strip() == "!" or skip.match(ln.strip()):
            continue
        if not ln.startswith(" "):
            s.mode, s.ctx, s.data = "config", [], {}
        s.cur_line = ln.strip()
        outer, s.capture = s.capture, []
        try:
            await s.dispatch_config(ln.split(), ln.strip())
        except CmdError as e:
            s.capture.append(str(e) + "\n")
        finally:
            reply, s.capture = "".join(s.capture), outer
        if reply.strip():
            await s.say(f"{ln.strip()}\n" + "\n".join(l for l in reply.splitlines() if not l.strip().startswith("^")))
        applied += 1
    s.mode, s.ctx, s.data = "exec", [], {}
    s.event("SYS-5-CONFIG_I", f"Configured from tftp://{host}/{fname}")
    await s.say(f"{len(text)} bytes copied, {applied} lines applied to running-config")


async def h_enable_secret(s, a, neg):
    pw = str(a[-1]) if a else ""
    def fn(p):
        ios = dict(p.get("ios") or {})
        if neg:
            ios.pop("enable_secret", None)
        else:
            ios["enable_secret"] = hashlib.sha256(pw.encode()).hexdigest()
        p["ios"] = ios
    s.patch(fn)


async def h_banner(s, a, neg):
    text = " ".join(str(x) for x in a)
    if neg:
        return s.patch(lambda p: p.setdefault("ios", {}).pop("banner", None))
    delim, body = text[:1], text[1:]
    if delim in body:
        banner = body[: body.index(delim)]
    else:
        await s.say(f"Enter TEXT message.  End with the character '{delim}'.")
        lines = [body] if body else []
        while True:
            ln = await s.readline("", help_ok=False)
            if delim in ln:
                lines.append(ln[: ln.index(delim)])
                break
            lines.append(ln)
        banner = "\n".join(lines)
    def fn(p):
        ios = dict(p.get("ios") or {}); ios["banner"] = banner.strip("\n")[:2000]; p["ios"] = ios
    s.patch(fn)


async def h_line(s, a, neg):
    s.mode, s.data = "line", {"line": "line " + " ".join(str(x) for x in a)}


async def h_line_cmd(s, a, neg):
    cmd = ("no " if neg else "") + " ".join(s.cur_line.split())
    def fn(p):
        ios = dict(p.get("ios") or {})
        lines = dict(ios.get("lines") or {})
        cur = [x for x in lines.get(s.data["line"], []) if x.split()[0] != cmd.split()[0] or x == cmd]
        if cmd not in cur:
            cur.append(cmd)
        lines[s.data["line"]] = cur[-20:]
        ios["lines"] = lines; p["ios"] = ios
    s.patch(fn)


def unsupported(what):
    async def h(s, a, neg):
        raise CmdError(f"% patchyard: {what}")
    return h


async def h_speed(s, a, neg):
    p = hardware.port_map(s.node).get(s.data.get("ifn", ""))
    sp = hardware.fmt_speed(p["kbit"]) if p else "set by the cable"
    await s.say(f"% patchyard: speed and duplex are fixed by the port type ({sp}, full duplex)")


async def h_no(s, a, neg):
    rest = " ".join(str(x) for x in a)
    if not rest:
        return await s.say("% Incomplete command.\n")
    return await s.dispatch_config(rest.split(), rest, neg=True)


async def h_do(s, a, neg):
    line = " ".join(str(x) for x in a)
    return await s.dispatch(s.G["priv"], line.split(), line, from_do=True)


# ---- router: show
async def r_show_run(s, a, neg, startup=False, iface=None):
    if startup:
        rc, out, err = await s.env.io.sh("cat /etc/frr/frr.conf")
        if rc:
            raise CmdError("% startup-config is not present")
        body = out
    else:
        rc, body = await s.env.io.vtysh("show running-config")
    text = ios_config(s, body, iface=iface)
    if not startup and not iface:
        text = f"Building configuration...\n\nCurrent configuration : {len(text)} bytes\n" + text
    await s.say(text)


async def r_show_run_if(s, a, neg):
    return await r_show_run(s, a, neg, iface=s.resolve(a[0]))


async def r_show_start(s, a, neg):
    return await r_show_run(s, a, neg, startup=True)


async def kernel_ifaces(s):
    rc, out, err = await s.env.io.ns("ip", "-j", "-s", "addr")
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def ios_ifaces(s, kern):
    """[(ifn, kernel dict or None)] in IOS order: physical ports (cabled or not), subifs, loopbacks."""
    by = {k["ifname"]: k for k in kern}
    names = []
    for p in hardware.ports(s.node) or []:
        names.append(p["if"])
    for k in kern:
        n = k["ifname"]
        if n == "eth0" or n in names:
            continue
        if n == "lo" or re.fullmatch(r"loop\d+|eth\d+(\.\d+)?|vlan\d+|br0", n):
            names.append(n)
    if "lo" not in names:
        names.append("lo")

    def key(n):
        if n == "lo" or n.startswith("loop"):
            return (2, int(n[4:] or 0) if n != "lo" else 0, 0)
        base, _, sub = n.partition(".")
        num = int(re.sub(r"\D", "", base) or 0)
        return (1 if base.startswith("vlan") or base == "br0" else 0, num, int(sub or 0))
    return [(n, by.get(n)) for n in sorted(set(names), key=key)]


def _status(k):
    if not k:
        return "down", "down"
    flags = k.get("flags", [])
    if "UP" not in flags:
        return "administratively down", "down"
    oper = k.get("operstate", "")
    if k["ifname"] in ("lo",) or k["ifname"].startswith("loop") or oper in ("UP", "UNKNOWN") and "LOWER_UP" in flags:
        return "up", "up"
    return "down", "down"


async def r_show_ip_int_brief(s, a, neg):
    kern = await kernel_ifaces(s)
    rows = []
    for ifn, k in ios_ifaces(s, kern):
        if ifn == "br0":
            continue
        ip4 = next((x["local"] for x in (k or {}).get("addr_info", []) if x.get("family") == "inet" and not x["local"].startswith("127.")), None)
        st, pr = _status(k)
        rows.append(f"{s.ifname(ifn):<27}{(ip4 or 'unassigned'):<16}YES {('manual' if ip4 else 'unset'):<7}{st:<22}{pr}")
    await s.say(f"{'Interface':<27}{'IP-Address':<16}OK? Method Status                Protocol\n" + "\n".join(rows))


async def r_show_interfaces(s, a, neg):
    kern = await kernel_ifaces(s)
    want = s.resolve(a[0]) if a else None
    out = []
    for ifn, k in ios_ifaces(s, kern):
        if want and ifn != want:
            continue
        if ifn == "br0":
            continue
        st, pr = _status(k)
        p = hardware.port_map(s.node).get(ifn.partition(".")[0])
        hw = "Loopback" if ifn.startswith("lo") else ("Serial (emulated over Ethernet)" if p and p["type"] == "se" else "Ethernet")
        mac = (k or {}).get("address", "0000.0000.0000")
        macd = ".".join(mac.replace(":", "")[i:i + 4] for i in (0, 4, 8)) if ":" in mac else mac
        ips = [f'{x["local"]}/{x["prefixlen"]}' for x in (k or {}).get("addr_info", []) if x.get("family") == "inet" and not x["local"].startswith("127.")]
        bw = (p["kbit"] if p and p["kbit"] else 10_000_000) if p else 8_000_000 if ifn.startswith("lo") else 1_000_000
        stats = (k or {}).get("stats64") or (k or {}).get("stats") or {}
        rx, tx = stats.get("rx", {}), stats.get("tx", {})
        out += [f"{s.ifname(ifn)} is {st}, line protocol is {pr}",
                f"  Hardware is {hw}, address is {macd} (bia {macd})"]
        out += [f"  Internet address is {ips[0]}" if ips else "  Internet address is unassigned",
                f"  MTU {(k or {}).get('mtu', 1500)} bytes, BW {bw} Kbit/sec, DLY 10 usec,",
                f"  Encapsulation {'802.1Q Virtual LAN, Vlan ID ' + ifn.partition('.')[2] if '.' in ifn else 'ARPA' if not ifn.startswith('lo') else 'LOOPBACK'}, loopback not set"]
        if p:
            out.append(f"  Full Duplex, {hardware.fmt_speed(p['kbit'])}, media type is {p['media']} ({hardware.PORT_TYPES[p['type']]['label']})")
        out += [f"     {rx.get('packets', 0)} packets input, {rx.get('bytes', 0)} bytes, {rx.get('dropped', 0)} no buffer",
                f"     {rx.get('errors', 0)} input errors, 0 CRC, 0 frame, {rx.get('over_errors', 0)} overrun, 0 ignored",
                f"     {tx.get('packets', 0)} packets output, {tx.get('bytes', 0)} bytes, 0 underruns",
                f"     {tx.get('errors', 0)} output errors, {tx.get('collisions', 0)} collisions, {tx.get('dropped', 0)} output drops"]
    if want and not out:
        raise CmdError("% Interface not present (not cabled yet?)")
    await s.say("\n".join(out))


async def r_show_arp(s, a, neg):
    rc, out, _ = await s.env.io.ns("ip", "-j", "-4", "neigh")
    try:
        rows = json.loads(out) if rc == 0 else []
    except ValueError:
        rows = []
    lines = [f"{'Protocol':<10}{'Address':<17}{'Age (min)':<11}{'Hardware Addr':<16}{'Type':<6}Interface"]
    for r in rows:
        if r.get("dev") == "eth0" or "lladdr" not in r:
            continue
        mac = r["lladdr"].replace(":", "")
        lines.append(f"{'Internet':<10}{r['dst']:<17}{'0':<11}{mac[0:4] + '.' + mac[4:8] + '.' + mac[8:12]:<16}{'ARPA':<6}{s.ifname(r['dev'])}")
    await s.say("\n".join(lines))


def passthrough_show(cmd):
    async def h(s, a, neg):
        full = cmd.format(*a) if a else cmd
        rc, out = await s.env.io.vtysh(s.tr_in(full))
        await s.say(s.tr_out(out))
    return h


async def r_show_ip_route(s, a, neg):
    args = [str(x) for x in a]
    if len(args) >= 2 and is_ip(args[0]) and is_ip(args[1]):
        args = [f"{args[0]}/{m2len(args[1])}"] + args[2:]
    rc, out = await s.env.io.vtysh(s.tr_in("show ip route " + " ".join(args)))
    if rc and "Unknown command" in out:
        raise CmdError("% Invalid input detected.")
    await s.say(s.tr_out(out))


async def r_write(s, a, neg):
    await s.say("Building configuration...")
    rc, out = await s.env.io.vtysh("write memory")
    await s.say("[OK]" if rc == 0 else s.tr_out(s.frr_msg(out)))


async def r_copy_rs(s, a, neg):
    await s.readline("Destination filename [startup-config]? ", help_ok=False)
    await r_write(s, a, neg)


# ---- router: config
async def r_hostname(s, a, neg):
    if neg:
        await s.frr_do("no hostname")
        s.host = s.env.node_id
        s.patch(lambda p: p.__setitem__("ios", {k: v for k, v in (p.get("ios") or {}).items() if k != "hostname"}))
        return
    name = str(a[0])
    if await s.frr_do(f"hostname {name}"):
        s.host = name
        s.patch(lambda p: p.__setitem__("ios", {**(p.get("ios") or {}), "hostname": name}))


async def r_interface(s, a, neg):
    ifn = s.resolve(a[0])
    if ifn == "Null0":
        raise CmdError("% Null0 needs no configuration")
    base, dot, sub = ifn.partition(".")
    if neg:
        if dot:
            subs = s.props.get("subifs") or []
            hit = [x for x in subs if x["parent"] == base and int(x.get("num") or x["vlan"]) == int(sub)]
            s.patch(lambda p: p.__setitem__("subifs", [x for x in p.get("subifs", []) if x not in hit]))
            await s.env.io.ns("ip", "link", "del", ifn)
            await s.frr(f"no interface {ifn}", ctx=[])
            return
        if ifn.startswith("loop"):
            n = int(ifn[4:])
            s.patch(lambda p: p.__setitem__("loopbacks", [x for x in p.get("loopbacks", []) if int(x) != n]))
            await s.env.io.ns("ip", "link", "del", ifn)
            await s.frr(f"no interface {ifn}", ctx=[])
            return
        raise CmdError("% Physical interfaces can't be removed")
    if ifn.startswith("vlan"):
        raise CmdError("% Patchyard routers have no VLAN interfaces; use a subinterface (interface Gi0/0/0.10 + encapsulation dot1Q 10)")
    if ifn.startswith("loop"):
        n = int(ifn[4:])
        if n not in [int(x) for x in s.props.get("loopbacks", [])]:
            s.patch(lambda p: p.__setitem__("loopbacks", sorted({*map(int, p.get("loopbacks", [])), n})))
            await s.env.io.ns("ip", "link", "add", ifn, "type", "dummy")
            await s.env.io.ns("ip", "link", "set", ifn, "up")
    s.mode = "subif" if dot else "if"
    s.ctx = [f"interface {ifn}"]
    s.data = {"ifn": ifn}


def _subif(s):
    ifn = s.data.get("ifn", "")
    base, _, sub = ifn.partition(".")
    return next((x for x in s.props.get("subifs") or [] if x["parent"] == base and int(x.get("num") or x["vlan"]) == int(sub or -1)), None)


async def r_ip_address(s, a, neg):
    ifn = s.data["ifn"]
    if "." in ifn and not _subif(s):
        raise CmdError("% Configuring IP routing on a LAN subinterface is only allowed if that\nsubinterface is already configured as part of an IEEE 802.10, IEEE 802.1Q,\nor ISL vLAN.")
    if neg and not a:
        _, out = await s.env.io.vtysh("show running-config")
        cur, inside = [], False
        for ln in out.splitlines():
            if ln.startswith("interface "):
                inside = ln.split()[1] == ifn
            elif inside and ln.strip().startswith("ip address "):
                cur.append(ln.strip())
        if cur:
            await s.frr_do(*[f"no {c}" for c in cur])
        return
    try:
        plen = m2len(a[1])
    except ValueError:
        raise CmdError(f"% Bad mask {a[1]} for address {a[0]}")
    await s.frr_do(f"{'no ' if neg else ''}ip address {a[0]}/{plen}")


async def r_shutdown(s, a, neg):
    if await s.frr_do("no shutdown" if neg else "shutdown"):
        s.event("LINK-5-CHANGED", f"Interface {s.ifname(s.data.get('ifn', ''))}, changed state to {'up' if neg else 'administratively down'}")


async def r_encap(s, a, neg):
    ifn = s.data["ifn"]
    if "." not in ifn:
        raise CmdError("% Encapsulation is set on a subinterface (interface Gi0/0/0.10)")
    base, _, sub = ifn.partition(".")
    vid = int(a[0])
    if neg:
        s.patch(lambda p: p.__setitem__("subifs", [x for x in p.get("subifs", []) if not (x["parent"] == base and int(x.get("num") or x["vlan"]) == int(sub))]))
        await s.env.io.ns("ip", "link", "del", ifn)
        return
    cur = _subif(s)
    if cur and int(cur["vlan"]) == vid:
        return
    if cur:
        await s.env.io.ns("ip", "link", "del", ifn)
    entry = {"parent": base, "vlan": vid}
    if int(sub) != vid:
        entry["num"] = int(sub)
    s.patch(lambda p: p.__setitem__("subifs", [x for x in p.get("subifs", []) if not (x["parent"] == base and int(x.get("num") or x["vlan"]) == int(sub))] + [entry]))
    rc, out, err = await s.env.io.ns("ip", "link", "add", "link", base, "name", ifn, "type", "vlan", "id", str(vid))
    if rc and "File exists" not in err:
        if "Cannot find device" in err:
            await s.say(f"% patchyard: saved; {s.ifname(base)} isn't cabled yet, the subinterface is created when it is")
        else:
            await s.say(f"% {err.strip()}")
    await s.env.io.ns("ip", "link", "set", ifn, "up")


async def r_ip_ospf_area(s, a, neg):
    await s.frr_do(f"{'no ' if neg else ''}ip ospf area {a[-1]}")


async def r_bandwidth(s, a, neg):
    if neg:
        return await s.frr_do("no bandwidth")
    await s.frr_do(f"bandwidth {max(1, min(100000, int(a[0]) // 1000))}")


async def r_mpls_ip(s, a, neg):
    ifn = s.data["ifn"]
    if neg:
        return await s.frr_do(f"no interface {ifn}", ctx=["mpls ldp", "address-family ipv4"])
    _, run = await s.env.io.vtysh("show running-config")
    if "\nmpls ldp" not in run:
        rid = await _loopback_ip(s)
        if not rid:
            raise CmdError("% patchyard: give Loopback0 a /32 address first; LDP uses it as router-id and transport address")
        await s.frr_do(f"router-id {rid}", ctx=["mpls ldp"])
        await s.frr_do(f"discovery transport-address {rid}", ctx=["mpls ldp", "address-family ipv4"])
    await s.env.io.ns("sh", "-c", f"[ -e /proc/sys/net/mpls/conf/{ifn}/input ] && echo 1 > /proc/sys/net/mpls/conf/{ifn}/input; true")
    await s.frr_do(f"interface {ifn}", ctx=["mpls ldp", "address-family ipv4"])


async def _loopback_ip(s):
    _, run = await s.env.io.vtysh("show running-config")
    inside = False
    for ln in run.splitlines():
        if ln.startswith("interface "):
            inside = ln.split()[1] == "lo"
        elif inside and ln.strip().startswith("ip address ") and ln.strip().endswith("/32"):
            return ln.split()[2].split("/")[0]
    return None


async def r_ldp_rid(s, a, neg):
    ifn = s.resolve(a[0])
    if ifn != "lo":
        raise CmdError("% patchyard: use Loopback0 as the LDP router-id")
    rid = await _loopback_ip(s)
    if not rid:
        raise CmdError("% Loopback0 has no /32 address")
    await s.frr_do(f"{'no ' if neg else ''}router-id {rid}", ctx=["mpls ldp"])


async def r_router_ospf(s, a, neg):
    vrf = a[1] if len(a) > 1 else None
    head = f"router ospf{' vrf ' + vrf if vrf else ''}"
    if neg:
        return await s.frr_do(f"no {head}", ctx=[])
    if await s.frr_do(head, ctx=[]):
        s.mode, s.ctx, s.data = "ospf", [head], {}


async def r_ospf_rid(s, a, neg):
    await s.frr_do(f"{'no ' if neg else ''}ospf router-id {a[0] if a else ''}".strip())


async def r_ospf_network(s, a, neg):
    try:
        plen = w2len(a[1])
    except ValueError:
        raise CmdError(f"% OSPF: Invalid wildcard mask {a[1]}")
    await s.frr_do(f"{'no ' if neg else ''}network {a[0]}/{plen} area {a[2]}")


async def r_passive(s, a, neg):
    if str(a[0]).lower() == "default":
        return await s.frr_do(f"{'no ' if neg else ''}passive-interface default")
    ifn = s.resolve(a[0])
    await s.frr_do(f"{'no ' if neg else ''}ip ospf passive", ctx=[f"interface {ifn}"])


async def r_passive_default(s, a, neg):
    await s.frr_do(f"{'no ' if neg else ''}passive-interface default")


async def r_redistribute(s, a, neg):
    words = [str(x) for x in a if str(x).lower() != "subnets"]
    await s.frr_do(f"{'no ' if neg else ''}redistribute " + " ".join(words))


async def r_router_bgp(s, a, neg):
    head = f"router bgp {a[0]}"
    if neg:
        return await s.frr_do(f"no {head}", ctx=[])
    if await s.frr_do(head, ctx=[]):
        s.mode, s.ctx, s.data = "bgp", [head], {"asn": a[0], "bgp_ctx": head}


async def r_bgp_network(s, a, neg):
    plen = m2len(a[1]) if len(a) > 1 else classful(a[0])
    await s.frr_do(f"{'no ' if neg else ''}network {a[0]}/{plen}")


async def r_bgp_af(s, a, neg, kind="ipv4"):
    asn = s.data.get("asn") or s.ctx[0].split()[2]
    if kind == "vrf":
        ctx = [f"router bgp {asn} vrf {a[0]}", "address-family ipv4 unicast"]
    elif kind == "vpnv4":
        ctx = [f"router bgp {asn}", "address-family ipv4 vpn"]
    else:
        ctx = [f"router bgp {asn}", "address-family ipv4 unicast"]
    if neg:
        return await s.frr_do(f"no {ctx[1]}", ctx=ctx[:1])
    if await s.frr_do(ctx[1], ctx=ctx[:1]):
        s.mode, s.ctx = "bgp-af", ctx
        s.data = {"asn": asn, "bgp_ctx": f"router bgp {asn}"}
        if kind == "vrf":
            await vrf_sync_bgp(s, str(a[0]))


def bgp_af(kind):
    async def h(s, a, neg):
        return await r_bgp_af(s, a, neg, kind)
    return h


async def r_exit_af(s, a, neg):
    s.mode, s.ctx = "bgp", [s.data.get("bgp_ctx") or s.ctx[0]]


async def r_ip_route(s, a, neg, vrf=None):
    a = [str(x) for x in a]
    try:
        pfx = f"{a[0]}/{m2len(a[1])}"
    except ValueError:
        raise CmdError("% Inconsistent address and mask")
    nh = a[2]
    nh = nh if is_ip(nh) else s.resolve(nh)
    rest = a[3:]
    line = f"{'no ' if neg else ''}ip route {pfx} {nh} {' '.join(rest)}".strip()
    await s.frr_do(line, ctx=[f"vrf {vrf}"] if vrf else [])


async def r_ip_route_vrf(s, a, neg):
    return await r_ip_route(s, a[1:], neg, vrf=a[0])


async def r_description(s, a, neg):
    text = " ".join(str(x) for x in a)
    await s.frr_do("no description" if neg else f"description {text}")


# --------------------------------------------------------------------------- switch handlers

def _ports(s):
    return dict(s.props.get("ports") or {})


def _sw_ifs(s):
    return s.data.get("ifns") or [s.data["ifn"]]


async def sw_commit(s, fn):
    s.patch(fn)
    st = await s.env.states()
    if st.get(s.env.node_id) == "running":
        r = await s.env.apply_switch()
        if not r.get("ok"):
            await s.say("% switch script: " + (r.get("output") or "")[-400:])


def _ensure_vlan(p, vid):
    vl = list(p.get("vlans") or [{"id": 1, "name": "default"}])
    if not any(int(v["id"]) == int(vid) for v in vl):
        vl.append({"id": int(vid), "name": f"VLAN{int(vid):04d}"})
        p["vlans"] = sorted(vl, key=lambda v: int(v["id"]))
        return True
    return False


async def sw_interface(s, a, neg):
    ifn = s.resolve(a[0])
    if neg:
        raise CmdError("% Physical interfaces can't be removed")
    if ifn.startswith("vlan"):
        s.mode, s.data = "svi", {"vid": int(ifn[4:])}
        return
    if ifn.startswith("lo") or "." in ifn:
        raise CmdError("% Patchyard switches support physical ports and one management Vlan interface")
    if ifn.startswith("po") and not any((c or {}).get("channel") == int(ifn[2:]) for c in (s.props.get("ports") or {}).values()):
        await s.say(f"% patchyard: Port-channel{ifn[2:]} has no members yet; add them with 'channel-group {ifn[2:]} mode active'")
    s.mode, s.data = "if", {"ifn": ifn}


async def sw_interface_range(s, a, neg):
    text = " ".join(str(x) for x in a).replace(" - ", "-").replace(" , ", ",").replace(", ", ",")
    ifns = []
    for part in text.split(","):
        part = part.strip()
        m = re.fullmatch(r"(.*?)(\d+)-(\d+)", part.replace(" ", ""))
        if m and not re.fullmatch(r".*/\d+-\d+/.*", part):
            head, lo, hi = m.groups()
            for n in range(int(lo), int(hi) + 1):
                ifns.append(s.resolve(f"{head}{n}"))
        else:
            ifns.append(s.resolve(part))
    s.mode, s.data = "if-range", {"ifns": ifns}


async def sw_mode(s, a, neg, mode="access"):
    ifs = _sw_ifs(s)
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            c["mode"] = "access" if neg else mode
            ports[i] = c
        p["ports"] = ports
    await sw_commit(s, fn)


def sw_mode_h(mode):
    async def h(s, a, neg):
        return await sw_mode(s, a, neg, mode)
    return h


async def sw_access_vlan(s, a, neg):
    vid = 1 if neg else int(a[0])
    ifs = _sw_ifs(s)
    created = []
    def fn(p):
        if _ensure_vlan(p, vid):
            created.append(vid)
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {}); c["vlan"] = vid; ports[i] = c
        p["ports"] = ports
    await sw_commit(s, fn)
    if created:
        await s.say(f"% Access VLAN does not exist. Creating vlan {vid}")


async def sw_trunk_native(s, a, neg):
    vid = 1 if neg else int(a[0])
    ifs = _sw_ifs(s)
    def fn(p):
        _ensure_vlan(p, vid)
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {}); c["native"] = vid; ports[i] = c
        p["ports"] = ports
    await sw_commit(s, fn)


async def sw_trunk_allowed(s, a, neg):
    words = " ".join(str(x) for x in a).lower().split()
    if words[0] in ("add", "remove", "except") and len(words) < 2:
        raise CmdError("% Incomplete command.")
    ifs = _sw_ifs(s)
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            cur = vlan_set(c.get("allowed") or "1-4094")
            if neg or words[0] == "all":
                new = list(range(1, 4095))
            elif words[0] == "none":
                new = []
            elif words[0] == "add":
                new = cur + vlan_set(words[1])
            elif words[0] == "remove":
                rm = set(vlan_set(words[1])); new = [v for v in cur if v not in rm]
            elif words[0] == "except":
                ex = set(vlan_set(words[1])); new = [v for v in range(1, 4095) if v not in ex]
            else:
                new = vlan_set(words[0])
            c["allowed"] = "" if len(set(new)) == 4094 else vlan_text(new) or "none"
            if c["allowed"] == "none":
                raise CmdError("% patchyard: a trunk needs at least one allowed VLAN")
            ports[i] = c
        p["ports"] = ports
    await sw_commit(s, fn)


async def sw_shutdown(s, a, neg):
    ifs = _sw_ifs(s)
    for i in ifs:
        s.event("LINK-5-CHANGED", f"Interface {s.ifname(i)}, changed state to {'up' if neg else 'administratively down'}")
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            if neg:
                c.pop("shutdown", None)
            else:
                c["shutdown"] = True
            ports[i] = c
        p["ports"] = ports
    await sw_commit(s, fn)


async def sw_description(s, a, neg):
    ifs = _sw_ifs(s)
    text = " ".join(str(x) for x in a)
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            if neg:
                c.pop("description", None)
            else:
                c["description"] = text[:200]
            ports[i] = c
        p["ports"] = ports
    s.patch(fn)


async def sw_portfast(s, a, neg):
    ifs = _sw_ifs(s)
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            if neg:
                c.pop("portfast", None)
            else:
                c["portfast"] = True
            ports[i] = c
        p["ports"] = ports
    s.patch(fn)
    if not neg:
        await s.say("% patchyard: portfast stored, but the kernel bridge has no portfast; ports still take ~30 s to forward with STP on")


async def sw_vlan(s, a, neg):
    vids = vlan_set(str(a[0]))
    if neg:
        if 1 in vids:
            raise CmdError("% Default VLAN 1 may not be deleted.")
        def fn(p):
            p["vlans"] = [v for v in (p.get("vlans") or []) if int(v["id"]) not in vids]
        await sw_commit(s, fn)
        return
    def fn(p):
        for v in vids:
            _ensure_vlan(p, v)
    await sw_commit(s, fn)
    s.mode, s.data = "vlan", {"vids": vids}


async def sw_vlan_name(s, a, neg):
    vids = s.data["vids"]
    name = str(a[0])[:32]
    def fn(p):
        out = []
        for v in p.get("vlans") or []:
            if int(v["id"]) in vids:
                v = {**v, "name": f"VLAN{int(v['id']):04d}" if neg else name}
            out.append(v)
        p["vlans"] = out
    s.patch(fn)


async def sw_svi_ip(s, a, neg):
    vid = s.data["vid"]
    if neg:
        return await sw_commit(s, lambda p: p.__setitem__("svi", {**(p.get("svi") or {}), "ip": ""}))
    try:
        plen = m2len(a[1])
    except ValueError:
        raise CmdError(f"% Bad mask {a[1]} for address {a[0]}")
    cur = s.props.get("svi") or {}
    if cur.get("ip") and int(cur.get("vlan") or 1) != vid:
        await s.say(f"% patchyard: a Patchyard switch has one management SVI; moving it from Vlan{cur.get('vlan')} to Vlan{vid}")
    def fn(p):
        _ensure_vlan(p, vid)
        p["svi"] = {**(p.get("svi") or {}), "vlan": vid, "ip": f"{a[0]}/{plen}"}
    await sw_commit(s, fn)


async def sw_svi_shut(s, a, neg):
    if not neg:
        await s.say("% patchyard: the management SVI can't be shut; remove its address with 'no ip address' instead")


async def sw_default_gw(s, a, neg):
    await sw_commit(s, lambda p: p.__setitem__("svi", {**(p.get("svi") or {}), "gateway": "" if neg else str(a[0])}))


async def sw_hostname(s, a, neg):
    name = s.env.node_id if neg else str(a[0])
    s.patch(lambda p: p.__setitem__("hostname", name) if not neg else p.pop("hostname", None))
    s.host = name


async def sw_stp_prio(s, a, neg):
    prio = 32768 if neg else int(a[-1])
    if prio % 4096:
        raise CmdError("% Bridge Priority must be in increments of 4096.\n% Allowed values are:\n  0     4096  8192  12288 16384 20480 24576 28672\n  32768 36864 40960 45056 49152 53248 57344 61440")
    await sw_commit(s, lambda p: (p.__setitem__("stp_priority", prio), p.__setitem__("stp", True)))
    await s.say("% patchyard: one spanning tree for all VLANs (kernel 802.1D); the priority applies to the whole switch")


async def sw_stp_mode(s, a, neg):
    mode = str(a[0]).lower() if a else ""
    ovs = s.props.get("engine") == "ovs"
    await sw_commit(s, lambda p: p.__setitem__("stp", not neg))
    if ovs and mode.startswith("mst"):
        await s.say("% patchyard: MST isn't available; this switch runs RSTP (one instance, Open vSwitch)")
    elif ovs and mode not in ("rapid-pvst", "pvst", ""):
        await s.say(f"% patchyard: {mode} isn't available; running RSTP")
    elif not ovs and mode and mode != "pvst":
        await s.say(f"% patchyard: {mode} needs the Open vSwitch engine (Config → Global → Switch engine); this switch runs 802.1D")


async def sw_channel_group(s, a, neg):
    if s.props.get("engine") != "ovs":
        raise CmdError("% patchyard: EtherChannel needs the Open vSwitch engine (Config → Global → Switch engine)")
    ifs = _sw_ifs(s)
    if any(i.startswith("po") for i in ifs):
        raise CmdError("% channel-group goes on the member ports, not the port-channel")
    if not neg and not a:
        raise CmdError("% Incomplete command.")
    n = int(a[0]) if a else 0
    mode = str(a[1]).lower() if len(a) > 1 else "active"
    if mode not in ("active", "passive", "on"):
        raise CmdError("% patchyard: modes are active, passive (LACP) or on (static)")
    def fn(p):
        ports = dict(p.get("ports") or {})
        for i in ifs:
            c = dict(ports.get(i) or {})
            if neg:
                c.pop("channel", None)
            else:
                c["channel"] = n
            ports[i] = c
        p["ports"] = ports
        if not neg:
            ch = dict(p.get("channels") or {}); ch[str(n)] = {"mode": mode}; p["channels"] = ch
            if f"po{n}" not in ports:
                p["ports"][f"po{n}"] = {"mode": ports[ifs[0]].get("mode", "access"), "vlan": ports[ifs[0]].get("vlan", 1)}
    await sw_commit(s, fn)
    if not neg:
        await s.say(f"Creating a port-channel interface Port-channel {n}")


async def sw_show_etherchannel(s, a, neg):
    p = s.props
    if p.get("engine") != "ovs":
        return await s.say("% patchyard: EtherChannel needs the Open vSwitch engine")
    groups = {}
    for i, c in (p.get("ports") or {}).items():
        if (c or {}).get("channel"):
            groups.setdefault(int(c["channel"]), []).append(i)
    rc, out, _ = await s.env.io.sh("ovs-appctl bond/show 2>/dev/null")
    live = {}
    cur = None
    for ln in out.splitlines():
        m = re.match(r"---- po(\d+) ----", ln)
        if m:
            cur = int(m.group(1)); live[cur] = {}
        m = re.match(r"member (\S+): (enabled|disabled)", ln.strip())
        if m and cur is not None:
            live[cur][m.group(1)] = m.group(2)
    L = ["Flags:  D - down        P - bundled in port-channel", "        s - suspended   U - in use", "",
         f"Number of channel-groups in use: {len(groups)}", "",
         "Group  Port-channel  Protocol    Ports", "------+-------------+-----------+-----------------------------------------------"]
    for n, ms in sorted(groups.items()):
        mode = ((p.get("channels") or {}).get(str(n)) or {}).get("mode", "active")
        flags = "".join(f"{s.short(m)}({'P' if live.get(n, {}).get(m) == 'enabled' else 'D'}) " for m in sorted(ms, key=lambda x: int(x[3:])))
        L.append(f"{n:<7}Po{n}({'SU' if n in live else 'SD'}){'':<5}{'LACP' if mode != 'on' else '-':<12}{flags}")
    await s.say("\n".join(L))



async def sw_stp_off(s, a, neg):
    # "no spanning-tree vlan 1" -> off
    await sw_commit(s, lambda p: p.__setitem__("stp", not neg))


async def sw_write(s, a, neg):
    await s.say("Building configuration...\n[OK]\n(patchyard: switch settings are saved as you type them)")


async def sw_show_run(s, a, neg):
    await s.say(switch_config(s))


async def sw_show_vlan(s, a, neg):
    p = s.props
    vl = p.get("vlans") or [{"id": 1, "name": "default"}]
    if not any(int(v["id"]) == 1 for v in vl):
        vl = [{"id": 1, "name": "default"}] + list(vl)
    ports = p.get("ports") or {}
    allp = [x["if"] for x in (hardware.ports(s.node) or [])] or sorted({l[f"{e}_if"] for l in s.lab["links"] for e in ("a", "b") if l[e] == s.node["id"]}, key=lambda x: int(re.sub(r"\D", "", x) or 0))
    lines = [f"{'VLAN':<5}{'Name':<33}{'Status':<10}Ports", f"{'----':<5}{'-' * 32:<33}{'-' * 9:<10}{'-' * 31}"]
    for v in sorted(vl, key=lambda x: int(x["id"])):
        members = [s.short(i) for i in allp if (ports.get(i) or {}).get("mode", "access") == "access" and int((ports.get(i) or {}).get("vlan") or 1) == int(v["id"])]
        chunks = [", ".join(members[k:k + 4]) for k in range(0, len(members), 4)] or [""]
        lines.append(f"{v['id']:<5}{v['name'][:32]:<33}{'active':<10}{chunks[0]}")
        lines += [" " * 48 + c for c in chunks[1:]]
    await s.say("\n".join(lines))


async def sw_show_trunk(s, a, neg):
    ports = s.props.get("ports") or {}
    tr = [(i, c) for i, c in ports.items() if (c or {}).get("mode") == "trunk"]
    if not tr:
        return await s.say("")
    l1 = [f"{'Port':<12}{'Mode':<13}{'Encapsulation':<15}{'Status':<14}Native vlan"]
    l2 = [f"{'Port':<12}Vlans allowed on trunk"]
    for i, c in sorted(tr, key=lambda x: int(re.sub(r"\D", "", x[0]) or 0)):
        l1.append(f"{s.short(i):<12}{'on':<13}{'802.1q':<15}{'trunking':<14}{c.get('native') or 1}")
        l2.append(f"{s.short(i):<12}{c.get('allowed') or '1-4094'}")
    await s.say("\n".join(l1) + "\n\n" + "\n".join(l2))


async def sw_show_status(s, a, neg):
    kern = {k["ifname"]: k for k in await kernel_ifaces(s)}
    ports = s.props.get("ports") or {}
    allp = hardware.ports(s.node) or [{"if": i, "kbit": None, "type": "gi", "media": "copper"} for i in sorted(kern) if re.fullmatch(r"eth[1-9]\d*", i)]
    lines = [f"{'Port':<10}{'Name':<19}{'Status':<13}{'Vlan':<11}{'Duplex':<7}{'Speed':<7}Type"]
    for p in allp:
        c = ports.get(p["if"]) or {}
        k = kern.get(p["if"])
        st = "disabled" if c.get("shutdown") else ("connected" if k and "LOWER_UP" in k.get("flags", []) else "notconnect")
        vl = "trunk" if c.get("mode") == "trunk" else str(c.get("vlan") or 1)
        sp = {100_000: "100", 1_000_000: "1000", 1_544: "1.5M"}.get(p.get("kbit"), "10G" if p.get("type") == "te" else "auto")
        typ = hardware.PORT_TYPES.get(p.get("type"), {}).get("label", "")
        lines.append(f"{s.short(p['if']):<10}{(c.get('description') or '')[:18]:<19}{st:<13}{vl:<11}{'full':<7}{sp:<7}{typ}")
    await s.say("\n".join(lines))


async def sw_show_mac_ovs(s):
    rc, out, _ = await s.env.io.sh("ovs-vsctl --bare --columns=name,ofport list interface; echo ===; ovs-appctl fdb/show br0")
    head, _, fdb = out.partition("===")
    words = head.split()
    ofp = {words[i + 1]: words[i] for i in range(0, len(words) - 1, 2)}
    chan = {i: f"po{c['channel']}" for i, c in (s.props.get("ports") or {}).items() if (c or {}).get("channel")}
    lines = ["          Mac Address Table", "-------------------------------------------", "",
             f"{'Vlan':<8}{'Mac Address':<19}{'Type':<12}Ports", f"{'----':<8}{'-----------':<19}{'--------':<12}-----"]
    n = 0
    for ln in fdb.splitlines()[1:]:
        f = ln.split()
        if len(f) < 3 or f[0] == "LOCAL":
            continue
        mac = f[2].replace(":", "")
        port = ofp.get(f[0], f[0])
        port = chan.get(port, port)
        lines.append(f"{f[1]:<8}{mac[0:4] + '.' + mac[4:8] + '.' + mac[8:12]:<19}{'DYNAMIC':<12}{s.short(port)}")
        n += 1
    lines.append(f"Total Mac Addresses for this criterion: {n}")
    await s.say("\n".join(lines))


async def sw_show_mac(s, a, neg):
    if s.props.get("engine") == "ovs":
        return await sw_show_mac_ovs(s)
    rc, out, _ = await s.env.io.ns("bridge", "-j", "fdb", "show", "br", "br0")
    try:
        rows = json.loads(out) if rc == 0 else []
    except ValueError:
        rows = []
    lines = ["          Mac Address Table", "-------------------------------------------", "",
             f"{'Vlan':<8}{'Mac Address':<19}{'Type':<12}Ports", f"{'----':<8}{'-----------':<19}{'--------':<12}-----"]
    n = 0
    for r in rows:
        if "permanent" in r.get("state", "") or "self" in r.get("flags", []) or not r.get("vlan") and r.get("ifname") == "br0":
            continue
        if r.get("ifname") == "br0" or r.get("state") == "permanent":
            continue
        mac = r.get("mac", "").replace(":", "")
        lines.append(f"{str(r.get('vlan', 1)):<8}{mac[0:4] + '.' + mac[4:8] + '.' + mac[8:12]:<19}{'DYNAMIC':<12}{s.short(r.get('ifname', ''))}")
        n += 1
    lines.append(f"Total Mac Addresses for this criterion: {n}")
    await s.say("\n".join(lines))


async def sw_show_stp(s, a, neg):
    p = s.props
    if p.get("engine") == "ovs":
        if not p.get("stp"):
            return await s.say("No spanning tree instance exists.")
        rc, out, _ = await s.env.io.sh("ovs-appctl rstp/show br0 2>&1")
        return await s.say("VLAN0001  (patchyard: Open vSwitch RSTP, one instance for all VLANs)\n  Spanning tree enabled protocol rstp\n\n" + s.tr_out(out))
    if not p.get("stp"):
        return await s.say("No spanning tree instance exists.")
    script = ("cd /sys/class/net/br0/bridge && echo $(cat root_id) $(cat bridge_id) $(cat root_port) $(cat root_path_cost) "
              "$(cat hello_time) $(cat max_age) $(cat forward_delay); "
              "for i in /sys/class/net/br0/brif/*; do echo $(basename $i) $(cat $i/state) $(cat $i/path_cost) $(cat $i/port_no) $(cat $i/designated_root) $(cat $i/designated_bridge); done")
    rc, out, _ = await s.env.io.ns("sh", "-c", script)
    lines = out.split("\n")
    if rc or not lines or len(lines[0].split()) < 7:
        raise CmdError("% spanning tree state isn't available (is the switch running?)")
    root, me, rport, rcost, hello, maxage, fwd = lines[0].split()[:7]

    def bid(x):
        prio, _, mac = x.partition(".")
        mac = mac.replace(":", "")
        return int(prio, 16), f"{mac[0:4]}.{mac[4:8]}.{mac[8:12]}"
    rp, rm = bid(root)
    mp, mm = bid(me)
    portno = {}
    states = {0: "DIS", 1: "LIS", 2: "LRN", 3: "FWD", 4: "BLK"}
    rows = []
    for ln in lines[1:]:
        f = ln.split()
        if len(f) < 6:
            continue
        ifn, st, cost, pno = f[0], int(f[1]), f[2], int(f[3], 16) if f[3].startswith("0x") else int(f[3])
        portno[pno] = ifn
        role = "Root" if str(pno) == str(int(rport, 16) if rport.startswith("0x") else rport) and rport not in ("0", "0x0") else ("Altn" if st == 4 else "Desg")
        rows.append(f"{s.short(ifn):<20}{role:<5}{states.get(st, '?'):<4}{cost:<10}{'128.' + str(pno):<9}P2p")
    tick = lambda v: int(int(v) / 100) if v.isdigit() else v
    out = ["VLAN0001  (patchyard: one 802.1D instance covers all VLANs)", "  Spanning tree enabled protocol ieee",
           f"  Root ID    Priority    {rp}", f"             Address     {rm}"]
    out.append("             This bridge is the root" if root == me else f"             Cost        {rcost}\n             Port        {rport}")
    out += [f"             Hello Time   {tick(hello)} sec  Max Age {tick(maxage)} sec  Forward Delay {tick(fwd)} sec", "",
            f"  Bridge ID  Priority    {mp}", f"             Address     {mm}", "",
            f"{'Interface':<20}{'Role':<5}{'Sts':<4}{'Cost':<10}{'Prio.Nbr':<9}Type", "-" * 19 + " ---- --- --------- -------- ----"] + rows
    await s.say("\n".join(out))


async def sw_show_ip_int_brief(s, a, neg):
    kern = {k["ifname"]: k for k in await kernel_ifaces(s)}
    ports = s.props.get("ports") or {}
    allp = [x["if"] for x in hardware.ports(s.node) or []] or sorted([i for i in kern if re.fullmatch(r"eth[1-9]\d*", i)], key=lambda x: int(x[3:]))
    rows = []
    svi = s.props.get("svi") or {}
    if svi.get("ip"):
        rows.append(f"{'Vlan' + str(svi.get('vlan') or 1):<27}{svi['ip'].split('/')[0]:<16}YES manual up                    up")
    for i in allp:
        k = kern.get(i)
        c = ports.get(i) or {}
        st = "administratively down" if c.get("shutdown") else ("up" if k and "LOWER_UP" in k.get("flags", []) else "down")
        rows.append(f"{s.ifname(i):<27}{'unassigned':<16}YES unset  {st:<22}{'up' if st == 'up' else 'down'}")
    await s.say(f"{'Interface':<27}{'IP-Address':<16}OK? Method Status                Protocol\n" + "\n".join(rows))


async def sw_show_int(s, a, neg):
    return await r_show_interfaces(s, a, neg)


# --------------------------------------------------------------------------- config rendering

# ---- router: ACLs, NAT, DHCP relay, VRFs (props + l3.py; applied into the router's namespace)
def _l3_err(e):
    msg = str(e)
    return CmdError(msg if msg.startswith("%") else f"% {msg}")


async def l3_apply(s):
    errs = await s.env.apply_l3()
    for e in errs or []:
        await s.say(f"% patchyard: {e}")


def _ifcfg_set(s, ifn, key, value):
    def fn(p):
        cfg = {k: dict(v) for k, v in (p.get("ifcfg") or {}).items()}
        c = cfg.setdefault(ifn, {})
        if value in (None, "", [], False):
            c.pop(key, None)
        else:
            c[key] = value
        if not c:
            cfg.pop(ifn, None)
        p["ifcfg"] = cfg
    s.patch(fn)


def _ifcfg(s, ifn):
    return dict((s.props.get("ifcfg") or {}).get(ifn) or {})


async def r_acl_numbered(s, a, neg):
    from . import l3
    num, text = str(a[0]), (str(a[1]) if len(a) > 1 else "")
    try:
        l3.acl_kind(num)
    except l3.AclError as e:
        raise _l3_err(e)
    if neg:                                  # IOS: 'no access-list N ...' removes the whole list
        acls = dict(s.props.get("acls") or {})
        acls.pop(num, None)
        s.patch(lambda p: p.__setitem__("acls", acls))
        return await l3_apply(s)
    if not text:
        raise CmdError("% Incomplete command.")
    try:
        acls = l3.add_entry(s.props.get("acls") or {}, num, text)
    except l3.AclError as e:
        raise _l3_err(e)
    s.patch(lambda p: p.__setitem__("acls", acls))
    await l3_apply(s)


async def r_ip_acl(s, a, neg, kind="standard"):
    from . import l3
    name = str(a[0])
    try:
        k = l3.acl_kind(name)
    except l3.AclError as e:
        raise _l3_err(e)
    if k and k != kind:
        raise CmdError(f"% {name} is a{'n' if k == 'extended' else ''} {k} access list number")
    acls = dict(s.props.get("acls") or {})
    if neg:
        acls.pop(name, None)
        s.patch(lambda p: p.__setitem__("acls", acls))
        return await l3_apply(s)
    if name in acls and acls[name]["type"] != kind:
        raise CmdError(f"% A {acls[name]['type']} access list named {name} already exists")
    if name not in acls:
        acls[name] = {"type": kind, "entries": []}
        s.patch(lambda p: p.__setitem__("acls", acls))
    s.mode = "acl-std" if kind == "standard" else "acl-ext"
    s.ctx, s.data = [], {"acl": name, "kind": kind}


def ip_acl(kind):
    async def h(s, a, neg):
        return await r_ip_acl(s, a, neg, kind)
    return h


async def r_ace(s, a, neg, action=None):
    """Inside 'ip access-list': [seq] permit|deny|remark ...; 'no <seq>' or 'no permit ...'."""
    from . import l3
    name, kind = s.data["acl"], s.data["kind"]
    seq = None
    if action is None:                       # "<seq> ..." or "<seq>" (only with no)
        seq = int(a[0])
        text = str(a[1]) if len(a) > 1 else ""
    else:
        text = f"{action} {a[0]}" if a else action
    acls = dict(s.props.get("acls") or {})
    cur = acls.get(name) or {"type": kind, "entries": []}
    if neg:
        ents = cur["entries"]
        if seq is not None:
            ents = [e for e in ents if e["seq"] != seq]
        else:
            ents = [e for e in ents if e["text"] != " ".join(text.split())]
        acls[name] = {**cur, "entries": ents}
        s.patch(lambda p: p.__setitem__("acls", acls))
        return await l3_apply(s)
    if not text or text.split()[0].lower() not in ("permit", "deny", "remark"):
        raise CmdError("% Incomplete command.")
    try:
        acls = l3.add_entry(acls, name, text, seq=seq, kind=kind)
    except l3.AclError as e:
        raise _l3_err(e)
    s.patch(lambda p: p.__setitem__("acls", acls))
    await l3_apply(s)


def ace(action):
    async def h(s, a, neg):
        return await r_ace(s, a, neg, action)
    return h


async def r_access_group(s, a, neg):
    from . import l3
    name, d = str(a[0]), str(a[1]).lower()
    try:
        l3.acl_kind(name)
    except l3.AclError as e:
        raise _l3_err(e)
    if d not in ("in", "out"):
        raise CmdError("% Invalid input: in or out")
    _ifcfg_set(s, s.data["ifn"], f"acl_{d}", None if neg else name)
    await l3_apply(s)


async def r_ip_nat_if(s, a, neg):
    side = str(a[0]).lower()
    if side not in ("inside", "outside"):
        raise CmdError("% Invalid input: inside or outside")
    cur = _ifcfg(s, s.data["ifn"]).get("nat")
    if neg and cur != side:
        return
    _ifcfg_set(s, s.data["ifn"], "nat", None if neg else side)
    await l3_apply(s)


def _nat_update(s, fn):
    from . import l3
    nat = {"pools": dict((s.props.get("nat") or {}).get("pools") or {}), "rules": list((s.props.get("nat") or {}).get("rules") or [])}
    fn(nat)
    try:
        l3.normalise_nat(nat)
    except (l3.AclError, ValueError, KeyError) as e:
        raise _l3_err(e)
    s.patch(lambda p: p.__setitem__("nat", nat))


async def r_nat_list(s, a, neg, target="interface", overload=False):
    acl = str(a[0])
    rule = {"type": "list", "acl": acl, "overload": overload}
    if target == "interface":
        rule["interface"] = s.resolve(a[1]); rule["overload"] = True
    else:
        rule["pool"] = str(a[1])

    def fn(nat):
        nat["rules"] = [r for r in nat["rules"] if not (r["type"] == "list" and r["acl"] == acl)]
        if not neg:
            if target == "pool" and rule["pool"] not in nat["pools"]:
                raise CmdError(f"% Pool {rule['pool']} not found")
            nat["rules"].append(rule)
    _nat_update(s, fn)
    await l3_apply(s)


def nat_list(target, overload=False):
    async def h(s, a, neg):
        return await r_nat_list(s, a, neg, target, overload)
    return h


async def r_nat_static(s, a, neg):
    local, glob = str(a[0]), str(a[1])

    def fn(nat):
        nat["rules"] = [r for r in nat["rules"] if not (r["type"] == "static" and r["local"] == local)]
        if not neg:
            if any(r.get("global") == glob for r in nat["rules"]):
                raise CmdError(f"% similar static entry ({glob}) already exists")
            nat["rules"].append({"type": "static", "local": local, "global": glob})
    _nat_update(s, fn)
    await l3_apply(s)


async def r_nat_static_port(s, a, neg, proto="tcp", iface=False):
    local, lport = str(a[0]), int(a[1])
    rule = {"type": "static-port", "proto": proto, "local": local, "lport": lport, "gport": int(a[3])}
    if iface:
        rule["interface"] = s.resolve(a[2])
    else:
        rule["global"] = str(a[2])

    def fn(nat):
        nat["rules"] = [r for r in nat["rules"] if not (r["type"] == "static-port" and r["proto"] == proto and r["gport"] == rule["gport"]
                                                        and r.get("global") == rule.get("global") and r.get("interface") == rule.get("interface"))]
        if not neg:
            nat["rules"].append(rule)
    _nat_update(s, fn)
    await l3_apply(s)


def nat_static_port(proto, iface=False):
    async def h(s, a, neg):
        return await r_nat_static_port(s, a, neg, proto, iface)
    return h


async def r_nat_pool(s, a, neg):
    name = str(a[0])
    if neg:
        def fn(nat):
            if any(r.get("pool") == name for r in nat["rules"]):
                raise CmdError(f"% Pool {name} in use, cannot destroy")
            nat["pools"].pop(name, None)
        _nat_update(s, fn)
        return await l3_apply(s)
    if len(a) < 4:
        raise CmdError("% Incomplete command.")
    start, end, m = str(a[1]), str(a[2]), a[3]
    mask = len2mask(int(m)) if isinstance(m, int) else str(m)
    _nat_update(s, lambda nat: nat["pools"].__setitem__(name, {"start": start, "end": end, "mask": mask}))
    await l3_apply(s)


async def r_show_acl(s, a, neg):
    from . import l3
    cnt = {}
    try:
        rc, out, _ = await s.env.io.ns("nft", "-j", "list", "ruleset")
        if rc == 0:
            cnt = l3.counters(out)
    except CmdError:
        pass
    await s.w(l3.show_access_lists(s.props.get("acls") or {}, cnt, str(a[0]) if a else None))


async def r_clear_acl(s, a, neg):
    for t in ("nl_acl", "nl_nat"):
        await s.env.io.ns("nft", "reset", "counters", "table", "ip", t)


async def r_show_nat(s, a, neg):
    from . import l3
    rc, out, err = await s.env.io.ns("conntrack", "-L", "-n")
    if rc and "conntrack" in (err or "") and "not found" in (err or ""):
        raise CmdError("% patchyard: conntrack isn't installed in the app image")
    await s.w(l3.show_translations(l3.nat_translations(out, s.props)))


async def r_show_nat_stats(s, a, neg):
    cfg = s.props.get("ifcfg") or {}
    ins = [s.ifname(i) for i, c in cfg.items() if c.get("nat") == "inside"]
    outs = [s.ifname(i) for i, c in cfg.items() if c.get("nat") == "outside"]
    rc, out, _ = await s.env.io.ns("conntrack", "-L", "-n")
    from . import l3
    rows = l3.nat_translations(out, s.props)
    rules = (s.props.get("nat") or {}).get("rules") or []
    L = [f"Total active translations: {len(rows)} ({sum(1 for r in rules if r['type'] != 'list')} static, {len(rows) - sum(1 for r in rules if r['type'] == 'static')} dynamic)",
         "Outside interfaces:", "  " + (", ".join(outs) or "none"), "Inside interfaces:", "  " + (", ".join(ins) or "none"),
         "Dynamic mappings:"]
    for r in rules:
        if r["type"] == "list":
            L.append(f"-- Inside Source\n[Id: {rules.index(r) + 1}] access-list {r['acl']} " +
                     (f"interface {s.ifname(r['interface'])}" if r.get("interface") else f"pool {r['pool']}"))
    await s.say("\n".join(L))


async def r_clear_nat(s, a, neg):
    await s.env.io.ns("conntrack", "-D", "--src-nat")
    await s.env.io.ns("conntrack", "-D", "--dst-nat")


async def r_helper(s, a, neg):
    ifn, ip = s.data["ifn"], str(a[0])
    cur = [h for h in _ifcfg(s, ifn).get("helpers", []) if h != ip]
    if not neg:
        cur.append(ip)
    _ifcfg_set(s, ifn, "helpers", cur[:4])
    await l3_apply(s)


# VRFs
async def r_vrf_def(s, a, neg, style="definition"):
    from . import l3
    name = str(a[0])
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,14}", name):
        raise CmdError("% patchyard: VRF names: letters, digits, _ and -, up to 15 characters")
    vrfs = {k: dict(v) for k, v in (s.props.get("vrfs") or {}).items()}
    if neg:
        vrfs.pop(name, None)

        def fn(p):
            p["vrfs"] = vrfs
            p["ifcfg"] = {k: {kk: vv for kk, vv in v.items() if not (kk == "vrf" and vv == name)} for k, v in (p.get("ifcfg") or {}).items()}
            p["ifcfg"] = {k: v for k, v in p["ifcfg"].items() if v}
        s.patch(fn)
        await s.env.io.ns("ip", "link", "del", name)
        return
    if name not in vrfs:
        vrfs[name] = {"style": style, "table": l3.vrf_table(vrfs, name), "import": [], "export": []}
        s.patch(lambda p: p.__setitem__("vrfs", vrfs))
        await l3_apply(s)
    s.mode, s.ctx, s.data = "vrf", [], {"vrf": name}


def vrf_def(style):
    async def h(s, a, neg):
        return await r_vrf_def(s, a, neg, style)
    return h


def _vrf_patch(s, fn):
    name = s.data["vrf"]
    vrfs = {k: dict(v) for k, v in (s.props.get("vrfs") or {}).items()}
    v = vrfs.setdefault(name, {"style": "definition", "import": [], "export": []})
    v["import"], v["export"] = list(v.get("import") or []), list(v.get("export") or [])
    fn(v)
    s.patch(lambda p: p.__setitem__("vrfs", vrfs))


async def r_vrf_rd(s, a, neg):
    from . import l3
    rd = str(a[0])
    if not neg:
        try:
            l3.check_rd(rd)
        except l3.AclError as e:
            raise _l3_err(e)
    _vrf_patch(s, lambda v: v.__setitem__("rd", "" if neg else rd))
    await vrf_sync_bgp(s, s.data["vrf"])


async def r_vrf_rt(s, a, neg):
    from . import l3
    which, rt = str(a[0]).lower(), str(a[1])
    if which not in ("import", "export", "both"):
        raise CmdError("% Invalid input: import, export or both")
    try:
        l3.check_rd(rt)
    except l3.AclError as e:
        raise _l3_err(e)

    def fn(v):
        for k in (("import", "export") if which == "both" else (which,)):
            v[k] = [x for x in v[k] if x != rt] + ([] if neg else [rt])
    _vrf_patch(s, fn)
    await vrf_sync_bgp(s, s.data["vrf"])


async def r_vrf_af(s, a, neg):
    s.mode = "vrf-af"


async def r_vrf_exit_af(s, a, neg):
    s.mode = "vrf"


async def vrf_sync_bgp(s, name):
    """Cisco keeps rd/route-targets under the VRF; FRR wants them under 'router bgp N vrf X'."""
    v = (s.props.get("vrfs") or {}).get(name) or {}
    _, run = await s.env.io.vtysh("show running-config")
    m = re.search(rf"^router bgp (\d+) vrf {re.escape(name)}$", run, re.M)
    if not m or not v.get("rd"):
        return
    ctx = [f"router bgp {m.group(1)} vrf {name}", "address-family ipv4 unicast"]
    lines = [f"rd vpn export {v['rd']}", "label vpn export auto"]
    if v.get("import"):
        lines.append("rt vpn import " + " ".join(v["import"]))
    if v.get("export"):
        lines.append("rt vpn export " + " ".join(v["export"]))
    lines += ["export vpn", "import vpn"]
    await s.frr_do(*lines, ctx=ctx)


async def r_vrf_forwarding(s, a, neg):
    ifn, name = s.data["ifn"], str(a[-1])
    if not neg and name not in (s.props.get("vrfs") or {}):
        raise CmdError(f"% VRF {name} not configured.")
    cur = _ifcfg(s, ifn).get("vrf")
    if (neg and cur != name) or (not neg and cur == name):
        return
    _, out = await s.env.io.vtysh("show running-config")
    addrs, inside = [], False
    for ln in out.splitlines():
        if ln.startswith("interface "):
            inside = ln.split()[1] == ifn
        elif inside and ln.strip().startswith("ip address "):
            addrs.append(ln.strip())
    if addrs:
        await s.frr(*[f"no {x}" for x in addrs])
        await s.say(f"% Interface {s.ifname(ifn)} IPv4 disabled and address(es) removed due to {'disabling' if neg else 'enabling'} VRF {name}")
    _ifcfg_set(s, ifn, "vrf", None if neg else name)
    await l3_apply(s)


async def r_show_vrf(s, a, neg):
    vrfs = s.props.get("vrfs") or {}
    cfg = s.props.get("ifcfg") or {}
    L = [f"  {'Name':<32}{'Default RD':<21}Interfaces"]
    for name, v in sorted(vrfs.items()):
        ifs = [s.short(i) for i, c in sorted(cfg.items()) if c.get("vrf") == name]
        L.append(f"  {name:<32}{(v.get('rd') or '<not set>'):<21}{ifs[0] if ifs else ''}")
        L += [f"  {'':<53}{i}" for i in ifs[1:]]
    rc, out = await s.env.io.vtysh("show vrf")
    extra = [ln.split()[1] for ln in out.splitlines() if ln.startswith("vrf ") and len(ln.split()) > 1 and ln.split()[1] not in vrfs]
    L += [f"  {n:<32}{'<not set>':<21}(created outside the IOS CLI)" for n in extra]
    await s.say("\n".join(L))



def _with_l3(s, out: list[str]) -> list[str]:
    """Add the props-backed router config (VRFs, ACLs, NAT, helpers) to the FRR-derived lines."""
    from . import l3
    p = getattr(s, "props", None) or {}
    cfg = {k: v for k, v in (p.get("ifcfg") or {}).items() if v}
    res, seen, cur = [], set(), None
    for ln in out:
        m = re.match(r"^interface (\S+)$", ln)
        if m:
            cur = m.group(1)
            seen.add(cur)
            res.append(ln)
            extra = l3.iface_lines(cfg.get(cur) or {})
            res += extra
            continue
        if ln.startswith(" vrf forwarding ") and cur and (cfg.get(cur) or {}).get("vrf"):
            continue                                     # already added from props
        if not ln.startswith(" "):
            cur = None
        res.append(ln)
    missing = [i for i in cfg if i not in seen and l3.iface_lines(cfg[i])]
    if missing:
        at = next((k for k, l in enumerate(res) if l.startswith(("router ", "ip route", "line ", "end", "mpls ", "vrf ", "bgp ", "segment"))), len(res))
        blk = []
        for i in sorted(missing):
            blk += [f"interface {i}"] + l3.iface_lines(cfg[i]) + ["!"]
        res[at:at] = blk
    head = l3.vrf_lines(p.get("vrfs") or {})
    if head:
        at = next((k + 1 for k, l in enumerate(res) if l.startswith("hostname ")), 0)
        res[at:at] = ["!"] + head
    tail = l3.nat_lines(p.get("nat") or {}, lambda i: i) + ["!"] + l3.acl_lines(p.get("acls") or {})
    if len(tail) > 1:
        at = next((k for k, l in enumerate(res) if l.startswith(("line ", "end"))), len(res))
        res[at:at] = tail + ["!"]
    return res


def ios_config(s, frr_text: str, iface=None) -> str:
    """FRR running/startup config -> IOS-looking config."""
    out, section = [], None
    lines = frr_text.splitlines()
    host = s.host
    for ln in lines:
        st = ln.strip()
        if not st or st.startswith(("Building configuration", "Current configuration", "frr version", "frr defaults",
                                    "service integrated-vtysh-config", "log syslog", "no ipv6 forwarding", "ip forwarding", "no ip forwarding")):
            continue
        if st in ("exit", "exit-vrf"):
            if not ln.startswith(" "):
                out.append("!")
                section = None
            continue
        if not ln.startswith(" "):
            section = st.split()[0] if st != "!" else section
        m = re.match(r"^(\s*)ip address (\S+)/(\d+)(.*)$", ln)
        if m:
            ln = f"{m.group(1)}ip address {m.group(2)} {len2mask(int(m.group(3)))}{m.group(4)}"
        m = re.match(r"^router ospf(.*)$", ln)
        if m:
            ln = f"router ospf 1{m.group(1)}"
        m = re.match(r"^(\s*)ospf router-id (.*)$", ln)
        if m:
            ln = f"{m.group(1)}router-id {m.group(2)}"
        m = re.match(r"^(\s*)ip ospf area (.*)$", ln)
        if m:
            ln = f"{m.group(1)}ip ospf 1 area {m.group(2)}"
        m = re.match(r"^(\s*)network (\S+)/(\d+) area (.*)$", ln)
        if m:
            ln = f"{m.group(1)}network {m.group(2)} {len2wild(int(m.group(3)))} area {m.group(4)}"
        m = re.match(r"^(\s+)network (\S+)/(\d+)(.*)$", ln)
        if m and section == "router":
            ln = f"{m.group(1)}network {m.group(2)} mask {len2mask(int(m.group(3)))}{m.group(4)}"
        m = re.match(r"^ip route (\S+)/(\d+) (.*)$", ln)
        if m:
            ln = f"ip route {m.group(1)} {len2mask(int(m.group(2)))} {m.group(3)}"
        m = re.match(r"^interface (\S+) vrf (\S+)$", ln)
        if m:
            out.append(f"interface {m.group(1)}")
            ln = f" vrf forwarding {m.group(2)}"
        m = re.match(r"^(\s*)bandwidth (\d+)$", ln)
        if m:
            ln = f"{m.group(1)}bandwidth {int(m.group(2)) * 1000}"
        out.append(ln)
    out = _with_l3(s, out)
    text = s.tr_out("\n".join(out))
    if iface:
        want = s.ifname(iface)
        blk, take = [], False
        for ln in text.splitlines():
            if ln.startswith("interface "):
                take = ln.split(" ", 1)[1] == want
            elif not ln.startswith(" "):
                take = False
            if take:
                blk.append(ln)
        return "\n".join(blk + ["end"]) if blk else f"% Interface {want} has no configuration\n"
    ios = s.ios_props()
    head = ["!", "version FRRouting (shown through the Patchyard IOS CLI)", "!"]
    extras = [x for x in ios.get("extra", [])] + [f"logging host {h}" for h in ios.get("logging_hosts", [])]
    if ios.get("enable_secret"):
        extras.append(f"enable secret 9 $nl${ios['enable_secret'][:22]}")
    body = text.splitlines()
    idx = next((i for i, l in enumerate(body) if l.startswith("hostname ")), -1)
    if idx < 0:
        body.insert(0, f"hostname {host}")
        idx = 0
    body[idx + 1:idx + 1] = ["!"] + extras if extras else []
    tail = []
    if ios.get("banner"):
        tail += ["banner motd ^C", ios["banner"], "^C", "!"]
    for name, cmds in (ios.get("lines") or {}).items():
        tail += [name] + [f" {c}" for c in cmds] + ["!"]
    if body and body[-1] == "end":
        body = body[:-1] + tail + ["end"]
    else:
        body += tail + ["end"]
    out2, prev = [], None
    for l in head + body:
        if l == "!" and prev == "!":
            continue
        out2.append(l); prev = l
    return "\n".join(out2) + "\n"


def switch_config(s) -> str:
    p = s.props
    ios = s.ios_props()
    ovs = p.get("engine") == "ovs"
    L = ["Building configuration...", "", "Current configuration :", "!", f"version patchyard-switch ({'Open vSwitch' if ovs else 'Linux bridge'})", "!",
         f"hostname {s.host}", "!"]
    L += ios.get("extra", []) + [f"logging host {h}" for h in ios.get("logging_hosts", [])]
    if ios.get("enable_secret"):
        L.append(f"enable secret 9 $nl${ios['enable_secret'][:22]}")
    L.append("!")
    if p.get("stp"):
        L.append("spanning-tree mode rapid-pvst   ! patchyard: RSTP, one instance" if ovs else "spanning-tree mode pvst   ! patchyard: 802.1D, one instance")
        if int(p.get("stp_priority") or 32768) != 32768:
            L.append(f"spanning-tree vlan 1-4094 priority {p.get('stp_priority')}")
    else:
        L.append("no spanning-tree vlan 1-4094")
    L.append("!")
    for v in sorted(p.get("vlans") or [], key=lambda x: int(x["id"])):
        if int(v["id"]) == 1:
            continue
        L += [f"vlan {v['id']}", f" name {v['name']}", "!"]
    ports = p.get("ports") or {}
    allp = [x["if"] for x in hardware.ports(s.node) or []] or sorted((x for x in ports if x.startswith("eth")), key=lambda x: int(re.sub(r"\D", "", x) or 0))
    pos = sorted((x for x in ports if re.fullmatch(r"po\d+", x)), key=lambda x: int(x[2:])) if ovs else []
    for i in pos + allp:
        c = ports.get(i) or {}
        L.append(f"interface {s.ifname(i)}")
        if c.get("channel") and ovs:
            mode = ((p.get("channels") or {}).get(str(c["channel"])) or {}).get("mode", "active")
            L += [f" channel-group {c['channel']} mode {mode}"] + ([" shutdown"] if c.get("shutdown") else []) + ["!"]
            continue
        if c.get("description"):
            L.append(f" description {c['description']}")
        if c.get("mode") == "trunk":
            if int(c.get("native") or 1) != 1:
                L.append(f" switchport trunk native vlan {c['native']}")
            if c.get("allowed"):
                L.append(f" switchport trunk allowed vlan {c['allowed']}")
            L.append(" switchport mode trunk")
        else:
            if int(c.get("vlan") or 1) != 1:
                L.append(f" switchport access vlan {c['vlan']}")
            if c.get("mode") == "access":
                L.append(" switchport mode access")
        if c.get("portfast"):
            L.append(" spanning-tree portfast")
        if c.get("shutdown"):
            L.append(" shutdown")
        L.append("!")
    svi = p.get("svi") or {}
    if svi.get("ip"):
        ip, _, pl = svi["ip"].partition("/")
        L += [f"interface Vlan{svi.get('vlan') or 1}", f" ip address {ip} {len2mask(int(pl or 24))}", "!"]
    if svi.get("gateway"):
        L += [f"ip default-gateway {svi['gateway']}", "!"]
    if ios.get("banner"):
        L += ["banner motd ^C", ios["banner"], "^C", "!"]
    for name, cmds in (ios.get("lines") or {}).items():
        L += [name] + [f" {c}" for c in cmds] + ["!"]
    L.append("end")
    body = "\n".join(L[3:])
    return "\n".join(L[:2]) + f"\nCurrent configuration : {len(body)} bytes\n" + body


# --------------------------------------------------------------------------- grammars

def _common_exec(g, priv):
    g.add("enable", h_enable).add("disable", h_disable).add("exit", h_logout).add("logout", h_logout).add("quit", h_logout)
    g.add("terminal length <0-512>", h_terminal_noop).add("terminal monitor", h_terminal_noop).add("terminal no monitor", h_terminal_noop)
    g.add("show history", h_show_history).add("show clock", h_show_clock).add("show version", h_show_version)
    g.add("show inventory", h_show_inventory).add("show cdp neighbors", h_show_cdp).add("show cdp neighbors detail", h_show_cdp)
    g.add("ping HOST", h_ping).add("ping HOST LINE", h_ping).add("ping vrf WORD HOST", h_ping_vrf).add("ping vrf WORD HOST LINE", h_ping_vrf)
    g.add("traceroute HOST", h_traceroute).add("traceroute vrf WORD HOST", h_trace_vrf)
    if priv:
        g.add("configure terminal", h_conf_t).add("reload", h_reload)
    return g


def _common_config(g):
    g.add("exit", h_exit).add("end", h_end).add("do LINE", h_do).add("no LINE", h_no)
    return g


def _global_config(g, sw):
    g.add("enable secret LINE", h_enable_secret).add("enable password LINE", h_enable_secret)
    g.add("banner motd LINE", h_banner)
    g.add("line console <0-0>", h_line).add("line vty <0-15> <0-15>", h_line).add("line vty <0-15>", h_line).add("line aux <0-0>", h_line)
    g.add("logging host A.B.C.D", h_logging_host).add("logging A.B.C.D", h_logging_host)
    for pre in ("service LINE", "logging LINE", "ntp LINE", "ip domain-lookup", "ip domain-name WORD", "ip domain lookup", "ip name-server LINE",
                "username LINE", "ip ssh LINE", "crypto LINE", "cdp run", "lldp run", "snmp-server LINE", "clock timezone LINE", "ip cef",
                "ip routing", "ipv6 unicast-routing", "mpls label protocol ldp", "vtp mode WORD", "vtp domain WORD", "errdisable LINE",
                "ip http server", "ip http secure-server", "no ip domain-lookup"):
        words = [w for w in pre.split() if w[0].islower()]
        g.add(pre, accepted(" ".join(words)))
    if sw:
        g.add("ip access-list LINE", unsupported("packet-filtering ACLs aren't supported yet"))
        g.add("access-list LINE", unsupported("packet-filtering ACLs aren't supported yet"))
    return g


def _line_mode(g):
    _common_config(g)
    for c in ("password LINE", "login", "login local", "logging synchronous", "exec-timeout <0-35791> <0-2147483>", "exec-timeout <0-35791>",
              "transport input LINE", "privilege level <0-15>", "history size <0-256>"):
        g.add(c, h_line_cmd)
    return g


def router_grammars():
    ex = _common_exec(Grammar(HELPS), False)
    ex.add("show running-config", h_deny).add("show startup-config", h_deny)
    for g in (ex,):
        g.add("show ip interface brief", r_show_ip_int_brief).add("show interfaces", r_show_interfaces).add("show interfaces IF", r_show_interfaces)
        g.add("show ip route", r_show_ip_route).add("show ip route LINE", r_show_ip_route)
        g.add("show arp", r_show_arp).add("show ip arp", r_show_arp)
    pv = _common_exec(Grammar(HELPS), True)
    pv.add("show ip interface brief", r_show_ip_int_brief).add("show interfaces", r_show_interfaces).add("show interfaces IF", r_show_interfaces)
    pv.add("show ip route", r_show_ip_route).add("show ip route LINE", r_show_ip_route)
    pv.add("show arp", r_show_arp).add("show ip arp", r_show_arp)
    pv.add("show running-config", r_show_run).add("show running-config interface IF", r_show_run_if).add("show startup-config", r_show_start)
    pv.add("show mpls forwarding-table", passthrough_show("show mpls table")).add("show mpls ldp neighbor", passthrough_show("show mpls ldp neighbor"))
    pv.add("show ip protocols", passthrough_show("show ip ospf")).add("show ip vrf", r_show_vrf).add("show vrf", r_show_vrf)
    pv.add("clear access-list counters", r_clear_acl).add("clear ip nat translation *", r_clear_nat)
    pv.add("show ip nat translations", r_show_nat).add("show ip nat translations verbose", r_show_nat).add("show ip nat statistics", r_show_nat_stats)
    pv.add("write", r_write).add("write memory", r_write).add("copy running-config startup-config", r_copy_rs)
    pv.add("copy WORD WORD", h_copy)
    for g in (ex, pv):
        g.add("show ip ospf neighbor", passthrough_show("show ip ospf neighbor")).add("show ip bgp summary", passthrough_show("show ip bgp summary"))
        g.add("show access-lists", r_show_acl).add("show access-lists WORD", r_show_acl)
        g.add("show ip access-lists", r_show_acl).add("show ip access-lists WORD", r_show_acl)
    cf = _global_config(_common_config(Grammar(HELPS)), False)
    cf.add("hostname WORD", r_hostname).add("interface IF", r_interface)
    cf.add("router ospf <1-65535>", r_router_ospf).add("router ospf <1-65535> vrf WORD", r_router_ospf).add("router bgp <1-4294967295>", r_router_bgp)
    cf.add("ip route A.B.C.D MASK IF", r_ip_route).add("ip route A.B.C.D MASK IF LINE", r_ip_route)
    cf.add("ip route A.B.C.D MASK A.B.C.D", r_ip_route).add("ip route A.B.C.D MASK A.B.C.D LINE", r_ip_route)
    cf.add("ip route vrf WORD A.B.C.D MASK A.B.C.D", r_ip_route_vrf).add("ip route vrf WORD A.B.C.D MASK IF", r_ip_route_vrf)
    cf.add("ip route vrf WORD A.B.C.D MASK A.B.C.D LINE", r_ip_route_vrf)
    cf.add("mpls ldp router-id IF", r_ldp_rid).add("mpls ldp router-id IF force", r_ldp_rid)
    cf.add("ip vrf WORD", vrf_def("ip")).add("vrf definition WORD", vrf_def("definition"))
    cf.add("access-list <1-2699> LINE", r_acl_numbered).add("access-list <1-2699>", r_acl_numbered)
    cf.add("ip access-list standard WORD", ip_acl("standard")).add("ip access-list extended WORD", ip_acl("extended"))
    cf.add("ip nat inside source list WORD interface IF overload", nat_list("interface"))
    cf.add("ip nat inside source list WORD pool WORD", nat_list("pool")).add("ip nat inside source list WORD pool WORD overload", nat_list("pool", True))
    cf.add("ip nat inside source static A.B.C.D A.B.C.D", r_nat_static)
    for pr in ("tcp", "udp"):
        cf.add(f"ip nat inside source static {pr} A.B.C.D <1-65535> A.B.C.D <1-65535>", nat_static_port(pr))
        cf.add(f"ip nat inside source static {pr} A.B.C.D <1-65535> interface IF <1-65535>", nat_static_port(pr, True))
    cf.add("ip nat pool WORD A.B.C.D A.B.C.D netmask MASK", r_nat_pool).add("ip nat pool WORD A.B.C.D A.B.C.D prefix-length <1-32>", r_nat_pool)
    cf.add("ip nat pool WORD", r_nat_pool)
    cf.add("ip dhcp LINE", unsupported("DHCP server on routers isn't supported; use a Patchyard server's DHCP service"))
    cf.add("vlan VLANS", unsupported("routers have no VLAN database; use subinterfaces (interface Gi0/0/0.10, encapsulation dot1Q 10)"))
    itf = _common_config(Grammar(HELPS))
    itf.add("ip address A.B.C.D MASK", r_ip_address).add("ip address A.B.C.D MASK secondary", r_ip_address).add("ip address", r_ip_address)
    itf.add("shutdown", r_shutdown).add("description LINE", r_description)
    itf.add("ip ospf <1-65535> area AREA", r_ip_ospf_area).add("bandwidth <1-100000000>", r_bandwidth)
    itf.add("encapsulation dot1q <1-4094>", r_encap).add("encapsulation dot1q <1-4094> native", r_encap)
    itf.add("mpls ip", r_mpls_ip).add("speed LINE", h_speed).add("duplex LINE", h_speed)
    itf.add("ip access-group WORD WORD", r_access_group)
    itf.add("ip nat WORD", r_ip_nat_if)
    itf.add("ip helper-address A.B.C.D", r_helper)
    itf.add("vrf forwarding WORD", r_vrf_forwarding).add("ip vrf forwarding WORD", r_vrf_forwarding)
    itf.add("channel-group LINE", unsupported("EtherChannel isn't supported yet"))
    ospf = _common_config(Grammar(HELPS))
    ospf.add("router-id A.B.C.D", r_ospf_rid).add("network A.B.C.D WILD area AREA", r_ospf_network)
    ospf.add("passive-interface IF", r_passive).add("passive-interface default", r_passive_default)
    ospf.add("redistribute LINE", r_redistribute)
    bgp = _common_config(Grammar(HELPS))
    bgp.add("network A.B.C.D", r_bgp_network).add("network A.B.C.D mask MASK", r_bgp_network)
    bgp.add("address-family ipv4", bgp_af("ipv4")).add("address-family ipv4 unicast", bgp_af("ipv4"))
    bgp.add("address-family ipv4 vrf WORD", bgp_af("vrf")).add("address-family vpnv4", bgp_af("vpnv4")).add("address-family vpnv4 unicast", bgp_af("vpnv4"))
    af = _common_config(Grammar(HELPS))
    af.add("network A.B.C.D", r_bgp_network).add("network A.B.C.D mask MASK", r_bgp_network).add("exit-address-family", r_exit_af)
    af.add("redistribute LINE", r_redistribute)
    acl = _common_config(Grammar(HELPS))
    acl.add("permit LINE", ace("permit")).add("deny LINE", ace("deny")).add("remark LINE", ace("remark"))
    acl.add("<1-2147483647> LINE", ace(None)).add("<1-2147483647>", ace(None))
    vrf = _common_config(Grammar(HELPS))
    vrf.add("rd WORD", r_vrf_rd).add("route-target WORD WORD", r_vrf_rt).add("description LINE", h_terminal_noop)
    vrf.add("address-family ipv4", r_vrf_af).add("address-family ipv4 unicast", r_vrf_af)
    vaf = _common_config(Grammar(HELPS))
    vaf.add("route-target WORD WORD", r_vrf_rt).add("exit-address-family", r_vrf_exit_af)
    return {"exec": ex, "priv": pv, "config": cf, "if": itf, "subif": itf, "ospf": ospf, "bgp": bgp, "bgp-af": af, "line": _line_mode(Grammar(HELPS)),
            "acl-std": acl, "acl-ext": acl, "vrf": vrf, "vrf-af": vaf}


def switch_grammars():
    ex = _common_exec(Grammar(HELPS), False)
    pv = _common_exec(Grammar(HELPS), True)
    for g in (ex, pv):
        g.add("show vlan", sw_show_vlan).add("show vlan brief", sw_show_vlan).add("show interfaces trunk", sw_show_trunk)
        g.add("show interfaces status", sw_show_status).add("show interfaces", sw_show_int).add("show interfaces IF", sw_show_int)
        g.add("show mac address-table", sw_show_mac).add("show mac address-table dynamic", sw_show_mac)
        g.add("show spanning-tree", sw_show_stp).add("show ip interface brief", sw_show_ip_int_brief)
        g.add("show etherchannel summary", sw_show_etherchannel).add("show etherchannel", sw_show_etherchannel)
    pv.add("show running-config", sw_show_run).add("show startup-config", sw_show_run)
    pv.add("write", sw_write).add("write memory", sw_write).add("copy running-config startup-config", sw_write)
    pv.add("copy WORD WORD", h_copy)
    cf = _global_config(_common_config(Grammar(HELPS)), True)
    cf.add("hostname WORD", sw_hostname).add("interface IF", sw_interface).add("interface range LINE", sw_interface_range)
    cf.add("vlan VLANS", sw_vlan).add("ip default-gateway A.B.C.D", sw_default_gw).add("ip default-gateway", sw_default_gw)
    cf.add("spanning-tree vlan VLANS priority <0-61440>", sw_stp_prio).add("spanning-tree mode WORD", sw_stp_mode)
    cf.add("spanning-tree vlan VLANS", sw_stp_off)
    cf.add("ip route LINE", unsupported("Patchyard switches are layer 2; use 'ip default-gateway' for the management SVI"))
    cf.add("router LINE", unsupported("Patchyard switches are layer 2; routing runs on a router"))
    itf = _common_config(Grammar(HELPS))
    itf.add("switchport mode access", sw_mode_h("access")).add("switchport mode trunk", sw_mode_h("trunk"))
    itf.add("switchport access vlan <1-4094>", sw_access_vlan).add("switchport trunk native vlan <1-4094>", sw_trunk_native)
    itf.add("switchport trunk allowed vlan LINE", sw_trunk_allowed)
    itf.add("switchport trunk encapsulation dot1q", h_terminal_noop).add("switchport nonegotiate", h_terminal_noop).add("switchport", h_terminal_noop)
    itf.add("shutdown", sw_shutdown).add("description LINE", sw_description).add("spanning-tree portfast", sw_portfast)
    itf.add("spanning-tree portfast LINE", sw_portfast).add("speed LINE", h_speed).add("duplex LINE", h_speed)
    itf.add("ip address A.B.C.D MASK", unsupported("routed switch ports aren't supported; use 'interface vlan N' for management"))
    itf.add("channel-group <1-64> mode WORD", sw_channel_group).add("channel-group <1-64>", sw_channel_group).add("channel-group", sw_channel_group)
    itf.add("description", sw_description)
    itf.add("spanning-tree LINE", h_terminal_noop)
    vlan = _common_config(Grammar(HELPS)).add("name WORD", sw_vlan_name)
    svi = _common_config(Grammar(HELPS)).add("ip address A.B.C.D MASK", sw_svi_ip).add("ip address", sw_svi_ip).add("shutdown", sw_svi_shut)
    svi.add("description LINE", h_terminal_noop)
    return {"exec": ex, "priv": pv, "config": cf, "if": itf, "if-range": itf, "vlan": vlan, "svi": svi, "line": _line_mode(Grammar(HELPS))}


def _fix_none(gs):
    return gs


RT_GRAMMARS = router_grammars()
SW_GRAMMARS = switch_grammars()


# --------------------------------------------------------------------------- batch (Config tab forms)

_ERR = re.compile(r"^\s*% (Invalid|Incomplete|Ambiguous|Bad|Unknown|Please|Inconsistent|Configuring|Interface not|Physical|patchyard: (?!saved|portfast|one spanning|speed|rapid|mst|a Patchyard switch has))", re.M)


async def run_batch(env: Env, commands: list[str]) -> dict:
    """Run IOS commands as if typed at the console, starting in privileged EXEC.
    The Config tab forms use this, so a form and the CLI always do exactly the same thing."""
    out: list[str] = []

    async def send(t):
        out.append(t)
    s = Session(env, send)
    await s.init_host()
    s.priv = True
    errors = []
    for ln in commands:
        ln = ln.strip()
        if not ln or "\n" in ln:
            continue
        await s.w(s.prompt() + ln + "\n")
        start = len(out)
        await s.guarded(s.execute(ln))
        reply = "".join(out[start:])
        if _ERR.search(reply.replace("\r", "")):
            errors.append(ln)
    text = "".join(out).replace("\r", "")
    return {"ok": not errors, "errors": errors, "transcript": text, "hostname": s.host}


# --------------------------------------------------------------------------- websocket entry

async def serve(ws, lab: str, node: str):
    from fastapi import WebSocketDisconnect
    from .util import check_name
    await ws.accept()
    try:
        check_name(lab); check_name(node)
        env = Env(lab, node)
        n = next((x for x in env.load()["nodes"] if x["id"] == node), None)
        if not n or n["type"] not in ("router", "switch") or n.get("raw"):
            await ws.send_text("\r\nThe IOS CLI is available on Patchyard routers and switches.\r\n")
            await ws.close()
            return
    except Exception as e:
        await ws.send_text(f"\r\n{e}\r\n"); await ws.close(); return

    async def send(t):
        await ws.send_text(t)
    s = Session(env, send)

    async def reader():
        while True:
            m = json.loads(await ws.receive_text())
            if m.get("t") == "i":
                for ch in _split_keys(m["d"]):
                    s.feed(ch)

    rt = asyncio.create_task(reader())
    st = asyncio.create_task(s.run())
    try:
        await asyncio.wait({rt, st}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        s.closed = True
        for t in (rt, st):
            t.cancel()
        try:
            await ws.close()
        except Exception:
            pass


def _split_keys(data: str) -> list[str]:
    """Pasted text arrives as one string; escape sequences stay as single chars here and
    are reassembled by the line editor."""
    return list(data)
