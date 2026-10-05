/* Dark theme only: animated particle-cloud background in the style of motion.dev's AI Kit hero - tens of thousands
   of fine, randomly scattered dots lying on a slowly morphing 3D surface: big layered folds rising left and right
   and a peak behind the middle. Lit slopes glow blue, far / shadowed sides fall off to violet, a few dots sparkle,
   and the cloud dissolves towards the bottom of the screen.
   The surface is evaluated on a coarse grid each frame and every dot interpolates from it, so the dot count can be
   high without the maths growing with it.
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
  var W = 0, H = 0, raf = 0, running = false, t0 = performance.now(), last = 0;
  var FRAME_MS = 1000 / 24;                        // the motion is slow; 24 redraws a second keep it smooth and cheap

  var XMIN = -2.2, XMAX = 2.2;                     // surface extent across, depth is 0 (near) .. 1 (far)
  var GX = 220, GZ = 110;                          // surface grid
  var HG = new Float32Array((GX + 1) * (GZ + 1));

  // colour ramp: lit blue -> periwinkle -> violet -> deep purple (index 0 = brightest blue)
  var RAMP = [[66, 136, 255], [84, 128, 255], [104, 118, 255], [124, 108, 253], [142, 100, 249], [158, 94, 242], [150, 86, 220]];

  var N = 0, PX, PZ, PS, PK, PR;
  function seed(n) {
    N = n; PX = new Float32Array(n); PZ = new Float32Array(n); PS = new Float32Array(n);
    PK = new Float32Array(n); PR = new Uint8Array(n);
    for (var k = 0; k < n; k++) {
      PX[k] = XMIN + (XMAX - XMIN) * Math.random();
      PZ[k] = Math.random();
      var r = Math.random();
      PS[k] = 0.45 + r * r * r * r * 1.1;             // mostly fine dust, a few bigger grains
      PK[k] = Math.random();                       // keep-threshold: density follows the light
      PR[k] = Math.random() < 0.02 ? 1 : 0;        // sparkles
    }
  }

  function g(x, z, cx, cz, sx, sz) { var dx = (x - cx) / sx, dz = (z - cz) / sz; return Math.exp(-dx * dx - dz * dz); }

  // big folds left / right and a peak behind the middle, each carrying its own ridges, on a slow rolling swell
  function height(x, z, t) {
    var s1 = Math.sin(t * 0.19), s2 = Math.cos(t * 0.15), s3 = Math.sin(t * 0.12 + 1.3);
    var L = g(x, z, -1.10 + 0.10 * s1, 0.38 + 0.05 * s2, 0.55, 0.32);
    var R = g(x, z,  1.15 - 0.10 * s2, 0.44 + 0.05 * s1, 0.52, 0.34);
    var C = g(x, z,  0.05 + 0.08 * s3, 0.80, 0.52, 0.20);
    var F = g(x, z, -0.70 + 0.06 * s2, 0.22, 0.38, 0.12);   // inner fold in front of the left one
    var ridge = Math.sin(x * 3.4 + z * 7.0 + t * 0.35) * 0.5 + Math.sin(x * -2.2 + z * 9.5 - t * 0.28) * 0.5;
    return 1.05 * L + 0.95 * R + 1.30 * C + 0.45 * F
         + 0.20 * (L + R + 0.6 * C) * ridge
         + 0.10 * Math.sin(x * 1.8 + z * 1.3 + t * 0.30) * Math.cos(z * 2.9 - t * 0.24)
         + 0.04 * Math.sin(x * 6.1 - z * 5.0 + t * 0.50);
  }

  // Dots are written straight into a pixel buffer (no canvas paths): drawn at CSS-pixel resolution, so on a
  // high-DPI screen the browser's scaling gives each grain a slight soft glow.
  var img = null, px = null;
  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W; cv.height = H;
    img = ctx.createImageData(W, H); px = img.data;
    seed(Math.max(40000, Math.min(150000, Math.round(W * H / 13))));
  }

  // one grain: colour replaces, opacity accumulates (so dense lit areas build up a glow)
  function plot(x, y, r, gc, b, a) {
    if (x < 0 || y < 0 || x >= W || y >= H) return;
    var o = (y * W + x) << 2, na = px[o + 3] + a;
    px[o] = r; px[o + 1] = gc; px[o + 2] = b; px[o + 3] = na > 255 ? 255 : na;
  }

  function frame(now) {
    if (running && !still) raf = requestAnimationFrame(frame);
    if (!still && now - last < FRAME_MS) return;
    last = now;
    var t = (now - t0) / 1000, i, j, k;

    var dxg = (XMAX - XMIN) / GX, dzg = 1 / GZ;
    for (j = 0; j <= GZ; j++) for (i = 0; i <= GX; i++) HG[j * (GX + 1) + i] = height(XMIN + i * dxg, j * dzg, t);

    var cx = W / 2, horizon = H * 0.17, f = Math.min(W * 0.95, H * 1.9) * 0.56, camY = 1.25;
    var fadeTop = H * 0.40, fadeBot = H * 0.74;    // the cloud dissolves between these lines
    var rows = Math.min(H, Math.ceil(fadeBot) + 2); // nothing is drawn below fadeBot: clear / upload only above it
    px.fill(0, 0, rows * W * 4);
    var LX = -0.50, LZ = -0.80, LY = 0.40;         // light from the viewer's side, front-left
    var last_ramp = RAMP.length - 1;
    for (k = 0; k < N; k++) {
      var x = PX[k], z = PZ[k];
      var gx = (x - XMIN) / dxg, gz = z * GZ;
      var ix = gx | 0, iz = gz | 0; if (ix >= GX) ix = GX - 1; if (iz >= GZ) iz = GZ - 1;
      var fx = gx - ix, fz = gz - iz, o = iz * (GX + 1) + ix;
      var h00 = HG[o], h10 = HG[o + 1], h01 = HG[o + GX + 1], h11 = HG[o + GX + 2];
      var y = (h00 * (1 - fx) + h10 * fx) * (1 - fz) + (h01 * (1 - fx) + h11 * fx) * fz;

      var depth = 1.0 + z * 3.3;
      var sx = cx + x * f / depth;
      var sy = horizon + (camY - y) * f / depth * 0.80;
      if (sx < 0 || sx >= W || sy < 0 || sy > fadeBot) continue;

      var ddx = ((h10 - h00) * (1 - fz) + (h11 - h01) * fz) / dxg;
      var ddz = ((h01 - h00) * (1 - fx) + (h11 - h10) * fx) / dzg;
      var lit = (-ddx * LX - ddz * LZ + LY) / Math.sqrt(ddx * ddx + ddz * ddz + 1);
      lit = lit < 0.1 ? 0 : lit > 1 ? 1 : (lit - 0.1) / 0.9;
      var crest = y < 0 ? 0 : y > 0.9 ? 1 : y / 0.9;
      var shade = 0.6 * lit + 0.4 * crest;
      if (PK[k] > 0.32 + 0.68 * shade) continue;   // sparse in the shadows, dense where it is lit

      var ex = Math.abs(x) / 2.0, edge = ex >= 1 ? 0 : 1 - ex * ex * ex * ex;   // the far left / right stay dark
      var low = sy > fadeTop ? 1 - (sy - fadeTop) / (fadeBot - fadeTop) : 1;
      var a = (0.42 + 0.58 * shade) * (1 - z * 0.35) * edge * low * low;
      if (a < 0.04) continue;
      var size = PS[k] * (1.2 - z * 0.45), ixs = sx | 0, iys = sy | 0, r, gc, b;

      if (PR[k] && shade > 0.3) {                  // sparkles: pale, brighter, twinkling
        r = 200; gc = 220; b = 255; a = a * (0.75 + 0.45 * Math.sin(t * 2.4 + k)); size = 2;
      } else {
        var c = RAMP[Math.round(((1 - lit) * 0.7 + z * 0.3) * last_ramp)];
        r = c[0]; gc = c[1]; b = c[2];
      }
      var a8 = (a > 1 ? 1 : a) * 255 | 0;
      plot(ixs, iys, r, gc, b, a8);
      if (size >= 1.35) {                          // the bigger grains cover 2x2
        var a2 = a8 * 0.7 | 0;
        plot(ixs + 1, iys, r, gc, b, a2); plot(ixs, iys + 1, r, gc, b, a2); plot(ixs + 1, iys + 1, r, gc, b, a2 >> 1);
      }
    }
    ctx.putImageData(img, 0, 0, 0, 0, W, rows);
  }

  function isDark() { return root.getAttribute('data-bs-theme') === 'dark'; }
  function start() {
    if (running) return;
    running = true; cv.classList.add('on'); resize(); last = 0;
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    running = false; cancelAnimationFrame(raf);
    cv.classList.remove('on'); ctx.clearRect(0, 0, W, H);
  }
  function sync() { if (isDark() && !document.hidden) start(); else if (!isDark()) stop(); else { running = false; cancelAnimationFrame(raf); } }

  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ['data-bs-theme'] });
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('resize', function () { if (running) { resize(); last = 0; if (still) frame(performance.now()); } });
  sync();
})();
