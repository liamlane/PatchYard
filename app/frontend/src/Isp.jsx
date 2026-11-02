// Config tab for ISP and Internet devices: what each customer gets, how to set their router up
// (with a button that does it), peering, the hosted "public internet" services, and the uplink.
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";

const MODES = [["static", "Static /30"], ["dhcp", "DHCP (broadband)"], ["bgp", "BGP"]];

export function IspConfig({ lab, node, running, onUpdateNode, notify }) {
  const [plan, setPlan] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState({});
  const [busy, setBusy] = useState("");
  const [site, setSite] = useState({ name: "", ip: "", title: "", text: "" });
  const p = node.props || {};
  const key = JSON.stringify([p, lab.links.filter((l) => l.a === node.id || l.b === node.id), lab.nodes.filter((n) => n.type === "isp" || n.type === "internet").map((n) => n.id)]);
  useEffect(() => {
    const t = setTimeout(() => api.isp(lab.name, node.id).then((r) => { setPlan(r); setErr(""); }).catch((e) => setErr(e.message)), 1300);
    return () => clearTimeout(t);
  }, [lab.name, node.id, key]); // eslint-disable-line
  const setPort = (ifn, patch) => onUpdateNode(node.id, { ports: { ...(p.ports || {}), [ifn]: { ...((p.ports || {})[ifn] || {}), ...patch } } });
  const apply = async () => {
    setBusy("apply");
    try { const r = await api.ispApply(lab.name, node.id); notify("Provider config applied", r.notes.join(" · "), "ok"); } catch (e) { notify(e.message, null, "bad"); }
    setBusy("");
  };
  const isInet = node.type === "internet";
  return (
    <div className="isp-cfg">
      <p className="hint">{isInet
        ? "The internet core. Cable ISPs to it: they get a default route and reach each other through it. It hosts the public services every lab device can use."
        : "A service provider. Cable a router, firewall or PC to any port and it gets an internet connection. Cable it to an Internet node or another ISP and they peer with BGP."}
        {" "}Changes save straight away; {running ? <>press <b>Apply</b> to load them into the running lab.</> : "they load when you deploy."}</p>
      {err && <p className="error">{err}</p>}
      <div className="formrow">
        <label>AS number<input className="narrow" value={p.asn ?? ""} placeholder={plan ? String(plan.asn) : ""} onChange={(e) => onUpdateNode(node.id, { asn: e.target.value.replace(/\D/g, "") || undefined })} /></label>
        <label>{isInet ? "Direct customers pool" : "Customer pool"}<input value={p.pool ?? ""} placeholder={plan?.pool} onChange={(e) => onUpdateNode(node.id, { pool: e.target.value.trim() || undefined })} /></label>
        {running && <button className="primary" disabled={busy === "apply"} onClick={apply}><Icon name="play" size={14} /> {busy === "apply" ? "Applying…" : "Apply"}</button>}
      </div>
      {plan && <>
        <h4>Customers</h4>
        {!plan.ports.length && <p className="empty">No customers yet. Cable a router, firewall or PC to this {isInet ? "internet node" : "ISP"}.</p>}
        {plan.ports.map((x) => (
          <div key={x.if} className="isp-port">
            <div className="row">
              <b>{x.peer}</b><span className="hint">{x.help.peer_name} ↔ {x.if}</span>
              <span className="grow" />
              <select value={x.mode} onChange={(e) => setPort(x.if, { mode: e.target.value })} aria-label="Service">
                {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
              {x.mode === "bgp" && <label className="inline">customer AS<input className="narrow" value={((p.ports || {})[x.if] || {}).customer_as ?? ""} placeholder={String(x.customer_as)}
                onChange={(e) => setPort(x.if, { customer_as: e.target.value.replace(/\D/g, "") || undefined })} /></label>}
              {x.mode !== "dhcp" && <select value={String(((p.ports || {})[x.if] || {}).routed || "")} onChange={(e) => setPort(x.if, { routed: e.target.value || undefined })} aria-label="Routed block">
                <option value="">no routed block</option><option value="29">+ routed /29</option><option value="28">+ routed /28</option></select>}
            </div>
            <p className="isp-help">{x.help.text}</p>
            <div className="row">
              {x.help.ios.length > 0 && <button className="link" onClick={() => setOpen({ ...open, [x.if]: !open[x.if] })}>{open[x.if] ? "Hide" : "Show"} the IOS commands</button>}
              <span className="grow" />
              {x.help.peer_type === "router" && running && <button disabled={busy === x.if} onClick={async () => {
                setBusy(x.if);
                try { const r = await api.ispConfigure(lab.name, node.id, x.if); notify(r.ok ? `${x.peer} configured for its ${x.mode === "bgp" ? "BGP" : "internet"} connection` : `${x.peer}: some commands failed`, r.errors?.join("; "), r.ok ? "ok" : "warn"); }
                catch (e) { notify(e.message, null, "bad"); }
                setBusy("");
              }}>{busy === x.if ? "Configuring…" : `Configure ${x.peer} for me`}</button>}
            </div>
            {open[x.if] && <pre className="eq">{x.help.ios.join("\n")}</pre>}
          </div>))}
        {plan.transit.length > 0 && <>
          <h4>Peering</h4>
          <table className="grid compact"><thead><tr><th>Port</th><th>Peer</th><th>Address</th><th>Peer address</th><th>Peer AS</th></tr></thead>
            <tbody>{plan.transit.map((t) => <tr key={t.if}><td className="mono">{t.if}</td><td><b>{t.peer}</b> <span className="hint">{t.peer_type}</span></td><td className="mono">{t.ip}</td><td className="mono">{t.peer_ip}</td><td>{t.peer_as}</td></tr>)}</tbody></table>
        </>}
        {plan.hosts ? <>
          <h4>Public internet services</h4>
          <p className="hint">{isInet ? "This node" : "With no Internet node cabled to it, this ISP"} hosts the lab's internet: DNS on <code>1.1.1.1</code>, <code>8.8.8.8</code> and <code>9.9.9.9</code>, and these sites. Try them from a PC's browser or <code>ping 8.8.8.8</code>.</p>
          <table className="grid compact"><thead><tr><th>Name</th><th>Address</th><th>What it is</th><th /></tr></thead>
            <tbody>{plan.sites.map((s, i) => <tr key={s.name}><td className="mono">{s.name}</td><td className="mono">{s.ip}</td>
              <td>{s.kind === "myip" ? "shows the address you came from (test your NAT)" : s.kind === "speedtest" ? "iperf3 server for speed tests" : s.text}</td>
              <td>{i >= 4 && <button className="link danger" onClick={() => onUpdateNode(node.id, { sites: (p.sites || []).filter((x) => x.name !== s.name) })}>Remove</button>}</td></tr>)}</tbody></table>
          <div className="formrow">
            <label>Add a site<input placeholder="shop.example.org" value={site.name} onChange={(e) => setSite({ ...site, name: e.target.value.trim().toLowerCase() })} /></label>
            <label>Address<input className="narrow2" placeholder="198.18.2.10" value={site.ip} onChange={(e) => setSite({ ...site, ip: e.target.value.trim() })} /></label>
            <label>Page text<input value={site.text} placeholder="Welcome to the shop" onChange={(e) => setSite({ ...site, text: e.target.value })} /></label>
            <button disabled={!site.name || !site.ip} onClick={() => { onUpdateNode(node.id, { sites: [...(p.sites || []), { ...site, title: site.name }] }); setSite({ name: "", ip: "", title: "", text: "" }); }}>Add</button>
          </div>
          <h4>Uplink</h4>
          <div className="seg">
            <button className={(p.uplink || "simulated") === "simulated" ? "on" : ""} onClick={() => onUpdateNode(node.id, { uplink: "simulated" })}>Simulated internet</button>
            <button className={p.uplink === "real" ? "on" : ""} onClick={() => onUpdateNode(node.id, { uplink: "real" })}>Real internet</button>
          </div>
          <p className="hint">{p.uplink === "real"
            ? <>Lab traffic for anything else is masqueraded out of this node's management interface to the real internet, and DNS forwards real names. {!plan.internet_ok && <b className="error">This lab doesn't have internet access on this server, so real traffic will be blocked: ask an admin for "Lab internet".</b>}</>
            : "Only the lab's own public addresses exist: nothing leaves the server."}</p>
        </> : <p className="hint">Internet services and the uplink live on the Internet node this ISP is cabled to. Default route: {plan.upstream}.</p>}
        {plan.leases?.length > 0 && <>
          <h4>DHCP leases</h4>
          <table className="grid compact"><tbody>{plan.leases.map((l) => <tr key={l.mac}><td className="mono">{l.ip}</td><td className="mono">{l.mac}</td><td>{l.host}</td></tr>)}</tbody></table>
        </>}
      </>}
    </div>
  );
}
