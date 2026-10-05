// LogiTestHub "Tester CLI" intro screen shown in the recorded browser before the first step.
// It becomes the first seconds of video.webm and is saved as poster.png, so the player shows a branded frame.
// Design: LMX logo (its ball travels round the swoosh), "Tester CLI", a glass terminal that types "> Tester-CLI",
// 8 round glass icons orbiting it, the test's steps and a browser card on the right, feature row at the bottom.
// Everything animates in about 2.6 s; the poster is taken once it has settled (the orbit keeps drifting).
const fs = require('fs');
const path = require('path');

const STATIC = path.join(__dirname, '..', 'manager', 'static');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function dataUri(file) {   // the page is set with setContent (no server), so images go in as data: URIs
  const type = file.endsWith('.svg') ? 'image/svg+xml' : 'image/png';
  try { return `data:${type};base64,` + fs.readFileSync(path.join(STATIC, file)).toString('base64'); } catch (_) { return ''; }
}

// line icons for the feature row
const FEAT = {
  browser: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10 5-3v10l-5-3"/>',
  camera: '<path d="M4 8h3l2-2.5h6L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  ai: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
  shield: '<path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6z"/><path d="m9 12 2 2 4-4"/>',
};
const FEATS = [['browser', 'Headless<br>Execution', '#2f7cf6'], ['video', 'Screen<br>Recording', '#8b5cf6'],
  ['camera', 'Step<br>Screenshots', '#e04f6a'], ['ai', 'AI Steps<br>(Claude)', '#f08a2c'], ['shield', 'Secrets<br>Masked', '#16a3a0']];
const svgLine = (d, c) => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="${c}" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;

// filled glyphs for the orbiting bubbles
const ORBIT = [
  ['#2f6cf0', '<path d="M8 3.5h8v3H8z" fill="#2f6cf0"/><rect x="5" y="5" width="14" height="16" rx="2" fill="#2f6cf0"/><path d="M8 10h5M8 13.5h5M8 17h3" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/><path d="m14.5 15.5 1.6 1.6 3.4-3.6" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'],
  ['#2f86f0', '<circle cx="12" cy="8" r="4.2" fill="#2f86f0"/><path d="M4 20.5c.8-4.2 4-6.5 8-6.5s7.2 2.3 8 6.5z" fill="#2f86f0"/>'],
  ['#7c4df0', '<path fill="#7c4df0" d="M12 2.8l1.6 2.5 2.9-.7.6 2.9 2.7 1.3-1.2 2.7 1.2 2.7-2.7 1.3-.6 2.9-2.9-.7L12 21.2l-1.6-2.5-2.9.7-.6-2.9-2.7-1.3 1.2-2.7-1.2-2.7 2.7-1.3.6-2.9 2.9.7z"/><circle cx="12" cy="12" r="3.4" fill="#fff"/>'],
  ['#18a5a0', '<path d="M12 2.5 4.5 5.5v6c0 5 3.3 8.6 7.5 10 4.2-1.4 7.5-5 7.5-10v-6z" fill="#18a5a0"/><path d="m8.5 12 2.4 2.4 4.6-4.8" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'],
  ['#2f6cf0', '<path fill="#2f6cf0" d="M7 19a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.6A3.8 3.8 0 0 1 17.5 19z"/>'],
  ['#2f86f0', '<rect x="4" y="13" width="3.6" height="7" rx="1" fill="#2f86f0"/><rect x="9.2" y="9.5" width="3.6" height="10.5" rx="1" fill="#2f86f0"/><rect x="14.4" y="6" width="3.6" height="14" rx="1" fill="#2f86f0"/>'],
  ['#8b4df0', '<ellipse cx="12" cy="13.5" rx="5" ry="6.5" fill="#8b4df0"/><circle cx="12" cy="6.5" r="2.6" fill="#8b4df0"/><path d="M3.5 10h3.4M17.1 10h3.4M3.5 14.5h3.4M17.1 14.5h3.4M4.5 19l3-2M19.5 19l-3-2M7.5 4l2 2M16.5 4l-2 2" stroke="#8b4df0" stroke-width="1.6" stroke-linecap="round"/><path d="M12 8v12" stroke="#fff" stroke-width="1.3"/>'],
  ['#2f6cf0', '<path d="m8.5 7.5-4.5 4.5 4.5 4.5M15.5 7.5l4.5 4.5-4.5 4.5M13.6 5l-3.2 14" stroke="#2f6cf0" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'],
  ['#2ead33', null, 'playwright-logo.svg'],   // Playwright: the browser engine that runs the steps
  ['#3776ab', '<path fill="#3776ab" d="M11.9 2C6.8 2 7.1 4.2 7.1 4.2v2.3H12v.7H5.2S2 6.8 2 12s2.8 5 2.8 5h1.7v-2.4s-.1-2.8 2.8-2.8h4.8s2.7 0 2.7-2.6V4.6S17.2 2 11.9 2zm-2.7 1.5a.9.9 0 1 1 0 1.8.9.9 0 0 1 0-1.8z"/><path fill="#ffd43b" d="M12.1 22c5.1 0 4.8-2.2 4.8-2.2v-2.3H12v-.7h6.8S22 17.2 22 12s-2.8-5-2.8-5h-1.7v2.4s.1 2.8-2.8 2.8H9.9s-2.7 0-2.7 2.6v4.6S6.8 22 12.1 22zm2.7-1.5a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8z"/>'],   // Python: LogiTestHub server
];

// LMX logo geometry (logo-lmx.png pixels): the swoosh oval and where the ball rests
const LOGO_FILE = [1643, 581];
const SWOOSH = { cx: 822, cy: 290, a: 833, b: 205, tilt: -12.8 };
const BALL_HOME = [1595, 68], BALL_SPRITE = 112;
const LOGO_W = 230;

function introHtml({ title, steps, url, runLabel }) {
  const shown = steps.slice(0, 4);
  const stepsHtml = shown.map((s, i) => `
      <div class="st ${i === 0 ? 'on' : ''}" style="animation-delay:${1050 + i * 120}ms">
        <span class="dot"></span>
        <div><b>Step ${i + 1}</b><span>${esc(s.length > 58 ? s.slice(0, 57) + '…' : s)}</span></div>
      </div>`).join('') + (steps.length > shown.length
    ? `<div class="more" style="animation-delay:${1050 + shown.length * 120}ms">+ ${steps.length - shown.length} more steps</div>` : '');
  const logo = dataUri('logo-lmx-noball.png'), ball = dataUri('logo-lmx-ball.png');
  const lh = Math.round(LOGO_W * LOGO_FILE[1] / LOGO_FILE[0]);
  const bw = (BALL_SPRITE * LOGO_W / LOGO_FILE[0]).toFixed(2);
  const geo = JSON.stringify({ k: LOGO_W / LOGO_FILE[0], ...SWOOSH, home: BALL_HOME, r: BALL_SPRITE / 2 });
  const bubbles = ORBIT.map(([c, g, file], i) => {
    const art = file ? (dataUri(file) ? `<img src="${dataUri(file)}" width="36" height="36" alt="">` : '') : `<svg viewBox="0 0 24 24" width="30" height="30">${g}</svg>`;
    return `<div class="bub" data-i="${i}"><div class="bin" style="animation-delay:${750 + i * 60}ms;box-shadow:0 10px 26px ${c}33, inset 0 0 0 1px #fff">${art}</div></div>`;
  }).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><title>Tester CLI · LogiTestHub</title><style>
*{box-sizing:border-box;margin:0}
html,body{height:100%}
body{font-family:Manrope,"Segoe UI",system-ui,sans-serif;color:#141833;overflow:hidden;position:relative;
  background:radial-gradient(620px 440px at 4% 20%,rgba(150,170,255,.30),transparent 70%),
             radial-gradient(600px 460px at 98% 8%,rgba(240,160,235,.32),transparent 70%),
             radial-gradient(640px 420px at 96% 96%,rgba(255,196,170,.30),transparent 70%),
             radial-gradient(600px 420px at 2% 96%,rgba(205,170,255,.30),transparent 70%),#f3f2fc;}
.waves{position:absolute;inset:0;width:100%;height:100%;z-index:0;pointer-events:none}
.wrap{position:relative;z-index:1;height:100%;display:flex;flex-direction:column;align-items:center;padding:14px 50px 0}
.logo{position:relative;width:${LOGO_W}px;height:${lh}px;animation:pop .7s cubic-bezier(.2,.8,.2,1) both}
.logo img{position:absolute;inset:0;width:100%;height:100%;z-index:1}
.logo img.ball{inset:auto;left:0;top:0;width:${bw}px;height:${bw}px;z-index:2;will-change:transform}
.h1{margin-top:2px;font-size:46px;font-weight:800;letter-spacing:-.02em;line-height:1.05;animation:up .6s ease-out .35s both}
.h1 b{background:linear-gradient(90deg,#8b3cf6,#d93fd0 55%,#ff5f9e);-webkit-background-clip:text;background-clip:text;color:transparent}
.tag{margin-top:4px;font-size:16px;font-weight:500;color:#2c3150;animation:up .6s ease-out .5s both}
.tag b{font-weight:800;background:linear-gradient(90deg,#7b3ff2,#e0479e);-webkit-background-clip:text;background-clip:text;color:transparent}
.title{margin-top:8px;font-size:15.5px;color:#2c3150;max-width:1180px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;animation:up .6s ease-out .65s both}
.title b{color:#141833;font-weight:800}
.stage{flex:1;width:100%;max-width:1250px;display:grid;grid-template-columns:1.05fr 1fr;gap:30px;align-items:center;min-height:0}
.orbit{position:relative;height:100%;min-height:360px}
.ring{position:absolute;border-radius:50%;border:1.5px dashed rgba(140,120,240,.35);animation:fade .8s ease-out .7s both}
.ring.r2{border:1px solid rgba(255,255,255,.9);box-shadow:0 0 60px rgba(150,140,255,.25) inset}
.halo{position:absolute;border-radius:50%;background:radial-gradient(circle,rgba(255,255,255,.95) 0%,rgba(225,220,255,.6) 45%,transparent 70%);animation:fade 1s ease-out .55s both}
.term{position:absolute;z-index:2;width:228px;height:150px;animation:termIn .75s cubic-bezier(.2,.8,.2,1) .55s both}
.term .sheet{position:absolute;inset:0;border-radius:16px;background:linear-gradient(160deg,rgba(255,255,255,.85),rgba(210,215,255,.55));border:1px solid #fff;box-shadow:0 18px 40px rgba(80,70,180,.18)}
.term .s1{transform:translate(-16px,10px) rotate(-6deg);opacity:.7}
.term .s2{transform:translate(14px,-8px) rotate(5deg);opacity:.75}
.term .main{position:absolute;inset:0;border-radius:16px;background:linear-gradient(150deg,#26338f 0%,#1b2470 55%,#2b2f86 100%);
  border:3px solid rgba(255,255,255,.85);box-shadow:0 24px 50px rgba(40,40,140,.35),0 0 0 6px rgba(160,150,255,.18);overflow:hidden}
.term .dots{display:flex;gap:6px;padding:10px 12px 0}
.term .dots i{width:8px;height:8px;border-radius:50%}
.term .dots i:nth-child(1){background:#ff6f91}.term .dots i:nth-child(2){background:#ffc85c}.term .dots i:nth-child(3){background:#56d6a0}
.term .cmd{padding:16px 16px 0;font-size:25px;font-weight:700;color:#fff;letter-spacing:-.01em;white-space:nowrap;height:52px}
.term .cmd .pr{color:#7fd1ff;margin-right:8px}
.term .cmd .car{display:inline-block;width:3px;height:24px;background:#9fe0ff;margin-left:3px;vertical-align:-3px;animation:blink .8s steps(1) infinite}
.term .ln{height:5px;border-radius:3px;margin:7px 16px 0;transform-origin:left;transform:scaleX(0)}
.term .ln.go{transition:transform .45s cubic-bezier(.2,.8,.2,1);transform:scaleX(1)}
.bub{position:absolute;left:0;top:0;width:62px;height:62px;margin:-31px 0 0 -31px;will-change:transform}
.bin{width:100%;height:100%;border-radius:50%;display:grid;place-items:center;
  background:radial-gradient(circle at 35% 30%,#fff 0%,rgba(255,255,255,.92) 55%,rgba(235,232,255,.85) 100%);
  animation:pop .5s cubic-bezier(.2,.8,.2,1) both}
.spark{position:absolute;border-radius:50%;background:radial-gradient(circle,#c7b8ff,#8f7cf0);opacity:.7;animation:fade 1s ease-out 1s both}
.right{display:flex;flex-direction:column;gap:22px;padding-right:10px}
.steps{position:relative;padding-left:26px;border-left:2px solid #dcd7f3;margin-left:8px}
.st{position:relative;display:flex;gap:10px;margin:0 0 16px;animation:slide .55s ease-out both}
.st .dot{position:absolute;left:-34px;top:3px;width:14px;height:14px;border-radius:50%;background:#f3f2fc;border:2px solid #b9b3dd}
.st.on .dot{border:4px solid #7b3ff2;background:#fff;box-shadow:0 0 0 5px rgba(123,63,242,.16)}
.st b{display:block;font-size:15px;color:#7c80a0}
.st span{display:block;font-size:13px;color:#8a8fae;margin-top:2px}
.st.on b{color:#141833} .st.on span{color:#2c3150}
.more{font-size:12.5px;color:#7c80a0;margin-top:-6px;animation:slide .55s ease-out both}
.card{position:relative;border-radius:16px;background:rgba(255,255,255,.88);border:1px solid #ebe8f8;
  box-shadow:0 20px 50px rgba(80,70,170,.13);overflow:hidden;animation:rise .7s cubic-bezier(.2,.8,.2,1) 1.45s both}
.bar{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#f8f7fd;border-bottom:1px solid #eeecf8}
.bar i{width:10px;height:10px;border-radius:50%;display:inline-block}
.bar i:nth-child(1){background:#ff7b7b}.bar i:nth-child(2){background:#ffc85c}.bar i:nth-child(3){background:#56d6a0}
.url{flex:1;margin-left:8px;padding:5px 12px;border-radius:999px;background:#fff;border:1px solid #eeecf8;font-size:12px;color:#2c3150;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cbody{padding:20px 22px;display:flex;align-items:center;gap:16px}
.spin{width:42px;height:42px;border-radius:50%;border:3px solid #ece9fb;border-top-color:#7b3ff2;flex:none;animation:r 1s linear infinite}
.cbody b{display:block;font-size:15.5px;color:#141833}
.cbody span{display:block;font-size:12.5px;color:#4a4f72;margin-top:3px}
.pill{position:absolute;right:16px;bottom:16px;font-size:12px;font-weight:700;color:#fff;background:linear-gradient(90deg,#6d3df5,#b13fe0);border-radius:999px;padding:4px 12px}
.feats{width:100%;max-width:1180px;display:grid;grid-template-columns:repeat(5,1fr);padding:12px 20px 4px;margin-top:6px;
  border-top:1px solid rgba(220,215,245,.8);background:linear-gradient(180deg,rgba(255,255,255,.35),rgba(255,255,255,0))}
.f{display:flex;align-items:center;justify-content:center;gap:12px;font-size:13px;color:#2c3150;line-height:1.3;position:relative;animation:pop .45s cubic-bezier(.2,.8,.2,1) both}
.f+.f:before{content:"";position:absolute;left:0;top:8px;bottom:8px;width:1px;background:#e2def3}
.f i{width:40px;height:40px;border-radius:11px;display:grid;place-items:center;background:#fff;box-shadow:0 6px 14px rgba(80,70,170,.10)}
.pow{font-size:12.5px;color:#7c80a0;padding:6px 0 10px;animation:fade .6s ease-out 2.1s both}
@keyframes r{to{transform:rotate(360deg)}}
@keyframes blink{50%{opacity:0}}
@keyframes pop{from{opacity:0;transform:scale(.75)}to{opacity:1;transform:none}}
@keyframes up{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes slide{from{opacity:0;transform:translateX(-18px)}to{opacity:1;transform:none}}
@keyframes rise{from{opacity:0;transform:translateY(24px) scale(.97)}to{opacity:1;transform:none}}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes termIn{from{opacity:0;transform:translateY(16px) scale(.85)}to{opacity:1;transform:none}}
</style></head><body>
<svg class="waves" viewBox="0 0 1366 768" preserveAspectRatio="none" aria-hidden="true">
  <defs><filter id="bl" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="6"/></filter>
    <linearGradient id="wv" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity=".0"/><stop offset=".3" stop-color="#fff" stop-opacity=".9"/><stop offset="1" stop-color="#fff" stop-opacity=".2"/></linearGradient></defs>
  <path d="M-40 250 C 260 190, 420 420, 760 470 S 1220 560, 1420 520" fill="none" stroke="url(#wv)" stroke-width="26" filter="url(#bl)" opacity=".8"/>
  <path d="M-40 330 C 280 260, 460 520, 800 560 S 1240 640, 1420 600" fill="none" stroke="url(#wv)" stroke-width="8" filter="url(#bl)" opacity=".9"/>
  <path d="M-40 560 C 240 470, 520 690, 860 700 S 1260 650, 1420 700" fill="none" stroke="#e7d9ff" stroke-width="40" filter="url(#bl)" opacity=".45"/>
</svg>
<div class="wrap">
  <div class="logo">${logo ? `<img src="${logo}" alt="">` : ''}${ball ? `<img class="ball" id="ball" src="${ball}" alt="">` : ''}</div>
  <div class="h1">Tester <b>CLI</b></div>
  <div class="tag">Smarter Testing for <b>Stronger Products</b></div>
  <div class="title">Running test case: <b>${esc(title)}</b></div>
  <div class="stage">
    <div class="orbit" id="orbit">
      <div class="halo" id="halo"></div><div class="ring r2" id="ring2"></div><div class="ring" id="ring"></div>
      <span class="spark" style="width:9px;height:9px" data-p="0.12,-0.42"></span><span class="spark" style="width:12px;height:12px" data-p="0.48,-0.30"></span>
      <span class="spark" style="width:8px;height:8px" data-p="-0.47,0.36"></span><span class="spark" style="width:10px;height:10px" data-p="0.40,0.40"></span>
      <div class="term" id="term"><div class="sheet s1"></div><div class="sheet s2"></div>
        <div class="main"><div class="dots"><i></i><i></i><i></i></div>
          <div class="cmd"><span class="pr">&gt;</span><span id="typed"></span><span class="car"></span></div>
          <div class="ln" style="width:58%;background:linear-gradient(90deg,#59c9ff,#9d7bff)"></div>
          <div class="ln" style="width:44%;background:linear-gradient(90deg,#ff7fb6,#c07bff)"></div>
          <div class="ln" style="width:76%;background:linear-gradient(90deg,#59c9ff,#7f9cff)"></div>
          <div class="ln" style="width:36%;background:linear-gradient(90deg,#59c9ff,#59e0c4)"></div>
        </div></div>
      ${bubbles}
    </div>
    <div class="right">
      <div class="steps">${stepsHtml}</div>
      <div class="card">
        <div class="bar"><i></i><i></i><i></i><div class="url">${esc(url)}</div></div>
        <div class="cbody"><div class="spin"></div><div><b>Starting headless browser…</b><span>Recording the screen and every step for review.</span></div></div>
        <div class="pill">${esc(runLabel)}</div>
      </div>
    </div>
  </div>
  <div class="feats">${FEATS.map(([k, t, c], i) => `<div class="f" style="animation-delay:${1650 + i * 110}ms"><i>${svgLine(FEAT[k], c)}</i><div>${t}</div></div>`).join('')}</div>
  <div class="pow">Powered by LogiTestHub</div>
</div>
<script>
(function () {
  var t0 = performance.now();
  function seg(t, a, b) { return Math.max(0, Math.min(1, (t - a) / (b - a))); }
  function eo(x) { return 1 - Math.pow(1 - x, 3); }

  // 1. LMX ball: 1.5 laps round the swoosh (behind the letters on the top arc), resting next to the X
  var g = ${geo}, ball = document.getElementById('ball');
  var ct = Math.cos(g.tilt * Math.PI / 180), st = Math.sin(g.tilt * Math.PI / 180);
  function at(s) { var u = g.a * Math.cos(s), v = g.b * Math.sin(s); return [g.cx + u * ct - v * st, g.cy + u * st + v * ct]; }
  var hu = (g.home[0] - g.cx) * ct + (g.home[1] - g.cy) * st, hv = -(g.home[0] - g.cx) * st + (g.home[1] - g.cy) * ct;
  var sEnd = Math.atan2(hv / g.b, hu / g.a), e = at(sEnd);

  // 2. orbit stage geometry (centre of the left column)
  var orbit = document.getElementById('orbit'), term = document.getElementById('term');
  var bubs = [].slice.call(document.querySelectorAll('.bub')), sparks = [].slice.call(document.querySelectorAll('.spark'));
  var cx, cy, R;
  function layout() {
    var w = orbit.clientWidth, h = orbit.clientHeight;
    cx = w / 2; cy = h / 2; R = Math.min(w * 0.38, h * 0.44, 222);
    term.style.left = (cx - 114) + 'px'; term.style.top = (cy - 75) + 'px';
    [['halo', 0.78], ['ring2', 0.86], ['ring', 1.0]].forEach(function (p) {
      var el = document.getElementById(p[0]), r = R * p[1];
      el.style.width = el.style.height = (2 * r) + 'px'; el.style.left = (cx - r) + 'px'; el.style.top = (cy - r) + 'px';
    });
    sparks.forEach(function (s) { var p = s.dataset.p.split(','); s.style.left = (cx + R * 1.12 * p[0]) + 'px'; s.style.top = (cy + R * 1.12 * p[1]) + 'px'; });
  }
  layout(); window.addEventListener('resize', layout);

  // 3. "> Tester-CLI" types itself, then the code lines fill
  var word = 'Tester-CLI', typed = document.getElementById('typed'), lines = [].slice.call(document.querySelectorAll('.ln')), linesOn = 0;

  function frame(now) {
    var t = now - t0;
    if (ball) {
      var p = seg(t, 0, 1600), s = sEnd - 3 * Math.PI * (1 - eo(p)), q = at(s), snap = seg(t, 1200, 1600);
      var x = q[0] + (g.home[0] - e[0]) * snap, y = q[1] + (g.home[1] - e[1]) * snap;
      ball.style.transform = 'translate(' + ((x - g.r) * g.k) + 'px,' + ((y - g.r) * g.k) + 'px) scale(' + (1 + .15 * Math.sin(s) * (1 - snap)) + ')';
      ball.style.zIndex = Math.sin(s) < 0 && snap < .5 ? 0 : 2;
    }
    var n = Math.round(word.length * seg(t, 950, 1700));
    if (typed.textContent.length !== n) typed.textContent = word.slice(0, n);
    while (linesOn < lines.length && t > 1650 + linesOn * 140) lines[linesOn++].classList.add('go');
    // the icons: one fast turn while they arrive, then a slow drift (keeps moving through the run's first seconds)
    var spin = 2 * Math.PI * eo(seg(t, 700, 2500)) + t * 0.00018;
    bubs.forEach(function (b, i) {
      var a = -Math.PI / 2 + i * (2 * Math.PI / bubs.length) + spin;
      var depth = (Math.sin(a) + 1) / 2;   // 0 top .. 1 bottom
      b.style.transform = 'translate(' + (cx + R * Math.cos(a)) + 'px,' + (cy + R * 0.92 * Math.sin(a)) + 'px) scale(' + (0.92 + 0.12 * depth) + ')';
      b.style.zIndex = depth > 0.5 ? 3 : 1;
    });
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
</script></body></html>`;
}

// Shows the intro in the recorded page, lets the animation play into the video, then saves poster.png (settled frame).
async function showIntro(page, runDir, info, holdMs = 3000) {
  await page.setContent(introHtml(info), { waitUntil: 'load' });
  await page.waitForTimeout(Math.max(0, holdMs - 200));
  await page.screenshot({ path: path.join(runDir, 'poster.png') }).catch(() => {});
  await page.waitForTimeout(200);
}

module.exports = { introHtml, showIntro };
