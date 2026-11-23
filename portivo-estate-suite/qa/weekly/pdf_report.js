#!/usr/bin/env node
// Render a weekly run (history/<date>.json) as a PDF. Read-only: reads a local JSON file, writes a local PDF.
// Usage: node pdf_report.js <history.json> <out.pdf>
const fs = require('fs');
const { chromium } = require('playwright');
const [, , IN, OUT] = process.argv;
const data = JSON.parse(fs.readFileSync(IN, 'utf8'));
const { summary, metrics, results } = data;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const SEV = { 'F1': 'S3', 'F2': 'S3', 'F3': 'S3', 'A1': 'S3', 'F7': 'S4', 'F10': 'S4', 'F11': 'S4' };
const sev = known => (SEV[(known || '').split(' ')[0]] || '');
const by = s => results.filter(r => r.status === s);
const verdict = summary.fail ? 'REGRESSIONS FOUND' : (by('SKIP').some(r => /NETWORK/.test(r.detail)) ? 'INCONCLUSIVE CHECKS' : 'NO NEW REGRESSIONS');
const vclass = summary.fail ? 'bad' : 'ok';

const table = (rows, cols) => rows.length ? `<table><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(c[1](r))}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<p class="muted">None.</p>';

const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Portivo Weekly Quality Report ${esc(summary.date)}</title>
<style>
:root{--ink:#1a2230;--muted:#5b6678;--line:#d9dee7;--ok:#146c43;--okbg:#e6f4ec;--bad:#a4262c;--badbg:#fbeaea;--warn:#8a5a00;--warnbg:#fff4dc;--accent:#0f6b73}
*{box-sizing:border-box}body{font:10.5pt/1.45 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif;color:var(--ink);margin:0}
h1{font-size:20pt;margin:0 0 2pt}h2{font-size:12.5pt;margin:18pt 0 6pt;padding-bottom:3pt;border-bottom:1.5px solid var(--accent);color:var(--accent)}
.sub{color:var(--muted);margin:0 0 12pt}
.banner{padding:10pt 12pt;border-radius:6pt;font-weight:600;margin:10pt 0}.banner.ok{background:var(--okbg);color:var(--ok)}.banner.bad{background:var(--badbg);color:var(--bad)}
.cards{display:flex;gap:8pt;margin:8pt 0}.card{flex:1;border:1px solid var(--line);border-radius:6pt;padding:8pt;text-align:center}
.card b{display:block;font-size:20pt}.card span{font-size:8.5pt;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;font-size:9pt;margin:4pt 0}th{text-align:left;background:#f1f4f8;padding:4pt 6pt;border-bottom:1px solid var(--line)}
td{padding:4pt 6pt;border-bottom:1px solid var(--line);vertical-align:top;word-break:break-word}tr{page-break-inside:avoid}th:first-child,td:first-child{white-space:nowrap;width:30pt}
.muted{color:var(--muted)}.note{background:var(--warnbg);color:var(--warn);padding:8pt 10pt;border-radius:6pt;margin:8pt 0;font-size:9.5pt}
.foot{margin-top:18pt;font-size:8.5pt;color:var(--muted);border-top:1px solid var(--line);padding-top:6pt}
</style></head><body>
<h1>Portivo Estate Suite: Weekly Quality Report</h1>
<p class="sub">Run date ${esc(summary.date)} &middot; Target: portivoestatesuite.com (production) &middot; Mode: read-only. Nothing was created, changed, sent or deleted.</p>
<div class="banner ${vclass}">${esc(verdict)}: ${summary.pass} passed, ${summary.fail} new failures, ${summary.known} known issues still open, ${summary.skipped} not run.</div>
<div class="cards">
<div class="card"><b>${summary.pass}</b><span>Passed</span></div>
<div class="card"><b>${summary.fail}</b><span>New failures</span></div>
<div class="card"><b>${summary.known}</b><span>Known open</span></div>
<div class="card"><b>${summary.resolved}</b><span>Possibly fixed</span></div>
<div class="card"><b>${summary.skipped}</b><span>Not run</span></div>
</div>

<h2>New failures</h2>
${table(by('FAIL'), [['ID', r => r.id], ['Check', r => r.name], ['Detail', r => r.detail]])}

<h2>Known issues still open</h2>
<p class="muted">Already reported. Listed so a change in status is visible. Severity uses the S1 (money, legal duty, personal data) to S4 (cosmetic) scale in the quality charter.</p>
${table(by('KNOWN'), [['ID', r => r.id], ['Check', r => r.name], ['Tracked as', r => r.known], ['Sev', r => sev(r.known)], ['Evidence', r => r.detail]])}

<h2>Possibly fixed (needs a person to verify)</h2>
${table(by('RESOLVED?'), [['ID', r => r.id], ['Check', r => r.name], ['Tracked as', r => r.known]])}

<h2>Not run: untested, not passed</h2>
${table(by('SKIP'), [['ID', r => r.id], ['Check', r => r.name], ['Reason', r => r.detail]])}
<div class="note"><b>Coverage limits.</b> This weekly run covers the public surface only. It does NOT test: logged-in behaviour, TLS certificate and protocols (the test environment's proxy re-signs traffic), rate limiting and lockout, cross-organisation data isolation, billing, email, AI features, background jobs, or anything that writes data. A clean report here is not evidence that the product is secure or fully working.</div>

<h2>Passed checks</h2>
${table(by('PASS'), [['ID', r => r.id], ['Check', r => r.name], ['Detail', r => r.detail]])}

<h2>Metrics</h2>
<table><tbody>
<tr><td>Login page load to network idle</td><td>${esc(metrics.login_load_ms)} ms</td></tr>
<tr><td>Landing page accessibility violations (axe)</td><td>${esc((metrics.axe_landing || []).map(v => `${v.id}: ${v.nodes} (${v.impact})`).join('; ') || 'not run')}</td></tr>
<tr><td>Login page accessibility violations (axe)</td><td>${esc(metrics.axe_login ? metrics.axe_login.length : 'not run')}</td></tr>
</tbody></table>

<h2>Actions for the owner</h2>
<table><tbody>
<tr><td>1</td><td>Remove the saved "Portivo demo account" network secret in the environment settings. It likely caused the unconfirmed A2 finding and will distort authenticated tests.</td></tr>
<tr><td>2</td><td>Rotate the demo password. It was shared in a chat transcript.</td></tr>
<tr><td>3</td><td>Confirm whether the demo workspace is disposable, and provide disposable QA organisations plus test inboxes, so write-path checks can be run safely.</td></tr>
<tr><td>4</td><td>Run an external TLS scan (for example SSL Labs). It cannot be done from the test environment.</td></tr>
<tr><td>5</td><td>Engineering triage of the known issues above, starting with the CSP weaknesses and the edge route-gating check.</td></tr>
</tbody></table>
<p class="foot">Generated by the weekly read-only suite (qa/weekly). Source data: qa/weekly/history/${esc(summary.date)}.json.</p>
</body></html>`;

(async () => {
  const launch = {}; if (process.env.HTTPS_PROXY) launch.proxy = { server: process.env.HTTPS_PROXY };
  const b = await chromium.launch(launch);
  const p = await b.newPage();
  await p.setContent(html, { waitUntil: 'load' });
  await p.pdf({ path: OUT, format: 'A4', printBackground: true, margin: { top: '16mm', bottom: '16mm', left: '14mm', right: '14mm' }, displayHeaderFooter: true, headerTemplate: '<span></span>', footerTemplate: '<div style="font-size:8px;color:#777;width:100%;text-align:center">Portivo weekly quality report &middot; page <span class="pageNumber"></span> of <span class="totalPages"></span></div>' });
  await b.close();
  console.log('wrote', OUT);
})();
