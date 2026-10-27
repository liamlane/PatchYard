import { useState } from "react";
import { api } from "./api.js";
import { catalogue, chassisOf, portsOf, usedPorts, fmtSpeed, ifLabel, mediaConflicts } from "./hw.js";

// Rear-panel view of a chassis device: model, power, on-board ports, slots with
// drag-in modules. Ports light up from live state; cables show who they go to.
export default function HardwarePanel({ lab, node, deployed, running, poweredOff, ifStates, settings, onUpdateNode, notify, onStatus }) {
  const cat = catalogue();
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(null);
  if (!cat || node.raw || !["router", "switch", "firewall", "pc", "server"].includes(node.type)) return null;
  const ch = chassisOf(node);
  const ports = portsOf(node) || [];
  const used = usedPorts(lab, node.id);
  const peer = Object.fromEntries(lab.links.flatMap((l) => [
    l.a === node.id && [l.a_if, `${l.b} ${ifLabel(lab.nodes.find((n) => n.id === l.b), l.b_if)}`],
    l.b === node.id && [l.b_if, `${l.a} ${ifLabel(lab.nodes.find((n) => n.id === l.a), l.a_if)}`]].filter(Boolean)));
  const needOff = settings?.general?.module_power_off !== false;
  const canSwap = !deployed || poweredOff || !needOff;
  const canModel = !running;
  const models = Object.entries(cat.chassis).filter(([, c]) => c.type === node.type);

  const setModel = (id) => {
    if (!id) return onUpdateNode(node.id, { chassis: null, modules: null });
    const c = cat.chassis[id];
    const next = { ...node, props: { ...node.props, chassis: id, modules: { ...(c.default_modules || {}) } } };
    const have = new Set((portsOf(next) || []).map((p) => p.if));
    const missing = [...used].filter((i) => !have.has(i));
    if (missing.length) return notify(`${c.label} has no port for ${missing.join(", ")}`, "Unplug those cables first, or pick a bigger model.", "bad");
    const bad = mediaConflicts(lab, next);
    if (bad.length) return notify("Cable type wouldn't match", bad.join("\n") + "\nRe-cable with the right cable, or fit a copper transceiver.", "bad");
    onUpdateNode(node.id, { chassis: id, modules: next.props.modules });
    notify(`${node.id} is now a ${c.label}`, used.size ? "Existing cables keep their ports (eth1 is the first on-board port)." : null, "ok");
  };
  const setModule = (slotId, mid) => {
    if (!canSwap) return notify("Power the device off first", "Like real hardware, modules are swapped with the power off (Settings → General).", "warn");
    const cur = node.props?.modules || {};
    if (!mid) {
      const lost = ports.filter((p) => p.slot === slotId && used.has(p.if));
      if (lost.length) return notify(`Unplug ${lost.map((p) => p.name).join(", ")} first`, null, "bad");
    }
    const mods = { ...cur, [slotId]: mid || undefined };
    if (!mid) delete mods[slotId];
    const next = { ...node, props: { ...node.props, modules: mods } };
    const have = new Set((portsOf(next) || []).map((p) => p.if));
    const lost = ports.filter((p) => used.has(p.if) && !have.has(p.if));
    if (lost.length) return notify(`Unplug ${lost.map((p) => p.name).join(", ")} first`, null, "bad");
    const bad = mediaConflicts(lab, next);
    if (bad.length) return notify("Cable type wouldn't match", bad.join("\n") + "\nRe-cable with the right cable first.", "bad");
    onUpdateNode(node.id, { modules: mods });
  };
  const setSfp = (ifn, kind) => {
    const sfp = { ...(node.props?.sfp || {}), [ifn]: kind };
    if (kind === "fibre") delete sfp[ifn];
    const bad = mediaConflicts(lab, { ...node, props: { ...node.props, sfp } });
    if (bad.length) return notify("Cable type wouldn't match", bad.join("\n") + "\nUnplug or re-cable first.", "bad");
    onUpdateNode(node.id, { sfp });
  };
  const power = async (on) => {
    if (!on && !confirm(`Power ${node.id} off?\n\nAnything not saved (write memory / Save running configs) is lost, like on a real device.`)) return;
    setBusy(true);
    try { const r = await api.power(lab.name, node.id, on); notify(r.ok ? `${node.id} powered ${on ? "on" : "off"}` : `Power ${on ? "on" : "off"} failed`, r.output, r.ok ? "ok" : "bad"); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(false); onStatus?.();
  };
  const led = (p) => {
    const st = (ifStates || {})[p.if];
    if (!used.has(p.if)) return "empty";
    if (!st) return "idle";
    return st === "up" ? "up" : "down";
  };
  const Port = ({ p }) => (
    <span className={`hp-port hp-${p.media} hp-${led(p)}`} title={`${p.long} · ${p.label}${peer[p.if] ? ` · to ${peer[p.if]}` : " · empty"}`}>
      <i />
      <small>{p.name.replace(/^[A-Za-z]+/, "")}</small>
    </span>
  );
  const onboard = ports.filter((p) => !p.slot);
  const mods = Object.entries(cat.modules).filter(([, m]) => ch?.slots.some((s) => s.family === m.family));
  return (
    <div className="hw">
      <div className="row hw-head">
        <label className="row">Model
          <select value={node.props?.chassis || ""} disabled={!canModel} onChange={(e) => setModel(e.target.value)}>
            <option value="">Generic (ports added as you cable)</option>
            {models.map(([id, c]) => <option key={id} value={id}>{c.label}</option>)}
          </select>
        </label>
        {deployed && (
          <button className={`power ${poweredOff ? "off" : "on"}`} disabled={busy} onClick={() => power(poweredOff)} title={poweredOff ? "Power on" : "Power off"}>
            <span className="pw-led" />{poweredOff ? "Powered off · switch on" : "Power off"}
          </button>
        )}
      </div>
      {!canModel && <p className="hint">Destroy the lab or power the device off to change the model.</p>}
      {ch && <p className="hint">{ch.desc}</p>}
      {ch && (
        <div className="hw-body">
          <div className="hp" aria-label={`${ch.label} rear panel`}>
            <div className="hp-row">
              <span className="hp-tag">{node.id}</span>
              <div className="hp-ports">{onboard.map((p) => <Port key={p.if} p={p} />)}</div>
            </div>
            <div className="hp-slots">
              {ch.slots.map((s) => {
                const mid = node.props?.modules?.[s.id];
                const m = cat.modules[mid];
                return (
                  <div key={s.id} className={`hp-slot ${m ? "full" : ""} ${over === s.id ? "drop" : ""}`}
                    onDragOver={(e) => { if (canSwap) { e.preventDefault(); setOver(s.id); } }} onDragLeave={() => setOver(null)}
                    onDrop={(e) => { e.preventDefault(); setOver(null); const id = e.dataTransfer.getData("application/netlab-module"); if (id && cat.modules[id]?.family === s.family) setModule(s.id, id); else if (id) notify(`${cat.modules[id].label} doesn't fit slot ${s.id}`, null, "bad"); }}>
                    <span className="hp-sid">slot {s.id}</span>
                    {m ? (<>
                      <span className="hp-mname">{m.label}</span>
                      <div className="hp-ports">{ports.filter((p) => p.slot === s.id).map((p) => <Port key={p.if} p={p} />)}</div>
                      <button className="link danger hp-x" disabled={!canSwap} onClick={() => setModule(s.id, null)} title="Remove module">remove</button>
                    </>) : (
                      <select className="hp-pick" disabled={!canSwap} value="" onChange={(e) => setModule(s.id, e.target.value)} aria-label={`Module for slot ${s.id}`}>
                        <option value="">empty: drop or pick a module…</option>
                        {mods.filter(([, mm]) => mm.family === s.family).map(([id, mm]) => <option key={id} value={id}>{mm.label}</option>)}
                      </select>
                    )}
                  </div>
                );
              })}
              {!ch.slots.length && <span className="hint">Fixed configuration, no module slots.</span>}
            </div>
          </div>
          {ch.slots.length > 0 && (
            <div className="hw-lib">
              <h4>Modules</h4>
              {mods.map(([id, m]) => (
                <div key={id} className={`hw-mod ${canSwap ? "" : "locked"}`} draggable={canSwap} onDragStart={(e) => e.dataTransfer.setData("application/netlab-module", id)}
                  title={canSwap ? "Drag into an empty slot" : "Power the device off to swap modules"}>
                  <b>{m.label}</b><span className="hint">{m.ports.map(([t, n]) => `${n}× ${cat.port_types[t].label}`).join(", ")}</span>
                </div>
              ))}
              {!canSwap && <p className="hint">Power the device off to swap modules (Settings → General).</p>}
            </div>
          )}
        </div>
      )}
      {ports.some((p) => p.sfp) && (
        <details className="sfp-box" open={Object.keys(node.props?.sfp || {}).length > 0}>
          <summary>Transceivers <span className="hint">SFP/SFP+ ports take fibre optics by default; fit a copper SFP (RJ45) to plug copper devices in.</span></summary>
          <div className="sfp-grid">{ports.filter((p) => p.sfp).map((p) => (
            <label key={p.if} className="row"><span className="mono">{p.name}</span>
              <select value={p.sfp} onChange={(e) => setSfp(p.if, e.target.value)}>{Object.entries(cat.transceivers || { fibre: "fibre", copper: "copper" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              {peer[p.if] && <span className="hint">to {peer[p.if]}</span>}</label>))}</div>
        </details>
      )}
      {ch && (
        <p className="hint hw-legend">
          <span className="hp-port hp-copper hp-up"><i /></span> up <span className="hp-port hp-copper hp-down"><i /></span> down
          <span className="hp-port hp-copper hp-empty"><i /></span> empty · copper <span className="hp-port hp-fibre hp-empty"><i /></span> SFP
          <span className="hp-port hp-serial hp-empty"><i /></span> serial · speeds: {[...new Set(ports.map((p) => `${p.name.replace(/[\d/]+$/, "")} ${fmtSpeed(p.kbit)}`))].join(", ")}
        </p>
      )}
    </div>
  );
}

// Port chooser shown when a cable is plugged into a chassis device (like Packet Tracer).
export function PortMenu({ lab, node, x, y, media, otherMedia, onPick, onClose }) {
  const ports = portsOf(node) || [];
  const used = usedPorts(lab, node.id);
  const peer = Object.fromEntries(lab.links.flatMap((l) => [l.a === node.id && [l.a_if, l.b], l.b === node.id && [l.b_if, l.a]].filter(Boolean)));
  const want = otherMedia || media;
  const left = Math.min(x + 8, window.innerWidth - 300), top = Math.min(y + 8, window.innerHeight - 380);
  const groups = [...new Set(ports.map((p) => p.slot || "on board"))];
  return (
    <div className="portmenu" style={{ left, top }} role="menu" aria-label={`Ports on ${node.id}`} onMouseDown={(e) => e.stopPropagation()}>
      <header><b>{node.id}</b><span className="hint">{media} cable · pick a port</span><button className="icon-btn" onClick={onClose} aria-label="Close">×</button></header>
      <div className="pm-list">
        {groups.map((g) => (
          <div key={g}>
            <div className="pm-group">{g === "on board" ? "On board" : `Slot ${g}`}</div>
            {ports.filter((p) => (p.slot || "on board") === g).map((p) => {
              const busy = used.has(p.if), bad = p.media !== want;
              return (
                <button key={p.if} role="menuitem" className={`pm-item ${busy || bad ? "off" : ""}`} disabled={busy || bad} onClick={() => onPick(p.if)}
                  title={bad ? `Needs a ${p.media} cable` : busy ? `In use (to ${peer[p.if]})` : p.label}>
                  <span className={`hp-port hp-${p.media} hp-${busy ? "up" : "empty"}`}><i /></span>
                  <span className="mono">{p.long}</span><span className="hint">{busy ? `to ${peer[p.if]}` : bad ? p.media : p.label}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
