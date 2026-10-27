import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { Modal, Icon } from "./ui.jsx";

const MINI = {
  "ospf-triangle": { n: [[60, 18, "r"], [22, 70, "r"], [98, 70, "r"], [60, -14, "p"], [134, 70, "s"]], l: [[0, 1], [0, 2], [1, 2], [0, 3], [2, 4]] },
  "ospf-multiarea": { n: [[78, -12, "r"], [40, 22, "r"], [116, 22, "r"], [40, 56, "r"], [116, 56, "r"], [40, 90, "p"], [116, 90, "s"]], l: [[0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6]] },
  "bgp-multi-as": { n: [[48, 10, "r"], [48, 64, "r"], [108, 10, "r"], [108, 64, "r"], [12, 10, "p"], [144, 64, "s"]], l: [[0, 1], [0, 2], [1, 3], [2, 3], [0, 4], [3, 5]] },
  "serial-wan": { n: [[78, 20, "r"], [30, 64, "r"], [126, 64, "r"], [78, -14, "s"], [30, 96, "p"], [126, 96, "p"]], l: [[0, 1], [0, 2], [0, 3], [1, 4], [2, 5]] },
  "router-on-a-stick": { n: [[70, 0, "r"], [70, 42, "w"], [36, 84, "p"], [104, 84, "p"]], l: [[0, 1], [1, 2], [1, 3]] },
  "campus-stp": { n: [[78, -8, "r"], [78, 26, "w"], [40, 58, "w"], [116, 58, "w"], [16, 92, "p"], [56, 92, "p"], [100, 92, "p"], [140, 92, "s"]], l: [[0, 1], [1, 2], [1, 3], [2, 3], [2, 4], [2, 5], [3, 6], [3, 7]] },
  "campus-rstp-lacp": { n: [[78, -14, "r"], [46, 16, "w"], [110, 16, "w"], [46, 54, "w"], [110, 54, "w"], [30, 90, "p"], [78, 90, "p"], [130, 90, "s"]], l: [[0, 1], [1, 2], [1, 3], [1, 4], [2, 3], [2, 4], [3, 5], [4, 6], [4, 7]] },
  "small-office": { n: [[60, -14, "r"], [112, -14, "s"], [60, 18, "f"], [60, 52, "w"], [112, 52, "s"], [20, 88, "p"], [60, 88, "p"], [100, 88, "p"]], l: [[0, 1], [0, 2], [2, 3], [3, 4], [3, 5], [3, 6], [3, 7]] },
  "firewall-edge": { n: [[78, -10, "r"], [130, -10, "s"], [78, 30, "f"], [130, 30, "s"], [78, 62, "w"], [50, 92, "p"], [106, 92, "p"]], l: [[0, 1], [0, 2], [2, 3], [2, 4], [4, 5], [4, 6]] },
  "isp-mpls": { n: [[78, -10, "r"], [40, 22, "r"], [62, 22, "r"], [94, 22, "r"], [116, 22, "r"], [24, 56, "r"], [50, 56, "r"], [70, 56, "r"], [86, 56, "r"], [106, 56, "r"], [132, 56, "r"], [24, 88, "p"], [50, 88, "p"], [70, 88, "p"], [86, 88, "p"], [106, 88, "p"], [132, 88, "p"]],
    l: [[0, 1], [0, 2], [0, 3], [0, 4], [1, 2], [3, 4], [1, 5], [1, 6], [1, 7], [2, 5], [2, 6], [2, 7], [3, 8], [3, 9], [3, 10], [4, 8], [4, 9], [4, 10], [5, 11], [6, 12], [7, 13], [8, 14], [9, 15], [10, 16]] },
};
function MiniTopo({ id }) {
  const t = MINI[id];
  if (!t) return <div className="mini" />;
  const C = { r: "var(--accent)", w: "var(--ok)", p: "var(--muted)", s: "var(--fibre)", f: "var(--bad)" };
  return (
    <svg className="mini" viewBox="0 -24 156 128" aria-hidden="true">
      {t.l.map(([a, b], i) => <line key={i} x1={t.n[a][0]} y1={t.n[a][1]} x2={t.n[b][0]} y2={t.n[b][1]} className="mini-l" />)}
      {t.n.map(([x, y, k], i) => k === "w" || k === "f" ? <rect key={i} x={x - 6} y={y - 4} width="12" height="8" rx="1.5" fill={C[k]} /> : <circle key={i} cx={x} cy={y} r={k === "r" ? 5.5 : 4} fill={C[k]} />)}
    </svg>
  );
}

function Templates({ notify, onOpenLab, close }) {
  const [list, setList] = useState([]);
  const [names, setNames] = useState({});
  const [busy, setBusy] = useState("");
  useEffect(() => { api.templates().then(setList).catch((e) => notify(e.message, null, "bad")); }, [notify]);
  const create = async (t) => {
    const name = (names[t.id] || t.id).trim();
    setBusy(t.id);
    try { await api.fromTemplate(t.id, name); notify(`Created ${name} from ${t.title}`, "Deploy it from the toolbar.", "ok"); close(); onOpenLab(name); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy("");
  };
  return (
    <div className="tpl-grid">
      {list.map((t) => (
        <div key={t.id} className="tpl">
          <MiniTopo id={t.id} />
          <h3>{t.title} <span className="hint">{t.devices} devices</span></h3>
          <p>{t.desc}</p>
          <div className="row">
            <input placeholder={t.id} value={names[t.id] ?? ""} onChange={(e) => setNames({ ...names, [t.id]: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} aria-label="New lab name" />
            <button className="primary" disabled={busy === t.id} onClick={() => create(t)}>{busy === t.id ? "Creating…" : "Create lab"}</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function DiffView({ diff }) {
  if (!diff) return null;
  if (!diff.changes.length) return <p className="empty">No differences: {diff.to} matches “{diff.from.label}”.</p>;
  return (
    <div className="diffs">
      {diff.changes.map((c) => (
        <details key={c.path} open={diff.changes.length <= 4}>
          <summary><span className={`dstat d-${c.status}`}>{c.status}</span><span className="mono">{c.path}</span></summary>
          <pre className="diff">{c.diff.split("\n").map((l, i) => (
            <div key={i} className={l.startsWith("+++") || l.startsWith("---") ? "d-file" : l.startsWith("@@") ? "d-hunk" : l.startsWith("+") ? "d-add" : l.startsWith("-") ? "d-del" : ""}>{l || " "}</div>))}</pre>
        </details>
      ))}
    </div>
  );
}

function Snapshots({ lab, deployed, notify, onReload, onRedeploy }) {
  const [list, setList] = useState([]);
  const [label, setLabel] = useState("");
  const [diff, setDiff] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.snapshots(lab.name).then(setList).catch((e) => notify(e.message, null, "bad"));
  useEffect(() => { load(); }, [lab.name]); // eslint-disable-line
  const take = async () => {
    setBusy(true);
    try { const r = await api.snapshot(lab.name, label); notify(`Snapshot “${r.snapshot.label}” taken`, `${r.snapshot.files} files, ${r.saved_configs.saved.length} running configs saved first`, "ok"); setLabel(""); load(); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  const restore = async (s) => {
    if (!confirm(`Restore “${s.label}”? The current state is snapshotted first, so you can undo this.`)) return;
    setBusy(true);
    try {
      const r = await api.snapRestore(lab.name, s.id);
      notify(`Restored “${s.label}”`, `${r.files} files. Safety snapshot: ${r.safety_snapshot.label}`, "ok");
      await onReload(); load(); setDiff(null);
      if (r.running && confirm("The lab is running. Redeploy now so routers load the restored configs?")) onRedeploy();
    } catch (e) { notify(e.message, null, "bad"); }
    setBusy(false);
  };
  return (
    <div>
      <div className="row">
        <input placeholder="Label, e.g. OSPF working" value={label} onChange={(e) => setLabel(e.target.value)} onKeyDown={(e) => e.key === "Enter" && take()} style={{ flex: 1 }} />
        <button className="primary" disabled={busy} onClick={take}><Icon name="camera" size={15} /> Take snapshot</button>
      </div>
      <p className="hint">Captures the topology plus every router config{deployed ? " (running configs are saved first)" : ""}, switch script and server file.</p>
      <table className="grid snaps"><thead><tr><th>When</th><th>Label</th><th>Contents</th><th /></tr></thead>
        <tbody>{list.map((s) => (
          <tr key={s.id}>
            <td className="mono">{new Date(s.ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" })}</td>
            <td>{s.auto && <span className="schip s-warn">auto</span>} {s.label}</td>
            <td className="hint">{s.nodes} devices · {s.links} links · {s.files} files</td>
            <td><div className="row nowrap">
              <button className="link" onClick={() => api.snapDiff(lab.name, s.id).then(setDiff).catch((e) => notify(e.message, null, "bad"))}><Icon name="diff" size={14} /> Diff vs now</button>
              <button className="link" disabled={busy} onClick={() => restore(s)}><Icon name="restore" size={14} /> Restore</button>
              <button className="link danger" onClick={() => confirm(`Delete snapshot “${s.label}”?`) && api.snapDelete(lab.name, s.id).then(load)} aria-label="Delete snapshot"><Icon name="trash" size={14} /></button>
            </div></td>
          </tr>))}
        </tbody></table>
      {!list.length && <p className="empty">No snapshots yet. Take one before you start changing configs.</p>}
      {diff && <><h3 className="diff-h">Changes since “{diff.from.label}” <button className="link" onClick={() => setDiff(null)}>hide</button></h3><DiffView diff={diff} /></>}
    </div>
  );
}

function Share({ lab, notify, onOpenLab, close, refreshLabs }) {
  const [cname, setCname] = useState(lab ? `${lab.name}-copy`.slice(0, 31) : "");
  const [zname, setZname] = useState("");
  const [path, setPath] = useState("");
  const file = useRef(null);
  const [busy, setBusy] = useState(false);
  const go = async (fn) => { setBusy(true); try { await fn(); } catch (e) { notify(e.message, null, "bad"); } setBusy(false); };
  return (
    <div className="share">
      {lab && (<>
        <section><h3><Icon name="copy" size={16} /> Clone “{lab.name}”</h3>
          <div className="row"><input value={cname} onChange={(e) => setCname(e.target.value.toLowerCase())} aria-label="Clone name" />
            <button disabled={busy} onClick={() => go(async () => { const r = await api.clone(lab.name, cname); notify(`Cloned to ${cname}`, r.warning || null, r.warning ? "warn" : "ok"); await refreshLabs(); close(); onOpenLab(cname); })}>Clone</button></div>
          <p className="hint">Copies the topology and every saved config into a new, independent lab.</p></section>
        <section><h3><Icon name="download" size={16} /> Export</h3>
          <a className="btn" href={api.exportUrl(lab.name)} download>Download netlab-{lab.name}.zip</a>
          <p className="hint">lab.json plus configs. Running router configs are saved first.</p></section>
      </>)}
      <section><h3><Icon name="upload" size={16} /> Import a netlab zip</h3>
        <div className="row"><input type="file" accept=".zip,application/zip" ref={file} onChange={(e) => !zname && setZname((e.target.files[0]?.name || "").replace(/^netlab-|\.zip$/g, "").toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 31))} />
          <input placeholder="new lab name" value={zname} onChange={(e) => setZname(e.target.value.toLowerCase())} aria-label="Imported lab name" />
          <button disabled={busy || !zname} onClick={() => go(async () => { const f = file.current.files[0]; if (!f) throw new Error("choose a zip first"); await api.importZip(zname, f); notify(`Imported ${zname}`, null, "ok"); await refreshLabs(); close(); onOpenLab(zname); })}>Import</button></div></section>
      <section><h3><Icon name="layers" size={16} /> Import a containerlab topology</h3>
        <div className="row"><input placeholder="/home/fsec/gui-isp-lab-p2/isp-lab.clab.yml" value={path} onChange={(e) => setPath(e.target.value)} style={{ flex: 1 }} aria-label="Path on the server" />
          <button disabled={busy || !path} onClick={() => go(async () => { const l = await api.importClab(path); notify(`Imported ${l.name}`, null, "ok"); await refreshLabs(); close(); onOpenLab(l.name); })}>Import</button></div>
        <p className="hint">A .clab.yml already on the netlab VM. Devices keep their own definitions and config files.</p></section>
    </div>
  );
}

function Labs({ lab, notify, onOpenLab, refreshLabs, close }) {
  const [list, setList] = useState(null);
  const [busy, setBusy] = useState("");
  const load = async () => {
    try {
      const ls = await api.labs();
      const st = await Promise.all(ls.map((l) => api.status(l.name).then((s) => Object.keys(s.nodes || {}).length).catch(() => 0)));
      setList(ls.map((l, i) => ({ ...l, running: st[i] })));
    } catch (e) { notify(e.message, null, "bad"); }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line
  const del = async (l) => {
    if (!confirm(`${l.running ? "Destroy and delete" : "Delete"} lab ${l.name}?\n\nThis removes its topology, saved configs and snapshots. There is no undo; export it first if you might want it back.`)) return;
    setBusy(l.name);
    try {
      if (l.running) { const r = await api.destroy(l.name); if (!r.ok) throw new Error("destroy failed: " + (r.output || "").slice(-300)); }
      await api.remove(l.name);
      notify(`Deleted ${l.name}`, null, "ok");
      if (lab?.name === l.name) onOpenLab("");
      refreshLabs(); load();
    } catch (e) { notify(e.message, null, "bad"); }
    setBusy("");
  };
  if (!list) return <p className="hint">Loading labs…</p>;
  return (
    <table className="grid labs-tbl">
      <thead><tr><th>Lab</th><th>Devices</th><th>Cables</th><th>State</th><th /></tr></thead>
      <tbody>{list.map((l) => (
        <tr key={l.name} className={lab?.name === l.name ? "cur" : ""}>
          <td><button className="link" onClick={() => { close(); onOpenLab(l.name); }}>{l.name}</button>{lab?.name === l.name && <span className="hint"> (open)</span>}</td>
          <td>{l.nodes}</td><td>{l.links}</td>
          <td>{l.running ? <span className="schip s-ok">{l.running} running</span> : <span className="hint">not deployed</span>}</td>
          <td><button className="link danger" disabled={busy === l.name} onClick={() => del(l)}>{busy === l.name ? "Working…" : l.running ? "Destroy & delete" : "Delete"}</button></td>
        </tr>))}
        {!list.length && <tr><td colSpan={5} className="empty">No labs yet.</td></tr>}
      </tbody>
    </table>
  );
}

export default function LabManager({ lab, deployed, initial = "templates", notify, onOpenLab, onClose, refreshLabs, onReload, onRedeploy }) {
  const [tab, setTab] = useState(lab ? initial : "templates");
  const tabs = [["labs", "Labs", "grid"], ["templates", "Templates", "layers"], ...(lab ? [["snapshots", "Snapshots", "camera"]] : []), ["share", "Clone · export · import", "copy"]];
  return (
    <Modal title="Lab manager" onClose={onClose} wide>
      <div className="seg mgr-tabs">{tabs.map(([k, l, i]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}><Icon name={i} size={15} /> {l}</button>)}</div>
      {tab === "labs" && <Labs lab={lab} notify={notify} onOpenLab={onOpenLab} refreshLabs={refreshLabs} close={onClose} />}
      {tab === "templates" && <Templates notify={notify} onOpenLab={onOpenLab} close={onClose} />}
      {tab === "snapshots" && lab && <Snapshots lab={lab} deployed={deployed} notify={notify} onReload={onReload} onRedeploy={onRedeploy} />}
      {tab === "share" && <Share lab={lab} notify={notify} onOpenLab={onOpenLab} close={onClose} refreshLabs={refreshLabs} />}
    </Modal>
  );
}
export { MiniTopo };
