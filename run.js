#!/usr/bin/env node
// Usage: node run.js <case_test.md> [--vars <variables.json>] [--headed]
// Runs a plain-English test case in headless Chromium, records a video, screenshots every
// step, writes events.ndjson + report.html under runs/<timestamp>-<slug>/.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { loadVars, fill, makeMasker } = require('./lib/vars');
const { parseStep, runAction, uiRuleCheck } = require('./lib/actions');
const { writeReport } = require('./lib/report');
const { parseCase } = require('./lib/testcase');
const { showIntro } = require('./lib/intro');
const { aiStep, explain, MODEL: AI_MODEL } = require('./lib/ai');

const DEFAULT_VARS = 'F:/TestMU-Ai/.testmuai/variables/etail.json';

function parseArgs(argv) {
  const a = { file: null, base: null, vars: DEFAULT_VARS, headed: false, ai: !!process.env.ANTHROPIC_API_KEY };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--vars') a.vars = argv[++i];
    else if (argv[i] === '--base') a.base = argv[++i];   // folder for @import paths (the case's original folder)
    else if (argv[i] === '--headed') a.headed = true;
    else if (argv[i] === '--no-ai') a.ai = false;
    else a.file = argv[i];
  }
  return a;
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  if (!args.file || !fs.existsSync(args.file)) {
    console.error('Usage: node run.js <case_test.md> [--base <folder for @import>] [--vars <variables.json>] [--headed]');
    process.exit(2);
  }
  const { vars, secretValues } = loadVars(args.vars);
  const mask = makeMasker(secretValues);
  let tc;
  try {
    tc = parseCase(fs.readFileSync(args.file, 'utf8'), args.base || path.dirname(path.resolve(args.file)));
  } catch (e) {
    console.error(e.message);   // e.g. "Helper not found: ../helpers/login.md" -> shown as the run's error
    process.exit(3);
  }

  const stamp = new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
  const slug = path.basename(args.file).replace(/_test\.md$|\.md$/, '').slice(0, 40);
  const runDir = path.join(__dirname, 'runs', `${stamp}-${slug}`);
  fs.mkdirSync(runDir, { recursive: true });
  const log = fs.createWriteStream(path.join(runDir, 'events.ndjson'));
  const emit = ev => { log.write(JSON.stringify(ev) + '\n'); };

  const started = Date.now();
  emit({ type: 'run_start', test: tc.title, file: args.file, at: new Date().toISOString() });

  const browser = await chromium.launch({ headless: !args.headed });
  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    recordVideo: { dir: runDir, size: { width: 1366, height: 768 } },
  });
  const page = await context.newPage();
  const videoStart = Date.now(); // step offsets below let the report seek the video to each step

  // Branded intro: first seconds of the video + poster.png (never shows secrets: only step text, masked).
  const firstNav = tc.steps.map(s => fill(s.text, vars).match(/^navigate to (.+?)\.?$/i)).find(Boolean);
  await showIntro(page, runDir, {
    title: tc.title,
    steps: tc.steps.map(s => mask(fill(s.text, vars))),
    url: firstNav ? firstNav[1].replace(/^https?:\/\//, '') : 'eTail · headless Chromium',
    runLabel: `${tc.steps.length} steps`,
  });

  const results = [];
  const aiTokens = { in: 0, out: 0, cacheRead: 0, cacheWrite: 0, calls: 0, model: AI_MODEL };   // AI usage report
  let failed = false;
  for (let i = 0; i < tc.steps.length; i++) {
    const s = tc.steps[i];
    const shown = mask(fill(s.text, vars));
    const r = { n: i + 1, section: s.section, text: shown, status: 'skipped', ms: 0, error: '', shot: '', warnings: [] };
    if (failed) { results.push(r); emit({ type: 'step', ...r }); continue; }

    const action = parseStep(fill(s.text, vars));
    const t0 = Date.now();
    r.at = Math.max(0, (t0 - videoStart) / 1000);
    if (action.needsAI) {
      if (!args.ai) {
        r.status = 'needs-ai';
      } else {
        try {
          const out = await aiStep(page, fill(s.text, vars));
          r.status = out.verdict === 'pass' ? 'passed' : 'failed';
          r.ai = mask(out.reason);
          if (r.status === 'failed') { r.error = r.ai; failed = true; }
          aiTokens.in += out.usage.input_tokens || 0;
          aiTokens.out += out.usage.output_tokens || 0;
          aiTokens.cacheRead += out.usage.cache_read_input_tokens || 0;
          aiTokens.cacheWrite += out.usage.cache_creation_input_tokens || 0;
          aiTokens.calls += 1;
        } catch (e) {
          r.status = 'needs-ai';
          r.ai = explain(e);
          if (/no API credits|API key invalid/.test(r.ai)) args.ai = false; // stop retrying every step
        }
      }
    } else {
      try {
        await runAction(page, action);
        r.status = 'passed';
        const bad = await uiRuleCheck(page);
        if (bad.length) r.warnings.push(`Page shows ${bad.join(', ')}`);
      } catch (e) {
        r.status = 'failed';
        r.error = mask(e.message.split('\n')[0]);
        failed = true;
      }
    }
    r.ms = Date.now() - t0;
    r.shot = `step-${String(r.n).padStart(2, '0')}.png`;
    await page.screenshot({ path: path.join(runDir, r.shot) }).catch(() => { r.shot = ''; });
    results.push(r);
    emit({ type: 'step', ...r });
    const note = r.error || (r.status === 'needs-ai' && r.ai) || '';
    console.log(`${String(r.n).padStart(2)}. [${r.status.toUpperCase()}] ${r.text}${note ? '  -> ' + note : ''}`);
  }

  const video = page.video();
  await context.close();
  await browser.close();
  let videoFile = '';
  if (video) {
    const vp = await video.path();
    videoFile = 'video.webm';
    fs.renameSync(vp, path.join(runDir, videoFile));
  }

  const needsAI = results.filter(r => r.status === 'needs-ai').length;
  const status = failed ? 'FAILED' : needsAI ? 'PARTIAL' : 'PASSED';
  const summary = {
    test: tc.title, file: args.file, status, durationMs: Date.now() - started,
    passed: results.filter(r => r.status === 'passed').length,
    failed: results.filter(r => r.status === 'failed').length,
    needsAI, skipped: results.filter(r => r.status === 'skipped').length,
    total: results.length, at: new Date().toISOString(), aiTokens,
  };
  emit({ type: 'run_end', ...summary });
  log.end();

  const report = writeReport(runDir, summary, results, videoFile);
  console.log('\n==============================================================');
  console.log(` RESULT : ${status}  (passed ${summary.passed}, failed ${summary.failed}, needs AI ${needsAI}, skipped ${summary.skipped})`);
  console.log(` REPORT : ${report}`);
  console.log('==============================================================');
  process.exitCode = failed ? 1 : 0;
})();
