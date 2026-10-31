// Admin panel: users, invite links, every lab on the server, limits and the lab sandbox.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { Modal, Icon } from "./ui.jsx";

const TABS = [["users", "Users", "grid"], ["requests", "Requests", "flag"], ["invites", "Invites", "plus"], ["labs", "All labs", "layers"], ["limits", "Limits & sandbox", "gear"], ["email", "Email", "bell"], ["channels", "Alert channels", "zap"], ["audit", "Audit log", "diff"]];
const QK = [["max_labs", "Labs"], ["max_running_labs", "Running at once"], ["max_devices", "Devices per lab"], ["device_memory_mb", "Memory per device (MB)"], ["device_cpus", "CPUs per device"], ["max_disk_mb", "Disk (MB)"]];
const ago = (t) => (!t ? "never" : (() => { const s = Date.now() / 1000 - t; return s < 120 ? "just now" : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; })());

export default function Admin({ tab = "users", onTab, onClose, notify, me, onOpenLab }) {
  const setTab = onTab;
  const [pending, setPending] = useState(0);
  useEffect(() => { api.adminRequests().then((r) => setPending(r.filter((x) => x.state === "pending").length)).catch(() => {}); }, [tab]);
  return (
    <Modal title="Admin" onClose={onClose} xwide footer={<button onClick={onClose}>Close</button>}>
      <div className="settings">
        <nav className="set-nav">{TABS.map(([k, l, i]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={i} size={16} />{l}{k === "requests" && pending > 0 && <span className="count">{pending}</span>}</button>)}</nav>
        <div className="set-body">
          {tab === "users" && <Users notify={notify} me={me} />}
          {tab === "requests" && <Requests notify={notify} onChange={setPending} />}
          {tab === "invites" && <Invites notify={notify} />}
          {tab === "labs" && <AllLabs notify={notify} onOpenLab={(n) => { onOpenLab(n); onClose(); }} />}
          {tab === "limits" && <Limits notify={notify} />}
          {tab === "email" && <Email notify={notify} me={me} />}
          {tab === "channels" && <Channels notify={notify} />}
          {tab === "audit" && <Audit notify={notify} />}
        </div>
      </div>
    </Modal>
  );
}

function Users({ notify, me }) {
  const [list, setList] = useState(null);
  const [edit, setEdit] = useState(null);
  const [nu, setNu] = useState({ username: "", role: "user", full_name: "", email: "", send_link: true });
  const [prof, setProf] = useState(null);
  const [secret, setSecret] = useState(null);
  const load = useCallback(() => api.adminUsers().then(setList).catch((e) => notify(e.message, null, "bad")), [notify]);
  useEffect(() => { load(); }, [load]);
  const patch = async (name, body, msg) => {
    try { const r = await api.adminUserPatch(name, body); if (r.password) setSecret({ name, password: r.password }); if (msg) notify(msg, null, "ok"); load(); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  if (!list) return <p className="hint">Loading…</p>;
  return (<>
    <h3>Users</h3>
    <table className="grid compact admin-users"><thead><tr><th>User</th><th>Name and email</th><th>Role</th><th>Labs</th><th>Disk</th><th title="Two-factor sign-in">2FA</th><th>Running</th><th>Last sign-in</th><th>Lab internet</th><th /></tr></thead>
      <tbody>{list.map((u) => (
        <tr key={u.name} className={u.disabled ? "hint" : ""}>
          <td><b>{u.name}</b>{u.name === me?.name && <span className="hint"> (you)</span>}{u.disabled && <span className="hint"> · disabled</span>}</td>
          <td>{u.full_name || <span className="hint">—</span>}<br /><span className="hint">{u.email ? <a href={`mailto:${u.email}`}>{u.email}</a> : "no email"}</span></td>
          <td><select value={u.role} disabled={u.name === me?.name} onChange={(e) => patch(u.name, { role: e.target.value }, `${u.name} is now ${e.target.value === "admin" ? "an admin" : "a user"}`)}>
            <option value="user">user</option><option value="admin">admin</option></select></td>
          <td>{u.labs}{u.effective_quota.max_labs ? <span className="hint"> / {u.effective_quota.max_labs}</span> : ""}</td>
          <td className={u.effective_quota.max_disk_mb && u.disk_mb > u.effective_quota.max_disk_mb ? "error" : ""}>{u.disk_mb < 1 ? "<1" : Math.round(u.disk_mb)}{u.effective_quota.max_disk_mb ? <span className="hint"> / {u.effective_quota.max_disk_mb}</span> : ""} MB</td>
          <td>{u.two_factor ? <span className="svc-state svc-up" title={`${u.recovery_left} recovery codes left`}>on</span> : <span className="hint">off</span>}</td>
          <td className="mono">{u.running.join(", ") || "—"}</td>
          <td>{ago(u.last_login)}</td>
          <td><input type="checkbox" checked={u.role === "admin" || u.internet} disabled={u.role === "admin"} title="Full internet from this user's lab devices (otherwise package mirrors only)"
            onChange={(e) => patch(u.name, { internet: e.target.checked })} /></td>
          <td><span className="row">
            <button className="link" onClick={() => setProf(prof?.name === u.name ? null : { name: u.name, full_name: u.full_name || "", email: u.email || "" })}>Details</button>
            <button className="link" onClick={() => setEdit(edit === u.name ? null : u.name)}>Limits</button>
            {u.email && <button className="link" title="Email them a one-hour link to choose a new password" onClick={async () => {
              try { await api.adminSendReset(u.name); notify(`Reset link sent to ${u.email}`, null, "ok"); } catch (e) { notify(e.message, null, "bad"); }
            }}>Email reset link</button>}
            <button className="link" onClick={() => confirm(`Reset ${u.name}'s password? They'll be signed out.`) && patch(u.name, { reset_password: true })}>Reset password</button>
            {u.two_factor && <button className="link" title="For a lost phone: they sign in with their password and set it up again" onClick={() => confirm(`Remove two-factor sign-in from ${u.name}? They'll be signed out.`) && patch(u.name, { reset_2fa: true }, `Two-factor removed for ${u.name}`)}>Remove 2FA</button>}
            {u.name !== me?.name && <button className="link" onClick={() => patch(u.name, { disabled: !u.disabled }, u.disabled ? `${u.name} enabled` : `${u.name} disabled and signed out`)}>{u.disabled ? "Enable" : "Disable"}</button>}
            {u.name !== me?.name && <button className="link danger" onClick={async () => {
              const withLabs = u.labs > 0;
              if (!confirm(withLabs ? `Delete ${u.name} and their ${u.labs} lab(s)? Running labs are destroyed. This can't be undone.` : `Delete ${u.name}?`)) return;
              try { await api.adminUserDelete(u.name, withLabs); notify(`${u.name} deleted`, null, "ok"); load(); } catch (e) { notify(e.message, null, "bad"); }
            }}>Delete</button>}
          </span></td>
        </tr>))}</tbody></table>
    {secret && <div className="secstate on"><Icon name="flag" /> New password for <b>{secret.name}</b>: <code className="mono">{secret.password}</code> <span className="hint">(shown once; send it to them securely)</span>
      <button className="link" onClick={() => setSecret(null)}>hide</button></div>}
    {prof && <div className="subbox">
      <h4>Details for {prof.name}</h4>
      <div className="formrow">
        <label>Full name<input value={prof.full_name} maxLength={80} onChange={(e) => setProf({ ...prof, full_name: e.target.value })} /></label>
        <label>Email<input type="email" value={prof.email} maxLength={254} onChange={(e) => setProf({ ...prof, email: e.target.value })} /></label>
        <button onClick={() => setProf(null)}>Cancel</button>
        <button className="primary" onClick={() => { patch(prof.name, { full_name: prof.full_name, email: prof.email }, `Details for ${prof.name} saved`); setProf(null); }}>Save</button>
      </div></div>}
    {edit && <QuotaEdit user={list.find((u) => u.name === edit)} onSave={(q) => { patch(edit, { quota: q }, `Limits for ${edit} saved`); setEdit(null); }} onCancel={() => setEdit(null)} />}
    <h3>Add a user directly</h3>
    <p className="hint">Usually easier: send an invite link (Invites). This creates the account now and shows a generated password once.</p>
    <div className="formrow">
      <label>Username<input value={nu.username} placeholder="alice" onChange={(e) => setNu({ ...nu, username: e.target.value.trim().toLowerCase() })} /></label>
      <label>Full name<input value={nu.full_name} maxLength={80} onChange={(e) => setNu({ ...nu, full_name: e.target.value })} /></label>
      <label>Email<input type="email" value={nu.email} maxLength={254} onChange={(e) => setNu({ ...nu, email: e.target.value })} /></label>
      <label>Role<select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option value="user">user</option><option value="admin">admin</option></select></label>
      {nu.email && <label className="chk" title="Email them a link to choose their own password, instead of showing you one"><input type="checkbox" checked={nu.send_link} onChange={(e) => setNu({ ...nu, send_link: e.target.checked })} /> Email a set-password link</label>}
      <button className="primary" disabled={!/^[a-z][a-z0-9_-]{2,23}$/.test(nu.username)} onClick={async () => {
        try {
          const r = await api.adminUserCreate({ ...nu, send_link: nu.send_link && !!nu.email });
          if (r.emailed) notify(`${r.name} created: a set-password link went to ${r.email}`, null, "ok");
          else if (r.email_error) notify(`${r.name} created, but the email failed`, r.email_error, "warn");
          else setSecret({ name: r.name, password: r.password });
          setNu({ username: "", role: "user", full_name: "", email: "", send_link: true }); load();
        } catch (e) { notify(e.message, null, "bad"); }
      }}>Create</button>
    </div>
  </>);
}

function QuotaEdit({ user, onSave, onCancel }) {
  const [q, setQ] = useState(() => Object.fromEntries(QK.map(([k]) => [k, user.quota?.[k] ?? ""])));
  return (
    <div className="subbox">
      <h4>Limits for {user.name}</h4>
      <p className="hint">Blank = the server default (Limits & sandbox). 0 = unlimited. Admins are never limited.</p>
      <div className="formrow">{QK.map(([k, l]) => (
        <label key={k}>{l}<input className="narrow" value={q[k]} placeholder={String(user.effective_quota[k])} onChange={(e) => setQ({ ...q, [k]: e.target.value.replace(/[^\d.]/g, "") })} /></label>))}
        <button onClick={onCancel}>Cancel</button><button className="primary" onClick={() => onSave(q)}>Save</button></div>
    </div>
  );
}

function Requests({ notify, onChange }) {
  const [list, setList] = useState(null);
  const [net, setNet] = useState({});
  const load = useCallback(() => api.adminRequests().then((r) => { setList(r); onChange(r.filter((x) => x.state === "pending").length); })
    .catch((e) => notify(e.message, null, "bad")), [notify, onChange]);
  useEffect(() => { load(); }, [load]);
  if (!list) return <p className="hint">Loading…</p>;
  const pending = list.filter((r) => r.state === "pending"), done = list.filter((r) => r.state !== "pending");
  const act = async (r, ok) => {
    try {
      if (ok) { await api.adminRequestApprove(r.id, { internet: !!net[r.id] }); notify(`${r.username} approved: they can sign in now`, r.email, "ok"); }
      else { await api.adminRequestDecline(r.id); notify(`${r.username}'s request declined`, null, "ok"); }
      load();
    } catch (e) { notify(e.message, null, "bad"); }
  };
  return (<>
    <h3>Account requests</h3>
    <p className="hint">People ask for an account from the sign-in page (turn that off under Limits & sandbox). Approving creates the account with the username and password they chose, and the server's default limits. With email set up (Admin → Email) they're told by email either way; otherwise let them know yourself.</p>
    {!pending.length && <p className="empty">No requests waiting.</p>}
    {pending.map((r) => (
      <div key={r.id} className="req-card">
        <div className="row"><b>{r.full_name}</b><span className="mono">{r.username}</span><span className="grow" /><span className="hint">{ago(r.created)}{r.ip ? ` · from ${r.ip}` : ""}</span></div>
        <div><a href={`mailto:${r.email}`}>{r.email}</a>{r.org && <span className="hint"> · {r.org}</span>}</div>
        <blockquote>{r.reason}</blockquote>
        <div className="row">
          <label className="chk" title="Full internet from this user's lab devices (otherwise package mirrors only)"><input type="checkbox" checked={!!net[r.id]} onChange={(e) => setNet({ ...net, [r.id]: e.target.checked })} /> Lab internet</label>
          <span className="grow" />
          <button onClick={() => confirm(`Decline ${r.full_name}'s request?`) && act(r, false)}>Decline</button>
          <button className="primary" onClick={() => act(r, true)}>Approve</button>
        </div>
      </div>))}
    {done.length > 0 && <>
      <h3>Declined</h3>
      <p className="hint">Kept for 30 days so the person sees "declined" when they try to sign in, and can't request again with the same email straight away.</p>
      <table className="grid compact"><thead><tr><th>Name</th><th>Username</th><th>Email</th><th>Declined</th><th /></tr></thead>
        <tbody>{done.map((r) => <tr key={r.id}><td>{r.full_name}</td><td className="mono">{r.username}</td><td>{r.email}</td><td>{ago(r.decided)} by {r.by}</td>
          <td><button className="link" onClick={() => act(r, false)}>Forget</button></td></tr>)}</tbody></table></>}
  </>);
}

function Invites({ notify }) {
  const [list, setList] = useState([]);
  const [f, setF] = useState({ role: "user", days: 7, note: "", email: "" });
  const [made, setMade] = useState(null);
  const load = useCallback(() => api.adminInvites().then(setList).catch((e) => notify(e.message, null, "bad")), [notify]);
  useEffect(() => { load(); }, [load]);
  const link = made ? `${location.origin}/invite/${made.token}` : "";
  return (<>
    <h3>Invite links</h3>
    <p className="hint">Each link creates one account. Send it to the person; they pick their own username and password. Links expire, and you can revoke them before they're used.</p>
    <div className="formrow">
      <label>Role<select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="user">user</option><option value="admin">admin</option></select></label>
      <label>Valid for (days)<input className="narrow" value={f.days} onChange={(e) => setF({ ...f, days: e.target.value.replace(/[^\d.]/g, "") })} /></label>
      <label>Email it to <span className="hint">(optional)</span><input type="email" value={f.email} placeholder="name@example.com" onChange={(e) => setF({ ...f, email: e.target.value.trim() })} /></label>
      <label>Note<input value={f.note} placeholder="who it's for" onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
      <button className="primary" onClick={async () => {
        try {
          const r = await api.adminInviteCreate(f); setMade(r);
          if (r.emailed) notify(`Invite emailed to ${f.email}`, null, "ok"); else if (r.email_error) notify("Link created, but the email failed", r.email_error, "warn");
          setF({ ...f, note: "", email: "" }); load();
        } catch (e) { notify(e.message, null, "bad"); }
      }}>{f.email ? "Create and email" : "Create link"}</button>
    </div>
    {made && <div className="secstate on invite-made"><Icon name="flag" /><span className="mono">{link}</span>
      <button onClick={() => { navigator.clipboard?.writeText(link); notify("Link copied", null, "ok"); }}>Copy</button>
      <span className="hint">Shown once.</span></div>}
    <table className="grid compact"><thead><tr><th>Created</th><th>By</th><th>Role</th><th>Note</th><th>State</th><th /></tr></thead>
      <tbody>{list.map((i) => (
        <tr key={i.id}><td>{ago(i.created)}</td><td>{i.by}</td><td>{i.role}</td><td>{i.note}</td>
          <td>{i.state === "used" ? <>used by <b>{i.used_by}</b></> : i.state === "expired" ? <span className="hint">expired</span> : <>open, {Math.max(0, Math.round((i.expires - Date.now() / 1000) / 3600))} h left</>}</td>
          <td>{i.state === "open" && <button className="link danger" onClick={async () => { await api.adminInviteDelete(i.id); load(); }}>Revoke</button>}</td></tr>))}
        {!list.length && <tr><td colSpan={6} className="hint">No invites yet.</td></tr>}</tbody></table>
  </>);
}

function AllLabs({ notify, onOpenLab }) {
  const [labs, setLabs] = useState(null);
  const [users, setUsers] = useState([]);
  const load = useCallback(() => { api.labs().then(setLabs).catch((e) => notify(e.message, null, "bad")); api.adminUsers().then(setUsers).catch(() => {}); }, [notify]);
  useEffect(() => { load(); }, [load]);
  if (!labs) return <p className="hint">Loading…</p>;
  const running = new Set(users.flatMap((u) => u.running));
  return (<>
    <h3>All labs</h3>
    <table className="grid compact"><thead><tr><th>Lab</th><th>Owner</th><th>Devices</th><th>State</th><th>Trusted</th><th /></tr></thead>
      <tbody>{labs.map((l) => (
        <tr key={l.name}><td><b>{l.name}</b></td>
          <td><select value={l.owner} onChange={async (e) => { await api.adminLabOwner(l.name, { owner: e.target.value }); notify(`${l.name} now belongs to ${e.target.value}`, null, "ok"); load(); }}>
            {users.map((u) => <option key={u.name}>{u.name}</option>)}</select></td>
          <td>{l.nodes}</td><td>{running.has(l.name) ? <span className="svc-state svc-up">running</span> : <span className="hint">stopped</span>}</td>
          <td><input type="checkbox" checked={!!l.trusted} title="Run this lab's devices privileged on the shared network, like before multi-user. Only for labs you trust."
            onChange={async (e) => { await api.adminLabOwner(l.name, { owner: l.owner, trusted: e.target.checked }); load(); }} /></td>
          <td><span className="row"><button className="link" onClick={() => onOpenLab(l.name)}>Open</button>
            {running.has(l.name) && <button className="link danger" onClick={async () => { if (confirm(`Destroy ${l.name}? Its owner's running configs are saved first.`)) { await api.destroy(l.name); load(); } }}>Destroy</button>}</span></td></tr>))}</tbody></table>
  </>);
}

function Limits({ notify }) {
  const [s, setS] = useState(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { api.settings().then(setS).catch((e) => notify(e.message, null, "bad")); }, [notify]);
  if (!s) return <p className="hint">Loading…</p>;
  const q = s.users.quota, sb = s.sandbox;
  const upd = (sec, k, v) => { setS({ ...s, [sec]: { ...s[sec], [k]: v } }); setDirty(true); };
  const save = async () => {
    try { const r = await api.saveSettings(s); setS({ ...r, _meta: s._meta }); setDirty(false); notify("Saved", null, "ok"); } catch (e) { notify(e.message, null, "bad"); }
  };
  return (<>
    <h3>Default limits for users</h3>
    <p className="hint">Admins have no limits. Per-user overrides are under Users → Limits. 0 = unlimited.</p>
    <div className="formrow">{QK.map(([k, l]) => (
      <label key={k}>{l}<input className="narrow" value={q[k]} onChange={(e) => upd("users", "quota", { ...q, [k]: e.target.value.replace(/[^\d.]/g, "") })} /></label>))}</div>
    <h3>Sign-in</h3>
    <label className="col">Require two-factor sign-in
      <select value={s.security.require_2fa || "auto"} onChange={(e) => upd("security", "require_2fa", e.target.value)}>
        <option value="auto">Automatic: admins, on a public (sandboxed) server</option><option value="admins">For admins</option>
        <option value="everyone">For everyone</option><option value="off">Not required (anyone can still turn it on)</option></select></label>
    <p className="hint">Someone who must have it and hasn't set it up is taken straight to the setup page when they sign in. Lost phone: Users → Remove 2FA, or on the host <code>docker exec patchyard python -m app.auth reset-2fa NAME</code>.</p>
    <h3>Challenges</h3>
    <label className="chk"><input type="checkbox" checked={s.challenges?.leaderboard !== false} onChange={(e) => upd("challenges", "leaderboard", e.target.checked)} /> Show a leaderboard (usernames, best score, time to complete) for built-in and published challenges</label>
    <h3>Sign-up</h3>
    <label className="chk"><input type="checkbox" checked={s.users.allow_requests !== false} onChange={(e) => upd("users", "allow_requests", e.target.checked)} /> Let people request an account from the sign-in page</label>
    <p className="hint">Requests wait in Admin → Requests until you approve them. Invite links work either way.</p>
    <h3>Images users may choose</h3>
    <p className="hint">Besides the lab defaults (FRR, network-multitool, Alpine). One per line.</p>
    <textarea rows={3} className="mono" value={(s.users.allowed_images || []).join("\n")} onChange={(e) => upd("users", "allowed_images", e.target.value.split("\n"))} />
    <h3>Lab sandbox</h3>
    <p className="hint">For a public server. Lab devices run unprivileged with CPU/memory limits, each lab gets its own management network, and labs can't reach the internet except the package mirrors below (users with "Lab internet" ticked get full access). Turn it off only for a private, trusted install. Changes apply to labs at their next deploy.</p>
    <label className="chk"><input type="checkbox" checked={sb.enabled} onChange={(e) => upd("sandbox", "enabled", e.target.checked)} /> Sandbox lab devices</label>
    <p className="hint">An install made with <code>--public</code> forces this on (PATCHYARD_SANDBOX=1 in deploy/.env).</p>
    <div className="formrow">
      <label>Management pool<input value={sb.mgmt_pool} onChange={(e) => upd("sandbox", "mgmt_pool", e.target.value.trim())} /></label>
      <label>Per-lab prefix<input className="narrow" value={sb.lab_prefix} onChange={(e) => upd("sandbox", "lab_prefix", e.target.value.replace(/\D/g, ""))} /></label>
      <label>Package proxy port<input className="narrow" value={sb.proxy_port} onChange={(e) => upd("sandbox", "proxy_port", e.target.value.replace(/\D/g, ""))} /></label>
    </div>
    <label className="col">Package mirrors labs may reach (one host per line; subdomains included)
      <textarea rows={3} className="mono" value={(sb.proxy_allow || []).join("\n")} onChange={(e) => upd("sandbox", "proxy_allow", e.target.value.split("\n"))} /></label>
    <div className="row"><span className="grow" />{dirty && <span className="hint">Unsaved changes</span>}<button className="primary" disabled={!dirty} onClick={save}>Save</button></div>
  </>);
}

const SECS = [["starttls", "STARTTLS (usually port 587)"], ["ssl", "SSL/TLS (usually port 465)"], ["none", "None (port 25, not recommended)"]];
const NOTIFY = [["requests", "New account requests (to admins)"], ["accounts", "Approvals, declines, invites and set-password links (to the person)"],
  ["password_reset", "“Forgot password?” on the sign-in page"], ["alerts", "Alert digests to users who opt in (Settings → Account)"]];
const PRESETS = [["Microsoft 365", { host: "smtp.office365.com", port: 587, security: "starttls" }], ["Gmail / Google Workspace", { host: "smtp.gmail.com", port: 587, security: "starttls" }],
  ["Fasthosts", { host: "smtp.livemail.co.uk", port: 587, security: "starttls" }], ["SendGrid", { host: "smtp.sendgrid.net", port: 587, security: "starttls", username: "apikey" }],
  ["Mailgun (EU)", { host: "smtp.eu.mailgun.org", port: 587, security: "starttls" }]];

function Email({ notify, me }) {
  const [all, setAll] = useState(null);
  const [info, setInfo] = useState(null);
  const [e, setE] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [to, setTo] = useState(me?.email || "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const load = useCallback(async () => {
    try { const [s, i] = await Promise.all([api.settings(), api.adminEmail()]); setAll(s); setE(s.email); setInfo(i); setDirty(false); }
    catch (x) { notify(x.message, null, "bad"); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);
  if (!e) return <p className="hint">Loading…</p>;
  const upd = (k, v) => { setE({ ...e, [k]: v }); setDirty(true); setResult(null); };
  const save = async () => {
    try { const r = await api.saveSettings({ ...all, email: e }); setAll({ ...r, _meta: all._meta }); setE(r.email); setDirty(false); notify("Email settings saved", null, "ok"); api.adminEmail().then(setInfo).catch(() => {}); return true; }
    catch (x) { notify(x.message, null, "bad"); return false; }
  };
  const test = async () => {
    setBusy(true); setResult(null);
    try {
      if (dirty && !(await save())) { setBusy(false); return; }
      const r = await api.adminEmailTest(to); setResult(r);
      if (r.settings) setE((cur) => ({ ...cur, verified: r.settings.verified, working: r.settings.working, password_set: r.settings.password_set }));
      api.adminEmail().then(setInfo).catch(() => {});
    } catch (x) { setResult({ ok: false, error: x.message }); }
    setBusy(false);
  };
  const state = !e.host || !e.from_addr ? ["off", "Not set up"] : dirty ? ["warn", "Unsaved changes"] : e.working ? ["up", `Working: test email delivered to ${e.verified.to} ${ago(e.verified.at)}`]
    : ["warn", "Saved, not tested yet: send a test to confirm it works"];
  return (<>
    <h3>Outgoing email</h3>
    <div className={`email-state es-${state[0]}`}><span className={`led led-${state[0] === "up" ? "up" : state[0] === "warn" ? "warn" : "off"}`} /> {state[1]}{e.working && !e.enabled ? " (but turned off below)" : ""}</div>
    <p className="hint">Patchyard sends email through your mail provider's SMTP server. Nothing goes out until you turn it on and a test has worked.</p>
    <div className="formrow">
      <label>Provider preset<select value="" onChange={(x) => { const p = PRESETS.find(([n]) => n === x.target.value); if (p) { setE({ ...e, ...p[1] }); setDirty(true); } }}>
        <option value="">Fill in from…</option>{PRESETS.map(([n]) => <option key={n}>{n}</option>)}</select></label>
    </div>
    <div className="formrow">
      <label>SMTP server<input value={e.host} placeholder="smtp.example.com" onChange={(x) => upd("host", x.target.value.trim())} /></label>
      <label>Port<input className="narrow" value={e.port} onChange={(x) => upd("port", x.target.value.replace(/\D/g, ""))} /></label>
      <label>Security<select value={e.security} onChange={(x) => upd("security", x.target.value)}>{SECS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
    </div>
    <div className="formrow">
      <label>Username<input value={e.username} autoComplete="off" placeholder="often the full email address" onChange={(x) => upd("username", x.target.value)} /></label>
      <label>Password<input type="password" autoComplete="new-password" value={e.password} placeholder={e.password_set ? "saved (leave blank to keep)" : "app password or API key"} onChange={(x) => upd("password", x.target.value)} /></label>
      {e.password_set && <button className="link" onClick={() => { setE({ ...e, password: "", password_clear: true, password_set: false }); setDirty(true); }}>Remove saved password</button>}
    </div>
    <div className="formrow">
      <label>From address<input type="email" value={e.from_addr} placeholder="patchyard@yourdomain" onChange={(x) => upd("from_addr", x.target.value.trim())} /></label>
      <label>From name<input value={e.from_name} onChange={(x) => upd("from_name", x.target.value)} /></label>
    </div>
    <div className="formrow">
      <label className="wide">Site address (for links in emails)<input value={e.site_url} placeholder={info?.site_url || "https://lab.example.com"} onChange={(x) => upd("site_url", x.target.value.trim())} />
        <span className="hint">{e.site_url ? "" : info?.site_url ? `Blank uses ${info.site_url} from the installer.` : "Needed for invite, reset and sign-in links."}</span></label>
    </div>
    <div className="formrow">
      <label className="wide">Also send admin emails to <span className="hint">(comma-separated; admins with an email address always get them)</span>
        <input value={(e.admin_to || []).join(", ")} placeholder="it@yourdomain" onChange={(x) => upd("admin_to", x.target.value.split(/[,\s]+/).filter(Boolean))} /></label>
    </div>
    <p className="hint">Admin emails go to: {info?.admins?.length ? info.admins.join(", ") : <b>nobody yet: add your email in Settings → Account, or above</b>}</p>
    <h4>Send emails for</h4>
    {NOTIFY.map(([k, l]) => <label key={k} className="chk block"><input type="checkbox" checked={e.notify[k]} onChange={(x) => upd("notify", { ...e.notify, [k]: x.target.checked })} /> {l}</label>)}
    <label className="chk block"><input type="checkbox" checked={e.allow_invalid_cert} onChange={(x) => upd("allow_invalid_cert", x.target.checked)} /> Accept an invalid TLS certificate <span className="hint">(only for an internal relay you trust)</span></label>
    <h4>Turn it on</h4>
    <label className="chk block"><input type="checkbox" checked={e.enabled} onChange={(x) => upd("enabled", x.target.checked)} /> <b>Send email</b></label>
    <div className="row"><span className="grow" />{dirty && <span className="hint">Unsaved changes</span>}<button disabled={!dirty} onClick={load}>Discard</button><button className="primary" disabled={!dirty} onClick={save}>Save</button></div>
    <h4>Test it</h4>
    <div className="formrow">
      <label>Send a test to<input type="email" value={to} placeholder="you@example.com" onChange={(x) => setTo(x.target.value.trim())} /></label>
      <button className="primary" disabled={busy || !to || !e.host || !e.from_addr} onClick={test}>{busy ? "Sending…" : dirty ? "Save and send test" : "Send test email"}</button>
    </div>
    {result && (result.ok
      ? <div className="secstate on"><Icon name="flag" /> Sent. Check the inbox (and spam) for "Patchyard test email". The settings are now marked as working.</div>
      : <div className="secstate bad"><Icon name="x" /> <span><b>Couldn't send:</b> {result.error}</span></div>)}
    {info?.status?.last_error && <p className="hint">Last failure ({ago(info.status.last_error_at)}): {info.status.last_error}</p>}
    {info?.status && <p className="hint">Since Patchyard started: {info.status.sent} sent, {info.status.failed} failed.</p>}
    <p className="hint">Tip: if the test can't connect, your server's host may block outgoing SMTP; port 587 with STARTTLS is the most likely to be allowed.</p>
  </>);
}

const ACTIONS = { "login": "Signed in", "login.fail": "Failed sign-in", "password.change": "Changed password", "password.reset_request": "Asked for a reset link",
  "password.reset": "Reset password by email", "request.new": "Requested an account", "request.approve": "Approved request", "request.decline": "Declined request",
  "invite.create": "Created invite", "invite.revoke": "Revoked invite", "invite.redeem": "Used invite", "user.create": "Created user", "user.update": "Changed user",
  "user.delete": "Deleted user", "user.reset_password": "Reset password", "user.send_reset": "Emailed reset link", "settings.change": "Changed settings",
  "lab.create": "Created lab", "lab.delete": "Deleted lab", "lab.deploy": "Deployed", "lab.destroy": "Destroyed", "lab.owner": "Moved lab",
  "email.test": "Sent test email", "challenge.start": "Started challenge", "challenge.publish": "Published challenge", "challenge.unpublish": "Unpublished challenge",
  "disk.pause": "Paused labs (disk limit)" };

function Audit({ notify }) {
  const [rows, setRows] = useState(null);
  const [who, setWho] = useState("");
  const [q, setQ] = useState("");
  const load = useCallback(() => api.adminAudit(who, q).then(setRows).catch((e) => notify(e.message, null, "bad")), [who, q, notify]);
  useEffect(() => { const t = setTimeout(load, 250); return () => clearTimeout(t); }, [load]);
  return (<>
    <h3>Audit log</h3>
    <p className="hint">Sign-ins, account changes, settings and lab lifecycle, newest first. Kept in the data folder (audit.log, rotated at 5 MB).</p>
    <div className="formrow">
      <label>User<input value={who} placeholder="any" onChange={(e) => setWho(e.target.value.trim().toLowerCase())} /></label>
      <label>Search<input value={q} placeholder="lab, address, action…" onChange={(e) => setQ(e.target.value)} /></label>
      <button onClick={load}>Refresh</button>
    </div>
    {!rows ? <p className="hint">Loading…</p> : (
      <table className="grid compact audit"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Target</th><th>Detail</th><th>Address</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i} className={r.action === "login.fail" || r.action === "disk.pause" ? "warnrow" : ""}>
            <td title={new Date(r.ts * 1000).toLocaleString()}>{ago(r.ts)}</td><td>{r.who || <span className="hint">—</span>}</td>
            <td>{ACTIONS[r.action] || r.action}</td><td className="mono">{r.target}</td><td className="hint">{r.detail}</td><td className="mono hint">{r.ip}</td></tr>))}
          {!rows.length && <tr><td colSpan={6} className="hint">Nothing yet.</td></tr>}</tbody></table>)}
  </>);
}

const CH_TYPES = [["ntfy", "ntfy (phone push)"], ["teams", "Microsoft Teams (Workflows webhook)"], ["slack", "Slack (incoming webhook)"], ["webhook", "Webhook (JSON POST)"]];
const CH_HINT = {
  ntfy: "A topic on the ntfy server below (or a full https://server/topic). Subscribe to it in the ntfy app. Token: only for a protected topic.",
  teams: "In Teams: the channel's … menu → Workflows → “Post to a channel when a webhook request is received”, then paste the URL it gives you.",
  slack: "In Slack: an app with Incoming Webhooks for the channel; paste its https://hooks.slack.com/… URL.",
  webhook: "Patchyard POSTs JSON (event, severity, rule, lab, node, message, ts). Optional bearer token.",
};

function Channels({ notify }) {
  const [all, setAll] = useState(null);
  const [c, setC] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState({});
  const [labs, setLabs] = useState([]);
  const load = useCallback(async () => {
    try { const s = await api.settings(); setAll(s); setC(s.channels); setDirty(false); setStatus(await api.adminChannelStatus()); setLabs(await api.labs()); }
    catch (x) { notify(x.message, null, "bad"); }
  }, [notify]);
  useEffect(() => { load(); }, [load]);
  if (!c) return <p className="hint">Loading…</p>;
  const upd = (k, v) => { setC({ ...c, [k]: v }); setDirty(true); };
  const setItem = (i, p) => upd("items", c.items.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const save = async () => {
    try { const r = await api.saveSettings({ ...all, channels: c }); setAll({ ...r, _meta: all._meta }); setC(r.channels); setDirty(false); notify("Alert channels saved", null, "ok"); return r.channels; }
    catch (x) { notify(x.message, null, "bad"); return null; }
  };
  return (<>
    <h3>Alert channels</h3>
    <p className="hint">Besides the bell and email digests, alerts can go to phones (ntfy), Teams, Slack or any webhook. Each alert rule can pick a channel (Settings → Alert rules → Send to); otherwise every channel gets the alerts it wants. At most 20 messages per channel in 10 minutes, then a single "more held back" note.</p>
    {c.items.map((x, i) => (
      <div key={x.id || i} className="subbox ch-item">
        <div className="formrow">
          <label>Name<input value={x.name} onChange={(e) => setItem(i, { name: e.target.value })} /></label>
          <label>Type<select value={x.type} onChange={(e) => setItem(i, { type: e.target.value })}>{CH_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="chk"><input type="checkbox" checked={x.enabled} onChange={(e) => setItem(i, { enabled: e.target.checked })} /> On</label>
          <span className="grow" />
          {status[x.id] && <span className={status[x.id].ok ? "hint" : "error"}>{status[x.id].ok ? `last sent ${ago(status[x.id].at)}` : `last failed: ${status[x.id].error}`}</span>}
        </div>
        <div className="formrow">
          <label className="wide">{x.type === "ntfy" ? "Topic" : "Webhook URL"}<input value={x.url} placeholder={x.type === "ntfy" ? "patchyard-alerts-k3x9" : "https://…"} onChange={(e) => setItem(i, { url: e.target.value.trim() })} />
            <span className="hint">{CH_HINT[x.type]}</span></label>
        </div>
        <div className="formrow">
          {(x.type === "ntfy" || x.type === "webhook") && <label>Token<input type="password" autoComplete="new-password" value={x.token || ""} placeholder={x.token_set ? "saved (blank keeps it)" : "optional"} onChange={(e) => setItem(i, { token: e.target.value })} /></label>}
          <label>At least<select value={x.min_severity} onChange={(e) => setItem(i, { min_severity: e.target.value })}>{["info", "warning", "critical"].map((v) => <option key={v}>{v}</option>)}</select></label>
          <label className="chk"><input type="checkbox" checked={x.resolved} onChange={(e) => setItem(i, { resolved: e.target.checked })} /> Also when resolved</label>
          <label>Quiet from<input className="narrow" placeholder="22:00" value={x.quiet?.start || ""} onChange={(e) => setItem(i, { quiet: { ...x.quiet, start: e.target.value.trim() } })} /></label>
          <label>to<input className="narrow" placeholder="07:00" value={x.quiet?.end || ""} onChange={(e) => setItem(i, { quiet: { ...x.quiet, end: e.target.value.trim() } })} /></label>
          <label className="chk"><input type="checkbox" checked={x.quiet?.critical !== false} onChange={(e) => setItem(i, { quiet: { ...x.quiet, critical: e.target.checked } })} /> Critical breaks quiet hours</label>
        </div>
        <div className="formrow">
          <label className="wide">Only these labs <span className="hint">(blank = all)</span>
            <select multiple size={Math.min(4, Math.max(2, labs.length))} value={x.labs || []} onChange={(e) => setItem(i, { labs: [...e.target.selectedOptions].map((o) => o.value) })}>
              {labs.map((l) => <option key={l.name} value={l.name}>{l.name}{l.owner ? ` · ${l.owner}` : ""}</option>)}</select></label>
        </div>
        <div className="row">
          <button className="link danger" onClick={() => upd("items", c.items.filter((_, j) => j !== i))}>Remove</button>
          <span className="grow" />
          <button onClick={async () => {
            const saved = dirty ? await save() : c;
            if (!saved) return;
            const id = saved.items[i]?.id;
            const r = await api.adminChannelTest(id).catch((e) => ({ ok: false, error: e.message }));
            notify(r.ok ? `Test sent to ${x.name}` : `${x.name}: ${r.error}`, null, r.ok ? "ok" : "bad");
            setStatus(await api.adminChannelStatus().catch(() => ({})));
          }}>{dirty ? "Save and send test" : "Send test"}</button>
        </div>
      </div>))}
    <button onClick={() => upd("items", [...c.items, { id: "", name: "Phone", type: "ntfy", url: "", min_severity: "warning", resolved: true, enabled: true, quiet: { start: "", end: "", critical: true }, labs: [] }])}>
      <Icon name="plus" size={14} /> Add channel</button>
    <h3>Settings</h3>
    <div className="formrow">
      <label>Time zone for quiet hours<input value={c.timezone} onChange={(e) => upd("timezone", e.target.value.trim())} placeholder="Europe/London" /></label>
      <label className="wide">ntfy server<input value={c.ntfy_server} onChange={(e) => upd("ntfy_server", e.target.value.trim())} /></label>
    </div>
    <label className="chk block"><input type="checkbox" checked={c.user_ntfy} onChange={(e) => upd("user_ntfy", e.target.checked)} /> Let users get their own labs' alerts on their phone (ntfy topic in Settings → Account)</label>
    <div className="row"><span className="grow" />{dirty && <span className="hint">Unsaved changes</span>}<button disabled={!dirty} onClick={load}>Discard</button><button className="primary" disabled={!dirty} onClick={save}>Save</button></div>
  </>);
}
