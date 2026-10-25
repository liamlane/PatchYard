import { useState } from "react";
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
            <span className="tl-name">{l.a} <span className="hint">{l.a_if}</span> ↔ {l.b} <span className="hint">{l.b_if}</span>
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
