import { useEffect, useState } from "react";
import { api } from "./api.js";
import { labIf, portsOf, nodeById, fmtSpeed } from "./hw.js";
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
      <header><Icon name="link" /><h3>{link.a} <span className="hint">{labIf(lab, link.a, link.a_if)}</span> ↔ {link.b} <span className="hint">{labIf(lab, link.b, link.b_if)}</span></h3>
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>

      <div className="lp-live">
        <div><span className="hint">{link.a} → {link.b}</span><b className="mono">{fmtBps(rate?.ab_bps)}</b></div>
        <div><span className="hint">{link.b} → {link.a}</span><b className="mono">{fmtBps(rate?.ba_bps)}</b></div>
        <Sparkline data={hist || []} w={110} h={34} />
      </div>

      <section>
        <h4>Cable</h4>
        <div className="row"><label>{link.a} port <input className="narrow" value={link.a_if} disabled={deployed || !!portsOf(nodeById(lab, link.a))} onChange={(e) => onEdit("a_if", e.target.value)} title={labIf(lab, link.a, link.a_if)} /></label>
          <label>{link.b} port <input className="narrow" value={link.b_if} disabled={deployed || !!portsOf(nodeById(lab, link.b))} onChange={(e) => onEdit("b_if", e.target.value)} title={labIf(lab, link.b, link.b_if)} /></label></div>
        {(portsOf(nodeById(lab, link.a)) || portsOf(nodeById(lab, link.b))) && <p className="hint">Ports on modular devices are chosen when you plug the cable in. Re-cable to move it. Link speed: {fmtSpeed(Math.min(...[link.a, link.b].map((id, i) => (portsOf(nodeById(lab, id)) || []).find((p) => p.if === (i ? link.b_if : link.a_if))?.kbit || Infinity)))}</p>}
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

      <Failover lab={lab} link={link} deployed={deployed} down={down} notify={notify} />
      <Wireshark lab={lab} link={link} deployed={deployed} notify={notify} />

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

function Failover({ lab, link, deployed, down, notify }) {
  const [job, setJob] = useState(null);
  const poll = async () => {
    const js = await api.failoverJobs(lab.name).catch(() => []);
    const j = js.find((x) => x.link === link.id) || null;
    setJob(j);
    return j;
  };
  useEffect(() => { setJob(null); poll(); }, [link.id]); // eslint-disable-line
  useEffect(() => {
    if (!job || !["starting", "running"].includes(job.state)) return;
    const t = setInterval(poll, 1500);
    return () => clearInterval(t);
  }, [job?.id, job?.state]); // eslint-disable-line
  const r = job?.result;
  const sec = (g) => (!g ? "–" : !g.recovered ? "no recovery" : g.outage < 0.3 ? "no loss" : `${g.outage.toFixed(1)} s (${g.lost} lost)`);
  return (
    <section>
      <h4>Failover test</h4>
      <p className="hint">Pings between hosts 5 times a second, takes this cable down until traffic recovers, brings it back, and reports the outage per flow and which routes moved.</p>
      <div className="row">
        <button disabled={!deployed || down || ["starting", "running"].includes(job?.state)} onClick={async () => {
          try { setJob(await api.failover(lab.name, link.id)); } catch (e) { notify(e.message, null, "bad"); }
        }}><Icon name="zap" size={14} /> {["starting", "running"].includes(job?.state) ? `Running: ${job.step}…` : "Run failover test"}</button>
      </div>
      {job?.state === "failed" && <p className="error">{job.error}</p>}
      {job?.notes?.map((n) => <p key={n} className="hint">{n}</p>)}
      {r && <>
        <table className="grid compact fo"><thead><tr><th>Flow</th><th>On failure</th><th>On restore</th></tr></thead>
          <tbody>{r.flows.map((x) => <tr key={x.src + x.dst}><td>{x.src} → {x.dst}</td>
            <td className={x.on_failure.recovered ? "" : "error"}>{sec(x.on_failure)}</td><td className={x.on_restore.recovered ? "" : "error"}>{sec(x.on_restore)}</td></tr>)}</tbody></table>
        {[["Routes that changed when it failed", r.routes_on_failure], ["…and when it came back", r.routes_on_restore]].map(([t, rows]) => rows.length > 0 && (
          <details key={t}><summary>{t} ({rows.reduce((n, x) => n + x.lost.length + x.new.length + x.moved.length, 0)})</summary>
            {rows.map((x) => <div key={x.router} className="small"><b>{x.router}</b>
              {x.moved.map((m) => <div key={m.prefix} className="mono">{m.prefix}: {m.from} → {m.to}</div>)}
              {x.lost.map((pf) => <div key={pf} className="mono error">{pf}: gone</div>)}
              {x.new.map((pf) => <div key={pf} className="mono">{pf}: new</div>)}</div>)}
          </details>))}
        {!r.routes_on_failure.length && <p className="hint">No routing changes: traffic didn't depend on this cable, or the network has no alternative path.</p>}
      </>}
    </section>
  );
}

function Wireshark({ lab, link, deployed, notify }) {
  const [secs, setSecs] = useState("60");
  const [end, setEnd] = useState("a");
  const url = api.linkPcapUrl(lab.name, link.id, secs, end);
  const live = `curl -sN -H "Authorization: Bearer YOUR_TOKEN" "${location.origin}${api.linkPcapUrl(lab.name, link.id, 0, end)}" | wireshark -k -i -`;
  return (
    <section>
      <h4>Capture to Wireshark</h4>
      <p className="hint">Records this cable with tcpdump and downloads a .pcap you can open in Wireshark. Every packet, full size, from the moment you press it.</p>
      <div className="row">
        <select value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Capture on">
          <option value="a">on {link.a}'s port</option><option value="b">on {link.b}'s port</option></select>
        <select value={secs} onChange={(e) => setSecs(e.target.value)} aria-label="For">
          <option value="10">10 s</option><option value="30">30 s</option><option value="60">1 min</option><option value="300">5 min</option></select>
        <a className={`btn ${deployed ? "" : "disabled"}`} href={deployed ? url : undefined} download
          onClick={() => deployed && notify(`Capturing for ${Number(secs) >= 60 ? `${Number(secs) / 60} min` : `${secs} s`}`, "The download finishes when the capture does.", "info")}>
          <Icon name="download" size={14} /> Capture</a>
      </div>
      <details><summary className="hint">Live in Wireshark</summary>
        <p className="hint">Make an API token (Settings → Account), then on a computer with Wireshark run this. Packets appear as they cross the cable; stop it with Ctrl+C (an hour at most).</p>
        <pre className="eq small">{live}</pre>
        <button className="link" onClick={() => navigator.clipboard?.writeText(live).then(() => notify("Copied", null, "ok"))}>Copy command</button>
      </details>
    </section>
  );
}
