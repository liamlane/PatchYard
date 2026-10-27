# NetLab build log

## 2026-10-27: 0.11.0 additions (services, NICs, transceivers, templates)
- Fix: router module ports missing from the Config tab interface list (state re-read 1.5 s after a props change).
- PC/laptop/server NIC slot (`nic-*` modules, always eth1); `props.sfp` copper/fibre transceivers on SFP/SFP+ ports; UI refuses model/module/transceiver changes that would strand a cable on the wrong media.
- Services tab rebuilt (`Services.jsx`): per-service pages with On/Off and running state (`/server-status`: ps + dnsmasq lease file). DHCP domain/option 150+66/lease/reservations, DNS CNAME, FTP (vsftpd, local users, PAM file without pam_shells, no anonymous: vsftpd refuses a writable chroot for anon), file upload/download/delete for FTP and TFTP folders, PC `ftp` client over curl, udhcpc sends hostname. Tested in namespaces: dnsmasq config (`--test`, CNAME answer, lease file written with hostname), vsftpd login/list/put/get/rename/delete through the PC client, bad password refused.
- Templates: ospf-multiarea, bgp-multi-as, serial-wan, campus-rstp-lacp, small-office; FRR configs checked with `vtysh -C` (FRR 8.4). Not run as live labs (no Docker in the sandbox).
- Docs: README, architecture, changelog, roadmap, new screenshots (office canvas, router/switch/firewall Config, Services, Physical, templates).
- Tests: 46 passing; headless UI runs for hardware and Services.

## 2026-10-27: 0.11.0, Config tab redesign, firewall, Open vSwitch, server services
- Config tab rebuilt Packet Tracer style for every device type; router/switch forms generate IOS and run it through `ios.run_batch` (same engine as the CLI); router state parsed from FRR running config (`cfgstate.py`).
- Firewall device (`firewall.py`, nftables): zones/levels, ordered rules with counters, masquerade + DNAT, routes. Tested end to end in network namespaces (inside→outside PAT seen as the firewall's address, outside→inside blocked, dmz→inside blocked, port forward to a DMZ web server, reject rule, firewall answers ping); template ruleset validated with `nft -c`.
- Open vSwitch switch engine (RSTP, LACP bonds, internal-port SVI) with IOS port-channel commands. Script generation unit-tested; not run (no openvswitch module in the build sandbox).
- Servers: TFTP (tftp-hpa, needs `-4`), rsyslog receiver, chrony. TFTP put/get and syslog receive tested between namespaces; rsyslog config validated with `rsyslogd -N1`.
- IOS: `copy run tftp:`, `copy tftp: run` (line-by-line restore), `logging host` events (CONFIG_I, LINK-5-CHANGED).
- Firewall edge template. VyOS dropped from this release: no official container image.
- Tests: 44 passing. Headless UI run through router (mocked running state), switch, firewall, PC and server pages, with the resulting IOS commands and props checked.

## 2026-10-27: 0.10.0 additions after review
- Reported: DHCP on a PC gave `mv: can't rename '/etc/resolv.conf.118': resource busy`. Cause: busybox udhcpc's default hook writes a temp file and mv's it over /etc/resolv.conf, which Docker bind-mounts. Fix: `labs.DHCP_SCRIPT` hook (sets address/gateway, writes resolv.conf in place), bind-mounted at /usr/local/bin/netlab-dhcp for PCs/servers and installed to /tmp for labs deployed before. Tested against busybox udhcpc + dnsmasq in network namespaces with a bind-mounted resolv.conf.
- Added: reachability matrix (`reach.py`, Dashboard), IOS pipe filters, `show run` gated behind enable, Windows-style ipconfig/arp in the PC prompt, rename carries configs/<node>.
- Fixed: power flag cleared on destroy; port picker closes on Escape. Lint passes (eslint no-undef/hooks, pyflakes) with only unused-import warnings left.

## 2026-10-27: 0.10.0, hardware models, modules, power, IOS-syntax CLI
- `hardware.py`: chassis catalogue (3 routers, 5 switches), 8 modules, port types with media and speed, stable ethN mapping (slot k port p = eth{100(k+1)+p+1}), IOS name parse/resolve, validation on save (cable on a missing port is refused).
- Link speed as netem rate at the slower end, merged with impairments; re-applied after deploy and live wiring.
- Power off/on (`live.power`): remove container, start from lab file, re-plug cables, restore impairments/admin-down. Power state in props, cleared on deploy, ignored by the device-stopped alert.
- `ios.py`: line editor, grammar matcher with IOS abbreviations, ?/Tab, modes, router translation to FRR (interfaces, subifs with dot1Q, loopbacks via dummy, OSPF wildcards, BGP AFs incl. vrf/vpnv4, static routes, LDP), switch commands on props, show commands, ping/traceroute via nsenter with IOS output, reload = power cycle, enable secret, banner, stored accept-only lines.
- Props revision sync (status `rev`/`rev_nodes`, `_rev` on save) so server-side edits survive a stale autosave.
- UI: rear-panel Physical tab with drag-in modules and power, port picker on cabling, serial cable, port names everywhere, CLI mode switch (IOS/FRR/Shell), Settings (module power rule, CLI default, default models), Labs tab with delete.
- Tests: 34 passing (hardware mapping/resolve/validation/speed, mask maths, grammar, FRR→IOS config rendering, rev merge, save validation, router exec). IOS CLI driven end-to-end against a local FRR 8.4 (router) and a namespace bridge (switch show commands); websocket route tested. Not tested against FRR 10 in containers, live power cycle, netem speed caps, or switch VLAN filtering (sandbox kernel lacks vlan_filtering/8021q).

## 2026-10-26 — 0.9.0: alerting, settings, live edit, traffic, login, container + repo
- Rev 7 confirmed working on the VM except the 1-hour history check (deferred, on the roadmap).
- Decisions: in-app notifications only for now (external channels on the roadmap); live add happens immediately; global Settings; GitHub repo with a published container image so updates are `docker compose pull && up -d`.
- **Alerting** (`alerts.py`): rules in settings; evaluated after every metrics sample (device, link, CPU, memory, throughput, errors/drops) and every health interval (BGP/OSPF/LDP via vtysh JSON; ping probes run with the host's ping inside the source device's namespace). `for_s` hold time, grace after a lab comes up (reset on deploy), auto-resolve, history in SQLite (`alerts` table next to metrics), in-memory event feed for toasts. Firing alerts reattach after a restart.
- **Settings** (`settings.py`): `/opt/netlab/settings.json`, defaults merged, validation/clamping, env overrides; labs/templates/metrics read it live. UI: General, Lab defaults, Monitoring, Alert rules (editor incl. ping probes), Notifications, Appearance, Security, About.
- **Live edit** (`live.py`): new device = `docker run` mirroring containerlab's linux node (name, labels incl. `containerlab=<lab>`, mgmt network, binds, privileged, exec) so `containerlab destroy` still cleans it; cable = veth created on the host, moved into each namespace and renamed (host-bridge ends enslaved to the bridge); per-type post-link hooks (switch script re-run, PC/server address, router MPLS input + subinterfaces). Frontend diffs the topology against what's running and applies adds/removes (covers drop, cable, delete, duplicate, undo/redo).
- **Traffic generator** (`traffic.py`): host iperf3 via nsenter on both ends; TCP/UDP, rate, duration, streams, reverse; JSON results.
- **Login** (`auth.py`): optional; PBKDF2 password, HMAC-signed session cookie, ASGI middleware covering /api and /ws; `python -m app.auth reset`.
- **Packaging**: repo layout (`app/`, `labs/`, `deploy/`, `tools/`, `tests/`), Dockerfile (Node build stage + Python runtime with containerlab 0.82.0, Docker CLI 27.5.1, iproute2, iptables, tcpdump, nsenter, iperf3, ping), compose (privileged, host network/PID/cgroup, docker.sock, /opt/netlab and /home same-path mounts), `setup.sh` (modules, migrate from systemd, compose up), `update.sh`, GitHub Actions (pytest + UI build, then build/push to GHCR with latest/semver/sha tags).
- Tests: pytest 17/17 (incl. auth flow, alert fire/resolve, grace, settings validation, live-link failure path). Headless UI: alert toast + bell + device marker, ack, live add → cable → undo → unplug → remove (correct call order), traffic job, probe rule saved, login gate + sign-in. Image build not testable in the sandbox (registries blocked): first CI run is its build test.

## 2026-10-25 — rev 7: GUI upgrade
Monitoring with 48 h history (host-side counters, SQLite), dashboard, traffic overlay, protocol views; link fault injection (netem, presets, up/down/flap); workspace (undo/redo, palette, menus, annotations, align, dark mode); lab manager (templates, snapshots/diff/restore, clone, zip). Fixed the Phase 2 generator's sandbox-path bug.

## 2026-10-25 — rev 4–6
CLI console (uvloop preexec ordering), server DNS bind-dynamic, OSPF form router-id, VLANs/trunking/STP/SVI, router-on-a-stick, Save configs reporting, `write memory` via directory bind.

## 2026-10-25 — Phase 1 and 2 live on FLT-NETLAB-01
DNS fix (`local=/#/` + `host-record`), web hosts start nginx, Phase 2 signed off: failover 0.15/0.22 s down, 1.55/1.62 s up (interface-loss case).

## 2026-10-21 — pre-deploy audit
SPA path traversal, shared `clab-mgmt`, ping argument injection, installer fixes.

## Environment
FLT-NETLAB-01: VM 120 on PV2 (`local` qcow2 40 GB), 4 vCPU host, 6144/4096 MB, vmbr0, 10.10.40.20/24, Ubuntu 24.04.3, containerlab 0.82.0.

## Watch list
- Web-app import of isp-lab-p2 uses lab name isp-lab → destroy the CLI lab first; import a copy.
- Linux-bridge switch STP is 802.1D only (30 s); RSTP/LACP need the OVS engine (untested on the VM).
- 0.11.0 untested on the VM: OVS engine, the five new templates as running labs, vsftpd/busybox adduser on Alpine, FRR 10 behaviour of the Config tab.
- Clones of labs with host bridges share the bridge.
- Live-added devices: images not already on the VM are pulled on first add (slow the first time).
