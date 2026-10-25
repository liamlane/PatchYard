#!/usr/bin/env bash
# Deploy the ISP lab. Creates the host bridge for 1.1.1.0/28 first.
set -euo pipefail
cd "$(dirname "$0")"
if ! ip link show br-inet >/dev/null 2>&1; then
  sudo ip link add br-inet type bridge
  sudo ip link set br-inet up
fi
# stop Docker's iptables dropping frames bridged across br-inet
sudo iptables -C DOCKER-USER -i br-inet -j ACCEPT 2>/dev/null \
  || sudo iptables -I DOCKER-USER -i br-inet -j ACCEPT
sudo containerlab deploy -t isp-lab.clab.yml --reconfigure
