/* Dark theme only: animated "network" background in the style of motion.dev's docs header - a band of points
   across the top of the screen, joined into a loose triangle mesh by hairline gold lines, rolling on a slow wave.
   A soft glow drifts along the band (lines are brighter there), the band thins out at its top and bottom rows,
   and a few stray points with single links float above it. Small lights travel along the lines from point to point
   (a few hops each, like signals through a network), some points twinkle, and the mesh lights up around the mouse.
   Runs only while <html data-bs-theme="dark">; stops (and clears) in light mode, pauses while the tab is hidden,
   and draws a single still frame when the OS asks for reduced motion. */
(function () {
  var root = document.documentElement;
  var cv = document.createElement('canvas');
  cv.className = 'bg-wave'; cv.setAttribute('aria-hidden', 'true');
  document.body.insertBefore(cv, document.body.firstChild);
  var ctx = cv.getContext('2d');
  if (!ctx) return;

  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var W = 0, H = 0, DPR = 1, raf = 0, running = false, t0 = performance.now(), last = 0;
  var FRAME_MS = 1000 / 30;
  var ROWS = 7, BUCKETS = 10;                       // lines are stroked in 10 opacity groups (one path each)

  var P = [], E = [], X, Y, B, gap = 60;            // points, edges (index pairs), per-frame x / y / brightness
  var ADJ = [], PULSES = [], MAXP = 9;              // neighbours of each point; the travelling lights
  var MX = -1e4, MY = -1e4, MR = 170;               // mouse position (CSS px) and the radius it lights up

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function build() {
    P = []; E = [];
    gap = Math.max(48, Math.min(84, W / 24));
    var cols = Math.ceil(W / gap) + 4, top = H * 0.07, band = H * 0.30, r, c;
    for (r = 0; r < ROWS; r++) for (c = 0; c < cols; c++) {
      P.push({ bx: (c - 2) * gap + rnd(-0.38, 0.38) * gap, by: top + band * r / (ROWS - 1) + rnd(-0.32, 0.32) * gap * 0.8,
        ph: rnd(0, 6.283), amp: rnd(3, 10), sp: rnd(0.18, 0.45), row: (r + 0.5) / ROWS, tw: Math.random() < 0.08 ? rnd(0.8, 1.6) : 0 });
    }
    function at(r, c) { return r * cols + c; }
    for (r = 0; r < ROWS; r++) for (c = 0; c < cols; c++) {   // right, down and one diagonal: a triangle mesh
      if (c + 1 < cols) E.push(at(r, c), at(r, c + 1));
      if (r + 1 < ROWS) {
        E.push(at(r, c), at(r + 1, c));
        if (Math.random() < 0.5) { if (c + 1 < cols) E.push(at(r, c), at(r + 1, c + 1)); }
        else if (c > 0) E.push(at(r, c), at(r + 1, c - 1));
      }
    }
    // stray points above the band, each linked to its nearest neighbour (sparse constellation)
    var first = P.length, n = Math.round(cols * 1.3), i, j;
    for (i = 0; i < n; i++) P.push({ bx: rnd(0, W), by: rnd(-gap * 0.3, top + gap * 0.4), ph: rnd(0, 6.283), amp: rnd(3, 9), sp: rnd(0.15, 0.4), row: -1 });
    for (i = first; i < P.length; i++) {
      var best = -1, bd = 1e9;
      for (j = 0; j < P.length; j++) {
        if (j === i || (P[j].row >= 0 && P[j].row > 0.2)) continue;   // strays link to strays or the band's top row
        var dx = P[j].bx - P[i].bx, dy = P[j].by - P[i].by, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = j; }
      }
      if (best >= 0 && bd < gap * gap * 4) E.push(i, best);
    }
    X = new Float32Array(P.length); Y = new Float32Array(P.length); B = new Float32Array(P.length);
    ADJ = P.map(function () { return []; });
    for (i = 0; i < E.length; i += 2) { ADJ[E[i]].push(E[i + 1]); ADJ[E[i + 1]].push(E[i]); }
    PULSES = [];
  }

  function resize() {
    W = window.innerWidth; H = window.innerHeight; DPR = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.round(W * DPR); cv.height = Math.round(H * DPR);
    build();
  }

  function frame(now) {
    if (running && !still) raf = requestAnimationFrame(frame);
    if (!still && now - last < FRAME_MS) return;
    last = now;
    var t = (now - t0) / 1000, i, k;
    var glowX = W * (0.55 + 0.30 * Math.sin(t * 0.045)), glowW = W * 0.30;
    for (i = 0; i < P.length; i++) {
      var p = P[i];
      var x = p.bx + Math.sin(t * p.sp + p.ph) * p.amp;
      var y = p.by + Math.cos(t * p.sp * 1.3 + p.ph) * p.amp * 0.6
            + Math.sin(p.bx * 0.0042 + t * 0.22) * H * 0.035 + Math.cos(p.bx * 0.0093 - t * 0.17) * H * 0.018;
      X[i] = x; Y[i] = y;
      var gx = (x - glowX) / glowW;
      var b = 0.28 + 0.72 * Math.exp(-gx * gx);                     // brighter where the glow is
      b *= p.row < 0 ? 0.45 : Math.pow(Math.sin(Math.PI * p.row), 0.7);   // band thins at its top / bottom rows
      var ex = Math.min(x, W - x) / (W * 0.06); if (ex < 1) b *= ex < 0 ? 0 : ex;   // soft left / right ends
      if (p.tw) b *= 1 + 0.9 * Math.max(0, Math.sin(t * p.tw + p.ph * 3));   // twinkle
      var mdx = x - MX, mdy = y - MY, md = mdx * mdx + mdy * mdy;
      if (md < MR * MR) { var mk = 1 - Math.sqrt(md) / MR; b += mk * mk * 0.9; }   // glow around the mouse
      B[i] = b > 1 ? 1 : b;
    }

    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.lineWidth = 0.7;
    var maxLen2 = gap * gap * 5.5;
    for (k = 1; k <= BUCKETS; k++) {                // one path per opacity group
      var lo = (k - 1) / BUCKETS, hi = k / BUCKETS;
      ctx.beginPath();
      for (i = 0; i < E.length; i += 2) {
        var a = E[i], c = E[i + 1], bb = Math.min(B[a], B[c]);
        if (bb <= lo || bb > hi) continue;
        var dx = X[a] - X[c], dy = Y[a] - Y[c];
        if (dx * dx + dy * dy > maxLen2) continue;
        ctx.moveTo(X[a], Y[a]); ctx.lineTo(X[c], Y[c]);
      }
      ctx.strokeStyle = 'rgba(214, 196, 98, ' + (hi * 0.55).toFixed(3) + ')';
      ctx.stroke();
    }
    for (k = 1; k <= BUCKETS; k++) {                // the points themselves, a touch brighter than the lines
      var lo2 = (k - 1) / BUCKETS, hi2 = k / BUCKETS;
      ctx.beginPath();
      for (i = 0; i < P.length; i++) {
        if (B[i] <= lo2 || B[i] > hi2) continue;
        ctx.moveTo(X[i] + 1.3, Y[i]); ctx.arc(X[i], Y[i], 1.3, 0, 6.283);
      }
      ctx.fillStyle = 'rgba(232, 214, 120, ' + (hi2 * 0.85).toFixed(3) + ')';
      ctx.fill();
    }
    pulses(t);
  }

  // travelling lights: start on a well-lit point, run along a line, then hop on to a neighbour (3-6 hops)
  function pulses(t) {
    if (!still && PULSES.length < MAXP && Math.random() < 0.06) {
      var s = (Math.random() * P.length) | 0;
      if (B[s] > 0.35 && ADJ[s].length) PULSES.push({ a: s, b: ADJ[s][(Math.random() * ADJ[s].length) | 0], t0: t, dur: rnd(0.9, 1.6), hops: 3 + ((Math.random() * 4) | 0) });
    }
    for (var i = PULSES.length - 1; i >= 0; i--) {
      var q = PULSES[i], k = (t - q.t0) / q.dur;
      if (k >= 1) {                                // arrived: hop on (not straight back), or fade out
        var nb = ADJ[q.b].filter(function (n) { return n !== q.a; });
        if (--q.hops <= 0 || !nb.length) { PULSES.splice(i, 1); continue; }
        q.a = q.b; q.b = nb[(Math.random() * nb.length) | 0]; q.t0 = t; k = 0;
      }
      var e = k * k * (3 - 2 * k), ax = X[q.a], ay = Y[q.a];
      var x = ax + (X[q.b] - ax) * e, y = ay + (Y[q.b] - ay) * e;
      var al = Math.min(1, q.hops / 2) * (0.35 + 0.65 * Math.max(B[q.a], B[q.b]));
      var tail = Math.max(0, e - 0.35);            // a short bright trail behind the light
      ctx.beginPath(); ctx.moveTo(ax + (X[q.b] - ax) * tail, ay + (Y[q.b] - ay) * tail); ctx.lineTo(x, y);
      ctx.strokeStyle = 'rgba(255, 232, 140, ' + (0.75 * al).toFixed(3) + ')'; ctx.lineWidth = 1.1; ctx.stroke();
      var gr = ctx.createRadialGradient(x, y, 0, x, y, 9);
      gr.addColorStop(0, 'rgba(255, 240, 170, ' + al.toFixed(3) + ')'); gr.addColorStop(1, 'rgba(255, 220, 110, 0)');
      ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(x, y, 9, 0, 6.283); ctx.fill();
    }
    ctx.lineWidth = 0.7;
  }

  function isDark() { return root.getAttribute('data-bs-theme') === 'dark'; }
  function start() {
    if (running) return;
    running = true; cv.classList.add('on'); resize(); last = 0;
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    running = false; cancelAnimationFrame(raf);
    cv.classList.remove('on'); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
  }
  function sync() { if (isDark() && !document.hidden) start(); else if (!isDark()) stop(); else { running = false; cancelAnimationFrame(raf); } }

  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['data-bs-theme'] });
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('mousemove', function (e) { MX = e.clientX; MY = e.clientY; }, { passive: true });
  document.addEventListener('mouseleave', function () { MX = MY = -1e4; });
  window.addEventListener('resize', function () { if (running) { resize(); last = 0; if (still) frame(performance.now()); } });
  sync();
})();
