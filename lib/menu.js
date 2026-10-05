// Reads any web application's navigation menu as modules -> sub-modules (no AI).
// Used by menu-crawl.js (LogiTestHub "Set up modules") and by tests/menu-regression.js.
//
//   readMenu({ baseUrl, vars, loginSteps, etailLogin, menuUrl, shot }) ->
//     { ok, stage, reason, warning, strategy, tried, title, url, modules: [{ name, link, subs: [{ name, link }] }] }
//
// Sign-in order: the Environment's own Login steps (if given) -> eTail's login steps -> any login form
// (user / email + password, also two-step "Next" forms). Then the sidebar / hamburger is opened, collapsed
// groups are expanded (toggles only: a page link is never opened) and six readers run; the best result wins:
//   nested-list (eTail / AdminLTE, React apps with wrapped sub-lists), toggle-panel (Bootstrap dropdowns and
//   collapses, aria-controls accordions, details/summary, Material expansion panels), aria-roles (menubar /
//   menu / tree), heading-groups (a label followed by links), click-dropdowns (menus that only exist after a
//   click), expand-groups (sidebar groups filled only on click), flat-links (last resort).
const fs = require('fs');
const path = require('path');
const { parseCase } = require('./testcase');
const { parseStep, runAction } = require('./actions');
const { fill } = require('./vars');

const NAV_WAIT = { waitUntil: 'domcontentloaded', timeout: 60000 };
const settle = page => page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
const visible = loc => loc.filter({ visible: true });

async function visiblePassword(page) { return visible(page.locator('input[type=password]')).count(); }

// ---------------------------------------------------------------- sign-in

async function runSteps(page, steps, vars, label) {
  for (const [i, s] of steps.entries()) {
    const a = parseStep(fill(s.text, vars));
    if (a.needsAI) throw new Error(`${label} step ${i + 1} is not a standard step (Navigate / Type ... into the X field / Select / Click / Assert ...): "${s.text}"`);
    await runAction(page, a);
  }
}

const USER_SEL = 'input[type=email], input[autocomplete=username], input[name*=user i], input[name*=email i], input[name*=login i], ' +
  'input[id*=user i], input[id*=email i], input[placeholder*=user i], input[placeholder*=email i], input[placeholder*=mobile i], input[type=text], input[type=tel], input:not([type])';
const SUBMIT_SEL = 'button[type=submit], input[type=submit], button:has-text("Sign in"), button:has-text("Log in"), button:has-text("Login"), ' +
  'button:has-text("Continue"), button:has-text("Next"), a:has-text("Sign in"), a:has-text("Login")';

async function submit(page, fallbackInput) {
  const btn = visible(page.locator(SUBMIT_SEL)).first();
  if (await btn.count()) await btn.click(); else if (fallbackInput) await fallbackInput.press('Enter');
  await settle(page);
}

// Any app's login form. Two-step forms (email first, "Next", then password) are handled too.
async function genericLogin(page, vars) {
  await page.goto(vars.base_url, NAV_WAIT);
  await settle(page);
  if (!(await visiblePassword(page))) {
    const user = visible(page.locator(USER_SEL)).first();
    const looksLikeLogin = /log ?in|sign ?in|account|password/i.test((await page.title()) + ' ' + await page.locator('body').innerText().catch(() => ''));
    if (!(await user.count()) || !looksLikeLogin || !vars.username) return;   // already inside (no login page)
    await user.fill(vars.username);
    await submit(page, user);
    for (let i = 0; i < 10 && !(await visiblePassword(page)); i++) await page.waitForTimeout(300);
    if (!(await visiblePassword(page))) return;
  }
  const pass = visible(page.locator('input[type=password]')).first();
  const user = visible(page.locator(USER_SEL)).first();
  if (vars.username && await user.count() && !(await user.inputValue().catch(() => ''))) await user.fill(vars.username);
  await pass.fill(vars.password || '');
  if (vars.branch) {   // optional branch / company picker on the login form
    const sel = visible(page.locator('select')).first();
    if (await sel.count()) await sel.selectOption({ label: vars.branch }).catch(() => {});
  }
  await submit(page, pass);
  for (let i = 0; i < 20 && await visiblePassword(page); i++) {   // SPA redirect after login; stop early on an error message
    if (await visible(page.locator('[role=alert], .alert, .error, .invalid-feedback, .text-danger')).count()) break;
    await page.waitForTimeout(500);
  }
}

// Why are we still on a sign-in page? Returns a plain-language reason, or null when signed in.
async function loginProblem(page) {
  const body = await page.locator('body').innerText().catch(() => '');
  const otp = await visible(page.locator('input[name*=otp i], input[id*=otp i], input[placeholder*=otp i], input[autocomplete=one-time-code], ' +
    'input[name*=verification i], input[placeholder*=verification i], input[name*="2fa" i]')).count();
  if (otp || /\b(otp|one[- ]time (password|code)|verification code)\b/i.test(body) && !(await visiblePassword(page)) && /enter|sent/i.test(body)) {
    return 'The application asks for an OTP / verification code after the password. Add Login steps to the Environment that handle it (or use a login that has no OTP).';
  }
  const captcha = await page.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], [class*=captcha i], img[src*=captcha i], [id*=captcha i]').count();
  if (await visiblePassword(page)) {
    if (captcha) return 'The login page has a CAPTCHA, which a robot cannot solve. Use "Type them", or a test login without CAPTCHA.';
    const msg = await page.locator('[role=alert], .alert, .error, .invalid-feedback, .text-danger, .toast, [class*=error i]')
      .evaluateAll(els => els.map(e => e.innerText.trim()).filter(t => t && t.length < 200)[0] || '').catch(() => '');
    return msg ? `The application refused the login: "${msg}". Check the Environment's username and password.`
               : 'Still on the login page after signing in. Check the Environment\'s URL, username and password, or add Login steps to the Environment.';
  }
  return null;
}

async function signIn(page, vars, { loginSteps, etailLogin }) {
  if (loginSteps && loginSteps.trim()) {
    const steps = parseCase('# login\n' + loginSteps).steps;
    await page.goto(vars.base_url, NAV_WAIT);
    await settle(page);
    await runSteps(page, steps, vars, 'Login');
    await settle(page);
    return 'custom';
  }
  if (etailLogin && fs.existsSync(etailLogin)) {
    try {
      const steps = parseCase(fs.readFileSync(etailLogin, 'utf8'), path.dirname(etailLogin)).steps.filter(s => !parseStep(fill(s.text, vars)).needsAI);
      // quick check: open the URL and compare the title the eTail steps expect, so other apps do not wait 10 s
      const a0 = steps[0] && parseStep(fill(steps[0].text, vars)), a1 = steps[1] && parseStep(fill(steps[1].text, vars));
      let fits = true;
      if (a0 && a0.kind === 'navigate' && a1 && a1.kind === 'assertTitle') {
        await page.goto(a0.url, NAV_WAIT);
        await settle(page);
        const t = (await page.title()).toLowerCase(), want = a1.value.toLowerCase();
        fits = a1.mode === 'is' ? t === want : t.includes(want);
      }
      if (fits) {
        await runSteps(page, steps, vars, 'eTail login');
        return 'etail';
      }
    } catch (_) { /* not an eTail login screen: try the generic login */ }
  }
  await genericLogin(page, vars);
  return 'generic';
}

// ---------------------------------------------------------------- open hidden menus (toggles only)

async function openMenus(page) {
  const labelled = await page.evaluate(() => [...document.querySelectorAll('aside a, nav a, [role=navigation] a')]
    .filter(a => a.textContent.trim() && a.offsetParent).length);
  if (labelled < 5) {   // icon-only / collapsed sidebar or a mobile hamburger
    const toggle = visible(page.locator('button[aria-label*=sidebar i], button[aria-label*=menu i], button[aria-label*=navigation i], ' +
      'button[title*=menu i], button[title*=sidebar i], .navbar-toggler, .sidebar-toggle, .menu-toggle, [data-toggle=push-menu], ' +
      '[data-widget=pushmenu], button.hamburger')).first();
    if (await toggle.count()) { await toggle.click().catch(() => {}); await page.waitForTimeout(800); }
  }
  // collapsed groups inside the navigation (aria-expanded=false); never inside the page content
  const closed = visible(page.locator('aside [aria-expanded=false], nav [aria-expanded=false], [role=navigation] [aria-expanded=false], ' +
    '.sidebar [aria-expanded=false]')).filter({ hasNot: page.locator('[href]:not([href="#"]):not([href^="javascript"])') });
  const n = Math.min(await closed.count(), 40);
  for (let i = 0; i < n; i++) {
    const el = closed.nth(0);
    const href = await el.getAttribute('href').catch(() => null);
    if (href && !/^(#|javascript:)/i.test(href)) break;   // a real page link: never follow it
    await el.click({ timeout: 2000 }).catch(() => {});
  }
  await page.evaluate(() => document.querySelectorAll('nav details, aside details, details').forEach(d => { d.open = true; }));
  if (n) await page.waitForTimeout(500);
}

// ---------------------------------------------------------------- readers (run inside the page)

function extractInPage() {
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const realHref = a => { const h = a && a.getAttribute && a.getAttribute('href'); return h && !/^(#|javascript:)/i.test(h.trim()) ? a.href : ''; };
  const isLink = a => a && a.matches && a.matches('a[href], [role=menuitem], [role=treeitem]') && !!realHref(a.closest('a[href]') || a);
  const textOf = el => {   // label text without badges like "New": own text first, else first labelled child
    if (!el) return '';
    const own = clean([...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
    if (own) return own;
    for (const c of el.children) { if (c.matches('svg, i, img, .badge, [class*=badge], [class*=count]')) continue; const t = clean(c.textContent); if (t) return t; }
    return clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent);
  };
  const linkItem = a => ({ name: textOf(a), link: realHref(a.closest('a[href]') || a) });
  const isBrand = a => !!(a.querySelector('h1, h2, img') || a.closest('[class*=brand i], [class*=logo i], .navbar-header'));
  const hasWords = t => /\p{L}{2,}/u.test(t);   // a real label, not an icon or emoji
  const navRoots = () => {
    const r = [...document.querySelectorAll('aside, nav, [role=navigation], header, .sidebar, .side-nav, .sidenav, .navbar, .menu, [class*=sidebar]')];
    return r.filter(el => !r.some(o => o !== el && o.contains(el)));   // outermost only
  };
  const out = {};

  // 1. nested-list: the nested list with the most links (eTail / AdminLTE; React apps wrap the sub-list in a div)
  {
    const ownText = li => {
      const a = li.querySelector(':scope > a, :scope > span, :scope > button, :scope > div > a, :scope > div > button, :scope > label');
      let t = textOf(a);
      if (!t) for (const n of li.childNodes) if (n.nodeType === 3) t += n.textContent;
      return clean(t);
    };
    const subList = li => li.querySelector(':scope > ul, :scope > ol') || li.querySelector(':scope > div ul, :scope > div ol');
    const lists = [...document.querySelectorAll('ul, ol')].filter(u => [...u.children].some(li => li.tagName === 'LI' && subList(li)));
    let best = null, bestN = 0;
    for (const u of lists) {
      const parentLi = u.parentElement && u.parentElement.closest('li');
      if (parentLi && lists.includes(parentLi.parentElement)) continue;   // a sub-list itself
      const n = u.querySelectorAll('a[href]').length;
      if (n > bestN) { best = u; bestN = n; }
    }
    const res = [];
    if (best) {
      for (const li of best.querySelectorAll(':scope > li')) {
        const name = ownText(li);
        if (!name) continue;
        const subs = [];
        const walk = (ul, prefix) => {
          for (const s of ul.querySelectorAll(':scope > li')) {
            const n = ownText(s);
            if (!n) continue;
            const inner = subList(s);
            if (inner) walk(inner, prefix + n + ' › ');
            else subs.push({ name: prefix + n, link: realHref(s.querySelector(':scope > a, a')) });
          }
        };
        const sub = subList(li);
        if (sub) walk(sub, '');
        res.push({ name, link: realHref(li.querySelector(':scope > a')), subs });
      }
    }
    out['nested-list'] = res;
  }

  // 2. toggle-panel: a toggle and the panel it opens
  {
    const pairs = [];
    const byId = id => id && document.getElementById(id.replace(/^#/, ''));
    document.querySelectorAll('[aria-controls]').forEach(t => { const p = byId(t.getAttribute('aria-controls')); if (p) pairs.push([t, p]); });
    document.querySelectorAll('[data-bs-target], [data-target], a[data-bs-toggle=collapse][href^="#"], a[data-toggle=collapse][href^="#"]').forEach(t => {
      const id = t.getAttribute('data-bs-target') || t.getAttribute('data-target') || t.getAttribute('href');
      const p = id && id.startsWith('#') && byId(id); if (p) pairs.push([t, p]);
    });
    document.querySelectorAll('.dropdown, .nav-item.dropdown, .dropdown-submenu').forEach(d => {
      const t = d.querySelector(':scope > .dropdown-toggle, :scope > a, :scope > button'), p = d.querySelector(':scope > .dropdown-menu');
      if (t && p) pairs.push([t, p]);
    });
    document.querySelectorAll('details').forEach(d => { const s = d.querySelector(':scope > summary'); if (s) pairs.push([s, d]); });
    document.querySelectorAll('mat-expansion-panel, .mat-expansion-panel, .mat-mdc-expansion-panel').forEach(p => {
      const h = p.querySelector('mat-expansion-panel-header, .mat-expansion-panel-header, .mat-mdc-expansion-panel-header'); if (h) pairs.push([h, p]);
    });
    const seen = new Set(), res = [];
    for (const [t, p] of pairs) {
      if (seen.has(p)) continue; seen.add(p);
      const name = textOf(t.querySelector('.mat-expansion-panel-header-title, .mat-content') || t);
      const links = [...p.querySelectorAll('a[href], [role=menuitem]')].filter(a => !t.contains(a) && isLink(a)).map(linkItem).filter(x => x.name);
      if (name && links.length && name.length <= 80) res.push({ name, link: '', subs: links });
    }
    out['toggle-panel'] = res;
  }

  // 3. aria-roles: menubar / menu / tree with menuitem / treeitem and nested groups
  {
    const res = [];
    const roots = [...document.querySelectorAll('[role=menubar], [role=tree], [role=navigation] [role=menu]')];
    for (const r of roots) {
      const items = [...r.querySelectorAll('[role=menuitem], [role=treeitem]')].filter(it => {
        const g = it.parentElement && it.parentElement.closest('[role=group], [role=menu], [role=tree], [role=menubar]');
        return g === r || (g && !r.contains(g.parentElement && g.parentElement.closest('[role=menuitem], [role=treeitem]')));
      });
      for (const it of items) {
        const grp = (it.getAttribute('aria-owns') && document.getElementById(it.getAttribute('aria-owns'))) ||
          it.querySelector('[role=group], [role=menu]') || (it.nextElementSibling && it.nextElementSibling.matches('[role=group], [role=menu]') ? it.nextElementSibling : null);
        const subs = grp ? [...grp.querySelectorAll('[role=menuitem], [role=treeitem], a[href]')].filter(isLink).map(linkItem).filter(x => x.name) : [];
        const label = it.querySelector(':scope > a, :scope > span, :scope > button') || it;
        const name = textOf(label);
        if (name) res.push({ name, link: realHref(it.closest('a[href]') || it.querySelector('a[href]')), subs });
      }
    }
    out['aria-roles'] = res;
  }

  // 4. heading-groups: a label (heading / caption without links) followed by links, inside the navigation
  {
    const res = [], groups = new Map();
    const isLabel = el => el && !el.querySelector('a[href], input, select, textarea') && !el.matches('a, input, select, textarea, svg, img, script, style, button') &&
      clean(el.textContent).length > 1 && clean(el.textContent).length <= 60 && hasWords(clean(el.textContent));
    for (const root of navRoots()) {
      for (const a of root.querySelectorAll('a[href]')) {
        if (!isLink(a) || !textOf(a) || isBrand(a)) continue;
        let node = a, label = null;
        outer: while (node && node !== root) {
          let sib = node.previousElementSibling;
          while (sib) {
            if (isLabel(sib)) { label = sib; break outer; }
            sib = sib.previousElementSibling;
          }
          node = node.parentElement;
        }
        const key = label || a;
        if (!groups.has(key)) groups.set(key, []);
        if (label) groups.get(key).push(linkItem(a));
      }
    }
    for (const [lab, links] of groups) {
      if (links.length) res.push({ name: clean(lab.textContent), link: '', subs: links });
      else res.push({ name: textOf(lab), link: realHref(lab), subs: [] });
    }
    out['heading-groups'] = res;
  }

  // 6. flat-links: every navigation link as a module (last resort)
  out['flat-links'] = navRoots().flatMap(r => [...r.querySelectorAll('a[href]')]).filter(a => isLink(a) && !isBrand(a)).map(a => ({ ...linkItem(a), subs: [] })).filter(x => x.name);
  return out;
}

// 5. click-dropdowns: top-bar menus whose items exist only after a click (React / Headless UI / Radix)
async function clickDropdowns(page) {
  const toggles = visible(page.locator('header [aria-haspopup]:not([aria-haspopup=false]), nav [aria-haspopup]:not([aria-haspopup=false]), ' +
    '[role=menubar] [aria-haspopup], header .dropdown-toggle, nav .dropdown-toggle'));
  const res = [];
  const n = Math.min(await toggles.count(), 25);
  for (let i = 0; i < n; i++) {
    const t = toggles.nth(i);
    const name = (await t.innerText().catch(() => '')).replace(/\s+/g, ' ').trim() || (await t.getAttribute('aria-label')) || '';
    const href = await t.getAttribute('href').catch(() => null);
    if (!name || name.length > 80 || (href && !/^(#|javascript:)/i.test(href))) continue;
    await t.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(400);
    const subs = await page.evaluate(() => {
      const open = [...document.querySelectorAll('[role=menu], [role=listbox], .dropdown-menu.show, [data-state=open], [data-headlessui-state~=open], .popover, [class*=menu][class*=open]')]
        .filter(m => m.offsetParent);
      const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
      return open.flatMap(m => [...m.querySelectorAll('a[href], [role=menuitem]')]).map(a => ({ name: clean(a.textContent), link: a.href || '' }))
        .filter(x => x.name);
    });
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(150);
    if (subs.length) res.push({ name, link: '', subs });
  }
  return res;
}

// 7. expand-groups: sidebar groups whose sub-pages are added to the page only when the group is clicked
//    (StarGold-style collapsed sidebars: <div class="sidebar-group"><button title="Masters">). Only buttons are
//    pressed, one by one; links are never followed; anything that looks like an action (logout, delete...) is skipped.
const UNSAFE = /log ?out|sign ?out|delete|remove|save|submit|reset|clear|close|cancel|theme|dark|light|refresh|toggle (sidebar|menu)/i;
async function expandGroups(page) {
  const btns = await page.locator('aside button, nav button, [class*=sidebar] button, aside [role=button], nav [role=button]').elementHandles();
  const res = [], seen = new Set();
  for (const h of btns.slice(0, 60)) {
    const info = await h.evaluate(el => {
      const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
      const txt = clean([...el.querySelectorAll('span, div')].map(x => x.childElementCount ? '' : x.textContent).join(' ')) || clean(el.textContent);
      const r = el.getBoundingClientRect();
      return { name: txt || clean(el.getAttribute('title') || el.getAttribute('aria-label') || el.getAttribute('data-bs-original-title') || el.getAttribute('data-tooltip')),
               shown: r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden', isSidebarToggle: /sidebar-toggle|menu-toggle|hamburger|navbar-toggler/i.test(el.className) };
    }).catch(() => null);
    if (!info || !info.shown || info.isSidebarToggle || !info.name || info.name.length > 60 || UNSAFE.test(info.name) || seen.has(info.name.toLowerCase())) continue;
    seen.add(info.name.toLowerCase());
    const before = new Set(await page.evaluate(() => [...document.querySelectorAll('a[href]')].filter(a => a.offsetParent || a.getClientRects().length).map(a => a.href + '|' + a.textContent.trim())));
    await h.click({ timeout: 2000 }).catch(() => {});
    await page.waitForTimeout(350);
    const subs = await h.evaluate((el, beforeList) => {
      const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
      const before = new Set(beforeList);
      const label = a => clean([...a.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ')) || clean(a.textContent) || clean(a.getAttribute('title'));
      const ok = a => !/^(#|javascript:)/i.test((a.getAttribute('href') || '').trim()) && !el.contains(a);
      const group = el.closest('li, [class*=group], [class*=item], [class*=menu]') || el.parentElement;
      let links = group ? [...group.querySelectorAll('a[href]')].filter(ok) : [];
      if (!links.length) {   // the sub-list was added elsewhere (a flyout / popover): take the links that just appeared
        links = [...document.querySelectorAll('a[href]')].filter(a => ok(a) && (a.offsetParent || a.getClientRects().length) && !before.has(a.href + '|' + a.textContent.trim()));
      }
      return links.map(a => ({ name: label(a), link: a.href })).filter(x => x.name);
    }, [...before]).catch(() => []);
    if (subs.length) res.push({ name: info.name, link: '', subs });
  }
  return res;
}

// ---------------------------------------------------------------- pick the best reading

const JUNK = /^(log ?out|sign ?out|logout|profile|my profile|help|toggle (sidebar|navigation|menu)|menu|search|notifications?|close)$/i;   // top-level utility items
const JUNK_SUB = /^(log ?out|sign ?out|logout)$/i;   // a sub-module named "Profile" or "Notification" is a real page (eTail Settings)

function tidy(mods) {
  const merged = new Map();
  for (const m of mods || []) {
    const name = String(m.name || '').replace(/\s+/g, ' ').trim();
    if (!name || name.length > 120 || JUNK.test(name)) continue;
    if (!m.link && !(m.subs || []).length) continue;   // a section caption like "MAIN NAVIGATION": no page, no sub-items
    const key = name.toLowerCase();
    if (!merged.has(key)) merged.set(key, { name, link: m.link || '', subs: [] });
    const t = merged.get(key);
    if (!t.link && m.link) t.link = m.link;
    const have = new Set(t.subs.map(s => s.name.toLowerCase()));
    for (const s of m.subs || []) {
      const sn = String(s.name || '').replace(/\s+/g, ' ').trim();
      if (sn && sn.length <= 160 && !JUNK_SUB.test(sn) && !have.has(sn.toLowerCase())) { t.subs.push({ name: sn, link: s.link || '' }); have.add(sn.toLowerCase()); }
    }
  }
  return [...merged.values()];
}

function score(mods) {
  const withSubs = mods.filter(m => m.subs.length).length, subs = mods.reduce((a, m) => a + m.subs.length, 0);
  return withSubs * 10 + subs + Math.min(mods.length, 20) * 0.5;
}

async function extract(page) {
  const raw = await page.evaluate(extractInPage);
  const cands = Object.entries(raw).map(([k, v]) => [k, tidy(v)]);
  const STRUCTURAL = ['nested-list', 'toggle-panel', 'aria-roles'];   // real menu markup: trusted over a guess from headings
  const rank = list => list.sort((a, b) => score(b[1]) - score(a[1]))[0];
  const structural = rank(cands.filter(([k]) => STRUCTURAL.includes(k)));
  const strongStructure = structural && structural[1].filter(m => m.subs.length).length >= 2;
  let best = strongStructure ? structural : rank(cands.filter(([k]) => k !== 'flat-links'));
  if (!strongStructure) {   // no real menu markup: menus may exist only after a click - try those readers too, keep the best
    const clicked = tidy(await clickDropdowns(page));
    cands.push(['click-dropdowns', clicked]);
    if (score(clicked) > score(best ? best[1] : [])) best = ['click-dropdowns', clicked];
    const groups = await expandGroups(page);
    // pages directly in the sidebar (Dashboard, Rate Panel...) stay as modules next to the groups
    const direct = (raw['flat-links'] || []).filter(l => !groups.some(g => g.subs.some(x => x.link === l.link)));
    const expanded = tidy([...groups, ...direct]);
    cands.push(['expand-groups', expanded]);
    if (score(expanded) > score(best ? best[1] : [])) best = ['expand-groups', expanded];
  }
  if (!best || !best[1].length) best = cands.find(([k]) => k === 'flat-links');
  const tried = Object.fromEntries(cands.map(([k, v]) => [k, { modules: v.length, subs: v.reduce((a, m) => a + m.subs.length, 0) }]));
  return { strategy: best ? best[0] : null, modules: best ? best[1] : [], tried };
}

// ---------------------------------------------------------------- whole run

async function readMenu(page, { vars, loginSteps, etailLogin, menuUrl, mask = s => s }) {
  const r = { ok: false, stage: 'open', modules: [] };
  try {
    r.login = await signIn(page, vars, { loginSteps, etailLogin });
  } catch (e) {
    r.stage = 'login';
    r.reason = /net::ERR_|page\.goto: /i.test(e.message)
      ? `Could not open ${vars.base_url}: ${String(e.message).split('\n')[0]}` : String(e.message).split('\n')[0];
    return r;
  }
  r.stage = 'login';
  const problem = await loginProblem(page);
  if (problem) { r.reason = problem; return r; }
  r.stage = 'menu';
  if (menuUrl) {
    const u = /^https?:/i.test(menuUrl) ? menuUrl : new URL(fill(menuUrl, vars).replace(/^\//, ''), vars.base_url).href;
    try { await page.goto(fill(u, vars), NAV_WAIT); } catch (e) { r.reason = `Could not open the Menu page URL ${u}.`; return r; }
  }
  await settle(page);
  await openMenus(page);
  const x = await extract(page);
  Object.assign(r, x, { title: mask(await page.title()), url: mask(page.url()) });
  r.modules = x.modules.map(m => ({ name: mask(m.name), link: mask(m.link), subs: m.subs.map(s => ({ name: mask(s.name), link: mask(s.link) })) }));
  const withSubs = r.modules.filter(m => m.subs.length).length;
  if (!r.modules.length) {
    r.reason = `Signed in, but no menu was found on ${r.url}. If the menu is on another page, set the Menu page URL; otherwise use "Type them".`;
    return r;
  }
  r.ok = true;
  if (!withSubs) r.warning = `Only top-level items were found (no sub-menus) on ${r.url}. If sub-menus open on another page, set the Menu page URL.`;
  else if (r.modules.length < 2) r.warning = 'Only one module was found. Check the screenshot: the menu may be partly hidden.';
  return r;
}

module.exports = { readMenu, signIn, genericLogin, loginProblem, openMenus, extract, tidy };
