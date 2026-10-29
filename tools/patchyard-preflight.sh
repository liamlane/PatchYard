#!/usr/bin/env bash
# Patchyard preflight: checks a host before (or after) installing. Read-only apart from modprobe.
#   sudo ./tools/patchyard-preflight.sh
# FAIL = Patchyard won't work; WARN = a feature won't work (named on the line).
SUDO=""; [ "$(id -u)" = 0 ] || SUDO=sudo
r() { printf '%-5s %-34s %s\n' "$1" "$2" "$3"; }
c() { if eval "$3" >/dev/null 2>&1; then r OK "$2"; else r "$1" "$2" "${4:-}"; fi; }
. /etc/os-release 2>/dev/null
r INFO "OS" "${PRETTY_NAME:-unknown}, kernel $(uname -r), $(uname -m)"
c FAIL "x86_64 CPU"                    '[ "$(uname -m)" = x86_64 ]' "this build is x86_64 only"
c WARN ">= 4 GB RAM"                   '[ $(awk "/MemTotal/{print \$2}" /proc/meminfo) -gt 3800000 ]' "small labs only"
c WARN ">= 15 GB free on /"            '[ $(df --output=avail -k / | tail -1) -gt 15000000 ]' "device images need space"
c FAIL "docker daemon"                 "$SUDO docker info" "install Docker Engine (the installer does this)"
c FAIL "docker compose plugin"         "$SUDO docker compose version"
c FAIL "docker is not the snap"        '! (command -v snap && snap list docker)' "remove the docker snap; containerlab can't use it"
for m in bridge 8021q sch_netem dummy nf_tables; do c FAIL "kernel module $m" "$SUDO modprobe $m" "core feature"; done
c WARN "kernel module vrf"             "$SUDO modprobe vrf" "router VRFs"
c WARN "kernel modules mpls"           "$SUDO modprobe mpls_router && $SUDO modprobe mpls_iptunnel" "MPLS/LDP, MPLS L3VPN template (Ubuntu: linux-modules-extra-\$(uname -r))"
c WARN "kernel module openvswitch"     "$SUDO modprobe openvswitch" "Open vSwitch switch engine (RSTP, LACP)"
c WARN "no 172.31.255.0/24 clash"      '! ip route | grep -v "clab\|br-" | grep -q "172\.31\.255\."' "change the management subnet in Settings > Lab defaults"
c WARN "reach ghcr.io"                 'curl -sI -m 8 https://ghcr.io/v2/ -o /dev/null' "the Patchyard image comes from GHCR"
c WARN "reach quay.io"                 'curl -sI -m 8 https://quay.io/v2/ -o /dev/null' "FRR router image"
c WARN "reach dl-cdn.alpinelinux.org"  'curl -sI -m 8 https://dl-cdn.alpinelinux.org/ -o /dev/null' "servers/firewalls install packages at deploy"
if [ -n "$(command -v systemd-detect-virt)" ]; then r INFO "virtualisation" "$(systemd-detect-virt 2>/dev/null || echo none)"; fi
