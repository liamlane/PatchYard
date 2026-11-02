// Path explainer and troubleshooting tickets (side panels).
import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon, useInterval } from "./ui.jsx";
import { ICONS } from "./icons.jsx";

const VERD = { deliver: ["ok", "delivered"], forward: ["ok", "forwards"], send: ["ok", "sends"], drop: ["bad", "drops it"], "no-route": ["bad", "no route"],
  loop: ["bad", "loop"], down: ["bad", "not running"], mgmt: ["warn", "leaves the lab"] };

function Hops({ title, r, onFocus }) {
  if (!r) return null;
  const ok = r.result === "delivered";
  return (
    <div className="trace">
      <div className={`trace-head ${ok ? "ok" : "bad"}`}><b>{title}</b> <span className="mono">{r.src} → {r.dst}{r.dport ? `:${r.dport}` : ""} {r.proto}</span>
        <div>{ok ? "✓ " : "✗ "}{r.reason}</div></div>
      <ol className="hops">
        {r.hops.map((h, i) => {
          const I = ICONS[h.type] || ICONS.pc;
          const [cls, word] = VERD[h.verdict] || ["", h.verdict];
          return (
            <li key={i} className={`hop v-${cls}`}>
              <button className="hop-dev" onClick={() => onFocus?.(h.node)} title="Show on the canvas"><I size={26} /><b>{h.node}</b></button>
              <div className="hop-body">
                <div className="row nowrap">
                  {h.in && <span className="hint">in {h.in}</span>}
                  {h.out && <span className="hint">out {h.out}</span>}
                  <span className="grow" /><span className={`schip s-${cls || "ok"}`}>{word}</span>
                </div>
                {h.route && <div className="mono small">{h.route} {h.protocol ? `(${h.protocol})` : ""} via {h.via}</div>}
                {h.notes.map((n, j) => <div key={j} className="hop-note">{n}</div>)}
                {(h.l2 || []).map((s, j) => <div key={j} className="hop-l2"><Icon name="grid" size={12} /> {s.node}: {s.in} ({s.in_mode}) → {s.out} ({s.out_mode})</div>)}
              </div>
            </li>);
        })}
      </ol>
    </div>
  );
}

export function ExplainPanel({ lab, states, preset, onClose, onFocusNode, notify }) {
  const l3 = lab.nodes.filter((n) => ["pc", "server", "router", "firewall", "isp", "internet"].includes(n.type) && states[n.id] === "running");
  const [f, setF] = useState({ src: preset?.src || l3.find((n) => n.type === "pc")?.id || "", target: preset?.target || "", proto: "icmp", port: "" });
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (preset?.src) setF((x) => ({ ...x, src: preset.src, target: preset.target || x.target })); }, [preset?.k]); // eslint-disable-line
  const go = async (e) => {
    e?.preventDefault(); setBusy(true); setR(null);
    try { setR(await api.explain(lab.name, { ...f, port: f.port ? Number(f.port) : null })); } catch (x) { notify(x.message, null, "bad"); }
    setBusy(false);
  };
  return (
    <aside className="dash explain" aria-label="Path explainer">
      <header className="dash-head"><Icon name="link" /><h2>Explain a path</h2><span className="hint" />
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
      <form className="dash-sec" onSubmit={go}>
        <p className="hint">Why does (or doesn't) traffic get through? Walks the path hop by hop in the running lab: each device's real routing decision, the switches in between, and which ACL, firewall or NAT rule matched.</p>
        <div className="tg-grid">
          <label>From<select value={f.src} onChange={(e) => setF({ ...f, src: e.target.value })}><option value="">device…</option>{l3.map((n) => <option key={n.id}>{n.id}</option>)}</select></label>
          <label>To<input list="explain-targets" value={f.target} placeholder="device, address or name" onChange={(e) => setF({ ...f, target: e.target.value.trim() })} />
            <datalist id="explain-targets">{lab.nodes.map((n) => <option key={n.id} value={n.id} />)}<option value="8.8.8.8" /><option value="www.example.com" /></datalist></label>
          <label>Protocol<div className="seg">{["icmp", "tcp", "udp"].map((p) => <button type="button" key={p} className={f.proto === p ? "on" : ""} onClick={() => setF({ ...f, proto: p })}>{p.toUpperCase()}</button>)}</div></label>
          {f.proto !== "icmp" && <label>Port<input className="narrow" value={f.port} placeholder="80" onChange={(e) => setF({ ...f, port: e.target.value.replace(/\D/g, "") })} /></label>}
        </div>
        <div className="row"><button className="primary" disabled={busy || !f.src || !f.target}><Icon name="play" size={14} /> {busy ? "Tracing…" : "Explain"}</button>
          {!l3.length && <span className="hint">Deploy the lab first.</span>}</div>
      </form>
      {r && <div className="dash-sec">
        <div className={`trace-verdict ${r.ok ? "ok" : "bad"}`}>{r.ok ? "✓ Works both ways" : r.forward.result !== "delivered" ? "✗ Doesn't get there" : "✗ Gets there, but the reply doesn't come back"}</div>
        <Hops title="There" r={r.forward} onFocus={onFocusNode} />
        <Hops title="Back" r={r.reply} onFocus={onFocusNode} />
        <p className="hint">Routing comes from each device's kernel; ACLs, firewall rules and NAT are evaluated from their configuration. Replies through NAT are traced to the translating device.</p>
      </div>}
    </aside>
  );
}

const fmtMin = (m) => (m < 1 ? "under a minute" : m < 90 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`);

export function TicketPanel({ lab, deployed, onClose, onExplain, notify }) {
  const [t, setT] = useState(null);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState("");
  const [now, setNow] = useState(Date.now());
  const load = () => api.ticket(lab.name).then((x) => setT(x && x.id ? x : null)).catch(() => {});
  useEffect(() => { load(); setRes(null); }, [lab.name]); // eslint-disable-line
  useInterval(() => setNow(Date.now()), t && !t.solved && !t.gave_up ? 1000 : 0, [t?.id, t?.solved, t?.gave_up]);
  const act = async (a, label) => {
    setBusy(a);
    try {
      if (a === "new-normal" || a === "new-hard") { setT(await api.ticketNew(lab.name, a === "new-hard" ? "hard" : "normal")); setRes(null); }
      else if (a === "check") { const r = await api.ticketDo(lab.name, "check"); setT(r.ticket); setRes(r.results); if (r.ticket.solved) notify(`Ticket solved in ${fmtMin(r.ticket.minutes)}`, null, "ok"); }
      else if (a === "hint") setT(await api.ticketDo(lab.name, "hint"));
      else if (a === "give-up") { const r = await api.ticketDo(lab.name, "give-up"); setT(r.ticket); notify("Fault undone", r.undone.join("; "), "info"); }
      else if (a === "close") { await api.ticketDo(lab.name, "close"); setT(null); setRes(null); }
    } catch (e) { notify(e.message, null, "bad"); }
    setBusy(label || "");
  };
  const open = t && !t.solved && !t.gave_up;
  return (
    <aside className="dash tickets" aria-label="Troubleshooting tickets">
      <header className="dash-head"><Icon name="bolt" /><h2>Troubleshooting</h2><span className="hint" />
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
      {!t && <div className="dash-sec">
        <p>Patchyard quietly breaks something realistic in this lab and gives you the user's complaint: a shut interface, a wrong mask or gateway, a switch port in the wrong VLAN, a lost OSPF network, a stray ACL, a missing static route. Find it, fix it, press Check.</p>
        <p className="hint">The lab must be deployed and working (hosts that can reach each other). Your fix is real configuration, so save configs or take a snapshot first if you want to keep the original.</p>
        <div className="row">
          <button className="primary" disabled={!deployed || !!busy} onClick={() => act("new-normal")}>{busy === "new-normal" ? "Breaking something…" : "New ticket"}</button>
          <button disabled={!deployed || !!busy} onClick={() => act("new-hard")}>{busy === "new-hard" ? "Breaking two things…" : "Hard ticket (two faults)"}</button>
        </div>
      </div>}
      {t && <div className="dash-sec">
        <div className="ticket-card">
          <div className="row"><b>Ticket #{t.id}</b><span className="schip s-warn">{t.level}</span><span className="grow" />
            <span className="hint">{t.solved ? `solved in ${fmtMin(t.minutes)}` : t.gave_up ? "given up" : `open ${fmtMin((now / 1000 - t.created) / 60)}`}</span></div>
          <blockquote>{t.symptom}</blockquote>
        </div>
        {open && <div className="row">
          <button className="primary" disabled={!!busy} onClick={() => act("check")}><Icon name="flag" size={14} /> {busy === "check" ? "Checking…" : "Check"}</button>
          <button disabled={!!busy || !t.hints_left} onClick={() => act("hint")}>Hint ({t.hints_left} left)</button>
          <button onClick={() => onExplain(t.src, t.target)}><Icon name="link" size={14} /> Explain the path</button>
          <span className="grow" />
          <button className="link danger" disabled={!!busy} onClick={() => confirm("Give up? The fault is revealed and undone.") && act("give-up")}>Give up</button>
        </div>}
        {t.hints.length > 0 && <ol className="hints">{t.hints.map((h, i) => <li key={i}>{h}</li>)}</ol>}
        {res && <ul className="checks">{res.map((r) => <li key={r.label} className={r.ok ? "ok" : "bad"}><span className="ck">{r.ok ? "✓" : "✗"}</span><div>{r.label} <span className="hint">· {r.detail}</span></div></li>)}</ul>}
        {t.faults && <div className="reveal"><b>{t.solved ? "What was broken" : "The fault"}</b><ul>{t.faults.map((f) => <li key={f}>{f}</li>)}</ul></div>}
        {!open && <div className="row"><button onClick={() => act("close")}>Close ticket</button><button className="primary" disabled={!deployed} onClick={() => act("new-normal")}>Next ticket</button></div>}
      </div>}
    </aside>
  );
}
