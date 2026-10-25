import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon, Sparkline, fmtBps } from "./ui.jsx";
import { impairText } from "./canvas.jsx";

const FIELDS = [["delay", "Delay", "ms"], ["jitter", "Jitter", "ms"], ["loss", "Loss", "%"], ["rate", "Rate cap", "kbit/s"], ["duplicate", "Duplicate", "%"], ["corrupt", "Corrupt", "%"]];

export default function LinkPanel({ lab, link, deployed, rate, hist, presets, onEdit, onLinkSaved, notify, onClose, onDelete }) {
  const [f, setF] = useState({});
  const [preset, setPreset] = useState("");
  const [dir, setDir] = useState("both");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState("");
  const [flapS, setFlapS] = useState(10);
  useEffect(() => {
    const im = link.impair || {};
    setF(Object.fromEntries(FIELDS.map(([k]) => [k, im[k] ?? ""])));
    setPreset(im.preset || ""); setDir(im.dir || "both"); setOut("");
  }, [link.id]); // eslint-disable-line
  const down = link.admin === "down";

  const pick = (k) => {
    setPreset(k);
    const p = presets?.[k] || {};
    setF(Object.fromEntries(FIELDS.map(([x]) => [x, p[x] ?? ""])));
  };
  const call = async (fn, okMsg) => {
    setBusy(true);
    try { const r = await fn(); setOut(r.output || ""); if (r.ok === false) notify(`${link.a}–${link.b}: ${okMsg} failed`, r.output, "bad"); else notify(`${link.a}–${link.b}: ${okMsg}`, null, "ok"); return r; }
    catch (e) { setOut(e.message); notify(e.message, null, "bad"); }
    finally { setBusy(false); }
  };
  const apply = async () => {
    const body = { ...Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && Number(v) !== 0).map(([k, v]) => [k, Number(v)])), dir, ...(preset && preset !== "clean" ? { preset } : {}) };
    const has = FIELDS.some(([k]) => body[k]);
    const r = await call(() => api.impair(lab.name, link.id, has ? body : null), has ? `impairment ${impairText(body)}` : "impairment cleared");
    if (r) onLinkSaved(link.id, { impair: has ? { ...body, presetLabel: preset ? presets?.[preset]?.label : undefined } : undefined });
  };
  const clear = async () => {
    const r = await call(() => api.impair(lab.name, link.id, null), "impairment cleared");
    if (r) { onLinkSaved(link.id, { impair: undefined }); pick("clean"); setPreset(""); }
  };
  const setState = async (up) => {
    const r = await call(() => api.linkState(lab.name, link.id, up), up ? "link up" : "link down");
    if (r) onLinkSaved(link.id, { admin: up ? undefined : "down" });
  };

  return (
    <div className="linkpanel" role="dialog" aria-label="Link">
      <header><Icon name="link" /><h3>{link.a} <span className="hint">{link.a_if}</span> ↔ {link.b} <span className="hint">{link.b_if}</span></h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>

      <div className="lp-live">
        <div><span className="hint">{link.a} → {link.b}</span><b className="mono">{fmtBps(rate?.ab_bps)}</b></div>
        <div><span className="hint">{link.b} → {link.a}</span><b className="mono">{fmtBps(rate?.ba_bps)}</b></div>
        <Sparkline data={hist || []} w={110} h={34} />
      </div>

      <section>
        <h4>Cable</h4>
        <div className="row"><label>{link.a} port <input className="narrow" value={link.a_if} disabled={deployed} onChange={(e) => onEdit("a_if", e.target.value)} /></label>
          <label>{link.b} port <input className="narrow" value={link.b_if} disabled={deployed} onChange={(e) => onEdit("b_if", e.target.value)} /></label></div>
        <div className="row"><div className="seg">{["copper", "fibre"].map((m) => <button key={m} className={link.media === m ? "on" : ""} onClick={() => onEdit("media", m)}>{m === "copper" ? "Copper" : "Fibre"}</button>)}</div>
          <button className="link danger" onClick={onDelete}><Icon name="trash" size={14} /> Delete cable</button></div>
        {deployed && <p className="hint">Port changes apply on the next deploy.</p>}
      </section>

      <section>
        <h4>State</h4>
        <div className="row">
          <div className="seg"><button className={!down ? "on ok" : ""} disabled={busy} onClick={() => setState(true)}>Up</button>
            <button className={down ? "on bad" : ""} disabled={busy} onClick={() => setState(false)}>Down</button></div>
          <span className="sep" />
          <button disabled={busy || !deployed || down} onClick={() => call(() => api.flap(lab.name, link.id, flapS), `flapping for ${flapS} s`)}><Icon name="zap" size={14} /> Flap</button>
          <input className="narrow" type="number" min="1" max="600" value={flapS} onChange={(e) => setFlapS(Number(e.target.value) || 10)} aria-label="Flap seconds" /> s
        </div>
        <p className="hint">Down = both ends administratively down, kept across redeploys. Flap = down, wait, up (for failover testing).</p>
      </section>

      <section>
        <h4>Impairment <span className="hint">tc netem</span></h4>
        <div className="presets">{Object.entries(presets || {}).map(([k, p]) => (
          <button key={k} className={`preset ${preset === k ? "on" : ""}`} onClick={() => pick(k)} title={impairText(p) || "no impairment"}>{p.label}</button>))}
        </div>
        <div className="lp-fields">{FIELDS.map(([k, l, u]) => (
          <label key={k}><span>{l}</span><input type="number" min="0" step="any" value={f[k] ?? ""} placeholder="0" onChange={(e) => { setF({ ...f, [k]: e.target.value }); setPreset(""); }} /><span className="hint">{u}</span></label>))}
        </div>
        <div className="row"><span className="hint">Direction</span>
          <div className="seg">{[["both", "Both"], ["a2b", `${link.a} →`], ["b2a", `← ${link.b}`]].map(([k, l]) => <button key={k} className={dir === k ? "on" : ""} onClick={() => setDir(k)}>{l}</button>)}</div></div>
        <div className="row">
          <button className="primary" disabled={busy} onClick={apply}>{deployed ? "Apply impairment" : "Save (applied at deploy)"}</button>
          <button disabled={busy || !link.impair} onClick={clear}>Clear</button>
          <button className="link" disabled={!deployed} onClick={() => api.tc(lab.name, link.id).then((r) => setOut(r.output)).catch((e) => setOut(e.message))}>Show qdisc</button>
        </div>
        {link.impair && <p className="hint">Active: <b>{impairText(link.impair)}</b>{link.impair.dir && link.impair.dir !== "both" ? ` (${link.impair.dir})` : ""}. Kept across redeploys.</p>}
      </section>
      {out && <pre className="eq">{out}</pre>}
    </div>
  );
}
