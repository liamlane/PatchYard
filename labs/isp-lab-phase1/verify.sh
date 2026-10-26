#!/usr/bin/env bash
# Phase 1 checks. Run after deploy; give BGP ~30s to converge.
P=clab-isp-lab
x() { echo; echo "===== $1: $2"; sudo docker exec "$P-$1" sh -c "$2"; }
for r in inet-r1 isp1-core isp1-pe-a c1a-r1; do
  x $r 'vtysh -c "show bgp summary"'
done
x isp1-core 'vtysh -c "show ip ospf neighbor"'
x isp1-core 'vtysh -c "show ip bgp"'
x inet-r1   'vtysh -c "show ip bgp"'
x c1a-r1    'vtysh -c "show ip route"'
x c1a-web   'nslookup www.inet.lab 1.1.1.1; nslookup www.inet.lab 1.1.1.2'
x c1a-web   'curl -s -m 5 http://www.inet.lab | head -n 5'
x c1a-web   'traceroute -n -w 1 1.1.1.3'
