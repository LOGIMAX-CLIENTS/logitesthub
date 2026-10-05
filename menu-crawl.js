#!/usr/bin/env node
// Reads an application's navigation menu as modules -> sub-modules for LogiTestHub "Set up modules" (no AI).
// All the logic is in lib/menu.js (shared with tests/menu-regression.js).
// Usage: node menu-crawl.js [--vars <variables.json>] [--login-steps <file>] [--url <menu page>] [--shot <png>]
// Prints JSON { ok, stage, reason, warning, strategy, tried, title, url, login, modules: [{ name, link, subs }] }.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { readMenu } = require('./lib/menu');
const { loadVars, makeMasker } = require('./lib/vars');

const arg = name => { const i = process.argv.indexOf(name); return i > -1 ? process.argv[i + 1] : undefined; };
const HELPERS = process.env.LTH_HELPERS_DIR || 'F:/TestMU-Ai/.testmuai/tests/Retail/etail-lot-inward/helpers';

(async () => {
  const varsFile = arg('--vars') || process.env.LTH_VARS_FILE || 'F:/TestMU-Ai/.testmuai/variables/etail.json';
  const shot = arg('--shot');
  let browser, page, mask = s => s, result;
  try {
    const { vars, secretValues } = loadVars(varsFile);
    mask = makeMasker(secretValues);
    const stepsFile = arg('--login-steps');
    browser = await chromium.launch({ headless: true });
    page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
    result = await readMenu(page, {
      vars, mask, menuUrl: arg('--url'),
      loginSteps: stepsFile && fs.existsSync(stepsFile) ? fs.readFileSync(stepsFile, 'utf8') : '',
      etailLogin: arg('--login') || path.join(HELPERS, 'login.md'),
    });
  } catch (e) {
    result = { ok: false, stage: 'open', reason: String(e && e.message || e).split('\n')[0].slice(0, 300), modules: [] };
  }
  if (shot && page) {
    try { await page.screenshot({ path: shot }); result.shot = path.basename(shot); } catch (_) { /* page closed */ }
  }
  if (browser) await browser.close().catch(() => {});
  result.reason = result.reason && mask(result.reason);
  process.stdout.write(JSON.stringify(result));
})();
