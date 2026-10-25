#!/usr/bin/env bash
# FLT-NETLAB-01 preflight: run on the VM before any deploy. Every line should say OK.
c() { local o; if o=$(eval "$2" 2>&1); then echo "OK    $1"; else echo "FAIL  $1  -> $(echo "$o" | tail -1)"; fi; }
c "Ubuntu 24.04"                  'grep -q "24.04" /etc/os-release'
c ">=4 GB RAM"                    '[ $(awk "/MemTotal/{print \$2}" /proc/meminfo) -gt 3800000 ]'
c ">=15 GB free on /"             '[ $(df --output=avail -k / | tail -1) -gt 15000000 ]'
for m in mpls_router mpls_iptunnel vrf bridge 8021q sch_netem; do c "kernel module $m" "sudo modprobe $m"; done
c "MPLS sysctl present"           'test -e /proc/sys/net/mpls/platform_labels'
c "docker running"                'sudo docker info'
c "containerlab installed"        'containerlab version'
c "no 172.31.255.0/24 clash"      '! ip route | grep -v "clab-mgmt\|br-" | grep -q "172.31.255."'
c "reach quay.io"                 'curl -sfI -m 8 https://quay.io/v2/ -o /dev/null || [ $? -eq 22 ]'
c "reach ghcr.io"                 'curl -sI -m 8 https://ghcr.io/v2/ -o /dev/null'
c "FRR image can build a VRF"     'sudo docker run --rm --privileged quay.io/frrouting/frr:10.0.0 ip link add vrftest type vrf table 9'
