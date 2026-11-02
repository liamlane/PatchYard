const { chromium } = require('playwright');
// Retakes the documentation screenshots in docs/images/.
// Needs a fresh dev server with login on and an admin "biscuit" / "admin-pass-123", e.g.
//   PATCHYARD_HOME=/tmp/py PATCHYARD_DATA=/tmp/py/labs uvicorn app.main:app --port 8097   (from app/backend)
// Run:  node tools/docs-screenshots.js [shot,shot,...]     (Playwright with Chromium installed)
// Lab containers aren't needed: running state and live results for the internet-edge lab are sample data,
// served by request interception below. Keep that data consistent with the lab's addressing.
const path = require('path');
const OUT = path.join(__dirname, '..', 'docs', 'images') + '/';
const B = process.env.PATCHYARD_URL || 'http://localhost:8097';
const only = process.argv[2] ? process.argv[2].split(',') : null;
const want = (k) => !only || only.includes(k);
(async () => {
  const b = await chromium.launch(); const errs = [];
  const ctx = await b.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  await ctx.addInitScript(() => { try { localStorage.setItem('netlab.theme', '"dark"'); } catch {} });
  const p = await ctx.newPage(); p.on('pageerror', e => errs.push(e.message));
  const r = async (m, u, d) => { const x = await p.request.fetch(B + u, { method: m, data: d }); if (x.status() >= 400) console.log('API', m, u, x.status(), (await x.text()).slice(0, 200)); try { return await x.json(); } catch { return null; } };
  const hideToasts = () => p.addStyleTag({ content: '.toasts{display:none!important}' });
  const login = async () => { await p.goto(B + '/'); await p.waitForTimeout(600); await p.locator('input[autocomplete=username]').fill('biscuit'); await p.locator('input[type=password]').fill('admin-pass-123'); await p.getByRole('button', { name: 'Sign in' }).click(); await p.waitForTimeout(1800); await hideToasts(); };
  await login();
  await r('PATCH', '/api/auth/profile', { email: 'biscuit@example.com', full_name: 'Liam Lane' });
  // ---------- data ----------
  const HW = await r('GET', '/api/hardware');
  const dm = (id) => ({ chassis: id, ...(HW.chassis[id].default_modules ? { modules: { ...HW.chassis[id].default_modules } } : {}) });
  for (const [t, n] of [['isp-mpls', 'dual-isp-mpls'], ['small-office', 'small-office'], ['serial-wan', 'serial-wan'], ['nat-acl-edge', 'nat-acl-edge'], ['campus-rstp-lacp', 'campus-lacp'], ['firewall-edge', 'firewall-edge']])
    await r('POST', '/api/templates/' + t, { name: n });
  await r('POST', '/api/challenges/builtin/ospf-finish/start', { name: 'ospf-practice' });
  // the 0.15 showcase lab: an office dual-homed to two ISPs, a broadband customer, and the internet core
  await r('POST', '/api/labs', { name: 'internet-edge' });
  const lab = await r('GET', '/api/labs/internet-edge');
  lab.nodes = [
    { id: 'inet1', type: 'internet', x: 560, y: -40, props: { asn: '64496', pool: '203.0.113.0/24', sites: [{ name: 'shop.example.org', ip: '198.18.2.10', title: 'shop.example.org', text: 'Welcome to the shop' }] } },
    { id: 'isp1', type: 'isp', x: 330, y: 130, props: { asn: '64501', pool: '81.10.0.0/16', ports: { eth2: { mode: 'bgp', customer_as: '65010' } } } },
    { id: 'isp2', type: 'isp', x: 790, y: 130, props: { asn: '64502', pool: '81.20.0.0/16', ports: { eth2: { mode: 'bgp', customer_as: '65010' }, eth3: { mode: 'dhcp' } } } },
    { id: 'edge1', type: 'router', x: 560, y: 300, props: { chassis: 'router-2s', modules: { '0/1': 'nim-4ge' } } },
    { id: 'sw1', type: 'switch', x: 560, y: 470, props: dm('switch-24') },
    { id: 'pc1', type: 'pc', x: 380, y: 620, props: { ...dm('pc'), ip_mode: 'static', ip: '192.168.10.10/24', gateway: '192.168.10.1' } },
    { id: 'pc2', type: 'pc', x: 560, y: 630, props: { ...dm('pc'), ip_mode: 'static', ip: '192.168.10.11/24', gateway: '192.168.10.1' } },
    { id: 'srv1', type: 'server', x: 740, y: 620, props: { ...dm('server'), ip_mode: 'static', ip: '192.168.10.20/24', gateway: '192.168.10.1', services: { http: { enabled: true } } } },
    { id: 'home1', type: 'pc', x: 1010, y: 300, props: { ...dm('pc'), ip_mode: 'dhcp' } },
  ];
  lab.links = [
    { id: 'l1', a: 'isp1', a_if: 'eth1', b: 'inet1', b_if: 'eth1' }, { id: 'l2', a: 'isp2', a_if: 'eth1', b: 'inet1', b_if: 'eth2' },
    { id: 'l3', a: 'edge1', a_if: 'eth1', b: 'isp1', b_if: 'eth2' }, { id: 'l4', a: 'edge1', a_if: 'eth2', b: 'isp2', b_if: 'eth2' },
    { id: 'l5', a: 'edge1', a_if: 'eth101', b: 'sw1', b_if: 'eth1' }, { id: 'l6', a: 'sw1', a_if: 'eth2', b: 'pc1', b_if: 'eth1' },
    { id: 'l7', a: 'sw1', a_if: 'eth3', b: 'pc2', b_if: 'eth1' }, { id: 'l8', a: 'sw1', a_if: 'eth4', b: 'srv1', b_if: 'eth1' },
    { id: 'l9', a: 'isp2', a_if: 'eth3', b: 'home1', b_if: 'eth1' }];
  lab.annotations = [
    { id: 'ann-office', kind: 'area', x: 330, y: 260, w: 560, h: 450, text: 'Office · AS65010 · 192.168.10.0/24', color: 'green' },
    { id: 'ann-note', kind: 'note', x: 40, y: -40, w: 250, h: 120, color: 'yellow', text: 'edge1 is dual-homed with BGP to isp1 and isp2. home1 is a broadband customer on DHCP. Try: Explain a path from pc1 to www.example.com.' }];
  await r('PUT', '/api/labs/internet-edge', lab);
  // ---------- a "running" view: the lab isn't deployed in the doc environment, so live data is sample data ----------
  const RUN = Object.fromEntries(lab.nodes.map((n) => [n.id, 'running']));
  const IF = Object.fromEntries(lab.nodes.map((n) => [n.id, Object.fromEntries([1, 2, 3, 4].map((i) => [`eth${i}`, 'up']))]));
  await p.route('**/api/labs/internet-edge/status', async (route) => { const x = await route.fetch(); const j = await x.json(); j.nodes = RUN; j.ifaces = IF; await route.fulfill({ json: j }); });
  const hop = (node, type, verdict, extra) => ({ node, type, verdict, in: null, out: null, route: null, protocol: null, via: null, notes: [], l2: [], ...extra });
  await p.route('**/api/labs/internet-edge/explain', (route) => route.fulfill({ json: { ok: false,
    forward: { src: '192.168.10.10', dst: '198.18.1.10', proto: 'tcp', dport: 80, result: 'delivered', reason: 'reached inet1 (www.example.com)', hops: [
      hop('pc1', 'pc', 'send', { out: 'eth1', route: '0.0.0.0/0', via: '192.168.10.1' }),
      hop('edge1', 'router', 'forward', { in: 'Gi0/1/0', out: 'Gi0/0/0', route: '0.0.0.0/0', protocol: 'bgp', via: '81.10.0.17', l2: [{ node: 'sw1', in: 'Gi1/0/2', in_mode: 'access 1', out: 'Gi1/0/1', out_mode: 'access 1' }], notes: ['NAT: ip nat inside source list 1 interface Gi0/0/0 overload: source becomes 81.10.0.18'] }),
      hop('isp1', 'isp', 'forward', { in: 'eth2', out: 'eth1', route: '198.18.0.0/15', protocol: 'bgp', via: '100.100.0.1' }),
      hop('inet1', 'internet', 'deliver', { in: 'eth1', notes: ['198.18.1.10 is www.example.com (nginx on this node)'] })] },
    reply: { src: '198.18.1.10', dst: '81.10.0.18', proto: 'tcp', dport: null, result: 'dropped', reason: 'edge1 drops it', hops: [
      hop('inet1', 'internet', 'send', { out: 'eth1', route: '81.10.0.0/16', protocol: 'bgp', via: '100.100.0.2' }),
      hop('isp1', 'isp', 'forward', { in: 'eth1', out: 'eth2', route: '81.10.0.16/30', protocol: 'connected', via: 'directly connected' }),
      hop('edge1', 'router', 'drop', { in: 'Gi0/0/0', notes: ['ACL OUTSIDE-IN applied inbound on Gi0/0/0: line 30 "deny ip any any log" matched (no "permit tcp any any established" above it)'] })] } } }));
  const now = Date.now() / 1000;
  const TICKET = { id: 4127, created: now - 7 * 60, level: 'normal', symptom: 'The user at pc2: “I can\'t reach srv1 (192.168.10.20) since this morning. It was fine yesterday.”', src: 'pc2', target: 'srv1', solved: false, gave_up: false, hints_used: 1, checks_run: 1,
    hints: ['Start with the path explainer from the user\'s device: it shows where the packet stops.'], hints_left: 1 };
  await p.route('**/api/labs/internet-edge/ticket', (route) => route.fulfill({ json: TICKET }));
  await p.route('**/api/labs/internet-edge/ticket/check', (route) => route.fulfill({ json: { ticket: TICKET, results: [{ label: 'pc2 can reach srv1 again', ok: false, detail: '100% packet loss' }] } }));
  const FO = [{ id: 'a1b2c3d4', lab: 'internet-edge', link: 'l3', state: 'done', step: 'finished', notes: [], started: now - 200, label: 'edge1 Gi0/0/0 ↔ isp1 eth2', result: {
    all_recovered: true, all_restored: true, down_at: now - 180, up_at: now - 120,
    flows: [{ src: 'pc1', dst: 'srv1', ip: '192.168.10.20', on_failure: { recovered: true, outage: 0, lost: 0 }, on_restore: { recovered: true, outage: 0, lost: 0 } },
      { src: 'pc1', dst: 'home1', ip: '81.20.0.26', on_failure: { recovered: true, outage: 2.8, lost: 14 }, on_restore: { recovered: true, outage: 0.4, lost: 2 } },
      { src: 'pc2', dst: 'home1', ip: '81.20.0.26', on_failure: { recovered: true, outage: 3.0, lost: 15 }, on_restore: { recovered: true, outage: 0.4, lost: 2 } }],
    routes_on_failure: [{ router: 'edge1', lost: [], new: [], moved: [{ prefix: '0.0.0.0/0', from: 'bgp 81.10.0.17', to: 'bgp 81.20.0.17' }] }, { router: 'isp1', lost: ['81.10.0.16/30'], new: [], moved: [{ prefix: '192.168.10.0/24', from: 'bgp 81.10.0.18', to: 'bgp 100.100.0.1' }] }],
    routes_on_restore: [{ router: 'edge1', lost: [], new: [], moved: [{ prefix: '0.0.0.0/0', from: 'bgp 81.20.0.17', to: 'bgp 81.10.0.17' }] }] } }];
  await p.route('**/api/labs/internet-edge/failover', (route) => route.fulfill({ json: route.request().method() === 'GET' ? FO : FO[0] }));
  await p.route('**/api/labs/internet-edge/baseline', (route) => route.fulfill({ json: { ts: now - 1500, summary: { routers: 4, routes: 38, reachable: 20, pairs: 20 } } }));
  await p.route('**/api/labs/internet-edge/baseline/compare', (route) => route.fulfill({ json: { baseline: { ts: now - 1500 }, now: { ts: now },
    routes: [{ router: 'edge1', prefix: '0.0.0.0/0', vrf: 'default', change: 'moved', before: 'bgp via 81.10.0.17', after: 'bgp via 81.20.0.17' },
      { router: 'isp1', prefix: '192.168.10.0/24', vrf: 'default', change: 'moved', before: 'bgp via 81.10.0.18', after: 'bgp via 100.100.0.1' },
      { router: 'isp2', prefix: '192.168.10.0/24', vrf: 'default', change: 'gained', before: null, after: 'bgp via 81.20.0.18' },
      { router: 'edge1', prefix: '10.99.0.0/24', vrf: 'default', change: 'lost', before: 'static via 192.168.10.254', after: null }],
    neighbours: [{ router: 'edge1', neighbour: 'BGP 81.10.0.17', before: 'Established', after: 'Idle' }, { router: 'isp1', neighbour: 'BGP 81.10.0.18', before: 'Established', after: 'Active' }],
    reach: [], broken: [], fixed: [] } }));
  await p.route('**/api/labs/internet-edge/traffic', (route) => route.fulfill({ json: [
    { id: 'v1', src: 'pc1', dst: 'home1', target: '81.20.0.26', proto: 'udp', profile: 'voip', duration: 30, elapsed: 30, streams: 1, rate_mbps: 0.0872, reverse: false, state: 'done',
      result: { bps: 87150, jitter_ms: 3.2, lost_percent: 0.4, rtt_ms: 31.5, r_factor: 91.4, mos: 4.37, grade: 'excellent' } },
    { id: 'v0', src: 'pc1', dst: 'home1', target: '81.20.0.26', proto: 'udp', profile: 'voip', duration: 30, elapsed: 30, streams: 1, rate_mbps: 0.0872, reverse: false, state: 'done',
      result: { bps: 86400, jitter_ms: 38.0, lost_percent: 4.8, rtt_ms: 612.0, r_factor: 54.0, mos: 2.79, grade: 'bad' } }] }));
  await p.reload(); await p.waitForTimeout(1500); await hideToasts();

  const open = async (name) => { await p.locator('.top select').first().selectOption(name); await p.waitForTimeout(1500); await p.keyboard.press('Escape'); };
  const fit = async () => { await p.locator('.react-flow__controls-fitview').click().catch(() => {}); await p.waitForTimeout(900); };
  const closeWins = async () => { while (await p.locator('.win-x').count()) { await p.locator('.win-x').first().click(); await p.waitForTimeout(100); } };
  const win = async (node, tab) => { await closeWins(); await p.locator('.react-flow__node', { hasText: new RegExp('^\\s*' + node + '\\s*$') }).first().dblclick(); await p.waitForTimeout(900); if (tab) { await p.locator('.win [role=tab]', { hasText: tab }).first().click(); await p.waitForTimeout(700); } return p.locator('.win').last(); };
  const nav = async (w, text) => { await w.locator('button, a, li', { hasText: text }).first().click(); await p.waitForTimeout(600); };
  const side = async (title) => { await p.locator(`button[aria-label^="${title}"]`).first().click(); await p.waitForTimeout(700); };
  const closeSide = async () => { await p.locator('.dash [aria-label^="Close"]').first().click().catch(() => {}); await p.waitForTimeout(300); };
  const panelShot = async (sel, file) => { const el = p.locator(sel).first(); const bb = await el.boundingBox();
    const bottom = await el.evaluate((e) => { let m = 0; for (const c of e.querySelectorAll(':scope > *')) { const r = c.getBoundingClientRect(); if (r.height) m = Math.max(m, r.bottom); } return m; });
    await p.screenshot({ path: file, clip: { x: bb.x, y: bb.y, width: bb.width, height: Math.min(bb.height, bottom - bb.y + 8) } }); };
  const step = async (k, fn) => { if (!want(k)) return; try { await fn(); console.log('ok', k); } catch (e) { console.log('FAIL', k, e.message.split('\n')[0]); } await closeWins(); await p.keyboard.press('Escape'); await closeSide(); };

  // ---------- 0.15 showcase ----------
  await open('internet-edge'); await fit();
  await step('explain', async () => {
    await side('Explain a path');
    await p.locator('.explain select').first().selectOption('pc1');
    await p.locator('.explain input').first().fill('www.example.com');
    await p.locator('.explain .seg button', { hasText: 'TCP' }).click(); await p.locator('.explain input').nth(1).fill('80');
    await p.locator('.explain button.primary').click(); await p.waitForTimeout(800);
    await fit(); await p.locator('.explain').evaluate((e) => e.scrollTop = 175);
    await p.screenshot({ path: OUT + 'screen-explain.png' });
  });
  await step('isp', async () => { const w = await win('isp2', 'Config'); await p.waitForTimeout(1800); await w.getByRole('button', { name: /Show the IOS commands/ }).first().click(); await p.waitForTimeout(200); await w.screenshot({ path: OUT + 'screen-isp.png' }); });
  await step('internet', async () => { const w = await win('inet1', 'Config'); await p.waitForTimeout(1800); await w.locator('.win-body').evaluate((e) => e.scrollTop = 120); await w.screenshot({ path: OUT + 'screen-internet.png' }); });
  await step('ticket', async () => { await side('Troubleshooting'); await p.getByRole('button', { name: /^Check$/ }).click(); await p.waitForTimeout(600); await panelShot('.tickets', OUT + 'screen-ticket.png'); });
  await step('failover', async () => { await p.locator('.react-flow__edge[data-id="l3"]').first().click({ force: true }); await p.waitForTimeout(1500);
    const h = p.getByText('Failover test', { exact: true }).first(); await h.scrollIntoViewIfNeeded(); await p.waitForTimeout(400);
    const sec = h.locator('xpath=..'); await sec.evaluate((e) => e.querySelectorAll('details').forEach((d) => d.open = true)); await p.waitForTimeout(200); await sec.screenshot({ path: OUT + 'screen-failover.png' }); });
  await step('rehearsal', async () => { await p.locator('button[aria-label^="Dashboard"]').first().click(); await p.waitForTimeout(900);
    await p.getByRole('button', { name: 'Compare with now' }).click(); await p.waitForTimeout(800);
    await p.locator('.baseline').scrollIntoViewIfNeeded(); await p.locator('.baseline').screenshot({ path: OUT + 'screen-rehearsal.png' });
    await p.locator('[aria-label="Close dashboard"]').click(); });
  await step('voip', async () => { await side('Traffic generator'); await p.locator('.traffic .seg button', { hasText: 'VoIP call' }).click(); await p.locator('.traffic select').first().selectOption('pc1'); await p.locator('.traffic select').nth(1).selectOption('home1'); await p.waitForTimeout(2500); await panelShot('.traffic', OUT + 'screen-voip.png'); });
  const hero = async (name, file) => {
    const c2 = await b.newContext({ viewport: { width: 1200, height: 705 }, deviceScaleFactor: 2, storageState: await ctx.storageState() });
    await c2.addInitScript((n) => { try { localStorage.setItem('netlab.theme', '"dark"'); localStorage.setItem('netlab.last', JSON.stringify(n)); } catch {} }, name);
    await c2.route('**/api/labs/internet-edge/status', async (route) => { const x = await route.fetch(); const j = await x.json(); j.nodes = RUN; j.ifaces = IF; await route.fulfill({ json: j }); });
    const h = await c2.newPage(); await h.goto(B + '/'); await h.waitForTimeout(2200); await h.addStyleTag({ content: '.toasts{display:none!important}' });
    await h.locator('.top select').first().selectOption(name).catch(() => {}); await h.waitForTimeout(1500);
    await h.locator('.react-flow__controls-fitview').click(); await h.mouse.move(700, 120); await h.waitForTimeout(900);
    await h.screenshot({ path: file }); await c2.close(); };
  await step('canvas-internet', () => hero('internet-edge', OUT + 'screen-canvas-internet.png'));
  await step('canvas-dual', () => hero('dual-isp-mpls', OUT + 'screen-canvas-dual-isp.png'));
  await step('canvas-office', () => hero('small-office', OUT + 'screen-canvas-office.png'));
  await step('labmgr', async () => { await p.getByRole('button', { name: 'Lab manager' }).click(); await p.waitForTimeout(400); await p.locator('.mgr-tabs button', { hasText: 'Templates' }).click(); await p.waitForTimeout(700); await p.addStyleTag({ content: '.tpl .warn-t{display:none!important}' }); await p.waitForTimeout(200); await p.locator('.modal').screenshot({ path: OUT + 'screen-lab-manager.png' }); });
  await step('ios-import', async () => { await open('internet-edge'); await p.getByRole('button', { name: 'Lab manager' }).click(); await p.waitForTimeout(400); await p.locator('.mgr-tabs button', { hasText: 'import' }).click(); await p.waitForTimeout(400);
    await p.locator('.share textarea').first().fill('hostname CORE-R1\n!\ninterface GigabitEthernet0/0\n description to BRANCH-R2\n ip address 10.0.12.1 255.255.255.252\n!\ninterface GigabitEthernet0/1\n ip address 10.10.0.1 255.255.255.0\n ip access-group LAN-IN in\n!\ninterface Loopback0\n ip address 1.1.1.1 255.255.255.255\n!\nrouter ospf 1\n router-id 1.1.1.1\n network 10.0.12.0 0.0.0.3 area 0\n network 10.10.0.0 0.0.0.255 area 0\n!\nip access-list extended LAN-IN\n permit ip 10.10.0.0 0.0.0.255 any\n!\nsnmp-server community labRO RO\nntp server 10.10.0.5\n');
    await p.getByRole('button', { name: 'Another router' }).click();
    await p.locator('.share textarea').nth(1).fill('hostname BRANCH-R2\n!\ninterface GigabitEthernet0/0\n description to CORE-R1\n ip address 10.0.12.2 255.255.255.252\n!\ninterface GigabitEthernet0/1\n ip address 10.20.0.1 255.255.255.0\n!\nrouter ospf 1\n network 10.0.0.0 0.255.255.255 area 0\n!\nip route 0.0.0.0 0.0.0.0 10.0.12.1\n');
    await p.locator('input[aria-label="IOS lab name"]').fill('imported-wan'); await p.getByRole('button', { name: /^Import 2/ }).click(); await p.waitForTimeout(2000);
    const sec = p.locator('.share section', { hasText: 'Import Cisco IOS configs' }); await sec.scrollIntoViewIfNeeded(); await sec.screenshot({ path: OUT + 'screen-ios-import.png' }); });
  await step('tokens', async () => { await r('POST', '/api/auth/tokens', { label: 'gitlab-ci' }); await r('POST', '/api/auth/tokens', { label: 'nightly-checks' });
    await p.locator('.acct-btn').click(); await p.getByRole('menuitem', { name: /Account and password/ }).click(); await p.waitForTimeout(600);
    await p.locator('input[aria-label="Token name"]').fill('lab-deploy-script'); await p.getByRole('button', { name: 'Create token' }).click(); await p.waitForTimeout(700);
    await p.evaluate(() => { const c = document.querySelector('.token-fresh code'); if (c) c.textContent = 'pyt_' + 'x'.repeat(8) + '…' + c.textContent.slice(-4); });
    await p.locator('.tokens').scrollIntoViewIfNeeded(); await p.locator('.tokens').screenshot({ path: OUT + 'screen-tokens.png' }); });
  await step('admin-users', async () => { await r('POST', '/api/admin/users', { username: 'dave', password: 'dave-pass-12345', full_name: 'Dave Smith', email: 'dave@example.com' }); await r('POST', '/api/admin/users', { username: 'priya', password: 'priya-pass-1234', full_name: 'Priya Patel', email: 'priya@example.com' });
    await p.locator('.acct-btn').click(); await p.getByRole('menuitem', { name: /Admin:/ }).click(); await p.waitForTimeout(700); await p.locator('.modal').screenshot({ path: OUT + 'screen-admin-users.png' });
    await r('POST', '/api/admin/invites', { role: 'user', days: 7, note: 'CCNA evening class' }); await r('POST', '/api/admin/invites', { role: 'admin', days: 2, note: 'co-admin' });
    await p.locator('.set-nav button', { hasText: 'Invites' }).click(); await p.waitForTimeout(700); await p.locator('.modal').screenshot({ path: OUT + 'screen-admin-invites.png' });
    await p.locator('.set-nav button', { hasText: 'Audit log' }).click(); await p.waitForTimeout(700); await p.locator('.modal').screenshot({ path: OUT + 'screen-admin-audit.png' });
    await p.locator('.set-nav button', { hasText: 'Limits' }).click(); await p.waitForTimeout(700); await p.locator('.set-body h3', { hasText: 'Idle labs' }).scrollIntoViewIfNeeded(); await p.locator('.modal').screenshot({ path: OUT + 'screen-admin-limits.png' }); });
  await step('alerts', async () => { await p.keyboard.press(','); await p.waitForTimeout(700); await p.locator('.set-nav button', { hasText: 'Alert rules' }).click(); await p.waitForTimeout(700); await p.locator('.modal').screenshot({ path: OUT + 'screen-settings-alerts.png' }); });
  await step('invite', async () => { const inv = await r('POST', '/api/admin/invites', { role: 'user', days: 7 }); const q = await b.newPage({ viewport: { width: 1280, height: 860 } }); await q.addInitScript(() => { try { localStorage.setItem('netlab.theme', '"dark"'); } catch {} }); await q.goto(B + '/invite/' + inv.token); await q.waitForTimeout(1200); await q.screenshot({ path: OUT + 'screen-invite.png' }); await q.close(); });
  await step('challenges', async () => { await p.getByRole('button', { name: 'Lab manager' }).click(); await p.waitForTimeout(400); await p.locator('.mgr-tabs button', { hasText: 'Challenges' }).click(); await p.waitForTimeout(800); await p.locator('.modal').screenshot({ path: OUT + 'screen-challenges.png' }); });
  await step('challenge', async () => { await open('ospf-practice'); await fit(); await p.locator('button[aria-label^="Challenge"]').first().click(); await p.waitForTimeout(600);
    await p.getByRole('button', { name: /Show what will be checked/ }).click(); await p.waitForTimeout(300); await p.screenshot({ path: OUT + 'screen-challenge.png' });
    await p.locator('.challenge .dash-head button[title="Edit the challenge"]').click(); await p.waitForTimeout(400); await p.locator('.challenge').screenshot({ path: OUT + 'screen-challenge-editor.png' }); });
  // ---------- refreshed shots ----------
  await step('physical', async () => { await open('serial-wan'); const w = await win('hq', 'Physical'); await w.screenshot({ path: OUT + 'screen-physical.png' }); });
  await step('router', async () => { await open('serial-wan'); const w = await win('hq', 'Config'); await nav(w, 'Serial0/1/0'); await w.screenshot({ path: OUT + 'screen-device-router.png' }); });
  await step('acl', async () => { await open('nat-acl-edge'); const w = await win('r1', 'Config'); await nav(w, 'Access lists'); await w.locator('button', { hasText: 'OUTSIDE-IN' }).first().click().catch(() => {}); await p.waitForTimeout(400); await w.screenshot({ path: OUT + 'screen-acl.png' }); });
  await step('nat', async () => { await open('nat-acl-edge'); const w = await win('r1', 'Config'); await nav(w, /^NAT/); await w.screenshot({ path: OUT + 'screen-nat.png' }); });
  await step('switch', async () => { await open('campus-lacp'); const w = await win('dist1', 'Config'); await nav(w, /Port-channel|Po1/); await w.screenshot({ path: OUT + 'screen-device-switch.png' }); });
  await step('firewall', async () => { await open('firewall-edge'); const w = await win('fw1', 'Config'); await nav(w, /^NAT/); await w.screenshot({ path: OUT + 'screen-firewall.png' }); });
  await step('services', async () => { await open('small-office'); const w = await win('srv1', 'Services'); await nav(w, /^DHCP/); await w.screenshot({ path: OUT + 'screen-services.png' }); });
  await b.close();
  console.log('ERR', errs);
})();
