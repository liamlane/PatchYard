import { useMemo, useState } from "react";
import { api } from "./api.js";
import { Modal, Sparkline, TimeChart, Gauge, StateChip, fmtBps, fmtBytes, fmtNum, fmtAgo, useInterval, Icon } from "./ui.jsx";

const RANGES = [["15m", 900], ["1h", 3600], ["6h", 21600], ["24h", 86400], ["48h", 172800]];

export function ChartModal({ lab, node, iface, onClose }) {
  const [range, setRange] = useState(iface ? 3600 : 3600);
  const [metric, setMetric] = useState("bps");
  const [h, setH] = useState(null);
  useInterval(() => api.history(lab, node, iface, range).then(setH).catch(() => setH({ points: [], columns: [] })), 15000, [lab, node, iface, range]);
  const ts = (h?.points || []).map((p) => p[0]);
  const col = (i) => (h?.points || []).map((p) => (p[i] == null ? null : p[i]));
  const series = iface
    ? metric === "bps" ? [{ label: "Received", color: "var(--c-rx)", values: col(1) }, { label: "Sent", color: "var(--c-tx)", values: col(2) }]
      : metric === "pps" ? [{ label: "Received", color: "var(--c-rx)", values: col(3) }, { label: "Sent", color: "var(--c-tx)", values: col(4) }]
        : [{ label: "Drops", color: "var(--bad)", values: col(5) }, { label: "Errors", color: "var(--warn)", values: col(6) }]
    : metric === "mem" ? [{ label: "Memory", color: "var(--c-tx)", values: col(2) }] : [{ label: "CPU", color: "var(--c-rx)", values: col(1) }];
  const fmt = iface ? (metric === "bps" ? fmtBps : metric === "pps" ? (v) => `${fmtNum(v)} pps` : (v) => fmtNum(v))
    : metric === "mem" ? fmtBytes : (v) => `${v.toFixed(v < 10 ? 1 : 0)}%`;
  const vals = series[0].values.filter((v) => v != null);
  const stat = (arr) => arr.length ? { avg: arr.reduce((a, b) => a + b, 0) / arr.length, max: Math.max(...arr), last: arr[arr.length - 1] } : null;
  const st = series.map((s) => stat(s.values.filter((v) => v != null)));
  return (
    <Modal title={`${node}${iface ? ` · ${iface}` : " · CPU / memory"}`} onClose={onClose} wide>
      <div className="row between">
        <div className="seg">{(iface ? [["bps", "Throughput"], ["pps", "Packets"], ["err", "Drops & errors"]] : [["cpu", "CPU"], ["mem", "Memory"]]).map(([k, l]) => (
          <button key={k} className={(metric === k || (!iface && k === "cpu" && metric === "bps")) ? "on" : ""} onClick={() => setMetric(k)}>{l}</button>))}</div>
        <div className="seg">{RANGES.map(([l, v]) => <button key={v} className={range === v ? "on" : ""} onClick={() => setRange(v)}>{l}</button>)}</div>
      </div>
      <TimeChart ts={ts} series={series} fmt={fmt} height={300} />
      <div className="stats">
        {series.map((s, i) => st[i] && (
          <div key={i} className="stat"><span className="sw" style={{ background: s.color }} />{s.label}
            <span>now <b>{fmt(st[i].last)}</b></span><span>avg <b>{fmt(st[i].avg)}</b></span><span>peak <b>{fmt(st[i].max)}</b></span></div>
        ))}
        {!vals.length && <p className="hint">Samples are kept for 48 h. Points every {h?.step ? Math.round(h.step) : 10} s at this range.</p>}
      </div>
    </Modal>
  );
}

export default function MonitorTab({ lab, node, running }) {
  const isRouter = node.type === "router";
  const views = isRouter ? ["Overview", "Routes", "OSPF", "BGP", "LDP", "BFD"] : ["Overview"];
  const [view, setView] = useState("Overview");
  const [live, setLive] = useState(null);
  const [cpuHist, setCpuHist] = useState([]);
  const [ifHist, setIfHist] = useState({});
  const [proto, setProto] = useState(null);
  const [chart, setChart] = useState(null);
  const [err, setErr] = useState("");

  useInterval(() => {
    if (!running) return;
    api.live(lab.name).then((d) => { setLive(d.nodes?.[node.id] || null); setErr(""); }).catch((e) => setErr(e.message));
  }, 5000, [lab.name, node.id, running]);

  useInterval(() => {
    if (!running || view !== "Overview") return;
    api.history(lab.name, node.id, null, 900).then((h) => setCpuHist(h.points.map((p) => p[1]))).catch(() => {});
    const ifs = Object.keys(live?.ifaces || {});
    Promise.all(ifs.map((i) => api.history(lab.name, node.id, i, 900).then((h) => [i, h.points.map((p) => (p[1] == null ? null : p[1] + p[2]))]).catch(() => [i, []])))
      .then((r) => setIfHist(Object.fromEntries(r)));
  }, 15000, [lab.name, node.id, running, view, Object.keys(live?.ifaces || {}).join(",")]);

  useInterval(() => {
    if (!running || view === "Overview") return;
    api.protocols(lab.name, node.id).then(setProto).catch((e) => setErr(e.message));
  }, view === "Overview" ? 0 : 10000, [lab.name, node.id, running, view]);

  if (!running) return <p className="empty">Deploy the lab to see live monitoring. History is kept for 48 h once it runs.</p>;
  const ifs = Object.entries(live?.ifaces || {}).sort(([a], [b]) => (a === "eth0") - (b === "eth0") || a.localeCompare(b, undefined, { numeric: true }));
  return (
    <div className="mon">
      {views.length > 1 && <div className="seg mon-views">{views.map((v) => <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v}</button>)}</div>}
      {err && <p className="error">{err}</p>}
      {view === "Overview" && (<>
        <div className="mon-top">
          <Gauge value={live?.cpu} label="CPU" sub={live?.cpu != null ? `of one core · ${live.cpu_ncpu} on host` : "collecting…"} />
          <Gauge value={live?.mem != null ? live.mem / 2 ** 20 : null} max={512} label="Memory" sub={live?.mem != null ? fmtBytes(live.mem) : "collecting…"} color="var(--c-tx)" />
          <button className="mon-spark" onClick={() => setChart({ iface: null })} title="Open CPU / memory history">
            <span className="hint">CPU · last 15 min</span><Sparkline data={cpuHist} w={220} h={44} color="var(--c-rx)" />
          </button>
        </div>
        <table className="grid mon-if">
          <thead><tr><th>Port</th><th>State</th><th>In</th><th>Out</th><th>Packets in/out</th><th>Drops</th><th>Errors</th><th>15 min</th></tr></thead>
          <tbody>{ifs.map(([n, i]) => (
            <tr key={n} className={`clickable ${n === "eth0" ? "dim" : ""}`} onClick={() => setChart({ iface: n })} title="Open history">
              <td className="mono">{n}{n === "eth0" && <span className="hint"> mgmt</span>}</td>
              <td><span className={`led led-${i.state === "up" ? "up" : i.state === "unknown" ? "warn" : "down"}`} />{i.state}</td>
              <td className="mono">{fmtBps(i.rx_bps)}</td><td className="mono">{fmtBps(i.tx_bps)}</td>
              <td className="mono">{fmtNum(i.rx_pps)} / {fmtNum(i.tx_pps)}</td>
              <td className={`mono ${i.drops ? "warn-t" : ""}`}>{i.drops}</td><td className={`mono ${i.errors ? "bad-t" : ""}`}>{i.errors}</td>
              <td><Sparkline data={ifHist[n] || []} w={110} h={22} /></td>
            </tr>))}
          </tbody>
        </table>
        {!ifs.length && <p className="hint">Waiting for the first sample (every 10 s)…</p>}
      </>)}
      {view === "Routes" && <Routes data={proto?.routes} />}
      {view === "OSPF" && <ProtoTable d={proto?.ospf} cols={[["Neighbor ID", "router_id", "mono"], ["State", (r) => <StateChip ok={r.full} warn={/2-way/i.test(r.state)}>{r.state}</StateChip>], ["Interface", "iface", "mono"], ["Address", "address", "mono"], ["Up", (r) => fmtAgo(r.uptime_ms)], ["Dead in", (r) => r.dead_ms != null ? `${Math.round(r.dead_ms / 1000)}s` : "–"]]} empty="No OSPF neighbours." />}
      {view === "BGP" && <ProtoTable d={proto?.bgp} group={(r) => `${r.vrf === "default" ? "Global" : `VRF ${r.vrf}`} · ${r.afi} · AS${r.local_as}`}
        cols={[["Peer", "peer", "mono"], ["Remote AS", "remote_as", "mono"], ["State", (r) => <StateChip ok={r.up} warn={/Connect|Active|Open/.test(r.state)}>{r.state}</StateChip>], ["Up/down", "uptime", "mono"], ["Pfx rcvd", "pfx_rcd", "mono"], ["Pfx sent", "pfx_snt", "mono"], ["Description", "desc"]]} empty="No BGP sessions." />}
      {view === "LDP" && <ProtoTable d={proto?.ldp} cols={[["Neighbor", "id", "mono"], ["State", (r) => <StateChip ok={r.up}>{r.state}</StateChip>], ["Transport", "transport", "mono"], ["Uptime", "uptime", "mono"]]} empty="No LDP sessions." />}
      {view === "BFD" && <ProtoTable d={proto?.bfd} cols={[["Peer", "peer", "mono"], ["Interface", "iface", "mono"], ["VRF", "vrf"], ["State", (r) => <StateChip ok={r.up}>{r.state}</StateChip>], ["Uptime", (r) => r.uptime != null ? `${r.uptime}s` : "–"]]} empty="No BFD peers (bfdd runs; configure peers to use it)." />}
      {chart && <ChartModal lab={lab.name} node={node.id} iface={chart.iface} onClose={() => setChart(null)} />}
    </div>
  );
}

function ProtoTable({ d, cols, empty, group }) {
  if (!d) return <p className="hint">Loading…</p>;
  const rows = d.rows || [];
  if (!rows.length) return <p className="empty">{d.note && !/^\s*[{[]/.test(d.note) ? d.note : empty}</p>;
  const groups = group ? rows.reduce((m, r) => ((m[group(r)] ||= []).push(r), m), {}) : { "": rows };
  return (<>{Object.entries(groups).map(([g, rs]) => (
    <div key={g} className="ptable">
      {g && <h4>{g} <span className="hint">{rs.filter((r) => r.up).length}/{rs.length} up</span></h4>}
      <table className="grid"><thead><tr>{cols.map(([h]) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rs.map((r, i) => <tr key={i}>{cols.map(([h, k, c]) => <td key={h} className={c || ""}>{typeof k === "function" ? k(r) : r[k] ?? "–"}</td>)}</tr>)}</tbody></table>
    </div>))}</>);
}

const PROTO_TAG = { connected: "C", local: "L", ospf: "O", bgp: "B", static: "S", kernel: "K", isis: "I", rip: "R" };

function Routes({ data }) {
  const [vrf, setVrf] = useState("all");
  const [q, setQ] = useState("");
  const [off, setOff] = useState(new Set(["kernel", "local"]));
  const rows = data?.rows || [];
  const vrfs = useMemo(() => [...new Set(rows.map((r) => r.vrf))], [rows]);
  const protos = useMemo(() => [...new Set(rows.map((r) => r.protocol))].sort(), [rows]);
  if (!data) return <p className="hint">Loading…</p>;
  const shown = rows.filter((r) => (vrf === "all" || r.vrf === vrf) && !off.has(r.protocol) && (!q || r.prefix.includes(q) || r.nexthops.join(" ").includes(q)));
  return (
    <div>
      <div className="row">
        <select value={vrf} onChange={(e) => setVrf(e.target.value)} aria-label="VRF"><option value="all">All VRFs ({vrfs.length})</option>{vrfs.map((v) => <option key={v}>{v}</option>)}</select>
        {protos.map((p) => <button key={p} className={`chip ${off.has(p) ? "off" : ""}`} onClick={() => setOff((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; })}>{PROTO_TAG[p] || "?"} {p}</button>)}
        <input placeholder="Filter prefix or next hop" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="hint">{shown.length} of {rows.length}</span>
      </div>
      <table className="grid routes"><thead><tr><th /><th>Prefix</th><th>VRF</th><th>AD/metric</th><th>Next hop</th><th>Age</th></tr></thead>
        <tbody>{shown.map((r, i) => (
          <tr key={i} className={r.selected ? "" : "dim"}>
            <td className="mono"><span className={`rtag rt-${r.protocol}`}>{PROTO_TAG[r.protocol] || "?"}</span>{r.selected ? ">" : " "}{r.fib ? "*" : " "}</td>
            <td className="mono">{r.prefix}</td><td>{r.vrf}</td>
            <td className="mono">{r.distance != null ? `${r.distance}/${r.metric}` : "–"}</td>
            <td className="mono">{r.nexthops.map((n, j) => <div key={j}>{n}</div>)}</td><td className="mono">{r.uptime}</td>
          </tr>))}</tbody></table>
      <p className="hint"><Icon name="help" size={13} /> &gt; selected · * installed in the kernel (FIB). Kernel and local routes are hidden by default.</p>
    </div>
  );
}
