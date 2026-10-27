# Roadmap

## Next (0.11.0)
- **New device types**: Open vSwitch switches (RSTP/MSTP, LACP, SPAN), VyOS router/firewall (NAT, VPN, policy routing), nftables firewall with a zone/rule form, extra servers (RADIUS, TACACS+, syslog, NTP, SNMP, TFTP).
- IOS CLI: VRF definition/forwarding, packet ACLs and NAT once the device types above provide them; `--More--` paging.
- **External notifications**: email (SMTP), phone push via ntfy, Teams/Slack webhooks, per-rule routing and quiet hours.
- **Verify the 1-hour history check** on the VM (deferred from rev 7 testing).

## Planned
- SD-WAN-style template: branch with dual internet, policy routing and probe-based path failover.
- BFD in the dual-ISP lab (closes the 180 s silent-failure gap) and BFD peers in templates.
- More protocols/templates: IS-IS, IPv6 dual-stack, VXLAN/EVPN fabric, VyOS firewall/NAT edge.
- Download Simulation captures as pcap for Wireshark.
- Lab challenges: Packet Tracer-style activities with a Check button (reachability + protocol assertions).
- Scheduled snapshots; include `/opt/netlab` in the Restic backup.
- Publish through NPM + Authentik; in-app "update available" check against GHCR.
- Self-test script: deploy a template, run checks, report pass/fail (for verifying each release on the VM).
- Templates built on chassis models (current templates use generic devices).
- SR Linux / Arista cEOS device types; VM-based images if nested virtualisation is enabled on PV2.
- Per-hop Simulation stepping (hold packets at each device like Packet Tracer).

## Done
- Phase 1 and Phase 2 containerlab labs verified on FLT-NETLAB-01 (incl. timed failover test).
- Web app: canvas, device windows, FRR CLI, PC desktop, server services, Simulation mode.
- VLANs/trunking, router-on-a-stick, STP, management SVI.
- Monitoring with history, dashboard, traffic overlay, protocol views.
- Link fault injection with presets.
- Workspace polish: undo/redo, palette, context menus, annotations, align, dark mode.
- Lab management: templates, snapshots/diff/restore, clone, export/import.
- Alerting with notifications centre; global Settings; live add/remove without redeploy; traffic generator; optional login.
- Container image + GitHub Actions publishing to GHCR.
- 0.10.0: hardware models with modules, port types/speeds, power on/off, IOS-syntax CLI, lab delete in the manager.
