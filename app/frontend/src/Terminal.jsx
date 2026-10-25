import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { wsUrl } from "./api.js";

export default function Terminal({ lab, node, mode }) {
  const ref = useRef(null);
  useEffect(() => {
    const term = new XTerm({
      fontFamily: '"IBM Plex Mono", ui-monospace, monospace', fontSize: 13, cursorBlink: true,
      theme: { background: "#14202B", foreground: "#DCE6EE", cursor: "#F2C14E" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(ref.current);
    const ws = new WebSocket(wsUrl(`/ws/console/${lab}/${node}?mode=${mode}`));
    const send = (o) => ws.readyState === 1 && ws.send(JSON.stringify(o));
    const resize = () => { try { fit.fit(); send({ t: "r", cols: term.cols, rows: term.rows }); } catch {} };
    ws.onopen = () => { resize(); term.focus(); };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = () => term.write("\r\n\x1b[33m[disconnected]\x1b[0m\r\n");
    const d = term.onData((s) => send({ t: "i", d: s }));
    const ro = new ResizeObserver(resize);
    ro.observe(ref.current);
    return () => { ro.disconnect(); d.dispose(); ws.close(); term.dispose(); };
  }, [lab, node, mode]);
  return <div className="term" ref={ref} />;
}
