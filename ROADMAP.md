# Roadmap

## Next (0.13.0)
- **Lab challenges**: Packet Tracer-style activities with instructions and a Check button (reachability, protocol and config assertions, percentage score), shareable as files.
- **External notifications**: email (SMTP), phone push via ntfy, Teams/Slack webhooks, per-rule routing and quiet hours.
- Firewall: site-to-site WireGuard/IPsec VPN, logging of dropped traffic to a syslog server.
- arm64 image (Raspberry Pi 5, Ampere, Apple Silicon VMs).
- Verify the 1-hour history check on a long-running install.

## Planned
- SD-WAN-style template: branch with dual internet, policy routing and probe-based path failover.
- BFD in the dual-ISP lab (closes the 180 s silent-failure gap) and BFD peers in templates.
- More protocols/templates: IS-IS, IPv6 dual-stack, VXLAN/EVPN fabric.
- RADIUS / TACACS+ servers with IOS `aaa` login on routers and switches; SNMP agent and a simple poller.
- OVS port mirroring (SPAN) into a capture PC; MST.
- VyOS as a device type (needs an image built from the VyOS ISO; no official container image).
- Download Simulation captures as pcap for Wireshark.
- Scheduled snapshots; include `/opt/netlab` in the Restic backup.
- Multi-user follow-ups: per-user disk quota, password reset by email, two-factor login, audit log, shared (team) labs, read-only lab sharing for instructors.
- Self-test script: deploy a template, run checks, report pass/fail (for verifying each release on the VM).
- Move the four original templates onto hardware models (the six 0.11 templates already use them).
- IOS `copy … ftp://` and an SMTP/email service on servers (Packet Tracer's EMAIL page).
- SR Linux / Arista cEOS device types; VM-based images where nested virtualisation is available.
- Per-hop Simulation stepping (hold packets at each device like Packet Tracer).

## Done
- Phase 1 and Phase 2 containerlab labs verified on a live host (incl. timed failover test).
- Web app: canvas, device windows, FRR CLI, PC desktop, server services, Simulation mode.
- VLANs/trunking, router-on-a-stick, STP, management SVI.
- Monitoring with history, dashboard, traffic overlay, protocol views.
- Link fault injection with presets.
- Workspace polish: undo/redo, palette, context menus, annotations, align, dark mode.
- Lab management: templates, snapshots/diff/restore, clone, export/import.
- Alerting with notifications centre; global Settings; live add/remove without redeploy; traffic generator; optional login.
- Container image + GitHub Actions publishing to GHCR.
- 0.10.0: hardware models with modules, port types/speeds, power on/off, IOS-syntax CLI, lab delete in the manager.
- 0.12.0: router ACLs, NAT, DHCP relay with multi-pool DHCP, VRFs from the IOS CLI; renamed to Patchyard with a one-line installer; multi-user with invites, per-user labs and limits, sandboxed lab devices and HTTPS for public hosting.
- 0.11.0: Packet Tracer-style Config and Services tabs, firewall device (zones, rules, NAT), Open vSwitch switches (RSTP, LACP), FTP/TFTP/Syslog/NTP servers, DHCP reservations and leases, host NICs and copper SFPs, IOS copy tftp and logging host, six new templates.
