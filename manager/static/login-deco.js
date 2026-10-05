/* Sign-in page (version 1): puts the decoration (static/style.css .lg-deco) into the empty space left and right of the
   sign-in box. Hidden when that space is too narrow; recalculated on resize. */
(function () {
  'use strict';
  var deco = document.querySelector('.lg-deco'), shell = document.querySelector('.lg-shell');
  if (!deco || !shell) return;
  document.body.insertBefore(deco, document.body.firstChild);   // page level, behind the sign-in box (not inside <main>)
  var MIN = 70;   // px of free space needed on each side (the 44 px tiles fit)
  function place() {
    var r = shell.getBoundingClientRect(), vw = document.documentElement.clientWidth;
    var free = Math.min(r.left, vw - r.right);
    deco.classList.toggle('on', free >= MIN && window.innerWidth >= 1100);
    deco.style.setProperty('--free', free + 'px');
    deco.style.setProperty('--mid', (r.top + r.height / 2 + window.scrollY) + 'px');
  }
  place();
  window.addEventListener('resize', place);
  window.addEventListener('load', place);
})();
