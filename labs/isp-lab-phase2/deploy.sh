#!/usr/bin/env bash
# Deploy Phase 2. Loads MPLS/VRF kernel modules, creates br-inet, deploys.
set -euo pipefail
cd "$(dirname "$0")"
for m in mpls_router mpls_iptunnel vrf; do sudo modprobe "$m"; done
printf "mpls_router\nmpls_iptunnel\nvrf\n" | sudo tee /etc/modules-load.d/netlab.conf >/dev/null
if ! ip link show br-inet >/dev/null 2>&1; then
  sudo ip link add br-inet type bridge
  sudo ip link set br-inet up
fi
sudo iptables -C DOCKER-USER -i br-inet -j ACCEPT 2>/dev/null \
  || sudo iptables -I DOCKER-USER -i br-inet -j ACCEPT
sudo containerlab deploy -t isp-lab.clab.yml --reconfigure
