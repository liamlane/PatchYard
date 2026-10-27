# How netlab works

This document covers how a lab goes from the canvas to running containers and what happens while it runs. It also covers where data is stored and how releases reach the VM.

![netlab architecture](images/architecture.svg)

## The pieces

**The netlab container** is the only thing installed on the VM. It is built from this repo and published to `ghcr.io/liamlane/netlab`. It runs uvicorn with the FastAPI app, which serves:

- the REST API under `/api`,
- the WebSockets under `/ws` (device consoles, live status, Simulation),
- the built React UI.

The image also includes containerlab, the Docker CLI, iproute2, `nsenter`, tcpdump, iperf3 and ping. The netlab container doesn't need any of these installed on the host.

The container runs with:

| Setting | Why |
| --- | --- |
| `privileged`, `pid: host`, `cgroup: host` | Find each device's PID, read its counters and cgroup stats, and `nsenter` into it |
| `network_mode: host` | Create veth pairs and bridges in the host namespace; listen on the VM's :8080 |
| `/var/run/docker.sock` | Run containerlab and `docker run` against the host's Docker Engine |
| `/opt/netlab`, `/home` (same paths) | containerlab bind-mounts config files by host path, so paths must look the same inside and outside the container |
| `/lib/modules:ro` | Load kernel modules at startup if the host hasn't already |

**Lab devices** are ordinary containers on the host's Docker Engine, labelled `containerlab=<lab>` and `clab-node-name=<node>`.

| Device | Image | What runs inside |
| --- | --- | --- |
| Router | `quay.io/frrouting/frr:10.0.0` | FRR daemons (zebra, ospfd, bgpd, ldpd, ...). Kernel MPLS and VRFs enabled per interface. The config directory is bind-mounted so `write memory` persists. |
| Switch | `alpine:3.20` | A generated `netlab-switch` script builds a VLAN-filtering Linux bridge: access/trunk ports, native VLAN, STP priority, management SVI. |
| PC | `ghcr.io/hellt/network-multitool` | Static or DHCP addressing, plus ping, traceroute, curl and dig from the PC desktop. |
| Server | `alpine:3.20` | dnsmasq (DNS records, DHCP scopes) and nginx (HTTP), configured from the server's service forms. |

All devices share a management network, `clab-mgmt` (172.31.255.0/24). Lab cables are veth pairs, one end in each device's namespace.

## Lab lifecycle

![Lab lifecycle](images/lifecycle.svg)

1. **Design.** The canvas edits a lab document (`lab.json`): devices, cables (copper/fibre), per-device properties and annotations. The UI autosaves it.
2. **Generate.** Deploy turns `lab.json` into a containerlab topology (`netlab-<lab>.clab.yml`) and per-device files:
   - FRR `frr.conf` and `daemons`,
   - switch scripts,
   - dnsmasq/nginx configs,
   - exec hooks: IP addresses, MPLS input, VRFs, subinterfaces.
3. **Snapshot.** If enabled in Settings, the current topology and configs are snapshotted first. The last 20 automatic snapshots are kept; named snapshots are never pruned.
4. **Deploy.** `containerlab deploy` creates the containers, the veth cables and the management network, then runs each device's exec hooks. Running configs are saved before a redeploy, so nothing typed into a CLI is lost.
5. **Running.** FRR converges, the bridges learn and forward, and the servers answer. The UI's status WebSocket shows each device turn green.

**Destroy** removes the containers. The lab's folder (`/opt/netlab/labs/<lab>`) keeps the topology, configs and snapshots. **Delete lab** removes that folder. It's only allowed once nothing is running.

### Live editing

When live editing is on (Settings → General), changes to a running lab apply immediately:

- **New device:** `docker run` with the same name, labels, binds, management network and exec hooks containerlab would have used. A later `containerlab destroy` still cleans it up.
- **New cable:**
  - a veth pair is created on the host, then each end is moved into its device's namespace and renamed (`eth3` and so on),
  - a per-type hook then runs: switches re-run their bridge script, PCs and servers re-apply addressing, and routers enable MPLS input and re-create subinterfaces.
- **Delete or undo:** the cable is unplugged or the container removed. The UI works out what to apply by comparing the canvas with what is actually running, so drag, duplicate, delete and undo/redo all take the same path.

### Configs

Router config directories are bind-mounted (`configs/<node>:/etc/frr`). FRR's `write memory` writes a temporary file and renames it, which a single-file bind mount would refuse. **Save running configs** (Ctrl+S) runs `write memory` on every router. It reports which devices saved, failed or were skipped.

## Hardware: models, modules, ports

Any router, switch, firewall, PC or server can have a chassis model (`props.chassis`) with on-board ports and module slots (`props.modules`). Ports are named the IOS way, but Linux interface names can't contain `/`, so each port has a fixed Linux name:

| Port | Linux name |
| --- | --- |
| on-board ports, in order | `eth1`, `eth2`, … |
| slot *k* (0-based), port *p* | `eth{100·(k+1)+p+1}`, e.g. slot 0/1 port 0 = `eth101` |

| PC / laptop / server NIC slot | always `eth1` (Fa0, Gi0 …) |

The numbering never moves when a module is added, so cables and FRR config stay put. `hardware.py` holds the catalogue and the mapping, and `hw.js` mirrors it for the UI. Devices without a model ("generic", every lab from before 0.10) keep free-form `eth1`, `eth2` … ports.

- **Media**: each port type takes one cable type: copper, fibre (SFP/SFP+) or serial. The port picker only offers ports that match the cable, and the server refuses a cable on a port the hardware doesn't have.
- **Transceivers**: an SFP or SFP+ port takes a fibre optic by default. `props.sfp` (`{"eth101": "copper"}`) fits a copper RJ45 transceiver instead, which makes the port copper, so a copper PC can plug into a fibre switch. The UI refuses a model, module or transceiver change that would leave an existing cable on a port of the wrong media (`mediaConflicts` in `hw.js`). The server doesn't enforce media on save, so older labs never get locked.
- **Speed**: links run at the slower end's speed, applied as `netem rate` on both ends (Fa 100 Mb/s, Gi 1 Gb/s, serial 1.544 Mb/s; 10G is left uncapped). An impairment's rate is used instead when it's lower. Serial is emulated: an Ethernet veth at T1 speed, not HDLC/PPP.
- **Power**: power off removes the device's container (unsaved running config is lost). Power on starts it from the lab file, re-plugs every cable whose other end is up, and re-applies link speed, impairments and admin-down state. With "Power off to swap modules" on (default), modules can only change while the device is off or the lab isn't deployed. A port only exists in Linux once it's cabled, which is why modules can be added to a running device with the rule turned off.

## IOS-syntax CLI

The CLI tab opens an IOS-style session (`ios.py`) over the console WebSocket. It has its own line editor (history, `?` help, Tab completion, Ctrl+Z/Ctrl+C) and a grammar per mode (user, privileged, config, interface, router, address-family, line, VLAN). Commands can be abbreviated as long as they're unambiguous.

- **Routers**: each command is translated and run with `vtysh` in the current context. Example: `ip address 10.0.0.1 255.255.255.252` under `interface Gi0/0/0` becomes `configure terminal` / `interface eth1` / `ip address 10.0.0.1/30`. Translated commands include:
  - OSPF `network` statements (wildcard masks) and `ip ospf N area`,
  - BGP `network … mask` and address families (including `vrf` and `vpnv4`),
  - static routes,
  - `mpls ip` (LDP),
  - loopbacks (Loopback0 is `lo`, others are dummy interfaces),
  - subinterfaces: `interface Gi0/0/0.10` + `encapsulation dot1Q 10` creates the 802.1Q device and saves it to the lab.

  Anything else is passed to FRR as typed, with interface names translated, so FRR-only commands keep working. FRR output and `show running-config` are translated back: port names, masks, `router ospf 1`.
- **Switches**: commands edit the same props as the Config tab (VLANs, access/trunk, native/allowed VLANs, SVI, STP priority, shutdown, description) and re-run the switch script. Show output is built from those props plus live bridge state (`bridge fdb`, `/sys/class/net/br0/bridge`).
- **Stored but no effect**: `service …`, `no ip domain-lookup`, line passwords and similar commands are saved and shown in `show running-config`, but change nothing.
- **Real effect**: `enable secret` really gates `enable`; the banner is shown at login.
- **Not supported**: features netlab doesn't have (packet ACLs and NAT on routers, EtherChannel on Linux-bridge switches, routed switch ports, VRF creation) print a `% netlab:` message instead of pretending.
- **Sync**: device props changed from the CLI bump a revision. The browser pulls them in from the status poll, and the server keeps them if an older autosave arrives.

## Config tab

Each device's Config tab reads its current configuration from `GET /api/labs/<lab>/nodes/<node>/state`:

- **Routers**: FRR's running config, parsed into interfaces, OSPF, BGP, static routes and LDP (`cfgstate.parse_frr`). If the router isn't running, the saved `frr.conf` is read instead. Live kernel state (link up/down, MAC, cable peer) is added per port.
- **Switches, firewalls, hosts**: their props plus live port state.

Router and switch forms don't write configuration themselves. They build IOS commands and send them to `POST …/ios`, which runs them in a headless IOS session (`ios.run_batch`), the same engine as the CLI tab. The transcript is shown under the form, so the form and the CLI can never disagree. Firewalls are saved as a whole model with `POST …/firewall`.

## Firewall

A firewall is an Alpine container with nftables. Its props hold:

- interfaces (address, zone),
- zones with security levels,
- ordered rules,
- NAT (masquerade per zone pair, port forwards),
- static routes and a default gateway.

`firewall.py` turns them into `fw.nft` and `fw.sh`, bind-mounted at `/etc/netlab`, and applies them with `nft -f`. The firewall is stateful: return traffic is allowed, invalid traffic is dropped, and port-forwarded traffic is allowed through. Traffic no rule matches is either allowed from a higher security level to a lower one (ASA-style), or dropped. Every rule has a counter, and the Config tab shows the hit counts.

## Open vSwitch switches

A switch with `engine: ovs` runs `ovsdb-server` and `ovs-vswitchd` inside its container (the host needs the `openvswitch` kernel module, which netlab loads).

- The switch script rebuilds `br0` on every apply, with RSTP and per-port VLAN settings.
- Port-channels are OVS bonds with LACP: `active`/`passive` map to LACP, `on` to static `balance-slb`.
- The management SVI is an OVS internal port.

The Linux-bridge engine stays the default because it's lighter.

## Server services

The server's Services tab edits `props.services` and saves everything with `POST …/services`. That regenerates the files below and runs `services.sh` in the container, which starts or stops each service to match:

| Service | Software | Notes |
| --- | --- | --- |
| DNS / DHCP | dnsmasq | A and CNAME records; DHCP options 3, 6, 15, 66/150; `dhcp-host` reservations; lease file in `configs/<server>/svc/dnsmasq.leases` |
| HTTP | nginx | index page from the UI, files in `configs/<server>/www` |
| FTP | vsftpd | local users created by the script (shell-less, home `/srv/ftp`), passive ports 30000–30100, no anonymous login; files in `configs/<server>/ftp` |
| TFTP | `in.tftpd -4 -c` | uploads allowed; files in `configs/<server>/tftp` |
| Syslog | rsyslog | UDP/TCP 514; written to `configs/<server>/syslog/syslog.log` |
| NTP | `chronyd -x` | serves the local clock |

- **Packages** are installed with `apk` on first use.
- **Validation**: values are checked before the lab is saved (`labs.validate_server`):
  - addresses and MACs,
  - CNAMEs must point at an A record on the same server,
  - lease time format,
  - FTP user names and passwords.
- **Live view**: `GET …/server-status` reports which daemons are running (from `ps` in the container) and the current DHCP leases.
- **Files**: `GET/PUT/DELETE …/server/{tftp|ftp}/{file}` lists, uploads, downloads and deletes files in the FTP and TFTP folders.
- **Clients**:
  - The IOS CLI's `copy … tftp:` runs the TFTP client from the netlab container inside the device's network namespace. `logging host` sends RFC 3164 messages the same way, using `logger`.
  - PCs get an `ftp SERVER` command (a small interactive client on top of `curl`) and send their hostname with DHCP, so it shows in the lease table.

## Monitoring and alerting

![Monitoring and alerting pipeline](images/monitoring.svg)

Everything is measured from the host side, so nothing extra is installed in the lab images.

- **Metrics collector** (every 10 s by default) reads:
  - per-port bytes, packets, drops and errors from `/proc/<pid>/net/dev`,
  - port state from `/sys/class/net/<if>/operstate` inside the device's root,
  - CPU and memory from cgroup v2 (falling back to v1).

  Rates are stored in SQLite (`/opt/netlab/metrics.db`), kept for 48 hours by default, and drive the charts, the lab dashboard and the traffic overlay on the cables.
- **Health loop** (every 30 s by default) runs `vtysh -c '... json'` on routers for BGP, OSPF and LDP neighbour state. It also runs ping probes from inside a source device's namespace, like an IP SLA probe, for loss and RTT.
- **Alert engine** checks the rules after every sample and every health pass:
  - each rule has a type, an optional lab/device scope, a threshold, a **for** hold time and a severity,
  - a **grace period** after a lab comes up keeps protocol convergence from alerting,
  - alerts resolve automatically when the condition clears, can be acknowledged, and are kept in an `alerts` table next to the metrics,
  - the UI polls a summary for the bell badge, toasts and device badges.

## Fault injection, traffic, Simulation

- **Faults** apply to one or both ends of a cable, using `tc qdisc ... netem` inside the device's namespace through `nsenter`. Options are delay, jitter, loss, rate, duplication and corruption. Link down/up and flap set the interface state. The presets are combinations of these values.
- **Traffic generator** starts `iperf3 -s` in the destination's namespace and `iperf3 -c` in the source's. Results (throughput, retransmits, or loss and jitter for UDP) come back as JSON.
- **Simulation** runs tcpdump on the lab's ports. scapy decodes the frames (Ethernet, ARP, IP, ICMP, TCP/UDP, DNS, HTTP, OSPF, BGP, LDP, MPLS), and the UI replays them as animated steps on the canvas with per-packet details.

## Storage

```
/opt/netlab/
├── labs/<lab>/
│   ├── lab.json                     topology, device properties, annotations
│   ├── netlab-<lab>.clab.yml        generated containerlab topology
│   ├── configs/<node>/              frr.conf, switch.sh, dnsmasq.conf, nginx.conf ...
│   └── snapshots/                   automatic and named snapshots
├── settings.json                    global settings (defaults merged in)
├── metrics.db                       SQLite: samples, alert history
└── deploy/                          docker-compose.yml + .env (written by setup.sh)
```

Labs imported from an existing containerlab folder (for example the original Phase 1/2 labs in `labs/`) keep their own working directory. netlab only stores its record of them.

## Security model

netlab can start privileged containers and enter any namespace, so it has to be treated as root on the VM. The optional login uses:

- a PBKDF2 password hash,
- an HMAC-signed session cookie,
- an ASGI middleware covering `/api` and `/ws`.

Keep port 8080 behind a reverse proxy with authentication, and don't expose it directly. Reset a lost password with `docker exec netlab python -m app.auth reset`.

## Releases

![Release and update flow](images/release-flow.svg)

Every push to `main`:

1. runs pytest and the UI build,
2. builds the image,
3. pushes it to GHCR as `latest` and `sha-<commit>`.

A `vX.Y.Z` tag also publishes `X.Y.Z`. On the VM, `deploy/update.sh` pulls the new image and recreates the container. The labs are separate containers, so they keep running throughout.

## Known limits

- The Linux-bridge switch engine runs the kernel's 802.1D STP (about 30 s convergence). For RSTP and LACP, use the Open vSwitch engine. MST isn't supported.
- There's no DHCP relay yet (`ip helper-address` is on the roadmap), so a DHCP server only serves its own subnet.
- FTP has no anonymous login, because vsftpd won't chroot anonymous users into a writable folder.
- There are no Cisco images (licensing). The IOS CLI is a translation layer over FRR: common IOS commands work, and anything it doesn't know goes to FRR as typed, so FRR's syntax rules apply there. One example is that FRR won't mix `network … area` with `ip ospf area` on the same router.
- `?` lists the commands netlab translates. FRR-only commands work, but aren't listed.
- Metrics come from the host and are per-container. They measure the Linux datapath, not a vendor ASIC.
