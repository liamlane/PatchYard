import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, applyEdgeChanges, useReactFlow } from "@xyflow/react";
import { api } from "./api.js";
import { DeviceNode, CableEdge, NoteNode, AreaNode } from "./canvas.jsx";
import { ICONS, TYPE_LABEL, PREFIX, CableIcon } from "./icons.jsx";
import DeviceWindow from "./DeviceWindow.jsx";
import SimPanel from "./SimPanel.jsx";
import Dashboard from "./Dashboard.jsx";
import LinkPanel from "./LinkPanel.jsx";
import LabManager, { MiniTopo } from "./LabManager.jsx";
import Palette from "./Palette.jsx";
import { NotificationsPanel, TrafficPanel, Login, InvitePage, ResetPage } from "./Notifications.jsx";
// Modals load on first use: smaller first page load
const Settings = lazy(() => import("./Settings.jsx"));
const Admin = lazy(() => import("./Admin.jsx"));
const Guide = lazy(() => import("./Guide.jsx"));
import { ChallengePanel } from "./Challenge.jsx";
import { Require2FA } from "./TwoFactor.jsx";
import { createPortal } from "react-dom";
import { setMe } from "./me.js";
import { PortMenu } from "./Hardware.jsx";
import { setCatalogue, portsOf, defaultHardware, labIf } from "./hw.js";
import { Icon, TB, Toasts, Modal, Menu, fmtBps } from "./ui.jsx";

const nodeTypes = { device: DeviceNode, note: NoteNode, area: AreaNode };
const edgeTypes = { cable: CableEdge };
const stripMpls = (p) => p.replace(/^MPLS\//, "");
const DEV_W = 76, DEV_H = 84;
const isTyping = (e) => {
  const t = e.target;
  return t.closest?.(".xterm") || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable;
};
const store = { get: (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };

export default function App() {
  return <ReactFlowProvider><Workspace /></ReactFlowProvider>;
}

function Workspace() {
  const rf = useReactFlow();
  const wrap = useRef(null);
  const [labs, setLabs] = useState([]);
  const [lab, setLab] = useState(null);
  const labRef = useRef(null); labRef.current = lab;
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState({ nodes: {}, ifaces: {}, capturing: false });
  const [tool, setTool] = useState("select");
  const [pick, setPick] = useState(null);
  const [pickIf, setPickIf] = useState(null);
  const [portMenu, setPortMenu] = useState(null);
  const [hwv, setHwv] = useState(0);
  const revRef = useRef(0);
  const [sel, setSel] = useState(new Set());
  const [selEdge, setSelEdge] = useState(null);
  const [wins, setWins] = useState([]);
  const [zTop, setZTop] = useState(10);
  const [busy, setBusy] = useState(null);
  const [log, setLog] = useState([]);
  const [toasts, setToasts] = useState([]);
  const [showLog, setShowLog] = useState(false);
  const [showIf, setShowIf] = useState(() => store.get("netlab.ports", true));
  const [theme, setTheme] = useState(() => store.get("netlab.theme", window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  const [snap, setSnap] = useState(() => store.get("netlab.snap", false));
  const [overlay, setOverlay] = useState(() => store.get("netlab.overlay", true));
  const [side, setSide] = useState(null);           // null | "dash" | "alerts" | "traffic"
  const [appSettings, setAppSettings] = useState(null);
  const [auth, setAuth] = useState({ checked: false, need: false, user: null });
  const [admin, setAdmin] = useState(null);          // null | admin tab to open
  const [acct, setAcct] = useState(false);
  const acctBtn = useRef(null);
  const closeAcct = useCallback(() => setAcct(false), []);
  const lastReq = useRef(-1);
  const [alertSum, setAlertSum] = useState(null);
  const lastEid = useRef(-1);
  const [settingsTab, setSettingsTab] = useState(null);
  const [trafficPreset, setTrafficPreset] = useState(null);
  const liveBase = useRef(null);
  const liveQueue = useRef(Promise.resolve());
  const [live, setLive] = useState(null);
  const [linkHist, setLinkHist] = useState({});
  const [presets, setPresets] = useState({});
  const [menu, setMenu] = useState(null);
  const [palette, setPalette] = useState(null);
  const [mgr, setMgr] = useState(null);
  const [help, setHelp] = useState(null);            // null | guide section id
  const [hit, setHit] = useState(null);
  const [tpls, setTpls] = useState([]);
  // simulation
  const [sim, setSim] = useState(false);
  const [events, setEvents] = useState([]);
  const lastId = useRef(0);
  const [cursor, setCursor] = useState(0);
  const [filter, setFilter] = useState(new Set());
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [follow, setFollow] = useState(true);
  const [capturing, setCapturing] = useState(false);
  const [anim, setAnim] = useState(null);
  const [dims, setDims] = useState({});
  // undo / redo
  const past = useRef([]), future = useRef([]);
  const [, setHv] = useState(0);
  const dragRec = useRef(false);

  useEffect(() => { document.documentElement.dataset.theme = theme; store.set("netlab.theme", theme); }, [theme]);
  useEffect(() => store.set("netlab.snap", snap), [snap]);
  useEffect(() => store.set("netlab.overlay", overlay), [overlay]);
  useEffect(() => store.set("netlab.ports", showIf), [showIf]);

  const notify = useCallback((msg, detail, kind = "info") => {
    const t = { t: new Date(), msg, detail, kind };
    setLog((l) => [...l, t].slice(-200));
    const id = Math.random().toString(36).slice(2);
    const sub = typeof detail === "string" && detail.length < 160 && !detail.includes("\n") ? detail : null;
    setToasts((x) => [...x.slice(-3), { id, msg, sub, kind }]);
    setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), kind === "bad" ? 9000 : 4500);
  }, []);
  const refreshLabs = useCallback(() => api.labs().then(setLabs).catch((e) => notify(e.message, null, "bad")), [notify]);
  useEffect(() => {
    api.authState().then((a) => { setMe(a.user); setAuth({ checked: true, need: a.enabled && !a.logged_in, user: a.user, multi: a.multi_user, requests: a.requests, forgot: a.forgot }); })
      .catch(() => setAuth({ checked: true, need: false, user: null }));
    const h = () => setAuth((x) => ({ ...x, checked: true, need: true }));
    const h2 = () => api.authState().then((a) => { setMe(a.user); setAuth((x) => ({ ...x, user: a.user })); }).catch(() => {});
    window.addEventListener("netlab-auth", h);
    window.addEventListener("netlab-2fa", h2);
    return () => { window.removeEventListener("netlab-auth", h); window.removeEventListener("netlab-2fa", h2); };
  }, []);
  useEffect(() => {
    if (!auth.checked || auth.need) return;
    refreshLabs(); api.presets().then(setPresets).catch(() => {}); api.templates().then(setTpls).catch(() => {});
    api.settings().then(setAppSettings).catch(() => {});
    api.hardware().then((c) => { setCatalogue(c); setHwv((v) => v + 1); }).catch(() => {});
  }, [refreshLabs, auth.checked, auth.need]);
  const beep = () => {
    try { const a = new AudioContext(), o = a.createOscillator(), g = a.createGain(); o.frequency.value = 880; g.gain.value = 0.08; o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime + 0.25); } catch { /* no audio */ }
  };
  useEffect(() => {
    if (!auth.checked || auth.need) return;
    let alive = true;
    const load = () => api.alertSummary(Math.max(0, lastEid.current)).then((r) => {
      if (!alive) return;
      setAlertSum(r);
      if (lastEid.current >= 0) {
        const n = appSettings?.notifications || { toast_min_severity: "warning", toast_resolved: true };
        const rank = { info: 0, warning: 1, critical: 2, none: 9 };
        for (const e of r.events) {
          if (rank[e.severity] < rank[n.toast_min_severity]) continue;
          if (e.kind === "resolved" && !n.toast_resolved) continue;
          notify(`${e.kind === "fired" ? "⚠" : "✓"} ${e.rule}${e.kind === "resolved" ? " resolved" : ""}`, `${e.lab}: ${e.message}`,
            e.kind === "resolved" ? "ok" : e.severity === "critical" ? "bad" : "warn");
          if (e.kind === "fired" && e.severity === "critical" && n.sound) beep();
        }
      }
      lastEid.current = Math.max(lastEid.current, r.last_event, ...r.events.map((e) => e.eid));
      if (r.requests != null) {
        if (r.requests > lastReq.current && lastReq.current >= 0) notify(`New account request${r.requests - lastReq.current > 1 ? "s" : ""}`, "Review it in Admin → Requests", "warn");
        lastReq.current = r.requests;
      }
    }).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [auth.checked, auth.need, appSettings, notify]); // eslint-disable-line

  const openLab = async (name) => {
    setWins([]); setSel(new Set()); setSelEdge(null); setEvents([]); lastId.current = 0; setSim(false); setLive(null); setLinkHist({});
    past.current = []; future.current = []; setHv((v) => v + 1); liveBase.current = null; revRef.current = 0; setPick(null); setPickIf(null); setPortMenu(null);
    if (!name) { setLab(null); store.set("netlab.last", null); return; }
    try { setLab(await api.get(name)); store.set("netlab.last", name); setTimeout(() => rf.fitView({ padding: 0.15, duration: 400 }), 120); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  useEffect(() => { const l = store.get("netlab.last", null); if (l) openLab(l); }, []); // eslint-disable-line

  // ---------- history-aware edits ----------
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const pushHistory = () => { if (!labRef.current) return; past.current.push(clone(labRef.current)); if (past.current.length > 100) past.current.shift(); future.current = []; setHv((v) => v + 1); };
  const change = (fn) => { pushHistory(); setLab((l) => fn(l)); setDirty(true); };
  const quiet = (fn) => { setLab((l) => fn(l)); setDirty(true); };
  const keepLive = (target) => {
    // live state (props pushed to containers, link impairments) is not part of undo
    const cur = labRef.current;
    const n = Object.fromEntries(cur.nodes.map((x) => [x.id, x])), k = Object.fromEntries(cur.links.map((x) => [x.id, x]));
    return { ...target, nodes: target.nodes.map((x) => (n[x.id] ? { ...x, props: n[x.id].props } : x)),
      links: target.links.map((x) => (k[x.id] ? { ...x, impair: k[x.id].impair, admin: k[x.id].admin } : x)) };
  };
  const undo = () => { if (!past.current.length) return; future.current.push(clone(labRef.current)); setLab(keepLive(past.current.pop())); setDirty(true); setHv((v) => v + 1); };
  const redo = () => { if (!future.current.length) return; past.current.push(clone(labRef.current)); setLab(keepLive(future.current.pop())); setDirty(true); setHv((v) => v + 1); };

  // autosave
  useEffect(() => {
    if (!dirty || !lab) return;
    const t = setTimeout(() => api.save(lab, revRef.current).then(() => setDirty(false)).catch((e) => notify(`Save failed: ${e.message}`, null, "bad")), 900);
    return () => clearTimeout(t);
  }, [lab, dirty, notify]);

  // status + live metrics polling
  useEffect(() => {
    if (!lab) return;
    let alive = true;
    const load = () => api.status(lab.name).then((s) => { if (alive) { setStatus(s); setCapturing(s.capturing); pullServerProps(s); } }).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [lab?.name]);
  // device props changed on the server (IOS CLI, power) -> merge them into the open lab
  const pullServerProps = (s) => {
    if (!s || s.rev == null || s.rev <= revRef.current) return;
    const newer = Object.entries(s.rev_nodes || {}).filter(([, r]) => r > revRef.current).map(([n]) => n);
    revRef.current = s.rev;
    const name = labRef.current?.name;
    if (!newer.length || !name) return;
    api.get(name).then((fresh) => setLab((l) => (l && l.name === fresh.name ? {
      ...l, nodes: l.nodes.map((n) => (newer.includes(n.id) ? { ...n, props: (fresh.nodes.find((f) => f.id === n.id) || n).props } : n)) } : l))).catch(() => {});
  };
  const states = status.nodes || {};
  const powerOff = new Set(status.power_off || []);
  const deployed = Object.keys(states).length > 0;
  const paused = Object.values(states).some((s) => s === "paused");
  const liveEdit = appSettings?.general?.live_edit !== false;

  // ---------- live editing: diff the topology against what's running, apply the difference ----------
  useEffect(() => {
    if (!deployed) liveBase.current = null;
    else if (!liveBase.current && labRef.current) liveBase.current = JSON.parse(JSON.stringify(labRef.current));
  }, [deployed, lab?.name]);
  const syncLive = async () => {
    const base = liveBase.current, cur = labRef.current;
    if (!base || !cur || base.name !== cur.name) return;
    const key = (l) => `${l.a}:${l.a_if}|${l.b}:${l.b_if}`;
    const bn = new Set(base.nodes.map((n) => n.id)), cn = new Set(cur.nodes.map((n) => n.id));
    const bl = new Map(base.links.map((l) => [l.id, l])), cl = new Map(cur.links.map((l) => [l.id, l]));
    const addN = cur.nodes.filter((n) => !bn.has(n.id) && !n.raw), delN = base.nodes.filter((n) => !cn.has(n.id));
    const addL = cur.links.filter((l) => !bl.has(l.id) || key(bl.get(l.id)) !== key(l));
    const delL = base.links.filter((l) => (!cl.has(l.id) || key(cl.get(l.id)) !== key(l)) && cn.has(l.a) && cn.has(l.b));
    if (!addN.length && !delN.length && !addL.length && !delL.length) return;
    liveBase.current = JSON.parse(JSON.stringify(cur));
    await api.save(cur, revRef.current); setDirty(false);
    const out = [], bad = [];
    const go = async (fn, label) => { try { const r = await fn(); (r.ok === false ? bad : out).push(r.output || label); } catch (e) { bad.push(`${label}: ${e.message}`); } };
    for (const l of delL) await go(() => api.liveUnlink(cur.name, l), `unplug ${l.a}–${l.b}`);
    for (const n of delN) await go(() => api.liveNodeDel(cur.name, n.id), `remove ${n.id}`);
    for (const n of addN) await go(() => api.liveNode(cur.name, n.id), `start ${n.id}`);
    for (const l of addL) await go(() => api.liveLink(cur.name, l.id), `wire ${l.a}–${l.b}`);
    if (bad.length) notify("Live change partly failed", [...bad, ...out].join("\n"), "bad");
    else notify(`Live: ${[addN.length && `${addN.length} device(s) started`, delN.length && `${delN.length} removed`, addL.length && `${addL.length} cable(s) wired`, delL.length && `${delL.length} unplugged`].filter(Boolean).join(", ")}`, out.join("\n"), "ok");
    api.status(cur.name).then(setStatus).catch(() => {});
  };
  useEffect(() => {
    if (!lab || !deployed || !liveEdit || !liveBase.current) return;
    const t = setTimeout(() => { liveQueue.current = liveQueue.current.then(syncLive).catch(() => {}); }, 450);
    return () => clearTimeout(t);
  }, [lab, deployed, liveEdit]); // eslint-disable-line
  useEffect(() => {
    if (!lab || !deployed) return;
    let alive = true, lastTs = null;
    const load = () => api.live(lab.name).then((d) => {
      if (!alive) return;
      setLive(d);
      if (d.ts && d.ts !== lastTs) {
        lastTs = d.ts;
        setLinkHist((h) => { const n = { ...h }; for (const [id, r] of Object.entries(d.links || {})) n[id] = [...(n[id] || []), r.total_bps].slice(-60); return n; });
      }
    }).catch(() => {});
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [lab?.name, deployed]);

  // ---------- simulation ----------
  const visible = useMemo(() => events.filter((e) => !filter.has(stripMpls(e.proto))), [events, filter]);
  const animate = useCallback((ev) => {
    if (!ev || !lab) return;
    const link = lab.links.find((l) => l.id === ev.link);
    if (!link) return;
    setAnim({ link: ev.link, key: `${ev.id}-${Date.now()}`, proto: ev.proto, reverse: ev.from !== link.a, dur: 0.7 / speed });
  }, [lab, speed]);
  const step = useCallback((d, abs) => {
    if (abs !== undefined) return animate(visible[abs]);
    setCursor((c) => { const n = Math.max(0, Math.min(visible.length - 1, c + d)); animate(visible[n]); return n; });
  }, [visible, animate]);
  useEffect(() => {
    if (!sim || !lab) return;
    let alive = true;
    const load = async () => {
      try {
        const r = await api.simEvents(lab.name, lastId.current);
        if (!alive) return;
        setCapturing(r.running);
        if (r.events.length) { lastId.current = r.events[r.events.length - 1].id; setEvents((e) => [...e, ...r.events].slice(-5000)); }
      } catch { /* next poll */ }
    };
    load(); const t = setInterval(() => { if (!document.hidden) load(); }, 1000);
    return () => { alive = false; clearInterval(t); };
  }, [sim, lab?.name]);
  useEffect(() => { if (!follow || !visible.length) return; setCursor(visible.length - 1); animate(visible[visible.length - 1]); }, [visible.length, follow]); // eslint-disable-line
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setCursor((c) => { if (c >= visible.length - 1) return c; animate(visible[c + 1]); return c + 1; }), 900 / speed);
    return () => clearInterval(t);
  }, [playing, speed, visible, animate]);
  const onCapture = async (on) => {
    try {
      if (on) { const r = await api.simStart(lab.name); notify(`Capturing on ${r.links} links`, null, "ok"); } else await api.simStop(lab.name);
      setCapturing(on);
    } catch (e) { notify(e.message, null, "bad"); }
  };
  const onClear = async () => { await api.simClear(lab.name).catch(() => {}); setEvents([]); lastId.current = 0; setCursor(0); };
  const toggleSim = async () => { if (sim && capturing) await onCapture(false); setSim(!sim); setPlaying(false); if (!sim) setSide(null); };

  // ---------- annotations ----------
  const annChange = useCallback((id, patch) => {
    pushHistory();
    setLab((l) => ({ ...l, annotations: (l.annotations || []).map((a) => (a.id === id ? { ...a, ...patch } : a)) }));
    setDirty(true);
  }, []); // eslint-disable-line
  const addAnn = (kind, pos) => {
    if (!lab) return;
    const id = `ann-${Date.now().toString(36)}`;
    const p = pos || centre();
    change((l) => ({ ...l, annotations: [...(l.annotations || []), kind === "note"
      ? { id, kind, x: p.x, y: p.y, w: 220, h: 110, text: "", color: "yellow" }
      : { id, kind, x: p.x - 150, y: p.y - 100, w: 320, h: 220, text: "Area", color: "blue" }] }));
  };

  // ---------- graph ----------
  const liveNodes = live?.nodes || {};
  const rfNodes = useMemo(() => {
    const anns = (lab?.annotations || []).map((a) => ({
      id: a.id, type: a.kind, position: { x: a.x, y: a.y }, width: a.w, height: a.h, selected: sel.has(a.id),
      zIndex: a.kind === "area" ? -10 : 2, dragHandle: a.kind === "area" ? ".ann-handle" : undefined,
      data: { text: a.text, color: a.color, onChange: annChange },
    }));
    const devs = (lab?.nodes || []).map((n) => ({
      id: n.id, type: "device", position: { x: n.x, y: n.y }, selected: sel.has(n.id), measured: dims[n.id], zIndex: 3,
      data: { label: n.id, type: n.type, state: powerOff.has(n.id) ? "poweroff" : states[n.id], cablePick: pick === n.id, overlay, cpu: liveNodes[n.id]?.cpu, hit: hit === n.id, alert: alertSum?.nodes?.[lab.name]?.[n.id] },
    }));
    return [...anns, ...devs];
  }, [lab, sel, states, pick, dims, overlay, liveNodes, hit, annChange, alertSum]);
  const presetLabel = (im) => (im?.preset && presets[im.preset] ? { ...im, presetLabel: presets[im.preset].label } : im);
  const rfEdges = useMemo(() => (lab?.links || []).map((l) => ({
    id: l.id, source: l.a, target: l.b, type: "cable", selected: selEdge === l.id, zIndex: 1,
    data: { media: l.media, aIf: labIf(lab, l.a, l.a_if), bIf: labIf(lab, l.b, l.b_if), showIf, aState: status.ifaces?.[l.a]?.[l.a_if], bState: status.ifaces?.[l.b]?.[l.b_if],
      anim: anim && anim.link === l.id ? anim : null, rate: live?.links?.[l.id], overlay, impair: presetLabel(l.impair), admin: l.admin },
  })), [lab, selEdge, showIf, status, anim, live, overlay, presets, hwv]); // eslint-disable-line

  const onNodesChange = (changes) => {
    const devDims = changes.filter((c) => c.type === "dimensions" && c.dimensions && !c.id.startsWith("ann-"));
    if (devDims.length) setDims((d) => ({ ...d, ...Object.fromEntries(devDims.map((c) => [c.id, c.dimensions])) }));
    const selCh = changes.filter((c) => c.type === "select");
    if (selCh.length) setSel((s) => { const n = new Set(s); selCh.forEach((c) => (c.selected ? n.add(c.id) : n.delete(c.id))); return n; });
    const pos = changes.filter((c) => c.type === "position" && c.position);
    if (pos.length && pos.some((c) => c.dragging) && !dragRec.current) { pushHistory(); dragRec.current = true; }
    const annDim = changes.filter((c) => c.type === "dimensions" && c.id.startsWith("ann-") && c.dimensions && c.resizing);
    if (pos.length || annDim.length) {
      const P = Object.fromEntries(pos.map((c) => [c.id, c.position])), D = Object.fromEntries(annDim.map((c) => [c.id, c.dimensions]));
      setLab((l) => ({
        ...l,
        nodes: l.nodes.map((n) => (P[n.id] ? { ...n, x: Math.round(P[n.id].x), y: Math.round(P[n.id].y) } : n)),
        annotations: (l.annotations || []).map((a) => ({ ...a, ...(P[a.id] ? { x: Math.round(P[a.id].x), y: Math.round(P[a.id].y) } : {}), ...(D[a.id] ? { w: Math.round(D[a.id].width), h: Math.round(D[a.id].height) } : {}) })),
      }));
    }
    if (changes.some((c) => c.type === "position" && c.dragging === false)) { dragRec.current = false; setDirty(true); }
    const removed = changes.filter((c) => c.type === "remove").map((c) => c.id);
    if (removed.length) {
      change((l) => ({ ...l, nodes: l.nodes.filter((n) => !removed.includes(n.id)), links: l.links.filter((k) => !removed.includes(k.a) && !removed.includes(k.b)),
        annotations: (l.annotations || []).filter((a) => !removed.includes(a.id)) }));
      setWins((w) => w.filter((x) => !removed.includes(x.node)));
      setSel(new Set());
    }
  };
  const onEdgesChange = (changes) => {
    const removed = changes.filter((c) => c.type === "remove").map((c) => c.id);
    const next = applyEdgeChanges(changes, rfEdges);
    const s = next.find((e) => e.selected);
    setSelEdge(s ? s.id : null);
    if (removed.length) change((l) => ({ ...l, links: l.links.filter((k) => !removed.includes(k.id)) }));
  };

  const centre = () => {
    const r = wrap.current.getBoundingClientRect();
    return rf.screenToFlowPosition({ x: r.left + r.width / 2 + (Math.random() * 80 - 40), y: r.top + r.height / 2 + (Math.random() * 80 - 40) });
  };
  const newIdFrom = (used, type) => { let i = 1; while (used.has(`${PREFIX[type]}${i}`)) i++; return `${PREFIX[type]}${i}`; };
  const defaultProps = (type) => (type === "server" ? { ip_mode: "static", services: { http: { enabled: true } }, ...defaultHardware(type, appSettings) }
    : type === "pc" ? { ip_mode: "static", ...defaultHardware(type, appSettings) } : defaultHardware(type, appSettings));
  const addNode = (type, pos) => {
    if (!lab) return;
    const id = newIdFrom(new Set(lab.nodes.map((n) => n.id)), type);
    const p = pos || centre();
    change((l) => ({ ...l, nodes: [...l.nodes, { id, type, x: Math.round(p.x), y: Math.round(p.y), props: defaultProps(type) }] }));
    notify(`Added ${TYPE_LABEL[type]} ${id}`, deployed ? (liveEdit ? "Starting it now…" : "Redeploy to start it") : null);
  };
  const nextIf = (id) => {
    const used = lab.links.flatMap((k) => [k.a === id && k.a_if, k.b === id && k.b_if]).filter(Boolean);
    let i = 1; while (used.includes(`eth${i}`)) i++; return `eth${i}`;
  };
  const choosePort = (n, ev, then) => {
    if (!portsOf(n)) return then(nextIf(n.id));
    setPortMenu({ node: n.id, x: ev?.clientX ?? 300, y: ev?.clientY ?? 200, then });
  };
  const connect = (a, aIf, b, bIf) => {
    const link = { id: `l${Date.now().toString(36)}`, a, a_if: aIf, b, b_if: bIf, media: tool === "select" ? "copper" : tool };
    change((l) => ({ ...l, links: [...l.links, link] }));
    notify(`Connected ${a} ${labIf(lab, a, aIf)} ↔ ${b} ${labIf(lab, b, bIf)}`, deployed ? (liveEdit ? "Wiring it now…" : "Redeploy to wire it") : null);
    setPick(null); setPickIf(null);
  };
  const onNodeClick = (ev, rn) => {
    if (rn.id.startsWith("ann-")) return;
    if (tool === "select") return openWin(rn.id);
    const n = lab.nodes.find((x) => x.id === rn.id);
    if (!n) return;
    if (!pick) return choosePort(n, ev, (ifn) => { setPick(n.id); setPickIf(ifn); });
    if (pick === n.id) { setPick(null); setPickIf(null); return; }
    const a = pick, aIf = pickIf;
    choosePort(n, ev, (ifn) => connect(a, aIf, n.id, ifn));
  };
  const openWin = (node, tab) => {
    setZTop((z) => z + 1);
    setWins((w) => w.some((x) => x.node === node)
      ? w.map((x) => (x.node === node ? { ...x, z: zTop + 1, tab: tab || x.tab, tk: tab ? Date.now() : x.tk } : x))
      : [...w, { node, tab, tk: Date.now(), z: zTop + 1, pos: { x: 120 + (w.length % 5) * 30, y: 30 + (w.length % 5) * 30 } }]);
  };
  const focusWin = (node) => { setZTop((z) => z + 1); setWins((w) => w.map((x) => (x.node === node ? { ...x, z: zTop + 1 } : x))); };
  const updateNode = (id, props) => quiet((l) => ({ ...l, nodes: l.nodes.map((n) => (n.id === id ? { ...n, props: { ...n.props, ...props } } : n)) }));
  const renameNode = (old, nu) => {
    if (!/^[a-z0-9][a-z0-9-]{0,30}$/.test(nu)) return notify("Names use lowercase letters, digits and dashes.", null, "bad");
    if (lab.nodes.some((n) => n.id === nu)) return notify(`${nu} already exists.`, null, "bad");
    change((l) => ({ ...l, nodes: l.nodes.map((n) => (n.id === old ? { ...n, id: nu } : n)), links: l.links.map((k) => ({ ...k, a: k.a === old ? nu : k.a, b: k.b === old ? nu : k.b })) }));
    setWins((w) => w.map((x) => (x.node === old ? { ...x, node: nu } : x)));
    api.renameFiles(lab.name, old, nu).then((r) => r.moved && notify(`Renamed ${old} to ${nu}`, "Its saved configuration moved with it.", "ok"))
      .catch((e) => notify(`Renamed, but the config folder didn't move: ${e.message}`, null, "warn"));
  };
  const focusNode = (id) => {
    const n = lab?.nodes.find((x) => x.id === id);
    if (!n) return;
    rf.setCenter(n.x + DEV_W / 2, n.y + DEV_H / 2, { zoom: Math.max(rf.getZoom(), 1.1), duration: 600 });
    setHit(id); setTimeout(() => setHit((h) => (h === id ? null : h)), 1800);
  };
  const selDevices = () => lab ? lab.nodes.filter((n) => sel.has(n.id)) : [];
  const duplicate = () => {
    const src = selDevices();
    if (!src.length) return;
    const used = new Set(lab.nodes.map((n) => n.id)); const made = [];
    const copies = src.map((n) => { const id = newIdFrom(used, n.type); used.add(id); made.push(id); const c = { ...clone(n), id, x: n.x + 48, y: n.y + 48 }; delete c.raw; return c; });
    change((l) => ({ ...l, nodes: [...l.nodes, ...copies] }));
    setSel(new Set(made));
    notify(`Duplicated ${src.length} device${src.length > 1 ? "s" : ""}`, "Settings copied; router configs start blank.");
  };
  const align = (mode) => {
    const ns = selDevices();
    if (ns.length < 2) return;
    const xs = ns.map((n) => n.x), ys = ns.map((n) => n.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    const order = [...ns].sort((a, b) => (mode === "distV" ? a.y - b.y : a.x - b.x));
    const at = {};
    order.forEach((n, i) => {
      const t = order.length > 1 ? i / (order.length - 1) : 0;
      at[n.id] = { L: { x: minX }, R: { x: maxX }, C: { x: cx }, T: { y: minY }, B: { y: maxY }, M: { y: cy },
        distH: { x: minX + t * (maxX - minX) }, distV: { y: minY + t * (maxY - minY) } }[mode];
    });
    change((l) => ({ ...l, nodes: l.nodes.map((n) => (at[n.id] ? { ...n, ...Object.fromEntries(Object.entries(at[n.id]).map(([k, v]) => [k, Math.round(v)])) } : n)) }));
  };
  const linkSaved = (id, patch) => quiet((l) => ({ ...l, links: l.links.map((x) => (x.id === id ? Object.fromEntries(Object.entries({ ...x, ...patch }).filter(([, v]) => v !== undefined)) : x)) }));

  // ---------- lab actions ----------
  const flush = async () => { if (dirty && lab) { await api.save(lab, revRef.current); setDirty(false); } };
  const doDeploy = async () => {
    setBusy(deployed ? "Redeploying. Running configs are saved first." : "Deploying the lab. First runs pull images and can take a few minutes.");
    try {
      await flush();
      const r = await api.deploy(lab.name);
      const sf = Object.entries(r.save_failed || {}).map(([n, e]) => `config save FAILED ${n}: ${e}`).join("\n");
      notify(r.ok ? "Lab deployed" : "Deploy failed — see the log", [sf, r.output].filter(Boolean).join("\n"), r.ok ? "ok" : "bad");
      if (!r.ok || sf) setShowLog(true);
      if (r.ok) liveBase.current = JSON.parse(JSON.stringify(labRef.current));
      setStatus(await api.status(lab.name));
    } catch (e) { notify(e.message, null, "bad"); setShowLog(true); }
    setBusy(null);
  };
  const doDestroy = async () => {
    if (!confirm(`Destroy ${lab.name}? Router configs are saved first.`)) return;
    setBusy("Saving configs and destroying the lab");
    try {
      const r = await api.destroy(lab.name);
      const sf = Object.entries(r.save_failed || {}).map(([n, e]) => `config save FAILED ${n}: ${e}`).join("\n");
      notify(r.ok ? "Lab destroyed" : "Destroy failed", [`Saved configs: ${r.saved_configs.join(", ") || "none"}`, sf, r.output].filter(Boolean).join("\n"), r.ok ? "ok" : "bad");
      if (sf) setShowLog(true);
      setStatus(await api.status(lab.name)); setLive(null);
    } catch (e) { notify(e.message, null, "bad"); }
    setBusy(null);
  };
  const doSaveConfigs = async () => {
    try {
      const r = await api.saveConfigs(lab.name);
      const failed = Object.entries(r.failed || {});
      const detail = [`Saved (${r.saved.length}): ${r.saved.join(", ") || "-"}`, ...failed.map(([n, e]) => `FAILED ${n}: ${e}`),
        r.skipped?.length ? `Not running, kept last saved file: ${r.skipped.join(", ")}` : "", "PCs, servers and switches keep their settings in the lab file automatically."].filter(Boolean).join("\n");
      notify(failed.length ? `Save configs: ${failed.length} router(s) failed` : `Saved running configs for ${r.saved.length} router(s)`, detail, failed.length ? "bad" : "ok");
      if (failed.length) setShowLog(true);
    } catch (e) { notify(`Save configs failed: ${e.message}`, null, "bad"); setShowLog(true); }
  };
  const doPause = async () => {
    try { paused ? await api.resume(lab.name) : await api.pause(lab.name); setStatus(await api.status(lab.name)); notify(paused ? "Network resumed" : "Network paused (every container frozen)"); }
    catch (e) { notify(e.message, null, "bad"); }
  };
  const newLab = async () => {
    const name = prompt("Lab name (lowercase letters, digits, dashes)");
    if (!name) return;
    try { await api.create(name); await refreshLabs(); openLab(name); notify(`Created ${name}`, "Drag devices in from the left, cable them, then Deploy.", "ok"); } catch (e) { notify(e.message, null, "bad"); }
  };
  const deleteLab = async () => {
    if (!confirm(`Delete lab ${lab.name}? This removes its saved files and snapshots.`)) return;
    try { await api.remove(lab.name); openLab(null); refreshLabs(); notify(`Deleted ${lab.name}`); } catch (e) { notify(e.message, null, "bad"); }
  };
  const fromTemplate = async (t) => {
    const name = prompt(`Name for the new “${t.title}” lab`, t.id);
    if (!name) return;
    setBusy(`Creating ${name} from ${t.title}`);
    try { await api.fromTemplate(t.id, name); await refreshLabs(); await openLab(name); notify(`Created ${name}`, "Click Deploy to start it.", "ok"); setTimeout(() => rf.fitView({ duration: 600, padding: 0.15 }), 150); }
    catch (e) { notify(e.message, null, "bad"); }
    setBusy(null);
  };
  const quickSnapshot = async () => {
    const label = prompt("Snapshot label", `before change ${new Date().toLocaleTimeString()}`);
    if (label == null) return;
    try { const r = await api.snapshot(lab.name, label); notify(`Snapshot “${r.snapshot.label}” saved`, `${r.snapshot.files} files`, "ok"); } catch (e) { notify(e.message, null, "bad"); }
  };

  // ---------- context menus ----------
  const onNodeContextMenu = (e, n) => {
    e.preventDefault();
    if (n.id.startsWith("ann-")) {
      return setMenu({ x: e.clientX, y: e.clientY, items: [{ header: n.type === "note" ? "Note" : "Area" },
        { label: "Delete", icon: "trash", danger: true, onClick: () => change((l) => ({ ...l, annotations: l.annotations.filter((a) => a.id !== n.id) })) }] });
    }
    const d = lab.nodes.find((x) => x.id === n.id);
    const running = states[n.id] === "running";
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { header: `${d.id} · ${TYPE_LABEL[d.type]}${states[d.id] ? ` · ${states[d.id]}` : ""}` },
      { label: "Open", icon: "window", onClick: () => openWin(d.id) },
      ["router", "switch", "firewall"].includes(d.type) && { label: "CLI", icon: "terminal", disabled: !running, onClick: () => openWin(d.id, "CLI") },
      ["pc", "server"].includes(d.type) && { label: "Desktop", icon: "terminal", onClick: () => openWin(d.id, "Desktop") },
      { label: "Monitor", icon: "pulse", disabled: !running, onClick: () => openWin(d.id, "Monitor") },
      { label: "Config", icon: "grid", onClick: () => openWin(d.id, "Config") },
      "-",
      { label: "Duplicate", icon: "copy", kbd: "Ctrl+D", onClick: () => { setSel(new Set([d.id])); setTimeout(() => keys.current.duplicate(), 0); } },
      { label: "Start cable from here", icon: "link", onClick: () => { setTool(tool === "select" ? "copper" : tool); setPick(null); choosePort(d, e, (ifn) => { setPick(d.id); setPickIf(ifn); }); } },
      { label: "Generate traffic from here", icon: "flow", disabled: !running, onClick: () => { if (sim) toggleSim(); setTrafficPreset({ src: d.id, k: Date.now() }); setSide("traffic"); } },
      "-",
      { label: "Delete device", icon: "trash", danger: true, onClick: () => { change((l) => ({ ...l, nodes: l.nodes.filter((x) => x.id !== d.id), links: l.links.filter((k) => k.a !== d.id && k.b !== d.id) })); setWins((w) => w.filter((x) => x.node !== d.id)); } },
    ] });
  };
  const onEdgeContextMenu = (e, edge) => {
    e.preventDefault();
    const l = lab.links.find((x) => x.id === edge.id);
    const run = async (fn, msg, patch) => { try { const r = await fn(); notify(`${l.a}–${l.b}: ${msg}`, r.output, r.ok === false ? "bad" : "ok"); if (patch) linkSaved(l.id, patch); } catch (err) { notify(err.message, null, "bad"); } };
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { header: `${l.a} ${l.a_if} ↔ ${l.b} ${l.b_if}` },
      { label: "Link settings…", icon: "link", onClick: () => setSelEdge(l.id) },
      l.admin === "down" ? { label: "Bring link up", icon: "play", onClick: () => run(() => api.linkState(lab.name, l.id, true), "link up", { admin: undefined }) }
        : { label: "Take link down", icon: "stop", onClick: () => run(() => api.linkState(lab.name, l.id, false), "link down", { admin: "down" }) },
      { label: "Flap for 10 s", icon: "zap", disabled: !deployed || l.admin === "down", onClick: () => run(() => api.flap(lab.name, l.id, 10), "flapping 10 s") },
      "-", { header: "Impairment" },
      ...["4g", "satellite", "lossy-wifi", "congested-wan"].filter((k) => presets[k]).map((k) => ({ label: presets[k].label, icon: "wave",
        onClick: () => run(() => api.impair(lab.name, l.id, { ...presets[k], preset: k, dir: "both" }), presets[k].label, { impair: { ...presets[k], preset: k, dir: "both" } }) })),
      l.impair && { label: "Clear impairment", icon: "x", onClick: () => run(() => api.impair(lab.name, l.id, null), "impairment cleared", { impair: undefined }) },
      "-",
      { label: "Delete cable", icon: "trash", danger: true, onClick: () => change((x) => ({ ...x, links: x.links.filter((k) => k.id !== l.id) })) },
    ] });
  };
  const onPaneContextMenu = (e) => {
    e.preventDefault();
    if (!lab) return;
    const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    setMenu({ x: e.clientX, y: e.clientY, items: [
      { header: "Add here" },
      ...["router", "switch", "firewall", "pc", "server"].map((t) => ({ label: TYPE_LABEL[t], icon: "plus", onClick: () => addNode(t, { x: p.x - DEV_W / 2, y: p.y - DEV_H / 2 }) })),
      { label: "Note", icon: "note", onClick: () => addAnn("note", p) }, { label: "Area", icon: "area", onClick: () => addAnn("area", { x: p.x + 150, y: p.y + 100 }) },
      "-",
      { label: "Select all devices", icon: "grid", kbd: "Ctrl+A", onClick: () => setSel(new Set(lab.nodes.map((n) => n.id))) },
      { label: "Fit to screen", icon: "search", onClick: () => rf.fitView({ duration: 500, padding: 0.15 }) },
    ] });
  };

  // ---------- commands + shortcuts ----------
  const commands = [
    { label: deployed ? "Redeploy lab" : "Deploy lab", icon: "play", group: "Lab", run: doDeploy, disabled: !lab || !!busy },
    { label: "Destroy lab", icon: "stop", group: "Lab", run: doDestroy, disabled: !deployed },
    { label: "Save running configs", icon: "save", group: "Lab", kbd: "Ctrl+S", run: doSaveConfigs, disabled: !deployed },
    { label: "Take snapshot", icon: "camera", group: "Lab", run: quickSnapshot, disabled: !lab },
    { label: "Lab manager: templates", icon: "layers", group: "Lab", run: () => setMgr("templates") },
    { label: "Lab manager: challenges", icon: "flag", group: "Lab", run: () => setMgr("challenges") },
    { label: "Challenge panel", icon: "flag", group: "Lab", run: () => setSide("challenge"), disabled: !lab },
    { label: "Lab manager: snapshots & diff", icon: "diff", group: "Lab", run: () => setMgr("snapshots"), disabled: !lab },
    { label: "Clone / export / import", icon: "copy", group: "Lab", run: () => setMgr("share") },
    { label: "New blank lab", icon: "plus", group: "Lab", run: newLab },
    { label: "Toggle dashboard", icon: "dash", group: "View", kbd: "M", run: () => setSide((s) => (s ? null : "dash")), disabled: !lab },
    { label: "Toggle traffic overlay", icon: "flow", group: "View", kbd: "O", run: () => setOverlay((v) => !v) },
    { label: "Toggle simulation mode", icon: "sim", group: "View", run: toggleSim, disabled: !lab },
    { label: "Toggle dark mode", icon: theme === "dark" ? "sun" : "moon", group: "View", kbd: "T", run: () => setTheme((t) => (t === "dark" ? "light" : "dark")) },
    { label: "Toggle snap to grid", icon: "grid", group: "View", kbd: "G", run: () => setSnap((v) => !v) },
    { label: "Toggle port labels", icon: "tag", group: "View", run: () => setShowIf((v) => !v) },
    { label: "Fit to screen", icon: "search", group: "View", run: () => rf.fitView({ duration: 500, padding: 0.15 }) },
    { label: "Add note", icon: "note", group: "Edit", kbd: "N", run: () => addAnn("note"), disabled: !lab },
    { label: "Add area", icon: "area", group: "Edit", kbd: "A", run: () => addAnn("area"), disabled: !lab },
    { label: "Undo", icon: "undo", group: "Edit", kbd: "Ctrl+Z", run: undo, disabled: !past.current.length },
    { label: "Redo", icon: "redo", group: "Edit", kbd: "Ctrl+Y", run: redo, disabled: !future.current.length },
    { label: "Duplicate selection", icon: "copy", group: "Edit", kbd: "Ctrl+D", run: duplicate, disabled: !sel.size },
    { label: "Help & how-to guide", icon: "help", group: "Help", run: () => setHelp("start") },
    { label: "Keyboard shortcuts", icon: "keyboard", group: "Help", kbd: "?", run: () => setHelp("shortcuts") },
    { label: "Settings", icon: "gear", group: "App", kbd: ",", run: () => setSettingsTab("general") },
    { label: "Alert rules", icon: "flag", group: "App", run: () => setSettingsTab("alerts") },
    { label: "Notifications", icon: "bell", group: "App", run: () => setSide("alerts") },
    { label: "Traffic generator", icon: "flow", group: "Lab", run: () => setSide("traffic"), disabled: !deployed },
    ...labs.map((l) => ({ label: `Open lab ${l.name}`, icon: "layers", group: "Labs", run: () => openLab(l.name) })),
  ];
  const keys = useRef({}); keys.current = { undo, redo, duplicate, doSaveConfigs, addAnn, addNode, deployed, lab };
  useEffect(() => {
    const h = (e) => {
      const k = keys.current, mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
      if (mod && key === "k") { e.preventDefault(); setPalette(""); return; }
      if (mod && key === "f" && k.lab) { e.preventDefault(); setPalette(""); return; }
      if (isTyping(e)) return;
      if (mod && key === "z" && !e.shiftKey) { e.preventDefault(); k.undo(); return; }
      if (mod && (key === "y" || (key === "z" && e.shiftKey))) { e.preventDefault(); k.redo(); return; }
      if (mod && key === "d") { e.preventDefault(); k.duplicate(); return; }
      if (mod && key === "s") { e.preventDefault(); if (k.deployed) k.doSaveConfigs(); return; }
      if (mod && key === "a" && k.lab) { e.preventDefault(); setSel(new Set(k.lab.nodes.map((n) => n.id))); return; }
      if (mod || e.altKey) return;
      const map = { v: () => { setTool("select"); setPick(null); setPickIf(null); setPortMenu(null); }, c: () => setTool("copper"), f: () => setTool("fibre"), s: () => setTool("serial"), n: () => k.addAnn("note"), a: () => k.addAnn("area"),
        g: () => setSnap((v) => !v), o: () => setOverlay((v) => !v), m: () => k.lab && setSide((s) => (s ? null : "dash")), t: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
        "?": () => setHelp("shortcuts"), ",": () => setSettingsTab("general"), 1: () => k.addNode("router"), 2: () => k.addNode("switch"), 3: () => k.addNode("firewall"), 4: () => k.addNode("pc"), 5: () => k.addNode("server"),
        escape: () => { setPick(null); setPickIf(null); setPortMenu(null); setSel(new Set()); setSelEdge(null); setMenu(null); } };
      if (map[key] && (k.lab || ["t", "?", ",", "escape"].includes(key))) { e.preventDefault(); map[key](); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const edge = lab?.links.find((l) => l.id === selEdge);
  const updLink = (k, v) => change((l) => ({ ...l, links: l.links.map((x) => (x.id === selEdge ? { ...x, [k]: v } : x)) }));
  const last = log[log.length - 1];
  const totalBps = Object.values(live?.links || {}).reduce((a, r) => a + r.total_bps, 0);
  const devSel = selDevices().length;
  const sideOpen = sim ? "sim" : side;

  const invite = location.pathname.match(/^\/invite\/([\w-]+)$/);
  if (invite) return <InvitePage token={invite[1]} />;
  const reset = location.pathname.match(/^\/reset\/([\w-]+)$/);
  if (reset) return <ResetPage token={reset[1]} />;
  if (auth.need) return <><Login canRequest={auth.requests} canForgot={auth.forgot} onDone={(info) => { history.replaceState(null, "", "/");
    if (info?.recovery_left != null) setTimeout(() => notify(`Signed in with a recovery code: ${info.recovery_left} left`, "Make new ones in Account and password if you're running low.", "warn"), 800); api.authState().then((a) => { setMe(a.user); setAuth({ checked: true, need: false, user: a.user, multi: a.multi_user, requests: a.requests, forgot: a.forgot }); }); }} /><Toasts items={toasts} onClose={(id) => setToasts((x) => x.filter((t) => t.id !== id))} /></>;
  const me = auth.user;
  if (!auth.need && me?.must_setup_2fa) return <Require2FA me={me} onDone={() => api.authState().then((a) => { setMe(a.user); setAuth((x) => ({ ...x, user: a.user })); })} />;
  return (
    <div className={`app ${sideOpen ? `app-side app-${sideOpen}` : ""}`}>
      <header className="top">
        <div className="brand"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="3" /><circle cx="19" cy="5" r="3" /><circle cx="19" cy="19" r="3" /><path d="M7.6 10.6l8.8-4.2M7.6 13.4l8.8 4.2" /></svg>Patchyard</div>
        <select className="labsel" value={lab?.name || ""} onChange={(e) => openLab(e.target.value)} aria-label="Lab">
          <option value="">Choose a lab…</option>
          {labs.map((l) => <option key={l.name} value={l.name}>{l.name} ({l.nodes}){me && l.owner && l.owner !== me.name ? ` · ${l.owner}` : ""}</option>)}
        </select>
        <TB icon="plus" label="New lab" onClick={newLab} />
        <TB icon="layers" label="Lab manager" onClick={() => setMgr(lab ? "snapshots" : "templates")} />
        {lab && <>
          <span className="sep" />
          <button className="tb tb-primary deploy" disabled={!!busy} onClick={doDeploy} title={deployed ? "Redeploy (configs saved first)" : "Deploy"}>
            <Icon name={deployed ? "redo2" : "play"} /><span className="tb-text">{deployed ? "Redeploy" : "Deploy"}</span></button>
          <TB icon="stop" label="Destroy" onClick={doDestroy} disabled={!!busy || !deployed} />
          <TB icon="save" label="Save running configs" kbd="Ctrl+S" onClick={doSaveConfigs} disabled={!deployed} />
          <TB icon="pause" label={paused ? "Resume network" : "Pause network"} onClick={doPause} disabled={!deployed} on={paused} />
          <TB icon="camera" label="Take snapshot" onClick={quickSnapshot} />
          <span className="sep" />
          <TB icon="undo" label="Undo" kbd="Ctrl+Z" onClick={undo} disabled={!past.current.length} />
          <TB icon="redo" label="Redo" kbd="Ctrl+Y" onClick={redo} disabled={!future.current.length} />
          <span className="sep" />
          <TB icon="grid" label="Snap to grid" kbd="G" on={snap} onClick={() => setSnap(!snap)} />
          <TB icon="tag" label="Port labels" on={showIf} onClick={() => setShowIf(!showIf)} />
          <TB icon="flow" label="Traffic overlay" kbd="O" on={overlay} onClick={() => setOverlay(!overlay)} />
          <TB icon="search" label="Find device or command" kbd="Ctrl+K" onClick={() => setPalette("")} />
        </>}
        <span className="grow" />
        {lab && <span className={`save-state ${dirty ? "saving" : ""}`}>{dirty ? "Saving…" : "Saved"}</span>}
        {lab && <div className="mode" role="group" aria-label="Mode">
          <button className={!sim ? "on" : ""} onClick={() => sim && toggleSim()}>Realtime</button>
          <button className={sim ? "on" : ""} onClick={() => !sim && toggleSim()}>Simulation</button>
        </div>}
        {lab && <TB icon="flag" label={lab.challenge ? `Challenge: ${lab.challenge.title}` : "Challenge (add instructions and checks)"} on={side === "challenge" && !sim}
          onClick={() => { if (sim) toggleSim(); setSide(side === "challenge" ? null : "challenge"); }} />}
        {lab && <TB icon="dash" label="Dashboard" kbd="M" on={side === "dash" && !sim} onClick={() => { if (sim) toggleSim(); setSide(side === "dash" && !sim ? null : "dash"); }} />}
        {lab && <TB icon="flow" label="Traffic generator" on={side === "traffic" && !sim} disabled={!deployed} onClick={() => { if (sim) toggleSim(); setSide(side === "traffic" ? null : "traffic"); }} />}
        <button className={`tb bell ${side === "alerts" ? "on" : ""} ${alertSum?.by_severity?.critical ? "crit" : alertSum?.unacked ? "warn" : ""}`} title="Notifications"
          aria-label={`Notifications, ${alertSum?.unacked || 0} unacknowledged`} onClick={() => { if (sim) toggleSim(); setSide(side === "alerts" ? null : "alerts"); }}>
          <Icon name="bell" />{alertSum?.unacked > 0 && <span className="badge">{alertSum.unacked > 99 ? "99+" : alertSum.unacked}</span>}</button>
        <TB icon="gear" label="Settings" kbd="," onClick={() => setSettingsTab("general")} />
        <TB icon={theme === "dark" ? "sun" : "moon"} label={theme === "dark" ? "Light mode" : "Dark mode"} kbd="T" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} />
        <TB icon="help" label="Help & how-to guide" onClick={() => setHelp(help ? null : "start")} on={!!help} />
        {me && auth.multi !== undefined && (
          <div className="acct">
            <button ref={acctBtn} className={`tb acct-btn ${acct ? "on" : ""}`} onClick={() => setAcct(!acct)} aria-haspopup="menu" aria-expanded={acct}
              title={alertSum?.requests ? `${alertSum.requests} account request(s) waiting` : "Your account"}>
              <span className="avatar">{me.name.slice(0, 1).toUpperCase()}{alertSum?.requests > 0 && <span className="dot" />}</span><span className="tb-text">{me.name}</span></button>
            {acct && <AccountMenu anchor={acctBtn} onClose={closeAcct}>
              <div className="hint">Signed in as <b>{me.full_name || me.name}</b>{me.full_name ? ` (${me.name})` : ""}{me.role === "admin" ? " · admin" : ""}</div>
              {me.role === "admin" && <button role="menuitem" onClick={() => { setAcct(false); setAdmin("users"); }}><Icon name="grid" size={15} /> Admin: users, invites, labs</button>}
              {me.role === "admin" && alertSum?.requests > 0 && <button role="menuitem" onClick={() => { setAcct(false); setAdmin("requests"); }}><Icon name="flag" size={15} /> Account requests <span className="badge inline">{alertSum.requests}</span></button>}
              <button role="menuitem" onClick={() => { setAcct(false); setSettingsTab("security"); }}><Icon name="keyboard" size={15} /> Account and password</button>
              <button role="menuitem" onClick={() => { setAcct(false); setHelp("start"); }}><Icon name="help" size={15} /> Help & how-to guide</button>
              <button role="menuitem" onClick={async () => { await api.logout(); location.href = "/"; }}><Icon name="x" size={15} /> Sign out</button>
            </AccountMenu>}
          </div>)}
      </header>

      <aside className="palette" aria-label="Devices and tools">
        <h3>Devices</h3>
        {["router", "switch", "firewall", "pc", "server"].map((t, i) => {
          const I = ICONS[t];
          return (
            <button key={t} className="pal" draggable disabled={!lab} onDragStart={(e) => e.dataTransfer.setData("application/netlab", t)}
              onClick={() => addNode(t)} title={`Drag or click to add a ${TYPE_LABEL[t]} (${i + 1})`}>
              <I size={40} /><span>{TYPE_LABEL[t]}</span><kbd className="pal-k">{i + 1}</kbd>
            </button>
          );
        })}
        <h3>Cables</h3>
        {[["select", "Select", "V"], ["copper", "Copper", "C"], ["fibre", "Fibre", "F"], ["serial", "Serial", "S"]].map(([k, l, kb]) => (
          <button key={k} disabled={!lab} className={`pal tool ${tool === k ? "on" : ""} tool-${k}`} onClick={() => { setTool(k); setPick(null); }} title={`${l} (${kb})`}>
            {k === "select" ? <span className="arrow">↖</span> : <CableIcon />}<span>{l}</span>
          </button>
        ))}
        {tool !== "select" && <p className="hint">{pick ? `Now click the device to connect ${pick} ${labIf(lab, pick, pickIf)} to.` : "Click the first device."}</p>}
        <h3>Annotate</h3>
        <button className="pal tool" disabled={!lab} onClick={() => addAnn("note")} title="Note (N)"><Icon name="note" size={22} /><span>Note</span></button>
        <button className="pal tool" disabled={!lab} onClick={() => addAnn("area")} title="Area (A)"><Icon name="area" size={22} /><span>Area</span></button>
      </aside>

      <main className="canvas" ref={wrap}
        onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
        onDrop={(e) => { e.preventDefault(); const t = e.dataTransfer.getData("application/netlab"); if (t) addNode(t, rf.screenToFlowPosition({ x: e.clientX - 38, y: e.clientY - 40 })); }}>
        {lab ? (
          <ReactFlow nodes={rfNodes} edges={rfEdges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onNodeClick={onNodeClick}
            onNodeContextMenu={onNodeContextMenu} onEdgeContextMenu={onEdgeContextMenu} onPaneContextMenu={onPaneContextMenu}
            onPaneClick={() => { setPick(null); setPickIf(null); setMenu(null); setPortMenu(null); }} deleteKeyCode={["Delete", "Backspace"]} multiSelectionKeyCode={["Meta", "Control"]}
            snapToGrid={snap} snapGrid={[24, 24]} nodesConnectable={false} fitView minZoom={0.15} maxZoom={2.5} proOptions={{ hideAttribution: true }}>
            <Background gap={24} size={snap ? 2 : 1.5} color="var(--grid)" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable nodeColor={(n) => (n.type !== "device" ? "transparent" : states[n.id] === "running" ? "#2E9E5B" : "#A3AFBA")} maskColor="var(--minimap-mask)" />
          </ReactFlow>
        ) : (
          <Welcome labs={labs} tpls={tpls} onOpen={openLab} onNew={newLab} onTemplate={fromTemplate} onManager={() => setMgr("share")} />
        )}
        {lab && !lab.nodes.length && !(lab.annotations || []).length && (
          <div className="empty-hint" aria-hidden="true"><div><b>An empty lab.</b> To build one:
            <ol><li>Drag a device from the left (or press 1-5).</li><li>Pick a cable (C copper, F fibre, S serial), click two devices and choose their ports.</li>
              <li>Double-click a device to configure it, then Deploy.</li></ol>
            Or start from a template in the Lab manager.</div></div>
        )}
        {lab && devSel >= 2 && (
          <div className="alignbar" role="toolbar" aria-label="Align selection">
            <span className="hint">{devSel} selected</span>
            {[["L", "alignL", "Align left"], ["C", "alignC", "Align centres"], ["R", "alignR", "Align right"], ["T", "alignT", "Align top"], ["M", "alignM", "Align middles"], ["B", "alignB", "Align bottom"],
              ["distH", "distH", "Distribute horizontally"], ["distV", "distV", "Distribute vertically"]].map(([m, ic, l]) => <TB key={m} icon={ic} label={l} onClick={() => align(m)} />)}
            <TB icon="copy" label="Duplicate" kbd="Ctrl+D" onClick={duplicate} />
          </div>
        )}
        {lab && deployed && overlay && (
          <div className="legend" aria-label="Traffic legend">
            <span className="hint">Traffic</span><span className="lg lg1" />&lt;1M<span className="lg lg2" />&lt;10M<span className="lg lg3" />&lt;100M<span className="lg lg4" />≥100M
            <span className="mono">Σ {fmtBps(totalBps)}</span>
          </div>
        )}
        {edge && (
          <LinkPanel key={edge.id} lab={lab} link={edge} deployed={deployed} rate={live?.links?.[edge.id]} hist={linkHist[edge.id]} presets={presets}
            onEdit={updLink} onLinkSaved={linkSaved} notify={notify} onClose={() => setSelEdge(null)}
            onDelete={() => { change((l) => ({ ...l, links: l.links.filter((x) => x.id !== edge.id) })); setSelEdge(null); }} />
        )}
        {portMenu && lab && (() => {
          const n = lab.nodes.find((x) => x.id === portMenu.node);
          return n ? <PortMenu lab={lab} node={n} x={portMenu.x} y={portMenu.y} media={tool === "select" ? "copper" : tool}
            otherMedia={pick && pick !== n.id ? (portsOf(lab.nodes.find((x) => x.id === pick)) || []).find((p) => p.if === pickIf)?.media : null}
            onPick={(ifn) => { const f = portMenu.then; setPortMenu(null); f(ifn); }} onClose={() => setPortMenu(null)} /> : null;
        })()}
        {wins.map((w) => {
          const n = lab?.nodes.find((x) => x.id === w.node);
          return n ? (
            <DeviceWindow key={w.node} lab={lab} node={n} state={states[n.id]} z={w.z} initialPos={w.pos} initialTab={w.tab} tabKey={w.tk}
              onFocus={() => focusWin(w.node)} onClose={() => setWins((x) => x.filter((y) => y.node !== w.node))}
              onUpdateNode={updateNode} onRename={renameNode} notify={notify}
              deployed={deployed} poweredOff={powerOff.has(n.id)} ifStates={status.ifaces?.[n.id]} settings={appSettings}
              onStatus={() => api.status(lab.name).then((s) => { setStatus(s); pullServerProps(s); }).catch(() => {})} />
          ) : null;
        })}
      </main>

      {sim && lab && (
        <SimPanel lab={lab} events={events} visible={visible} cursor={cursor} setCursor={setCursor} filter={filter} setFilter={setFilter}
          playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} follow={follow} setFollow={setFollow}
          capturing={capturing} onCapture={onCapture} onClear={onClear} onStep={step} notify={notify} />
      )}
      {!sim && side === "alerts" && (
        <NotificationsPanel onClose={() => setSide(null)} notify={notify} onSettings={() => setSettingsTab("alerts")} refreshKey={alertSum?.last_event}
          onGoto={async (labName, node) => { if (labName !== lab?.name) await openLab(labName); setTimeout(() => { focusNode(node); }, 400); }} />
      )}
      {!sim && side === "challenge" && lab && (
        <ChallengePanel key={lab.name} lab={lab} deployed={deployed} me={me} notify={notify} onClose={() => setSide(null)}
          onChange={(c) => quiet((l) => { const n = { ...l }; if (c) n.challenge = c; else delete n.challenge; return n; })} />
      )}
      {!sim && side === "traffic" && lab && (
        <TrafficPanel key={trafficPreset?.k || "t"} lab={lab} states={states} preset={trafficPreset} notify={notify} onClose={() => setSide(null)} />
      )}
      {!sim && side === "dash" && lab && (
        <Dashboard lab={lab} states={states} live={live} linkHist={linkHist} log={log} onFocusNode={(id) => { focusNode(id); openWin(id, "Monitor"); }}
          onSelectLink={(id) => setSelEdge(id)} onClose={() => setSide(null)} />
      )}

      <footer className="status">
        <button className="link" onClick={() => setShowLog(!showLog)}>{showLog ? "Hide log" : `Log (${log.length})`}</button>
        <span className="msg">{last ? last.msg : "Ready"}</span>
        {lab && deployed && <span className="mono counts">Σ {fmtBps(totalBps)}</span>}
        {lab && <span className="counts">{lab.nodes.length} devices · {lab.links.length} links · {Object.values(states).filter((s) => s === "running").length} running</span>}
        {lab && <button className="link danger" disabled={deployed} onClick={deleteLab} title={deployed ? "Destroy the lab first" : "Delete this lab"}>Delete lab</button>}
      </footer>
      {showLog && (
        <div className="logpane">
          {log.slice().reverse().map((l, i) => (
            <div key={i} className={`log-${l.kind}`}><span className="mono">{l.t.toLocaleTimeString()}</span> {l.msg}{l.detail && <pre>{l.detail}</pre>}</div>
          ))}
        </div>
      )}
      <Toasts items={toasts} onClose={(id) => setToasts((x) => x.filter((t) => t.id !== id))} />
      {menu && <Menu {...menu} onClose={() => setMenu(null)} />}
      {palette != null && <Palette initial={palette} commands={commands} devices={(lab?.nodes || []).map((n) => ({ ...n, state: states[n.id] }))}
        onPickDevice={(id) => { focusNode(id); openWin(id); }} onClose={() => setPalette(null)} />}
      {mgr && <LabManager lab={lab} deployed={deployed} initial={mgr} notify={notify} onOpenLab={openLab}
        onStartChallenge={async (n) => { await openLab(n); setSide("challenge"); refreshLabs(); }} onClose={() => setMgr(null)} refreshLabs={refreshLabs}
        onReload={async () => setLab(await api.get(lab.name))} onRedeploy={doDeploy} />}
      <Suspense fallback={null}>
      {help && <Guide section={help} onSection={setHelp} onClose={() => setHelp(null)} keys={KEYS} me={me} accounts={!!me && auth.multi !== undefined} />}
      {admin && <Admin tab={admin} onTab={setAdmin} onClose={() => { setAdmin(null); refreshLabs(); }} notify={notify} me={me} onOpenLab={openLab} />}
      {settingsTab && <Settings initial={settingsTab} me={me} onClose={() => setSettingsTab(null)} notify={notify} labs={labs} lab={lab} onSaved={setAppSettings}
        appearance={{ theme, setTheme, overlay, setOverlay, showIf, setShowIf, snap, setSnap }} />}
      </Suspense>
      {busy && <div className="busy" role="status"><div className="spinner" /><p>{busy}</p></div>}
    </div>
  );
}

function Welcome({ labs, tpls, onOpen, onNew, onTemplate, onManager }) {
  return (
    <div className="welcome">
      <div className="hero">
        <h1>Build a network. Run it for real.</h1>
        <p>Every device is a real container: FRR routers with real OSPF, BGP, MPLS and VRFs, 802.1Q switches, Linux PCs and servers. Monitor it live, break links on purpose, snapshot and roll back.</p>
        <div className="row"><button className="primary big" onClick={onNew}><Icon name="plus" /> New blank lab</button>
          <button className="big" onClick={onManager}><Icon name="upload" /> Import</button></div>
      </div>
      <h2>Start from a template</h2>
      <div className="tpl-grid">{tpls.map((t) => (
        <button key={t.id} className="tpl tpl-btn" onClick={() => onTemplate(t)}>
          <MiniTopo id={t.id} /><h3>{t.title} <span className="hint">{t.devices} devices</span></h3><p>{t.desc}</p>
        </button>))}
      </div>
      {labs.length > 0 && (<>
        <h2>Your labs</h2>
        <div className="labcards">{labs.map((l) => (
          <button key={l.name} className="labcard" onClick={() => onOpen(l.name)}><Icon name="layers" /><b>{l.name}</b><span className="hint">{l.nodes} devices · {l.links} links</span></button>))}
        </div>
      </>)}
    </div>
  );
}

const KEYS = [
  ["Ctrl+K / Ctrl+F", "Find a device or run a command"], ["Ctrl+Z · Ctrl+Y", "Undo · redo (topology, positions, notes)"], ["Ctrl+D", "Duplicate selected devices"],
  ["Ctrl+A", "Select all devices"], ["Shift+drag", "Box select"], ["Ctrl+click", "Add to selection"], ["Delete", "Delete selection"], ["Ctrl+S", "Save running configs"],
  ["1 2 3 4 5", "Add router · switch · firewall · PC · server"], ["V · C · F · S", "Select · copper · fibre · serial tool"], ["N · A", "Add note · area"],
  ["O", "Traffic overlay"], ["M", "Dashboard"], ["G", "Snap to grid"], ["T", "Dark / light"], ["Right-click", "Context menu on devices, cables, canvas"],
  ["Double-click", "Edit a note or area label · maximise a window"], [",", "Settings"], ["Esc", "Cancel / clear selection"],
];
// The account menu lives in a portal so it overlays the canvas instead of growing the toolbar
// (the toolbar scrolls sideways on narrow screens, which would clip it or add a scrollbar).
function AccountMenu({ anchor, onClose, children }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);
  useEffect(() => {
    const place = () => { const r = anchor.current?.getBoundingClientRect(); if (r) setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) }); };
    place();
    const away = (e) => { if (!ref.current?.contains(e.target) && !anchor.current?.contains(e.target)) onClose(); };
    const esc = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("resize", place); document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { window.removeEventListener("resize", place); document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [anchor, onClose]);
  if (!pos) return null;
  return createPortal(<div ref={ref} className="acct-menu" role="menu" style={{ top: pos.top, right: pos.right }}>{children}</div>, document.body);
}
