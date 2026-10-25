import { useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { protoColor } from "./canvas.jsx";

export default function SimPanel({ lab, events, visible, cursor, setCursor, filter, setFilter, playing, setPlaying,
  speed, setSpeed, follow, setFollow, capturing, onCapture, onClear, onStep, notify }) {
  const [detail, setDetail] = useState(null);
  const [src, setSrc] = useState("");
  const [dst, setDst] = useState("");
  const protos = useMemo(() => [...new Set(events.map((e) => e.proto.replace(/^MPLS\//, "")))].sort(), [events]);
  const sel = visible[cursor];
  const t0 = events[0]?.t || 0;
  const hosts = lab.nodes.filter((n) => n.type !== "switch");

  useEffect(() => {
    if (!sel) return setDetail(null);
    let alive = true;
    api.simEvent(lab.name, sel.id).then((d) => alive && setDetail(d)).catch(() => alive && setDetail(null));
    return () => { alive = false; };
  }, [sel?.id, lab.name]);

  useEffect(() => {
    const row = document.querySelector(`[data-ev="${sel?.id}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [sel?.id]);

  const toggle = (p) => { const n = new Set(filter); n.has(p) ? n.delete(p) : n.add(p); setFilter(n); };
  const pdu = async () => {
    if (!src || !dst) return;
    if (!capturing) await onCapture(true);
    try { const r = await api.simPdu(lab.name, src, dst); notify(r.ok ? `Simple PDU ${src} → ${dst}: reply received` : `Simple PDU ${src} → ${dst}: failed`); }
    catch (e) { notify(e.message); }
  };

  return (
    <aside className="sim" aria-label="Simulation panel">
      <div className="sim-bar">
        <button className={capturing ? "rec on" : "rec"} onClick={() => onCapture(!capturing)}>{capturing ? "Stop capture" : "Start capture"}</button>
        <button onClick={() => onStep(-1)} aria-label="Step back">◀</button>
        <button onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
        <button onClick={() => onStep(1)} aria-label="Step forward">▶</button>
        <label className="speed">Speed
          <input type="range" min="0.25" max="4" step="0.25" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
        </label>
        <label className="chk"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Follow live</label>
        <button className="link" onClick={onClear}>Clear</button>
      </div>
      <div className="sim-pdu">
        <span>Add Simple PDU</span>
        <select value={src} onChange={(e) => setSrc(e.target.value)} aria-label="Source device">
          <option value="">source…</option>
          {hosts.map((n) => <option key={n.id}>{n.id}</option>)}
        </select>
        <input placeholder="destination IP or name" value={dst} onChange={(e) => setDst(e.target.value)} />
        <button disabled={!src || !dst} onClick={pdu}>Send ping</button>
      </div>
      <div className="sim-filters">
        {protos.length ? protos.map((p) => (
          <button key={p} className={`chip ${filter.has(p) ? "off" : ""}`} onClick={() => toggle(p)} style={{ "--c": protoColor(p) }}>{p}</button>
        )) : <span className="hint">Protocols appear here as packets are captured.</span>}
      </div>
      <div className="sim-list">
        <table className="grid ev">
          <thead><tr><th>Time (s)</th><th>Last device</th><th>At device</th><th>Type</th><th>Info</th></tr></thead>
          <tbody>
            {visible.map((e, i) => (
              <tr key={e.id} data-ev={e.id} className={i === cursor ? "cur" : ""} onClick={() => { setPlaying(false); setFollow(false); setCursor(i); onStep(0, i); }}>
                <td className="mono">{(e.t - t0).toFixed(3)}</td>
                <td>{e.from}</td><td>{e.to}</td>
                <td><span className="sw" style={{ background: protoColor(e.proto) }} />{e.proto}</td>
                <td className="info">{e.info}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!visible.length && <p className="empty">{capturing ? "Waiting for packets… try Add Simple PDU." : "Start capture to record packets on every link."}</p>}
      </div>
      {detail && (
        <div className="pdu">
          <h3>PDU {detail.id}: {detail.from} → {detail.to}, {detail.len} bytes</h3>
          {detail.layers.map((l, i) => (
            <details key={i} open={i > 0 && i === detail.layers.length - 1}>
              <summary><span className="layer-n">{osi(l.name)}</span>{l.name}</summary>
              <table className="kv"><tbody>{l.fields.map(([k, v]) => <tr key={k}><td>{k}</td><td className="mono">{v}</td></tr>)}</tbody></table>
            </details>
          ))}
        </div>
      )}
    </aside>
  );
}

function osi(name) {
  if (/Ethernet|802|ARP/i.test(name)) return "L2";
  if (/MPLS/i.test(name)) return "L2.5";
  if (/^IP|ICMP|OSPF/i.test(name)) return "L3";
  if (/TCP|UDP/i.test(name)) return "L4";
  return "L7";
}
