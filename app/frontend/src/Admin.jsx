// Admin panel: users, invite links, every lab on the server, limits and the lab sandbox.
import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { Modal, Icon } from "./ui.jsx";

const TABS = [["users", "Users", "grid"], ["invites", "Invites", "plus"], ["labs", "All labs", "layers"], ["limits", "Limits & sandbox", "gear"]];
const QK = [["max_labs", "Labs"], ["max_running_labs", "Running at once"], ["max_devices", "Devices per lab"], ["device_memory_mb", "Memory per device (MB)"], ["device_cpus", "CPUs per device"]];
const ago = (t) => (!t ? "never" : (() => { const s = Date.now() / 1000 - t; return s < 120 ? "just now" : s < 7200 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`; })());

export default function Admin({ onClose, notify, me, onOpenLab }) {
  const [tab, setTab] = useState("users");
  return (
    <Modal title="Admin" onClose={onClose} xwide footer={<button onClick={onClose}>Close</button>}>
      <div className="settings">
        <nav className="set-nav">{TABS.map(([k, l, i]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={i} size={16} />{l}</button>)}</nav>
        <div className="set-body">
          {tab === "users" && <Users notify={notify} me={me} />}
          {tab === "invites" && <Invites notify={notify} />}
          {tab === "labs" && <AllLabs notify={notify} onOpenLab={(n) => { onOpenLab(n); onClose(); }} />}
          {tab === "limits" && <Limits notify={notify} />}
        </div>
      </div>
    </Modal>
  );
}

function Users({ notify, me }) {
  const [list, setList] = useState(null);
  const [edit, setEdit] = useState(null);
  const [nu, setNu] = useState({ username: "", role: "user" });
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
    <table className="grid compact admin-users"><thead><tr><th>User</th><th>Role</th><th>Labs</th><th>Running</th><th>Last sign-in</th><th>Lab internet</th><th /></tr></thead>
      <tbody>{list.map((u) => (
        <tr key={u.name} className={u.disabled ? "hint" : ""}>
          <td><b>{u.name}</b>{u.name === me?.name && <span className="hint"> (you)</span>}{u.disabled && <span className="hint"> · disabled</span>}</td>
          <td><select value={u.role} disabled={u.name === me?.name} onChange={(e) => patch(u.name, { role: e.target.value }, `${u.name} is now ${e.target.value === "admin" ? "an admin" : "a user"}`)}>
            <option value="user">user</option><option value="admin">admin</option></select></td>
          <td>{u.labs}{u.effective_quota.max_labs ? <span className="hint"> / {u.effective_quota.max_labs}</span> : ""}</td>
          <td className="mono">{u.running.join(", ") || "—"}</td>
          <td>{ago(u.last_login)}</td>
          <td><input type="checkbox" checked={u.role === "admin" || u.internet} disabled={u.role === "admin"} title="Full internet from this user's lab devices (otherwise package mirrors only)"
            onChange={(e) => patch(u.name, { internet: e.target.checked })} /></td>
          <td><span className="row">
            <button className="link" onClick={() => setEdit(edit === u.name ? null : u.name)}>Limits</button>
            <button className="link" onClick={() => confirm(`Reset ${u.name}'s password? They'll be signed out.`) && patch(u.name, { reset_password: true })}>Reset password</button>
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
    {edit && <QuotaEdit user={list.find((u) => u.name === edit)} onSave={(q) => { patch(edit, { quota: q }, `Limits for ${edit} saved`); setEdit(null); }} onCancel={() => setEdit(null)} />}
    <h3>Add a user directly</h3>
    <p className="hint">Usually easier: send an invite link (Invites). This creates the account now and shows a generated password once.</p>
    <div className="formrow">
      <label>Username<input value={nu.username} placeholder="alice" onChange={(e) => setNu({ ...nu, username: e.target.value.trim().toLowerCase() })} /></label>
      <label>Role<select value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}><option value="user">user</option><option value="admin">admin</option></select></label>
      <button className="primary" disabled={!/^[a-z][a-z0-9_-]{2,23}$/.test(nu.username)} onClick={async () => {
        try { const r = await api.adminUserCreate(nu); setSecret({ name: r.name, password: r.password }); setNu({ username: "", role: "user" }); load(); } catch (e) { notify(e.message, null, "bad"); }
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

function Invites({ notify }) {
  const [list, setList] = useState([]);
  const [f, setF] = useState({ role: "user", days: 7, note: "" });
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
      <label>Note<input value={f.note} placeholder="who it's for" onChange={(e) => setF({ ...f, note: e.target.value })} /></label>
      <button className="primary" onClick={async () => { try { setMade(await api.adminInviteCreate(f)); setF({ ...f, note: "" }); load(); } catch (e) { notify(e.message, null, "bad"); } }}>Create link</button>
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
