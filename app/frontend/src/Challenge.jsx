// Lab challenges: the instructions and Check button (side panel), the check editor, and the
// Challenges tab in the Lab manager (built-in and published activities).
import { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";
import { labIf, portsOf, usedPorts } from "./hw.js";
import { labNameFor, me as whoami } from "./me.js";

const TYPES = [
  ["ping", "Ping succeeds (or is blocked)"], ["http", "Web page loads"], ["ios_show", "IOS show output contains"],
  ["ospf_full", "OSPF neighbours FULL"], ["bgp_up", "BGP sessions Established"], ["route", "Route in the table"],
  ["iface_ip", "Interface has an address"], ["dhcp", "Got a DHCP address"], ["service", "Server service running"],
];
const FOR = { ospf_full: ["router"], bgp_up: ["router"], route: ["router"], ios_show: ["router", "switch"], service: ["server"], dhcp: ["pc", "server"] };
const SERVICES = ["dhcp", "dns", "http", "ftp", "tftp", "syslog", "ntp"];
const ago = (t) => { const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : new Date(t * 1000).toLocaleDateString(); };

export function describe(k) {
  const n = k.node || "?";
  switch (k.type) {
    case "ping": return `${n} ${k.expect === "blocked" ? "cannot" : "can"} ping ${k.target || "…"}`;
    case "http": return `${n} can open ${k.url || "…"}${k.match ? ` (shows “${k.match}”)` : ""}`;
    case "ios_show": return `${n}: ${k.command || "show …"} ${k.expect === "absent" ? "does not show" : "shows"} “${k.match || "…"}”`;
    case "ospf_full": return `${n} has ${k.count || 1} OSPF neighbour(s) in FULL`;
    case "bgp_up": return `${n} has ${k.count || 1} BGP session(s) Established`;
    case "route": return `${n} has ${{ ospf: "an OSPF", bgp: "a BGP", static: "a static", connected: "a connected" }[k.protocol] || "a"} route to ${k.prefix || "…"}`;
    case "iface_ip": return `${n} ${k.port || "…"} is ${k.cidr || "…"}`;
    case "dhcp": return `${n} got a DHCP address in ${k.subnet || "…"}`;
    case "service": return `${n} runs ${(k.service || "http").toUpperCase()}`;
    default: return k.type;
  }
}

// Instructions: paragraphs, "- " bullets, `code` and **bold**. No HTML is ever injected.
function Rich({ text }) {
  const inline = (s, key) => s.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("`") && p.endsWith("`") && p.length > 2 ? <code key={`${key}-${i}`}>{p.slice(1, -1)}</code>
    : p.startsWith("**") && p.endsWith("**") && p.length > 4 ? <b key={`${key}-${i}`}>{p.slice(2, -2)}</b> : p));
  const blocks = (text || "").split(/\n{2,}/);
  return <div className="rich">{blocks.map((b, i) => {
    const lines = b.split("\n");
    if (lines.every((l) => /^\s*[-*] /.test(l))) return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*] /, ""), `${i}-${j}`)}</li>)}</ul>;
    const head = lines.filter((l) => !/^\s*[-*] /.test(l)), items = lines.filter((l) => /^\s*[-*] /.test(l));
    return <div key={i}>{head.length > 0 && <p>{head.map((l, j) => <span key={j}>{j > 0 && <br />}{inline(l, `${i}-${j}`)}</span>)}</p>}
      {items.length > 0 && <ul>{items.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*] /, ""), `${i}-i${j}`)}</li>)}</ul>}</div>;
  })}</div>;
}

export function ChallengePanel({ lab, deployed, me, onClose, onChange, notify }) {
  const ch = lab.challenge;
  const [edit, setEdit] = useState(!ch);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { setRes(null); api.challengeResult(lab.name).then((r) => setRes(r && r.results ? r : null)).catch(() => {}); }, [lab.name]);
  const run = async () => {
    setBusy(true);
    try { const r = await api.challengeCheck(lab.name); setRes(r); notify(r.pct === 100 ? "Challenge complete: 100%" : `Score: ${r.pct}%`, `${r.score} of ${r.max} points`, r.pct === 100 ? "ok" : "info"); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  const fb = ch?.feedback || "full";
  return (
    <aside className="dash challenge" aria-label="Challenge">
      <header className="dash-head"><Icon name="flag" /><h2>{edit ? "Edit challenge" : ch?.title || "Challenge"}</h2><span className="hint" />
        {ch && <button className="icon-btn" title={edit ? "Back to the challenge" : "Edit the challenge"} onClick={() => setEdit(!edit)}><Icon name={edit ? "play" : "gear"} /></button>}
        <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
      {edit ? <Editor lab={lab} me={me} onSave={(c) => { onChange(c); if (c) setEdit(false); }} notify={notify} onCancel={ch ? () => setEdit(false) : null} />
        : <>
          <div className="dash-sec"><Rich text={ch.instructions} /></div>
          <div className="dash-sec">
            <div className="row">
              <button className="primary" disabled={busy || !deployed} onClick={run}><Icon name="flag" size={14} /> {busy ? "Checking…" : "Check"}</button>
              {!deployed && <span className="hint">Deploy the lab first: checks look at the running network.</span>}
              <span className="grow" />
              {res?.best != null && <span className="hint">best {res.best}%</span>}
            </div>
            {res && <>
              <div className={`score ${res.pct === 100 ? "done" : ""}`}><b>{res.pct}%</b><span>{res.score} / {res.max} points · {ago(res.ts)}</span></div>
              <div className="bar"><span style={{ width: `${res.pct}%` }} className={res.pct === 100 ? "ok" : ""} /></div>
              {res.completed && <p className="done-t">Completed {new Date(res.completed * 1000).toLocaleString()}.</p>}
              {fb !== "score" && <ul className="checks">{res.results.map((r) => (
                <li key={r.id} className={r.ok ? "ok" : "bad"}><span className="ck">{r.ok ? "✓" : "✗"}</span>
                  <div><span>{r.label}</span>{fb === "full" && <span className="hint"> · {r.detail}</span>}</div><span className="pts">{r.points}</span></li>))}</ul>}
              {fb === "score" && <p className="hint">This challenge only shows the score. Keep going!</p>}
            </>}
            {!res && fb !== "score" && <>
              <button className="link" onClick={() => setShowAll(!showAll)}>{showAll ? "Hide" : "Show"} what will be checked ({ch.checks.length})</button>
              {showAll && <ul className="checks pending">{ch.checks.map((k) => <li key={k.id}><span className="ck">·</span><div>{k.label || describe(k)}</div><span className="pts">{k.points}</span></li>)}</ul>}
            </>}
          </div>
        </>}
    </aside>
  );
}

function Editor({ lab, me, onSave, onCancel, notify }) {
  const [c, setC] = useState(() => JSON.parse(JSON.stringify(lab.challenge || { title: "", instructions: "", feedback: "full", checks: [] })));
  const [pub, setPub] = useState(null);
  const nodes = lab.nodes;
  const set = (k, v) => setC({ ...c, [k]: v });
  const setCk = (i, patch) => set("checks", c.checks.map((k, j) => (j === i ? { ...k, ...patch } : k)));
  const add = () => set("checks", [...c.checks, { id: `c${Date.now().toString(36)}`, type: "ping", node: nodes.find((n) => n.type === "pc")?.id || nodes[0]?.id || "", target: "", points: 1 }]);
  const move = (i, d) => { const a = [...c.checks]; const [x] = a.splice(i, 1); a.splice(i + d, 0, x); set("checks", a); };
  const total = c.checks.reduce((s, k) => s + (Number(k.points) || 1), 0);
  return (
    <div className="dash-sec ch-edit">
      <p className="hint">A challenge is instructions plus checks that run against the deployed lab. It's saved in the lab, so it travels with clone and export.</p>
      <label className="col">Title<input value={c.title} maxLength={120} placeholder="Finish the OSPF triangle" onChange={(e) => set("title", e.target.value)} /></label>
      <label className="col">Instructions <span className="hint">(blank line = new paragraph, "- " = bullet, `code`, **bold**)</span>
        <textarea rows={8} value={c.instructions} onChange={(e) => set("instructions", e.target.value)} placeholder={"What's broken, what to build, and when to press Check."} /></label>
      <label className="col">Players see
        <select value={c.feedback} onChange={(e) => set("feedback", e.target.value)}>
          <option value="full">each check, pass or fail, and why</option><option value="labels">each check, pass or fail</option><option value="score">only the score</option></select></label>
      <h3>Checks <span className="hint">{c.checks.length} · {total} points</span></h3>
      {c.checks.map((k, i) => <CheckRow key={k.id || i} k={k} i={i} n={c.checks.length} lab={lab} nodes={nodes} onChange={(p) => setCk(i, p)} onMove={(d) => move(i, d)}
        onDelete={() => set("checks", c.checks.filter((_, j) => j !== i))} />)}
      <button onClick={add} disabled={!nodes.length}><Icon name="plus" size={14} /> Add check</button>
      <div className="row ch-actions">
        {lab.challenge && <button className="link danger" onClick={() => confirm("Remove the challenge from this lab?") && onSave(null)}>Remove challenge</button>}
        <span className="grow" />
        {onCancel && <button onClick={onCancel}>Cancel</button>}
        <button className="primary" disabled={!c.checks.length} onClick={() => { const bad = c.checks.findIndex((k) => !k.node); if (bad >= 0) return notify(`Check ${bad + 1}: pick a device`, null, "bad"); onSave({ ...c, title: c.title || lab.name }); }}>Save challenge</button>
      </div>
      {me?.role === "admin" && lab.challenge && <div className="subbox">
        <h4>Publish to everyone on this server</h4>
        <p className="hint">Copies the lab as it is now (design and saved configs) into Lab manager → Challenges. Each player gets their own copy. Save configs first so the starting state is right.</p>
        {pub === null ? <button onClick={() => setPub({ desc: "", level: "Beginner" })}>Publish…</button> : <>
          <label className="col">Short description<input value={pub.desc} maxLength={400} onChange={(e) => setPub({ ...pub, desc: e.target.value })} /></label>
          <label className="col">Level<select value={pub.level} onChange={(e) => setPub({ ...pub, level: e.target.value })}>{["Beginner", "Intermediate", "Advanced"].map((l) => <option key={l}>{l}</option>)}</select></label>
          <div className="row"><button onClick={() => setPub(null)}>Cancel</button><button className="primary" onClick={async () => {
            try { const m = await api.adminChallengePublish({ lab: lab.name, ...pub }); notify(`Published “${m.title}”`, "Everyone sees it in Lab manager → Challenges.", "ok"); setPub(null); }
            catch (e) { notify(e.message, null, "bad"); }
          }}>Publish</button></div></>}
      </div>}
    </div>
  );
}

function CheckRow({ k, i, n, lab, nodes, onChange, onMove, onDelete }) {
  const allowed = FOR[k.type];
  const opts = nodes.filter((x) => !allowed || allowed.includes(x.type));
  const node = nodes.find((x) => x.id === k.node);
  const ports = useMemo(() => (!node ? [] : (portsOf(node) || []).map((p) => p.if).concat([...usedPorts(lab, node.id)]).filter((p, j, a) => a.indexOf(p) === j)), [node, lab]);
  const f = (key, props = {}) => <input {...props} value={k[key] ?? ""} onChange={(e) => onChange({ [key]: e.target.value })} />;
  return (
    <div className="ck-row">
      <div className="row">
        <span className="ck-n">{i + 1}</span>
        <select value={k.type} onChange={(e) => onChange({ type: e.target.value })}>{TYPES.map(([t, l]) => <option key={t} value={t}>{l}</option>)}</select>
        <select value={k.node} onChange={(e) => onChange({ node: e.target.value })} className={!opts.some((x) => x.id === k.node) ? "bad" : ""}>
          <option value="">device…</option>{opts.map((x) => <option key={x.id}>{x.id}</option>)}</select>
        <span className="grow" />
        <label className="pts-in" title="Points">pts<input className="narrow" value={k.points ?? 1} onChange={(e) => onChange({ points: e.target.value.replace(/\D/g, "") })} /></label>
        <button className="icon-btn" disabled={i === 0} onClick={() => onMove(-1)} title="Move up">↑</button>
        <button className="icon-btn" disabled={i === n - 1} onClick={() => onMove(1)} title="Move down">↓</button>
        <button className="icon-btn" onClick={onDelete} title="Delete check"><Icon name="trash" size={14} /></button>
      </div>
      <div className="row ck-fields">
        {k.type === "ping" && <>{f("target", { placeholder: "192.168.3.10 or www.lab" })}
          <select value={k.expect || "reach"} onChange={(e) => onChange({ expect: e.target.value })}><option value="reach">gets replies</option><option value="blocked">gets no replies (blocked)</option></select></>}
        {k.type === "http" && <>{f("url", { placeholder: "http://www.lab" })}{f("match", { placeholder: "page contains (optional)" })}</>}
        {k.type === "ios_show" && <>{f("command", { placeholder: "show ip ospf neighbor" })}
          <select value={k.expect || "contains"} onChange={(e) => onChange({ expect: e.target.value })}><option value="contains">contains</option><option value="absent">does not contain</option></select>
          {f("match", { placeholder: "FULL" })}<label className="chk"><input type="checkbox" checked={!!k.regex} onChange={(e) => onChange({ regex: e.target.checked })} /> regex</label></>}
        {(k.type === "ospf_full" || k.type === "bgp_up") && <label>at least {f("count", { className: "narrow", placeholder: "1" })}</label>}
        {k.type === "route" && <>{f("prefix", { placeholder: "192.168.3.0/24" })}
          <select value={k.protocol || "any"} onChange={(e) => onChange({ protocol: e.target.value })}>{["any", "connected", "static", "ospf", "bgp"].map((p) => <option key={p}>{p}</option>)}</select></>}
        {k.type === "iface_ip" && <><select value={k.port || ""} onChange={(e) => onChange({ port: e.target.value })}><option value="">port…</option>
          {ports.map((p) => <option key={p} value={p}>{labIf(lab, k.node, p)}</option>)}</select>{f("cidr", { placeholder: "10.0.12.1/30" })}</>}
        {k.type === "dhcp" && <>{f("subnet", { placeholder: "192.168.10.0/24" })}</>}
        {k.type === "service" && <select value={k.service || "http"} onChange={(e) => onChange({ service: e.target.value })}>{SERVICES.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}</select>}
      </div>
      <input className="ck-label" value={k.label || ""} placeholder={describe(k)} onChange={(e) => onChange({ label: e.target.value })} aria-label="What players see (optional)" />
    </div>
  );
}

export function ChallengeList({ notify, onStart, close }) {
  const [d, setD] = useState(null);
  const [names, setNames] = useState({});
  const [busy, setBusy] = useState("");
  const load = () => api.challenges().then(setD).catch((e) => notify(e.message, null, "bad"));
  useEffect(() => { load(); }, []); // eslint-disable-line
  if (!d) return <p className="hint">Loading…</p>;
  const all = [...d.published, ...d.builtin];
  const start = async (c) => {
    const name = (names[c.id] || labNameFor(c.kind === "builtin" ? c.id : (c.title || "challenge").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 20))).trim();
    setBusy(c.id);
    try { await api.challengeStart(c.kind, c.id, name); notify(`Started “${c.title}” as ${name}`, "The instructions are in the Challenge panel.", "ok"); close(); onStart(name); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy("");
  };
  return (<>
    <p className="hint">Activities with instructions and a Check button that scores your running lab. Each one starts as your own copy. Make your own from any lab: open the Challenge panel (flag button) and add checks.</p>
    <div className="tpl-grid">
      {all.map((c) => (
        <div key={`${c.kind}-${c.id}`} className="tpl">
          <h3>{c.title} <span className="hint">{c.devices} devices · {c.checks} checks</span></h3>
          <p>{c.desc}</p>
          <p className="hint">{c.level}{c.kind === "published" ? ` · published by ${c.by} ${ago(c.created)}` : " · built in"}</p>
          <div className="row">
            <input placeholder={labNameFor(c.kind === "builtin" ? c.id : "challenge")} value={names[c.id] ?? ""} onChange={(e) => setNames({ ...names, [c.id]: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} aria-label="New lab name" />
            <button className="primary" disabled={busy === c.id} onClick={() => start(c)}>{busy === c.id ? "Starting…" : "Start"}</button>
            {c.kind === "published" && whoami()?.role === "admin" && <button className="link danger" title="Admins: remove it from the list" onClick={async () => {
              if (!confirm(`Unpublish “${c.title}”? Labs people already started are kept.`)) return;
              try { await api.adminChallengeUnpublish(c.id); load(); } catch (e) { notify(e.message, null, "bad"); }
            }}>Unpublish</button>}
          </div>
        </div>))}
    </div>
  </>);
}
