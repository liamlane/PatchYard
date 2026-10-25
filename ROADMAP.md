# Roadmap

## Next
- **IOS-syntax CLI (Step 5c)**: translate common IOS commands (`show ip int brief`, `conf t`, `router ospf 1` with wildcard masks, `ip route`, `interface Gi0/1` aliases) to FRR, with `?` help and tab completion; unknown commands pass through to vtysh.
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
- RSTP/MSTP via Open vSwitch or mstpd (kernel bridge is 802.1D only).
- Renaming a device carries its saved config folder.
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
