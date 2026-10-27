#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
sudo containerlab destroy -t isp-lab.clab.yml --cleanup
sudo iptables -D DOCKER-USER -i br-inet -j ACCEPT 2>/dev/null || true
sudo ip link del br-inet 2>/dev/null || true
