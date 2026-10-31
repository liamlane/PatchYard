# Roadmap

## Next (0.15.0)
- **Shared labs:** invite other users to a lab (edit or read-only), so an instructor can watch or help.
- **Passkeys** (WebAuthn) as a second factor or instead of a password.
- **Challenge packs:** import/export a set of challenges, and more built-ins (STP, HSRP-style failover with FRR's VRRP, DHCP relay, IPv6).
- Download Simulation captures as pcap for Wireshark.
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
- Firewall: site-to-site WireGuard/IPsec VPN, logging of dropped traffic to a syslog server.
- arm64 image (Raspberry Pi 5, Ampere, Apple Silicon VMs).
- Move the four original templates onto hardware models (the six 0.11 templates already use them).
- IOS `copy … ftp://` and an SMTP/email service on servers (Packet Tracer's EMAIL page).
- SR Linux / Arista cEOS device types; VM-based images where nested virtualisation is available.
- Per-hop Simulation stepping (hold packets at each device like Packet Tracer).

## Done
- 0.14.0: two-factor sign-in with recovery codes and a per-server policy; alert channels (ntfy, Teams, Slack, webhooks) with per-rule routing, quiet hours and rate limits; personal phone alerts; three new built-in challenges and a leaderboard.
- 0.13.0: email (SMTP) with test and status, forgot-password, emailed invites and alert digests; lab challenges with a Check button, built-ins and publishing; audit log; per-user disk allowance; self-test; performance and stability pass.
- 0.12.1: account requests with admin approval, names and emails on accounts, built-in help & how-to guide.
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
