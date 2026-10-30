async function req(method, url, body) {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (r.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new Event("netlab-auth"));
  if (!r.ok) { const e = new Error(data.detail || `${r.status} ${r.statusText}`); e.status = r.status; throw e; }
  return data;
}
const L = (lab) => `/api/labs/${lab}`;
const N = (lab, node) => `${L(lab)}/nodes/${node}`;
export const api = {
  labs: () => req("GET", "/api/labs"),
  create: (name) => req("POST", "/api/labs", { name }),
  importClab: (path) => req("POST", "/api/labs/import", { path }),
  get: (lab) => req("GET", L(lab)),
  save: (lab, rev) => req("PUT", L(lab.name), rev != null ? { ...lab, _rev: rev } : lab),
  remove: (lab) => req("DELETE", L(lab)),
  deploy: (lab) => req("POST", `${L(lab)}/deploy`),
  destroy: (lab) => req("POST", `${L(lab)}/destroy`),
  status: (lab) => req("GET", `${L(lab)}/status`),
  saveConfigs: (lab) => req("POST", `${L(lab)}/save-configs`),
  pause: (lab) => req("POST", `${L(lab)}/pause`),
  resume: (lab) => req("POST", `${L(lab)}/resume`),
  ifaces: (lab, n) => req("GET", `${N(lab, n)}/interfaces`),
  running: (lab, n) => req("GET", `${N(lab, n)}/running-config`),
  config: (lab, n, commands) => req("POST", `${N(lab, n)}/config`, { commands }),
  ipconfig: (lab, n, body) => req("POST", `${N(lab, n)}/ipconfig`, body),
  services: (lab, n, body) => req("POST", `${N(lab, n)}/services`, body),
  switchApply: (lab, n, body) => req("POST", `${N(lab, n)}/switch`, body),
  switchShow: (lab, n) => req("GET", `${N(lab, n)}/switch`),
  subif: (lab, n, parent, vlan, action) => req("POST", `${N(lab, n)}/subif`, { parent, vlan, action }),
  browse: (lab, n, url) => req("POST", `${N(lab, n)}/browse`, { url }),
  simStart: (lab) => req("POST", `${L(lab)}/sim/start`),
  simStop: (lab) => req("POST", `${L(lab)}/sim/stop`),
  simClear: (lab) => req("POST", `${L(lab)}/sim/clear`),
  simEvents: (lab, after) => req("GET", `${L(lab)}/sim/events?after=${after}`),
  simEvent: (lab, id) => req("GET", `${L(lab)}/sim/events/${id}`),
  simPdu: (lab, src, dst) => req("POST", `${L(lab)}/sim/pdu`, { src, dst }),
  // monitoring
  live: (lab) => req("GET", `${L(lab)}/metrics/live`),
  history: (lab, node, iface, range) => req("GET", `${L(lab)}/metrics/history?node=${encodeURIComponent(node)}${iface ? `&iface=${encodeURIComponent(iface)}` : ""}&range=${range}`),
  protocols: (lab, n, which = "") => req("GET", `${N(lab, n)}/protocols${which ? `?which=${which}` : ""}`),
  health: (lab) => req("GET", `${L(lab)}/health`),
  // links
  presets: () => req("GET", "/api/impair/presets"),
  impair: (lab, id, impair) => req("POST", `${L(lab)}/links/${id}/impair`, { impair }),
  linkState: (lab, id, up) => req("POST", `${L(lab)}/links/${id}/state`, { up }),
  flap: (lab, id, seconds) => req("POST", `${L(lab)}/links/${id}/flap`, { seconds }),
  tc: (lab, id) => req("GET", `${L(lab)}/links/${id}/tc`),
  // lab management
  templates: () => req("GET", "/api/templates"),
  fromTemplate: (tid, name) => req("POST", `/api/templates/${tid}`, { name }),
  snapshots: (lab) => req("GET", `${L(lab)}/snapshots`),
  snapshot: (lab, label) => req("POST", `${L(lab)}/snapshots`, { label }),
  snapDiff: (lab, sid, against = "") => req("GET", `${L(lab)}/snapshots/${sid}/diff${against ? `?against=${against}` : ""}`),
  snapRestore: (lab, sid) => req("POST", `${L(lab)}/snapshots/${sid}/restore`),
  snapDelete: (lab, sid) => req("DELETE", `${L(lab)}/snapshots/${sid}`),
  clone: (lab, name) => req("POST", `${L(lab)}/clone`, { name }),
  exportUrl: (lab) => `${L(lab)}/export`,
  // platform
  version: () => req("GET", "/api/version"),
  settings: () => req("GET", "/api/settings"),
  saveSettings: (s) => req("PUT", "/api/settings", s),
  authState: () => req("GET", "/api/auth/state"),
  login: (username, password) => req("POST", "/api/auth/login", { username, password }),
  inviteCheck: (t) => req("GET", `/api/invite/${encodeURIComponent(t)}`),
  inviteRedeem: (t, body) => req("POST", `/api/invite/${encodeURIComponent(t)}`, body),
  adminUsers: () => req("GET", "/api/admin/users"),
  adminUserCreate: (body) => req("POST", "/api/admin/users", body),
  adminUserPatch: (n, body) => req("PATCH", `/api/admin/users/${n}`, body),
  adminUserDelete: (n, labs) => req("DELETE", `/api/admin/users/${n}${labs ? "?delete_labs=true" : ""}`),
  forgot: (login) => req("POST", "/api/auth/forgot", { login }),
  resetCheck: (t) => req("GET", `/api/auth/reset/${encodeURIComponent(t)}`),
  resetDo: (t, password) => req("POST", `/api/auth/reset/${encodeURIComponent(t)}`, { password }),
  adminEmail: () => req("GET", "/api/admin/email"),
  adminEmailTest: (to) => req("POST", "/api/admin/email/test", { to }),
  adminAudit: (who = "", q = "") => req("GET", `/api/admin/audit?who=${encodeURIComponent(who)}&q=${encodeURIComponent(q)}`),
  adminSendReset: (n) => req("POST", `/api/admin/users/${n}/send-reset`),
  challenges: () => req("GET", "/api/challenges"),
  challengeStart: (kind, id, name) => req("POST", `/api/challenges/${kind}/${id}/start`, { name }),
  challengeCheck: (lab) => req("POST", `${L(lab)}/challenge/check`),
  challengeResult: (lab) => req("GET", `${L(lab)}/challenge/result`),
  adminChallengePublish: (body) => req("POST", "/api/admin/challenges", body),
  adminChallengeUnpublish: (id) => req("DELETE", `/api/admin/challenges/${id}`),
  accountRequest: (body) => req("POST", "/api/auth/request", body),
  profile: (body) => req("PATCH", "/api/auth/profile", body),
  adminRequests: () => req("GET", "/api/admin/requests"),
  adminRequestApprove: (id, body = {}) => req("POST", `/api/admin/requests/${id}/approve`, body),
  adminRequestDecline: (id) => req("POST", `/api/admin/requests/${id}/decline`),
  adminInvites: () => req("GET", "/api/admin/invites"),
  adminInviteCreate: (body) => req("POST", "/api/admin/invites", body),
  adminInviteDelete: (id) => req("DELETE", `/api/admin/invites/${id}`),
  adminLabOwner: (lab, body) => req("POST", `/api/admin/labs/${lab}/owner`, body),
  logout: () => req("POST", "/api/auth/logout"),
  setPassword: (body) => req("POST", "/api/auth/password", body),
  alerts: (state = "") => req("GET", `/api/alerts${state ? `?state=${state}` : ""}`),
  alertSummary: (after) => req("GET", `/api/alerts/summary?after=${after}`),
  ack: (id) => req("POST", "/api/alerts/ack", id == null ? {} : { id }),
  clearResolved: () => req("DELETE", "/api/alerts/resolved"),
  hardware: () => req("GET", "/api/hardware"),
  system: () => req("GET", "/api/system"),
  updateCheck: (force) => req("GET", `/api/system/update${force ? "?force=true" : ""}`),
  nodeState: (lab, n) => req("GET", `${N(lab, n)}/state`),
  ios: (lab, n, commands) => req("POST", `${N(lab, n)}/ios`, { commands }),
  firewall: (lab, n, body) => req("POST", `${N(lab, n)}/firewall`, body),
  firewallStatus: (lab, n) => req("GET", `${N(lab, n)}/firewall`),
  tftpFiles: (lab, n) => req("GET", `${N(lab, n)}/server/tftp`),
  tftpUrl: (lab, n, f) => `${N(lab, n)}/server/tftp/${encodeURIComponent(f)}`,
  serverFiles: (lab, n, kind) => req("GET", `${N(lab, n)}/server/${kind}`),
  serverFileUrl: (lab, n, kind, f) => `${N(lab, n)}/server/${kind}/${encodeURIComponent(f)}`,
  serverFileDel: (lab, n, kind, f) => req("DELETE", `${N(lab, n)}/server/${kind}/${encodeURIComponent(f)}`),
  serverFilePut: async (lab, n, kind, file) => {
    const r = await fetch(`${N(lab, n)}/server/${kind}/${encodeURIComponent(file.name)}`, { method: "PUT", body: file });
    if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).detail || m; } catch { /* not json */ } throw new Error(m); }
    return r.json();
  },
  serverStatus: (lab, n) => req("GET", `${N(lab, n)}/server-status`),
  syslog: (lab, n) => req("GET", `${N(lab, n)}/server/syslog`),
  syslogClear: (lab, n) => req("DELETE", `${N(lab, n)}/server/syslog`),
  renameFiles: (lab, n, to) => req("POST", `${N(lab, n)}/rename-files`, { to }),
  reachability: (lab) => req("POST", `${L(lab)}/reachability`),
  power: (lab, n, on) => req("POST", `${N(lab, n)}/power`, { on }),
  liveNode: (lab, n) => req("POST", `${L(lab)}/live/nodes/${n}`),
  liveNodeDel: (lab, n) => req("DELETE", `${L(lab)}/live/nodes/${n}`),
  liveLink: (lab, id) => req("POST", `${L(lab)}/live/links/${id}`),
  liveUnlink: (lab, l) => req("POST", `${L(lab)}/live/unlink`, { a: l.a, a_if: l.a_if, b: l.b, b_if: l.b_if }),
  traffic: (lab) => req("GET", `${L(lab)}/traffic`),
  trafficStart: (lab, body) => req("POST", `${L(lab)}/traffic`, body),
  trafficStop: (lab, id) => req("POST", `${L(lab)}/traffic/${id}/stop`),
  importZip: async (name, file) => {
    const r = await fetch(`/api/labs/import-zip?name=${encodeURIComponent(name)}`, { method: "POST", body: file, headers: { "Content-Type": "application/zip" } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `${r.status} ${r.statusText}`);
    return d;
  },
};
export function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}
