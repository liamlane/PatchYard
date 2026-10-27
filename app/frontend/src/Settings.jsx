import { useEffect, useState } from "react";
import { catalogue } from "./hw.js";
import { api } from "./api.js";
import { Modal, Icon } from "./ui.jsx";

const TABS = [["general", "General", "gear"], ["defaults", "Lab defaults", "layers"], ["monitoring", "Monitoring", "pulse"], ["alerts", "Alert rules", "flag"],
  ["notifications", "Notifications", "bell"], ["appearance", "Appearance", "sun"], ["security", "Security", "keyboard"], ["about", "About & updates", "help"]];
const NEEDS_T = { cpu_high: "%", mem_high: "MB", link_util: "Mb/s", errors_rising: "per sample", drops_rising: "per sample", probe_loss: "% loss", probe_rtt: "ms" };

function Field({ label, hint, children }) {
  return <label className="set-f"><span className="set-l">{label}</span><span className="set-c">{children}{hint && <span className="hint">{hint}</span>}</span></label>;
}

export default function Settings({ onClose, notify, labs, lab, appearance, onSaved, initial = "general" }) {
  const [tab, setTab] = useState(initial);
  const [s, setS] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.settings().then(setS).catch((e) => notify(e.message, null, "bad")); }, [notify]);
  if (!s) return <Modal title="Settings" onClose={onClose} wide><p className="hint">Loading…</p></Modal>;
  const meta = s._meta || {};
  const upd = (sec, k, v) => { setS({ ...s, [sec]: { ...s[sec], [k]: v } }); setDirty(true); };
  const rules = s.alerts.rules;
  const setRule = (i, k, v) => { const r = rules.map((x, j) => (j === i ? { ...x, [k]: v } : x)); upd("alerts", "rules", r); };
  const save = async () => {
    setBusy(true);
    try { const r = await api.saveSettings(s); setS({ ...r, _meta: meta }); setDirty(false); onSaved(r); notify("Settings saved", null, "ok"); }
    catch (e) { notify(`Settings not saved: ${e.message}`, null, "bad"); }
    setBusy(false);
  };
  const footer = (<>
    {dirty && <span className="hint grow">Unsaved changes</span>}
    <button onClick={onClose}>Close</button>
    <button className="primary" disabled={!dirty || busy} onClick={save}>Save settings</button>
  </>);
  return (
    <Modal title="Settings" onClose={onClose} xwide footer={footer}>
      <div className="settings">
        <nav className="set-nav">{TABS.map(([k, l, i]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={i} size={16} />{l}</button>)}</nav>
        <div className="set-body">
          {tab === "general" && (<>
            <h3>General</h3>
            <Field label="Live editing" hint="Devices and cables you add to a running lab start and wire immediately. Off = changes wait for Redeploy.">
              <input type="checkbox" checked={s.general.live_edit} onChange={(e) => upd("general", "live_edit", e.target.checked)} /></Field>
            <Field label="Power off to swap modules" hint="Like real hardware: a running device must be powered off (Physical tab) before modules go in or out. Off = swap any time.">
              <input type="checkbox" checked={s.general.module_power_off !== false} onChange={(e) => upd("general", "module_power_off", e.target.checked)} /></Field>
            <Field label="Router CLI opens in" hint="The CLI tab can switch between IOS, FRR and shell at any time.">
              <select value={s.general.cli_default || "ios"} onChange={(e) => upd("general", "cli_default", e.target.value)}>
                <option value="ios">IOS syntax</option><option value="frr">FRR (vtysh)</option></select></Field>
            <Field label="Snapshot before every deploy" hint="Automatic safety snapshot of configs and topology.">
              <input type="checkbox" checked={s.snapshots.auto_before_deploy} onChange={(e) => upd("snapshots", "auto_before_deploy", e.target.checked)} /></Field>
            <Field label="Keep automatic snapshots" hint="Older automatic snapshots are pruned per lab; your named snapshots are never pruned.">
              <input type="number" min="1" max="500" value={s.snapshots.keep_auto} onChange={(e) => upd("snapshots", "keep_auto", Number(e.target.value))} /></Field>
          </>)}
          {tab === "defaults" && (<>
            <h3>Lab defaults</h3>
            <p className="hint">Used for new devices and new labs. Existing labs keep what they were built with until you change them.</p>
            <Field label="New router model" hint="Hardware for routers you add from the palette. Existing devices keep theirs.">
              <select value={s.lab_defaults.router_model || ""} onChange={(e) => upd("lab_defaults", "router_model", e.target.value)}>
                <option value="">Generic (ports added as you cable)</option>
                {Object.entries(catalogue()?.chassis || {}).filter(([, c]) => c.type === "router").map(([id, c]) => <option key={id} value={id}>{c.label}</option>)}</select></Field>
            <Field label="New switch model">
              <select value={s.lab_defaults.switch_model || ""} onChange={(e) => upd("lab_defaults", "switch_model", e.target.value)}>
                <option value="">Generic (ports added as you cable)</option>
                {Object.entries(catalogue()?.chassis || {}).filter(([, c]) => c.type === "switch").map(([id, c]) => <option key={id} value={id}>{c.label}</option>)}</select></Field>
            <Field label="Router image"><input className="wide" value={s.lab_defaults.frr_image} onChange={(e) => upd("lab_defaults", "frr_image", e.target.value)} /></Field>
            <Field label="PC image"><input className="wide" value={s.lab_defaults.pc_image} onChange={(e) => upd("lab_defaults", "pc_image", e.target.value)} /></Field>
            <Field label="Switch / server base"><input className="wide" value={s.lab_defaults.alpine_image} onChange={(e) => upd("lab_defaults", "alpine_image", e.target.value)} /></Field>
            <Field label="Management network" hint="Docker network shared by all labs."><input value={s.lab_defaults.mgmt_network} onChange={(e) => upd("lab_defaults", "mgmt_network", e.target.value)} /></Field>
            <Field label="Management subnet" hint="Must not overlap your LAN (MX WAN is 172.20.20.0/29)."><input value={s.lab_defaults.mgmt_subnet} onChange={(e) => upd("lab_defaults", "mgmt_subnet", e.target.value)} /></Field>
            <Field label="Link MTU" hint="For live-added cables (containerlab default 9500)."><input type="number" min="1280" max="9500" value={s.lab_defaults.link_mtu} onChange={(e) => upd("lab_defaults", "link_mtu", Number(e.target.value))} /></Field>
          </>)}
          {tab === "monitoring" && (<>
            <h3>Monitoring</h3>
            <Field label="Sample interval" hint="seconds; applies from the next sample"><input type="number" min="2" max="300" value={s.monitoring.interval} onChange={(e) => upd("monitoring", "interval", Number(e.target.value))} /></Field>
            <Field label="History retention" hint="hours (1–720). Older samples are pruned every 10 minutes."><input type="number" min="1" max="720" value={s.monitoring.retention_hours} onChange={(e) => upd("monitoring", "retention_hours", Number(e.target.value))} /></Field>
            <Field label="Protocol/probe check interval" hint="seconds; BGP/OSPF/LDP state and ping probes for alerting"><input type="number" min="10" max="600" value={s.monitoring.health_interval} onChange={(e) => upd("monitoring", "health_interval", Number(e.target.value))} /></Field>
          </>)}
          {tab === "alerts" && (<>
            <h3>Alert rules</h3>
            <div className="row">
              <label className="chk"><input type="checkbox" checked={s.alerts.enabled} onChange={(e) => upd("alerts", "enabled", e.target.checked)} /> Alerting on</label>
              <span className="sep" />
              <label className="chk">Grace after deploy <input className="narrow" type="number" min="0" max="3600" value={s.alerts.grace_s} onChange={(e) => upd("alerts", "grace_s", Number(e.target.value))} /> s</label>
              <span className="hint">Protocol, link and device rules wait this long after a lab comes up, so convergence doesn't alert.</span>
            </div>
            <div className="rules-wrap"><table className="grid rules">
              <thead><tr><th>On</th><th>Name</th><th>Condition</th><th>Threshold</th><th>Lab</th><th>Device</th><th>For</th><th>Severity</th><th /></tr></thead>
              <tbody>{rules.map((r, i) => (
                <tr key={r.id}>
                  <td><input type="checkbox" checked={r.enabled} onChange={(e) => setRule(i, "enabled", e.target.checked)} aria-label="Enabled" /></td>
                  <td><input value={r.name} onChange={(e) => setRule(i, "name", e.target.value)} aria-label="Rule name" /></td>
                  <td><select value={r.type} onChange={(e) => setRule(i, "type", e.target.value)} aria-label="Condition">
                    {Object.entries(meta.rule_types || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></td>
                  <td>{NEEDS_T[r.type] ? <span className="nowrap"><input className="narrow" type="number" min="0" value={r.threshold ?? ""} onChange={(e) => setRule(i, "threshold", Number(e.target.value))} /> <span className="hint">{NEEDS_T[r.type]}</span></span> : <span className="hint">–</span>}
                    {r.type?.startsWith("probe") && <input className="probe-t" placeholder="target IP" value={r.target || ""} onChange={(e) => setRule(i, "target", e.target.value)} aria-label="Probe target" />}</td>
                  <td><select value={r.lab || ""} onChange={(e) => setRule(i, "lab", e.target.value)} aria-label="Lab"><option value="">any</option>{labs.map((l) => <option key={l.name}>{l.name}</option>)}</select></td>
                  <td><input className="narrow2" list="set-nodes" placeholder={r.type?.startsWith("probe") ? "source" : "any"} value={r.node || ""} onChange={(e) => setRule(i, "node", e.target.value.toLowerCase())} aria-label="Device" /></td>
                  <td className="nowrap"><input className="narrow" type="number" min="0" value={r.for_s} onChange={(e) => setRule(i, "for_s", Number(e.target.value))} aria-label="For seconds" /> s</td>
                  <td><select value={r.severity} onChange={(e) => setRule(i, "severity", e.target.value)} aria-label="Severity">{(meta.severities || []).map((x) => <option key={x}>{x}</option>)}</select></td>
                  <td><button className="link danger" onClick={() => upd("alerts", "rules", rules.filter((_, j) => j !== i))} aria-label="Delete rule"><Icon name="trash" size={14} /></button></td>
                </tr>))}
              </tbody>
            </table></div>
            <datalist id="set-nodes">{(lab?.nodes || []).map((n) => <option key={n.id} value={n.id} />)}</datalist>
            <div className="row">
              <button onClick={() => upd("alerts", "rules", [...rules, { id: `r-${Date.now().toString(36)}`, name: "New rule", type: "cpu_high", threshold: 80, severity: "warning", for_s: 30, enabled: true, lab: "", node: "" }])}><Icon name="plus" size={14} /> Add rule</button>
              <button onClick={() => upd("alerts", "rules", [...rules, { id: `r-${Date.now().toString(36)}`, name: "Ping probe", type: "probe_rtt", threshold: 100, severity: "warning", for_s: 30, enabled: true, lab: lab?.name || "", node: "", target: "" }])}><Icon name="pulse" size={14} /> Add ping probe</button>
            </div>
            <p className="hint">Ping probes run from a device's namespace every check interval (5 pings), like an IP SLA probe. Alerts resolve automatically when the condition clears.</p>
          </>)}
          {tab === "notifications" && (<>
            <h3>In-app notifications</h3>
            <Field label="Pop-up for alerts from" hint="Lower severities still appear in the Notifications panel.">
              <select value={s.notifications.toast_min_severity} onChange={(e) => upd("notifications", "toast_min_severity", e.target.value)}>
                <option value="info">info and above</option><option value="warning">warning and above</option><option value="critical">critical only</option><option value="none">no pop-ups</option></select></Field>
            <Field label="Pop-up when resolved"><input type="checkbox" checked={s.notifications.toast_resolved} onChange={(e) => upd("notifications", "toast_resolved", e.target.checked)} /></Field>
            <Field label="Sound on critical"><input type="checkbox" checked={s.notifications.sound} onChange={(e) => upd("notifications", "sound", e.target.checked)} /></Field>
            <p className="hint">Email, phone push (ntfy) and Teams/Slack webhooks are on the roadmap.</p>
          </>)}
          {tab === "appearance" && (<>
            <h3>Appearance <span className="hint">(this browser)</span></h3>
            <Field label="Theme"><div className="seg">{["light", "dark"].map((t) => <button key={t} className={appearance.theme === t ? "on" : ""} onClick={() => appearance.setTheme(t)}>{t}</button>)}</div></Field>
            <Field label="Traffic overlay"><input type="checkbox" checked={appearance.overlay} onChange={(e) => appearance.setOverlay(e.target.checked)} /></Field>
            <Field label="Port labels"><input type="checkbox" checked={appearance.showIf} onChange={(e) => appearance.setShowIf(e.target.checked)} /></Field>
            <Field label="Snap to grid"><input type="checkbox" checked={appearance.snap} onChange={(e) => appearance.setSnap(e.target.checked)} /></Field>
          </>)}
          {tab === "security" && <Security s={s} notify={notify} />}
          {tab === "about" && <About meta={meta} />}
        </div>
      </div>
    </Modal>
  );
}

function Security({ s, notify }) {
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const set = s.security.password_set;
  const go = async (body, msg) => {
    try { await api.setPassword(body); notify(msg, null, "ok"); setCur(""); setPw(""); setPw2(""); setTimeout(() => location.reload(), 600); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  return (<>
    <h3>Security</h3>
    <p className="hint">netlab runs as root and drives Docker, so anyone who reaches it controls the VM. Turn on a login, and still publish it only through NPM with Authentik.</p>
    <div className={`secstate ${s.security.auth_enabled ? "on" : ""}`}><Icon name={s.security.auth_enabled ? "flag" : "help"} />{s.security.auth_enabled ? "Login is ON" : "Login is OFF — anyone on the network can use netlab"}</div>
    {set && <Field label="Current password"><input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Field>}
    <Field label={set ? "New password" : "Admin password"} hint="at least 8 characters"><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" /></Field>
    <Field label="Repeat"><input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" /></Field>
    <div className="row">
      <button className="primary" disabled={pw.length < 8 || pw !== pw2} onClick={() => go({ current: cur, new: pw }, set ? "Password changed" : "Login enabled")}>{set ? "Change password" : "Set password and turn login on"}</button>
      {s.security.auth_enabled && <button className="danger" disabled={!cur} onClick={() => go({ current: cur, enable: false }, "Login turned off")}>Turn login off</button>}
    </div>
    <p className="hint">Locked out? On the VM: <code>docker exec netlab python -m app.auth reset</code></p>
  </>);
}

function About({ meta }) {
  return (<>
    <h3>About netlab</h3>
    <table className="grid compact"><tbody>
      <tr><td>Version</td><td className="mono">{meta.version}</td></tr>
      <tr><td>Data folder</td><td className="mono">{meta.data}</td></tr>
    </tbody></table>
    <h3>Updating</h3>
    <p>netlab runs as a container built from the GitHub repository. Every push to <code>main</code> publishes a new image. To update, on the VM:</p>
    <pre className="eq">cd /opt/netlab/deploy{"\n"}docker compose pull && docker compose up -d</pre>
    <p className="hint">Labs, snapshots, settings and metrics history live in /opt/netlab on the VM, outside the container, so updates never touch them. Running labs keep running while netlab restarts.</p>
  </>);
}
