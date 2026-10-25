import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import Terminal from "./Terminal.jsx";
import { ICONS, TYPE_LABEL } from "./icons.jsx";
import MonitorTab from "./Monitor.jsx";

const TABS = {
  router: ["Physical", "Config", "CLI", "Monitor"],
  switch: ["Physical", "Config", "CLI", "Monitor"],
  pc: ["Physical", "Config", "Desktop", "Monitor"],
  server: ["Physical", "Config", "Services", "Desktop", "Monitor"],
};

function maskToPrefix(m) {
  if (/^\d+$/.test(m)) return Number(m);
  return m.split(".").reduce((a, o) => a + (Number(o) >>> 0).toString(2).split("1").length - 1, 0);
}
function prefixToMask(p) {
  const n = p === 0 ? 0 : (~0 << (32 - p)) >>> 0;
  return [24, 16, 8, 0].map((s) => (n >>> s) & 255).join(".");
}

export default function DeviceWindow({ lab, node, state, z, onFocus, onClose, onUpdateNode, onRename, notify, initialPos, initialTab, tabKey }) {
  const tabs = TABS[node.type] || ["Physical", "Config"];
  const [tab, setTab] = useState(initialTab && tabs.includes(initialTab) ? initialTab : tabs[node.type === "router" ? 2 : node.type === "pc" ? 2 : 0]);
  useEffect(() => { if (initialTab && tabs.includes(initialTab)) setTab(initialTab); }, [initialTab, tabKey]); // eslint-disable-line
  const [max, setMax] = useState(false);
  const [pos, setPos] = useState(initialPos);
  const drag = useRef(null);
  const running = state === "running";
  const Icon = ICONS[node.type];

  const startDrag = (e) => {
    onFocus();
    drag.current = { x: e.clientX - pos.x, y: e.clientY - pos.y };
    const mv = (ev) => setPos({ x: ev.clientX - drag.current.x, y: Math.max(0, ev.clientY - drag.current.y) });
    const up = () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", mv);
    window.addEventListener("mouseup", up);
  };

  return (
    <section className={`win ${max ? "win-max" : ""}`} style={max ? { zIndex: z } : { left: pos.x, top: pos.y, zIndex: z }} onMouseDown={onFocus} aria-label={`${node.id} window`}>
      <header className="win-head" onMouseDown={(e) => !max && startDrag(e)} onDoubleClick={() => setMax(!max)} title="Drag to move, double-click to maximise">
        <Icon size={22} />
        <h2>{node.id}</h2>
        <span className={`pill pill-${state || "off"}`}>{state || "not deployed"}</span>
        <button className="win-btn" onClick={() => setMax(!max)} aria-label={max ? "Restore window" : "Maximise window"}>{max ? "❐" : "□"}</button>
        <button className="win-x" onClick={onClose} aria-label="Close">×</button>
      </header>
      <nav className="tabs" role="tablist">
        {tabs.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>
      <div className="win-body">
        {tab === "Physical" && <Physical lab={lab} node={node} running={running} />}
        {tab === "Config" && node.type === "router" && <RouterConfig lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} onRename={onRename} notify={notify} />}
        {tab === "Config" && node.type === "switch" && <SwitchConfig lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} onRename={onRename} notify={notify} />}
        {tab === "Config" && node.type !== "router" && node.type !== "switch" && <BasicConfig lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} onRename={onRename} />}
        {tab === "CLI" && (running ? <Terminal lab={lab.name} node={node.id} mode={node.type === "router" ? "vtysh" : "sh"} /> : <NotRunning />)}
        {tab === "Desktop" && <Desktop lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} notify={notify} />}
        {tab === "Services" && <Services lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} notify={notify} />}
        {tab === "Monitor" && <MonitorTab lab={lab} node={node} running={running} />}
      </div>
    </section>
  );
}

function NotRunning() {
  return <p className="empty">Deploy the lab to use this tab.</p>;
}

function linksOf(lab, node) {
  return lab.links.filter((l) => l.a === node.id || l.b === node.id).map((l) => {
    const mine = l.a === node.id;
    return { ifname: mine ? l.a_if : l.b_if, peer: mine ? l.b : l.a, peerIf: mine ? l.b_if : l.a_if, media: l.media };
  }).sort((x, y) => x.ifname.localeCompare(y.ifname, undefined, { numeric: true }));
}

function Physical({ lab, node, running }) {
  const [ifs, setIfs] = useState(null);
  const [err, setErr] = useState("");
  const links = linksOf(lab, node);
  useEffect(() => {
    if (!running) return;
    let alive = true;
    const load = () => api.ifaces(lab.name, node.id).then((d) => alive && setIfs(d)).catch((e) => alive && setErr(e.message));
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [running, lab.name, node.id]);
  const byName = Object.fromEntries((ifs || []).map((i) => [i.name, i]));
  const names = [...new Set([...links.map((l) => l.ifname), ...(ifs || []).map((i) => i.name)])]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  return (
    <div>
      <p className="hint">{TYPE_LABEL[node.type]}, {node.raw ? `imported (${node.raw.image || node.raw.kind})` : "netlab device"}</p>
      {err && <p className="error">{err}</p>}
      <table className="grid">
        <thead><tr><th>Port</th><th>Link</th><th>Connected to</th><th>Media</th><th>MAC</th><th>IPv4</th></tr></thead>
        <tbody>
          {names.map((n) => {
            const i = byName[n], l = links.find((x) => x.ifname === n);
            const st = i ? i.state.toLowerCase() : "";
            return (
              <tr key={n}>
                <td className="mono">{n}</td>
                <td><span className={`led led-${st === "up" ? "up" : st === "unknown" ? "warn" : st ? "down" : "idle"}`} />{i ? i.state : "—"}</td>
                <td>{l ? `${l.peer} ${l.peerIf}` : ""}</td>
                <td>{l ? l.media : ""}</td>
                <td className="mono">{i?.mac || ""}</td>
                <td className="mono">{i?.ipv4.join(", ") || ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!running && <p className="hint">Port state and addresses appear once the lab is deployed.</p>}
    </div>
  );
}

function NameAndStartup({ node, running, onUpdateNode, onRename, placeholder }) {
  const [name, setName] = useState(node.id);
  const [startup, setStartup] = useState((node.props?.startup || []).join("\n"));
  useEffect(() => setName(node.id), [node.id]);
  return (
    <fieldset className="box">
      <legend>Device</legend>
      <label className="row">Display name
        <input value={name} disabled={running || !!node.raw} onChange={(e) => setName(e.target.value.toLowerCase())} />
        <button disabled={running || name === node.id || !!node.raw} onClick={() => onRename(node.id, name)}>Rename</button>
      </label>
      {running && <p className="hint">Destroy the lab to rename this device.</p>}
      {!node.raw && (
        <>
          <label className="col">Startup commands (run inside the container at every deploy, one per line)
            <textarea rows={4} value={startup} placeholder={placeholder} onChange={(e) => setStartup(e.target.value)} />
          </label>
          <button onClick={() => onUpdateNode(node.id, { startup: startup.split("\n").map((s) => s.trim()).filter(Boolean) })}>Save startup commands</button>
          <p className="hint">Takes effect on the next deploy.</p>
        </>
      )}
    </fieldset>
  );
}

function BasicConfig(props) {
  return <NameAndStartup {...props} placeholder="ip link set eth1 mtu 1400" />;
}

function RouterConfig({ lab, node, running, onUpdateNode, onRename, notify }) {
  const links = linksOf(lab, node);
  const subifs = node.props?.subifs || [];
  const ifOptions = [...links.map((l) => l.ifname), ...subifs.map((x) => `${x.parent}.${x.vlan}`), "lo"];
  const [si, setSi] = useState({ parent: links[0]?.ifname || "eth1", vlan: "" });
  const subif = async (parent, vlan, action) => {
    try {
      const r = await api.subif(lab.name, node.id, parent, Number(vlan), action);
      const next = action === "add"
        ? [...subifs.filter((x) => !(x.parent === parent && Number(x.vlan) === Number(vlan))), { parent, vlan: Number(vlan) }]
        : subifs.filter((x) => !(x.parent === parent && Number(x.vlan) === Number(vlan)));
      onUpdateNode(node.id, { subifs: next });
      setLog((l) => [...l, { c: `${action === "add" ? "create" : "delete"} subinterface ${parent}.${vlan} (802.1Q VLAN ${vlan})`, out: true, bad: !r.ok }, ...(r.output ? [{ c: r.output, out: true }] : [])]);
    } catch (e) { notify(e.message); }
  };
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({
    hostname: node.id, ifname: ifOptions[0] || "lo", ip: "", desc: "", up: true,
    sPrefix: "", sNh: "", oRid: "", oNet: "", oArea: "0", oPassive: "",
    bAs: "", bRid: "", bNbr: "", bRas: "", bNet: "", bNoPolicy: true,
  });
  const [cfg, setCfg] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const send = async (cmds) => {
    if (!running) return notify("Deploy the lab before configuring devices.");
    setBusy(true);
    try {
      const r = await api.config(lab.name, node.id, cmds);
      setLog((l) => [...l, ...cmds.map((c) => ({ c, ok: r.ok })), ...(r.output ? [{ c: r.output, out: true }] : [])].slice(-200));
    } catch (e) {
      setLog((l) => [...l, { c: e.message, out: true, bad: true }]);
    } finally { setBusy(false); }
  };

  const iface = () => {
    const c = [`interface ${f.ifname}`];
    if (f.ip) c.push(`ip address ${f.ip}`);
    if (f.desc) c.push(`description ${f.desc}`);
    c.push(f.up ? "no shutdown" : "shutdown", "exit");
    send(c);
  };
  const bgp = () => {
    const c = [`router bgp ${f.bAs}`];
    if (f.bRid) c.push(`bgp router-id ${f.bRid}`);
    if (f.bNoPolicy) c.push("no bgp ebgp-requires-policy");
    if (f.bNbr && f.bRas) c.push(`neighbor ${f.bNbr} remote-as ${f.bRas}`);
    c.push("address-family ipv4 unicast");
    if (f.bNbr) c.push(`neighbor ${f.bNbr} activate`);
    if (f.bNet) c.push(`network ${f.bNet}`);
    c.push("exit-address-family", "exit");
    send(c);
  };
  const ospf = () => {
    const c = ["router ospf"];
    if (f.oRid) c.push(`ospf router-id ${f.oRid}`);
    if (f.oNet) c.push(`network ${f.oNet} area ${f.oArea || 0}`);
    if (f.oPassive) c.push(`passive-interface ${f.oPassive}`);
    c.push("exit");
    // a new router-id only takes effect after the OSPF process restarts (same as IOS)
    if (f.oRid) c.push("end", "clear ip ospf process");
    send(c);
  };

  return (
    <div className="cfg">
      <div className="cfg-forms">
        <fieldset className="box"><legend>Global</legend>
          <label className="row">Hostname <input value={f.hostname} onChange={set("hostname")} />
            <button disabled={busy} onClick={() => send([`hostname ${f.hostname}`])}>Apply</button></label>
        </fieldset>
        <fieldset className="box"><legend>Interface</legend>
          <label className="row">Port
            <select value={f.ifname} onChange={set("ifname")}>{ifOptions.map((i) => <option key={i}>{i}</option>)}</select>
            <label className="chk"><input type="checkbox" checked={f.up} onChange={set("up")} /> Port on</label>
          </label>
          <label className="row">IPv4 address <input placeholder="10.0.0.1/30" value={f.ip} onChange={set("ip")} /></label>
          <label className="row">Description <input value={f.desc} onChange={set("desc")} /></label>
          <button disabled={busy} onClick={iface}>Apply interface</button>
        </fieldset>
        <fieldset className="box"><legend>VLAN subinterfaces (router-on-a-stick)</legend>
          <label className="row">Parent port
            <select value={si.parent} onChange={(e) => setSi({ ...si, parent: e.target.value })}>{links.map((l) => <option key={l.ifname}>{l.ifname}</option>)}</select>
            VLAN <input className="narrow" placeholder="10" value={si.vlan} onChange={(e) => setSi({ ...si, vlan: e.target.value.replace(/\D/g, "") })} />
            <button disabled={!si.vlan || !links.length} onClick={() => { subif(si.parent, si.vlan, "add"); setSi({ ...si, vlan: "" }); }}>Add</button>
          </label>
          {subifs.length > 0 && <p className="hint">{subifs.map((x) => (
            <span key={`${x.parent}.${x.vlan}`} className="mono">{x.parent}.{x.vlan} <button className="link" onClick={() => subif(x.parent, x.vlan, "del")}>remove</button>{"  "}</span>
          ))}</p>}
          <p className="hint">Creates the tagged subinterface; then address it in the Interface box. Cable the parent port to a switch trunk.</p>
        </fieldset>
        <fieldset className="box"><legend>Static route</legend>
          <label className="row">Network <input placeholder="0.0.0.0/0" value={f.sPrefix} onChange={set("sPrefix")} /></label>
          <label className="row">Next hop <input placeholder="10.0.0.2" value={f.sNh} onChange={set("sNh")} /></label>
          <button disabled={busy || !f.sPrefix || !f.sNh} onClick={() => send([`ip route ${f.sPrefix} ${f.sNh}`])}>Add route</button>
        </fieldset>
        <fieldset className="box"><legend>OSPF</legend>
          <label className="row">Router ID <input placeholder="loopback IP, e.g. 10.255.255.1" value={f.oRid} onChange={set("oRid")} /></label>
          <p className="hint">Set it first. Left blank, FRR picks the highest address, which is the management eth0 (172.31.255.x) and changes on every deploy.</p>
          <label className="row">Network <input placeholder="10.0.0.0/30" value={f.oNet} onChange={set("oNet")} />
            Area <input className="narrow" value={f.oArea} onChange={set("oArea")} /></label>
          <label className="row">Passive interface
            <select value={f.oPassive} onChange={set("oPassive")}><option value="">none</option>{ifOptions.map((i) => <option key={i}>{i}</option>)}</select></label>
          <button disabled={busy} onClick={ospf}>Apply OSPF</button>
        </fieldset>
        <fieldset className="box"><legend>BGP</legend>
          <label className="row">Local AS <input className="narrow" value={f.bAs} onChange={set("bAs")} />
            Router ID <input value={f.bRid} onChange={set("bRid")} /></label>
          <label className="row">Neighbor <input value={f.bNbr} onChange={set("bNbr")} />
            Remote AS <input className="narrow" value={f.bRas} onChange={set("bRas")} /></label>
          <label className="row">Network <input placeholder="192.0.2.0/24" value={f.bNet} onChange={set("bNet")} /></label>
          <label className="chk"><input type="checkbox" checked={f.bNoPolicy} onChange={set("bNoPolicy")} /> Allow eBGP without route-maps (lab shortcut)</label>
          <button disabled={busy || !f.bAs} onClick={bgp}>Apply BGP</button>
        </fieldset>
        <NameAndStartup node={node} running={running} onUpdateNode={onUpdateNode} onRename={onRename}
          placeholder={"ip link add CUST-A type vrf table 10\nip link set CUST-A up\nip link set eth3 master CUST-A"} />
      </div>
      <div className="cfg-side">
        <h3>Equivalent commands</h3>
        <pre className="eq" aria-live="polite">
          {log.length ? log.map((l, i) => <div key={i} className={l.out ? (l.bad ? "eq-bad" : "eq-out") : ""}>{l.out ? l.c : `${node.id}${l.c === "end" || l.c.startsWith("clear ") ? "#" : "(config)#"} ${l.c}`}</div>) : "Commands you apply from the forms appear here."}
        </pre>
        <div className="row">
          <button disabled={!running} onClick={async () => { try { setCfg((await api.running(lab.name, node.id)).config); } catch (e) { notify(e.message); } }}>Show running config</button>
          {cfg && <button onClick={() => setCfg("")}>Hide</button>}
        </div>
        {cfg && <pre className="eq">{cfg}</pre>}
      </div>
    </div>
  );
}

function Desktop({ lab, node, running, onUpdateNode, notify }) {
  const [app, setApp] = useState(null);
  if (!app) {
    return (
      <div className="desk">
        <button className="tile" onClick={() => setApp("ip")}><span className="app-ico">IP</span>IP Configuration</button>
        <button className="tile" disabled={!running} onClick={() => setApp("cmd")}><span className="app-ico">C:\</span>Command Prompt</button>
        <button className="tile" disabled={!running} onClick={() => setApp("web")}><span className="app-ico">www</span>Web Browser</button>
        {!running && <p className="hint">Command Prompt and Web Browser need the lab deployed.</p>}
      </div>
    );
  }
  return (
    <div className="desk-app">
      <button className="back" onClick={() => setApp(null)}>Back to desktop</button>
      {app === "ip" && <IpConfig lab={lab} node={node} running={running} onUpdateNode={onUpdateNode} notify={notify} />}
      {app === "cmd" && <Terminal lab={lab.name} node={node.id} mode="pc" />}
      {app === "web" && <Browser lab={lab} node={node} />}
    </div>
  );
}

function IpConfig({ lab, node, running, onUpdateNode, notify }) {
  const p = node.props || {};
  const [cidrIp, cidrLen] = (p.ip || "/24").split("/");
  const [mode, setMode] = useState(p.ip_mode === "dhcp" ? "dhcp" : "static");
  const [ip, setIp] = useState(cidrIp || "");
  const [mask, setMask] = useState(prefixToMask(Number(cidrLen || 24)));
  const [gw, setGw] = useState(p.gateway || "");
  const [dns, setDns] = useState(p.dns || "");
  const [out, setOut] = useState("");
  const apply = async () => {
    const body = { mode, ip: ip ? `${ip}/${maskToPrefix(mask)}` : "", gateway: gw, dns };
    onUpdateNode(node.id, { ip_mode: mode, ip: body.ip, gateway: gw, dns });
    if (!running) return setOut("Saved. Applied at the next deploy.");
    try { const r = await api.ipconfig(lab.name, node.id, body); setOut(r.output); } catch (e) { notify(e.message); }
  };
  return (
    <div className="form">
      <div className="row">
        <label className="chk"><input type="radio" checked={mode === "dhcp"} onChange={() => setMode("dhcp")} /> DHCP</label>
        <label className="chk"><input type="radio" checked={mode === "static"} onChange={() => setMode("static")} /> Static</label>
      </div>
      <label className="row">IPv4 Address <input disabled={mode === "dhcp"} value={ip} onChange={(e) => setIp(e.target.value)} /></label>
      <label className="row">Subnet Mask <input disabled={mode === "dhcp"} value={mask} onChange={(e) => setMask(e.target.value)} /></label>
      <label className="row">Default Gateway <input disabled={mode === "dhcp"} value={gw} onChange={(e) => setGw(e.target.value)} /></label>
      <label className="row">DNS Server <input disabled={mode === "dhcp"} value={dns} onChange={(e) => setDns(e.target.value)} /></label>
      <button onClick={apply}>{mode === "dhcp" ? "Request DHCP address" : "Apply"}</button>
      {out && <pre className="eq">{out}</pre>}
    </div>
  );
}

function Browser({ lab, node }) {
  const [url, setUrl] = useState("http://");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = async (e) => {
    e?.preventDefault();
    setBusy(true);
    try { setRes(await api.browse(lab.name, node.id, url)); } catch (err) { setRes({ ok: false, error: err.message }); }
    setBusy(false);
  };
  return (
    <div className="browser">
      <form className="row" onSubmit={go}>
        <input className="url" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="URL" />
        <button disabled={busy}>{busy ? "Loading…" : "Go"}</button>
      </form>
      {res && !res.ok && <p className="error">{res.error}</p>}
      {res?.ok && <iframe title="page" sandbox="" srcDoc={res.body || `<p>HTTP ${res.status}, empty body</p>`} />}
    </div>
  );
}

function Services({ lab, node, running, onUpdateNode, notify }) {
  const init = node.props?.services || {};
  const [svc, setSvc] = useState({
    dns: { enabled: false, records: [], ...init.dns },
    dhcp: { enabled: false, start: "", end: "", mask: "255.255.255.0", gateway: "", dns: "", ...init.dhcp },
    http: { enabled: true, index_html: "", ...init.http },
  });
  const [rec, setRec] = useState({ name: "", ip: "" });
  const [out, setOut] = useState("");
  if (node.raw) return <p className="empty">Imported device: edit its service files in the lab folder.</p>;
  const upd = (s, k, v) => setSvc({ ...svc, [s]: { ...svc[s], [k]: v } });
  const save = async () => {
    onUpdateNode(node.id, { services: svc });
    if (!running) return setOut("Saved. Applied at the next deploy.");
    try { const r = await api.services(lab.name, node.id, svc); setOut(r.ok ? "Services applied." : r.output); } catch (e) { notify(e.message); }
  };
  return (
    <div className="form">
      <fieldset className="box"><legend>DNS</legend>
        <label className="chk"><input type="checkbox" checked={svc.dns.enabled} onChange={(e) => upd("dns", "enabled", e.target.checked)} /> DNS service on</label>
        <div className="row">
          <input placeholder="www.example.lab" value={rec.name} onChange={(e) => setRec({ ...rec, name: e.target.value })} />
          <input placeholder="Address" value={rec.ip} onChange={(e) => setRec({ ...rec, ip: e.target.value })} />
          <button disabled={!rec.name || !rec.ip} onClick={() => { upd("dns", "records", [...svc.dns.records, rec]); setRec({ name: "", ip: "" }); }}>Add record</button>
        </div>
        <table className="grid"><thead><tr><th>Name</th><th>Type</th><th>Address</th><th /></tr></thead>
          <tbody>{svc.dns.records.map((r, i) => (
            <tr key={i}><td>{r.name}</td><td>A</td><td className="mono">{r.ip}</td>
              <td><button className="link" onClick={() => upd("dns", "records", svc.dns.records.filter((_, j) => j !== i))}>Remove</button></td></tr>
          ))}</tbody></table>
      </fieldset>
      <fieldset className="box"><legend>DHCP</legend>
        <label className="chk"><input type="checkbox" checked={svc.dhcp.enabled} onChange={(e) => upd("dhcp", "enabled", e.target.checked)} /> DHCP service on</label>
        {[["start", "Start address"], ["end", "End address"], ["mask", "Subnet mask"], ["gateway", "Default gateway"], ["dns", "DNS server"]].map(([k, l]) => (
          <label className="row" key={k}>{l} <input value={svc.dhcp[k]} onChange={(e) => upd("dhcp", k, e.target.value)} /></label>
        ))}
      </fieldset>
      <fieldset className="box"><legend>HTTP</legend>
        <label className="chk"><input type="checkbox" checked={svc.http.enabled} onChange={(e) => upd("http", "enabled", e.target.checked)} /> HTTP service on</label>
        <label className="col">index.html
          <textarea rows={6} value={svc.http.index_html} placeholder={`<h1>${node.id}</h1>`} onChange={(e) => upd("http", "index_html", e.target.value)} />
        </label>
      </fieldset>
      <button onClick={save}>Save services</button>
      {out && <p className="hint">{out}</p>}
    </div>
  );
}

function SwitchConfig({ lab, node, running, onUpdateNode, onRename, notify }) {
  const links = linksOf(lab, node);
  const p = node.props || {};
  const [vlans, setVlans] = useState(p.vlans?.length ? p.vlans : [{ id: 1, name: "default" }]);
  const [ports, setPorts] = useState(p.ports || {});
  const [stp, setStp] = useState(!!p.stp);
  const [prio, setPrio] = useState(p.stp_priority || 32768);
  const [svi, setSvi] = useState({ vlan: 1, ip: "", gateway: "", ...(p.svi || {}) });
  const [nv, setNv] = useState({ id: "", name: "" });
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  if (node.raw) return <p className="empty">Imported bridge: VLANs are not managed here.</p>;
  const port = (ifn) => ({ mode: "access", vlan: 1, native: 1, allowed: "", ...(ports[ifn] || {}) });
  const setPort = (ifn, k, v) => setPorts({ ...ports, [ifn]: { ...port(ifn), [k]: v } });
  const ids = [...new Set([1, ...vlans.map((v) => Number(v.id))])].sort((a, b) => a - b);
  const vname = (id) => vlans.find((v) => Number(v.id) === Number(id))?.name;
  const apply = async () => {
    const body = { vlans, ports, stp, svi, stp_priority: prio };
    onUpdateNode(node.id, body);
    setBusy(true);
    try { const r = await api.switchApply(lab.name, node.id, body); setOut(r.output); if (!r.ok) notify(`${node.id}: switch config failed`); }
    catch (e) { setOut(e.message); notify(e.message); }
    setBusy(false);
  };
  const show = async () => { try { setOut((await api.switchShow(lab.name, node.id)).output); } catch (e) { setOut(e.message); } };
  return (
    <div className="cfg">
      <div className="cfg-forms">
        <fieldset className="box"><legend>VLAN database</legend>
          <table className="grid"><thead><tr><th>VLAN</th><th>Name</th><th /></tr></thead>
            <tbody>{vlans.map((v, i) => (
              <tr key={i}><td className="mono">{v.id}</td><td>{v.name}</td>
                <td>{Number(v.id) !== 1 && <button className="link" onClick={() => setVlans(vlans.filter((_, j) => j !== i))}>Remove</button>}</td></tr>
            ))}</tbody></table>
          <div className="row">
            <input className="narrow" placeholder="10" value={nv.id} onChange={(e) => setNv({ ...nv, id: e.target.value.replace(/\D/g, "") })} />
            <input placeholder="USERS" value={nv.name} onChange={(e) => setNv({ ...nv, name: e.target.value })} />
            <button disabled={!nv.id || Number(nv.id) < 1 || Number(nv.id) > 4094 || ids.includes(Number(nv.id))}
              onClick={() => { setVlans([...vlans, { id: Number(nv.id), name: nv.name || `VLAN${String(nv.id).padStart(4, "0")}` }].sort((a, b) => a.id - b.id)); setNv({ id: "", name: "" }); }}>Add VLAN</button>
          </div>
        </fieldset>
        <fieldset className="box"><legend>Ports</legend>
          {!links.length && <p className="hint">Cable devices to this switch first.</p>}
          {links.length > 0 && (
            <table className="grid"><thead><tr><th>Port</th><th>To</th><th>Mode</th><th>Access VLAN / Native</th><th>Allowed (trunk)</th></tr></thead>
              <tbody>{links.map((l) => {
                const c = port(l.ifname);
                return (
                  <tr key={l.ifname}>
                    <td className="mono">{l.ifname}</td><td>{l.peer}</td>
                    <td><select value={c.mode} onChange={(e) => setPort(l.ifname, "mode", e.target.value)}><option value="access">access</option><option value="trunk">trunk</option></select></td>
                    <td><select value={c.mode === "trunk" ? c.native : c.vlan} onChange={(e) => setPort(l.ifname, c.mode === "trunk" ? "native" : "vlan", Number(e.target.value))}>
                      {ids.map((id) => <option key={id} value={id}>{id}{vname(id) ? ` ${vname(id)}` : ""}</option>)}</select></td>
                    <td>{c.mode === "trunk" ? <input placeholder="all (1-4094)" value={c.allowed} onChange={(e) => setPort(l.ifname, "allowed", e.target.value)} /> : <span className="hint">—</span>}</td>
                  </tr>);
              })}</tbody></table>)}
          <label className="chk"><input type="checkbox" checked={stp} onChange={(e) => setStp(e.target.checked)} /> Spanning tree (802.1D, kernel). Ports take ~30 s to forward after a change.</label>
          <label className="row">Bridge priority <select value={prio} disabled={!stp} onChange={(e) => setPrio(Number(e.target.value))}>
            {Array.from({ length: 16 }, (_, i) => i * 4096).map((v) => <option key={v} value={v}>{v}{v === 32768 ? " (default)" : v === 0 ? " (always root)" : ""}</option>)}</select>
            <span className="hint">Lowest wins root. Set the core switch to 4096.</span></label>
        </fieldset>
        <fieldset className="box"><legend>Management (SVI)</legend>
          <label className="row">VLAN <select value={svi.vlan} onChange={(e) => setSvi({ ...svi, vlan: Number(e.target.value) })}>{ids.map((id) => <option key={id} value={id}>{id}</option>)}</select>
            IP <input placeholder="192.168.10.2/24" value={svi.ip} onChange={(e) => setSvi({ ...svi, ip: e.target.value })} /></label>
          <label className="row">Default gateway <input placeholder="192.168.10.1" value={svi.gateway} onChange={(e) => setSvi({ ...svi, gateway: e.target.value })} /></label>
          <p className="hint">Layer 2 switch: the SVI is for managing and pinging the switch, it does not route between VLANs.</p>
        </fieldset>
        <div className="row">
          <button className="primary" disabled={busy} onClick={apply}>{running ? "Apply to switch" : "Save (applied at deploy)"}</button>
          <button disabled={!running} onClick={show}>Show VLANs / STP / MAC table</button>
        </div>
        <NameAndStartup node={node} running={running} onUpdateNode={onUpdateNode} onRename={onRename} placeholder="ip link set eth1 mtu 1500" />
      </div>
      <div className="cfg-side">
        <h3>Switch output</h3>
        <pre className="eq">{out || "Apply or Show to see the switch's VLAN table (bridge vlan show), port STP states and learned MACs."}</pre>
      </div>
    </div>
  );
}
