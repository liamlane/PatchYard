import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon, useInterval, fmtBps } from "./ui.jsx";
import { CodeStep } from "./TwoFactor.jsx";

const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  return s < 60 ? `${Math.floor(s)}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : new Date(ts * 1000).toLocaleString();
};
const dur = (a, b) => { const s = Math.max(0, (b || Date.now() / 1000) - a); return s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${(s / 3600).toFixed(1)}h`; };

export function NotificationsPanel({ onClose, onGoto, notify, onSettings, refreshKey }) {
  const [tab, setTab] = useState("active");
  const [rows, setRows] = useState([]);
  const load = () => api.alerts(tab === "active" ? "firing" : "").then(setRows).catch(() => {});
  useInterval(load, 5000, [tab, refreshKey]);
  const ack = async (id) => { await api.ack(id).catch((e) => notify(e.message, null, "bad")); load(); };
  return (
    <aside className="dash notif" aria-label="Notifications">
      <header className="dash-head"><Icon name="bell" /><h2>Notifications</h2><span className="hint" />
        <button className="icon-btn" onClick={onSettings} title="Alert rules"><Icon name="gear" /></button>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
      <div className="row notif-bar">
        <div className="seg"><button className={tab === "active" ? "on" : ""} onClick={() => setTab("active")}>Active</button><button className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}>History</button></div>
        <span className="grow" />
        {tab === "active" && <button onClick={() => ack(null)} disabled={!rows.some((r) => !r.acked)}>Acknowledge all</button>}
        {tab === "all" && <button onClick={async () => { const r = await api.clearResolved(); notify(`Cleared ${r.deleted} resolved alerts`); load(); }}>Clear resolved</button>}
      </div>
      <div className="notif-list">
        {rows.map((r) => (
          <div key={r.id} className={`alert al-${r.severity} ${r.state} ${r.acked ? "acked" : ""}`}>
            <span className="al-dot" />
            <div className="al-body">
              <div className="al-top"><b>{r.rule}</b><span className={`schip ${r.severity === "critical" ? "s-bad" : r.severity === "warning" ? "s-warn" : "s-ok"}`}>{r.severity}</span>
                {r.state === "resolved" && <span className="schip s-ok">resolved</span>}{r.acked ? <span className="hint">acked</span> : null}</div>
              <div>{r.message}</div>
              <div className="hint">
                <button className="link" onClick={() => onGoto(r.lab, r.node)}>{r.lab} · {r.node}</button>
                {" · "}{ago(r.started)}{" · "}{r.state === "resolved" ? `lasted ${dur(r.started, r.resolved)}` : `for ${dur(r.started)}`}
              </div>
            </div>
            {r.state === "firing" && !r.acked && <button className="link" onClick={() => ack(r.id)}>Ack</button>}
          </div>
        ))}
        {!rows.length && <p className="empty">{tab === "active" ? "All clear. Nothing is firing." : "No alert history yet."}</p>}
      </div>
    </aside>
  );
}

export function TrafficPanel({ lab, states, onClose, notify, preset }) {
  const hosts = lab.nodes.filter((n) => states[n.id] === "running" && (n.raw || {}).kind !== "bridge");
  const [f, setF] = useState({ src: preset?.src || "", dst: "", proto: "tcp", rate_mbps: "", duration: 20, streams: 1, reverse: false });
  const [jobs, setJobs] = useState([]);
  useInterval(() => api.traffic(lab.name).then(setJobs).catch(() => {}), 2000, [lab.name]);
  const start = async () => {
    try { const j = await api.trafficStart(lab.name, { ...f, rate_mbps: Number(f.rate_mbps) || 0 }); notify(`Traffic ${j.src} → ${j.dst} (${j.target}) started`, null, "ok"); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <aside className="dash traffic" aria-label="Traffic generator">
      <header className="dash-head"><Icon name="flow" /><h2>Traffic generator</h2><span className="hint">iperf3</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
      <div className="dash-sec">
        <div className="tg-grid">
          <label>From<select value={f.src} onChange={set("src")}><option value="">source…</option>{hosts.map((n) => <option key={n.id}>{n.id}</option>)}</select></label>
          <label>To<select value={f.dst} onChange={set("dst")}><option value="">destination…</option>{hosts.filter((n) => n.id !== f.src).map((n) => <option key={n.id}>{n.id}</option>)}</select></label>
          <label>Protocol<div className="seg">{["tcp", "udp"].map((p) => <button key={p} className={f.proto === p ? "on" : ""} onClick={() => setF({ ...f, proto: p })}>{p.toUpperCase()}</button>)}</div></label>
          <label>Rate<span className="nowrap"><input className="narrow" type="number" min="0" placeholder={f.proto === "udp" ? "10" : "max"} value={f.rate_mbps} onChange={set("rate_mbps")} /> Mb/s</span></label>
          <label>Duration<span className="nowrap"><input className="narrow" type="number" min="1" max="3600" value={f.duration} onChange={set("duration")} /> s</span></label>
          <label>Streams<input className="narrow" type="number" min="1" max="16" value={f.streams} onChange={set("streams")} /></label>
        </div>
        <label className="chk"><input type="checkbox" checked={f.reverse} onChange={set("reverse")} /> Reverse (destination sends to source)</label>
        <div className="row"><button className="primary" disabled={!f.src || !f.dst} onClick={start}><Icon name="play" size={14} /> Start</button>
          <span className="hint">The destination's first lab address is used. Watch it on the canvas with the traffic overlay (O).</span></div>
      </div>
      <div className="dash-sec">
        <h3>Jobs</h3>
        {jobs.map((j) => (
          <div key={j.id} className={`job job-${j.state}`}>
            <div className="row nowrap"><b>{j.src} → {j.dst}</b><span className="hint mono">{j.target} · {j.proto.toUpperCase()}{j.rate_mbps ? ` · ${j.rate_mbps} Mb/s` : ""}{j.streams > 1 ? ` · ${j.streams}×` : ""}{j.reverse ? " · reverse" : ""}</span>
              <span className="grow" /><span className={`schip ${j.state === "done" ? "s-ok" : j.state === "failed" ? "s-bad" : "s-warn"}`}>{j.state}</span>
              {["starting", "running"].includes(j.state) && <button className="link danger" onClick={() => api.trafficStop(lab.name, j.id)}>Stop</button>}</div>
            {["starting", "running"].includes(j.state) && <div className="bar"><span style={{ width: `${(j.elapsed / j.duration) * 100}%` }} /></div>}
            {j.result && <div className="mono job-r">{fmtBps(j.result.bps)}
              {j.result.retransmits != null && ` · ${j.result.retransmits} retransmits`}
              {j.result.lost_percent != null && ` · ${j.result.lost_percent.toFixed(2)}% loss · ${j.result.jitter_ms?.toFixed(2)} ms jitter`}</div>}
            {j.error && <div className="error">{j.error}</div>}
          </div>
        ))}
        {!jobs.length && <p className="hint">No runs yet. Try a UDP stream through an impaired link and compare loss with the netem setting.</p>}
      </div>
    </aside>
  );
}

const BRAND = <div className="brand"><svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="3" /><circle cx="19" cy="5" r="3" /><circle cx="19" cy="19" r="3" /><path d="M7.6 10.6l8.8-4.2M7.6 13.4l8.8 4.2" /></svg>Patchyard</div>;

export function Login({ onDone, canRequest, canForgot }) {
  const [mode, setMode] = useState(() => (canRequest && location.pathname === "/request-account" ? "request" : "signin"));
  const [user, setUser] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [ticket, setTicket] = useState(null);
  const notice = new URLSearchParams(location.search).get("reset") ? "Password changed. Sign in with it and your two-factor code." : "";
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.login(user.trim().toLowerCase(), pw); if (r.two_factor) setTicket(r.ticket); else onDone(); } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  if (ticket) return <div className="login"><CodeStep ticket={ticket} onDone={onDone} onBack={() => { setTicket(null); setPw(""); }} /></div>;
  if (mode === "forgot") return <Forgot onBack={() => setMode("signin")} />;
  if (mode !== "signin") return <RequestAccount sent={mode === "sent"} onSent={() => setMode("sent")} onBack={() => { setMode("signin"); history.replaceState(null, "", "/"); }} />;
  return (
    <div className="login">
      <form className="login-card" onSubmit={go}>
        {BRAND}
        {notice && <p className="ok-t">{notice}</p>}
        <label className="col">Username<input autoFocus value={user} onChange={(e) => setUser(e.target.value)} autoComplete="username" autoCapitalize="none" /></label>
        <label className="col">Password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" /></label>
        {err && <p className="error">{err}</p>}
        <button className="primary big" disabled={busy || !pw}>Sign in</button>
        {canForgot && <p className="hint"><button type="button" className="link" onClick={() => setMode("forgot")}>Forgot your password?</button></p>}
        {canRequest
          ? <p className="hint">No account? <button type="button" className="link" onClick={() => { setMode("request"); history.replaceState(null, "", "/request-account"); }}>Request one</button>, or use an invite link if you were sent one.</p>
          : <p className="hint">No account? Ask whoever runs this server for an invite link.</p>}
      </form>
    </div>
  );
}

function Forgot({ onBack }) {
  const [login, setLogin] = useState("");
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await api.forgot(login.trim()); setSent(true); } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={go}>
        {BRAND}
        <h3>Reset your password</h3>
        {sent ? <>
          <p>If that matches an account with an email address, a reset link is on its way. It works once, for an hour.</p>
          <p className="hint">Nothing after a few minutes? Check spam, or ask the admin.</p>
        </> : <>
          <label className="col">Username or email<input autoFocus value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoCapitalize="none" /></label>
          {err && <p className="error">{err}</p>}
          <button className="primary big" disabled={busy || !login.trim()}>Email me a reset link</button>
        </>}
        <p className="hint"><button type="button" className="link" onClick={onBack}>Back to sign in</button></p>
      </form>
    </div>
  );
}

export function ResetPage({ token }) {
  const [state, setState] = useState(null);
  const [f, setF] = useState({ password: "", again: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.resetCheck(token).then(setState).catch((e) => setState({ error: e.message })); }, [token]);
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.resetDo(token, f.password); location.href = r.sign_in ? "/?reset=1" : "/"; } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={go}>
        {BRAND}
        {!state && <p className="hint">Checking your link…</p>}
        {state?.error && <><p className="error">{state.error}</p><a href="/">Go to sign in</a></>}
        {state?.ok && <>
          <h3>Choose a new password</h3>
          <p className="hint">For <b>{state.user}</b>. Other sessions of this account will be signed out.</p>
          <input type="text" autoComplete="username" value={state.user} readOnly hidden />
          <label className="col">New password<input type="password" autoFocus value={f.password} autoComplete="new-password" onChange={(e) => setF({ ...f, password: e.target.value })} />
            <span className="hint">At least 10 characters.</span></label>
          <label className="col">Repeat password<input type="password" value={f.again} className={f.again && f.again !== f.password ? "bad" : ""} autoComplete="new-password" onChange={(e) => setF({ ...f, again: e.target.value })} /></label>
          {err && <p className="error">{err}</p>}
          <button className="primary big" disabled={busy || f.password.length < 10 || f.password !== f.again}>Set password and sign in</button>
        </>}
      </form>
    </div>
  );
}

const NAME_OK = (v) => /^[a-z][a-z0-9_-]{2,23}$/.test(v);
const EMAIL_OK = (v) => /^[^@\s<>"']{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(v.trim());

function RequestAccount({ sent, onSent, onBack }) {
  const [f, setF] = useState({ full_name: "", email: "", org: "", username: "", password: "", again: "", reason: "", agree: false, website: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k, fx = (v) => v) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : fx(e.target.value) });
  const ok = f.full_name.trim().length >= 2 && EMAIL_OK(f.email) && NAME_OK(f.username) && f.password.length >= 10 && f.password === f.again
    && f.reason.trim().length >= 10 && f.agree;
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { const { again, ...body } = f; await api.accountRequest(body); onSent(); } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  if (sent) return (
    <div className="login"><div className="login-card">
      {BRAND}
      <h3>Request sent</h3>
      <p>An admin will review it. Once it's approved, sign in with the username and password you just chose.</p>
      <p className="hint">Signing in before then tells you whether it's still waiting.</p>
      <button className="primary big" onClick={onBack}>Back to sign in</button>
    </div></div>);
  return (
    <div className="login">
      <form className="login-card login-wide" onSubmit={go}>
        {BRAND}
        <h3>Request an account</h3>
        <p className="hint">An admin approves every account. Your labs are private to you.</p>
        <div className="login-grid">
          <label className="col">Full name<input autoFocus value={f.full_name} onChange={set("full_name")} autoComplete="name" maxLength={80} /></label>
          <label className="col">Email<input type="email" value={f.email} className={f.email && !EMAIL_OK(f.email) ? "bad" : ""} onChange={set("email")} autoComplete="email" maxLength={254} /></label>
          <label className="col">Organisation (optional)<input value={f.org} onChange={set("org")} autoComplete="organization" maxLength={80} /></label>
          <label className="col">Username<input value={f.username} className={f.username && !NAME_OK(f.username) ? "bad" : ""} autoComplete="username" autoCapitalize="none"
            onChange={set("username", (v) => v.trim().toLowerCase())} />
            <span className="hint">3-24 characters: lowercase letters, digits, _ and -.</span></label>
          <label className="col">Password<input type="password" value={f.password} autoComplete="new-password" onChange={set("password")} />
            <span className="hint">At least 10 characters.</span></label>
          <label className="col">Repeat password<input type="password" value={f.again} className={f.again && f.again !== f.password ? "bad" : ""} autoComplete="new-password" onChange={set("again")} /></label>
        </div>
        <label className="col">What will you use it for?<textarea rows={3} value={f.reason} maxLength={500} onChange={set("reason")}
          placeholder="e.g. CCNA study, practising OSPF and BGP, testing a design before a client rollout" /></label>
        <input className="hp" tabIndex={-1} autoComplete="off" aria-hidden="true" value={f.website} onChange={set("website")} name="website" />
        <label className="chk"><input type="checkbox" checked={f.agree} onChange={set("agree")} /> I'll use this server for personal, educational or other non-commercial work, and I won't use lab devices to attack or scan anything.</label>
        {err && <p className="error">{err}</p>}
        <button className="primary big" disabled={busy || !ok}>Send request</button>
        <p className="hint"><button type="button" className="link" onClick={onBack}>Back to sign in</button></p>
      </form>
    </div>
  );
}

export function InvitePage({ token }) {
  const [state, setState] = useState(null);
  const [f, setF] = useState({ full_name: "", email: "", username: "", password: "", again: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.inviteCheck(token).then((r) => setState(r)).catch((e) => setState({ error: e.message })); }, [token]);
  const okName = /^[a-z][a-z0-9_-]{2,23}$/.test(f.username);
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try { await api.inviteRedeem(token, { full_name: f.full_name, email: f.email, username: f.username, password: f.password }); location.href = "/"; } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <div className="login">
      <form className="login-card" onSubmit={go}>
        {BRAND}
        {!state && <p className="hint">Checking your invite…</p>}
        {state?.error && <><p className="error">{state.error}</p><a href="/">Go to sign in</a></>}
        {state?.ok && <>
          <h3>Create your account</h3>
          <label className="col">Full name<input autoFocus value={f.full_name} autoComplete="name" maxLength={80} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></label>
          <label className="col">Email<input type="email" value={f.email} className={f.email && !EMAIL_OK(f.email) ? "bad" : ""} autoComplete="email" maxLength={254} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="col">Username<input value={f.username} className={f.username && !okName ? "bad" : ""} autoComplete="username" autoCapitalize="none"
            onChange={(e) => setF({ ...f, username: e.target.value.trim().toLowerCase() })} />
            <span className="hint">3-24 characters: lowercase letters, digits, _ and -. Your labs are private to you.</span></label>
          <label className="col">Password<input type="password" value={f.password} autoComplete="new-password" onChange={(e) => setF({ ...f, password: e.target.value })} />
            <span className="hint">At least 10 characters.</span></label>
          <label className="col">Repeat password<input type="password" value={f.again} autoComplete="new-password" onChange={(e) => setF({ ...f, again: e.target.value })} /></label>
          {err && <p className="error">{err}</p>}
          <button className="primary big" disabled={busy || !okName || f.full_name.trim().length < 2 || !EMAIL_OK(f.email) || f.password.length < 10 || f.password !== f.again}>Create account</button>
        </>}
      </form>
    </div>
  );
}
