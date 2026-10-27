import { useEffect, useRef, useState } from "react";
import { Handle, Position, BaseEdge, EdgeLabelRenderer, getStraightPath, NodeResizer, useStore } from "@xyflow/react";
import { ICONS } from "./icons.jsx";
import { fmtShort } from "./ui.jsx";

const hidden = { opacity: 0, width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0, top: 30, left: "50%", transform: "translate(-50%,-50%)" };

export function DeviceNode({ data, selected }) {
  const Icon = ICONS[data.type] || ICONS.pc;
  const cls = ["dev", selected && "dev-sel", data.cablePick && "dev-pick", `dev-${data.state || "off"}`, data.hit && "dev-hit", data.alert && `dev-al dev-al-${data.alert}`].filter(Boolean).join(" ");
  const cpu = data.cpu;
  return (
    <div className={cls} title={`${data.label} (${data.state === "poweroff" ? "powered off" : data.state || "not deployed"})`}>
      <Handle type="target" position={Position.Top} style={hidden} isConnectable={false} />
      <Handle type="source" position={Position.Top} style={hidden} isConnectable={false} />
      {data.overlay && cpu != null && (
        <span className={`dev-cpu ${cpu > 80 ? "hot" : cpu > 40 ? "warm" : ""}`} title={`CPU ${cpu.toFixed(1)}%`}>{cpu < 10 ? cpu.toFixed(1) : Math.round(cpu)}%</span>
      )}
      {data.alert && <span className="dev-alert" title={`${data.alert} alert firing`}>!</span>}
      <div className="dev-icon"><Icon /></div>
      <div className="dev-label"><span className="dev-dot" />{data.label}</div>
      {data.badge && <div className="dev-badge">{data.badge}</div>}
    </div>
  );
}

export const PROTO_COLOR = {
  ICMP: "#7B4FC9", ARP: "#7A8794", OSPF: "#138A7E", BGP: "#1F5FA8", LDP: "#8A5A2B",
  DNS: "#2E8B3E", HTTP: "#D9731A", DHCP: "#C2407A", TCP: "#4D6275", UDP: "#4D6275", STP: "#B23A6B",
};
export const protoColor = (p) => PROTO_COLOR[(p || "").replace(/^MPLS\//, "")] || "#4D6275";

function dotColor(s) {
  if (s === "up") return "var(--ok)";
  if (s === "down" || s === "lowerlayerdown") return "var(--bad)";
  if (s) return "var(--warn)";
  return "var(--idle)";
}

export function trafficColor(bps) {
  if (bps == null || bps < 2_000) return null;
  if (bps < 1_000_000) return "var(--t1)";
  if (bps < 10_000_000) return "var(--t2)";
  if (bps < 100_000_000) return "var(--t3)";
  return "var(--t4)";
}

export function impairText(im) {
  if (!im) return "";
  const p = [];
  if (im.delay) p.push(`${im.delay}${im.jitter ? `±${im.jitter}` : ""}ms`);
  if (im.loss) p.push(`${im.loss}% loss`);
  if (im.rate) p.push(im.rate >= 1000 ? `${(im.rate / 1000).toFixed(im.rate % 1000 ? 1 : 0)}M` : `${im.rate}k`);
  if (im.corrupt) p.push(`${im.corrupt}% corrupt`);
  if (im.duplicate) p.push(`${im.duplicate}% dup`);
  return p.join(" · ");
}

export function CableEdge({ id, sourceX: x1, sourceY: y1, targetX: x2, targetY: y2, data, selected }) {
  const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1, r = Math.min(36, len / 3);
  const sourceX = x1 + (dx / len) * r, sourceY = y1 + (dy / len) * r;
  const targetX = x2 - (dx / len) * r, targetY = y2 - (dy / len) * r;
  const [path, lx, ly] = getStraightPath({ sourceX, sourceY, targetX, targetY });
  const fibre = data?.media === "fibre", serial = data?.media === "serial";
  const k = 0.14;
  const ax = sourceX + (targetX - sourceX) * k, ay = sourceY + (targetY - sourceY) * k;
  const bx = targetX + (sourceX - targetX) * k, by = targetY + (sourceY - targetY) * k;
  const anim = data?.anim, rate = data?.rate, down = data?.admin === "down";
  const rev = `M ${targetX},${targetY} L ${sourceX},${sourceY}`;
  const total = rate ? rate.ab_bps + rate.ba_bps : 0;
  const tc = data?.overlay ? trafficColor(total) : null;
  const width = tc ? Math.min(9, 3 + Math.log10(1 + total / 1000) * 1.2) : selected ? 4 : 2.5;
  const flowDir = rate && rate.ba_bps > rate.ab_bps ? "rev" : "fwd";
  const flowSpeed = total > 0 ? Math.max(0.35, 2.4 - Math.log10(1 + total / 1000) * 0.45) : 0;
  const im = data?.impair;
  const zoom = useStore((st) => st.transform[2]);
  const showRate = data?.overlay && total >= 2000 && (zoom >= 0.85 || selected);
  return (
    <g className={`cable ${down ? "cable-down" : ""} ${im ? "cable-imp" : ""}`}>
      {selected && <path d={path} className="cable-sel" />}
      {im && !down && <path d={path} className="cable-imp-glow" />}
      <BaseEdge id={id} path={path} style={{
        stroke: down ? "var(--bad)" : tc || (fibre ? "var(--fibre)" : serial ? "var(--serial)" : "var(--copper)"), strokeWidth: width,
        strokeDasharray: down ? "4 6" : fibre && !tc ? "10 4" : serial && !tc ? "2 3" : undefined, transition: "stroke-width .6s, stroke .6s",
      }} />
      {data?.overlay && tc && !down && (
        <path d={flowDir === "fwd" ? path : rev} className="cable-flow" style={{ animationDuration: `${flowSpeed}s` }} />
      )}
      <circle cx={ax} cy={ay} r={5} fill={dotColor(data?.aState)} stroke="var(--panel)" strokeWidth={1.5} />
      <circle cx={bx} cy={by} r={5} fill={dotColor(data?.bState)} stroke="var(--panel)" strokeWidth={1.5} />
      {data?.showIf && (<>
        <text x={ax} y={ay - 10} className="if-label" textAnchor="middle">{data.aIf}</text>
        <text x={bx} y={by - 10} className="if-label" textAnchor="middle">{data.bIf}</text>
      </>)}
      {anim && (
        <g key={anim.key}>
          <rect x={-9} y={-6} width={18} height={12} rx={1.5} fill={protoColor(anim.proto)} stroke="#fff" strokeWidth={1.2}>
            <animateMotion dur={`${anim.dur || 0.7}s`} fill="freeze" path={anim.reverse ? rev : path} />
          </rect>
        </g>
      )}
      {(down || im || showRate) && (
        <EdgeLabelRenderer>
          <div className="edge-tags" style={{ transform: `translate(-50%,-50%) translate(${lx}px,${ly}px)` }}>
            {down && <span className="etag etag-down">✕ link down</span>}
            {!down && im && <span className="etag etag-imp" title="netem impairment">⚠ {im.preset && im.presetLabel ? im.presetLabel : impairText(im)}</span>}
            {!down && showRate && (
              <span className="etag etag-rate" title={`${data.aIf}→ ${fmtShort(rate.ab_bps)}b/s · ←${data.bIf} ${fmtShort(rate.ba_bps)}b/s`}>
                ↑{fmtShort(rate.ab_bps)} ↓{fmtShort(rate.ba_bps)}</span>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </g>
  );
}

const ANN_COLORS = ["yellow", "blue", "green", "orange", "purple", "red", "grey"];

function useEditable(text, onCommit) {
  const [edit, setEdit] = useState(false);
  const [val, setVal] = useState(text);
  const ref = useRef(null);
  useEffect(() => { if (!edit) setVal(text); }, [text, edit]);
  useEffect(() => { if (edit) ref.current?.focus(); }, [edit]);
  const commit = () => { setEdit(false); if (val !== text) onCommit(val); };
  return { edit, setEdit, val, setVal, ref, commit };
}

function ColorDots({ color, onPick }) {
  return (
    <div className="ann-colors nodrag">
      {ANN_COLORS.map((c) => <button key={c} className={`ann-c ac-${c} ${c === color ? "on" : ""}`} onClick={() => onPick(c)} aria-label={`${c} colour`} />)}
    </div>
  );
}

export function NoteNode({ id, data, selected }) {
  const e = useEditable(data.text, (t) => data.onChange(id, { text: t }));
  return (
    <div className={`ann-note ac-${data.color || "yellow"} ${selected ? "sel" : ""}`} onDoubleClick={() => e.setEdit(true)}>
      <NodeResizer isVisible={selected} minWidth={120} minHeight={60} onResizeEnd={(_, p) => data.onChange(id, { x: p.x, y: p.y, w: p.width, h: p.height })} />
      {e.edit
        ? <textarea ref={e.ref} className="nodrag ann-edit" value={e.val} onChange={(x) => e.setVal(x.target.value)} onBlur={e.commit}
            onKeyDown={(x) => { if (x.key === "Escape") e.commit(); x.stopPropagation(); }} />
        : <div className="ann-text">{data.text || <span className="hint">Double-click to write a note</span>}</div>}
      {selected && <ColorDots color={data.color} onPick={(c) => data.onChange(id, { color: c })} />}
    </div>
  );
}

export function AreaNode({ id, data, selected }) {
  const e = useEditable(data.text, (t) => data.onChange(id, { text: t }));
  return (
    <div className={`ann-area ac-${data.color || "blue"} ${selected ? "sel" : ""}`}>
      <NodeResizer isVisible={selected} minWidth={120} minHeight={80} onResizeEnd={(_, p) => data.onChange(id, { x: p.x, y: p.y, w: p.width, h: p.height })} />
      <div className="ann-handle" onDoubleClick={() => e.setEdit(true)}>
        {e.edit
          ? <input ref={e.ref} className="nodrag" value={e.val} onChange={(x) => e.setVal(x.target.value)} onBlur={e.commit}
              onKeyDown={(x) => { if (x.key === "Enter" || x.key === "Escape") e.commit(); x.stopPropagation(); }} />
          : <span>{data.text || "Area"}</span>}
      </div>
      {selected && <ColorDots color={data.color} onPick={(c) => data.onChange(id, { color: c })} />}
    </div>
  );
}
