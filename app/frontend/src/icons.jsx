// Original generic network-diagram icons (not Cisco artwork).
const S = { fill: "var(--icon-body)", stroke: "var(--icon-line)", strokeWidth: 2 };
const A = { fill: "none", stroke: "var(--icon-mark)", strokeWidth: 2.4, strokeLinecap: "round", strokeLinejoin: "round" };

export function RouterIcon({ size = 56 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <ellipse cx="32" cy="40" rx="26" ry="10" {...S} />
      <path d="M6 30v10M58 30v10" stroke="var(--icon-line)" strokeWidth="2" />
      <ellipse cx="32" cy="30" rx="26" ry="10" {...S} />
      <path {...A} d="M20 30h9m-3-3 3 3-3 3M44 30h-9m3-3-3 3 3 3M32 22v5m-3-2 3-3 3 3M32 38v-5m-3 2 3 3 3-3" />
    </svg>
  );
}
export function SwitchIcon({ size = 56 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <path d="M8 26l8-8h44l-8 8z" {...S} />
      <rect x="8" y="26" width="44" height="18" {...S} />
      <path d="M52 26l8-8v18l-8 8z" {...S} />
      <path {...A} d="M14 31h14m-3-3 3 3-3 3M44 39H30m3-3-3 3 3 3" />
    </svg>
  );
}
export function PcIcon({ size = 56 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect x="10" y="12" width="44" height="30" rx="2" {...S} />
      <rect x="15" y="17" width="34" height="20" fill="var(--icon-screen)" />
      <path d="M26 42l-3 8h18l-3-8" {...S} />
      <path d="M18 52h28" stroke="var(--icon-line)" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}
export function ServerIcon({ size = 56 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect x="18" y="6" width="28" height="52" rx="2" {...S} />
      <path d="M22 16h20M22 22h20M22 28h20" stroke="var(--icon-line)" strokeWidth="2" />
      <circle cx="26" cy="48" r="2.2" fill="var(--ok)" />
      <circle cx="33" cy="48" r="2.2" fill="var(--icon-mark)" />
    </svg>
  );
}
export function CableIcon({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M4 26C10 26 8 6 16 6s6 20 12 20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="4" cy="26" r="2.6" fill="currentColor" />
      <circle cx="28" cy="26" r="2.6" fill="currentColor" />
    </svg>
  );
}
export const ICONS = { router: RouterIcon, switch: SwitchIcon, pc: PcIcon, server: ServerIcon };
export const TYPE_LABEL = { router: "Router", switch: "Switch", pc: "PC", server: "Server" };
export const PREFIX = { router: "r", switch: "sw", pc: "pc", server: "srv" };
