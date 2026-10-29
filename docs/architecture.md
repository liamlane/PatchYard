# How Patchyard works

This document covers how a lab goes from the canvas to running containers and what happens while it runs. It also covers where data is stored and how releases reach the host.

![Patchyard architecture](images/architecture.svg)

## The pieces

**The Patchyard container** is the only thing installed on the host. It is built from this repo and published to `ghcr.io/liamlane/patchyard`. It runs uvicorn with the FastAPI app, which serves:

- the REST API under `/api`,
- the WebSockets under `/ws` (device consoles, live status, Simulation),
- the built React UI.

The image also includes containerlab, the Docker CLI, iproute2, nftables, conntrack, dnsmasq, `nsenter`, tcpdump, iperf3 and ping. None of these need to be installed on the host.

The container runs with:

| Setting | Why |
| --- | --- |
| `privileged`, `pid: host`, `cgroup: host` | Find each device's PID, read its counters and cgroup stats, and `nsenter` into it |
| `network_mode: host` | Create veth pairs and bridges in the host namespace; listen on the host's :8080 |
| `/var/run/docker.sock` | Run containerlab and `docker run` against the host's Docker Engine |
| `/opt/patchyard`, `/home` (same paths) | containerlab bind-mounts config files by host path, so paths must look the same inside and outside the container |
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

**Destroy** removes the containers. The lab's folder (`/opt/patchyard/labs/<lab>`) keeps the topology, configs and snapshots. **Delete lab** removes that folder. It's only allowed once nothing is running.

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
- **Not supported**: features Patchyard doesn't have (EtherChannel on Linux-bridge switches, routed switch ports, DHCP server on routers, IPv6 ACLs) print a `% patchyard:` message instead of pretending.
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

A switch with `engine: ovs` runs `ovsdb-server` and `ovs-vswitchd` inside its container (the host needs the `openvswitch` kernel module, which the installer loads).

- The switch script rebuilds `br0` on every apply, with RSTP and per-port VLAN settings.
- Port-channels are OVS bonds with LACP: `active`/`passive` map to LACP, `on` to static `balance-slb`.
- The management SVI is an OVS internal port.

The Linux-bridge engine stays the default because it's lighter.

## Router ACLs, NAT, DHCP relay and VRFs

FRR has no packet filters, NAT or DHCP relay, so these live in the router's props and are applied from outside the router container. The router image stays stock FRR. `l3.py` holds the model, and the IOS CLI and the Config tab both edit it.

| Props | IOS |
| --- | --- |
| `acls` `{name: {type, entries: [{seq, text}]}}` | `access-list N …` (1-99/1300-1999 standard, 100-199/2000-2699 extended); `ip access-list standard NAME` / `ip access-list extended NAME` with `[seq] permit …`, `deny …`, `remark …` |
| `ifcfg` `{ethN: {acl_in, acl_out, nat, helpers, vrf}}` | `ip access-group N in` / `out`, `ip nat inside` / `outside`, `ip helper-address`, `vrf forwarding` |
| `nat` `{pools, rules}` | `ip nat inside source list … interface … overload`, `… pool …`, `… static …`, `ip nat pool …` |
| `vrfs` `{name: {rd, import, export, table}}` | `vrf definition` / `ip vrf`, `rd`, `route-target` |

Changes are saved with the lab straight away, like switch settings. They don't need `write memory`.

- **ACLs and NAT** become one nftables ruleset (tables `nl_acl` and `nl_nat`), loaded into the router's network namespace with the app's own `nft` (`nsenter -n`):
  - Inbound ACLs hook prerouting ahead of DNAT and conntrack, so they see addresses as they arrive, like IOS. That also covers traffic to the router itself.
  - Outbound ACLs filter routed traffic.
  - Like IOS, ACLs are stateless, entries are matched in sequence order, and every list ends in an implicit deny.
  - A denied packet gets an ICMP administratively-prohibited reply ("U" in a ping), or a silent drop on kernels that can't reject from prerouting.
  - Every entry has a counter, which `show access-lists` and the Config tab read back.
- **Static NAT globals** are added to the outside interface as /32 addresses labelled `ethN:nat`, so the router answers ARP for them. `show ip nat translations` reads conntrack.
- **DHCP relay** is dnsmasq in relay-only mode (`--port=0 --dhcp-relay=<interface address>,<server>`). It runs from the app inside the router's namespace, one process per router, and sets the giaddr so the server picks the pool for that subnet.
- **VRFs** are Linux VRF devices (`ip link add NAME type vrf table 1001+`), with interfaces enslaved to them. FRR picks them up for `router ospf vrf`, `ip route vrf` and `router bgp ASN vrf`. Cisco-style `rd`/`route-target` under the VRF are written into FRR's `router bgp ASN vrf NAME` (`rd vpn export`, `rt vpn import/export`, `export/import vpn`) when that BGP instance exists.
- **Re-applied** after every deploy, when a cable is plugged into the router, on power-on, and when Patchyard itself restarts (the relays run from the app). They stop when the lab is destroyed.

## Server services

The server's Services tab edits `props.services` and saves everything with `POST …/services`. That regenerates the files below and runs `services.sh` in the container, which starts or stops each service to match:

| Service | Software | Notes |
| --- | --- | --- |
| DNS / DHCP | dnsmasq | A and CNAME records; one tagged `dhcp-range` per pool with its own options (3, 6, 15, 66/150), so relayed requests get the pool for their subnet; `dhcp-host` reservations; lease file in `configs/<server>/svc/dnsmasq.leases` |
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
  - The IOS CLI's `copy … tftp:` runs the TFTP client from the Patchyard container inside the device's network namespace. `logging host` sends RFC 3164 messages the same way, using `logger`.
  - PCs get an `ftp SERVER` command (a small interactive client on top of `curl`) and send their hostname with DHCP, so it shows in the lease table.

## Monitoring and alerting

![Monitoring and alerting pipeline](images/monitoring.svg)

Everything is measured from the host side, so nothing extra is installed in the lab images.

- **Metrics collector** (every 10 s by default) reads:
  - per-port bytes, packets, drops and errors from `/proc/<pid>/net/dev`,
  - port state from `/sys/class/net/<if>/operstate` inside the device's root,
  - CPU and memory from cgroup v2 (falling back to v1).

  Rates are stored in SQLite (`/opt/patchyard/metrics.db`), kept for 48 hours by default, and drive the charts, the lab dashboard and the traffic overlay on the cables.
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
/opt/patchyard/
├── labs/<lab>/
│   ├── lab.json                     topology, device properties, annotations
│   ├── netlab-<lab>.clab.yml        generated containerlab topology
│   ├── configs/<node>/              frr.conf, switch.sh, dnsmasq.conf, nginx.conf ...
│   └── snapshots/                   automatic and named snapshots
├── settings.json                    global settings (defaults merged in)
├── metrics.db                       SQLite: samples, alert history
└── deploy/                          docker-compose.yml + .env (written by setup.sh)
```

Labs imported from an existing containerlab folder (for example the original Phase 1/2 labs in `labs/`) keep their own working directory. Patchyard only stores its record of them.

## Security model

Patchyard itself can start privileged containers and enter any namespace, so whoever administers it is root on the host.

**Accounts** (`users.py`, `<home>/users.json`):

- Users have a role, `admin` or `user`, and passwords are PBKDF2 hashes.
- New accounts come from invite links. Only a hash of each token is stored, and each token is single-use and expires.
- The session cookie is `user.expiry.gen.signature`, HMAC-signed with `<home>/secret.key`. Bumping a user's `gen` (password change, disable) ends their other sessions.
- Logins are throttled per username and per address.
- State-changing requests from a foreign `Origin` are refused.

**Access control** (`auth.AuthMiddleware`, pure ASGI so it covers WebSockets):

- Every `/api/labs/<lab>/…` and `/ws/console/<lab>/…` request is checked against the lab's `owner` in `lab.json`. Labs from before multi-user belong to the first admin. Someone else's lab answers 404.
- Admin-only: settings changes, importing from a host path, `/api/admin/*`.
- Lab lists and alerts are filtered to the caller's labs.
- `PUT /api/labs/<lab>` keeps the server's `owner`/`trusted`/`sandbox_subnet` whatever the browser sends. For normal users it also refuses raw containerlab definitions (`sandbox.check_raw`) and images outside the allowed list.

**Quotas** (`users.quota`): labs, labs running at once, devices per lab, and memory/CPU per device. They come from Settings `users.quota`, with per-user overrides; admins are unlimited. They're checked on create, save, deploy and live add.

**Sandbox** (`sandbox.py`, `proxy.py`; on with `--public` / `PATCHYARD_SANDBOX=1`):

- **Node spec:** every node spec is rewritten to `privileged: false`, `cap-add: [NET_ADMIN, NET_RAW]`, `sysctls: {net.ipv4.ip_forward: 1}`, plus memory/CPU limits and a `PATCHYARD_PROXY` variable.
- **Exec lines:** `apk` runs through the proxy. `sysctl -w` and `/proc/sys` writes are taken out of the exec lines, and Patchyard sets them from outside with `nsenter -n` after deploy, power-on and live add (`sandbox.fixups`).
- **Management network:** each lab gets `py-<lab>`, a subnet from `sandbox.mgmt_pool` stored as `sandbox_subnet`, with containerlab `external-access: false`.
- **Host bridges** (e.g. the MPLS template's INET bridge) must be named `br<hash of lab>`, so labs can't touch each other's or the host's bridges.
- **Host firewall:** the nftables table `ip patchyard` drops lab traffic to the internet and the host, apart from the proxy port, replies, and the `full` set (labs whose owner has internet access, admins' labs, trusted labs). The proxy serves only the lab pool and connects only to `sandbox.proxy_allow` hosts on 80/443.
- **Trusted labs:** a lab an admin marks `trusted` skips the sandbox.

Behind Caddy (`--domain`), Patchyard listens on 127.0.0.1 and the cookie is `Secure`. Reset a lost password with `sudo docker exec patchyard python -m app.auth reset-password NAME`.

## Releases

![Release and update flow](images/release-flow.svg)

Every push to `main`:

1. runs pytest and the UI build,
2. builds the image,
3. pushes it to GHCR as `latest` and `sha-<commit>`.

A `vX.Y.Z` tag also publishes `X.Y.Z` and `X.Y` images and creates a GitHub release with that version's CHANGELOG section and the installer attached. Settings → About compares the running version with the latest release (`GET /api/system/update`, cached for 6 hours; off with `PATCHYARD_UPDATE_CHECK=0`).

On the host, `deploy/update.sh` pulls the new image and recreates the container. The labs are separate containers, so they keep running throughout.

## Host capabilities

`GET /api/system` reports, for each feature, whether its kernel modules are loaded, built in, or loadable (`/sys/module`, `modules.builtin`, `modules.dep`). It also reports the Docker and containerlab versions, CPU and memory. The UI uses the report in three places:

- Settings → About lists what this host supports.
- Templates that need a missing feature are flagged in the Lab manager.
- The Open vSwitch engine option warns when `openvswitch` is missing.

## Known limits

- The Linux-bridge switch engine runs the kernel's 802.1D STP (about 30 s convergence). For RSTP and LACP, use the Open vSwitch engine. MST isn't supported.
- Router ACLs are IPv4 only. Outbound ACLs see addresses before source NAT (IOS evaluates them after translation); inbound ACLs match IOS (before NAT).
- Dynamic NAT to a pool without `overload` maps many inside hosts onto the pool with port translation, rather than one-to-one.
- FTP has no anonymous login, because vsftpd won't chroot anonymous users into a writable folder.
- There are no Cisco images (licensing). The IOS CLI is a translation layer over FRR: common IOS commands work, and anything it doesn't know goes to FRR as typed, so FRR's syntax rules apply there. One example is that FRR won't mix `network … area` with `ip ospf area` on the same router.
- `?` lists the commands Patchyard translates. FRR-only commands work, but aren't listed.
- Metrics come from the host and are per-container. They measure the Linux datapath, not a vendor ASIC.
