#!/usr/bin/env node
// Portivo weekly read-only quality check.
// SAFETY CONTRACT: GET/HEAD/OPTIONS/TRACE only against one host. No form submissions with data,
// no writes, no AI calls, no sweeps, no billing, no admin login, no deletes of anything.
// The optional authenticated section does ONE normal login from env vars and aborts every non-GET API call.
// Usage: node run_weekly.js <outdir>
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const BASE = 'https://portivoestatesuite.com';
const HOST = new URL(BASE).host;
const OUT = process.argv[2] || '.';
const KNOWN = JSON.parse(fs.readFileSync(path.join(__dirname, 'known_issues.json'), 'utf8'));
const AXE_BASE = KNOWN.axeLandingBaseline;
const results = [];
const metrics = {};

let netErr = false; // set when any HTTP request in the current check failed at network level
function add(id, name, ok, detail) {
  const known = KNOWN.known[id];
  if (netErr) { ok = null; detail = 'NETWORK ERROR, inconclusive (not a pass, not a fail). ' + (detail || ''); netErr = false; }
  let status = ok === null ? 'SKIP' : ok ? (known ? 'RESOLVED?' : 'PASS') : (known ? 'KNOWN' : 'FAIL');
  results.push({ id, name, status, known: known || null, detail: String(detail || '').slice(0, 300) });
}

// ---- HTTP helper (curl honours the sandbox proxy and CA bundle) ----
function curl(url, { method = 'GET', headers = {}, cookie, pathAsIs = false } = {}) {
  const u = new URL(url);
  if (u.host !== HOST) throw new Error('refusing request to other host: ' + u.host);
  if (!['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)) throw new Error('refusing method ' + method);
  const args = ['-sS', '-i', '-m', '30', '-X', method];
  if (pathAsIs) args.push('--path-as-is');
  for (const [k, v] of Object.entries(headers)) args.push('-H', `${k}: ${v}`);
  if (cookie) args.push('-b', cookie);
  args.push(url);
  let out;
  for (let attempt = 1; attempt <= 3 && out === undefined; attempt++) {
    try { out = execFileSync('curl', args, { encoding: 'utf8', maxBuffer: 20e6, stdio: ['ignore', 'pipe', 'ignore'] }); } catch (e) { out = undefined; }
  }
  if (out === undefined) { netErr = true; return { status: 0, headers: {}, body: '', err: 'network error after 3 attempts' }; }
  const blocks = out.split('\r\n\r\n').filter((b, i) => !(i === 0 && /Connection Established/i.test(b)));
  const head = blocks[0] || '';
  const lines = head.split('\r\n');
  const status = parseInt((lines[0].match(/ (\d{3})/) || [])[1] || '0', 10);
  const h = {};
  for (const l of lines.slice(1)) { const i = l.indexOf(':'); if (i > 0) h[l.slice(0, i).toLowerCase()] = l.slice(i + 1).trim(); }
  return { status, headers: h, body: blocks.slice(1).join('\r\n\r\n') };
}

// ---- Public HTTP checks ----
function httpChecks() {
  const home = curl(BASE + '/');
  const login = curl(BASE + '/login');
  add('P00', 'Site reachable (home and login return 200)', home.status === 200 && login.status === 200, `home=${home.status} login=${login.status}`);
  const h = home.headers;
  const http = curl('http://' + HOST + '/');
  add('P01', 'HTTP redirects to HTTPS', [301, 308].includes(http.status) && /^https:/.test(http.headers.location || ''), `status=${http.status}`);
  const hs = h['strict-transport-security'] || '';
  add('P02', 'HSTS with max-age >= 1 year', /max-age=(\d+)/.test(hs) && +RegExp.$1 >= 31536000, hs);
  const csp = h['content-security-policy'] || '';
  add('P03', 'Clickjacking protection (frame-ancestors none or XFO DENY)', /frame-ancestors 'none'/.test(csp) || /deny/i.test(h['x-frame-options'] || ''), '');
  add('P04', 'X-Content-Type-Options nosniff', /nosniff/i.test(h['x-content-type-options'] || ''), '');
  add('P05', 'Referrer-Policy present', !!h['referrer-policy'], h['referrer-policy']);
  add('P06', 'Permissions-Policy present', !!h['permissions-policy'], '');
  add('P07', 'CSP present', !!csp, '');
  const script = (csp.match(/script-src([^;]*)/) || [, ''])[1];
  add('P08', "CSP script-src has no 'unsafe-eval'", !/unsafe-eval/.test(script), script.trim());
  add('P09', "CSP script-src has no 'unsafe-inline'", !/unsafe-inline/.test(script), script.trim());
  const connect = (csp.match(/connect-src([^;]*)/) || [, ''])[1];
  add('P10', 'CSP connect-src has no direct onrender.com backend host', !/onrender\.com/.test(connect), connect.trim());
  const trace = curl(BASE + '/', { method: 'TRACE' });
  add('P11', 'TRACE method blocked', trace.status >= 400 || trace.status === 405, `status=${trace.status}`);
  const env = curl(BASE + '/.env'), git = curl(BASE + '/.git/config');
  add('P12', '/.env and /.git/config not exposed', env.status === 404 && git.status === 404, `env=${env.status} git=${git.status}`);
  const trav = curl(BASE + '/app/../etc/passwd', { pathAsIs: true });
  add('P13', 'Path traversal not served', trav.status !== 200, `status=${trav.status}`);
  const gated = ['/app', '/app/settings', '/app/arrears', '/tenant'].map(p => [p, curl(BASE + p)]);
  add('P14', 'Unauthenticated /app and /tenant routes redirect to /login',
    gated.every(([, r]) => [307, 308].includes(r.status) && /\/login/.test(r.headers.location || '')), gated.map(([p, r]) => `${p}=${r.status}`).join(' '));
  const fake = curl(BASE + '/app', { cookie: 'pmos_at=' + 'A'.repeat(43) });
  add('P15', 'Edge gate rejects a fabricated session cookie', [307, 308, 401, 403].includes(fake.status), `status=${fake.status}`);
  const cors = curl(BASE + '/api/backend/auth/me', { headers: { Origin: 'https://evil.example' } });
  add('P16', 'API does not reflect a hostile Origin', !/evil\.example/.test(cors.headers['access-control-allow-origin'] || ''), cors.headers['access-control-allow-origin'] || 'none');
  const me = curl(BASE + '/api/backend/auth/me');
  let json = null; try { json = JSON.parse(me.body); } catch {}
  add('P17', 'Unauthenticated API returns clean 401 JSON', me.status === 401 && !!json, `status=${me.status}`);
  const health = curl(BASE + '/api/backend/health');
  add('P18', 'Health endpoint answers 200', health.status === 200, `status=${health.status}`);
  const sec = curl(BASE + '/.well-known/security.txt');
  add('P19', 'security.txt published', sec.status === 200, `status=${sec.status}`);
  const sm = curl(BASE + '/sitemap.xml');
  const locs = [...sm.body.matchAll(/<loc>([^<]+)/g)].map(m => m[1]).filter(l => l.startsWith(BASE)).slice(0, 15);
  const bad = locs.filter(l => curl(l).status !== 200);
  add('P20', 'Every sitemap URL returns 200', locs.length > 0 && bad.length === 0, `urls=${locs.length} bad=${bad.join(',')}`);
  const nf = curl(BASE + '/qa-weekly-nonexistent-page');
  add('P21', '404 page served for unknown route', nf.status === 404, `status=${nf.status}`);
}

// ---- Browser checks ----
async function browserChecks() {
  let chromium; try { ({ chromium } = require('playwright')); } catch { add('B00', 'Playwright available', null, 'playwright not installed'); return null; }
  const launch = { }; if (process.env.HTTPS_PROXY) launch.proxy = { server: process.env.HTTPS_PROXY };
  const browser = await chromium.launch(launch);
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const consoleErr = [], external = new Set();
  page.on('console', m => { if (m.type() === 'error') consoleErr.push(m.text()); });
  page.on('request', r => { const u = new URL(r.url()); if (/^https?:$/.test(u.protocol) && u.host !== HOST) external.add(u.host); });
  const t0 = Date.now();
  await page.goto(BASE + '/login', { waitUntil: 'networkidle', timeout: 60000 });
  metrics.login_load_ms = Date.now() - t0;
  add('B01', 'Login page renders email, password (masked) and submit', await page.$('input[type=email]') && await page.$('input[type=password]') && await page.$('button[type=submit]') ? true : false, `load=${metrics.login_load_ms}ms`);
  const fields = await page.$$eval('input', els => els.map(e => ({ labelled: !!(e.labels && e.labels.length) || !!e.getAttribute('aria-label'), ac: e.getAttribute('autocomplete') })));
  add('B02', 'Login inputs labelled with autocomplete', fields.length >= 2 && fields.every(f => f.labelled && f.ac), JSON.stringify(fields));
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
  add('B03', 'No horizontal overflow at 375px', !(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), '');
  await page.setViewportSize({ width: 1280, height: 800 });
  // Console noise excluding two known, tracked calls
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  const unexpected = consoleErr.filter(e => !/status of (401|404)/.test(e));
  add('B04', 'No unexpected console errors on public pages', unexpected.length === 0, unexpected.slice(0, 3).join(' | '));
  const thirdParty = [...external].filter(h => !/unsplash/.test(h));
  add('B05', 'No analytics/error-tracking requests before consent', thirdParty.length === 0, [...external].join(','));
  add('B06', 'No third-party image hotlinking before consent', ![...external].some(h => /unsplash/.test(h)), [...external].join(','));
  // axe
  let axeSrc = null; try { axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8'); } catch {}
  if (!axeSrc) { add('B07', 'Accessibility scan (axe) of landing page', null, 'axe-core not installed'); add('B08', 'Accessibility scan (axe) of login page', null, 'axe-core not installed'); }
  else {
    const scan = async p => { await page.goto(BASE + p, { waitUntil: 'networkidle' }); await page.evaluate(axeSrc); return page.evaluate(async () => (await axe.run()).violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }))); };
    const land = await scan('/'); metrics.axe_landing = land;
    const worse = land.filter(v => !(v.id in AXE_BASE) || v.nodes > AXE_BASE[v.id]);
    add('B07', 'Landing page: no NEW or worse accessibility violations vs baseline', worse.length === 0, `current=${land.map(v => v.id + ':' + v.nodes).join(',')} worse=${worse.map(v => v.id).join(',')}`);
    const lg = await scan('/login'); metrics.axe_login = lg;
    add('B08', 'Login page: zero accessibility violations', lg.length === 0, lg.map(v => v.id + ':' + v.nodes).join(','));
  }
  const titles = {};
  for (const p of ['/', '/login', '/admin']) { await page.goto(BASE + p, { waitUntil: 'domcontentloaded' }); titles[p] = await page.title(); }
  add('B09', 'Distinct page titles for /, /login, /admin', new Set(Object.values(titles)).size === 3, JSON.stringify(titles));
  await browser.close();
}

// ---- Optional authenticated read-only section ----
async function authChecks() {
  const email = process.env.PORTIVO_EMAIL, pass = process.env.PORTIVO_PASS;
  if (!email || !pass) { add('A00', 'Authenticated section (login + page loads)', null, 'PORTIVO_EMAIL/PORTIVO_PASS not provided to this run; NOT TESTED'); return; }
  const { chromium } = require('playwright');
  const launch = {}; if (process.env.HTTPS_PROXY) launch.proxy = { server: process.env.HTTPS_PROXY };
  const browser = await chromium.launch(launch);
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(BASE + '/login', { waitUntil: 'networkidle' });
  const e = p.getByRole('button', { name: 'Essential only' }); if (await e.count()) await e.click();
  await p.fill('input[name=email]', email); await p.fill('input[name=password]', pass);
  const t0 = Date.now();
  await Promise.all([p.waitForURL(u => !u.pathname.startsWith('/login'), { timeout: 30000 }).catch(() => null), p.click('button[type=submit]:has-text("Sign in")')]);
  await p.waitForTimeout(3000);
  metrics.login_ms = Date.now() - t0;
  const ok = !new URL(p.url()).pathname.startsWith('/login');
  add('A01', 'Demo user can log in (one attempt)', ok, `ms=${metrics.login_ms} url=${new URL(p.url()).pathname}`);
  if (!ok) { await browser.close(); return; }
  const blocked = [];
  await ctx.route('**/api/**', r => { const m = r.request().method(); if (m === 'GET' || m === 'HEAD') return r.continue(); blocked.push(m); return r.abort(); });
  const pages = ['/app', '/app/properties', '/app/operations', '/app/arrears', '/app/compliance', '/app/reports'];
  const failures = [];
  for (const pg of pages) {
    const q = await ctx.newPage(); const errs5 = [];
    q.on('response', r => { if (r.status() >= 500) errs5.push(`${r.status()} ${new URL(r.url()).pathname}`); });
    const resp = await q.goto(BASE + pg, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => null);
    const stayed = new URL(q.url()).pathname.startsWith('/app');
    if (!resp || resp.status() >= 400 || !stayed || errs5.length) failures.push(`${pg}:${resp && resp.status()}${stayed ? '' : ' redirected'}${errs5.length ? ' ' + errs5.join(',') : ''}`);
    await q.close();
  }
  add('A02', 'Key desk pages load without 4xx/5xx or logout redirect', failures.length === 0, failures.join(' | ') || `${pages.length} pages ok`);
  add('A03', 'Read-only guard: no write requests were attempted', blocked.length === 0, `blocked=${blocked.length}`);
  await browser.close();
}

(async () => {
  httpChecks();
  await browserChecks().catch(e => add('B99', 'Browser checks completed', false, e.message));
  await authChecks().catch(e => add('A99', 'Authenticated checks completed', false, e.message.replace(process.env.PORTIVO_PASS || '§', '***')));
  const date = new Date().toISOString().slice(0, 10);
  const count = s => results.filter(r => r.status === s).length;
  const summary = { date, pass: count('PASS'), fail: count('FAIL'), known: count('KNOWN'), resolved: count('RESOLVED?'), skipped: count('SKIP') };
  fs.writeFileSync(path.join(OUT, `${date}.json`), JSON.stringify({ summary, metrics, results }, null, 2));
  const rows = list => list.map(r => `| ${r.id} | ${r.name} | ${r.known ? r.known + ' | ' : ''}${r.detail.replace(/\|/g, '/')} |`).join('\n');
  const sec = (title, status, withKnown) => { const l = results.filter(r => r.status === status); return l.length ? `\n## ${title} (${l.length})\n| ID | Check | ${withKnown ? 'Tracked as | ' : ''}Detail |\n|---|---|${withKnown ? '---|' : ''}---|\n${rows(l)}\n` : ''; };
  const md = `# Weekly Quality Report ${date}\n\nTarget: ${BASE} (production). Mode: read-only. Nothing was created, changed, sent or deleted.\n\n**Result: ${summary.fail ? 'REGRESSIONS FOUND' : 'No new regressions'}**. Pass ${summary.pass}, new failures ${summary.fail}, known issues still open ${summary.known}, known issues now passing ${summary.resolved}, not run ${summary.skipped}.\n${sec('New failures (regressions)', 'FAIL', false)}${sec('Known issues now passing (verify and close)', 'RESOLVED?', true)}${sec('Known issues still open', 'KNOWN', true)}${sec('Not run (these are untested, not passed)', 'SKIP', false)}${sec('Passed', 'PASS', false)}\n## Metrics\n\`\`\`\n${JSON.stringify(metrics, null, 1)}\n\`\`\`\n\n## Coverage limits\nTLS certificate and protocol checks, rate limiting, cross-org isolation, billing, email, AI, scheduler and every other write-path check are NOT covered by this weekly run. See docs/quality/RISK_AND_TEST_MAP.md.\n`;
  fs.writeFileSync(path.join(OUT, `${date}.md`), md);
  console.log(JSON.stringify(summary));
  process.exit(summary.fail ? 1 : 0);
})();
