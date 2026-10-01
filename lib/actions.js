// Turns one plain-English test line into a browser action.
// Known patterns run deterministically (no AI cost). Unknown lines return { needsAI: true };
// the AI agent step plugs in there once API credits are available.

const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
const unquote = s => String(s).trim().replace(/^['"]|['"]$/g, '');

const PATTERNS = [
  { re: /^navigate to (.+?)\.?$/i, kind: 'navigate', map: m => ({ url: unquote(m[1]) }) },
  { re: /^type (.+?) into the (.+?) (?:field|box|input)\.?$/i, kind: 'type', map: m => ({ value: unquote(m[1]), target: m[2] }) },
  { re: /^select (.+?) from the (.+?) dropdown\.?$/i, kind: 'select', map: m => ({ value: unquote(m[1]), target: m[2] }) },
  { re: /^click (?:the |on )?(.+?)(?: button| link| tab| menu)?\.?$/i, kind: 'click', map: m => ({ target: unquote(m[1]) }) },
  { re: /^assert the page title (is|contains) (.+?)\.?$/i, kind: 'assertTitle', map: m => ({ mode: m[1].toLowerCase(), value: unquote(m[2]) }) },
  { re: /^assert the current url contains (.+?)\.?$/i, kind: 'assertUrl', map: m => ({ value: unquote(m[1]) }) },
  { re: /^assert (?:the )?text (['"].+?['"]) is visible.*$/i, kind: 'assertText', map: m => ({ value: unquote(m[1]) }) },
  { re: /^assert (?:the page shows|the page contains) (.+?)\.?$/i, kind: 'assertText', map: m => ({ value: unquote(m[1]) }) },
  { re: /^wait (\d+) ?(?:s|sec|seconds?)\.?$/i, kind: 'wait', map: m => ({ ms: Number(m[1]) * 1000 }) },
];

// TestMU-Ai cases name the exact element: "Click the 'Save' button (#add_newcategory)." The "(#id)" is
// taken out of the sentence and used as the element to act on, so the words no longer have to match the page.
const ID_HINT = /\s*\((#[A-Za-z][\w-]*)\)/g;
const LITERAL = /^(['"]).*$|^\{\{\s*[\w.-]+\s*\}\}$|^\S+$/;   // 'text', {{var}} or one word; "a numeric HSN code" is a description

function parseStep(line) {
  const ids = [...line.matchAll(ID_HINT)].map(m => m[1]);
  if (ids.length > 1) return { kind: 'unknown', needsAI: true };   // "Metal (#a), Tax Group (#b) ..." = several actions
  const css = ids[0] || null;
  const text = css ? line.replace(ID_HINT, '').trim() : line;
  let m;
  if (css && (m = text.match(/^select the first (?:available )?option (?:in|from|of) the (.+?)\.?$/i))) return { kind: 'selectFirst', target: m[1], css };
  if (css && (m = text.match(/^assert (?:that )?(?:the )?(.+?) is (?:visible|shown|displayed)\.?$/i))) return { kind: 'assertVisible', target: m[1], css };
  for (const p of PATTERNS) {
    m = text.match(p.re);
    if (!m) continue;
    const step = { kind: p.kind, ...p.map(m) };
    if (!css) return step;
    if (!['click', 'type', 'select'].includes(step.kind)) break;
    if (step.kind === 'type' && !LITERAL.test(m[1].trim())) break;
    return { ...step, css };
  }
  return { kind: 'unknown', needsAI: true };
}

// The element named by "(#id)", when the step has one and it is on the page.
async function byCss(page, css) {
  if (!css) return null;
  const el = page.locator(css).first();
  try { await el.waitFor({ state: 'attached', timeout: 10000 }); } catch (_) { return null; }
  return el;
}

async function firstVisible(candidates) {
  for (const loc of candidates) {
    try {
      const n = await loc.count();
      for (let i = 0; i < n; i++) {
        const el = loc.nth(i);
        if (await el.isVisible()) return el;
      }
    } catch (_) { /* try next strategy */ }
  }
  return null;
}

// Finds an input by placeholder, label, then id/name that matches the words ("User Name" -> username).
async function findField(page, target) {
  const key = norm(target);
  return firstVisible([
    page.getByPlaceholder(target, { exact: false }),
    page.getByLabel(target, { exact: false }),
    page.locator(`input[id="${key}" i], input[name="${key}" i], textarea[id="${key}" i], textarea[name="${key}" i]`),
    page.locator(`input[id*="${key}" i], input[name*="${key}" i]`),
  ]);
}

// Select2-aware dropdown pick: eTail renders most selects through Select2, where the real
// <select> is hidden. Opens the widget, types the value, picks the matching option.
async function selectOption(page, target, value, css) {
  const select = await findSelect(page, target, css);
  if (!select) throw new Error(`Dropdown "${target}"${css ? ` (${css})` : ''} not found`);
  const container = select.locator('xpath=following-sibling::span[contains(@class,"select2")][1] | following-sibling::div[contains(@class,"select2")][1]');
  if (await container.count()) {
    await container.first().click();
    const search = page.locator('.select2-container--open .select2-search__field, .select2-drop-active input.select2-input').first();
    if (await search.count() && await search.isVisible()) await search.fill(value);
    const option = page.locator('.select2-results__option, .select2-result-label').filter({ hasText: value });
    const exact = option.filter({ hasText: new RegExp(`^\\s*${value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*$`, 'i') });
    const pick = (await exact.count()) ? exact.first() : option.first();
    await pick.waitFor({ state: 'visible', timeout: 10000 });
    await pick.click();
    return;
  }
  await select.selectOption({ label: value });
}

async function findSelect(page, target, css) {
  const hinted = await byCss(page, css);
  if (hinted) return hinted;
  const key = norm(target);
  const byLabel = page.locator('label').filter({ hasText: new RegExp(target.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i') });
  if (await byLabel.count()) {
    const forId = await byLabel.first().getAttribute('for');
    if (forId) {
      const s = page.locator(`select#${forId}`);
      if (await s.count()) return s.first();
    }
    const near = byLabel.first().locator('xpath=following::select[1]');
    if (await near.count()) return near.first();
  }
  const s = page.locator(`select[id*="${key}" i], select[name*="${key}" i]`);
  return (await s.count()) ? s.first() : null;
}

async function findClickable(page, target) {
  return firstVisible([
    page.getByRole('button', { name: target, exact: false }),
    page.getByRole('link', { name: target, exact: false }),
    page.locator(`input[type=submit][value*="${target}" i], input[type=button][value*="${target}" i]`),
    page.getByText(target, { exact: false }),
  ]);
}

async function runAction(page, step) {
  switch (step.kind) {
    case 'navigate':
      await page.goto(step.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      return;
    case 'type': {
      const el = (await byCss(page, step.css)) || await findField(page, step.target);
      if (!el) throw new Error(`Field "${step.target}"${step.css ? ` (${step.css})` : ''} not found`);
      await el.fill(step.value);
      return;
    }
    case 'select':
      return selectOption(page, step.target, step.value, step.css);
    case 'selectFirst': {
      // First real option (skips the empty "-- Select --"). Set on the <select> itself and fire change,
      // which also updates a Select2 widget and runs the page's own change handlers.
      const sel = await byCss(page, step.css);
      if (!sel) throw new Error(`Dropdown "${step.target}" (${step.css}) not found`);
      await retry(async () => (await sel.evaluate(el => [...(el.options || [])].some(o => o.value && !o.disabled))) || null);
      const picked = await sel.evaluate(el => {
        const o = [...(el.options || [])].find(x => x.value && !x.disabled);
        if (!o) return null;
        el.value = o.value;
        if (window.jQuery) window.jQuery(el).trigger('change'); else el.dispatchEvent(new Event('change', { bubbles: true }));
        return o.text.trim() || o.value;
      });
      if (picked === null) throw new Error(`Dropdown "${step.target}" (${step.css}) has no options to pick`);
      await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
      return;
    }
    case 'assertVisible': {
      const ok = await retry(async () => (await page.locator(step.css).first().isVisible()) || null);
      if (ok === null) throw new Error(`Expected ${step.target} (${step.css}) to be visible`);
      return;
    }
    case 'click': {
      const el = (await byCss(page, step.css)) || await findClickable(page, step.target);
      if (!el) throw new Error(`"${step.target}"${step.css ? ` (${step.css})` : ''} not found to click`);
      await el.click();
      await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      return;
    }
    case 'assertTitle': {
      // Case-insensitive: the dev server serves "Logimax | Login" while older cases say "LOGIMAX | Login".
      const v = step.value.toLowerCase();
      const title = await retry(async () => {
        const t = (await page.title()).toLowerCase();
        return (step.mode === 'is' ? t === v : t.includes(v)) ? t : null;
      });
      if (title === null) throw new Error(`Expected title ${step.mode} "${step.value}", got "${await page.title()}"`);
      return;
    }
    case 'assertUrl': {
      const ok = await retry(async () => page.url().includes(step.value) || null);
      if (ok === null) throw new Error(`Expected URL to contain "${step.value}", got "${page.url()}"`);
      return;
    }
    case 'assertText':
      await page.getByText(step.value, { exact: false }).first().waitFor({ state: 'visible', timeout: 10000 });
      return;
    case 'wait':
      await page.waitForTimeout(step.ms);
      return;
    default:
      throw new Error('Unsupported step');
  }
}

// Polls a check for up to `ms` (eTail pages redirect after AJAX, e.g. login), returns null on timeout.
async function retry(check, ms = 10000) {
  const end = Date.now() + ms;
  for (;;) {
    try { const v = await check(); if (v !== null) return v; } catch (_) { /* page navigating */ }
    if (Date.now() > end) return null;
    await new Promise(r => setTimeout(r, 300));
  }
}

// CLAUDE.md UI rule: NaN / undefined / Infinity must never reach the screen.
async function uiRuleCheck(page) {
  const text = await page.locator('body').innerText().catch(() => '');
  const hits = [];
  for (const bad of ['NaN', 'undefined', 'Infinity']) {
    if (new RegExp(`(^|[^A-Za-z])${bad}([^A-Za-z]|$)`).test(text)) hits.push(bad);
  }
  return hits;
}

module.exports = { parseStep, runAction, uiRuleCheck };
