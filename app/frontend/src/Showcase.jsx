// Public, read-only view of a lab whose owner made a showcase link (/s/<token>): the canvas,
// and each router's configuration (passwords and keys hidden). No sign-in.
import { useEffect, useMemo, useState } from "react";
import { ReactFlow, ReactFlowProvider, Background, Controls } from "@xyflow/react";
import { DeviceNode, CableEdge, NoteNode, AreaNode } from "./canvas.jsx";
import { ICONS, TYPE_LABEL } from "./icons.jsx";
import { setCatalogue, labIf, chassisOf } from "./hw.js";
import { Rich } from "./Challenge.jsx";

const nodeTypes = { device: DeviceNode, note: NoteNode, area: AreaNode };
const edgeTypes = { cable: CableEdge };
const noop = () => {};

export default function Showcase({ token }) {
  const [lab, setLab] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [hw, setHw] = useState(0);
  useEffect(() => {
    fetch("/api/hardware").then((r) => (r.ok ? r.json() : null)).then((c) => { if (c) { setCatalogue(c); setHw(1); } }).catch(() => {});
    fetch(`/api/showcase/${encodeURIComponent(token)}`).then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "this link doesn't exist (any more)");
      setLab(d); document.title = `${d.name} · Patchyard`;
    }).catch((e) => setErr(e.message));
  }, [token]);
  const nodes = useMemo(() => !lab ? [] : [
    ...(lab.annotations || []).map((a) => ({ id: a.id, type: a.kind, position: { x: a.x, y: a.y }, width: a.w, height: a.h, zIndex: a.kind === "area" ? -10 : 2,
      draggable: false, selectable: false, data: { text: a.text, color: a.color, onChange: noop } })),
    ...lab.nodes.map((n) => ({ id: n.id, type: "device", position: { x: n.x, y: n.y }, zIndex: 3, selected: sel === n.id, draggable: false,
      data: { label: n.id, type: n.type, state: "off" } })),
  ], [lab, sel]);
  const edges = useMemo(() => !lab ? [] : lab.links.map((l) => ({ id: l.id, source: l.a, target: l.b, type: "cable",
    data: { media: l.media, aIf: labIf(lab, l.a, l.a_if), bIf: labIf(lab, l.b, l.b_if), showIf: true } })), [lab, hw]); // eslint-disable-line
  if (err) return <div className="login"><div className="login-card"><h3>Patchyard</h3><p className="error">{err}</p><a href="/">Go to Patchyard</a></div></div>;
  if (!lab) return <div className="login"><p className="hint">Loading…</p></div>;
  const node = lab.nodes.find((n) => n.id === sel);
  const I = node ? ICONS[node.type] || ICONS.pc : null;
  const ch = node ? chassisOf(node) : null;
  return (
    <div className="showcase">
      <header className="show-head">
        <a className="brand" href="/"><svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="3" /><circle cx="19" cy="5" r="3" /><circle cx="19" cy="19" r="3" /><path d="M7.6 10.6l8.8-4.2M7.6 13.4l8.8 4.2" /></svg>Patchyard</a>
        <h1>{lab.name}</h1>
        <span className="hint">{lab.owner ? `by ${lab.owner} · ` : ""}{lab.nodes.length} devices · {lab.links.length} cables · read-only</span>
      </header>
      <div className="show-body">
        <div className="show-canvas">
          <ReactFlowProvider>
            <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} fitView minZoom={0.15} maxZoom={2.5}
              nodesConnectable={false} nodesDraggable={false} elementsSelectable onNodeClick={(_, n) => n.type === "device" && setSel(n.id)} onPaneClick={() => setSel(null)}
              proOptions={{ hideAttribution: true }}>
              <Background gap={24} size={1.2} /><Controls showInteractive={false} />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
        <aside className="show-side">
          {!node && <>
            {lab.challenge?.title && <><h2>{lab.challenge.title}</h2><Rich text={lab.challenge.instructions || ""} /></>}
            <p className="hint">Click a device to see what it is{Object.keys(lab.configs).length ? " and its configuration" : ""}.</p>
            <ul className="show-list">{lab.nodes.map((n) => <li key={n.id}><button className="link" onClick={() => setSel(n.id)}>{n.id}</button> <span className="hint">{TYPE_LABEL[n.type]}</span></li>)}</ul>
          </>}
          {node && <>
            <h2 className="row nowrap">{I && <I size={28} />} {node.id}</h2>
            <p className="hint">{TYPE_LABEL[node.type]}{ch ? ` · ${ch.label}` : ""}{node.props.asn ? ` · AS ${node.props.asn}` : ""}{node.props.ip ? ` · ${node.props.ip}` : ""}</p>
            <h4>Cables</h4>
            <ul className="show-list">{lab.links.filter((l) => l.a === node.id || l.b === node.id).map((l) => {
              const mine = l.a === node.id;
              return <li key={l.id} className="mono small">{labIf(lab, node.id, mine ? l.a_if : l.b_if)} → {mine ? l.b : l.a} {labIf(lab, mine ? l.b : l.a, mine ? l.b_if : l.a_if)}</li>;
            })}</ul>
            {lab.configs[node.id] && <><h4>Configuration</h4><pre className="eq show-cfg">{lab.configs[node.id]}</pre></>}
            <button className="link" onClick={() => setSel(null)}>← All devices</button>
          </>}
          <p className="hint show-foot">Built with Patchyard, a network lab in the browser. <a href="/">Sign in or try it</a>.</p>
        </aside>
      </div>
    </div>
  );
}
