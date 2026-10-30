// Server Services tab, Packet Tracer style: one page per service, each with an On/Off
// switch, live "running" lights from the container and one Save for the lot.
import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { SideNav, Field, ApplyBar, isIp } from "./Config.jsx";

const SERVICES = [
  ["summary", "Summary"], ["http", "HTTP"], ["dhcp", "DHCP"], ["dns", "DNS"], ["tftp", "TFTP"],
  ["ftp", "FTP"], ["syslog", "SYSLOG"], ["ntp", "NTP"],
];
const PORTS = { http: "TCP 80", dhcp: "UDP 67", dns: "UDP/TCP 53", tftp: "UDP 69", ftp: "TCP 21", syslog: "UDP/TCP 514", ntp: "UDP 123" };
const isMac = (v) => /^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$/i.test(String(v || "").trim());
const isHost = (v) => /^[A-Za-z0-9.-]{1,253}$/.test(String(v || "").trim());

function dhcpDefaults(d = {}) {
  const { start, end, mask, gateway, dns, pools, ...rest } = d;
  const ps = pools?.length ? pools : (start || end) ? [{ name: "serverPool", start: start || "", end: end || "", mask: mask || "255.255.255.0", gateway: gateway || "", dns: dns || "" }] : [];
  return { enabled: false, domain: "", tftp: "", lease: "12h", reservations: [], ...rest, pools: ps };
}

function withDefaults(init = {}) {
  return {
    http: { enabled: true, index_html: "", ...init.http },
    dhcp: dhcpDefaults(init.dhcp),
    dns: { enabled: false, records: [], ...init.dns },
    tftp: { enabled: false, ...init.tftp },
    ftp: { enabled: false, users: [{ user: "cisco", password: "cisco" }], ...init.ftp },
    syslog: { enabled: false, ...init.syslog },
    ntp: { enabled: false, stratum: 8, ...init.ntp },
  };
}

function problems(s) {
  const out = [];
  const d = s.dhcp;
  if (d.enabled) {
    if (!d.pools.length) out.push("DHCP: add a pool");
    d.pools.forEach((pl) => {
      if (!isIp(pl.start) || !isIp(pl.end)) out.push(`DHCP pool ${pl.name}: start and end address are needed`);
      for (const k of ["gateway", "dns", "tftp"]) if (pl[k] && !isIp(pl[k])) out.push(`DHCP pool ${pl.name}: ${k} isn't an IP address`);
    });
    if (d.tftp && !isIp(d.tftp)) out.push("DHCP: TFTP server isn't an IP address");
    if (d.domain && !isHost(d.domain)) out.push("DHCP: bad domain name");
    if (!/^(\d+[mhd]?|infinite)$/.test(String(d.lease || "12h"))) out.push("DHCP: lease time like 30m, 12h, 7d or infinite");
    d.reservations.forEach((r, i) => { if (!isMac(r.mac) || !isIp(r.ip)) out.push(`DHCP reservation ${i + 1}: needs a MAC and an IP`); });
  }
  const a = new Set(s.dns.records.filter((r) => (r.type || "A") === "A").map((r) => r.name.toLowerCase()));
  s.dns.records.forEach((r) => { if (r.type === "CNAME" && !a.has(String(r.target).toLowerCase())) out.push(`DNS: CNAME ${r.name} → ${r.target} needs an A record for ${r.target}`); });
  if (s.ftp.enabled && !s.ftp.users.length) out.push("FTP: add at least one user");
  return out;
}

const STATE_TEXT = (running) => ({ off: "off", idle: running ? "starting…" : "not deployed", up: "running", down: "not running" });

// a real component (not one defined inside ServicesX's render): a parent re-render between
// mousedown and click would otherwise remount the buttons and swallow the click
function OnOff({ k, svc, upd, state, running }) {
  return (
    <div className="svc-head">
      <h3>{SERVICES.find(([x]) => x === k)[1]}</h3>
      <span className="hint">{PORTS[k]}</span>
      <span className="grow" />
      <div className="seg" role="radiogroup" aria-label={`${k} service`}>
        <button role="radio" aria-checked={svc[k].enabled} className={svc[k].enabled ? "on" : ""} onClick={() => upd(k, "enabled", true)}>On</button>
        <button role="radio" aria-checked={!svc[k].enabled} className={!svc[k].enabled ? "on" : ""} onClick={() => upd(k, "enabled", false)}>Off</button>
      </div>
      <span className={`svc-state svc-${state}`}>{STATE_TEXT(running)[state]}</span>
    </div>
  );
}

export default function ServicesX({ lab, node, running, onUpdateNode, notify }) {
  const saved = withDefaults(node.props?.services);
  const [svc, setSvc] = useState(saved);
  const [page, setPage] = useState("summary");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState("");
  const [live, setLive] = useState(null);
  const key = JSON.stringify(node.props?.services || {});
  useEffect(() => setSvc(withDefaults(JSON.parse(key))), [key]);
  useEffect(() => {
    let stop = false;
    const load = () => api.serverStatus(lab.name, node.id).then((r) => !stop && setLive(r)).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => { stop = true; clearInterval(t); };
  }, [lab.name, node.id, running]);
  if (node.raw) return <p className="empty">Imported device: edit its service files in the lab folder.</p>;

  const dirty = JSON.stringify(svc) !== JSON.stringify(saved);
  const upd = (s, k, v) => setSvc((x) => ({ ...x, [s]: { ...x[s], [k]: v } }));
  const bad = problems(svc);
  const ip = /^\d/.test(node.props?.ip || "") ? node.props.ip.split("/")[0] : "SERVER-IP";
  const state = (k) => {
    if (k === "summary") return null;
    if (!svc[k].enabled) return "off";
    if (!live?.running) return "idle";
    return live.services?.[k] ? "up" : "down";
  };
  const save = async () => {
    setBusy(true);
    try {
      onUpdateNode(node.id, { services: svc });
      if (!running) setOut("Saved. The services start when the lab is deployed.");
      else { const r = await api.services(lab.name, node.id, svc); setOut(r.output || (r.ok ? "Services applied." : "Failed")); if (!r.ok) notify(`${node.id}: some services failed to start`, null, "bad"); }
      setTimeout(() => api.serverStatus(lab.name, node.id).then(setLive).catch(() => {}), 1500);
    } catch (e) { setOut(e.message); notify(e.message, null, "bad"); }
    setBusy(false);
  };
  const head = (k) => <OnOff k={k} svc={svc} upd={upd} state={state(k)} running={running} />;
  const groups = [{ title: "Services", items: SERVICES.map(([k, label]) => ({ key: k, label, dot: k === "summary" ? null : state(k) === "off" ? "off" : state(k) === "up" ? "up" : state(k) === "down" ? "down" : "idle" })) }];

  return (
    <div className="cfgx cfgx-nolog">
      <SideNav groups={groups} active={page} onPick={setPage} />
      <div className="cfgx-main svc">
        {page === "summary" && (<>
          <h3>Services on {node.id}</h3>
          <p className="hint">Server address {ip}. Pick a service on the left to set it up; Save applies everything at once{running ? " to the running server" : " (started at deploy)"}.</p>
          <table className="grid"><thead><tr><th>Service</th><th>Port</th><th>Configured</th><th>Status</th><th /></tr></thead>
            <tbody>{SERVICES.slice(1).map(([k, label]) => (
              <tr key={k}><td><b>{label}</b></td><td className="mono">{PORTS[k]}</td>
                <td>{svc[k].enabled ? "On" : "Off"}</td>
                <td><span className={`svc-state svc-${state(k)}`}>{{ off: "—", idle: running ? "starting…" : "not deployed", up: "running", down: "not running" }[state(k)]}</span></td>
                <td><button className="link" onClick={() => setPage(k)}>Open</button></td></tr>))}</tbody></table>
          {live?.leases?.length > 0 && <p className="hint">{live.leases.length} DHCP lease{live.leases.length > 1 ? "s" : ""} handed out: see DHCP.</p>}
        </>)}

        {page === "http" && (<>
          {head("http")}
          <Field label="index.html" wide hint={`Browse to http://${ip}/ from a PC's Desktop › Web Browser, or a DNS name that points here.`}>
            <textarea rows={12} className="mono" value={svc.http.index_html} placeholder={`<h1>${node.id}</h1>`} onChange={(e) => upd("http", "index_html", e.target.value)} />
          </Field>
          <Field label="Preview" wide><iframe className="svc-preview" title="preview" sandbox="" srcDoc={svc.http.index_html || `<h1>${node.id}</h1><p>Served from ${node.id}.</p>`} /></Field>
        </>)}

        {page === "dhcp" && <DhcpPage svc={svc} upd={upd} head={head} leases={live?.leases || []} running={running} />}
        {page === "dns" && <DnsPage svc={svc} upd={upd} head={head} ip={ip} />}

        {page === "tftp" && (<>
          {head("tftp")}
          <p className="hint">Back up a router or switch from its CLI: <span className="mono">copy running-config tftp://{ip}/r1-confg</span>; restore with <span className="mono">copy tftp://{ip}/r1-confg running-config</span>. Uploads from devices are allowed.</p>
          <FileArea lab={lab} node={node} kind="tftp" notify={notify} />
        </>)}

        {page === "ftp" && <FtpPage lab={lab} node={node} svc={svc} upd={upd} head={head} ip={ip} notify={notify} />}

        {page === "syslog" && (<>
          {head("syslog")}
          <p className="hint">Point devices at it with <span className="mono">logging host {ip}</span> (router/switch CLI): configuration changes and interface up/down are logged.</p>
          <SyslogView lab={lab} node={node} running={running} />
        </>)}

        {page === "ntp" && (<>
          {head("ntp")}
          <Field label="Stratum" hint="What this server advertises; clients prefer lower numbers. It serves its own clock.">
            <input className="narrow" type="number" min="1" max="15" value={svc.ntp.stratum} onChange={(e) => upd("ntp", "stratum", Number(e.target.value))} />
          </Field>
          <p className="hint">Test from a PC: <span className="mono">ntpd -q -n -p {ip}</span>.</p>
        </>)}

        {out && <pre className="eq svc-out">{out}</pre>}
        <ApplyBar dirty={dirty} busy={busy} disabled={bad.length > 0} label={running ? "Save and apply" : "Save"}
          note={bad[0] || (dirty ? "Unsaved changes" : "")} onApply={save} onReset={() => setSvc(saved)} />
      </div>
    </div>
  );
}

function DhcpPage({ svc, upd, head, leases, running }) {
  const d = svc.dhcp;
  const [r, setR] = useState({ mac: "", ip: "", name: "" });
  const now = Date.now() / 1000;
  return (<>
    {head("dhcp")}
    <div className="fx-grid">
      {[["domain", "Domain name", "lab.local"], ["tftp", "TFTP server", "option 150/66"], ["lease", "Lease time", "12h"]].map(([k, l, ph]) => (
        <Field key={k} label={l}><input className={d[k] && k === "tftp" && !isIp(d[k]) ? "bad" : ""} value={d[k] || ""} placeholder={ph} onChange={(e) => upd("dhcp", k, e.target.value.trim())} /></Field>))}
    </div>
    <h4>Pools</h4>
    <p className="hint">One pool per subnet. The server hands out from the pool that matches the subnet the request came from: its own LAN, or a remote VLAN through a router with <span className="mono">ip helper-address</span> pointing here (the server needs a route back, usually its default gateway).</p>
    <PoolEditor pools={d.pools} setPools={(ps) => upd("dhcp", "pools", ps)} />
    <h4>Reservations</h4>
    <p className="hint">Always give a device the same address. Its MAC is on the PC's Config tab or <span className="mono">ipconfig /all</span>.</p>
    <table className="grid compact"><thead><tr><th>MAC address</th><th>IP address</th><th>Name</th><th /></tr></thead>
      <tbody>{d.reservations.map((x, i) => (
        <tr key={i}><td className="mono">{x.mac}</td><td className="mono">{x.ip}</td><td>{x.name}</td>
          <td><button className="link danger" onClick={() => upd("dhcp", "reservations", d.reservations.filter((_, j) => j !== i))}>Remove</button></td></tr>))}
        <tr><td><input className={r.mac && !isMac(r.mac) ? "bad" : ""} placeholder="aa:bb:cc:dd:ee:ff" value={r.mac} onChange={(e) => setR({ ...r, mac: e.target.value.trim() })} /></td>
          <td><input className={r.ip && !isIp(r.ip) ? "bad" : ""} placeholder="10.0.0.50" value={r.ip} onChange={(e) => setR({ ...r, ip: e.target.value.trim() })} /></td>
          <td><input placeholder="printer" value={r.name} onChange={(e) => setR({ ...r, name: e.target.value.trim() })} /></td>
          <td><button disabled={!isMac(r.mac) || !isIp(r.ip)} onClick={() => { upd("dhcp", "reservations", [...d.reservations, { ...r, mac: r.mac.toLowerCase().replace(/-/g, ":") }]); setR({ mac: "", ip: "", name: "" }); }}>Add</button></td></tr>
      </tbody></table>
    <h4>Leases</h4>
    <table className="grid compact"><thead><tr><th>IP address</th><th>MAC address</th><th>Host name</th><th>Expires</th></tr></thead>
      <tbody>{leases.map((l) => (
        <tr key={l.mac + l.ip}><td className="mono">{l.ip}</td><td className="mono">{l.mac}</td><td>{l.host || <span className="hint">—</span>}</td>
          <td>{l.expires === 0 ? "never" : l.expires < now ? "expired" : `in ${Math.max(1, Math.round((l.expires - now) / 60))} min`}</td></tr>))}
        {!leases.length && <tr><td colSpan={4} className="hint">{running ? "No leases yet. On a PC: Desktop › IP Configuration › DHCP, or ipconfig /renew." : "Deploy the lab to hand out addresses."}</td></tr>}</tbody></table>
  </>);
}

function PoolEditor({ pools, setPools }) {
  const blank = { name: "", start: "", end: "", mask: "255.255.255.0", gateway: "", dns: "", tftp: "" };
  const [ed, setEd] = useState(null);           // index being edited, or "new"
  const [f, setF] = useState(blank);
  const open = (i) => { setEd(i); setF(i === "new" ? { ...blank, name: `pool${pools.length + 1}` } : { ...blank, ...pools[i] }); };
  const okName = /^[A-Za-z0-9_.-]{1,32}$/.test(f.name) && !pools.some((p, j) => p.name === f.name && j !== ed);
  const ok = okName && isIp(f.start) && isIp(f.end) && isIp(f.mask) && ["gateway", "dns", "tftp"].every((k) => !f[k] || isIp(f[k]));
  const save = () => { const ps = [...pools]; if (ed === "new") ps.push(f); else ps[ed] = f; setPools(ps); setEd(null); };
  return (<>
    <table className="grid compact"><thead><tr><th>Pool</th><th>Range</th><th>Mask</th><th>Gateway</th><th>DNS</th><th /></tr></thead>
      <tbody>{pools.map((p, i) => (
        <tr key={i} className={ed === i ? "sel" : ""}><td><b>{p.name}</b></td><td className="mono">{p.start} – {p.end}</td><td className="mono">{p.mask}</td><td className="mono">{p.gateway || "—"}</td><td className="mono">{p.dns || "—"}</td>
          <td className="row"><button className="link" onClick={() => open(i)}>Edit</button><button className="link danger" onClick={() => { setPools(pools.filter((_, j) => j !== i)); setEd(null); }}>Remove</button></td></tr>))}
        {!pools.length && <tr><td colSpan={6} className="hint">No pools yet.</td></tr>}</tbody></table>
    {ed === null ? <p><button onClick={() => open("new")}>Add pool</button></p> : (
      <div className="subbox">
        <h4>{ed === "new" ? "New pool" : `Edit ${pools[ed].name}`}</h4>
        <div className="fx-grid">
          {[["name", "Pool name", "users"], ["start", "Start IP", "10.10.0.100"], ["end", "End IP", "10.10.0.199"], ["mask", "Subnet mask", "255.255.255.0"],
            ["gateway", "Default gateway", "10.10.0.1"], ["dns", "DNS server", "10.20.0.5"], ["tftp", "TFTP (this pool)", "optional"]].map(([k, l, ph]) => (
            <Field key={k} label={l}><input className={f[k] && k !== "name" && !isIp(f[k]) ? "bad" : k === "name" && f.name && !okName ? "bad" : ""} value={f[k] || ""} placeholder={ph}
              onChange={(e) => setF({ ...f, [k]: e.target.value.trim() })} /></Field>))}
        </div>
        <div className="row"><span className="grow" /><button onClick={() => setEd(null)}>Cancel</button><button className="primary" disabled={!ok} onClick={save}>{ed === "new" ? "Add" : "Done"}</button></div>
      </div>)}
  </>);
}

function DnsPage({ svc, upd, head, ip }) {
  const recs = svc.dns.records;
  const [r, setR] = useState({ name: "", type: "A", ip: "", target: "" });
  const aNames = recs.filter((x) => (x.type || "A") === "A").map((x) => x.name);
  const ok = isHost(r.name) && (r.type === "A" ? isIp(r.ip) : aNames.includes(r.target)) && !recs.some((x) => x.name.toLowerCase() === r.name.toLowerCase());
  return (<>
    {head("dns")}
    <p className="hint">This server answers for the names below and nothing else. Give PCs {ip} as their DNS server (or hand it out from DHCP), then <span className="mono">nslookup www.lab</span> or browse by name.</p>
    <table className="grid compact"><thead><tr><th>Name</th><th>Type</th><th>Detail</th><th /></tr></thead>
      <tbody>{recs.map((x, i) => (
        <tr key={i}><td className="mono">{x.name}</td><td>{x.type || "A"}</td><td className="mono">{(x.type || "A") === "A" ? x.ip : `→ ${x.target}`}</td>
          <td><button className="link danger" onClick={() => upd("dns", "records", recs.filter((_, j) => j !== i))}>Remove</button></td></tr>))}
        <tr><td><input placeholder="www.lab" value={r.name} onChange={(e) => setR({ ...r, name: e.target.value.trim() })} /></td>
          <td><select value={r.type} onChange={(e) => setR({ ...r, type: e.target.value })}><option value="A">A Record</option><option value="CNAME">CNAME</option></select></td>
          <td>{r.type === "A"
            ? <input className={r.ip && !isIp(r.ip) ? "bad" : ""} placeholder="10.0.0.5" value={r.ip} onChange={(e) => setR({ ...r, ip: e.target.value.trim() })} />
            : <select value={r.target} onChange={(e) => setR({ ...r, target: e.target.value })}><option value="">points at…</option>{aNames.map((n) => <option key={n} value={n}>{n}</option>)}</select>}</td>
          <td><button disabled={!ok} onClick={() => { upd("dns", "records", [...recs, r.type === "A" ? { name: r.name, type: "A", ip: r.ip } : { name: r.name, type: "CNAME", target: r.target }]); setR({ name: "", type: r.type, ip: "", target: "" }); }}>Add</button></td></tr>
      </tbody></table>
  </>);
}

function FtpPage({ lab, node, svc, upd, head, ip, notify }) {
  const users = svc.ftp.users;
  const [u, setU] = useState({ user: "", password: "" });
  const okUser = /^[a-z_][a-z0-9_-]{0,15}$/.test(u.user) && !["root", "ftp", "nobody"].includes(u.user) && u.password && !/[:'"\\]/.test(u.password) && !users.some((x) => x.user === u.user);
  return (<>
    {head("ftp")}
    <p className="hint">From a PC's command prompt: <span className="mono">ftp {ip}</span>, log in, then <span className="mono">dir</span>, <span className="mono">put FILE</span>, <span className="mono">get FILE</span>, <span className="mono">delete FILE</span>, <span className="mono">quit</span>. Users share one folder and can read and write.</p>
    <h4>User setup</h4>
    <table className="grid compact"><thead><tr><th>Username</th><th>Password</th><th /></tr></thead>
      <tbody>{users.map((x, i) => (
        <tr key={x.user}><td className="mono">{x.user}</td><td className="mono">{"•".repeat(Math.min(8, x.password.length))}</td>
          <td><button className="link danger" onClick={() => upd("ftp", "users", users.filter((_, j) => j !== i))}>Remove</button></td></tr>))}
        <tr><td><input placeholder="username" value={u.user} onChange={(e) => setU({ ...u, user: e.target.value.trim().toLowerCase() })} /></td>
          <td><input placeholder="password" value={u.password} onChange={(e) => setU({ ...u, password: e.target.value })} /></td>
          <td><button disabled={!okUser} onClick={() => { upd("ftp", "users", [...users, u]); setU({ user: "", password: "" }); }}>Add</button></td></tr>
      </tbody></table>
    <h4>Files</h4>
    <FileArea lab={lab} node={node} kind="ftp" notify={notify} />
  </>);
}

export function FileArea({ lab, node, kind, notify }) {
  const [files, setFiles] = useState([]);
  const pick = useRef(null);
  const load = () => api.serverFiles(lab.name, node.id, kind).then(setFiles).catch(() => {});
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 6000); return () => clearInterval(t); }, [lab.name, node.id, kind]); // eslint-disable-line
  const upload = async (fl) => {
    for (const f of fl) {
      try { await api.serverFilePut(lab.name, node.id, kind, f); } catch (e) { notify(`${f.name}: ${e.message}`, null, "bad"); }
    }
    load();
  };
  return (
    <div className="filearea" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload([...e.dataTransfer.files]); }}>
      <table className="grid compact">
        <thead><tr><th>File</th><th>Size</th><th>Modified</th><th /></tr></thead>
        <tbody>{files.map((f) => (
          <tr key={f.name}><td><a href={api.serverFileUrl(lab.name, node.id, kind, f.name)} download={f.name}>{f.name}</a></td><td className="mono">{f.size.toLocaleString()} B</td>
            <td>{new Date(f.mtime * 1000).toLocaleString()}</td>
            <td><button className="link danger" onClick={async () => { if (confirm(`Delete ${f.name}?`)) { await api.serverFileDel(lab.name, node.id, kind, f.name).catch((e) => notify(e.message, null, "bad")); load(); } }}>Delete</button></td></tr>))}
          {!files.length && <tr><td colSpan={4} className="hint">No files yet.</td></tr>}</tbody>
      </table>
      <div className="row"><button onClick={() => pick.current?.click()}>Upload files…</button><span className="hint">or drop files here (up to 20 MB each)</span>
        <input ref={pick} type="file" multiple hidden onChange={(e) => { upload([...e.target.files]); e.target.value = ""; }} /></div>
    </div>
  );
}

export function SyslogView({ lab, node, running }) {
  const [lines, setLines] = useState([]);
  const [filter, setFilter] = useState("");
  const load = () => api.syslog(lab.name, node.id).then((r) => setLines(r.lines)).catch(() => {});
  useEffect(() => { load(); const t = setInterval(() => { if (!document.hidden) load(); }, 4000); return () => clearInterval(t); }, [lab.name, node.id, running]); // eslint-disable-line
  const shown = lines.filter((l) => !filter || l.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div className="syslog">
      <div className="row"><input placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)} /><span className="hint">{shown.length} of {lines.length} messages</span><span className="grow" />
        <button className="link danger" onClick={async () => { await api.syslogClear(lab.name, node.id); load(); }}>Clear log</button></div>
      <pre className="eq syslog-pre">{shown.length ? shown.slice(-200).map((l, i) => <div key={i} className={/%[A-Z]+-[0-3]-/.test(l) ? "eq-bad" : /%[A-Z]+-[45]-/.test(l) ? "warn-t" : ""}>{l}</div>) : "No messages yet."}</pre>
    </div>
  );
}
