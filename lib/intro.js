// LogiTestHub intro screen shown in the recorded browser before the first step.
// It becomes the first seconds of video.webm and is saved as poster.png, so the
// player (inline and full screen) shows a branded frame instead of a blank page.
const fs = require('fs');
const path = require('path');

const LOGO = path.join(__dirname, '..', 'manager', 'static', 'logo.svg');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function logoSvg() {
  try { return fs.readFileSync(LOGO, 'utf8').replace(/<\?xml[^>]*>/, ''); } catch (_) { return ''; }
}

// Icons: simple line glyphs, our own set.
const ICON = {
  browser: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10 5-3v10l-5-3"/>',
  camera: '<path d="M4 8h3l2-2.5h6L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  ai: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><rect x="7" y="7" width="10" height="10" rx="2.5"/><path d="M10 14.5 12 9.5l2 5M10.6 13h2.8"/>',
  shield: '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
  share: '<circle cx="17" cy="5.5" r="2.5"/><circle cx="6.5" cy="12" r="2.5"/><circle cx="17" cy="18.5" r="2.5"/><path d="m8.7 10.8 6.1-3.9M8.7 13.2l6.1 3.9"/>',
};
const icon = k => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[k]}</svg>`;

function introHtml({ title, steps, url, runLabel }) {
  const shown = steps.slice(0, 4);
  const stepsHtml = shown.map((s, i) => `
      <div class="st ${i === 0 ? 'on' : ''}">
        <span class="dot"></span>
        <div><b>Step ${i + 1}</b><span>${esc(s.length > 58 ? s.slice(0, 57) + '…' : s)}</span></div>
      </div>`).join('') + (steps.length > shown.length ? `<div class="more">+ ${steps.length - shown.length} more steps</div>` : '');
  const feats = [['browser', 'Headless<br>Chromium'], ['video', 'Screen<br>Recording'], ['camera', 'Step<br>Screenshots'],
    ['ai', 'AI Steps<br>(Claude)'], ['shield', 'Secrets<br>Masked'], ['share', 'Shareable<br>Report']];
  return `<!doctype html><html><head><meta charset="utf-8"><title>LogiTestHub</title><style>
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{font-family:"Segoe UI",Manrope,system-ui,sans-serif;color:#e7eef0;overflow:hidden;
  background:radial-gradient(900px 520px at 50% 38%,rgba(212,175,55,.10),transparent 60%),
             radial-gradient(1200px 700px at 15% -10%,#1f4c5a 0%,transparent 55%),
             linear-gradient(160deg,#0f2a33 0%,#0a1c24 60%,#07141a 100%);}
.wrap{height:100%;display:flex;flex-direction:column;align-items:center;padding:52px 60px 0}
.brand{display:flex;align-items:center;gap:14px}
.brand svg{width:52px;height:52px}
.wm{font-size:40px;font-weight:300;letter-spacing:-.02em;color:#f5efe0}
.wm b{font-weight:800;background:linear-gradient(90deg,#f3dc97,#d4af37);-webkit-background-clip:text;color:transparent}
.tag{margin-top:8px;font-size:12px;letter-spacing:.32em;color:#d4af37;font-weight:700}
.title{margin-top:26px;font-size:17px;color:#a9c0c6}
.title b{color:#f1f5f6;font-weight:600}
.stage{flex:1;width:100%;max-width:1080px;display:grid;grid-template-columns:1fr 1.15fr;gap:60px;align-items:center}
.steps{position:relative;padding-left:26px;border-left:2px solid rgba(255,255,255,.12)}
.st{position:relative;display:flex;gap:10px;margin:0 0 22px}
.st .dot{position:absolute;left:-33px;top:4px;width:12px;height:12px;border-radius:50%;background:#0a1c24;border:2px solid #5d7b83}
.st.on .dot{border-color:#d4af37;box-shadow:0 0 0 5px rgba(212,175,55,.18)}
.st b{display:block;font-size:15px;color:#6f8b92}
.st span{display:block;font-size:13px;color:#57737a;margin-top:2px}
.st.on b{color:#f5efe0} .st.on span{color:#b9cbd0}
.more{font-size:12px;color:#57737a;margin-top:-6px}
.card{position:relative;border-radius:16px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);
  box-shadow:0 30px 80px rgba(0,0,0,.35),0 0 120px rgba(34,179,166,.10);overflow:hidden}
.bar{display:flex;align-items:center;gap:10px;padding:12px 14px;background:rgba(0,0,0,.18);border-bottom:1px solid rgba(255,255,255,.07)}
.bar i{width:10px;height:10px;border-radius:50%;background:#3b545b;display:inline-block}
.url{flex:1;margin-left:8px;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.07);font-size:12px;color:#8fb0b8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cbody{padding:34px 28px;display:flex;align-items:center;gap:18px}
.spin{width:44px;height:44px;border-radius:50%;border:3px solid rgba(255,255,255,.12);border-top-color:#22b3a6;flex:none;animation:r 1s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
.cbody b{display:block;font-size:16px;color:#f1f5f6}
.cbody span{display:block;font-size:13px;color:#8fa8ae;margin-top:3px}
.pill{position:absolute;right:16px;bottom:14px;font-size:11px;font-weight:700;color:#0a1c24;background:linear-gradient(90deg,#f3dc97,#d4af37);border-radius:999px;padding:3px 10px}
.feats{width:100%;border-top:1px solid rgba(255,255,255,.08);padding:16px 40px 20px;display:grid;grid-template-columns:repeat(6,1fr);position:relative}
.feats:before{content:"Powered by LogiTestHub";position:absolute;top:-9px;left:50%;transform:translateX(-50%);font-size:11px;color:#6f8b92;background:#08171d;padding:0 12px}
.f{display:flex;flex-direction:column;align-items:center;gap:6px;color:#7f9aa1;font-size:11.5px;text-align:center;line-height:1.35}
.f svg{color:#22b3a6}
</style></head><body><div class="wrap">
  <div class="brand">${logoSvg()}<div class="wm">Logi<b>TestHub</b></div></div>
  <div class="tag">PLAN • TEST • TRACK • DELIVER</div>
  <div class="title">Running test case: <b>${esc(title)}</b></div>
  <div class="stage">
    <div class="steps">${stepsHtml}</div>
    <div class="card">
      <div class="bar"><i></i><i></i><i></i><div class="url">${esc(url)}</div></div>
      <div class="cbody"><div class="spin"></div><div><b>Starting headless browser…</b><span>Recording the screen and every step for review.</span></div></div>
      <div class="pill">${esc(runLabel)}</div>
    </div>
  </div>
  <div class="feats">${feats.map(([k, t]) => `<div class="f">${icon(k)}<div>${t}</div></div>`).join('')}</div>
</div></body></html>`;
}

// Shows the intro in the recorded page, waits so it lands in the video, saves poster.png.
async function showIntro(page, runDir, info, holdMs = 2500) {
  await page.setContent(introHtml(info), { waitUntil: 'load' });
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(runDir, 'poster.png') }).catch(() => {});
  await page.waitForTimeout(Math.max(0, holdMs - 400));
}

module.exports = { introHtml, showIntro };
