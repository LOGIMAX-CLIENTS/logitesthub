#!/usr/bin/env node
// Runs every *_test.md in a folder (one headless run each) and writes a summary page.
// Usage: node run-all.js <folder> [--no-ai]
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const dir = process.argv[2];
const extra = process.argv.slice(3);
if (!dir || !fs.existsSync(dir)) {
  console.error('Usage: node run-all.js <folder with _test.md files> [--no-ai]');
  process.exit(2);
}

const files = fs.readdirSync(dir).filter(f => f.endsWith('_test.md')).sort();
if (!files.length) { console.error('No _test.md files in ' + dir); process.exit(2); }

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const rows = [];
for (const f of files) {
  console.log(`\n>>> ${f}`);
  const r = spawnSync(process.execPath, [path.join(__dirname, 'run.js'), path.join(dir, f), ...extra], { encoding: 'utf8' });
  process.stdout.write(r.stdout || '');
  if (r.stderr) process.stderr.write(r.stderr);
  const status = (r.stdout.match(/RESULT : (\w+)/) || [])[1] || 'ERROR';
  const counts = (r.stdout.match(/RESULT : \w+\s+\((.*?)\)/) || [])[1] || '';
  const report = (r.stdout.match(/REPORT : (.+)/) || [])[1]?.trim() || '';
  rows.push({ f, status, counts, report });
}

const color = { PASSED: '#12805c', FAILED: '#c4320a', PARTIAL: '#6941c6', ERROR: '#98a2b3' };
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Test Run Summary</title>
<style>:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--line:#e4e7ec;--muted:#667085}@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--ink:#e6e8ec;--line:#2a2f3a;--muted:#98a2b3}}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:16px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted);text-transform:uppercase}
.b{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;font-weight:600;font-size:12px}a{color:inherit}</style></head><body>
<h1>Test Run Summary</h1><div class="sub">${esc(path.resolve(dir))} · ${esc(new Date().toLocaleString('en-IN'))} · ${rows.length} cases</div>
<table><thead><tr><th>Case</th><th>Result</th><th>Steps</th><th>Report</th></tr></thead><tbody>
${rows.map(r => `<tr><td>${esc(r.f.replace(/_test\.md$/, ''))}</td><td><span class="b" style="background:${color[r.status] || color.ERROR}">${esc(r.status)}</span></td><td>${esc(r.counts)}</td><td>${r.report ? `<a href="${esc('file:///' + r.report.replace(/\\/g, '/'))}">Open report</a>` : ''}</td></tr>`).join('\n')}
</tbody></table></body></html>`;
const out = path.join(__dirname, 'runs', `summary-${new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19)}.html`);
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, html);

console.log('\n==============================================================');
for (const r of rows) console.log(` ${r.status.padEnd(8)} ${r.f}`);
console.log(` SUMMARY : ${out}`);
console.log('==============================================================');
