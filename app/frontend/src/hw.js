// Device hardware: mirrors app/backend/app/hardware.py (the catalogue comes from /api/hardware).
let CAT = null;
export const setCatalogue = (c) => { CAT = c; };
export const catalogue = () => CAT;

export function chassisOf(node) {
  if (!CAT || !node || node.raw) return null;
  return CAT.chassis[node.props?.chassis] || null;
}

const port = (ifn, type, name, slot = null, module = null) => {
  const t = CAT.port_types[type];
  return { if: ifn, name: t.short + name, long: t.long + name, type, media: t.media, kbit: t.kbit, slot, module, label: t.label };
};

// All physical ports of a chassis device (cabled or not); null for generic devices.
export function portsOf(node) {
  const ch = chassisOf(node);
  if (!ch) return null;
  const out = [];
  let i = 0;
  for (const [type, prefix, first, count] of ch.onboard) {
    for (let n = 0; n < count; n++) { i++; out.push(port(`eth${i}`, type, `${prefix}${first + n}`)); }
  }
  const mods = node.props?.modules || {};
  ch.slots.forEach((slot, k) => {
    const m = CAT.modules[mods[slot.id]];
    if (!m) return;
    let p = 0;
    for (const [type, count] of m.ports) {
      for (let c = 0; c < count; c++) {
        out.push(port(`eth${100 * (k + 1) + p + 1}`, type, `${slot.id}/${(m.family === "router" ? 0 : 1) + p}`, slot.id, mods[slot.id]));
        p++;
      }
    }
  });
  return out;
}

export function ifLabel(node, ifn) {
  if (!ifn) return "";
  const [base, sub] = ifn.split(".");
  if (base === "lo") return "Lo0";
  const m = /^loop(\d+)$/.exec(base);
  if (m) return `Lo${m[1]}`;
  const p = (portsOf(node) || []).find((x) => x.if === base);
  return (p ? p.name : base) + (sub ? `.${sub}` : "");
}

export const nodeById = (lab, id) => lab?.nodes.find((n) => n.id === id);
export const labIf = (lab, id, ifn) => ifLabel(nodeById(lab, id), ifn);

export function usedPorts(lab, id) {
  return new Set(lab.links.flatMap((k) => [k.a === id && k.a_if, k.b === id && k.b_if]).filter(Boolean));
}

export function fmtSpeed(kbit) {
  if (!kbit) return "10G";
  if (kbit >= 1_000_000) return `${kbit / 1_000_000}G`;
  if (kbit >= 1000) return `${kbit / 1000}M`;
  return `${kbit}k`;
}

export function defaultHardware(type, settings) {
  if (!CAT || !["router", "switch"].includes(type)) return {};
  const id = settings?.lab_defaults?.[`${type}_model`] ?? CAT.defaults[type];
  const ch = CAT.chassis[id];
  if (!ch) return {};
  return { chassis: id, ...(ch.default_modules ? { modules: { ...ch.default_modules } } : {}) };
}
