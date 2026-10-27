// Packet Tracer-style Config tab: a section list on the left, one page per section,
// current values read back from the device, and the IOS commands each change sends.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { catalogue, fmtSpeed } from "./hw.js";

// ---------------------------------------------------------------- helpers
export const isIp = (v) => /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(String(v || "").trim());
export const lenToMask = (p) => { const n = p === 0 ? 0 : (~0 << (32 - p)) >>> 0; return [24, 16, 8, 0].map((s) => (n >>> s) & 255).join("."); };
export function maskToLen(m) {
  if (/^\d+$/.test(String(m))) return Number(m) <= 32 ? Number(m) : null;
  if (!isIp(m)) return null;
  const b = m.split(".").map((o) => Number(o).toString(2).padStart(8, "0")).join("");
  return /^1*0*$/.test(b) ? b.indexOf("0") === -1 ? 32 : b.indexOf("0") : null;
}
const wild = (p) => lenToMask(p).split(".").map((o) => 255 - Number(o)).join(".");
const splitCidr = (c) => { const [ip, l] = String(c || "").split("/"); return { ip: ip || "", mask: l ? lenToMask(Number(l)) : "" }; };
const toCidr = (ip, mask) => (ip ? `${ip.trim()}/${maskToLen(mask)}` : "");
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function useDraft(initial) {
  const [d, setD] = useState(initial);
  const key = JSON.stringify(initial);
  useEffect(() => setD(JSON.parse(key)), [key]);
  const dirty = !same(d, initial);
  const set = (k, v) => setD((x) => ({ ...x, [k]: v }));
  return [d, set, dirty, () => setD(JSON.parse(key)), setD];
}

export function Field({ label, hint, children, wide }) {
  return (
    <div className={`fx ${wide ? "fx-wide" : ""}`}>
      <span className="fx-l">{label}</span>
      <div className="fx-c">{children}{hint && <div className="hint">{hint}</div>}</div>
    </div>
  );
}

function IpMask({ ip, mask, onIp, onMask, disabled, placeholder = "10.0.0.1" }) {
  const badIp = ip && !isIp(ip), badMask = mask && maskToLen(mask) == null;
  return (
    <span className="ipmask">
      <input className={badIp ? "bad" : ""} value={ip} disabled={disabled} placeholder={placeholder} onChange={(e) => onIp(e.target.value.trim())} aria-label="IP address" />
      <span className="hint">/</span>
      <input className={`mask ${badMask ? "bad" : ""}`} value={mask} disabled={disabled} placeholder="255.255.255.0" onChange={(e) => onMask(e.target.value.trim())} aria-label="Subnet mask" />
      {mask && maskToLen(mask) != null && <span className="hint mono">/{maskToLen(mask)}</span>}
    </span>
  );
}

export function ApplyBar({ dirty, busy, onApply, onReset, label = "Apply", disabled, note }) {
  return (
    <div className="applybar">
      {note && <span className="hint">{note}</span>}
      <span className="grow" />
      {dirty && <button onClick={onReset} disabled={busy}>Reset</button>}
      <button className="primary" disabled={!dirty || busy || disabled} onClick={onApply}>{busy ? "Applying…" : label}</button>
    </div>
  );
}

export function SideNav({ groups, active, onPick }) {
  return (
    <nav className="cfgx-nav" aria-label="Configuration sections">
      {groups.map((g) => (
        <div key={g.title}>
          <div className="cfgx-g">{g.title}{g.action}</div>
          {g.items.map((it) => (
            <button key={it.key} className={`cfgx-i ${active === it.key ? "on" : ""}`} onClick={() => onPick(it.key)} title={it.title || it.label}>
              {it.dot && <span className={`led led-${it.dot}`} />}<span className="cfgx-t">{it.label}</span>{it.sub && <span className="hint">{it.sub}</span>}
            </button>
          ))}
        </div>
      ))}
    </nav>
  );
}

function IosLog({ log, onClear }) {
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [log]);
  return (
    <div className="ioslog">
      <div className="row ioslog-h"><b>Equivalent IOS commands</b><span className="hint">exactly what was sent; the CLI tab accepts the same</span><span className="grow" />
        {log.length > 0 && <button className="link" onClick={onClear}>clear</button>}</div>
      <pre ref={ref}>{log.length ? log.map((l, i) => <div key={i} className={/^\s*%(?! Access VLAN| netlab: saved)/.test(l) ? "eq-bad" : l.includes("#") ? "" : "eq-out"}>{l}</div>)
        : "Change something and Apply: the commands appear here."}</pre>
    </div>
  );
}

const portDot = (p) => (p.admin === "down" ? "down" : p.oper === "up" ? "up" : p.oper === "down" && p.peer ? "down" : "idle");

// shared: fetch state + run IOS batches
function useDevice(lab, node, running, notify) {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.nodeState(lab.name, node.id).then((s) => { setSt(s); setErr(""); }).catch((e) => setErr(e.message)), [lab.name, node.id]);
  // props edited in the browser (e.g. a module added on the Physical tab) reach the server with the
  // autosave a moment later, so read again once that has happened
  useEffect(() => { load(); const t = setTimeout(load, 1500); return () => clearTimeout(t); }, [load, running, JSON.stringify(node.props || {})]); // eslint-disable-line
  const ios = async (cmds, okMsg) => {
    setBusy(true);
    try {
      const r = await api.ios(lab.name, node.id, cmds);
      setLog((l) => [...l, ...r.transcript.split("\n").filter((x) => x !== "")].slice(-400));
      if (!r.ok) notify(`${node.id}: ${r.errors.length} command(s) failed`, r.errors.join("\n"), "bad");
      else if (okMsg) notify(okMsg, null, "ok");
      await load();
      return r;
    } catch (e) { notify(e.message, null, "bad"); return null; } finally { setBusy(false); }
  };
  return { st, err, log, setLog, busy, setBusy, ios, load };
}

// ================================================================= ROUTER
export function RouterConfigX({ lab, node, running, notify, onRename, onUpdateNode }) {
  const dev = useDevice(lab, node, running, notify);
  const [page, setPage] = useState("settings");
  const st = dev.st;
  if (!st) return <div className="cfgx-load">{dev.err ? <p className="error">{dev.err}</p> : <p className="hint">Reading the configuration…</p>}</div>;
  const ports = st.ports || [];
  const phys = ports.filter((p) => !p.if.includes(".") && p.if.startsWith("eth"));
  const subs = ports.filter((p) => p.if.includes("."));
  const loops = ports.filter((p) => p.if === "lo" || p.if.startsWith("loop"));
  const groups = [
    { title: "Global", items: [{ key: "settings", label: "Settings" }] },
    { title: "Routing", items: [{ key: "static", label: "Static", sub: st.static.length ? String(st.static.length) : "" }, { key: "ospf", label: "OSPF", sub: st.ospf ? "on" : "" },
      { key: "bgp", label: "BGP", sub: st.bgp ? `AS ${st.bgp.asn}` : "" }, { key: "mpls", label: "MPLS / LDP", sub: st.ldp ? "on" : "" }] },
    { title: "Interfaces", items: [...phys, ...subs, ...loops].map((p) => ({ key: `if:${p.if}`, label: p.name, dot: portDot(p), sub: p.cfg?.addrs?.[0] || (p.peer ? `→ ${p.peer.node}` : ""), title: p.name })) },
  ];
  const ro = !running;
  const P = { st, dev, ro, notify, lab, node };
  return (
    <div className="cfgx">
      <SideNav groups={groups} active={page} onPick={setPage} />
      <div className="cfgx-main">
        {ro && <p className="banner">Showing the saved configuration (frr.conf). Deploy the lab and power the router on to change it.</p>}
        {page === "settings" && <RSettings {...P} onRename={onRename} onUpdateNode={onUpdateNode} />}
        {page === "static" && <RStatic {...P} />}
        {page === "ospf" && <ROspf {...P} />}
        {page === "bgp" && <RBgp {...P} />}
        {page === "mpls" && <RMpls {...P} />}
        {page.startsWith("if:") && <RIface key={page} {...P} port={ports.find((p) => p.if === page.slice(3))} onPick={setPage} />}
      </div>
      <IosLog log={dev.log} onClear={() => dev.setLog([])} />
    </div>
  );
}

function NameAndStartup({ node, running, onRename, onUpdateNode, placeholder }) {
  const [name, setName] = useState(node.id);
  const [startup, setStartup] = useState((node.props?.startup || []).join("\n"));
  useEffect(() => setName(node.id), [node.id]);
  return (<>
    <Field label="Display name" hint={running ? "Destroy the lab to rename this device." : "Its saved configuration moves with it."}>
      <span className="row"><input value={name} disabled={running || !!node.raw} onChange={(e) => setName(e.target.value.toLowerCase())} />
        <button disabled={running || name === node.id || !!node.raw} onClick={() => onRename(node.id, name)}>Rename</button></span>
    </Field>
    {!node.raw && <Field label="Startup commands" hint="Linux commands run inside the container at every deploy, one per line.">
      <textarea rows={3} value={startup} placeholder={placeholder} onChange={(e) => setStartup(e.target.value)} />
      <div><button disabled={startup === (node.props?.startup || []).join("\n")} onClick={() => onUpdateNode(node.id, { startup: startup.split("\n").map((s) => s.trim()).filter(Boolean) })}>Save startup commands</button></div>
    </Field>}
  </>);
}

function RSettings({ st, dev, ro, node, onRename, onUpdateNode }) {
  const [host, setHost] = useState(st.hostname);
  const [run, setRun] = useState("");
  const ch = catalogue()?.chassis?.[node.props?.chassis];
  return (
    <div className="page">
      <h3>Settings</h3>
      <Field label="Hostname" hint="The prompt and the name other devices see (FRR hostname).">
        <span className="row"><input value={host} disabled={ro} onChange={(e) => setHost(e.target.value.replace(/\s/g, ""))} />
          <button disabled={ro || !host || host === st.hostname || dev.busy} onClick={() => dev.ios(["configure terminal", `hostname ${host}`, "end"])}>Apply</button></span>
      </Field>
      <Field label="Hardware">{ch ? <span>{ch.label} <span className="hint">(change it on the Physical tab)</span></span> : <span className="hint">Generic router, ports are added as you cable</span>}</Field>
      <Field label="Configuration" hint="Like IOS: changes are live straight away; save them so they survive a redeploy or power cycle.">
        <span className="row">
          <button className="primary" disabled={ro || dev.busy} onClick={() => dev.ios(["write memory"], "Configuration saved")}>Save (write memory)</button>
          <button disabled={ro} onClick={async () => { const r = await dev.ios(["show running-config"]); if (r) setRun(r.transcript.split("\n").slice(1).join("\n")); }}>Show running-config</button>
          {run && <button className="link" onClick={() => setRun("")}>hide</button>}
        </span>
      </Field>
      {run && <pre className="eq runcfg">{run}</pre>}
      <NameAndStartup node={node} running={!ro} onRename={onRename} onUpdateNode={onUpdateNode}
        placeholder={"ip link add CUST-A type vrf table 10\nip link set CUST-A up"} />
    </div>
  );
}

function RIface({ st, dev, ro, port, onPick }) {
  const c = port?.cfg || { addrs: [] };
  const ospfOn = !!st.ospf;
  const init = useMemo(() => {
    const a = splitCidr(c.addrs?.[0]);
    return { on: !c.shutdown, ip: a.ip, mask: a.mask, desc: c.description || "", area: c.ospf_area ?? "", cost: c.ospf_cost ?? "", passive: !!c.ospf_passive, ldp: !!port?.ldp };
  }, [JSON.stringify(c), port?.ldp]); // eslint-disable-line
  const [d, set, dirty, reset] = useDraft(init);
  const [vlan, setVlan] = useState("");
  if (!port) return <p className="hint">That interface is gone.</p>;
  const isSub = port.if.includes("."), isLoop = port.if === "lo" || port.if.startsWith("loop");
  const bad = (d.ip && (!isIp(d.ip) || maskToLen(d.mask) == null)) || (d.cost !== "" && !/^\d+$/.test(String(d.cost)));
  const apply = () => {
    const cmds = ["configure terminal", `interface ${port.name}`];
    if (d.desc !== init.desc) cmds.push(d.desc ? `description ${d.desc}` : "no description");
    if (d.ip !== init.ip || d.mask !== init.mask) { if (init.ip) cmds.push("no ip address"); if (d.ip) cmds.push(`ip address ${d.ip} ${lenToMask(maskToLen(d.mask))}`); }
    if (String(d.area) !== String(init.area)) cmds.push(d.area !== "" ? `ip ospf 1 area ${d.area}` : `no ip ospf 1 area ${init.area}`);
    if (String(d.cost) !== String(init.cost)) cmds.push(d.cost !== "" ? `ip ospf cost ${d.cost}` : "no ip ospf cost");
    if (d.ldp !== init.ldp) cmds.push(d.ldp ? "mpls ip" : "no mpls ip");
    if (d.on !== init.on) cmds.push(d.on ? "no shutdown" : "shutdown");
    if (d.passive !== init.passive) cmds.push("exit", "router ospf 1", `${d.passive ? "" : "no "}passive-interface ${port.name}`);
    cmds.push("end");
    dev.ios(cmds);
  };
  return (
    <div className="page">
      <h3>{port.name} {isSub && <span className="hint">VLAN {port.vlan}</span>}</h3>
      <div className="statline">
        <span className={`led led-${portDot(port)}`} />
        <span>{port.oper === "up" ? "up" : port.admin === "down" ? "administratively down" : port.peer ? "down" : "not cabled"}</span>
        {port.peer && <span>· to <b>{port.peer.node}</b> {port.peer.port} ({port.peer.media})</span>}
        {port.kbit !== undefined && port.type && <span>· {fmtSpeed(port.kbit)}</span>}
        {port.mac && <span className="mono">· {port.mac}</span>}
      </div>
      {!isLoop && <Field label="Port status"><label className="chk"><input type="checkbox" checked={d.on} disabled={ro} onChange={(e) => set("on", e.target.checked)} /> On</label></Field>}
      <Field label="IPv4 address" hint={c.addrs?.length > 1 ? `Also: ${c.addrs.slice(1).join(", ")} (secondary, edit in the CLI)` : isLoop ? "Use a /32 (255.255.255.255) for router IDs and LDP." : null}>
        <IpMask ip={d.ip} mask={d.mask} disabled={ro} onIp={(v) => set("ip", v)} onMask={(v) => set("mask", v)} />
      </Field>
      <Field label="Description"><input className="wide" value={d.desc} disabled={ro} onChange={(e) => set("desc", e.target.value)} /></Field>
      <h4>OSPF on this interface</h4>
      <Field label="Area" hint={!ospfOn && d.area !== "" ? "OSPF isn't enabled yet: turn it on in Routing → OSPF too." : "Blank = not in OSPF. Same as 'ip ospf 1 area N'."}>
        <input className="narrow" value={d.area} disabled={ro} placeholder="0" onChange={(e) => set("area", e.target.value.trim())} /></Field>
      <Field label="Cost"><input className="narrow" value={d.cost} disabled={ro} placeholder="auto" onChange={(e) => set("cost", e.target.value.trim())} /></Field>
      <Field label="Passive" hint="Advertise the network but don't form neighbours (LANs, loopbacks)."><input type="checkbox" checked={d.passive} disabled={ro} onChange={(e) => set("passive", e.target.checked)} /></Field>
      {!isLoop && <Field label="MPLS (LDP)" hint="Same as 'mpls ip'. Needs Loopback0 with a /32."><input type="checkbox" checked={d.ldp} disabled={ro} onChange={(e) => set("ldp", e.target.checked)} /></Field>}
      <ApplyBar dirty={dirty} busy={dev.busy} disabled={ro || bad} onApply={apply} onReset={reset} />
      {!isSub && !isLoop && (
        <div className="subbox">
          <h4>Subinterfaces (router-on-a-stick)</h4>
          <span className="row">VLAN <input className="narrow" value={vlan} disabled={ro} placeholder="10" onChange={(e) => setVlan(e.target.value.replace(/\D/g, ""))} />
            <button disabled={ro || !vlan || Number(vlan) < 1 || Number(vlan) > 4094} onClick={async () => {
              await dev.ios(["configure terminal", `interface ${port.name}.${vlan}`, `encapsulation dot1Q ${vlan}`, "end"]); onPick(`if:${port.if}.${vlan}`); setVlan("");
            }}>Add {port.name}.{vlan || "N"}</button></span>
          <p className="hint">Cable this port to a switch trunk, then give the subinterface an address.</p>
        </div>
      )}
      {(isSub || (isLoop && port.if !== "lo")) && (
        <p><button className="link danger" disabled={ro} onClick={async () => { await dev.ios(["configure terminal", `no interface ${port.name}`, "end"]); onPick("settings"); }}>Remove {port.name}</button></p>
      )}
      {isLoop && port.if === "lo" && (
        <p className="row"><button disabled={ro} onClick={async () => {
          const used = st.ports.filter((p) => p.if.startsWith("loop")).map((p) => Number(p.if.slice(4)));
          let n = 1; while (used.includes(n)) n++;
          await dev.ios(["configure terminal", `interface Loopback${n}`, "end"]); onPick(`if:loop${n}`);
        }}>Add another loopback</button></p>
      )}
    </div>
  );
}

function RStatic({ st, dev, ro }) {
  const blank = { net: "", mask: "", via: "", dist: "" };
  const [f, setF] = useState(blank);
  const ifs = st.ports.filter((p) => p.if.startsWith("eth"));
  const okNet = isIp(f.net) && maskToLen(f.mask) != null && (isIp(f.via) || ifs.some((p) => p.name === f.via) || f.via === "Null0") && (!f.dist || /^\d+$/.test(f.dist));
  const viaLabel = (v) => (isIp(v) ? v : (st.ports.find((p) => p.if === v)?.name || v));
  return (
    <div className="page">
      <h3>Static routes</h3>
      <table className="grid compact">
        <thead><tr><th>Network</th><th>Mask</th><th>Next hop / exit</th><th>AD</th><th /></tr></thead>
        <tbody>{st.static.map((r, i) => {
          const [net, len] = r.prefix.split("/");
          return (<tr key={i}><td className="mono">{net}</td><td className="mono">{lenToMask(Number(len))}</td><td className="mono">{viaLabel(r.via)}{r.vrf && <span className="hint"> vrf {r.vrf}</span>}</td><td>{r.distance ?? 1}</td>
            <td><button className="link danger" disabled={ro || !!r.vrf} onClick={() => dev.ios(["configure terminal", `no ip route ${net} ${lenToMask(Number(len))} ${viaLabel(r.via)}`, "end"])}>Remove</button></td></tr>);
        })}
          {!st.static.length && <tr><td colSpan={5} className="hint">No static routes.</td></tr>}</tbody>
      </table>
      <h4>Add a route</h4>
      <div className="formrow">
        <label>Network<input value={f.net} placeholder="0.0.0.0" className={f.net && !isIp(f.net) ? "bad" : ""} onChange={(e) => setF({ ...f, net: e.target.value.trim() })} /></label>
        <label>Mask<input value={f.mask} placeholder="0.0.0.0" className={f.mask && maskToLen(f.mask) == null ? "bad" : ""} onChange={(e) => setF({ ...f, mask: e.target.value.trim() })} /></label>
        <label>Next hop<input list={`ifs-${st.id}`} value={f.via} placeholder="10.0.0.2 or interface" onChange={(e) => setF({ ...f, via: e.target.value.trim() })} />
          <datalist id={`ifs-${st.id}`}>{ifs.map((p) => <option key={p.if} value={p.name} />)}<option value="Null0" /></datalist></label>
        <label>AD<input className="narrow" value={f.dist} placeholder="1" onChange={(e) => setF({ ...f, dist: e.target.value.replace(/\D/g, "") })} /></label>
        <button className="primary" disabled={ro || !okNet || dev.busy} onClick={async () => { await dev.ios(["configure terminal", `ip route ${f.net} ${f.mask} ${f.via}${f.dist ? ` ${f.dist}` : ""}`, "end"]); setF(blank); }}>Add</button>
      </div>
      <p className="hint">Default route: network 0.0.0.0, mask 0.0.0.0.</p>
    </div>
  );
}

function ROspf({ st, dev, ro }) {
  const o = st.ospf;
  const init = useMemo(() => ({ rid: o?.router_id || "", conn: !!o?.redistribute.includes("connected"), stat: !!o?.redistribute.includes("static"),
    def: o?.default_originate || "" }), [JSON.stringify(o)]); // eslint-disable-line
  const [d, set, dirty, reset] = useDraft(init);
  const ifs = st.ports.filter((p) => p.cfg?.addrs?.length);
  const [ifArea, setIfArea] = useState({});
  const lo = st.ports.find((p) => p.if === "lo")?.cfg?.addrs?.find((a) => a.endsWith("/32"));
  if (!o) {
    return (
      <div className="page">
        <h3>OSPF</h3>
        <p>OSPF isn't running. Turning it on creates <span className="mono">router ospf 1</span>{lo ? <> with router ID <b>{lo.split("/")[0]}</b> (Loopback0)</> : ""}.</p>
        {!lo && <p className="hint">Tip: give Loopback0 a /32 first and use it as the router ID; otherwise FRR picks the highest address, which may change.</p>}
        <button className="primary" disabled={ro || dev.busy} onClick={() => dev.ios(["configure terminal", "router ospf 1", ...(lo ? [`router-id ${lo.split("/")[0]}`] : []), "end"])}>Turn on OSPF</button>
      </div>
    );
  }
  const apply = () => {
    const c = ["configure terminal", "router ospf 1"];
    if (d.rid !== init.rid) c.push(d.rid ? `router-id ${d.rid}` : "no router-id");
    if (d.conn !== init.conn) c.push(`${d.conn ? "" : "no "}redistribute connected`);
    if (d.stat !== init.stat) c.push(`${d.stat ? "" : "no "}redistribute static`);
    if (d.def !== init.def) c.push(d.def ? `default-information originate${d.def === "always" ? " always" : ""}` : "no default-information originate");
    c.push("end");
    if (d.rid !== init.rid) c.push("clear ip ospf process");
    dev.ios(c);
  };
  return (
    <div className="page">
      <h3>OSPF <span className="hint">process 1</span></h3>
      <Field label="Router ID" hint="Usually Loopback0's address. Changing it restarts OSPF (clear ip ospf process).">
        <span className="row"><input className={d.rid && !isIp(d.rid) ? "bad" : ""} value={d.rid} disabled={ro} placeholder={lo ? lo.split("/")[0] : "1.1.1.1"} onChange={(e) => set("rid", e.target.value.trim())} />
          {lo && d.rid !== lo.split("/")[0] && <button className="link" onClick={() => set("rid", lo.split("/")[0])}>use Loopback0</button>}</span></Field>
      <Field label="Redistribute"><span className="row">
        <label className="chk"><input type="checkbox" checked={d.conn} disabled={ro} onChange={(e) => set("conn", e.target.checked)} /> connected</label>
        <label className="chk"><input type="checkbox" checked={d.stat} disabled={ro} onChange={(e) => set("stat", e.target.checked)} /> static</label></span></Field>
      <Field label="Default route" hint="Advertise 0.0.0.0/0 into OSPF (default-information originate).">
        <select value={d.def} disabled={ro} onChange={(e) => set("def", e.target.value)}><option value="">no</option><option value="yes">if this router has one</option><option value="always">always</option></select></Field>
      <ApplyBar dirty={dirty} busy={dev.busy} disabled={ro || (d.rid && !isIp(d.rid))} onApply={apply} onReset={reset} />
      <h4>Interfaces in OSPF</h4>
      <p className="hint">Per-interface areas (<span className="mono">ip ospf 1 area N</span>). Edit cost and passive on each interface page.</p>
      <table className="grid compact">
        <thead><tr><th>Interface</th><th>Address</th><th>Area</th><th>Cost</th><th>Passive</th><th /></tr></thead>
        <tbody>{ifs.map((p) => {
          const cur = p.cfg.ospf_area ?? "", val = ifArea[p.if] ?? cur;
          return (<tr key={p.if}><td>{p.name}</td><td className="mono">{p.cfg.addrs[0]}</td>
            <td><input className="narrow" value={val} disabled={ro} placeholder="—" onChange={(e) => setIfArea({ ...ifArea, [p.if]: e.target.value.trim() })} /></td>
            <td>{p.cfg.ospf_cost ?? <span className="hint">auto</span>}</td><td>{p.cfg.ospf_passive ? "yes" : ""}</td>
            <td>{String(val) !== String(cur) && <button className="link" disabled={ro || dev.busy} onClick={async () => {
              await dev.ios(["configure terminal", `interface ${p.name}`, val !== "" ? `ip ospf 1 area ${val}` : `no ip ospf 1 area ${cur}`, "end"]);
              setIfArea((x) => { const n = { ...x }; delete n[p.if]; return n; });
            }}>apply</button>}</td></tr>);
        })}
          {!ifs.length && <tr><td colSpan={6} className="hint">Give interfaces addresses first.</td></tr>}</tbody>
      </table>
      {o.networks.length > 0 && (<>
        <h4>Network statements</h4>
        <p className="hint">FRR doesn't mix these with per-interface areas; remove them to use the table above.</p>
        <table className="grid compact"><tbody>{o.networks.map((n) => {
          const [a, l] = n.prefix.split("/");
          return (<tr key={n.prefix}><td className="mono">network {a} {wild(Number(l))} area {n.area}</td>
            <td><button className="link danger" disabled={ro} onClick={() => dev.ios(["configure terminal", "router ospf 1", `no network ${a} ${wild(Number(l))} area ${n.area}`, "end"])}>Remove</button></td></tr>);
        })}</tbody></table>
      </>)}
      <p className="row"><button className="link danger" disabled={ro} onClick={() => confirm("Remove OSPF (no router ospf 1)?") && dev.ios(["configure terminal", "no router ospf 1", "end"])}>Turn OSPF off</button></p>
    </div>
  );
}

function RBgp({ st, dev, ro }) {
  const b = st.bgp;
  const [asn, setAsn] = useState("");
  const [nb, setNb] = useState({ ip: "", as: "", src: "", desc: "" });
  const [net, setNet] = useState({ ip: "", mask: "" });
  const [rid, setRid] = useState(b?.router_id || "");
  useEffect(() => setRid(b?.router_id || ""), [b?.router_id]);
  const lo = st.ports.find((p) => p.if === "lo")?.cfg?.addrs?.find((a) => a.endsWith("/32"));
  if (!b) {
    return (
      <div className="page"><h3>BGP</h3>
        <Field label="Local AS"><span className="row"><input className="narrow" value={asn} placeholder="65001" onChange={(e) => setAsn(e.target.value.replace(/\D/g, ""))} />
          <button className="primary" disabled={ro || !asn || dev.busy} onClick={() => dev.ios(["configure terminal", `router bgp ${asn}`, ...(lo ? [`bgp router-id ${lo.split("/")[0]}`] : []), "end"])}>Turn on BGP</button></span></Field>
      </div>
    );
  }
  const R = (...c) => dev.ios(["configure terminal", `router bgp ${b.asn}`, ...c, "end"]);
  const srcs = st.ports.filter((p) => p.cfg?.addrs?.length);
  return (
    <div className="page">
      <h3>BGP <span className="hint">AS {b.asn}</span></h3>
      <Field label="Router ID"><span className="row"><input value={rid} disabled={ro} placeholder={lo ? lo.split("/")[0] : ""} onChange={(e) => setRid(e.target.value.trim())} />
        <button disabled={ro || rid === b.router_id || (rid && !isIp(rid))} onClick={() => R(rid ? `bgp router-id ${rid}` : "no bgp router-id")}>Apply</button></span></Field>
      <h4>Neighbours</h4>
      <table className="grid compact">
        <thead><tr><th>Address</th><th>Remote AS</th><th>Type</th><th>Update source</th><th>Description</th><th /></tr></thead>
        <tbody>{b.neighbors.map((n) => (
          <tr key={n.ip}><td className="mono">{n.ip}</td><td>{n.remote_as}</td><td>{String(n.remote_as) === String(b.asn) || n.remote_as === "internal" ? "iBGP" : "eBGP"}</td>
            <td>{n.update_source ? (st.ports.find((p) => p.if === n.update_source)?.name || n.update_source) : <span className="hint">—</span>}</td><td>{n.description}</td>
            <td><button className="link danger" disabled={ro} onClick={() => R(`no neighbor ${n.ip}`)}>Remove</button></td></tr>))}
          {!b.neighbors.length && <tr><td colSpan={6} className="hint">No neighbours.</td></tr>}</tbody>
      </table>
      <div className="formrow">
        <label>Address<input value={nb.ip} className={nb.ip && !isIp(nb.ip) ? "bad" : ""} placeholder="10.0.0.2" onChange={(e) => setNb({ ...nb, ip: e.target.value.trim() })} /></label>
        <label>Remote AS<input className="narrow" value={nb.as} placeholder="65002" onChange={(e) => setNb({ ...nb, as: e.target.value.replace(/\D/g, "") })} /></label>
        <label>Update source<select value={nb.src} onChange={(e) => setNb({ ...nb, src: e.target.value })}><option value="">(outgoing interface)</option>{srcs.map((p) => <option key={p.if} value={p.name}>{p.name}</option>)}</select></label>
        <label>Description<input value={nb.desc} onChange={(e) => setNb({ ...nb, desc: e.target.value })} /></label>
        <button className="primary" disabled={ro || !isIp(nb.ip) || !nb.as || dev.busy} onClick={async () => {
          await R(`neighbor ${nb.ip} remote-as ${nb.as}`, ...(nb.src ? [`neighbor ${nb.ip} update-source ${nb.src}`] : []), ...(nb.desc ? [`neighbor ${nb.ip} description ${nb.desc}`] : []));
          setNb({ ip: "", as: "", src: "", desc: "" });
        }}>Add</button>
      </div>
      <p className="hint">iBGP between loopbacks: set the update source to Loopback0 on both ends, and make sure each loopback is reachable (OSPF).</p>
      <h4>Networks advertised</h4>
      <table className="grid compact"><tbody>{b.networks.map((n) => {
        const [a, l] = n.split("/");
        return <tr key={n}><td className="mono">network {a} mask {lenToMask(Number(l))}</td><td><button className="link danger" disabled={ro} onClick={() => R(`no network ${a} mask ${lenToMask(Number(l))}`)}>Remove</button></td></tr>;
      })}{!b.networks.length && <tr><td className="hint">None.</td></tr>}</tbody></table>
      <div className="formrow">
        <label>Network<IpMask ip={net.ip} mask={net.mask} onIp={(v) => setNet({ ...net, ip: v })} onMask={(v) => setNet({ ...net, mask: v })} placeholder="192.0.2.0" /></label>
        <button disabled={ro || !isIp(net.ip) || maskToLen(net.mask) == null} onClick={async () => { await R(`network ${net.ip} mask ${lenToMask(maskToLen(net.mask))}`); setNet({ ip: "", mask: "" }); }}>Add</button>
      </div>
      <p className="hint">The exact prefix must be in the routing table for BGP to advertise it.</p>
      <h4>Redistribute</h4>
      <span className="row">{["connected", "static", "ospf"].map((k) => (
        <label key={k} className="chk"><input type="checkbox" disabled={ro || dev.busy} checked={b.redistribute.includes(k)} onChange={(e) => R(`${e.target.checked ? "" : "no "}redistribute ${k}`)} /> {k}</label>))}</span>
      <p className="row"><button className="link danger" disabled={ro} onClick={() => confirm(`Remove BGP AS ${b.asn}?`) && dev.ios(["configure terminal", `no router bgp ${b.asn}`, "end"])}>Turn BGP off</button></p>
    </div>
  );
}

function RMpls({ st, dev, ro }) {
  const lo = st.ports.find((p) => p.if === "lo")?.cfg?.addrs?.find((a) => a.endsWith("/32"));
  const ifs = st.ports.filter((p) => p.if.startsWith("eth") && p.cfg?.addrs?.length);
  return (
    <div className="page">
      <h3>MPLS / LDP</h3>
      <Field label="LDP router ID">{st.ldp ? <span className="mono">{st.ldp.router_id}</span> : <span className="hint">not running</span>}</Field>
      {!lo && <p className="banner">Give Loopback0 a /32 address first: LDP uses it as router ID and transport address.</p>}
      <table className="grid compact">
        <thead><tr><th>Interface</th><th>Address</th><th>To</th><th>LDP</th></tr></thead>
        <tbody>{ifs.map((p) => (
          <tr key={p.if}><td>{p.name}</td><td className="mono">{p.cfg.addrs[0]}</td><td>{p.peer ? `${p.peer.node} ${p.peer.port}` : ""}</td>
            <td><input type="checkbox" disabled={ro || !lo || dev.busy} checked={!!p.ldp} onChange={(e) => dev.ios(["configure terminal", `interface ${p.name}`, e.target.checked ? "mpls ip" : "no mpls ip", "end"])} /></td></tr>))}
          {!ifs.length && <tr><td colSpan={4} className="hint">Address the core-facing interfaces first.</td></tr>}</tbody>
      </table>
      <p className="hint">Enable LDP on core links only (towards other MPLS routers), with an IGP (OSPF) carrying the loopbacks.</p>
    </div>
  );
}

// ================================================================= SWITCH
export function SwitchConfigX({ lab, node, running, notify, onRename, onUpdateNode }) {
  const dev = useDevice(lab, node, running, notify);
  const [page, setPage] = useState("settings");
  const st = dev.st;
  if (!st) return <div className="cfgx-load">{dev.err ? <p className="error">{dev.err}</p> : <p className="hint">Reading the configuration…</p>}</div>;
  const p = node.props || {};
  const ovs = p.engine === "ovs";
  const chans = [...new Set(Object.values(p.ports || {}).map((c) => c?.channel).filter(Boolean))].sort((a, b) => a - b);
  const groups = [
    { title: "Global", items: [{ key: "settings", label: "Settings" }, { key: "stp", label: "Spanning tree", sub: p.stp ? (ovs ? "RSTP" : "STP") : "off" }, { key: "mgmt", label: "Management (SVI)", sub: p.svi?.ip ? p.svi.ip.split("/")[0] : "" }] },
    { title: "Switching", items: [{ key: "vlans", label: "VLAN database", sub: String((p.vlans || [{}]).length) }, ...(ovs ? [{ key: "po", label: "Port-channels", sub: chans.length ? String(chans.length) : "" }] : [])] },
    { title: "Interfaces", items: st.ports.map((x) => {
      const c = p.ports?.[x.if] || {};
      return { key: `if:${x.if}`, label: x.name, dot: c.shutdown ? "down" : portDot(x), sub: c.channel ? `Po${c.channel}` : c.mode === "trunk" ? "trunk" : `v${c.vlan || 1}` };
    }) },
  ];
  const P = { st, dev, node, p, ovs, running, notify, onUpdateNode };
  return (
    <div className="cfgx">
      <SideNav groups={groups} active={page} onPick={setPage} />
      <div className="cfgx-main">
        {!running && <p className="banner">Not running: changes are saved and applied when the switch starts.</p>}
        {page === "settings" && <SwSettings {...P} onRename={onRename} />}
        {page === "stp" && <SwStp {...P} />}
        {page === "mgmt" && <SwMgmt {...P} />}
        {page === "vlans" && <SwVlans {...P} />}
        {page === "po" && <SwChannels {...P} chans={chans} onPick={setPage} />}
        {page.startsWith("if:") && <SwPort key={page} {...P} port={st.ports.find((x) => x.if === page.slice(3))} />}
      </div>
      <IosLog log={dev.log} onClear={() => dev.setLog([])} />
    </div>
  );
}

const vlanIds = (p) => [...new Set([1, ...(p.vlans || []).map((v) => Number(v.id))])].sort((a, b) => a - b);
const vlanName = (p, id) => (p.vlans || []).find((v) => Number(v.id) === Number(id))?.name;

function SwSettings({ node, p, dev, running, onRename, onUpdateNode, notify }) {
  const [host, setHost] = useState(p.hostname || node.id);
  return (
    <div className="page">
      <h3>Settings</h3>
      <Field label="Hostname"><span className="row"><input value={host} onChange={(e) => setHost(e.target.value.replace(/\s/g, ""))} />
        <button disabled={!host || host === (p.hostname || node.id) || dev.busy} onClick={() => dev.ios(["configure terminal", `hostname ${host}`, "end"])}>Apply</button></span></Field>
      <Field label="Switch engine" hint={running ? "Power the switch off (Physical tab) or destroy the lab to change engine." : "Open vSwitch adds RSTP and LACP port-channels; the Linux bridge is lighter (802.1D STP only)."}>
        <select value={p.engine || "bridge"} disabled={running} onChange={(e) => { onUpdateNode(node.id, { engine: e.target.value === "ovs" ? "ovs" : null }); notify(`Engine set to ${e.target.value === "ovs" ? "Open vSwitch" : "Linux bridge"}`, "Takes effect when the switch starts.", "ok"); }}>
          <option value="bridge">Linux bridge (802.1D)</option><option value="ovs">Open vSwitch (RSTP, LACP)</option></select></Field>
      <Field label="Configuration" hint="Switch settings are saved as you apply them (no separate write memory needed).">
        <button onClick={async () => { await dev.ios(["show running-config"]); }}>Show running-config</button></Field>
      <NameAndStartup node={node} running={running} onRename={onRename} onUpdateNode={onUpdateNode} placeholder="ip link set eth1 mtu 1500" />
    </div>
  );
}

function SwStp({ p, ovs, dev }) {
  const init = { on: !!p.stp, prio: Number(p.stp_priority || 32768) };
  const [d, set, dirty, reset] = useDraft(init);
  const apply = () => {
    const c = ["configure terminal"];
    if (d.on !== init.on) c.push(d.on ? `spanning-tree mode ${ovs ? "rapid-pvst" : "pvst"}` : "no spanning-tree vlan 1-4094");
    if (d.on && d.prio !== init.prio) c.push(`spanning-tree vlan 1-4094 priority ${d.prio}`);
    c.push("end");
    dev.ios(c);
  };
  return (
    <div className="page">
      <h3>Spanning tree <span className="hint">{ovs ? "RSTP (Open vSwitch)" : "802.1D (Linux bridge)"}</span></h3>
      <Field label="Enabled" hint={ovs ? "Converges in a couple of seconds." : "Ports take ~30 s to forward after a change. Switch the engine to Open vSwitch for RSTP."}>
        <input type="checkbox" checked={d.on} onChange={(e) => set("on", e.target.checked)} /></Field>
      <Field label="Bridge priority" hint="Lowest wins root. One instance covers all VLANs. Set the core switch to 4096.">
        <select value={d.prio} disabled={!d.on} onChange={(e) => set("prio", Number(e.target.value))}>
          {Array.from({ length: 16 }, (_, i) => i * 4096).map((v) => <option key={v} value={v}>{v}{v === 32768 ? " (default)" : v === 0 ? " (always root)" : ""}</option>)}</select></Field>
      <ApplyBar dirty={dirty} busy={dev.busy} onApply={apply} onReset={reset} />
      <p><button disabled={dev.busy} onClick={() => dev.ios(["show spanning-tree"])}>Show spanning-tree</button></p>
    </div>
  );
}

function SwMgmt({ p, dev }) {
  const s = p.svi || {};
  const init = { vlan: Number(s.vlan || 1), ...splitCidr(s.ip), gw: s.gateway || "" };
  const [d, set, dirty, reset] = useDraft(init);
  const bad = (d.ip && (!isIp(d.ip) || maskToLen(d.mask) == null)) || (d.gw && !isIp(d.gw));
  const apply = () => {
    const c = ["configure terminal"];
    if (d.ip !== init.ip || d.mask !== init.mask || d.vlan !== init.vlan) {
      c.push(`interface Vlan${d.vlan}`, d.ip ? `ip address ${d.ip} ${lenToMask(maskToLen(d.mask))}` : "no ip address", "exit");
    }
    if (d.gw !== init.gw) c.push(d.gw ? `ip default-gateway ${d.gw}` : "no ip default-gateway");
    c.push("end");
    dev.ios(c);
  };
  return (
    <div className="page">
      <h3>Management interface</h3>
      <p className="hint">A layer 2 switch has one management address (an SVI) for pinging and managing it. It doesn't route between VLANs.</p>
      <Field label="VLAN"><select value={d.vlan} onChange={(e) => set("vlan", Number(e.target.value))}>{vlanIds(p).map((v) => <option key={v} value={v}>{v} {vlanName(p, v) || ""}</option>)}</select></Field>
      <Field label="IPv4 address"><IpMask ip={d.ip} mask={d.mask} onIp={(v) => set("ip", v)} onMask={(v) => set("mask", v)} placeholder="192.168.10.2" /></Field>
      <Field label="Default gateway"><input className={d.gw && !isIp(d.gw) ? "bad" : ""} value={d.gw} placeholder="192.168.10.1" onChange={(e) => set("gw", e.target.value.trim())} /></Field>
      <ApplyBar dirty={dirty} busy={dev.busy} disabled={bad} onApply={apply} onReset={reset} />
    </div>
  );
}

function SwVlans({ p, dev, st }) {
  const [nv, setNv] = useState({ id: "", name: "" });
  const [ren, setRen] = useState({});
  const ports = p.ports || {};
  const members = (id) => st.ports.filter((x) => { const c = ports[x.if] || {}; return !c.channel && (c.mode || "access") === "access" && Number(c.vlan || 1) === id; }).map((x) => x.short);
  const vl = [{ id: 1, name: "default" }, ...(p.vlans || []).filter((v) => Number(v.id) !== 1)].sort((a, b) => a.id - b.id);
  return (
    <div className="page">
      <h3>VLAN database</h3>
      <table className="grid compact">
        <thead><tr><th>VLAN</th><th>Name</th><th>Access ports</th><th /></tr></thead>
        <tbody>{vl.map((v) => (
          <tr key={v.id}><td className="mono">{v.id}</td>
            <td>{v.id === 1 ? v.name : <span className="row"><input value={ren[v.id] ?? v.name} onChange={(e) => setRen({ ...ren, [v.id]: e.target.value.replace(/\s/g, "_") })} />
              {ren[v.id] !== undefined && ren[v.id] !== v.name && <button className="link" onClick={async () => { await dev.ios(["configure terminal", `vlan ${v.id}`, `name ${ren[v.id]}`, "end"]); setRen({}); }}>save</button>}</span>}</td>
            <td className="hint">{members(v.id).join(", ")}</td>
            <td>{v.id !== 1 && <button className="link danger" onClick={() => dev.ios(["configure terminal", `no vlan ${v.id}`, "end"])}>Remove</button>}</td></tr>))}</tbody>
      </table>
      <div className="formrow">
        <label>VLAN ID<input className="narrow" value={nv.id} placeholder="10" onChange={(e) => setNv({ ...nv, id: e.target.value.replace(/\D/g, "") })} /></label>
        <label>Name<input value={nv.name} placeholder="USERS" onChange={(e) => setNv({ ...nv, name: e.target.value.replace(/\s/g, "_") })} /></label>
        <button className="primary" disabled={!nv.id || Number(nv.id) < 2 || Number(nv.id) > 4094 || dev.busy} onClick={async () => {
          await dev.ios(["configure terminal", `vlan ${nv.id}`, ...(nv.name ? [`name ${nv.name}`] : []), "end"]); setNv({ id: "", name: "" });
        }}>Add VLAN</button>
      </div>
      <p><button disabled={dev.busy} onClick={() => dev.ios(["show vlan brief"])}>Show vlan brief</button></p>
    </div>
  );
}

function VlanPicker({ p, value, onChange, disabled }) {
  const all = !value;
  const set = new Set(all ? [] : String(value).split(",").flatMap((x) => { const [a, b] = x.split("-").map(Number); return b ? Array.from({ length: b - a + 1 }, (_, i) => a + i) : [a]; }));
  const ids = vlanIds(p);
  const emit = (s) => onChange([...s].sort((a, b) => a - b).join(","));
  return (
    <div className="vpick">
      <label className="chk"><input type="checkbox" checked={all} disabled={disabled} onChange={(e) => onChange(e.target.checked ? "" : ids.join(","))} /> all VLANs</label>
      {!all && ids.map((v) => (
        <label key={v} className="chk"><input type="checkbox" disabled={disabled} checked={set.has(v)} onChange={(e) => { const n = new Set(set); e.target.checked ? n.add(v) : n.delete(v); emit(n); }} /> {v}{vlanName(p, v) ? ` ${vlanName(p, v)}` : ""}</label>))}
      {!all && [...set].filter((v) => !ids.includes(v)).length > 0 && <span className="hint">also {[...set].filter((v) => !ids.includes(v)).join(", ")}</span>}
    </div>
  );
}

function SwPort({ p, port, dev, ovs }) {
  const c = (port && p.ports?.[port.if]) || {};
  const init = { on: !c.shutdown, desc: c.description || "", mode: c.mode || "access", vlan: Number(c.vlan || 1), native: Number(c.native || 1), allowed: c.allowed || "",
    portfast: !!c.portfast, channel: c.channel ? String(c.channel) : "", cmode: ((p.channels || {})[String(c.channel)] || {}).mode || "active" };
  const [d, set, dirty, reset] = useDraft(init);
  if (!port) return <p className="hint">That port is gone.</p>;
  const apply = () => {
    const x = ["configure terminal", `interface ${port.name}`];
    if (d.desc !== init.desc) x.push(d.desc ? `description ${d.desc}` : "no description");
    if (d.channel !== init.channel || (d.channel && d.cmode !== init.cmode)) x.push(d.channel ? `channel-group ${d.channel} mode ${d.cmode}` : "no channel-group");
    if (!d.channel) {
      if (d.mode !== init.mode) x.push(`switchport mode ${d.mode}`);
      if (d.mode === "access" && d.vlan !== init.vlan) x.push(`switchport access vlan ${d.vlan}`);
      if (d.mode === "trunk" && d.native !== init.native) x.push(`switchport trunk native vlan ${d.native}`);
      if (d.mode === "trunk" && d.allowed !== init.allowed) x.push(`switchport trunk allowed vlan ${d.allowed || "all"}`);
      if (d.portfast !== init.portfast) x.push(`${d.portfast ? "" : "no "}spanning-tree portfast`);
    }
    if (d.on !== init.on) x.push(d.on ? "no shutdown" : "shutdown");
    x.push("end");
    dev.ios(x);
  };
  const ids = vlanIds(p);
  return (
    <div className="page">
      <h3>{port.name}</h3>
      <div className="statline">
        <span className={`led led-${c.shutdown ? "down" : portDot(port)}`} />
        <span>{c.shutdown ? "administratively down" : port.oper === "up" ? "connected" : port.peer ? "down" : "not connected"}</span>
        {port.peer && <span>· to <b>{port.peer.node}</b> {port.peer.port} ({port.peer.media})</span>}
        {port.type && <span>· {fmtSpeed(port.kbit)}</span>}
      </div>
      <Field label="Port status"><label className="chk"><input type="checkbox" checked={d.on} onChange={(e) => set("on", e.target.checked)} /> On</label></Field>
      <Field label="Description"><input className="wide" value={d.desc} onChange={(e) => set("desc", e.target.value)} /></Field>
      {ovs && <Field label="Port-channel" hint="Bundle this port (LACP active/passive, or static 'on'). VLAN settings then come from the port-channel.">
        <span className="row"><select value={d.channel} onChange={(e) => set("channel", e.target.value)}><option value="">none</option>{Array.from({ length: 8 }, (_, i) => String(i + 1)).map((n) => <option key={n} value={n}>Po{n}</option>)}</select>
          {d.channel && <select value={d.cmode} onChange={(e) => set("cmode", e.target.value)}><option value="active">LACP active</option><option value="passive">LACP passive</option><option value="on">static (on)</option></select>}</span></Field>}
      {!d.channel && (<>
        <Field label="Mode"><div className="seg">{["access", "trunk"].map((m) => <button key={m} className={d.mode === m ? "on" : ""} onClick={() => set("mode", m)}>{m}</button>)}</div></Field>
        {d.mode === "access" && <Field label="Access VLAN"><select value={d.vlan} onChange={(e) => set("vlan", Number(e.target.value))}>{ids.map((v) => <option key={v} value={v}>{v} {vlanName(p, v) || ""}</option>)}</select></Field>}
        {d.mode === "trunk" && (<>
          <Field label="Native VLAN" hint="Untagged frames on the trunk belong to this VLAN."><select value={d.native} onChange={(e) => set("native", Number(e.target.value))}>{ids.map((v) => <option key={v} value={v}>{v} {vlanName(p, v) || ""}</option>)}</select></Field>
          <Field label="Allowed VLANs"><VlanPicker p={p} value={d.allowed} onChange={(v) => set("allowed", v)} /></Field>
        </>)}
        {d.mode === "access" && <Field label="PortFast" hint="Stored for the config; the kernel bridge / OVS RSTP edge detection decide the real behaviour."><input type="checkbox" checked={d.portfast} onChange={(e) => set("portfast", e.target.checked)} /></Field>}
      </>)}
      <ApplyBar dirty={dirty} busy={dev.busy} onApply={apply} onReset={reset} />
    </div>
  );
}

function SwChannels({ p, chans, dev, st, onPick }) {
  return (
    <div className="page">
      <h3>Port-channels</h3>
      {!chans.length && <p className="hint">None yet. Open a port and pick a port-channel to bundle it (two or more ports per channel).</p>}
      {chans.map((n) => {
        const ms = st.ports.filter((x) => Number(p.ports?.[x.if]?.channel) === n);
        const c = p.ports?.[`po${n}`] || {};
        return (
          <div key={n} className="subbox">
            <h4>Port-channel{n} <span className="hint">{((p.channels || {})[String(n)] || {}).mode || "active"} · {c.mode || "access"}{c.mode === "trunk" ? ` · allowed ${c.allowed || "all"}` : ` · VLAN ${c.vlan || 1}`}</span></h4>
            <p>{ms.map((m) => <button key={m.if} className="link" onClick={() => onPick(`if:${m.if}`)}>{m.name} </button>)}</p>
            <ChannelVlan n={n} p={p} dev={dev} />
          </div>
        );
      })}
      <p><button disabled={dev.busy} onClick={() => dev.ios(["show etherchannel summary"])}>Show etherchannel summary</button></p>
    </div>
  );
}

function ChannelVlan({ n, p, dev }) {
  const c = p.ports?.[`po${n}`] || {};
  const init = { mode: c.mode || "access", vlan: Number(c.vlan || 1), native: Number(c.native || 1), allowed: c.allowed || "" };
  const [d, set, dirty, reset] = useDraft(init);
  const ids = vlanIds(p);
  const apply = () => {
    const x = ["configure terminal", `interface Port-channel${n}`, `switchport mode ${d.mode}`];
    if (d.mode === "access") x.push(`switchport access vlan ${d.vlan}`);
    else x.push(`switchport trunk native vlan ${d.native}`, `switchport trunk allowed vlan ${d.allowed || "all"}`);
    x.push("end");
    dev.ios(x);
  };
  return (<>
    <Field label="Mode"><div className="seg">{["access", "trunk"].map((m) => <button key={m} className={d.mode === m ? "on" : ""} onClick={() => set("mode", m)}>{m}</button>)}</div></Field>
    {d.mode === "access" ? <Field label="Access VLAN"><select value={d.vlan} onChange={(e) => set("vlan", Number(e.target.value))}>{ids.map((v) => <option key={v}>{v}</option>)}</select></Field>
      : <><Field label="Native VLAN"><select value={d.native} onChange={(e) => set("native", Number(e.target.value))}>{ids.map((v) => <option key={v}>{v}</option>)}</select></Field>
        <Field label="Allowed VLANs"><VlanPicker p={p} value={d.allowed} onChange={(v) => set("allowed", v)} /></Field></>}
    <ApplyBar dirty={dirty} busy={dev.busy} onApply={apply} onReset={reset} />
  </>);
}

// ================================================================= PC / SERVER
export function HostConfigX({ lab, node, running, notify, onRename, onUpdateNode }) {
  const [page, setPage] = useState("settings");
  const [st, setSt] = useState(null);
  const load = useCallback(() => api.nodeState(lab.name, node.id).then(setSt).catch(() => {}), [lab.name, node.id]);
  useEffect(() => { load(); const t = setTimeout(load, 1500); return () => clearTimeout(t); }, [load, running, JSON.stringify(node.props || {})]); // eslint-disable-line
  const nic = st?.ports?.find((x) => x.if === "eth1");
  const groups = [{ title: "Global", items: [{ key: "settings", label: "Settings" }] },
    { title: "Interface", items: [{ key: "nic", label: nic?.name && nic.name !== "eth1" ? nic.name : node.type === "server" ? "Ethernet0" : "FastEthernet0", dot: nic ? portDot(nic) : "idle", sub: nic?.kernel_addrs?.[0] || "" }] }];
  return (
    <div className="cfgx cfgx-nolog">
      <SideNav groups={groups} active={page} onPick={setPage} />
      <div className="cfgx-main">
        {page === "settings" && <div className="page"><h3>Settings</h3>
          <HostIp lab={lab} node={node} running={running} notify={notify} onUpdateNode={onUpdateNode} onDone={load} only="global" />
          <NameAndStartup node={node} running={running} onRename={onRename} onUpdateNode={onUpdateNode} placeholder="ip link set eth1 mtu 1400" /></div>}
        {page === "nic" && <div className="page"><h3>{nic?.name && nic.name !== "eth1" ? nic.name : node.type === "server" ? "Ethernet0" : "FastEthernet0"} <span className="hint">eth1{nic?.type ? ` · ${fmtSpeed(nic.kbit)} ${nic.media}` : ""}</span></h3>
          {nic && <div className="statline"><span className={`led led-${portDot(nic)}`} /><span>{nic.oper === "up" ? "up" : nic.peer ? "down" : "not cabled"}</span>
            {nic.peer && <span>· to <b>{nic.peer.node}</b> {nic.peer.port}</span>}{nic.mac && <span className="mono">· {nic.mac}</span>}
            {nic.kernel_addrs?.length > 0 && <span className="mono">· now {nic.kernel_addrs.join(", ")}</span>}</div>}
          <HostIp lab={lab} node={node} running={running} notify={notify} onUpdateNode={onUpdateNode} onDone={load} only="nic" /></div>}
      </div>
    </div>
  );
}

function HostIp({ lab, node, running, notify, onUpdateNode, onDone, only }) {
  const p = node.props || {};
  const init = { mode: p.ip_mode === "dhcp" ? "dhcp" : "static", ...splitCidr(p.ip), gw: p.gateway || "", dns: p.dns || "" };
  const [d, set, dirty, reset] = useDraft(init);
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const bad = d.mode === "static" && ((d.ip && (!isIp(d.ip) || maskToLen(d.mask) == null)) || (d.gw && !isIp(d.gw)) || (d.dns && !isIp(d.dns)));
  const apply = async () => {
    const body = { mode: d.mode, ip: d.mode === "static" && d.ip ? toCidr(d.ip, d.mask) : "", gateway: d.gw, dns: d.dns };
    onUpdateNode(node.id, { ip_mode: d.mode, ip: body.ip, gateway: d.gw, dns: d.dns });
    if (!running) { setOut("Saved. Applied at the next deploy."); return; }
    setBusy(true);
    try { const r = await api.ipconfig(lab.name, node.id, body); setOut(r.output); if (!r.ok) notify(`${node.id}: IP configuration failed`, r.output, "bad"); onDone?.(); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  return (<>
    {only === "nic" && <Field label="IP configuration"><div className="seg">{[["dhcp", "DHCP"], ["static", "Static"]].map(([k, l]) => <button key={k} className={d.mode === k ? "on" : ""} onClick={() => set("mode", k)}>{l}</button>)}</div></Field>}
    {only === "nic" && d.mode === "static" && <Field label="IPv4 address"><IpMask ip={d.ip} mask={d.mask} onIp={(v) => set("ip", v)} onMask={(v) => set("mask", v)} placeholder="192.168.1.10" /></Field>}
    {(only === "global" || d.mode === "static") && (<>
      <Field label="Default gateway"><input className={d.gw && !isIp(d.gw) ? "bad" : ""} value={d.gw} disabled={d.mode === "dhcp"} placeholder={d.mode === "dhcp" ? "from DHCP" : "192.168.1.1"} onChange={(e) => set("gw", e.target.value.trim())} /></Field>
      <Field label="DNS server"><input className={d.dns && !isIp(d.dns) ? "bad" : ""} value={d.dns} disabled={d.mode === "dhcp"} placeholder={d.mode === "dhcp" ? "from DHCP" : "192.168.1.53"} onChange={(e) => set("dns", e.target.value.trim())} /></Field>
    </>)}
    <ApplyBar dirty={dirty || (only === "nic" && d.mode === "dhcp")} busy={busy} disabled={bad} onApply={apply} onReset={reset} label={d.mode === "dhcp" && only === "nic" ? "Request DHCP address" : "Apply"} />
    {out && <pre className="eq">{out}</pre>}
  </>);
}

// ================================================================= FIREWALL
const FW_DEFAULT = { zones: [{ name: "inside", level: 100 }, { name: "dmz", level: 50 }, { name: "outside", level: 0 }], policy: "levels", rules: [], nat: [], routes: [], interfaces: {}, allow_ping: true, gateway: "" };
const pick = (o, keys) => Object.fromEntries(keys.map((k) => [k, o[k]]));

export function FirewallConfigX({ lab, node, running, notify, onRename, onUpdateNode }) {
  const [page, setPage] = useState("policy");
  const [st, setSt] = useState(null);
  const [fw, setFw] = useState(null);
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState("");
  const base = useMemo(() => ({ ...FW_DEFAULT, ...pick({ ...FW_DEFAULT, ...(node.props || {}) }, Object.keys(FW_DEFAULT)) }), [JSON.stringify(node.props || {})]); // eslint-disable-line
  const [d, setD] = useState(base);
  useEffect(() => setD(base), [base]);
  const dirty = !same(d, base);
  const load = useCallback(() => {
    api.nodeState(lab.name, node.id).then(setSt).catch(() => {});
    if (running) api.firewallStatus(lab.name, node.id).then(setFw).catch(() => {});
  }, [lab.name, node.id, running]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const save = async () => {
    setBusy(true);
    try {
      const r = await api.firewall(lab.name, node.id, d);
      onUpdateNode(node.id, r.props);
      setOut(r.output || ""); notify(r.ok ? `${node.id}: firewall ${running ? "applied" : "saved"}` : `${node.id}: apply failed`, r.ok ? null : r.output, r.ok ? "ok" : "bad");
      load();
    } catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  const ports = st?.ports || [];
  const zoneOf = (ifn) => d.interfaces[ifn]?.zone || "";
  const groups = [
    { title: "Global", items: [{ key: "settings", label: "Settings" }, { key: "policy", label: "Security policy", sub: d.policy === "levels" ? "levels" : "deny" }, { key: "routing", label: "Routing", sub: d.gateway ? "gw set" : "" }] },
    { title: "Firewall", items: [{ key: "zones", label: "Zones", sub: String(d.zones.length) }, { key: "rules", label: "Rules", sub: String(d.rules.length) }, { key: "nat", label: "NAT", sub: d.nat.length ? String(d.nat.length) : "" }, { key: "status", label: "Status" }] },
    { title: "Interfaces", items: ports.map((p) => ({ key: `if:${p.if}`, label: p.name, dot: portDot(p), sub: zoneOf(p.if) || (d.interfaces[p.if]?.ip || "") })) },
  ];
  const upd = (k, v) => setD((x) => ({ ...x, [k]: v }));
  const zones = d.zones.map((z) => z.name);
  return (
    <div className="cfgx">
      <SideNav groups={groups} active={page} onPick={setPage} />
      <div className="cfgx-main">
        {page === "settings" && <div className="page"><h3>Settings</h3>
          <Field label="Answer ping" hint="Lets devices ping the firewall's own addresses."><input type="checkbox" checked={d.allow_ping} onChange={(e) => upd("allow_ping", e.target.checked)} /></Field>
          <NameAndStartup node={node} running={running} onRename={onRename} onUpdateNode={onUpdateNode} placeholder="sysctl -w net.ipv4.conf.all.rp_filter=1" /></div>}
        {page === "policy" && <div className="page"><h3>Security policy</h3>
          <Field label="Traffic no rule matches" hint="Return traffic for allowed connections is always let back in (stateful).">
            <div className="col">
              <label className="chk"><input type="radio" checked={d.policy === "levels"} onChange={() => upd("policy", "levels")} /> Allow from higher to lower security level (ASA style), deny the rest</label>
              <label className="chk"><input type="radio" checked={d.policy === "deny"} onChange={() => upd("policy", "deny")} /> Deny everything not allowed by a rule</label>
            </div></Field>
          <p className="hint">Rules are checked top to bottom before this default. Port forwards (NAT) are allowed through automatically.</p></div>}
        {page === "routing" && <FwRouting d={d} upd={upd} />}
        {page === "zones" && <FwZones d={d} upd={upd} />}
        {page === "rules" && <FwRules d={d} upd={upd} zones={zones} counters={fw?.counters || {}} />}
        {page === "nat" && <FwNat d={d} upd={upd} zones={zones} counters={fw?.counters || {}} />}
        {page === "status" && <div className="page"><h3>Status</h3>
          {!running ? <p className="hint">Deploy the lab to see live counters.</p> : (<>
            <Field label="Tracked connections">{fw?.conntrack ?? "–"}</Field>
            <Field label="Dropped by default">{fw?.counters?.["default-drop"]?.packets ?? 0} packets</Field>
            <h4>Ruleset (nft list ruleset)</h4><pre className="eq runcfg">{fw?.ruleset || ""}</pre></>)}</div>}
        {page.startsWith("if:") && <FwIface key={page} d={d} setD={setD} port={ports.find((p) => p.if === page.slice(3))} zones={zones} />}
        <ApplyBar dirty={dirty} busy={busy} onApply={save} onReset={() => setD(base)} label={running ? "Apply to firewall" : "Save"}
          note={dirty ? "Unsaved firewall changes (all pages apply together)" : ""} />
        {out && <pre className="eq">{out}</pre>}
      </div>
    </div>
  );
}

function FwIface({ d, setD, port, zones }) {
  const c = (port && d.interfaces[port.if]) || {};
  const { ip, mask } = splitCidr(c.ip);
  const [m, setM] = useState({ ip, mask });
  if (!port) return <p className="hint">That interface is gone.</p>;
  const put = (patch) => setD((x) => ({ ...x, interfaces: { ...x.interfaces, [port.if]: { ...(x.interfaces[port.if] || {}), ...patch } } }));
  const setIp = (nip, nmask) => { setM({ ip: nip, mask: nmask }); if ((!nip) || (isIp(nip) && maskToLen(nmask) != null)) put({ ip: nip ? toCidr(nip, nmask) : "" }); };
  return (
    <div className="page">
      <h3>{port.name}</h3>
      <div className="statline"><span className={`led led-${portDot(port)}`} /><span>{port.oper === "up" ? "up" : port.peer ? "down" : "not cabled"}</span>
        {port.peer && <span>· to <b>{port.peer.node}</b> {port.peer.port}</span>}</div>
      <Field label="Port status"><label className="chk"><input type="checkbox" checked={!c.shutdown} onChange={(e) => put({ shutdown: !e.target.checked })} /> On</label></Field>
      <Field label="Zone" hint="Interfaces in the same zone are one security domain (nameif)."><select value={c.zone || ""} onChange={(e) => put({ zone: e.target.value })}><option value="">(none)</option>{zones.map((z) => <option key={z}>{z}</option>)}</select></Field>
      <Field label="IPv4 address"><IpMask ip={m.ip} mask={m.mask} onIp={(v) => setIp(v, m.mask)} onMask={(v) => setIp(m.ip, v)} /></Field>
      <Field label="Description"><input className="wide" value={c.description || ""} onChange={(e) => put({ description: e.target.value })} /></Field>
    </div>
  );
}

function FwRouting({ d, upd }) {
  const [r, setR] = useState({ net: "", mask: "", via: "" });
  return (
    <div className="page"><h3>Routing</h3>
      <Field label="Default gateway"><input className={d.gateway && !isIp(d.gateway) ? "bad" : ""} value={d.gateway} placeholder="203.0.113.1" onChange={(e) => upd("gateway", e.target.value.trim())} /></Field>
      <h4>Static routes</h4>
      <table className="grid compact"><thead><tr><th>Network</th><th>Next hop</th><th /></tr></thead>
        <tbody>{d.routes.map((x, i) => <tr key={i}><td className="mono">{x.prefix}</td><td className="mono">{x.via}</td><td><button className="link danger" onClick={() => upd("routes", d.routes.filter((_, j) => j !== i))}>Remove</button></td></tr>)}
          {!d.routes.length && <tr><td colSpan={3} className="hint">None.</td></tr>}</tbody></table>
      <div className="formrow">
        <label>Network<IpMask ip={r.net} mask={r.mask} onIp={(v) => setR({ ...r, net: v })} onMask={(v) => setR({ ...r, mask: v })} placeholder="10.20.0.0" /></label>
        <label>Next hop<input value={r.via} placeholder="192.168.1.254" onChange={(e) => setR({ ...r, via: e.target.value.trim() })} /></label>
        <button disabled={!isIp(r.net) || maskToLen(r.mask) == null || !isIp(r.via)} onClick={() => { upd("routes", [...d.routes, { prefix: toCidr(r.net, r.mask), via: r.via }]); setR({ net: "", mask: "", via: "" }); }}>Add</button>
      </div>
    </div>
  );
}

function FwZones({ d, upd }) {
  const [z, setZ] = useState({ name: "", level: 50 });
  const used = (n) => Object.values(d.interfaces).some((c) => c.zone === n) || d.rules.some((r) => r.from === n || r.to === n) || d.nat.some((x) => x.from === n || x.to === n || x.zone === n);
  return (
    <div className="page"><h3>Zones</h3>
      <p className="hint">Security level 0-100 (inside 100, outside 0). With the "levels" policy, traffic may go from a higher level to a lower one.</p>
      <table className="grid compact"><thead><tr><th>Zone</th><th>Level</th><th>Interfaces</th><th /></tr></thead>
        <tbody>{d.zones.map((x, i) => (
          <tr key={x.name}><td>{x.name}</td>
            <td><input className="narrow" type="number" min="0" max="100" value={x.level} onChange={(e) => upd("zones", d.zones.map((y, j) => (j === i ? { ...y, level: Number(e.target.value) } : y)))} /></td>
            <td className="hint">{Object.entries(d.interfaces).filter(([, c]) => c.zone === x.name).map(([k]) => k).join(", ")}</td>
            <td><button className="link danger" disabled={used(x.name)} title={used(x.name) ? "In use by an interface, rule or NAT entry" : ""} onClick={() => upd("zones", d.zones.filter((_, j) => j !== i))}>Remove</button></td></tr>))}</tbody></table>
      <div className="formrow">
        <label>Name<input value={z.name} placeholder="guest" onChange={(e) => setZ({ ...z, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "").slice(0, 15) })} /></label>
        <label>Level<input className="narrow" type="number" min="0" max="100" value={z.level} onChange={(e) => setZ({ ...z, level: Number(e.target.value) })} /></label>
        <button disabled={!/^[a-z]/.test(z.name) || d.zones.some((x) => x.name === z.name)} onClick={() => { upd("zones", [...d.zones, z]); setZ({ name: "", level: 50 }); }}>Add zone</button>
      </div>
    </div>
  );
}

function FwRules({ d, upd, zones, counters }) {
  const blank = { from: "any", to: "any", proto: "tcp", src: "any", dst: "any", ports: "", action: "accept", comment: "", enabled: true };
  const [n, setN] = useState(blank);
  const move = (i, dir) => { const r = [...d.rules]; const j = i + dir; if (j < 0 || j >= r.length) return; [r[i], r[j]] = [r[j], r[i]]; upd("rules", r); };
  const setRule = (i, k, v) => upd("rules", d.rules.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const okAddr = (v) => v === "any" || /^\d+\.\d+\.\d+\.\d+(\/\d+)?$/.test(v);
  const nextId = () => { let k = 1; while (d.rules.some((r) => r.id === `r${k}`)) k++; return `r${k}`; };
  return (
    <div className="page"><h3>Rules <span className="hint">first match wins</span></h3>
      <div className="tblwrap"><table className="grid compact fwr">
        <thead><tr><th title="Enabled">On</th><th>#</th><th>From</th><th>To</th><th>Proto</th><th>Source</th><th>Destination</th><th>Ports</th><th>Action</th><th>Hits</th><th>Comment</th><th /></tr></thead>
        <tbody>{d.rules.map((r, i) => (
          <tr key={r.id} className={r.enabled === false ? "dim" : ""}>
            <td><input type="checkbox" checked={r.enabled !== false} onChange={(e) => setRule(i, "enabled", e.target.checked)} title="Enabled" /></td>
            <td className="mono">{i + 1}</td>
            <td><select value={r.from} onChange={(e) => setRule(i, "from", e.target.value)}><option>any</option>{zones.map((z) => <option key={z}>{z}</option>)}</select></td>
            <td><select value={r.to} onChange={(e) => setRule(i, "to", e.target.value)}><option>any</option>{zones.map((z) => <option key={z}>{z}</option>)}</select></td>
            <td><select value={r.proto} onChange={(e) => setRule(i, "proto", e.target.value)}>{["any", "tcp", "udp", "icmp"].map((x) => <option key={x}>{x}</option>)}</select></td>
            <td><input className={`addr ${okAddr(r.src) ? "" : "bad"}`} value={r.src} onChange={(e) => setRule(i, "src", e.target.value.trim() || "any")} /></td>
            <td><input className={`addr ${okAddr(r.dst) ? "" : "bad"}`} value={r.dst} onChange={(e) => setRule(i, "dst", e.target.value.trim() || "any")} /></td>
            <td><input className="narrow" value={r.ports} disabled={!["tcp", "udp"].includes(r.proto)} onChange={(e) => setRule(i, "ports", e.target.value.replace(/[^0-9,-]/g, ""))} /></td>
            <td><select className={`act act-${r.action}`} value={r.action} onChange={(e) => setRule(i, "action", e.target.value)}>{["accept", "drop", "reject"].map((x) => <option key={x}>{x}</option>)}</select></td>
            <td className="mono">{counters[r.id]?.packets ?? "–"}</td>
            <td><input value={r.comment} onChange={(e) => setRule(i, "comment", e.target.value)} /></td>
            <td className="nowrap"><button className="link" onClick={() => move(i, -1)} title="Move up">↑</button><button className="link" onClick={() => move(i, 1)} title="Move down">↓</button>
              <button className="link danger" onClick={() => upd("rules", d.rules.filter((_, j) => j !== i))} title="Delete">✕</button></td>
          </tr>))}
          <tr className="newrow"><td /><td className="hint">new</td>
            <td><select value={n.from} onChange={(e) => setN({ ...n, from: e.target.value })}><option>any</option>{zones.map((z) => <option key={z}>{z}</option>)}</select></td>
            <td><select value={n.to} onChange={(e) => setN({ ...n, to: e.target.value })}><option>any</option>{zones.map((z) => <option key={z}>{z}</option>)}</select></td>
            <td><select value={n.proto} onChange={(e) => setN({ ...n, proto: e.target.value })}>{["any", "tcp", "udp", "icmp"].map((x) => <option key={x}>{x}</option>)}</select></td>
            <td><input className={`addr ${okAddr(n.src) ? "" : "bad"}`} value={n.src} onChange={(e) => setN({ ...n, src: e.target.value.trim() || "any" })} /></td>
            <td><input className={`addr ${okAddr(n.dst) ? "" : "bad"}`} value={n.dst} onChange={(e) => setN({ ...n, dst: e.target.value.trim() || "any" })} /></td>
            <td><input className="narrow" value={n.ports} placeholder="80,443" disabled={!["tcp", "udp"].includes(n.proto)} onChange={(e) => setN({ ...n, ports: e.target.value.replace(/[^0-9,-]/g, "") })} /></td>
            <td><select value={n.action} onChange={(e) => setN({ ...n, action: e.target.value })}>{["accept", "drop", "reject"].map((x) => <option key={x}>{x}</option>)}</select></td>
            <td /><td><input value={n.comment} placeholder="web to DMZ" onChange={(e) => setN({ ...n, comment: e.target.value })} /></td>
            <td><button className="primary" disabled={!okAddr(n.src) || !okAddr(n.dst)} onClick={() => { upd("rules", [...d.rules, { ...n, id: nextId(), ports: ["tcp", "udp"].includes(n.proto) ? n.ports : "" }]); setN(blank); }}>Add</button></td>
          </tr>
        </tbody></table></div>
      <p className="hint">Addresses: any, a host (10.0.0.5) or a network (10.0.0.0/24). Ports: 80,443 or 8000-8080. Hits update every few seconds while running.</p>
    </div>
  );
}

function FwNat({ d, upd, zones, counters }) {
  const [m, setM] = useState({ from: zones[0] || "", to: zones[zones.length - 1] || "" });
  const [f, setF] = useState({ zone: zones[zones.length - 1] || "", proto: "tcp", port: "", to_ip: "", to_port: "" });
  const masq = d.nat.map((x, i) => [x, i]).filter(([x]) => x.type === "masquerade");
  const fwd = d.nat.map((x, i) => [x, i]).filter(([x]) => x.type === "dnat");
  return (
    <div className="page"><h3>NAT</h3>
      <h4>Source NAT (PAT / masquerade)</h4>
      <p className="hint">Hosts in the "from" zone reach the "to" zone using the firewall's own address there (like ASA dynamic PAT / interface overload).</p>
      <table className="grid compact"><thead><tr><th>From</th><th>To</th><th>Hits</th><th /></tr></thead>
        <tbody>{masq.map(([x, i]) => <tr key={i}><td>{x.from}</td><td>{x.to}</td><td className="mono">{counters[`nat:${x.from}>${x.to}`]?.packets ?? "–"}</td><td><button className="link danger" onClick={() => upd("nat", d.nat.filter((_, j) => j !== i))}>Remove</button></td></tr>)}
          {!masq.length && <tr><td colSpan={4} className="hint">None.</td></tr>}</tbody></table>
      <div className="formrow">
        <label>From<select value={m.from} onChange={(e) => setM({ ...m, from: e.target.value })}>{zones.map((z) => <option key={z}>{z}</option>)}</select></label>
        <label>To<select value={m.to} onChange={(e) => setM({ ...m, to: e.target.value })}>{zones.map((z) => <option key={z}>{z}</option>)}</select></label>
        <button disabled={!m.from || !m.to || m.from === m.to} onClick={() => upd("nat", [...d.nat, { type: "masquerade", ...m }])}>Add</button>
      </div>
      <h4>Port forwards (static NAT)</h4>
      <p className="hint">Connections to the firewall's address in the zone, on the port, go to the inside server. They're allowed through automatically.</p>
      <table className="grid compact"><thead><tr><th>Zone</th><th>Proto</th><th>Port</th><th>To</th><th>Hits</th><th /></tr></thead>
        <tbody>{fwd.map(([x, i]) => <tr key={i}><td>{x.zone}</td><td>{x.proto}</td><td>{x.port}</td><td className="mono">{x.to_ip}:{x.to_port}</td>
          <td className="mono">{counters[`pf:${x.port}`]?.packets ?? "–"}</td><td><button className="link danger" onClick={() => upd("nat", d.nat.filter((_, j) => j !== i))}>Remove</button></td></tr>)}
          {!fwd.length && <tr><td colSpan={6} className="hint">None.</td></tr>}</tbody></table>
      <div className="formrow">
        <label>Zone<select value={f.zone} onChange={(e) => setF({ ...f, zone: e.target.value })}>{zones.map((z) => <option key={z}>{z}</option>)}</select></label>
        <label>Proto<select value={f.proto} onChange={(e) => setF({ ...f, proto: e.target.value })}><option>tcp</option><option>udp</option></select></label>
        <label>Port<input className="narrow" value={f.port} placeholder="80" onChange={(e) => setF({ ...f, port: e.target.value.replace(/\D/g, "") })} /></label>
        <label>To address<input value={f.to_ip} placeholder="172.16.1.10" className={f.to_ip && !isIp(f.to_ip) ? "bad" : ""} onChange={(e) => setF({ ...f, to_ip: e.target.value.trim() })} /></label>
        <label>To port<input className="narrow" value={f.to_port} placeholder={f.port || "80"} onChange={(e) => setF({ ...f, to_port: e.target.value.replace(/\D/g, "") })} /></label>
        <button disabled={!f.zone || !f.port || !isIp(f.to_ip)} onClick={() => { upd("nat", [...d.nat, { type: "dnat", ...f, port: Number(f.port), to_port: Number(f.to_port || f.port) }]); setF({ ...f, port: "", to_ip: "", to_port: "" }); }}>Add</button>
      </div>
    </div>
  );
}
