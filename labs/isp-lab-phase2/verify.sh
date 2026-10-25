#!/usr/bin/env bash
# Phase 2 checks. Give OSPF/LDP/BGP ~60s after deploy.
P=clab-isp-lab
x() { echo; echo "===== $1: $2"; sudo docker exec "$P-$1" sh -c "$2"; }
v() { x "$1" "vtysh -c \"$2\""; }
for c in isp1-core1 isp2-core1; do
  v $c "show ip ospf neighbor"
  v $c "show mpls ldp neighbor"
  v $c "show bgp ipv4 vpn summary"
  v $c "show bgp vrf INTERNET ipv4 unicast"
done
v inet-r1  "show bgp ipv4 unicast"
v isp1-pe-a "show bgp vrf C1A ipv4 unicast"
v isp1-pe-a "show ip route vrf C1A"
v isp1-pe-a "show mpls table"
v c1a-r1   "show ip route"
x c1a-web  'nslookup www.inet.lab 1.1.1.1; nslookup www.inet.lab 1.1.1.2'
x c1a-web  'curl -s -m 5 http://www.inet.lab | head -n 3'
x c1a-web  'curl -s -m 5 http://c1b.isp1.lab | head -n 3'
x c1a-web  'curl -s -m 5 http://c2a.isp2.lab | head -n 3'
x c1a-web  'traceroute -n -w 1 92.20.100.2'
# kernel view (added): FRR can show labels the kernel never installed
x isp1-pe-a  'ip -M route; ip route show vrf C1A'
x isp1-core1 'ip -M route; ip route show vrf INTERNET'

# ---------- scripted failover (timed) ----------
# Drops isp1-core1's INET transit (eth1, VRF INTERNET) and times, from the link event:
#  control plane - PE-A loses / regains a C1A default path from core1 (81.10.254.1)
#  data plane    - c1a-web fetches http://www.inet.lab (web1)
# Skip with: NOFAILOVER=1 ./verify.sh
if [ -z "${NOFAILOVER:-}" ]; then
  d() { sudo docker exec "$P-$1" sh -c "$2"; }
  dflt() { d isp1-pe-a 'vtysh -c "show bgp vrf C1A ipv4 unicast 0.0.0.0/0"'; }
  via_core1() { dflt | grep -Eq '^ +81\.10\.254\.1 \(metric'; }
  web_ok()    { d c1a-web 'curl -s -m 1 http://www.inet.lab' | grep -q web1; }
  now() { date +%s.%N; }
  el()  { awk "BEGIN{printf \"%.2f\", $(now)-$1}"; }
  wait_for() {  # desc, test, timeout-seconds (wall clock), [start time; default now]
    local s=${4:-$(now)} e
    while :; do
      if eval "$2" >/dev/null 2>&1; then printf "  PASS %-46s %7ss\n" "$1" "$(el "$s")"; return 0; fi
      e=$(el "$s"); awk "BEGIN{exit !($e > $3)}" && break
      sleep 0.2
    done
    printf "  FAIL %-46s >%ss\n" "$1" "$3"; FO_FAIL=1; return 1; }
  paths() { echo "  C1A default paths from: $(dflt | grep -Eo '^ +81\.10\.254\.[12] \(metric' | grep -Eo '81\.10\.254\.[12]' | sort | uniq -c | tr -s ' ' | tr '\n' ' ')"; }
  FO_FAIL=0
  echo; echo "===== FAILOVER: isp1-core1 eth1 (INET transit) down/up"
  wait_for "baseline: C1A default path from core1"          via_core1 30
  wait_for "baseline: c1a-web -> www.inet.lab"              web_ok    30
  paths
  d isp1-core1 'ip link set eth1 down'; t0=$(now)
  wait_for "down: core1 path withdrawn (control)"           '! via_core1' 60 "$t0"
  wait_for "down: c1a-web -> www.inet.lab via core2 (data)" web_ok      60 "$t0"
  paths
  d isp1-core1 'ip link set eth1 up'; t1=$(now)
  wait_for "up: core1 path back (control)"                  via_core1 180 "$t1"
  wait_for "up: c1a-web -> www.inet.lab (data)"             web_ok    30  "$t1"
  paths
  [ "$FO_FAIL" = 0 ] && echo "  FAILOVER RESULT: PASS" || echo "  FAILOVER RESULT: FAIL"
fi
