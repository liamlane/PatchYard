#!/usr/bin/env python3
"""Regenerate every Phase 2 file (dual ISP, RR pair per ISP, MPLS L3VPN, INTERNET VRF).

  python3 generate.py <outdir> [lab-name]      # outdir must not exist or be empty
Also imported by the netlab web app's "ISP MPLS L3VPN" template.
"""
import os, sys, yaml

WEBHOST = '#!/bin/sh\n# netlab web host: serve a page naming this host and its eth1 address on :80.\n# network-multitool ships nginx but its entrypoint does not start it (cmd=bash).\nmkdir -p /srv/www /run/nginx\necho "<h1>$(hostname)</h1><p>$(ip -4 -o addr show eth1 | tr -s \' \' | cut -d\' \' -f4)</p>" > /srv/www/index.html\nif command -v nginx >/dev/null; then\n  printf \'events{}\\nhttp{server{listen 80;root /srv/www;}}\\n\' > /srv/nginx.conf\n  nginx -c /srv/nginx.conf && echo "web: nginx"\nelif busybox --list 2>/dev/null | grep -qx httpd; then\n  httpd -p 80 -h /srv/www && echo "web: busybox-httpd"\nelse\n  echo "web: NO SERVER AVAILABLE" >&2; exit 1\nfi\n'


def build(ROOT, NAME='isp-lab', force=False):
    if os.path.isdir(ROOT) and os.listdir(ROOT) and not force:
        raise SystemExit(f"{ROOT} exists and is not empty - choose a new folder")
    os.makedirs(ROOT+'/configs/common', exist_ok=True); os.makedirs(ROOT+'/configs/dns', exist_ok=True)
    IMG='quay.io/frrouting/frr:10.0.0'
    ISPS={1:dict(asn=65001,net='81.10',inet_links={1:('1.1.255.1','1.1.255.2'),2:('1.1.255.5','1.1.255.6')}),
          2:dict(asn=65002,net='92.20',inet_links={1:('1.1.255.9','1.1.255.10'),2:('1.1.255.13','1.1.255.14')})}
    TOWNS=['a','b','c']
    nodes={}; links=[]; dns=[]
    def w(path, txt):
        p=os.path.join(ROOT,'configs',path); os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'w').write(txt.lstrip('\n'))
    def frrnode(name, extra_exec):
        nodes[name]={'kind':'linux','image':IMG,
          'binds':[f'configs/{name}/frr.conf:/etc/frr/frr.conf','configs/common/daemons:/etc/frr/daemons','configs/common/vtysh.conf:/etc/frr/vtysh.conf'],
          'exec':['ip route del default','sysctl -w net.ipv4.ip_forward=1']+extra_exec}
    def host(name, ip, gw, img='ghcr.io/hellt/network-multitool', resolv=True):
        # every host serves a page naming itself (multitool's entrypoint does not start nginx)
        nodes[name]={'kind':'linux','image':img,'binds':['configs/common/webhost.sh:/usr/local/bin/netlab-web:ro'],
          'exec':[f'ip addr add {ip} dev eth1',f'ip route replace default via {gw} dev eth1']+(['sh -c "echo nameserver 1.1.1.1 > /etc/resolv.conf"'] if resolv else [])+['sh /usr/local/bin/netlab-web']}
    def mpls_exec(ifs):
        return ['sysctl -w net.mpls.platform_labels=1048575']+[f'sysctl -w net.mpls.conf.{i}.input=1' for i in ifs]
    VRF_SYS=['sysctl -w net.ipv4.tcp_l3mdev_accept=1','sysctl -w net.ipv4.udp_l3mdev_accept=1']
    def vrf_exec(vrf, table, ifname, addr):
        return VRF_SYS+[f'ip link add {vrf} type vrf table {table}',f'ip link set {vrf} up',f'ip link set {ifname} master {vrf}',f'ip addr add {addr} dev {ifname}']

    # ---------------- INET ----------------
    inet_nbrs=[]
    for n,isp in ISPS.items():
        for c,(a,b) in isp['inet_links'].items():
            inet_nbrs.append((n,c,a,b,isp['asn']))
    ifc=''
    for i,(n,c,a,b,asn) in enumerate(inet_nbrs, start=1):
        ifc+=f'!\ninterface eth{i}\n description TRANSIT ISP{n}-CORE{c} (AS{asn})\n ip address {a}/30\nexit\n'
        links.append([f'inet-r1:eth{i}', f'isp{n}-core{c}:eth1'])
    ifc+='!\ninterface eth5\n description PUBLIC SERVICES 1.1.1.0/28\n ip address 1.1.1.14/28\nexit\n'
    links.append(['inet-r1:eth5','br-inet:bi-inet-r1'])
    nb=''; af=''
    for (n,c,a,b,asn) in inet_nbrs:
        nb+=f' neighbor {b} remote-as {asn}\n neighbor {b} description ISP{n}-CORE{c}\n'
        af+=f'  neighbor {b} activate\n  neighbor {b} default-originate\n  neighbor {b} route-map RM-ISP{n}-IN in\n  neighbor {b} route-map RM-INET-OUT out\n'
    w('inet-r1/frr.conf', f'''
frr defaults traditional
hostname inet-r1
{ifc}!
ip prefix-list PL-INET-OUT seq 10 permit 1.1.1.0/28
ip prefix-list PL-INET-OUT seq 20 permit 81.10.0.0/16
ip prefix-list PL-INET-OUT seq 30 permit 92.20.0.0/16
ip prefix-list PL-ISP1-IN seq 10 permit 81.10.0.0/16
ip prefix-list PL-ISP2-IN seq 10 permit 92.20.0.0/16
!
route-map RM-ISP1-IN permit 10
 match ip address prefix-list PL-ISP1-IN
exit
!
route-map RM-ISP2-IN permit 10
 match ip address prefix-list PL-ISP2-IN
exit
!
route-map RM-INET-OUT permit 10
 match ip address prefix-list PL-INET-OUT
exit
!
router bgp 65000
 bgp router-id 1.1.1.14
{nb} !
 address-family ipv4 unicast
  network 1.1.1.0/28
{af} exit-address-family
exit
!
''')
    frrnode('inet-r1', [])
    nodes['br-inet']={'kind':'bridge'}
    for nm,ip in [('dns1','1.1.1.1'),('dns2','1.1.1.2')]:
        nodes[nm]={'kind':'linux','image':'alpine:3.20','cmd':'tail -f /dev/null',
          'binds':['configs/dns/dnsmasq.conf:/etc/dnsmasq.conf'],
          'exec':['apk add --no-cache dnsmasq iproute2',f'ip addr add {ip}/28 dev eth1','ip route replace default via 1.1.1.14 dev eth1','dnsmasq']}
        links.append([f'{nm}:eth1', f'br-inet:bi-{nm}'])
    host('web1','1.1.1.3/28','1.1.1.14',resolv=False); links.append(['web1:eth1','br-inet:bi-web1'])

    # ---------------- ISPs ----------------
    for n,isp in ISPS.items():
        asn=isp['asn']; x=isp['net']
        lo={1:f'{x}.254.1',2:f'{x}.254.2'}; pelo={t:f'{x}.254.{11+i}' for i,t in enumerate(TOWNS)}
        # p2p allocation from x.255.0/24
        core_link=(f'{x}.255.1',f'{x}.255.2')
        cp={}  # (core,town)->(core_ip,pe_ip)
        base=4
        for c in (1,2):
            for t in TOWNS:
                cp[(c,t)]=(f'{x}.255.{base+1}',f'{x}.255.{base+2}'); base+=4
        cust_rts=' '.join(f'{asn}:{1001+i}' for i in range(3))
        links.append([f'isp{n}-core1:eth2',f'isp{n}-core2:eth2'])
        for c in (1,2):
            name=f'isp{n}-core{c}'; other=2 if c==1 else 1
            a,b=isp['inet_links'][c]
            ifs=f'!\ninterface lo\n ip address {lo[c]}/32\nexit\n!\ninterface eth1\n description TRANSIT INET-R1 (AS65000) - VRF INTERNET\nexit\n'
            ifs+=f'!\ninterface eth2\n description CORE LINK ISP{n}-CORE{other}\n ip address {core_link[c-1]}/30\n ip ospf network point-to-point\nexit\n'
            ospf_nets=f' network {lo[c]}/32 area 0\n network {x}.255.0/30 area 0\n'
            ldp_ifs=['eth2']
            for i,t in enumerate(TOWNS):
                e=f'eth{3+i}'; cip,pip=cp[(c,t)]
                ifs+=f'!\ninterface {e}\n description PE ISP{n}-PE-{t.upper()}\n ip address {cip}/30\n ip ospf network point-to-point\nexit\n'
                ospf_nets+=f' network {cip}/30 area 0\n'.replace(cip, cip.rsplit('.',1)[0]+'.'+str(int(cip.rsplit('.',1)[1])-1))
                ldp_ifs.append(e)
                if c==1: pass
                links.append([f'{name}:{e}', f'isp{n}-pe-{t}:eth{c}'])
            ldp=''.join(f'  !\n  interface {i}\n  exit\n' for i in ldp_ifs)
            pe_nbrs=''.join(f' neighbor {pelo[t]} peer-group PE\n' for t in TOWNS)
            w(f'{name}/frr.conf', f'''
frr defaults traditional
hostname {name}
{ifs}!
vrf INTERNET
 ip route {x}.0.0/16 blackhole
exit-vrf
!
ip prefix-list PL-OWN seq 10 permit {x}.0.0/16 le 32
ip prefix-list PL-AGG seq 10 permit {x}.0.0/16
!
route-map RM-INET-IN deny 10
 match ip address prefix-list PL-OWN
exit
route-map RM-INET-IN permit 20
exit
!
route-map RM-INET-OUT permit 10
 match ip address prefix-list PL-AGG
exit
!
router ospf
 ospf router-id {lo[c]}
 passive-interface lo
{ospf_nets}exit
!
mpls ldp
 router-id {lo[c]}
 !
 address-family ipv4
  discovery transport-address {lo[c]}
{ldp} exit-address-family
exit
!
router bgp {asn}
 bgp router-id {lo[c]}
 bgp cluster-id {x}.254.254
 no bgp default ipv4-unicast
 neighbor PE peer-group
 neighbor PE remote-as {asn}
 neighbor PE update-source lo
{pe_nbrs} neighbor {lo[other]} remote-as {asn}
 neighbor {lo[other]} description RR PEER ISP{n}-CORE{other}
 neighbor {lo[other]} update-source lo
 !
 address-family ipv4 vpn
  neighbor PE activate
  neighbor PE route-reflector-client
  neighbor {lo[other]} activate
 exit-address-family
exit
!
router bgp {asn} vrf INTERNET
 bgp router-id {lo[c]}
 neighbor {a} remote-as 65000
 neighbor {a} description INET-R1
 !
 address-family ipv4 unicast
  network {x}.0.0/16
  neighbor {a} activate
  neighbor {a} route-map RM-INET-IN in
  neighbor {a} route-map RM-INET-OUT out
  label vpn export auto
  rd vpn export {lo[c]}:999
  rt vpn import {cust_rts}
  rt vpn export {asn}:999
  export vpn
  import vpn
 exit-address-family
exit
!
''')
            frrnode(name, mpls_exec(ldp_ifs)+vrf_exec('INTERNET',999,'eth1',f'{b}/30'))
        for i,t in enumerate(TOWNS):
            name=f'isp{n}-pe-{t}'; vrf=f'C{n}{t.upper()}'; rt=f'{asn}:{1001+i}'; cas=asn+100*n-1+ (100 if n==1 else 0)
            cas={1:65101,2:65201}[n]+i
            pe_ip=f'{x}.{i+1}.1'; ce_ip=f'{x}.{i+1}.2'; blk=f'{x}.100.{8*i}'; srv=f'{x}.100.{8*i+2}'; gw=f'{x}.100.{8*i+1}'
            (c1ip,p1),(c2ip,p2)=cp[(1,t)],cp[(2,t)]
            net1=p1.rsplit('.',1)[0]+'.'+str(int(p1.rsplit('.',1)[1])-2); net2=p2.rsplit('.',1)[0]+'.'+str(int(p2.rsplit('.',1)[1])-2)
            w(f'{name}/frr.conf', f'''
frr defaults traditional
hostname {name}
!
interface lo
 ip address {pelo[t]}/32
exit
!
interface eth1
 description UPLINK ISP{n}-CORE1
 ip address {p1}/30
 ip ospf network point-to-point
exit
!
interface eth2
 description UPLINK ISP{n}-CORE2
 ip address {p2}/30
 ip ospf network point-to-point
exit
!
interface eth3
 description CUSTOMER {vrf} (AS{cas}) - VRF {vrf}
exit
!
ip prefix-list PL-DEFAULT seq 10 permit 0.0.0.0/0
ip prefix-list PL-{vrf}-IN seq 10 permit {blk}/29
!
route-map RM-{vrf}-IN permit 10
 match ip address prefix-list PL-{vrf}-IN
exit
!
route-map RM-{vrf}-OUT permit 10
 match ip address prefix-list PL-DEFAULT
exit
!
router ospf
 ospf router-id {pelo[t]}
 passive-interface lo
 network {pelo[t]}/32 area 0
 network {net1}/30 area 0
 network {net2}/30 area 0
exit
!
mpls ldp
 router-id {pelo[t]}
 !
 address-family ipv4
  discovery transport-address {pelo[t]}
  !
  interface eth1
  exit
  !
  interface eth2
  exit
 exit-address-family
exit
!
router bgp {asn}
 bgp router-id {pelo[t]}
 no bgp default ipv4-unicast
 neighbor RR peer-group
 neighbor RR remote-as {asn}
 neighbor RR update-source lo
 neighbor {lo[1]} peer-group RR
 neighbor {lo[1]} description RR ISP{n}-CORE1
 neighbor {lo[2]} peer-group RR
 neighbor {lo[2]} description RR ISP{n}-CORE2
 !
 address-family ipv4 vpn
  neighbor RR activate
 exit-address-family
exit
!
router bgp {asn} vrf {vrf}
 bgp router-id {pelo[t]}
 neighbor {ce_ip} remote-as {cas}
 neighbor {ce_ip} description CUSTOMER {vrf}
 !
 address-family ipv4 unicast
  neighbor {ce_ip} activate
  neighbor {ce_ip} route-map RM-{vrf}-IN in
  neighbor {ce_ip} route-map RM-{vrf}-OUT out
  label vpn export auto
  rd vpn export {pelo[t]}:{1001+i}
  rt vpn import {asn}:999
  rt vpn export {rt}
  export vpn
  import vpn
 exit-address-family
exit
!
''')
            frrnode(name, mpls_exec(['eth1','eth2'])+vrf_exec(vrf,1001+i,'eth3',f'{pe_ip}/30'))
            ce=f'c{n}{t}-r1'
            w(f'{ce}/frr.conf', f'''
frr defaults traditional
hostname {ce}
!
interface eth1
 description ISP-{n} WAN
 ip address {ce_ip}/30
exit
!
interface eth2
 description PUBLIC LAN {blk}/29
 ip address {gw}/29
exit
!
ip prefix-list PL-DEFAULT seq 10 permit 0.0.0.0/0
ip prefix-list PL-LAN seq 10 permit {blk}/29
!
route-map RM-ISP-IN permit 10
 match ip address prefix-list PL-DEFAULT
exit
!
route-map RM-ISP-OUT permit 10
 match ip address prefix-list PL-LAN
exit
!
router bgp {cas}
 bgp router-id {gw}
 neighbor {pe_ip} remote-as {asn}
 neighbor {pe_ip} description ISP-{n} PE-{t.upper()}
 !
 address-family ipv4 unicast
  network {blk}/29
  neighbor {pe_ip} activate
  neighbor {pe_ip} route-map RM-ISP-IN in
  neighbor {pe_ip} route-map RM-ISP-OUT out
 exit-address-family
exit
!
''')
            frrnode(ce, [])
            links.append([f'{name}:eth3', f'{ce}:eth1'])
            hn=f'c{n}{t}-web'; host(hn, f'{srv}/29', gw); links.append([f'{ce}:eth2', f'{hn}:eth1'])
            dns.append((f'c{n}{t}.isp{n}.lab', srv))

    # daemons/vtysh/dns
    w('common/daemons','''
# FRR daemons - phase 2 (MPLS L3VPN)
bgpd=yes
ospfd=yes
ldpd=yes
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
pim6d=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no

vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
mgmtd_options="  -A 127.0.0.1"
bgpd_options="   -A 127.0.0.1"
ospfd_options="  -A 127.0.0.1"
ldpd_options="   -A 127.0.0.1"
staticd_options="-A 127.0.0.1"
''')
    w('common/vtysh.conf','service integrated-vtysh-config\n')
    recs=[('dns1.inet.lab','1.1.1.1'),('dns2.inet.lab','1.1.1.2'),('www.inet.lab','1.1.1.3')]+dns
    w('dns/dnsmasq.conf','# Shared by dns1 (1.1.1.1) and dns2 (1.1.1.2): one file, records always match\nno-resolv\nno-hosts\ninterface=eth1\nbind-interfaces\nlog-queries\n# authoritative: unknown names NXDOMAIN, missing types (AAAA) NODATA.\n# Without it dnsmasq REFUSES AAAA and musl resolvers (Alpine hosts) fail the lookup.\nlocal=/#/\n'+''.join(f'host-record={a},{b}\n' for a,b in recs))
    open(ROOT+'/configs/common/webhost.sh','w').write(WEBHOST); os.chmod(ROOT+'/configs/common/webhost.sh', 0o755)

    topo={'name':NAME,'mgmt':{'network':'clab-mgmt','ipv4-subnet':'172.31.255.0/24'},
          'topology':{'nodes':nodes,'links':[{'endpoints':l} for l in links]}}
    class D(yaml.SafeDumper): pass
    D.ignore_aliases=lambda *a:True
    open(ROOT+'/isp-lab.clab.yml','w').write('# Phase 2: dual ISP, RR pair per ISP, MPLS L3VPN, INTERNET VRF hub-and-spoke\n'+yaml.dump(topo,Dumper=D,sort_keys=False,width=200))
    return len(nodes), len(links)



if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    n, l = build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'isp-lab')
    print(n, 'nodes', l, 'links')
