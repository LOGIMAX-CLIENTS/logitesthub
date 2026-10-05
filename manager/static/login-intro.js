/* Sign-in intro (4 s): logo -> laptop -> icons orbit the laptop -> settle -> the normal sign-in page.
   The logo, laptop and icons are not separate elements on the page: they are parts of the sign-in picture
   (.lg-hero-img). The intro shows those same parts, cut out of the picture actually shown (so the remembered
   accent's version too), in an overlay above the page. Every part ends exactly on its place in the picture, then
   the overlay fades out: the page underneath is never changed. The overlay ignores the mouse, so the form works
   at once; a key press or click ends the intro early. Skipped for "reduce motion" users. */
(function () {
  'use strict';
  var DURATION = 4000;   // the timeline below is written for 4 s ...
  var SECONDS = 3;       // ... and plays in this many seconds (4 = the original speed)
  var img = document.querySelector('.lg-hero-img');
  function unhide() { document.documentElement.classList.remove('li-pending'); }
  if (!img || !window.requestAnimationFrame) return unhide();
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return unhide();

  // parts of the picture, in the picture's own pixels (765 x 822)
  var NAT_W = 765, NAT_H = 822;
  var LOGO = [247, 54, 547, 160];   // LMX logo as placed in the picture (300 x 106)
  // The LMX ball travels round the logo's swoosh and settles next to the X. The intro shows the logo file without its
  // ball (transparent, so no box around it) plus the ball on its own; both end exactly where the picture has them.
  var STATIC = document.currentScript ? document.currentScript.src.replace(/[^/]*$/, '') : '/static/';
  var LOGO_IMG = STATIC + 'logo-lmx-noball.png', BALL_IMG = STATIC + 'logo-lmx-ball.png';
  var LOGO_FILE = [1643, 581];                 // logo-lmx.png pixels
  var SWOOSH = { cx: 822, cy: 290, a: 833, b: 205, tilt: -12.8 * Math.PI / 180 };   // the swoosh oval, logo-file px
  var BALL_HOME = [1595, 68], BALL_FILE = 112;  // ball centre in the logo file; ball sprite size
  var BALL = [247 + (1595 - 56) * 300 / 1643, 54 + (68 - 56) * 106 / 581, 247 + (1595 + 56) * 300 / 1643, 54 + (68 + 56) * 106 / 581];
  var LAPTOP = [40, 488, 600, 790];
  var ICONS = [   // Test Cases, Test Runs, Insights, Better Products, Code, Chart
    [188, 390, 262, 460], [320, 390, 392, 460], [450, 390, 522, 460], [594, 390, 666, 460],
    [560, 530, 652, 622], [638, 628, 726, 722]];
  var ORBIT_C = [330, 640];        // laptop centre
  var ORBIT_R = [330, 150];        // ellipse radii (flat orbit = depth)

  var overlay = document.createElement('div');
  overlay.className = 'login-intro-animation';
  overlay.setAttribute('aria-hidden', 'true');
  document.body.appendChild(overlay);
  document.documentElement.classList.remove('li-pending');   // the overlay covers the page now
  // the page was hidden for a moment, so the browser skipped autofocus: focus that field as it would have been
  var af = document.querySelector('[autofocus]');
  if (af && (document.activeElement === document.body || !document.activeElement)) af.focus({ preventScroll: true });
  var done = false, raf = 0, t0 = 0, box, parts;

  function piece(r, cls, own) {   // own: a separate image file that fills the part (else: cut from the picture)
    var d = document.createElement('div');
    d.className = 'li-part ' + cls;
    overlay.appendChild(d);
    return { el: d, r: r, own: own };
  }

  function layout() {   // place every part on top of its spot in the picture as it is shown now
    var b = img.getBoundingClientRect();
    if (!b.width || !b.height) return false;
    box = { x: b.left, y: b.top, s: b.width / NAT_W, sy: b.height / NAT_H };
    var src = 'url("' + (img.currentSrc || img.src) + '")';
    parts.forEach(function (p) {
      var r = p.r, w = (r[2] - r[0]) * box.s, h = (r[3] - r[1]) * box.sy;
      p.w = w; p.h = h;
      p.hx = box.x + r[0] * box.s; p.hy = box.y + r[1] * box.sy;   // home = place in the picture
      var st = p.el.style;
      st.width = w + 'px'; st.height = h + 'px';
      if (p.own) {
        st.backgroundImage = 'url("' + p.own + '")'; st.backgroundSize = '100% 100%'; st.backgroundPosition = '0 0';
      } else {
        st.backgroundImage = src;
        st.backgroundSize = b.width + 'px ' + b.height + 'px';
        st.backgroundPosition = (-r[0] * box.s) + 'px ' + (-r[1] * box.sy) + 'px';
      }
    });
    return true;
  }

  function clamp(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function seg(t, a, b) { return clamp((t - a) / (b - a)); }
  function easeOut(x) { return 1 - Math.pow(1 - x, 3); }
  function easeInOut(x) { return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function put(p, x, y, sc, op, z) {
    p.el.style.transform = 'translate3d(' + x + 'px,' + y + 'px,0) scale(' + sc + ')';
    p.el.style.opacity = op;
    if (z !== undefined) p.el.style.zIndex = z;
  }

  function frame(now) {
    if (done) return;
    if (!t0) t0 = now;
    var t = (now - t0) * (DURATION / 1000 / SECONDS);
    var vw = window.innerWidth, vh = window.innerHeight;

    // 0-1 s: logo fades / scales in at the centre; 1-2 s: it glides to its place in the picture
    var logo = parts[0];
    var lIn = easeOut(seg(t, 0, 900)), lMove = easeInOut(seg(t, 1000, 2000));
    var big = Math.min(1.7, (vw * 0.42) / logo.w);
    var cx = (vw - logo.w) / 2, cy = (vh - logo.h) / 2 - vh * 0.06;
    var lx = cx + (logo.hx - cx) * lMove, ly = cy + (logo.hy - cy) * lMove, lsc = (0.7 + 0.3 * lIn) * (big + (1 - big) * lMove);
    put(logo, lx, ly, lsc, lIn, 1);

    // the ball: 1.5 laps round the swoosh (behind the letters on the top arc, in front on the bottom), slowing down,
    // ending on its place next to the X
    var ball = parts[parts.length - 1];
    var sw = SWOOSH, ct = Math.cos(sw.tilt), st = Math.sin(sw.tilt);
    function onSwoosh(s) {   // logo-file px
      var u = sw.a * Math.cos(s), v = sw.b * Math.sin(s);
      return [sw.cx + u * ct - v * st, sw.cy + u * st + v * ct];
    }
    var hu = (BALL_HOME[0] - sw.cx) * ct + (BALL_HOME[1] - sw.cy) * st, hv = -(BALL_HOME[0] - sw.cx) * st + (BALL_HOME[1] - sw.cy) * ct;
    var sEnd = Math.atan2(hv / sw.b, hu / sw.a);
    var bs = sEnd - 3 * Math.PI * (1 - easeOut(seg(t, 150, 2000)));
    var q = onSwoosh(bs), qe = onSwoosh(sEnd), snap = easeInOut(seg(t, 1500, 2000));
    var fx = q[0] + (BALL_HOME[0] - qe[0]) * snap, fy = q[1] + (BALL_HOME[1] - qe[1]) * snap;   // the oval is close; land exactly
    var kx = logo.w / LOGO_FILE[0], ky = logo.h / LOGO_FILE[1];
    var bx = lx + logo.w / 2 + (fx * kx - logo.w / 2) * lsc, by = ly + logo.h / 2 + (fy * ky - logo.h / 2) * lsc;
    var back = Math.sin(bs) < 0;   // top arc = behind the letters
    put(ball, bx - ball.w / 2, by - ball.h / 2, lsc * (1 + 0.12 * Math.sin(bs) * (1 - snap)), lIn, back && snap < 0.5 ? 0 : 2);

    // 1-2 s: the laptop rises in, then stays still
    var lap = parts[1], k = easeOut(seg(t, 1000, 1900));
    put(lap, lap.hx, lap.hy + (1 - k) * 28 * box.s, 0.96 + 0.04 * k, k, 2);

    // 2-3 s: icons leave their places and orbit the laptop (360 deg); 3-4 s: they slow down and settle back
    var ox = box.x + ORBIT_C[0] * box.s, oy = box.y + ORBIT_C[1] * box.sy;
    var rx = ORBIT_R[0] * box.s, ry = ORBIT_R[1] * box.sy;
    var spin = 2 * Math.PI * seg(t, 2000, 3000) + (Math.PI / 2) * easeOut(seg(t, 3000, 3700));
    var w = easeInOut(seg(t, 1900, 2300)) * (1 - easeInOut(seg(t, 3300, 3800)));   // 0 = home, 1 = on the orbit
    var show = easeOut(seg(t, 1800, 2200));
    for (var i = 2; i < 2 + ICONS.length; i++) {
      var p = parts[i], a = (i - 2) * (2 * Math.PI / ICONS.length) - Math.PI / 2 + spin;
      var depth = Math.sin(a);                       // -1 behind the laptop .. 1 in front
      var px = ox + rx * Math.cos(a) - p.w / 2, py = oy + ry * depth - p.h / 2;
      var sc = 1 + 0.12 * depth * w;
      put(p, p.hx + (px - p.hx) * w, p.hy + (py - p.hy) * w, sc, show * (1 - 0.35 * w * (depth < 0 ? -depth : 0)),
          depth < 0 && w > 0.5 ? 1 : 3);
      p.el.classList.toggle('li-moving', w > 0.05);
    }

    // 3.6-4 s: the overlay fades away: the real page (same picture, same places) is underneath
    overlay.style.opacity = 1 - easeInOut(seg(t, 3600, DURATION));
    if (t >= DURATION) return finish();
    raf = requestAnimationFrame(frame);
  }

  function finish(fast) {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf);
    window.removeEventListener('keydown', skip, true);
    window.removeEventListener('pointerdown', skip, true);
    window.removeEventListener('resize', onResize);
    if (fast) {
      overlay.style.transition = 'opacity .2s ease';
      overlay.style.opacity = '0';
      setTimeout(function () { overlay.remove(); }, 220);
    } else {
      overlay.remove();
    }
  }
  function skip() { finish(true); }
  function onResize() { if (!layout()) finish(true); }

  function start() {
    if (done) return;
    try {
      parts = [piece(LOGO, 'li-logo', LOGO_IMG), piece(LAPTOP, 'li-laptop')].concat(ICONS.map(function (r) { return piece(r, 'li-icon'); }),
                                                                              [piece(BALL, 'li-ball', BALL_IMG)]);
      if (!layout()) return finish();
      overlay.classList.add('li-on');
      window.addEventListener('keydown', skip, true);
      window.addEventListener('pointerdown', skip, true);
      window.addEventListener('resize', onResize);
      raf = requestAnimationFrame(frame);
    } catch (e) {
      finish();   // never leave a cover over the sign-in form
    }
  }

  // the cover goes up at once (no flash of the page), the parts move once the picture is ready
  var wait = setTimeout(function () { finish(true); }, 2500);   // picture too slow: just show the page
  function load(u) { var i = new Image(); i.src = u; return i.decode ? i.decode() : Promise.resolve(); }
  var ready = Promise.all([img.decode ? img.decode() : Promise.resolve(), load(LOGO_IMG), load(BALL_IMG)]);
  ready.then(function () { clearTimeout(wait); start(); }, function () { clearTimeout(wait); finish(true); });
})();
