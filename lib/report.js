// Writes a self-contained report.html next to the run's video and screenshots.
const fs = require('fs');
const path = require('path');

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const secs = ms => (ms / 1000).toFixed(1) + 's';

const LABEL = { passed: 'Passed', failed: 'Failed', 'needs-ai': 'Needs AI', skipped: 'Skipped' };

function writeReport(runDir, summary, results, videoFile) {
  let lastSection = null;
  const rows = results.map(r => {
    const head = r.section && r.section !== lastSection ? `<div class="section">${esc(r.section)}</div>` : '';
    lastSection = r.section;
    return `${head}
    <div class="step ${r.status}">
      <div class="num">${r.n}</div>
      <div class="body">
        <div class="text">${esc(r.text)}</div>
        ${r.error ? `<div class="err">${esc(r.error)}</div>` : ''}
        ${r.warnings.map(w => `<div class="warn">${esc(w)}</div>`).join('')}
        ${r.status === 'needs-ai' ? `<div class="note">Free-text step, not run: ${esc(r.ai || 'AI is switched off for this run')}.</div>` : ''}
        ${r.ai && r.status !== 'needs-ai' && !r.error ? `<div class="note">AI checked: ${esc(r.ai)}</div>` : ''}
      </div>
      <div class="meta"><span class="badge ${r.status}">${LABEL[r.status]}</span><span class="ms">${r.status === 'skipped' ? '' : secs(r.ms)}</span></div>
      ${r.shot ? `<img class="thumb" src="${esc(r.shot)}" alt="Step ${r.n} screenshot" loading="lazy">` : '<div class="thumb none"></div>'}
    </div>`;
  }).join('');

  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Test Run Report</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#667085;--line:#e4e7ec;--pass:#12805c;--fail:#c4320a;--ai:#6941c6;--skip:#98a2b3;--warn:#b54708}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b22;--ink:#e6e8ec;--muted:#98a2b3;--line:#2a2f3a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line);background:var(--card)}
h1{margin:0 0 6px;font-size:20px}.sub{color:var(--muted);font-size:13px}
.verdict{display:inline-block;padding:3px 10px;border-radius:999px;font-weight:600;font-size:13px;color:#fff;margin-right:10px}
.verdict.PASSED{background:var(--pass)}.verdict.FAILED{background:var(--fail)}.verdict.PARTIAL{background:var(--ai)}
.stats{display:flex;gap:18px;margin-top:10px;flex-wrap:wrap;font-size:13px}.stats b{font-size:16px}
main{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:20px;padding:20px 24px}
@media (max-width:900px){main{grid-template-columns:1fr;padding:16px}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.panel h2{margin:0 0 10px;font-size:15px}.videoWrap{position:sticky;top:12px}
video{width:100%;border-radius:8px;background:#000}
.section{margin:14px 0 6px;font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.step{display:grid;grid-template-columns:28px 1fr auto 96px;gap:10px;align-items:start;padding:10px;border:1px solid var(--line);border-left:4px solid var(--skip);border-radius:8px;margin-bottom:8px;background:var(--card)}
.step.passed{border-left-color:var(--pass)}.step.failed{border-left-color:var(--fail)}.step.needs-ai{border-left-color:var(--ai)}
.num{color:var(--muted);font-weight:600;text-align:right}.text{word-break:break-word}
.err{color:var(--fail);margin-top:4px;font-size:13px}.warn{color:var(--warn);margin-top:4px;font-size:13px}.note{color:var(--muted);margin-top:4px;font-size:12px}
.meta{display:flex;flex-direction:column;align-items:flex-end;gap:4px}.ms{color:var(--muted);font-size:12px}
.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;color:#fff;background:var(--skip);white-space:nowrap}
.badge.passed{background:var(--pass)}.badge.failed{background:var(--fail)}.badge.needs-ai{background:var(--ai)}
.thumb{width:96px;height:54px;object-fit:cover;border-radius:4px;border:1px solid var(--line);cursor:zoom-in}.thumb.none{cursor:default}
@media (max-width:600px){.step{grid-template-columns:24px 1fr}.meta{flex-direction:row;grid-column:2}.thumb{grid-column:2;width:100%;height:auto}}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;padding:16px;cursor:zoom-out}
#lb img{max-width:100%;max-height:100%;border-radius:6px}
</style></head><body>
<header>
  <h1>${esc(summary.test)}</h1>
  <div class="sub"><span class="verdict ${summary.status}">${summary.status}</span>${esc(summary.file)} · ${esc(new Date(summary.at).toLocaleString('en-IN'))} · ${secs(summary.durationMs)}</div>
  <div class="stats"><span><b>${summary.total}</b> steps</span><span style="color:var(--pass)"><b>${summary.passed}</b> passed</span><span style="color:var(--fail)"><b>${summary.failed}</b> failed</span><span style="color:var(--ai)"><b>${summary.needsAI}</b> needs AI</span><span><b>${summary.skipped}</b> skipped</span></div>
</header>
<main>
  <section class="panel"><h2>Steps</h2>${rows}</section>
  <section><div class="panel videoWrap"><h2>Screen recording</h2>${videoFile ? `<video src="${esc(videoFile)}" controls preload="metadata"></video>` : '<p class="sub">No video recorded.</p>'}</div></section>
</main>
<div id="lb"><img alt="Screenshot"></div>
<script>
const lb=document.getElementById('lb'),im=lb.querySelector('img');
document.querySelectorAll('img.thumb').forEach(t=>t.addEventListener('click',()=>{im.src=t.src;lb.style.display='flex'}));
lb.addEventListener('click',()=>{lb.style.display='none'});
</script>
</body></html>`;
  const file = path.join(runDir, 'report.html');
  fs.writeFileSync(file, html);
  return file;
}

module.exports = { writeReport };
