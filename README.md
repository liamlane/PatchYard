# netlab

A self-hosted network lab that looks like Packet Tracer and runs real protocols underneath. Every device on the canvas is a real Linux container. Routers run FRR (OSPF, BGP, MPLS/LDP, VRFs), switches are 802.1Q Linux bridges with STP, and PCs and servers run real DHCP, DNS and HTTP. The browser UI is for building, configuring, breaking and watching them.

Personal tool. Runs on FLT-NETLAB-01 (Ubuntu 24.04 VM on Proxmox PV2).

![Dual-ISP MPLS L3VPN lab on the netlab canvas](docs/images/screen-canvas-dual-isp.png)

## What it does

- **Build** topologies by drag and drop: routers, switches, PCs and servers, joined by copper or fibre cables. You can add notes and coloured areas, then align and snap them. Undo/redo, a command palette (Ctrl+K) and dark mode are included.
- **Configure** each device through Packet Tracer-style forms that show the equivalent commands, or open the CLI tab for FRR's `vtysh` (`write memory` works).
- **Run** the lab with one click. containerlab starts the containers and wires the cables. Devices and cables you add to a running lab start and connect straight away, without a redeploy.
- **Watch** live throughput, drops and errors per port, CPU and memory per device, and routing tables with OSPF/BGP/LDP state. History is kept for 48 hours, and a traffic overlay shows load on the cables.
- **Alert** on a stopped device, a link down, BGP/OSPF/LDP sessions, CPU, memory, throughput, errors, or ping-probe loss/RTT. Alerts appear as toasts and a bell badge, with a history you can acknowledge.
- **Break** links on purpose: up/down, flapping, or netem delay, jitter, loss, rate limits and corruption. Presets cover 4G, satellite, lossy Wi-Fi and a congested WAN.
- **Test** with the iperf3 traffic generator between any two devices, and step through real packet captures in Simulation mode.
- **Manage** labs from templates. Snapshots can be diffed and restored, and labs can be cloned or exported/imported as zip files. Global settings and an optional login are included.

## Screenshots

| | |
| --- | --- |
| ![Campus lab with STP, light theme](docs/images/screen-canvas-campus.png) | ![Switch VLAN, trunk and STP configuration](docs/images/screen-device-switch.png) |
| Campus template: three switches in an STP loop, VLANs, inter-VLAN routing | Switch config: VLAN database, access/trunk ports, native VLAN, STP priority |
| ![Router configuration form](docs/images/screen-device-router.png) | ![Alert rules in Settings](docs/images/screen-settings-alerts.png) |
| Router config forms: interfaces, subinterfaces, static routes, OSPF, BGP | Alert rules with hold times, severities and ping probes |

![Lab manager templates](docs/images/screen-lab-manager.png)

## Built with

| Layer | What |
| --- | --- |
| Lab runtime | [containerlab](https://containerlab.dev) 0.82, Docker Engine, Linux veth pairs, network namespaces |
| Routing | [FRR](https://frrouting.org) 10.0: OSPF, BGP, MPLS/LDP, VRFs, static routes |
| Switching | Linux bridge with `vlan_filtering` (802.1Q trunks/access, native VLAN), kernel 802.1D STP, SVI |
| Hosts | `network-multitool` PCs; Alpine servers with dnsmasq (DNS/DHCP) and nginx (HTTP) |
| Backend | Python 3.12, FastAPI, uvicorn, SQLite (metrics and alert history), scapy (packet decode) |
| Network tooling | iproute2, `tc netem`, `nsenter`, tcpdump, iperf3, ping |
| Frontend | React 18, React Flow (`@xyflow/react`) canvas, xterm.js consoles, Vite |
| Packaging | Docker image, docker compose, GitHub Actions, GitHub Container Registry |
| Host | Ubuntu 24.04 VM on Proxmox, kernel modules `mpls_router mpls_iptunnel vrf 8021q bridge sch_netem` |

## How it works

![netlab architecture](docs/images/architecture.svg)

netlab is one privileged container on the VM. It serves the UI and the API on port 8080, and uses the host's Docker socket and containerlab to run each lab device as its own container. Because it shares the host's network, PID and cgroup namespaces, it can:

- read each device's interface counters from `/proc`,
- read CPU and memory from cgroups,
- step into any device with `nsenter` to apply `tc netem`, run iperf3 or ping probes, or capture packets.

Everything that has to survive an update lives in `/opt/netlab` on the host: labs, configs, snapshots, settings and metrics.

![Lab lifecycle](docs/images/lifecycle.svg)

The full walk-through is in [docs/architecture.md](docs/architecture.md): deploy, live edit, monitoring, alerting, fault injection, simulation, storage and security.

## Run it

One-time setup on the VM:

```bash
git clone https://github.com/liamlane/netlab.git && cd netlab
echo "<token with read:packages>" | sudo docker login ghcr.io -u liamlane --password-stdin
./deploy/setup.sh ghcr.io/liamlane/netlab:latest
```

Then open `http://<vm-ip>:8080`.

`setup.sh` does the following:

- loads the kernel modules now and at every boot,
- installs Docker if it's missing,
- stops the old systemd install if there is one,
- writes `/opt/netlab/deploy/{docker-compose.yml,.env}`,
- starts the container.

## Update

![Release and update flow](docs/images/release-flow.svg)

Commit and push from GitHub Desktop. Actions tests the code, builds the image and publishes it to GHCR. Then on the VM:

```bash
cd ~/netlab && git pull && ./deploy/update.sh
```

Running labs keep running while netlab restarts. To pin a version, set `NETLAB_IMAGE=ghcr.io/liamlane/netlab:0.9.0` in `/opt/netlab/deploy/.env`. To build on the VM instead, run `sudo docker build -t netlab:local .` and set `NETLAB_IMAGE=netlab:local`.

## Security

netlab runs privileged with the Docker socket, so anyone who can reach it controls the VM. Turn on the login (Settings → Security). Publish netlab only through a reverse proxy with authentication, and firewall port 8080 to that proxy. If you're locked out, run `sudo docker exec netlab python -m app.auth reset`.

## Repository

| Path | What |
| --- | --- |
| `app/backend` | FastAPI app: `main` (routes), `labs`, `devices`, `live`, `metrics`, `alerts`, `monitor`, `impair`, `traffic`, `capture`, `labmgmt`, `templates`, `settings`, `auth` |
| `app/frontend` | React UI (Vite) |
| `labs/` | The original Phase 1 and Phase 2 containerlab labs, with deploy/verify scripts and the Phase 2 generator |
| `deploy/` | `docker-compose.yml`, `setup.sh`, `update.sh` |
| `tools/` | `netlab-preflight.sh` host checks |
| `tests/` | pytest suite (runs in CI) |
| `docs/` | Architecture notes, build log, images |

## Develop

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r app/backend/requirements-dev.txt
pytest -q tests
cd app/frontend && npm ci && npm run dev        # UI on :5173, proxies /api to :8080
```

[ROADMAP.md](ROADMAP.md) lists what's next. [CHANGELOG.md](CHANGELOG.md) lists what changed.
