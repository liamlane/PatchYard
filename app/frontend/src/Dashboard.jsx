import { useEffect, useState } from "react";
import { labIf } from "./hw.js";
import { api } from "./api.js";
import { Sparkline, StateChip, fmtBps, fmtBytes, fmtPct, useInterval, Icon } from "./ui.jsx";
import { impairText } from "./canvas.jsx";

function Kpi({ label, value, sub, tone }) {
  return <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}><div className="kpi-v">{value}</div><div className="kpi-l">{label}</div>{sub && <div className="kpi-s">{sub}</div>}</div>;
}

function Frac({ v }) {
  if (!v || !v[1]) return <span className="hint">–</span>;
  return <StateChip ok={v[0] === v[1]} warn={v[0] > 0 && v[0] < v[1]}>{v[0]}/{v[1]}</StateChip>;
}

export default function Dashboard({ lab, states, live, linkHist, log, onFocusNode, onSelectLink, onClose }) {
  const [health, setHealth] = useState(null);
  const [hErr, setHErr] = useState("");
  const running = Object.values(states).filter((s) => s === "running").length;
  useInterval(() => {
    if (!running) return;
    api.health(lab.name).then((h) => { setHealth(h); setHErr(""); }).catch((e) => setHErr(e.message));
  }, 15000, [lab.name, running > 0]);

  const nodes = live?.nodes || {};
  const links = lab.links;
  const ifState = (n, i) => nodes[n]?.ifaces?.[i]?.state;
  const upLinks = links.filter((l) => l.admin !== "down" && (ifState(l.a, l.a_if) === "up" || ifState(l.b, l.b_if) === "up")).length;
  const impaired = links.filter((l) => l.impair).length;
  const downAdmin = links.filter((l) => l.admin === "down").length;
  const total = Object.values(live?.links || {}).reduce((a, r) => a + r.total_bps, 0);
  const cpuList = Object.entries(nodes).filter(([, v]) => v.cpu != null).sort((a, b) => b[1].cpu - a[1].cpu);
  const memTotal = Object.values(nodes).reduce((a, v) => a + (v.mem || 0), 0);
  const topLinks = links.map((l) => ({ l, r: live?.links?.[l.id] })).filter((x) => x.r).sort((a, b) => b.r.total_bps - a.r.total_bps).slice(0, 8);
  const routers = lab.nodes.filter((n) => n.type === "router");
  const proto = (k) => Object.values(health || {}).reduce((a, h) => [a[0] + h[k][0], a[1] + h[k][1]], [0, 0]);

  return (
    <aside className="dash" aria-label="Dashboard">
      <header className="dash-head"><Icon name="dash" /><h2>{lab.name}</h2><span className="hint">live · 10 s</span>
        <button className="icon-btn" onClick={onClose} aria-label="Close dashboard"><Icon name="x" /></button></header>
      <div className="kpis">
        <Kpi label="Devices running" value={`${running}/${lab.nodes.length}`} tone={running === lab.nodes.length && running ? "ok" : running ? "warn" : ""} />
        <Kpi label="Links up" value={`${upLinks}/${links.length}`} sub={downAdmin ? `${downAdmin} admin down` : null} tone={downAdmin ? "bad" : upLinks === links.length && links.length ? "ok" : ""} />
        <Kpi label="Impaired links" value={impaired} tone={impaired ? "warn" : ""} />
        <Kpi label="Throughput" value={fmtBps(total)} sub="all links, both ways" />
        <Kpi label="Lab memory" value={fmtBytes(memTotal)} sub={`${cpuList.length} containers`} />
        <Kpi label="Peak CPU" value={cpuList[0] ? fmtPct(cpuList[0][1].cpu) : "–"} sub={cpuList[0]?.[0]} />
      </div>

      <Reachability lab={lab} running={running} onFocusNode={onFocusNode} />
      <Baseline lab={lab} running={running} />
      <TimeMachine lab={lab} running={running} />
      <Chaos lab={lab} running={running} />

      {routers.length > 0 && (
        <section className="dash-sec">
          <h3>Protocol health <span className="hint">{health ? "every 15 s" : running ? "checking…" : "deploy to check"}</span></h3>
          {hErr && <p className="error">{hErr}</p>}
          {health && (<>
            <div className="row proto-sum">
              <span>OSPF <Frac v={proto("ospf")} /></span><span>BGP <Frac v={proto("bgp")} /></span><span>LDP <Frac v={proto("ldp")} /></span>
            </div>
            <table className="grid compact"><thead><tr><th>Router</th><th>OSPF</th><th>BGP</th><th>LDP</th><th>Routes</th><th>VRFs</th></tr></thead>
              <tbody>{routers.filter((r) => health[r.id]).map((r) => {
                const h = health[r.id];
                return (<tr key={r.id} className="clickable" onClick={() => onFocusNode(r.id)}>
                  <td>{r.id}</td><td><Frac v={h.ospf} /></td><td><Frac v={h.bgp} /></td><td><Frac v={h.ldp} /></td>
                  <td className="mono">{h.routes}</td><td className="mono">{h.vrfs}</td></tr>);
              })}</tbody></table>
          </>)}
        </section>
      )}

      <section className="dash-sec">
        <h3>Busiest links</h3>
        {!topLinks.length && <p className="hint">No traffic samples yet.</p>}
        {topLinks.map(({ l, r }) => (
          <button key={l.id} className="toplink" onClick={() => onSelectLink(l.id)}>
            <span className="tl-name">{l.a} <span className="hint">{labIf(lab, l.a, l.a_if)}</span> ↔ {l.b} <span className="hint">{labIf(lab, l.b, l.b_if)}</span>
              {l.impair && <span className="etag etag-imp mini">⚠ {impairText(l.impair)}</span>}{l.admin === "down" && <span className="etag etag-down mini">down</span>}</span>
            <Sparkline data={linkHist[l.id] || []} w={96} h={22} />
            <span className="mono tl-rate">{fmtBps(r.total_bps)}</span>
          </button>
        ))}
      </section>

      <section className="dash-sec">
        <h3>CPU by device</h3>
        {cpuList.slice(0, 10).map(([n, v]) => (
          <button key={n} className="cpubar" onClick={() => onFocusNode(n)}>
            <span>{n}</span><span className="bar"><span style={{ width: `${Math.min(100, v.cpu)}%` }} className={v.cpu > 80 ? "hot" : v.cpu > 40 ? "warm" : ""} /></span>
            <span className="mono">{fmtPct(v.cpu)}</span><span className="mono hint">{fmtBytes(v.mem)}</span>
          </button>
        ))}
        {!cpuList.length && <p className="hint">Collecting…</p>}
      </section>

      <section className="dash-sec">
        <h3>Activity</h3>
        <div className="activity">{log.slice(-12).reverse().map((e, i) => (
          <div key={i} className={`act act-${e.kind || "info"}`}><span className="mono hint">{e.t.toLocaleTimeString()}</span> {e.msg}</div>))}
        </div>
      </section>
    </aside>
  );
}

function Reachability({ lab, running, onFocusNode }) {
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const hostsN = lab.nodes.filter((n) => n.type === "pc" || n.type === "server").length;
  if (hostsN < 2) return null;
  const go = async () => {
    setBusy(true); setErr("");
    try { setR(await api.reachability(lab.name)); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const hosts = r?.hosts || [];
  return (
    <section className="dash-sec">
      <h3>Reachability <span className="hint">{r ? `${r.ok}/${r.tried} host pairs answer ping · ${new Date(r.ts * 1000).toLocaleTimeString()}` : "ping every PC/server from every other"}</span></h3>
      <div className="row"><button className="primary" disabled={busy || !running} onClick={go}>{busy ? "Pinging…" : r ? "Check again" : "Check connectivity"}</button>
        {!running && <span className="hint">Deploy the lab first.</span>}</div>
      {err && <p className="error">{err}</p>}
      {r && (
        <div className="reach-wrap">
          <table className="reach">
            <thead><tr><th className="hint">from ↓ to →</th>{hosts.map((h) => <th key={h.id} title={h.ip || "no address"}><span className="rv">{h.id}</span></th>)}</tr></thead>
            <tbody>{hosts.map((s) => (
              <tr key={s.id}>
                <th><button className="link" onClick={() => onFocusNode(s.id)}>{s.id}</button><span className="hint mono"> {s.ip || (s.state === "running" ? "no IP" : s.state)}</span></th>
                {hosts.map((d) => {
                  if (s.id === d.id) return <td key={d.id} className="rc self" />;
                  const v = r.results[s.id]?.[d.id];
                  const na = v === undefined;
                  return <td key={d.id} className={`rc ${na ? "na" : v == null ? "bad" : "ok"}`}
                    title={na ? (d.ip ? `${s.id} not running` : `${d.id} has no address`) : v == null ? `${s.id} → ${d.id} (${d.ip}): no reply` : `${s.id} → ${d.id} (${d.ip}): ${v} ms`}>
                    {na ? "–" : v == null ? "✕" : v < 10 ? v.toFixed(1) : Math.round(v)}</td>;
                })}
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Baseline({ lab, running }) {
  const [b, setB] = useState(null);
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { setD(null); api.baseline(lab.name).then((x) => setB(x && x.ts ? x : null)).catch(() => {}); }, [lab.name]);
  const run = async (k) => {
    setBusy(k); setErr("");
    try {
      if (k === "take") { setB(await api.baselineTake(lab.name)); setD(null); }
      else setD(await api.baselineCompare(lab.name));
    } catch (e) { setErr(e.message); }
    setBusy("");
  };
  const t = (ts) => new Date(ts * 1000).toLocaleString();
  const nothing = d && !d.routes.length && !d.neighbours.length && !d.reach.length;
  return (
    <section className="dash-sec baseline">
      <h3>Change rehearsal <span className="hint">{b ? `baseline ${t(b.ts)} · ${b.summary.routes} routes on ${b.summary.routers} routers, ${b.summary.reachable}/${b.summary.pairs} pairs reachable` : "record the network, make your change, see exactly what it did"}</span></h3>
      <div className="row">
        <button disabled={!running || !!busy} onClick={() => run("take")}>{busy === "take" ? "Recording…" : b ? "Retake baseline" : "Take baseline"}</button>
        <button className="primary" disabled={!running || !b || !!busy} onClick={() => run("cmp")}>{busy === "cmp" ? "Comparing…" : "Compare with now"}</button>
        {!running && <span className="hint">Deploy the lab first.</span>}
      </div>
      {err && <p className="error">{err}</p>}
      {nothing && <p className="ok-t">✓ No difference: same routes, same neighbours, same reachability.</p>}
      {d && !nothing && <DiffView d={d} />}
    </section>
  );
}

function DiffView({ d }) {
  return (<>
        {(d.broken.length > 0 || d.fixed.length > 0) && <div className="row wrap">
          {d.broken.map((p) => <span key={p} className="schip s-bad">✗ {p}</span>)}
          {d.fixed.map((p) => <span key={p} className="schip s-ok">✓ {p}</span>)}</div>}
        {d.neighbours.length > 0 && <table className="grid compact"><thead><tr><th>Router</th><th>Neighbour</th><th>Before</th><th>Now</th></tr></thead>
          <tbody>{d.neighbours.map((n, i) => <tr key={i}><td>{n.router}</td><td className="mono">{n.neighbour}</td><td>{n.before}</td><td><b>{n.after}</b></td></tr>)}</tbody></table>}
        {d.routes.length > 0 && <table className="grid compact"><thead><tr><th>Router</th><th>Prefix</th><th /><th>Before</th><th>Now</th></tr></thead>
          <tbody>{d.routes.slice(0, 300).map((r, i) => <tr key={i} className={`chg-${r.change}`}><td>{r.router}</td><td className="mono">{r.prefix}{r.vrf !== "default" ? ` (${r.vrf})` : ""}</td>
            <td><span className={`schip s-${r.change === "lost" ? "bad" : r.change === "gained" ? "ok" : "warn"}`}>{r.change}</span></td>
            <td className="mono small">{r.before || "–"}</td><td className="mono small">{r.after || "–"}</td></tr>)}</tbody></table>}
        {d.routes.length > 300 && <p className="hint">…and {d.routes.length - 300} more route changes.</p>}
        {d.ifaces?.length > 0 && <table className="grid compact"><thead><tr><th>Port</th><th>Before</th><th>Now</th></tr></thead>
          <tbody>{d.ifaces.map((p) => <tr key={p.port}><td className="mono">{p.port}</td><td>{p.before}</td><td><span className={`schip s-${p.after === "up" ? "ok" : "bad"}`}>{p.after}</span></td></tr>)}</tbody></table>}
  </>);
}

const hhmmss = (t) => new Date(t * 1000).toLocaleTimeString();

function TimeMachine({ lab, running }) {
  const [tl, setTl] = useState(null);
  const [sel, setSel] = useState(null);
  const [pt, setPt] = useState(null);
  const [cmp, setCmp] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.history(lab.name).then(setTl).catch(() => {});
  useEffect(() => { setSel(null); setPt(null); setCmp(null); load(); }, [lab.name]); // eslint-disable-line
  useInterval(load, running ? 30000 : 0, [lab.name, running]);
  useEffect(() => {
    const h = (e) => { if (e.detail?.lab === lab.name) api.historyAt(lab.name, e.detail.ts).then((r) => r.i != null && pick(r.i)).catch(() => {}); };
    window.addEventListener("patchyard-timemachine", h);
    return () => window.removeEventListener("patchyard-timemachine", h);
  }); // eslint-disable-line
  const pick = async (i) => {
    setSel(i); setCmp(null); setPt(null);
    try { setPt(await api.historyPoint(lab.name, i)); } catch (e) { setPt({ error: e.message }); }
  };
  const pts = tl?.points || [];
  return (
    <section className="dash-sec timemachine">
      <h3>Time machine <span className="hint">{tl?.enabled === false ? "off (Settings)" : pts.length ? `${pts.length} change point${pts.length > 1 ? "s" : ""} · last check ${tl.sampled ? hhmmss(tl.sampled) : "–"}` : "records routes, neighbours and port states while the lab runs"}</span></h3>
      {!pts.length && <div className="row"><span className="hint">{running ? `Nothing recorded yet: the first sample comes within ${tl?.interval || 30} s.` : "Deploy the lab to start recording."}</span>
        {running > 0 && <button className="link" onClick={async () => { setBusy(true); await api.historyRecord(lab.name).catch(() => {}); await load(); setBusy(false); }} disabled={busy}>Record now</button>}</div>}
      {pts.length > 0 && <>
        <div className="tm-strip" role="listbox" aria-label="Change points">
          {pts.map((p) => <button key={p.i} role="option" aria-selected={sel === p.i} className={`tm-pt ${sel === p.i ? "on" : ""} ${p.first ? "first" : ""}`}
            title={`${hhmmss(p.ts)} · ${p.first ? "first sample" : p.summary}`} onClick={() => pick(p.i)} />)}
        </div>
        <ol className="tm-list">{[...pts].reverse().slice(0, 12).map((p) => (
          <li key={p.i} className={sel === p.i ? "on" : ""}><button className="link" onClick={() => pick(p.i)}>{hhmmss(p.ts)}</button>
            <span className="hint"> {p.first ? `first sample: ${p.routes} routes, ${p.ports_up} ports up` : p.summary}</span></li>))}</ol>
      </>}
      {pt?.error && <p className="error">{pt.error}</p>}
      {pt && !pt.error && <div className="tm-detail">
        <div className="row"><b>{hhmmss(pt.ts)}</b><span className="hint">{pt.prev_ts ? `what changed since ${hhmmss(pt.prev_ts)}` : "the first sample"}</span><span className="grow" />
          <button disabled={!running || busy} onClick={async () => { setBusy(true); try { setCmp(await api.historyCompare(lab.name, sel)); } catch (e) { setCmp({ error: e.message }); } setBusy(false); }}>{busy ? "Comparing…" : "Compare with now"}</button></div>
        {pt.prev_ts ? <DiffView d={pt.changes} /> : <p className="hint">{Object.values(pt.state.routes).reduce((n, r) => n + Object.keys(r).length, 0)} routes on {Object.keys(pt.state.routes).length} routers.</p>}
        {cmp && (cmp.error ? <p className="error">{cmp.error}</p> : <><h4>From then to now</h4>
          {!cmp.changes.routes.length && !cmp.changes.neighbours.length && !cmp.changes.ifaces.length ? <p className="ok-t">✓ The network is the same now.</p> : <DiffView d={cmp.changes} />}</>)}
      </div>}
    </section>
  );
}

function Chaos({ lab, running }) {
  const [j, setJ] = useState(null);
  const [f, setF] = useState({ minutes: 10, every: 60, kinds: ["down"], exclude: [] });
  const [err, setErr] = useState("");
  const load = () => api.chaos(lab.name).then((x) => setJ(x && x.id ? x : null)).catch(() => {});
  useEffect(() => { load(); }, [lab.name]); // eslint-disable-line
  useInterval(load, j?.state === "running" ? 3000 : 0, [j?.state, lab.name]);
  const on = j?.state === "running";
  const kind = (k) => setF({ ...f, kinds: f.kinds.includes(k) ? f.kinds.filter((x) => x !== k) : [...f.kinds, k] });
  const left = on ? Math.max(0, Math.round((j.ends - Date.now() / 1000) / 60)) : 0;
  return (
    <section className="dash-sec chaos">
      <h3>Chaos mode <span className="hint">{on ? `running · ${left} min left` : "break cables at random and score how well the network copes"}</span></h3>
      {!on && <>
        <div className="formrow">
          <label>For<span className="nowrap"><input className="narrow" value={f.minutes} onChange={(e) => setF({ ...f, minutes: e.target.value.replace(/\D/g, "") })} /> min</span></label>
          <label>A fault about every<span className="nowrap"><input className="narrow" value={f.every} onChange={(e) => setF({ ...f, every: e.target.value.replace(/\D/g, "") })} /> s</span></label>
          <label>Faults<div className="seg">{[["down", "Cable down"], ["loss", "40% loss"], ["delay", "300 ms delay"]].map(([k, l]) =>
            <button key={k} type="button" className={f.kinds.includes(k) ? "on" : ""} onClick={() => kind(k)}>{l}</button>)}</div></label>
        </div>
        {lab.links.length > 0 && <details><summary className="hint">Leave some cables alone ({f.exclude.length})</summary>
          <div className="chaos-ex">{lab.links.map((l) => <label key={l.id} className="chk"><input type="checkbox" checked={f.exclude.includes(l.id)}
            onChange={(e) => setF({ ...f, exclude: e.target.checked ? [...f.exclude, l.id] : f.exclude.filter((x) => x !== l.id) })} /> {labIf(lab, l.a, l.a_if) ? `${l.a} ${labIf(lab, l.a, l.a_if)} ↔ ${l.b} ${labIf(lab, l.b, l.b_if)}` : l.id}</label>)}</div></details>}
        <div className="row"><button className="primary" disabled={!running || !f.kinds.length} onClick={async () => {
          setErr("");
          try { setJ(await api.chaosStart(lab.name, { ...f, minutes: Number(f.minutes) || 10, every: Number(f.every) || 60 })); } catch (e) { setErr(e.message); }
        }}><Icon name="zap" size={14} /> Start chaos</button>
          {err && <span className="error">{err}</span>}
          <span className="hint">Every PC and server is pinged from every other every 15 s. The score is how often the ones that worked before still did. Everything is put back at the end.</span></div>
      </>}
      {j && <>
        <div className="row">
          {j.score != null && <span className={`chaos-score ${j.score >= 95 ? "ok" : j.score >= 75 ? "warn" : "bad"}`}>{j.score}<small>%</small></span>}
          <div className="grow">
            <div><b>Resilience</b> <span className="hint">{j.samples.length} samples of {j.baseline_pairs ?? "…"} host pairs{j.state !== "running" ? ` · ${j.state}` : ""}</span></div>
            {j.current && <div className="small">Now: <b>{j.current.link}</b> {j.current.what}</div>}
          </div>
          {on && <button className="danger" onClick={async () => setJ(await api.chaosStop(lab.name))}>Stop and repair</button>}
        </div>
        {j.samples.length > 0 && <div className="chaos-bars" aria-label="Samples">{j.samples.slice(-60).map((s) => (
          <span key={s.t} className={s.ok === s.of ? "ok" : s.ok / s.of >= 0.5 ? "warn" : "bad"} style={{ height: `${Math.max(8, 100 * s.ok / s.of)}%` }}
            title={`${hhmmss(s.t)}: ${s.ok}/${s.of} reachable${s.broken.length ? ` · broken: ${s.broken.join(", ")}` : ""}${s.fault ? ` · during ${s.fault.link} ${s.fault.what}` : ""}`} />))}</div>}
        <ol className="tm-list">{[...j.events].reverse().slice(0, 10).map((e, i) => <li key={i}><span className="mono small">{hhmmss(e.t)}</span> <b>{e.link}</b> <span className="hint">{e.what}</span></li>)}</ol>
        {j.error && <p className="error">{j.error}</p>}
      </>}
    </section>
  );
}
