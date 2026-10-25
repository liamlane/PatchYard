import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./ui.jsx";
import { ICONS } from "./icons.jsx";

function score(q, s) {
  if (!q) return 1;
  q = q.toLowerCase(); s = s.toLowerCase();
  if (s.includes(q)) return 100 - s.indexOf(q);
  let i = 0, run = 0, sc = 0;
  for (const c of s) { if (c === q[i]) { i++; run++; sc += run; } else run = 0; if (i === q.length) return sc; }
  return 0;
}

export default function Palette({ commands, devices, onPickDevice, onClose, initial = "" }) {
  const [q, setQ] = useState(initial);
  const [i, setI] = useState(0);
  const ref = useRef(null);
  useEffect(() => ref.current?.focus(), []);
  const items = useMemo(() => {
    const d = devices.map((n) => ({ kind: "dev", key: `d-${n.id}`, label: n.id, sub: `${n.type}${n.state ? ` · ${n.state}` : ""}`, n, s: score(q, `${n.id} ${n.type}`) }));
    const c = commands.filter((x) => !x.disabled).map((x) => ({ kind: "cmd", key: `c-${x.label}`, ...x, s: score(q.replace(/^>\s*/, ""), `${x.label} ${x.group || ""}`) }));
    const all = q.startsWith(">") ? c : [...d, ...c];
    return all.filter((x) => x.s > 0).sort((a, b) => b.s - a.s || (a.kind === "dev" ? -1 : 1)).slice(0, 40);
  }, [q, commands, devices]);
  useEffect(() => setI(0), [q]);
  const run = (x) => { onClose(); x.kind === "dev" ? onPickDevice(x.n.id) : x.run(); };
  const key = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setI((v) => Math.min(items.length - 1, v + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setI((v) => Math.max(0, v - 1)); }
    else if (e.key === "Enter" && items[i]) run(items[i]);
    else if (e.key === "Escape") onClose();
  };
  return (
    <div className="modal-back pal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-label="Command palette">
        <div className="cmdk-in"><Icon name="search" /><input ref={ref} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={key}
          placeholder="Find a device, or type > for commands" aria-label="Search" /><kbd>Esc</kbd></div>
        <div className="cmdk-list" role="listbox">
          {items.map((x, k) => {
            const DI = x.kind === "dev" ? ICONS[x.n.type] : null;
            return (
              <button key={x.key} role="option" aria-selected={k === i} className={`cmdk-i ${k === i ? "on" : ""}`} onMouseEnter={() => setI(k)} onClick={() => run(x)}>
                {DI ? <DI size={22} /> : <Icon name={x.icon || "bolt"} size={16} />}
                <span className="cmdk-l">{x.label}</span><span className="hint">{x.kind === "dev" ? x.sub : x.group}</span>
                {x.kbd && <kbd>{x.kbd}</kbd>}
              </button>
            );
          })}
          {!items.length && <p className="empty">Nothing matches “{q}”.</p>}
        </div>
      </div>
    </div>
  );
}
