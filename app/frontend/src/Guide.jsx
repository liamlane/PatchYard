// Help: a how-to guide and a tour of every feature, searchable, opened from the toolbar (?) or the account menu.
import { useMemo, useState } from "react";
import { Modal, Icon } from "./ui.jsx";

const K = ({ children }) => <kbd>{children}</kbd>;
const B = ({ children }) => <b className="ui">{children}</b>;
const Steps = ({ children }) => <ol className="steps">{children}</ol>;
const Tip = ({ children }) => <div className="tip"><Icon name="flag" size={14} /><div>{children}</div></div>;
const Cmd = ({ children }) => <pre className="eq">{children}</pre>;

// id, group, title, icon, keywords (for search), body
const SECTIONS = [
  { id: "start", group: "Getting started", title: "Quick start", icon: "play", kw: "first lab template deploy begin tutorial",
    body: (go) => <>
      <p>Patchyard is a network lab in your browser. Every device on the canvas is a real Linux container: routers run FRR, switches are real 802.1Q bridges, servers run real DHCP, DNS and web services. What you configure is what actually happens on the wire.</p>
      <h4>Your first lab in five minutes</h4>
      <Steps>
        <li>Click <B>Lab manager</B> in the toolbar and open <B>Templates</B>. Pick <B>OSPF triangle</B> and give the lab a name.</li>
        <li>Click <B>Deploy</B>. The devices start; their icons turn green when they're running (about 10–30 seconds).</li>
        <li>Click a router to open its window. In the <B>CLI</B> tab, type <code>show ip ospf neighbor</code>, then <code>show ip route</code>.</li>
        <li>Click a cable between two routers and press <B>Down</B>. Watch OSPF reconverge with <code>show ip route</code>, and the alert appear in the bell.</li>
        <li>Press <B>Up</B>, then <B>Destroy</B> when you're done. Your configs are saved first, so the next deploy picks up where you left off.</li>
      </Steps>
      <p>Then build your own: <button className="link" onClick={() => go("howto-ospf")}>Two routers running OSPF from scratch</button>.</p>
      <h4>Find your way around</h4>
      <ul>
        <li><b>Left:</b> the device palette (routers, switches, firewalls, PCs, servers), cable tools and notes.</li>
        <li><b>Centre:</b> the canvas. Click a device to open its window; click a cable for link controls.</li>
        <li><b>Toolbar:</b> lab picker, Lab manager, Deploy/Destroy, undo/redo, view toggles, Realtime/Simulation, dashboard, notifications, settings and this help.</li>
        <li><b>Anywhere:</b> <K>Ctrl</K>+<K>K</K> finds any device or command.</li>
      </ul>
    </> },
  { id: "concepts", group: "Getting started", title: "How labs work", icon: "layers", kw: "design edit deploy container save config persist running state",
    body: () => <>
      <h4>Design, then deploy</h4>
      <p>A lab is a saved design: devices, cables, hardware and each device's settings. While a lab isn't deployed you can change anything. <B>Deploy</B> starts one container per device and wires the cables as virtual Ethernet links.</p>
      <h4>Changing a running lab</h4>
      <ul>
        <li>Devices and cables you add or remove while it's running start and connect straight away. You don't need to redeploy.</li>
        <li>Settings changed in a device's <B>Config</B> tab or CLI apply to the running device immediately.</li>
        <li><B>Redeploy</B> saves the running configs, then rebuilds every device from the design. Use it after big changes.</li>
      </ul>
      <h4>Saving</h4>
      <ul>
        <li>The design saves itself as you work ("Saved" in the toolbar).</li>
        <li>Router running configs are saved when you deploy, destroy, take a snapshot, press <B>Save running configs</B> (<K>Ctrl</K>+<K>S</K>) or type <code>write memory</code>.</li>
        <li><B>Snapshots</B> keep a copy of the whole lab you can diff against and restore.</li>
      </ul>
      <h4>Pausing</h4>
      <p><B>Pause network</B> freezes every device without stopping it, so you can inspect state at a moment in time. <B>Resume</B> carries on.</p>
    </> },

  { id: "canvas", group: "Building", title: "Canvas and editing", icon: "grid", kw: "add device drag drop cable connect select move align note area undo duplicate delete context menu snap grid",
    body: () => <>
      <h4>Add devices</h4>
      <p>Drag a device from the palette onto the canvas, click it to drop it in the middle, or press <K>1</K>–<K>5</K> (router, switch, firewall, PC, server). Devices are named r1, sw1, fw1, pc1, srv1… Right-click a device to duplicate or delete it, start a cable or traffic from it; rename it on its Config tab.</p>
      <h4>Connect them</h4>
      <Steps>
        <li>Pick a cable: <B>Copper</B> (<K>C</K>), <B>Fibre</B> (<K>F</K>) or <B>Serial</B> (<K>S</K>).</li>
        <li>Click the first device and choose a port from the list.</li>
        <li>Click the second device and choose its port. Press <K>V</K> or <K>Esc</K> to go back to selecting.</li>
      </Steps>
      <p>Ports only accept the right cable: copper into RJ45 ports, fibre into SFP ports with an optic, serial into serial ports. See <i>Hardware and cabling</i>.</p>
      <h4>Select, move, arrange</h4>
      <ul>
        <li>Click to select, <K>Ctrl</K>+click to add, <K>Shift</K>+drag to box-select, <K>Ctrl</K>+<K>A</K> for everything.</li>
        <li><K>Ctrl</K>+<K>D</K> duplicates, <K>Delete</K> removes, <K>Ctrl</K>+<K>Z</K>/<K>Ctrl</K>+<K>Y</K> undo and redo.</li>
        <li>With two or more devices selected, an align bar appears (align edges or centres, spread evenly). <K>G</K> snaps to the grid.</li>
      </ul>
      <h4>Notes and areas</h4>
      <p><K>N</K> adds a sticky note, <K>A</K> a coloured area (for example "VLAN 10" or "ISP-1"). Double-click to edit the text; drag the label to move an area.</p>
      <h4>View</h4>
      <p>Toolbar toggles: port labels, traffic overlay (<K>O</K>), snap (<K>G</K>), dark mode (<K>T</K>). Scroll to zoom, drag the background to pan.</p>
    </> },
  { id: "hardware", group: "Building", title: "Hardware and cabling", icon: "bolt", kw: "physical model module slot sfp optic transceiver serial t1 nic speed power rear panel",
    body: () => <>
      <p>Every device has a hardware model. Open a device and go to the <B>Physical</B> tab to see its rear panel.</p>
      <ul>
        <li><b>Routers</b> have module slots for GE copper, SFP, 10G SFP+ and serial T1 modules.</li>
        <li><b>Switches</b> have uplink slots for SFP and SFP+ modules.</li>
        <li><b>PCs, laptops and servers</b> have a swappable network card.</li>
      </ul>
      <Steps>
        <li>Power the device off (the power button on the Physical tab). Like real kit, modules are fitted with the power off (an admin can relax this in Settings).</li>
        <li>Drag a module from the library into an empty slot.</li>
        <li>Power it back on. The new ports appear with names like Gi0/1/0 or Se0/1/0.</li>
      </Steps>
      <h4>SFP ports</h4>
      <p>An SFP port needs a transceiver: a fibre optic for a fibre cable, or a copper (RJ45) SFP for a copper cable. Choose it in the port list when you cable it, or on the Physical tab.</p>
      <h4>Speeds</h4>
      <p>A link runs at the slower of its two ports (FastEthernet 100 Mb/s, Gigabit 1 Gb/s, 10G, serial T1 1.544 Mb/s). The traffic generator shows the difference.</p>
      <p>Each port on the rear panel has a light for its link state, and shows what it's cabled to.</p>
    </> },

  { id: "routers", group: "Devices", title: "Routers", icon: "pulse", kw: "frr ospf bgp mpls ldp vrf static route acl nat dhcp relay helper loopback subinterface config tab",
    body: (go) => <>
      <p>Routers run FRR 10. Configure them in the <B>Config</B> tab (forms) or the <B>CLI</B> tab (IOS commands). Both change the same running device, and the Config tab shows the IOS commands each change sends.</p>
      <h4>Config tab pages</h4>
      <ul>
        <li><b>Global:</b> hostname and settings.</li>
        <li><b>Interfaces:</b> one page per port, with address, description, shutdown, ACLs in/out, NAT inside/outside, DHCP helper and VRF. Add subinterfaces (802.1Q, for router-on-a-stick) and loopbacks here.</li>
        <li><b>Routing:</b> static routes, OSPF (areas, networks, passive interfaces, router ID), BGP (neighbours, networks, VPNv4) and MPLS/LDP.</li>
        <li><b>Security & NAT:</b> access lists (standard and extended, numbered or named, with hit counts), NAT (PAT, static NAT, port forwards, pools) and VRFs.</li>
      </ul>
      <h4>What's under the hood</h4>
      <p>Routing is FRR. ACLs and NAT are nftables rules inside the router, in IOS order (inbound ACL before NAT). DHCP relay (<code>ip helper-address</code>) is a relay agent on the router's interface. VRFs are Linux VRF devices.</p>
      <p>CLI details: <button className="link" onClick={() => go("cli")}>IOS-style CLI</button>.</p>
    </> },
  { id: "switches", group: "Devices", title: "Switches", icon: "grid", kw: "vlan access trunk native stp spanning tree rstp lacp port-channel etherchannel svi management ovs open vswitch engine",
    body: () => <>
      <p>Switches are real 802.1Q bridges. Configure them in the Config tab or with IOS commands in the CLI tab.</p>
      <ul>
        <li><b>VLAN database:</b> create and name VLANs.</li>
        <li><b>Interfaces:</b> access (one VLAN) or trunk (allowed VLANs and native VLAN), shutdown, PortFast.</li>
        <li><b>Spanning tree:</b> bridge priority (the lowest becomes root) and per-port cost.</li>
        <li><b>Management (SVI):</b> an IP address and gateway so you can ping the switch.</li>
        <li><b>Port-channels:</b> bundle ports with LACP (Open vSwitch engine only).</li>
      </ul>
      <h4>Two engines</h4>
      <p><b>Linux bridge</b> (default) runs classic 802.1D STP, which takes about 30 seconds to converge. <b>Open vSwitch</b> adds RSTP (a couple of seconds) and LACP. Pick it with <b>Switch engine</b> on the switch's Config tab while the switch is off or the lab isn't deployed.</p>
    </> },
  { id: "firewalls", group: "Devices", title: "Firewalls", icon: "flag", kw: "zone security level rule allow deny nat pat port forward dmz nftables hit counter",
    body: () => <>
      <p>Firewalls work like an ASA: each interface belongs to a <b>zone</b> with a <b>security level</b> (0–100). With the default policy, traffic may go from a higher level to a lower one and replies come back; anything else needs a rule. Switch the policy to deny-all if you want every flow to need a rule.</p>
      <ul>
        <li><b>Zones:</b> name, level and interfaces (for example inside 100, dmz 50, outside 0).</li>
        <li><b>Rules:</b> ordered, stateful: from zone, to zone, protocol, source, destination, ports, allow or deny. Each rule has a hit counter.</li>
        <li><b>NAT:</b> PAT (hide inside behind the outside address) and port forwards (publish a DMZ server).</li>
        <li><b>Status:</b> the live ruleset and counters.</li>
      </ul>
      <p>It's nftables on Alpine underneath. The CLI tab is a shell, so <code>nft list ruleset</code> shows exactly what's applied.</p>
    </> },
  { id: "servers", group: "Devices", title: "Servers and services", icon: "window", kw: "dhcp dns http web ftp tftp syslog ntp pool reservation lease service on off",
    body: () => <>
      <p>A server's <B>Services</B> tab has a page per service, each with an On/Off switch and a running light.</p>
      <ul>
        <li><b>DHCP:</b> a pool per subnet (range, gateway, DNS, lease time), reservations by MAC, and live leases. Serve other subnets through a router's <code>ip helper-address</code>.</li>
        <li><b>DNS:</b> A and CNAME records. Clients using this server can resolve your lab names.</li>
        <li><b>HTTP:</b> a web server; edit its page (with a live preview). Browse to it from a PC's Web Browser.</li>
        <li><b>FTP:</b> users and passwords, and the files it serves (upload and download them here).</li>
        <li><b>TFTP:</b> receives router and switch backups (<code>copy running-config tftp:</code>).</li>
        <li><b>Syslog:</b> collects logs from devices with <code>logging host</code>.</li>
        <li><b>NTP:</b> a time source for the lab.</li>
      </ul>
      <p>Servers also have a <B>Desktop</B>, like PCs.</p>
    </> },
  { id: "pcs", group: "Devices", title: "PCs and laptops", icon: "window", kw: "desktop ip configuration dhcp static gateway command prompt ping tracert browser",
    body: () => <>
      <p>A PC's <B>Desktop</B> tab has three apps:</p>
      <ul>
        <li><b>IP Configuration:</b> DHCP or static (address, mask, gateway, DNS).</li>
        <li><b>Command Prompt:</b> <code>ping</code>, <code>tracert</code>, <code>ipconfig</code> (with <code>/release</code> and <code>/renew</code>), <code>nslookup</code>, <code>curl</code> and the usual Linux tools.</li>
        <li><b>Web Browser:</b> open pages on lab web servers by name or address.</li>
      </ul>
      <p>The Command Prompt and browser need the lab deployed.</p>
    </> },
  { id: "cli", group: "Devices", title: "IOS-style CLI", icon: "terminal", kw: "ios command conf t interface show run write memory tftp abbreviation tab completion help question mark vtysh shell enable",
    body: () => <>
      <p>Routers and switches open in <b>IOS</b> mode: Cisco-style commands translated to FRR and the switch's settings. Routers also have <b>FRR</b> (FRR's own <code>vtysh</code>) and <b>Shell</b>; firewalls have a shell. The mode switch is above the terminal.</p>
      <ul>
        <li><K>?</K> lists what can come next; <K>Tab</K> completes; abbreviations work (<code>conf t</code>, <code>int g0/0/0</code>, <code>sh ip int br</code>).</li>
        <li><K>↑</K>/<K>↓</K> history, <K>Ctrl</K>+<K>Z</K> back to exec mode, <K>Ctrl</K>+<K>C</K> cancels a ping, <code>do</code> runs exec commands from config mode.</li>
        <li>Errors look like IOS: <code>% Invalid input detected</code>.</li>
      </ul>
      <h4>Configuration</h4>
      <Cmd>{`enable / configure terminal / hostname / enable secret / banner motd
interface … / ip address / no shutdown / description / interface range (switches)
interface g0/0/0.10 + encapsulation dot1Q 10      (router-on-a-stick)
ip route / router ospf / network … area / passive-interface / router-id
router bgp / neighbor … remote-as / address-family / network
mpls ip / mpls ldp router-id
ip access-list standard|extended / access-list 101 … / ip access-group … in|out
ip nat inside|outside / ip nat inside source list … interface … overload
ip nat inside source static … / ip nat pool
ip helper-address / vrf definition / vrf forwarding
vlan / name / switchport mode access|trunk / switchport access vlan
switchport trunk allowed vlan / native vlan / spanning-tree … / channel-group
logging host`}</Cmd>
      <h4>Show and tools</h4>
      <Cmd>{`show running-config / startup-config / version / inventory / clock
show ip interface brief / interfaces / interfaces status / interfaces trunk
show ip route / ip protocols / ip ospf neighbor / ip bgp summary
show mpls ldp neighbor / mpls forwarding-table / vrf
show access-lists / ip nat translations / ip nat statistics
show vlan brief / spanning-tree / etherchannel summary / mac address-table
show arp / cdp neighbors
ping / traceroute / write memory / copy running-config tftp: / reload`}</Cmd>
      <Tip>Commands Patchyard doesn't model (like <code>service</code>, <code>ntp</code> or <code>ip ssh</code>) are accepted and kept in the config, so pasted configs don't fail half-way.</Tip>
    </> },

  { id: "deploy", group: "Running labs", title: "Deploy, destroy, snapshots", icon: "play", kw: "deploy redeploy destroy save running configs pause snapshot restore power",
    body: () => <>
      <ul>
        <li><B>Deploy</B> starts the lab. <B>Redeploy</B> saves configs and rebuilds it.</li>
        <li><B>Destroy</B> saves configs and stops every device. The design stays.</li>
        <li><B>Save running configs</B> (<K>Ctrl</K>+<K>S</K>) writes every device's running config into the lab.</li>
        <li><B>Take snapshot</B> saves the whole lab. In Lab manager → Snapshots you can diff two snapshots (or a snapshot against now) and restore one.</li>
        <li><b>Power</b>: right-click a device or use its Physical tab to switch it off and on, like pulling the plug. Its cables reconnect when it's back.</li>
      </ul>
      <Tip>On a shared server you may be limited to one running lab at a time. Destroy one before deploying another.</Tip>
    </> },
  { id: "monitor", group: "Running labs", title: "Monitoring and dashboard", icon: "dash", kw: "dashboard throughput drops errors cpu memory history graph overlay routing table neighbours protocols monitor tab",
    body: () => <>
      <ul>
        <li><B>Dashboard</B> (<K>M</K>): busiest links, CPU by device, recent activity and a reachability matrix (who can ping whom).</li>
        <li><b>Monitor tab</b> on each device: throughput, drops and errors per port, with 48 hours of history.</li>
        <li><b>Traffic overlay</b> (<K>O</K>): cables thicken and colour with live load.</li>
        <li><b>Protocols:</b> routing table, OSPF neighbours, BGP and LDP sessions, read live from the router.</li>
      </ul>
    </> },
  { id: "alerts", group: "Running labs", title: "Alerts and notifications", icon: "bell", kw: "alert rule notification bell toast severity hold time ping probe threshold acknowledge",
    body: () => <>
      <p>Patchyard watches your running labs and raises alerts in the bell (top right), with a toast and a marker on the device.</p>
      <ul>
        <li><b>Built-in rules:</b> device stopped, link down, BGP down, OSPF neighbour lost, LDP down, high CPU, interface errors rising.</li>
        <li><b>Hold time:</b> how long a condition must last before it fires, so short blips stay quiet.</li>
        <li><b>Ping probes:</b> "can PC1 reach SRV1?" checked continuously. Good for failover tests.</li>
        <li>Alerts resolve on their own when the condition clears. Acknowledge them to silence the badge.</li>
      </ul>
      <p>Admins edit the rules and toast settings in Settings → Alert rules and Notifications.</p>
    </> },
  { id: "faults", group: "Running labs", title: "Breaking things on purpose", icon: "zap", kw: "link down flap impair netem delay jitter loss rate corrupt duplicate preset failover",
    body: () => <>
      <p>Click a cable to open its link panel.</p>
      <ul>
        <li><b>Up / Down:</b> takes both ends administratively down, and keeps it that way across redeploys.</li>
        <li><b>Flap:</b> down, wait the number of seconds you set, then up. Ideal for timing failover.</li>
        <li><b>Impairments:</b> delay, jitter, loss, rate limit, duplication and corruption, per link.</li>
        <li><b>Presets:</b> home broadband, 4G, 3G, satellite (GEO and LEO), lossy Wi-Fi, congested WAN, intercontinental, dying link.</li>
      </ul>
    </> },
  { id: "traffic", group: "Running labs", title: "Traffic tests", icon: "flow", kw: "iperf iperf3 traffic generator bandwidth throughput udp tcp reachability matrix",
    body: () => <>
      <p>The <B>Traffic generator</B> (toolbar) runs iperf3 between any two running devices: TCP or UDP, a rate, duration, parallel streams, and reverse direction. Results show throughput, retransmits, loss and jitter.</p>
      <p>Pair it with an impaired link or a serial WAN to see the effect, and watch the traffic overlay while it runs.</p>
      <p>The dashboard's <b>reachability matrix</b> pings every host from every other host in one go.</p>
    </> },
  { id: "sim", group: "Running labs", title: "Simulation mode", icon: "sim", kw: "simulation packet capture pdu step play event list osi layers",
    body: () => <>
      <p>Switch the toolbar from <B>Realtime</B> to <B>Simulation</B> to watch real packets hop by hop.</p>
      <Steps>
        <li>Press <B>Start capture</B>, or use <B>Add Simple PDU</B> to send a ping from one device to another.</li>
        <li>The event list fills with every packet seen on every cable.</li>
        <li>Press <B>Play</B> to animate them on the canvas, or step through one at a time. Filter by protocol (ARP, ICMP, OSPF, DHCP…).</li>
        <li>Click an event to inspect its layers, like a packet analyser.</li>
      </Steps>
    </> },

  { id: "labs", group: "Labs", title: "Lab manager and templates", icon: "layers", kw: "lab manager template clone export import zip snapshot new rename delete share",
    body: () => <>
      <ul>
        <li><b>Labs:</b> your labs. Open or delete them.</li>
        <li><b>Templates:</b> twelve ready-made labs with working configs and notes: OSPF triangle, OSPF multi-area, BGP between three ASes, serial WAN hub-and-spoke, router-on-a-stick, campus with STP, campus RSTP + LACP, NAT and ACLs at the edge, central DHCP with relay, small office, firewall edge with DMZ, and dual-ISP MPLS L3VPN.</li>
        <li><b>Snapshots:</b> save, diff and restore.</li>
        <li><b>Clone · export · import:</b> copy a lab, download it as a zip, or import a zip someone sent you.</li>
      </ul>
    </> },

  { id: "howto-ospf", group: "How-to", title: "Two routers running OSPF", icon: "pulse", kw: "how to tutorial ospf from scratch build",
    body: () => <>
      <Steps>
        <li><B>New lab</B>, then add two routers (<K>1</K> twice) and two PCs (<K>4</K> twice).</li>
        <li>Copper cables: r1 Gi0/0/0 ↔ r2 Gi0/0/0, r1 Gi0/0/1 ↔ pc1 Fa0, r2 Gi0/0/1 ↔ pc2 Fa0.</li>
        <li><B>Deploy</B>.</li>
        <li>r1's CLI:
          <Cmd>{`conf t
int g0/0/0
 ip address 10.0.12.1 255.255.255.252
 no shut
int g0/0/1
 ip address 192.168.1.1 255.255.255.0
 no shut
router ospf 1
 network 10.0.12.0 0.0.0.3 area 0
 network 192.168.1.0 0.0.0.255 area 0
end
wr`}</Cmd></li>
        <li>r2: the same with 10.0.12.2 and 192.168.2.1/24 (and its own networks in OSPF).</li>
        <li>pc1 → Desktop → IP Configuration: static 192.168.1.10/24, gateway 192.168.1.1. pc2: 192.168.2.10/24, gateway 192.168.2.1.</li>
        <li>On r1: <code>show ip ospf neighbor</code> shows r2 in FULL. From pc1's Command Prompt: <code>ping 192.168.2.10</code>.</li>
      </Steps>
    </> },
  { id: "howto-vlan", group: "How-to", title: "VLANs and router-on-a-stick", icon: "grid", kw: "how to tutorial vlan trunk subinterface dot1q inter-vlan routing",
    body: () => <>
      <Steps>
        <li>Router r1, switch sw1, PCs pc1 and pc2. Cables: r1 Gi0/0/0 ↔ sw1 Gi1/0/24, pc1 Fa0 ↔ sw1 Gi1/0/1, pc2 Fa0 ↔ sw1 Gi1/0/2. Deploy.</li>
        <li>sw1:
          <Cmd>{`conf t
vlan 10
 name USERS
vlan 20
 name SERVERS
int g1/0/1
 switchport mode access
 switchport access vlan 10
int g1/0/2
 switchport mode access
 switchport access vlan 20
int g1/0/24
 switchport mode trunk
end`}</Cmd></li>
        <li>r1:
          <Cmd>{`conf t
int g0/0/0
 no shut
int g0/0/0.10
 encapsulation dot1Q 10
 ip address 192.168.10.1 255.255.255.0
int g0/0/0.20
 encapsulation dot1Q 20
 ip address 192.168.20.1 255.255.255.0
end`}</Cmd></li>
        <li>pc1: 192.168.10.10/24, gateway 192.168.10.1; pc2: 192.168.20.10/24, gateway 192.168.20.1. Ping between them, and check <code>show interfaces trunk</code> on sw1.</li>
      </Steps>
      <Tip>The Router-on-a-stick template has this ready-made, if you'd rather start from a working lab and break it.</Tip>
    </> },
  { id: "howto-nat", group: "How-to", title: "PAT to an outside network", icon: "flag", kw: "how to tutorial nat pat overload acl inside outside internet",
    body: () => <>
      <Steps>
        <li>Build pc1 — r1 — r2 and deploy. r1 Gi0/0/1 (inside) 192.168.1.1/24, r1 Gi0/0/0 (outside) 203.0.113.1/30, r2 Gi0/0/0 203.0.113.2/30. r2 plays the internet: give it <code>interface loopback0</code> with 8.8.8.8/32. pc1: 192.168.1.10/24, gateway 192.168.1.1.</li>
        <li>r1:
          <Cmd>{`conf t
ip route 0.0.0.0 0.0.0.0 203.0.113.2
access-list 1 permit 192.168.1.0 0.0.0.255
int g0/0/1
 ip nat inside
int g0/0/0
 ip nat outside
ip nat inside source list 1 interface g0/0/0 overload
end`}</Cmd></li>
        <li>pc1 pings 8.8.8.8. r2 has no route to 192.168.1.0, yet the ping works: <code>show ip nat translations</code> on r1 shows why.</li>
      </Steps>
    </> },

  { id: "account", group: "Your account", title: "Accounts, privacy and limits", icon: "keyboard", kw: "account password name email private labs limit quota sandbox internet package request invite sign out",
    when: (p) => p.accounts,
    body: () => <>
      <ul>
        <li><b>Your labs are private.</b> Other users can't see or open them. Admins can, to help or tidy up.</li>
        <li><b>Account and password:</b> your name (top right) → Account and password. Changing your password signs out your other sessions.</li>
        <li><b>Limits:</b> the server may limit how many labs you keep, how many run at once, devices per lab, and memory/CPU per device. You'll get a clear message when you hit one; ask the admin if you need more.</li>
        <li><b>Lab internet:</b> on a shared server, lab devices can't reach the internet, except the package mirrors servers and firewalls need when they start. Lab-to-lab traffic inside your lab is unaffected.</li>
      </ul>
    </> },
  { id: "admin", group: "Your account", title: "Running the server (admins)", icon: "gear", kw: "admin users requests approve invite link role disable reset password quota trusted sandbox mirrors backup update",
    when: (p) => p.me?.role === "admin",
    body: () => <>
      <p>Your name (top right) → <B>Admin</B>. A red dot on your avatar means account requests are waiting.</p>
      <ul>
        <li><b>Users:</b> role, name and email, limits, lab internet, reset password (shown once), disable, delete.</li>
        <li><b>Requests:</b> people who asked from the sign-in page. Approve creates the account with the username and password they chose; decline tells them when they next try to sign in. Turn requests off under Limits & sandbox.</li>
        <li><b>Invites:</b> one-time links that create an account directly, with a role and an expiry.</li>
        <li><b>All labs:</b> every lab and its owner. Open, destroy, move to another user, or mark a lab trusted (runs privileged, outside the sandbox: only for your own labs).</li>
        <li><b>Limits & sandbox:</b> default limits, allowed images, package mirrors, the management address pool.</li>
      </ul>
      <h4>On the host</h4>
      <Cmd>{`sudo /opt/patchyard/deploy/update.sh                              # update
sudo docker logs -f patchyard                                     # app log
sudo docker exec patchyard python -m app.auth list                # accounts
sudo docker exec patchyard python -m app.auth reset-password NAME # new password
sudo nft list table ip patchyard                                  # lab egress firewall`}</Cmd>
      <p>Back up <code>/opt/patchyard</code>: it holds accounts, settings, every lab and the certificates.</p>
    </> },

  { id: "troubleshooting", group: "Reference", title: "Troubleshooting", icon: "help", kw: "problem not working fix dhcp lease ospf neighbour ping fails device won't start package install limit not found",
    body: () => <>
      <dl className="faq">
        <dt>A device stays grey or red after deploy</dt>
        <dd>Check the deploy message and the log (bottom bar). First deploys download images, which can take a minute. On a shared server, check you haven't hit your running-lab or device limit.</dd>
        <dt>A PC gets no DHCP lease</dt>
        <dd>Is the server's DHCP switched on with a pool for that subnet? On another subnet, does the router interface facing the PC have <code>ip helper-address</code> pointing at the server? Is the switch port in the right VLAN?</dd>
        <dt>OSPF neighbours don't come up</dt>
        <dd>Both ends need addresses in the same subnet, <code>no shutdown</code>, the same area, and a <code>network</code> statement covering the interface. Passive interfaces never form neighbours.</dd>
        <dt>Ping works one way only</dt>
        <dd>The far side needs a route back: a default gateway on PCs, and routes (or OSPF) on routers. ACLs and firewall rules are the next suspects; their hit counters show what matched.</dd>
        <dt>A switch port takes 30 seconds to pass traffic</dt>
        <dd>That's classic STP listening and learning. Use PortFast on edge ports, or the Open vSwitch engine for RSTP.</dd>
        <dt>A service on a server won't start</dt>
        <dd>Servers install some packages the first time they start, so give them a minute. If a service still won't start, tell the admin: on a shared server the package mirror may be unreachable.</dd>
        <dt>"Lab not found" for a lab I can see in a link</dt>
        <dd>It belongs to someone else. Labs are private; ask them to export it and send you the zip.</dd>
      </dl>
    </> },
  { id: "shortcuts", group: "Reference", title: "Keyboard shortcuts", icon: "keyboard", kw: "keyboard shortcuts keys hotkeys",
    body: (_go, p) => <table className="grid keys"><tbody>{p.keys.map(([k, d]) => <tr key={k}><td>{k.split(" ").map((x, i) => (x.length <= 2 || x.includes("+") ? <kbd key={i}>{x}</kbd> : <span key={i}> {x} </span>))}</td><td>{d}</td></tr>)}</tbody></table> },
];

export default function Guide({ section = "start", onSection, onClose, keys = [], me, accounts }) {
  const [q, setQ] = useState("");
  const props = { keys, me, accounts };
  const visible = useMemo(() => SECTIONS.filter((s) => !s.when || s.when(props)), [me, accounts]); // eslint-disable-line
  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return visible;
    return visible.filter((s) => t.split(/\s+/).every((w) => `${s.title} ${s.group} ${s.kw}`.toLowerCase().includes(w)));
  }, [q, visible]);
  const cur = visible.find((s) => s.id === section) || visible[0];
  const groups = [...new Set(hits.map((s) => s.group))];
  const idx = visible.indexOf(cur);
  const go = (id) => { onSection(id); setQ(""); document.querySelector(".guide-body")?.scrollTo(0, 0); };
  return (
    <Modal title="Help & how-to guide" onClose={onClose} xwide>
      <div className="settings guide">
        <nav className="set-nav">
          <input className="guide-search" placeholder="Search help…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search help" />
          {groups.map((g) => <div key={g}>
            <div className="guide-group">{g}</div>
            {hits.filter((s) => s.group === g).map((s) => <button key={s.id} className={cur.id === s.id ? "on" : ""} onClick={() => go(s.id)}><Icon name={s.icon} size={15} />{s.title}</button>)}
          </div>)}
          {!hits.length && <p className="hint">Nothing matches "{q}".</p>}
        </nav>
        <div className="set-body guide-body">
          <h3>{cur.title}</h3>
          {cur.body(go, props)}
          <div className="row guide-nav">
            {idx > 0 && <button onClick={() => go(visible[idx - 1].id)}>← {visible[idx - 1].title}</button>}
            <span className="grow" />
            {idx < visible.length - 1 && <button onClick={() => go(visible[idx + 1].id)}>{visible[idx + 1].title} →</button>}
          </div>
        </div>
      </div>
    </Modal>
  );
}
