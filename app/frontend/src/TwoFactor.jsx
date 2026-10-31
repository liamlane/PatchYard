// Two-factor sign-in: the setup flow (QR code, confirm a code, recovery codes), the forced
// setup page when the server requires it, and the account section to manage it.
import { useEffect, useMemo, useState } from "react";
import qrcode from "qrcode-generator";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";

function QR({ text, size = 200 }) {
  const svg = useMemo(() => {
    const q = qrcode(0, "M");
    q.addData(text);
    q.make();
    const n = q.getModuleCount(), cell = size / (n + 8);
    let d = "";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) d += `M${(c + 4) * cell},${(r + 4) * cell}h${cell}v${cell}h-${cell}z`;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="QR code"><rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
  }, [text, size]);
  return <div className="qr" dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function RecoveryCodes({ codes, onDone }) {
  const text = codes.join("\n");
  return (
    <div className="recovery">
      <p><b>Save these recovery codes</b> somewhere safe, like your password manager. Each one signs you in once if you lose your phone. They won't be shown again.</p>
      <pre className="codes">{codes.map((c) => <span key={c}>{c}</span>)}</pre>
      <div className="row">
        <button onClick={() => navigator.clipboard?.writeText(text)}><Icon name="copy" size={14} /> Copy</button>
        <button onClick={() => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([`Patchyard recovery codes\n\n${text}\n`], { type: "text/plain" })); a.download = "patchyard-recovery-codes.txt"; a.click(); }}>
          <Icon name="download" size={14} /> Download</button>
        <span className="grow" />
        {onDone && <button className="primary" onClick={onDone}>I've saved them</button>}
      </div>
    </div>
  );
}

export function Setup2FA({ onDone, notify }) {
  const [s, setS] = useState(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [codes, setCodes] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.twofaSetup().then(setS).catch((e) => setErr(e.message)); }, []);
  const confirm = async (e) => {
    e?.preventDefault(); setBusy(true); setErr("");
    try { const r = await api.twofaEnable(code); setCodes(r.recovery_codes); notify?.("Two-factor sign-in is on", null, "ok"); }
    catch (x) { setErr(x.message); }
    setBusy(false);
  };
  if (codes) return <RecoveryCodes codes={codes} onDone={onDone} />;
  if (!s) return err ? <p className="error">{err}</p> : <p className="hint">Preparing…</p>;
  return (
    <form className="twofa-setup" onSubmit={confirm}>
      <ol className="steps">
        <li>Open an authenticator app on your phone (Microsoft Authenticator, Google Authenticator, 1Password, Bitwarden…) and add an account.</li>
        <li>Scan this code:
          <QR text={s.uri} />
          <span className="hint">Can't scan? Enter this key instead: <code className="mono key">{s.secret.match(/.{1,4}/g).join(" ")}</code></span></li>
        <li>Type the 6-digit code the app shows:
          <input className="code-in" inputMode="numeric" autoComplete="one-time-code" maxLength={7} autoFocus value={code}
            onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ""))} placeholder="123 456" /></li>
      </ol>
      {err && <p className="error">{err}</p>}
      <button className="primary" disabled={busy || code.replace(/\D/g, "").length !== 6}>Turn on two-factor sign-in</button>
    </form>
  );
}

// Full-page setup when the server requires 2FA for this account
export function Require2FA({ me, onDone }) {
  return (
    <div className="login">
      <div className="login-card login-wide">
        <div className="brand">Patchyard</div>
        <h3>Set up two-factor sign-in</h3>
        <p className="hint">This server requires it for {me?.role === "admin" ? "admin accounts" : "every account"}. It takes a minute, and from then on you'll enter a code from your phone when you sign in.</p>
        <Setup2FA onDone={onDone} />
        <p className="hint"><button className="link" onClick={async () => { await api.logout(); location.href = "/"; }}>Sign out</button></p>
      </div>
    </div>
  );
}

// Settings → Account
export function TwoFactorSection({ me, notify, required }) {
  const [on, setOn] = useState(!!me?.two_factor);
  const [mode, setMode] = useState(null);       // "setup" | "codes" | "off"
  const [pw, setPw] = useState("");
  const [codes, setCodes] = useState(null);
  const [left, setLeft] = useState(me?.recovery_left ?? 0);
  if (mode === "setup") return <div className="subbox"><h4>Set up two-factor sign-in</h4><Setup2FA notify={notify} onDone={() => { setOn(true); setLeft(10); setMode(null); if (me) me.two_factor = true; }} /></div>;
  if (codes) return <div className="subbox"><h4>New recovery codes</h4><RecoveryCodes codes={codes} onDone={() => { setCodes(null); setLeft(codes.length); }} /></div>;
  return (<>
    <div className={`email-state es-${on ? "up" : "warn"}`}><span className={`led led-${on ? "up" : "warn"}`} />
      {on ? `Two-factor sign-in is on · ${left} recovery code${left === 1 ? "" : "s"} left` : "Two-factor sign-in is off"}{required && !on ? " (this server requires it)" : ""}</div>
    {!on && <button className="primary" onClick={() => setMode("setup")}>Set up two-factor sign-in</button>}
    {on && mode === null && <div className="row">
      <button onClick={() => setMode("codes")}>New recovery codes</button>
      {!required && <button className="danger" onClick={() => setMode("off")}>Turn off</button>}
      {required && <span className="hint">Required on this server, so it can't be turned off.</span>}
    </div>}
    {on && (mode === "codes" || mode === "off") && <div className="formrow">
      <label>Your password<input type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
      <button onClick={() => { setMode(null); setPw(""); }}>Cancel</button>
      <button className={mode === "off" ? "danger" : "primary"} disabled={!pw} onClick={async () => {
        try {
          if (mode === "off") { await api.twofaDisable(pw); setOn(false); if (me) me.two_factor = false; notify("Two-factor sign-in is off", null, "ok"); }
          else setCodes((await api.twofaRecovery(pw)).recovery_codes);
          setMode(null); setPw("");
        } catch (x) { notify(x.message, null, "bad"); }
      }}>{mode === "off" ? "Turn off two-factor" : "Make new codes"}</button>
    </div>}
  </>);
}

// The code step of sign-in
export function CodeStep({ ticket, onDone, onBack }) {
  const [code, setCode] = useState("");
  const [rec, setRec] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const r = await api.twofaLogin(ticket, code.trim());
      onDone(r.recovery_left != null ? { recovery_left: r.recovery_left } : null);
    } catch (x) { setErr(x.message); if (/sign in again/.test(x.message)) setTimeout(onBack, 1500); }
    setBusy(false);
  };
  return (<form className="login-card" onSubmit={go}>
    <div className="brand">Patchyard</div>
    <h3>Two-factor sign-in</h3>
    <label className="col">{rec ? "Recovery code" : "Code from your authenticator app"}
      <input className={rec ? "" : "code-in"} autoFocus inputMode={rec ? "text" : "numeric"} autoComplete="one-time-code" value={code}
        placeholder={rec ? "abcd-efgh" : "123 456"} onChange={(e) => setCode(rec ? e.target.value : e.target.value.replace(/[^\d ]/g, ""))} /></label>
    {err && <p className="error">{err}</p>}
    <button className="primary big" disabled={busy || (!rec && code.replace(/\D/g, "").length !== 6) || (rec && code.trim().length < 8)}>Verify</button>
    <p className="hint"><button type="button" className="link" onClick={() => { setRec(!rec); setCode(""); setErr(""); }}>{rec ? "Use a code from the app" : "Lost your phone? Use a recovery code"}</button>
      {" · "}<button type="button" className="link" onClick={onBack}>Start again</button></p>
  </form>);
}
