# Changelog

## Unreleased
- Docs: README rewritten with screenshots and diagrams; `docs/architecture.md` (how netlab works); diagrams in `docs/images/` (light/dark aware SVG).
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
