#!/usr/bin/env bash
# One-time setup of netlab as a container on Ubuntu 24.04 (FLT-NETLAB-01).
#   ./deploy/setup.sh ghcr.io/<github-user>/netlab:latest
# Safe to re-run. Migrates from the old systemd install; labs in /opt/netlab are kept.
set -euo pipefail
IMAGE="${1:?usage: $0 ghcr.io/<github-user>/netlab:latest}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== kernel modules (loaded at every boot)"
printf "mpls_router\nmpls_iptunnel\nvrf\n8021q\nbridge\nsch_netem\ndummy\n" | sudo tee /etc/modules-load.d/netlab.conf >/dev/null
for m in mpls_router mpls_iptunnel vrf 8021q bridge sch_netem dummy; do
  sudo modprobe "$m" 2>/dev/null || { sudo apt-get install -y "linux-modules-extra-$(uname -r)"; sudo modprobe "$m"; }
done

echo "== docker"
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
sudo docker compose version >/dev/null

# (not "systemctl list-unit-files | grep -q": with pipefail, grep -q's early exit SIGPIPEs systemctl and the test is false)
if systemctl cat netlab.service >/dev/null 2>&1; then
  echo "== stopping the old systemd netlab (data is kept)"
  sudo systemctl disable --now netlab || true
fi

echo "== compose project in /opt/netlab/deploy"
sudo mkdir -p /opt/netlab/labs /opt/netlab/deploy
sudo cp "$HERE/docker-compose.yml" /opt/netlab/deploy/docker-compose.yml
printf "NETLAB_IMAGE=%s\nNETLAB_PORT=8080\n" "$IMAGE" | sudo tee /opt/netlab/deploy/.env >/dev/null

cd /opt/netlab/deploy
sudo docker compose pull
sudo docker compose up -d
sleep 5
sudo docker compose ps
curl -fsS "http://127.0.0.1:8080/api/version" && echo && echo "netlab is up: http://$(hostname -I | awk '{print $1}'):8080"
