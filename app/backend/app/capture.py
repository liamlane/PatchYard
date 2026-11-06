"""Simulation mode: per-link packet capture decoded with scapy, kept as an event list."""
import asyncio, collections, struct, time, logging
from .util import container_pid, dexec, run

log = logging.getLogger("netlab.capture")

try:
    from scapy.all import Ether, NoPayload, conf
    from scapy.layers import http  # noqa: F401  (binds HTTP to tcp/80)
    from scapy.contrib import ospf, bgp, ldp, mpls  # noqa: F401
    conf.verb = 0
    SCAPY = True
except Exception as e:  # pragma: no cover
    log.warning("scapy unavailable: %s", e)
    SCAPY = False

PROTO_MAP = [("DHCP", "DHCP"), ("DNS", "DNS"), ("BGP", "BGP"), ("LDP", "LDP"), ("OSPF", "OSPF"),
             ("HTTP", "HTTP"), ("ICMP", "ICMP"), ("ARP", "ARP"), ("BOOTP", "DHCP"),
             ("TCP", "TCP"), ("UDP", "UDP")]


def decode(data: bytes):
    if not SCAPY:
        return "ETH", f"{len(data)} bytes", "", []
    try:
        pkt = Ether(data)
    except Exception:
        return "ETH", f"{len(data)} bytes", "", []
    layers, names, layer = [], [], pkt
    while layer is not None and not isinstance(layer, NoPayload):
        names.append(type(layer).__name__)
        if layer.name not in ("Raw", "Padding"):
            fields = []
            for f in layer.fields_desc:
                try:
                    v = f.i2repr(layer, layer.getfieldval(f.name))
                except Exception:
                    v = str(layer.getfieldval(f.name))
                fields.append([f.name, str(v)[:200]])
            layers.append({"name": layer.name, "fields": fields})
        layer = layer.payload
    proto = next((label for key, label in PROTO_MAP if any(n.startswith(key) for n in names)),
                 names[1] if len(names) > 1 else "ETH")
    if "MPLS" in names:
        proto = f"MPLS/{proto}"
    try:
        info = pkt.summary()
    except Exception:
        info = ""
    return proto, info, pkt.src if hasattr(pkt, "src") else "", layers


class Capture:
    def __init__(self, lab: str):
        self.lab = lab
        self.events = collections.deque(maxlen=5000)
        self.next_id = 1
        self.procs, self.tasks = [], []
        self.running = False
        self.started = None

    async def start(self, labd: dict):
        await self.stop()
        self.running, self.started = True, time.time()
        for link in labd["links"]:
            a, aif = link["a"], link["a_if"]
            pid = await container_pid(self.lab, a)
            if not pid:
                continue
            rc, mac, _ = await dexec(self.lab, a, "cat", f"/sys/class/net/{aif}/address")
            proc = await asyncio.create_subprocess_exec(
                "nsenter", "-t", str(pid), "-n", "tcpdump", "-i", aif, "-U", "-n", "-s", "1600", "-w", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self.procs.append(proc)
            self.tasks.append(asyncio.create_task(self._read(proc, link, mac.strip().lower())))
        if self.procs:
            await asyncio.sleep(1.0)  # tcpdump needs ~0.5 s to open; don't miss the first ARP
        return len(self.procs)

    async def _read(self, proc, link, a_mac):
        try:
            hdr = await proc.stdout.readexactly(24)
            magic = hdr[:4]
            end = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
            nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
            while True:
                ph = await proc.stdout.readexactly(16)
                sec, frac, incl, orig = struct.unpack(end + "IIII", ph)
                data = await proc.stdout.readexactly(incl)
                t = sec + frac / (1e9 if nano else 1e6)
                proto, info, src, layers = decode(data)
                fwd = src.lower() == a_mac
                self.events.append({"id": self.next_id, "t": t, "link": link["id"],
                                    "from": link["a"] if fwd else link["b"],
                                    "to": link["b"] if fwd else link["a"],
                                    "proto": proto, "info": info, "len": orig, "layers": layers, "raw": data})
                self.next_id += 1
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception as e:
            log.warning("capture on %s failed: %s", link["id"], e)

    async def stop(self):
        for p in self.procs:
            if p.returncode is None:
                p.terminate()
        for t in self.tasks:
            t.cancel()
        self.procs, self.tasks, self.running = [], [], False

    def list(self, after: int):
        return [{k: v for k, v in e.items() if k not in ("layers", "raw")} for e in self.events if e["id"] > after]

    def get(self, eid: int):
        e = next((e for e in self.events if e["id"] == eid), None)
        return {k: v for k, v in e.items() if k != "raw"} if e else None

    def pcap(self, link: str | None = None, ids: set[int] | None = None) -> bytes:
        """The captured frames as a classic pcap file (Ethernet, microsecond timestamps) for Wireshark."""
        return pcap_file((e["t"], e["raw"]) for e in self.events
                         if e.get("raw") is not None and (not link or e["link"] == link) and (ids is None or e["id"] in ids))

    def clear(self):
        self.events.clear()


def pcap_header(snaplen: int = 65535) -> bytes:
    return struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, snaplen, 1)       # LINKTYPE_ETHERNET


def pcap_file(frames) -> bytes:
    out = bytearray(pcap_header())
    for t, data in frames:
        sec = int(t)
        out += struct.pack("<IIII", sec, int((t - sec) * 1e6), len(data), len(data)) + data
    return bytes(out)


CAPTURES: dict[str, Capture] = {}


def capture_for(lab: str) -> Capture:
    if lab not in CAPTURES:
        CAPTURES[lab] = Capture(lab)
    return CAPTURES[lab]
