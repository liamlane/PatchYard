# Changelog

## 0.10.0
- **Hardware models and modules**: routers and switches can have a chassis (branch / ISR / 10G edge routers; Fast Ethernet 24+2, compact 8+2, access 24+4 / 48+4, 10G distribution switches) with IOS-style port names (Gi0/0/0, Fa0/1, Te1/1/1, Se0/1/0). Physical tab shows the rear panel with live port LEDs; drag modules (GE copper, GE SFP, 10GE SFP+, serial T1/E1, switch uplinks) into slots. Port names show everywhere (canvas, link panel, monitor, dashboard, alerts).
- **Real port behaviour**: cables must match the port (copper, fibre/SFP, new serial cable), a port picker opens when you plug into a modular device, and links run at the slower end's speed (Fa 100M, Gi 1G, serial 1.544M; 10G uncapped) via netem, combined with any impairment.
- **Power**: power devices off/on inside a running lab (Physical tab). Unsaved config is lost like on real kit; cables re-plug and link settings re-apply on power-on. Module swaps need the power off (Settings → General, can be turned off).
- **IOS-syntax CLI** (CLI tab, default): user/privileged/config modes, abbreviations, `?` help, Tab completion, history, Ctrl+Z/Ctrl+C. Routers translate to FRR (interfaces, subinterfaces with `encapsulation dot1Q`, loopbacks, OSPF with wildcard masks, BGP incl. address families, static routes, MPLS LDP) and translate output back; unknown commands pass to FRR. Switches edit VLANs, access/trunk ports, native/allowed VLANs, SVI, default gateway, STP priority, shutdown/description. `show` commands: running/startup-config, ip interface brief, interfaces (+status/trunk), ip route, vlan brief, spanning-tree, mac address-table, cdp neighbors (from the cabling), arp, version, inventory. `ping` / `traceroute` with IOS output, `reload` power-cycles. FRR and shell modes are still there.
- **Lab manager**: new Labs tab listing every lab with Delete / Destroy & delete.
- **Reachability check** (Dashboard): pings every PC/server from every other one and shows a colour matrix with RTTs, like a connectivity "check results".
- **IOS output filters**: `| include`, `| exclude`, `| begin`, `| section`, `| count` on any show command (and `do`). `show running-config` needs `enable`, as on IOS.
- **PC command prompt**: `ipconfig /all`, `ipconfig /release`, `ipconfig /renew`, `arp -a`.
- Renaming a device moves its saved config folder (and fixes the hostname in frr.conf).
- Fix: DHCP on PCs/servers failed with `mv: can't rename '/etc/resolv.conf…': resource busy` (Docker bind-mounts resolv.conf; busybox's hook replaces it with mv). netlab now ships its own udhcpc hook that writes the file in place.
- Fix: power-off state is cleared when a lab is destroyed; Escape / changing tool closes the port picker.
- Device props edited on the server (IOS CLI, power) sync to the open browser and can't be overwritten by an older autosave.
- Settings: power-off rule for modules, CLI default, default router/switch model for new devices.
- `dummy` kernel module loaded (loopbacks); `traceroute` added to the image; version shows as `0.10.0+main.N` for branch builds (`VERSION` file).
- Docs: README with screenshots and diagrams, `docs/architecture.md`.
- `setup.sh` now detects the old `netlab.service` reliably (the `grep -q` check failed under `pipefail`).

## 0.9.0
- **Alerting**: rules engine (device stopped, link down, BGP/OSPF/LDP, CPU, memory, throughput, errors/drops, ping probes), server-side evaluation, grace period after deploy, "for" duration, auto-resolve; bell badge, notifications panel with acknowledge and history, toasts by severity, optional sound; devices with firing alerts are marked on the canvas.
- **Settings**: global, server-side (`/opt/netlab/settings.json`): live editing, auto-snapshot before deploy, lab default images/management network/MTU, monitoring interval/retention/check interval, alert rules editor, notification preferences, appearance, security, about/update.
- **Live editing**: new devices start and new cables wire into a running lab immediately (veth hot-plug, per-type port hooks); deletes and undo apply live.
- **Traffic generator**: iperf3 TCP/UDP between any two devices, rate, duration, streams, reverse; results with retransmits or loss/jitter.
- **Security**: optional login (PBKDF2 password, signed session cookie) covering the API and consoles; CLI reset.
- **Packaging**: Docker image (containerlab, Docker CLI and host tools included), compose deployment, `setup.sh` migration from systemd, `update.sh`, GitHub Actions test + build + publish to GHCR, pytest suite.
- Fixes: Phase 2 generator no longer depends on a build-sandbox path and refuses to overwrite a non-empty folder; unknown `/api` paths return 404.

## 0.8.0 (rev 7)
Monitoring with 48 h history, dashboard, traffic overlay, protocol views, link fault injection with presets, workspace/UX upgrade, lab manager (templates, snapshots, diff, restore, clone, zip).

## 0.7.0 (rev 4–6)
CLI console fix (uvloop), server DNS fix, OSPF form, VLANs/trunking/STP/SVI, router-on-a-stick, Save configs reporting, `write memory`.

## 0.6.0 (rev 2–3)
Path-traversal fix, shared management network, DNS fix (`local=/#/` + `host-record`), web hosts start nginx, Phase 1/2 verified live.
