# netlab

Self-hosted, Packet Tracer-style network lab for real protocol work. Every device is a real container (FRR routers with OSPF, BGP, MPLS/LDP and VRFs; 802.1Q switches; Linux PCs and servers) run by [containerlab](https://containerlab.dev). The browser UI gives you a drag-and-drop canvas, device windows with an FRR CLI, live monitoring with history, alerting, link fault injection, a traffic generator, a simulation mode built from real packet captures, and snapshots with diff and rollback.

Personal tool. Runs on FLT-NETLAB-01 (Ubuntu 24.04 VM on PV2).

## Run it (container)

One-time setup on the VM, from a clone of this repo:

```bash
git clone https://github.com/<github-user>/netlab.git && cd netlab
# private repo/package: log in to GHCR once with a token that has read:packages
echo "<token>" | sudo docker login ghcr.io -u <github-user> --password-stdin
./deploy/setup.sh ghcr.io/<github-user>/netlab:latest
```

Open `http://<vm-ip>:8080`. `setup.sh` loads the kernel modules at boot, stops the old systemd install if present, writes `/opt/netlab/deploy/{docker-compose.yml,.env}` and starts the container.

### Update

Every push to `main` builds and publishes `ghcr.io/<github-user>/netlab:latest` (tags `vX.Y.Z` also publish `X.Y.Z`). On the VM:

```bash
cd /opt/netlab/deploy && sudo docker compose pull && sudo docker compose up -d
```

(or `./deploy/update.sh`). Labs, snapshots, settings and metrics history live in `/opt/netlab`, outside the container. Running labs keep running while netlab restarts. Pin a version by setting `NETLAB_IMAGE=ghcr.io/<github-user>/netlab:0.9.0` in `/opt/netlab/deploy/.env`.

### Build locally instead

```bash
sudo docker build -t netlab:local .
sudo sed -i 's|^NETLAB_IMAGE=.*|NETLAB_IMAGE=netlab:local|' /opt/netlab/deploy/.env
cd /opt/netlab/deploy && sudo docker compose up -d
```

## Security

netlab runs privileged with the Docker socket: whoever reaches it controls the VM. Turn on the login (Settings → Security) and publish it only through Nginx Proxy Manager with Authentik forward auth; firewall port 8080 to the proxy. Locked out: `sudo docker exec netlab python -m app.auth reset`.

## Features

- **Build**: device palette, copper/fibre cables, notes and coloured areas, align/distribute, snap, undo/redo, command palette (Ctrl+K), context menus, dark mode, shortcuts (`?`).
- **Templates**: OSPF triangle, router-on-a-stick, campus with STP, dual-ISP MPLS L3VPN.
- **Live editing**: devices and cables added to a running lab start and wire immediately (veth hot-plug); removing them unplugs live. Undo works live too.
- **Devices**: Config forms with equivalent commands, FRR CLI (`write memory` works), PC desktop (IP config, command prompt, browser), server DNS/DHCP/HTTP, switch VLANs/trunks/native/STP priority/SVI, router-on-a-stick subinterfaces.
- **Monitoring**: per-device CPU/memory, per-port throughput/packets/drops/errors with history (default 48 h), parsed routing table and OSPF/BGP/LDP/BFD state, lab dashboard, traffic overlay on the canvas.
- **Alerting**: rules for device stopped, link down, BGP/OSPF/LDP sessions, CPU, memory, throughput, errors/drops, and ping probes (loss/RTT, IP SLA-style). Evaluated server-side; toasts, bell badge, notifications history with acknowledge.
- **Fault injection**: link up/down, flap, netem delay/jitter/loss/rate/duplicate/corrupt per direction, presets (4G, satellite, lossy Wi-Fi, congested WAN, ...).
- **Traffic generator**: iperf3 TCP/UDP between any two devices.
- **Lab management**: snapshots with diff and restore, auto-snapshot before deploy, clone, zip export/import, containerlab import.
- **Settings**: lab defaults (images, management network, MTU), monitoring intervals and retention, alert rules, notifications, appearance, security.

## Repository layout

| Path | What |
| --- | --- |
| `app/backend` | FastAPI app (`app/main.py` routes; `labs`, `devices`, `live`, `metrics`, `alerts`, `monitor`, `impair`, `traffic`, `labmgmt`, `templates`, `settings`, `auth`) |
| `app/frontend` | React + React Flow + xterm.js UI (Vite) |
| `labs/` | The original Phase 1 and Phase 2 containerlab labs with deploy/verify scripts and the Phase 2 generator |
| `deploy/` | `docker-compose.yml`, `setup.sh`, `update.sh` |
| `tools/` | `netlab-preflight.sh` host checks |
| `tests/` | pytest suite (runs in CI) |
| `docs/` | build log and design notes |

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r app/backend/requirements-dev.txt
pytest -q tests
cd app/frontend && npm ci && npm run dev        # UI on :5173, proxies /api to :8080
```

See [ROADMAP.md](ROADMAP.md) for what's next and [CHANGELOG.md](CHANGELOG.md) for what changed.
