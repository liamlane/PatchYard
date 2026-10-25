import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

// ---------- formatters ----------
export const fmtBps = (v) => {
  if (v == null || isNaN(v)) return "–";
  const u = ["b/s", "kb/s", "Mb/s", "Gb/s"]; let i = 0;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
};
export const fmtBytes = (v) => {
  if (v == null) return "–";
  const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${u[i]}`;
};
export const fmtShort = (v) => {
  if (v == null || isNaN(v)) return "–";
  const u = ["", "k", "M", "G"]; let i = 0;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v >= 10 || i === 0 ? v.toFixed(0) : v.toFixed(1)}${u[i]}`;
};
export const fmtPct = (v) => (v == null ? "–" : `${v < 10 ? v.toFixed(1) : v.toFixed(0)}%`);
export const fmtNum = (v) => (v == null ? "–" : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(v < 10 ? 1 : 0));
export const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const fmtAgo = (ms) => {
  if (ms == null) return "–";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

// ---------- icons (24px stroke set, original) ----------
const P = {
  play: "M7 5l12 7-12 7z", stop: "M6 6h12v12H6z", redo2: "M4 12a8 8 0 0 1 14-5.3M20 4v4h-4M20 12a8 8 0 0 1-14 5.3M4 20v-4h4",
  save: "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6", pause: "M8 5v14M16 5v14", undo: "M9 7L4 12l5 5M4 12h11a5 5 0 0 1 0 10h-3",
  redo: "M15 7l5 5-5 5M20 12H9a5 5 0 0 0 0 10h3", search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM21 21l-4.5-4.5",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z", grid: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18M15 3v18",
  note: "M5 3h10l4 4v14H5zM15 3v4h4M8 11h8M8 15h6", area: "M4 4h6M14 4h6v6M20 14v6h-6M10 20H4v-6M4 10V4",
  pulse: "M3 12h4l3-8 4 16 3-8h4", dash: "M4 13h6v7H4zM14 4h6v16h-6zM4 4h6v6H4z", sim: "M4 6h16M4 12h10M4 18h6M18 12l3 3-3 3",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16", upload: "M12 20V8M7 13l5-5 5 5M4 4h16", copy: "M8 8h12v12H8zM4 16V4h12",
  keyboard: "M2 6h20v12H2zM6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10", x: "M6 6l12 12M18 6L6 18",
  alignL: "M4 3v18M8 7h12v4H8zM8 14h8v4H8z", alignC: "M12 3v18M6 7h12v4H6zM8 14h8v4H8z", alignR: "M20 3v18M4 7h12v4H4zM8 14h8v4H8z",
  alignT: "M3 4h18M7 8h4v12H7zM14 8h4v8h-4z", alignM: "M3 12h18M7 5h4v14H7zM14 8h4v8h-4z", alignB: "M3 20h18M7 4h4v12H7zM14 8h4v8h-4z",
  distH: "M4 4v16M20 4v16M9 8h6v8H9z", distV: "M4 4h16M4 20h16M8 9h8v6H8z", flow: "M3 8h12l-3-3M21 16H9l3 3",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z", trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3", terminal: "M4 5h16v14H4zM7 9l3 3-3 3M12 15h5",
  window: "M3 4h18v16H3zM3 8h18", plus: "M12 5v14M5 12h14", help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.7v.5M12 17h.01",
  zap: "M12 3v6M12 15v6M5 12h4M15 12h4", wave: "M2 12c3-6 5-6 8 0s5 6 8 0 3-4 4-2", flag: "M5 21V4h11l-2 4 2 4H5",
  restore: "M3 12a9 9 0 1 0 3-6.7M3 4v5h5",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.1-2.7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 2.7-1.1V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z", diff: "M8 3v12M4 7h8M14 13h6M17 9v12M14 21h6", tag: "M3 12V3h9l9 9-9 9zM7.5 7.5h.01",
};
export function Icon({ name, size = 18, className = "", title }) {
  return (
    <svg className={`ico ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title && <title>{title}</title>}
      <path d={P[name] || P.help} fill={["play", "stop"].includes(name) ? "currentColor" : "none"} />
    </svg>
  );
}

export function TB({ icon, label, onClick, disabled, on, kbd, danger, primary, children }) {
  return (
    <button className={`tb ${on ? "on" : ""} ${danger ? "tb-danger" : ""} ${primary ? "tb-primary" : ""}`} onClick={onClick} disabled={disabled}
      title={kbd ? `${label} (${kbd})` : label} aria-label={label} aria-pressed={on === undefined ? undefined : !!on}>
      <Icon name={icon} />{children && <span className="tb-text">{children}</span>}
    </button>
  );
}

// ---------- toasts ----------
export function Toasts({ items, onClose }) {
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.kind || "info"}`}>
          <span className="toast-dot" />
          <div className="toast-body"><div>{t.msg}</div>{t.sub && <div className="toast-sub">{t.sub}</div>}</div>
          <button className="toast-x" onClick={() => onClose(t.id)} aria-label="Dismiss"><Icon name="x" size={14} /></button>
        </div>
      ))}
    </div>
  );
}

// ---------- modal ----------
export function Modal({ title, onClose, children, wide, xwide, footer }) {
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [onClose]);
  return createPortal(
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "modal-wide" : ""} ${xwide ? "modal-xwide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-head"><h2>{title}</h2><button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button></header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>, document.body);
}

// ---------- context menu ----------
export function Menu({ x, y, items, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ x, y });
  useEffect(() => {
    const r = ref.current?.getBoundingClientRect();
    if (r) setPos({ x: Math.min(x, window.innerWidth - r.width - 8), y: Math.min(y, window.innerHeight - r.height - 8) });
    const close = (e) => { if (!ref.current?.contains(e.target)) onClose(); };
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("mousedown", close); window.addEventListener("keydown", k);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", k); };
  }, [x, y, onClose]);
  return (
    <div className="menu" ref={ref} style={{ left: pos.x, top: pos.y }} role="menu">
      {items.filter(Boolean).map((it, i) => it === "-" ? <div key={i} className="menu-sep" /> : it.header ? <div key={i} className="menu-h">{it.header}</div> : (
        <button key={i} role="menuitem" className={`menu-i ${it.danger ? "danger" : ""}`} disabled={it.disabled}
          onClick={() => { onClose(); it.onClick(); }}>
          {it.icon && <Icon name={it.icon} size={15} />}<span>{it.label}</span>{it.kbd && <kbd>{it.kbd}</kbd>}
        </button>
      ))}
    </div>
  );
}

// ---------- charts ----------
export function Sparkline({ data, w = 120, h = 28, color = "var(--accent)", fill = true, max }) {
  const pts = (data || []).filter((v) => v != null);
  if (pts.length < 2) return <svg width={w} height={h} className="spark"><line x1="0" x2={w} y1={h - 1} y2={h - 1} className="spark-base" /></svg>;
  const m = max ?? Math.max(...pts, 1e-9);
  const xs = (i) => (i / (data.length - 1)) * w;
  const ys = (v) => h - 2 - (Math.max(0, v) / m) * (h - 4);
  let d = "", started = false;
  data.forEach((v, i) => { if (v == null) { started = false; return; } d += `${started ? "L" : "M"}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`; started = true; });
  const area = `${d}L${w},${h}L0,${h}Z`;
  return (
    <svg width={w} height={h} className="spark" aria-hidden="true">
      {fill && <path d={area} fill={color} opacity=".14" />}
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

const niceMax = (m) => {
  if (m <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(m))); const f = m / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
};

/** Time-series chart. series: [{label, color, values:[...]}] aligned with ts:[...]. */
export function TimeChart({ ts, series, height = 220, fmt = fmtBps, stacked = false }) {
  const ref = useRef(null);
  const [w, setW] = useState(600);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)));
    ro.observe(ref.current); return () => ro.disconnect();
  }, []);
  const padL = 64, padR = 12, padT = 10, padB = 26;
  const iw = w - padL - padR, ih = height - padT - padB;
  const { max, t0, t1 } = useMemo(() => {
    const all = series.flatMap((s) => s.values).filter((v) => v != null);
    return { max: niceMax(Math.max(0, ...all) * 1.1), t0: ts[0], t1: ts[ts.length - 1] };
  }, [ts, series]);
  if (!ts.length) return <div ref={ref} className="chart-empty" style={{ height }}>No samples yet. History builds up every 10 s while the lab runs.</div>;
  const X = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * iw;
  const Y = (v) => padT + ih - (v / max) * ih;
  const path = (vals) => { let d = "", on = false; vals.forEach((v, i) => { if (v == null) { on = false; return; } d += `${on ? "L" : "M"}${X(ts[i]).toFixed(1)},${Y(v).toFixed(1)}`; on = true; }); return d; };
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const tTicks = 5;
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect(); const x = e.clientX - r.left;
    if (x < padL || x > w - padR) return setHover(null);
    const t = t0 + ((x - padL) / iw) * (t1 - t0);
    let best = 0; ts.forEach((v, i) => { if (Math.abs(v - t) < Math.abs(ts[best] - t)) best = i; });
    setHover(best);
  };
  return (
    <div ref={ref} className="chart">
      <svg width={w} height={height} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label="time series chart">
        {ticks.map((v, i) => (
          <g key={i}><line x1={padL} x2={w - padR} y1={Y(v)} y2={Y(v)} className="grid-line" />
            <text x={padL - 6} y={Y(v) + 4} textAnchor="end" className="axis">{fmt(v)}</text></g>
        ))}
        {Array.from({ length: tTicks }, (_, i) => t0 + (i / (tTicks - 1)) * (t1 - t0)).map((t, i) => (
          <text key={i} x={X(t)} y={height - 8} textAnchor={i === 0 ? "start" : i === tTicks - 1 ? "end" : "middle"} className="axis">
            {new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</text>
        ))}
        {series.map((s, si) => {
          const d = path(s.values);
          // one closed fill per contiguous run, so gaps stay empty
          const fills = []; let run = [];
          s.values.forEach((v, i) => { if (v == null) { if (run.length) fills.push(run); run = []; } else run.push(i); });
          if (run.length) fills.push(run);
          return (<g key={si}>
            {fills.map((r, k) => <path key={k} d={`M${X(ts[r[0]])},${Y(0)}` + r.map((i) => `L${X(ts[i]).toFixed(1)},${Y(s.values[i]).toFixed(1)}`).join("") + `L${X(ts[r[r.length - 1]])},${Y(0)}Z`} fill={s.color} opacity=".10" />)}
            {!stacked && <path d={d} fill="none" stroke={s.color} strokeWidth="1.8" strokeLinejoin="round" />}
          </g>);
        })}
        {hover != null && (<g>
          <line x1={X(ts[hover])} x2={X(ts[hover])} y1={padT} y2={padT + ih} className="hover-line" />
          {series.map((s, si) => s.values[hover] != null && <circle key={si} cx={X(ts[hover])} cy={Y(s.values[hover])} r="3.5" fill={s.color} stroke="var(--panel)" strokeWidth="1.5" />)}
        </g>)}
      </svg>
      {hover != null && (
        <div className="chart-tip" style={{ left: Math.min(X(ts[hover]) + 12, w - 170) }}>
          <div className="mono">{fmtTime(ts[hover])}</div>
          {series.map((s, si) => <div key={si}><span className="sw" style={{ background: s.color }} />{s.label}: <b>{s.values[hover] == null ? "gap" : fmt(s.values[hover])}</b></div>)}
        </div>
      )}
      <div className="chart-legend">{series.map((s, si) => <span key={si}><span className="sw" style={{ background: s.color }} />{s.label}</span>)}</div>
    </div>
  );
}

export function Gauge({ value, label, sub, max = 100, color }) {
  const v = Math.max(0, Math.min(1, (value ?? 0) / max));
  const c = color || (v > 0.85 ? "var(--bad)" : v > 0.6 ? "var(--warn)" : "var(--ok)");
  const r = 26, C = 2 * Math.PI * r;
  return (
    <div className="gauge">
      <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
        <circle cx="32" cy="32" r={r} className="gauge-bg" />
        <circle cx="32" cy="32" r={r} fill="none" stroke={c} strokeWidth="6" strokeLinecap="round"
          strokeDasharray={`${v * C} ${C}`} transform="rotate(-90 32 32)" />
        <text x="32" y="36" textAnchor="middle" className="gauge-t">{value == null ? "–" : `${Math.round(value)}`}</text>
      </svg>
      <div><div className="gauge-l">{label}</div>{sub && <div className="hint">{sub}</div>}</div>
    </div>
  );
}

export function StateChip({ ok, warn, children, title }) {
  return <span className={`schip ${ok ? "s-ok" : warn ? "s-warn" : "s-bad"}`} title={title}>{children}</span>;
}

export function useInterval(fn, ms, deps = []) {
  const f = useRef(fn); f.current = fn;
  useEffect(() => {
    if (!ms) return;
    f.current();
    const t = setInterval(() => f.current(), ms);
    return () => clearInterval(t);
  }, [ms, ...deps]); // eslint-disable-line
}
