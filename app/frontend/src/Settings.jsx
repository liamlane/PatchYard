import { useEffect, useState } from "react";
import { catalogue } from "./hw.js";
import { api } from "./api.js";
import { TwoFactorSection } from "./TwoFactor.jsx";
import { Modal, Icon } from "./ui.jsx";

const TABS = [["general", "General", "gear"], ["defaults", "Lab defaults", "layers"], ["monitoring", "Monitoring", "pulse"], ["alerts", "Alert rules", "flag"],
  ["notifications", "Notifications", "bell"], ["appearance", "Appearance", "sun"], ["security", "Account", "keyboard"], ["about", "About & updates", "help"]];
const USER_TABS = [["appearance", "Appearance", "sun"], ["security", "Account", "keyboard"], ["about", "About", "help"]];
const NEEDS_T = { cpu_high: "%", mem_high: "MB", link_util: "Mb/s", errors_rising: "per sample", drops_rising: "per sample", probe_loss: "% loss", probe_rtt: "ms" };

function Field({ label, hint, children }) {
  return <label className="set-f"><span className="set-l">{label}</span><span className="set-c">{children}{hint && <span className="hint">{hint}</span>}</span></label>;
}

export default function Settings({ onClose, notify, labs, lab, appearance, onSaved, initial = "general", me }) {
  const [tab, setTab] = useState(initial);
  const [s, setS] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.settings().then(setS).catch((e) => notify(e.message, null, "bad")); }, [notify]);
  if (!s) return <Modal title="Settings" onClose={onClose} wide><p className="hint">Loading…</p></Modal>;
  const meta = s._meta || {};
  const admin = meta.admin !== false;
  const tabs = admin ? TABS : USER_TABS;
  if (!tabs.some(([k]) => k === tab)) setTab(tabs[0][0]);
  const upd = (sec, k, v) => { setS({ ...s, [sec]: { ...s[sec], [k]: v } }); setDirty(true); };
  const rules = s.alerts?.rules || [];
  const setRule = (i, k, v) => { const r = rules.map((x, j) => (j === i ? { ...x, [k]: v } : x)); upd("alerts", "rules", r); };
  const save = async () => {
    setBusy(true);
    try { const r = await api.saveSettings(s); setS({ ...r, _meta: meta }); setDirty(false); onSaved(r); notify("Settings saved", null, "ok"); }
    catch (e) { notify(`Settings not saved: ${e.message}`, null, "bad"); }
    setBusy(false);
  };
  const footer = admin ? (<>
    {dirty && <span className="hint grow">Unsaved changes</span>}
    <button onClick={onClose}>Close</button>
    <button className="primary" disabled={!dirty || busy} onClick={save}>Save settings</button>
  </>) : <button onClick={onClose}>Close</button>;
  return (
    <Modal title="Settings" onClose={onClose} xwide footer={footer}>
      <div className="settings">
        <nav className="set-nav">{tabs.map(([k, l, i]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={i} size={16} />{l}</button>)}</nav>
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
              <thead><tr><th>On</th><th>Name</th><th>Condition</th><th>Threshold</th><th>Lab</th><th>Device</th><th>For</th><th>Severity</th><th title="Alert channels (Admin → Alert channels)">Send to</th><th /></tr></thead>
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
                  <td><select value={(r.channels || [])[0] || ""} onChange={(e) => setRule(i, "channels", e.target.value ? [e.target.value] : [])} aria-label="Send to">
                    <option value="">all channels</option><option value="__none__">bell only</option>
                    {(s.channels?.items || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></td>
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
          {tab === "security" && <Security s={s} notify={notify} me={me} />}
          {tab === "about" && <About meta={meta} />}
        </div>
      </div>
    </Modal>
  );
}

function Security({ s, notify, me }) {
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try { await api.setPassword({ current: cur, new: pw }); notify("Password changed", "Other sessions of this account were signed out.", "ok"); setCur(""); setPw(""); setPw2(""); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  const [prof, setProf] = useState({ full_name: me?.full_name || "", email: me?.email || "" });
  const [alertMail, setAlertMail] = useState(!!me?.alert_email);
  const saveProf = async () => {
    try { const r = await api.profile(prof); if (me) { me.full_name = r.full_name; me.email = r.email; } notify("Details saved", null, "ok"); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  return (<>
    <h3>Account</h3>
    <table className="grid compact"><tbody>
      <tr><td>Signed in as</td><td><b>{me?.name || "—"}</b> <span className="hint">{me?.role}</span></td></tr>
    </tbody></table>
    {me && <>
      <Field label="Full name"><input value={prof.full_name} maxLength={80} autoComplete="name" onChange={(e) => setProf({ ...prof, full_name: e.target.value })} /></Field>
      <Field label="Email" hint="so the admin can reach you"><input type="email" value={prof.email} maxLength={254} autoComplete="email" onChange={(e) => setProf({ ...prof, email: e.target.value })} /></Field>
      <div className="row"><button onClick={saveProf} disabled={prof.full_name === (me.full_name || "") && prof.email === (me.email || "")}>Save details</button></div>
      <Field label="Email me about alerts" hint={prof.email ? "a digest of warnings and critical alerts from your running labs, at most every 10 minutes" : "add your email address first"}>
        <input type="checkbox" disabled={!me.email} checked={!!alertMail} onChange={async (e) => {
          const v = e.target.checked; setAlertMail(v);
          try { await api.profile({ alert_email: v }); me.alert_email = v; notify(v ? "You'll get alert emails" : "Alert emails off", null, "ok"); } catch (x) { setAlertMail(!v); notify(x.message, null, "bad"); }
        }} /></Field>
    </>}
    {me && <>
      <h3>Two-factor sign-in</h3>
      <p className="hint">A code from an authenticator app on your phone, as well as your password, every time you sign in.</p>
      <TwoFactorSection me={me} notify={notify} required={!!me.two_factor_required} />
      <PhoneAlerts me={me} notify={notify} />
    </>}
    <h3>Change password</h3>
    <Field label="Current password"><input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Field>
    <Field label="New password" hint="at least 10 characters"><input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" /></Field>
    <Field label="Repeat"><input type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" /></Field>
    <div className="row">
      <button className="primary" disabled={busy || !cur || pw.length < 10 || pw !== pw2} onClick={go}>Change password</button>
      <button onClick={async () => { await api.logout(); location.href = "/"; }}>Sign out</button>
    </div>
    {s.security && <>
      <h3>Sessions</h3>
      <Field label="Stay signed in for" hint="hours; applies to new sign-ins">
        <input type="number" min="1" max="720" value={s.security.session_hours} readOnly /></Field>
      <p className="hint">Users, invites, limits and the lab sandbox are under <b>Admin</b> (your name, top right). Locked out? On the host: <code>sudo docker exec patchyard python -m app.auth reset-password {me?.name || "admin"}</code></p>
    </>}
  </>);
}

function About({ meta }) {
  const [sys, setSys] = useState(null);
  const [upd, setUpd] = useState(null);
  useEffect(() => { api.system().then(setSys).catch(() => {}); api.updateCheck().then(setUpd).catch(() => {}); }, []);
  const feats = sys ? Object.entries(sys.features) : [];
  return (<>
    <h3>About Patchyard</h3>
    <table className="grid compact"><tbody>
      <tr><td>Version</td><td className="mono">{meta.version}</td></tr>
      <tr><td>Data folder</td><td className="mono">{meta.data}</td></tr>
      {sys?.kernel && <tr><td>Host</td><td>{sys.os || "Linux"} · kernel {sys.kernel} · {sys.cpus} CPU · {Math.round(sys.mem_mb / 1024)} GB · Docker {sys.docker || "?"} · containerlab {sys.containerlab || "?"}</td></tr>}
    </tbody></table>
    {meta.admin !== false && <>
    <h3>Updates</h3>
    {!upd ? <p className="hint">Checking…</p> : !upd.enabled ? <p className="hint">The update check is off (PATCHYARD_UPDATE_CHECK=0).</p>
      : upd.available ? <div className="secstate"><Icon name="flag" /> {upd.latest.tag} is out (you have {upd.current}). <a href={upd.latest.url} target="_blank" rel="noreferrer">What's new</a></div>
      : upd.error ? <p className="hint">Couldn't reach GitHub ({upd.error}).</p>
      : <p className="hint">Up to date{upd.latest?.tag ? ` (latest release ${upd.latest.tag})` : ""}.</p>}
    <p>To update, on the host:</p>
    <pre className="eq">sudo {meta.data ? meta.data.replace(/\/labs$/, "") : "/opt/patchyard"}/deploy/update.sh</pre>
    <p className="hint">Labs, snapshots, settings and metrics history live in the data folder, outside the container, so updates never touch them. Running labs keep running while Patchyard restarts.</p>
    </>}
    <h3>What this host supports</h3>
    <table className="grid compact"><tbody>{feats.map(([k, f]) => (
      <tr key={k}><td><span className={`led led-${f.ok ? "up" : "down"}`} />{f.needed_for}</td><td className="hint">{f.ok ? "ok" : `missing kernel module ${f.missing.join(", ")}`}</td></tr>))}
      {!sys && <tr><td className="hint">Checking…</td></tr>}</tbody></table>
    {feats.some(([, f]) => !f.ok) && <p className="hint">Load missing modules with <code>sudo modprobe NAME</code> (Ubuntu: <code>sudo apt install linux-modules-extra-$(uname -r)</code>), or re-run the installer, which does both.</p>}
    <h3>Licence</h3>
    <p className="hint">Copyright Liam Lane. Free to install and use for personal, educational and lab work; see NOTICE in the repository.</p>
  </>);
}

function PhoneAlerts({ me, notify }) {
  const [st, setSt] = useState(null);
  const [topic, setTopic] = useState(me.ntfy_topic || "");
  useEffect(() => { api.authState().then(setSt).catch(() => {}); }, []);
  if (!st?.user_ntfy) return null;
  const suggest = () => setTopic(`patchyard-${me.name}-${Math.random().toString(36).slice(2, 10)}`);
  return (<>
    <h3>Alerts on your phone</h3>
    <p className="hint">Install the free <b>ntfy</b> app (iOS, Android), subscribe to a topic on <code>{st.ntfy_server}</code>, and enter the same topic here.
      Warnings and critical alerts from your labs then arrive as push notifications. Anyone who knows the topic can read it, so make it hard to guess.</p>
    <div className="formrow">
      <label>Topic<input value={topic} placeholder="patchyard-yourname-x7k2q9" onChange={(e) => setTopic(e.target.value.trim())} /></label>
      {!topic && <button onClick={suggest}>Suggest one</button>}
      <button className="primary" disabled={topic === (me.ntfy_topic || "")} onClick={async () => {
        try { await api.profile({ ntfy_topic: topic }); me.ntfy_topic = topic; notify(topic ? "Phone alerts on" : "Phone alerts off", null, "ok"); } catch (x) { notify(x.message, null, "bad"); }
      }}>Save</button>
      {me.ntfy_topic && <button onClick={async () => { const r = await api.ntfyTest().catch((x) => ({ error: x.message })); notify(r.ok ? "Test sent: check your phone" : `Couldn't send: ${r.error}`, null, r.ok ? "ok" : "bad"); }}>Send a test</button>}
    </div>
  </>);
}
