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
const { aiStep, healStep, listElements, explain, MODEL: AI_MODEL } = require('./lib/ai');
const dbsteps = require('./lib/dbsteps');

const DEFAULT_VARS = process.env.LTH_VARS_FILE || 'tests/variables/variables.json';   // Settings -> System

function parseArgs(argv) {
  const a = { file: null, base: null, vars: DEFAULT_VARS, headed: false, ai: !!(process.env.ANTHROPIC_API_KEY || process.env.LTH_SERVER) };   // LTH_SERVER: AI via the LogiTestHub server (lth CLI)
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--vars') a.vars = argv[++i];
    else if (argv[i] === '--base') a.base = argv[++i];   // folder for @import paths (the case's original folder)
    else if (argv[i] === '--headed') a.headed = true;
    else if (argv[i] === '--no-ai') a.ai = false;
    else if (argv[i] === '--login') a.login = argv[++i];   // project login helper file, put before the case's steps
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
  for (const [k, v] of Object.entries(await dbsteps.loadMemory())) if (!(k in vars) && v != null) vars[k] = String(v);   // {{last_lot}} from an earlier test
  const mask = makeMasker(secretValues);
  let tc;
  try {
    const loginText = args.login && fs.existsSync(args.login) ? fs.readFileSync(args.login, 'utf8') : '';
    tc = parseCase(fs.readFileSync(args.file, 'utf8'), args.base || path.dirname(path.resolve(args.file)), loginText);
  } catch (e) {
    console.error(e.message);   // e.g. "Helper not found: ../helpers/login.md" -> shown as the run's error
    process.exit(3);
  }

  const stamp = new Date().toISOString().replace(/[:T]/g, '-').slice(0, 19);
  const slug = path.basename(args.file).replace(/_test\.md$|\.md$/, '').slice(0, 40);
  const runDir = path.join(process.env.LTH_RUNS_DIR || path.join(__dirname, 'runs'), `${stamp}-${slug}`);   // lth CLI: ~/.lth/runs
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

  // Server errors behind a step: an AJAX call / page load that answered 500+, or an AJAX answer that is a PHP /
  // database error page. A report that stays empty then fails as "text not visible"; this says why (TC-75: the
  // report's AJAX failed with "Database Error 1932: Table ... doesn't exist in engine").
  const serverErrors = [], pendingBodies = [];
  let curStep = 0, sinceNav = 0;
  const ERROR_PAGE = /A Database Error Occurred|A PHP Error was encountered|Fatal error|Uncaught (?:Error|Exception)|An uncaught Exception/i;
  const errorText = body => {
    const t = String(body || '').replace(/<(style|script)[\s\S]*?<\/>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/&#39;|&quot;/g, "'").replace(/\s+/g, ' ').trim();
    let m = t.match(/Error Number:\s*(\d+)\s*(.+?)\s+(?:SELECT|INSERT|UPDATE|DELETE|CALL|Filename:)/i);
    if (m) return `Database Error ${m[1]}: ${m[2]}`.slice(0, 300);
    m = t.match(/Severity:\s*(.+?)\s*Message:\s*(.+?)\s*Filename:\s*(\S+)\s*Line Number:\s*(\d+)/i);
    if (m) return `PHP ${m[1]}: ${m[2]} (${m[3]} line ${m[4]})`.slice(0, 300);
    return t.slice(0, 200);
  };
  page.on('response', resp => {
    const type = resp.request().resourceType();
    if (!['xhr', 'fetch', 'document'].includes(type)) return;
    const st = resp.status(), ajax = type !== 'document';
    if (st < 500 && !(ajax && /html/i.test(resp.headers()['content-type'] || ''))) return;
    const step = curStep, url = resp.url().replace(/[?#].*$/, '').replace(/^https?:\/\/[^/]+/, '').slice(-90);
    pendingBodies.push(resp.text().then(body => {
      if (st < 500 && !ERROR_PAGE.test(body)) return;
      serverErrors.push({ step, url, status: st, text: mask(errorText(body)) || `HTTP ${st}` });
    }).catch(() => { if (st >= 500) serverErrors.push({ step, url, status: st, text: `HTTP ${st}` }); }));
  });

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
  let failed = false, cleanupFailed = false;
  // "## Clean up" (or "## Cleanup" / "## Teardown"): runs when the test FAILED, so the same data can be used again.
  // When it PASSED the data is kept (people look at it): the steps are reported with their values filled in and
  // LogiTestHub asks before the next run whether to delete it. LTH_CLEANUP=always cleans up after a pass too.
  // A step whose {{value}} was never set (the test stopped before it) is skipped, so it never deletes the wrong rows.
  const CLEANUP = /^(clean[\s-]*up|tear[\s-]*down)\b/i;
  const keepOnPass = process.env.LTH_CLEANUP !== 'always';
  const keptSteps = [];

  // Heal (LTH_HEAL=1: Test Runs / schedules): a screen step that fails is shown to the AI with the fields and messages
  // on the page. A changed screen (new required field, new id / label) is healed - the steps it gives are run and the
  // test goes on, and LogiTestHub keeps the change in the case. An app bug is not healed. Asserts are never changed:
  // after the heal steps the same assert is checked again.
  const HEAL = process.env.LTH_HEAL === '1';
  let heals = 0;
  const NO_HEAL_STEP = /^(assert|sql|navigate|unknown|badHint)/;
  const unsafe = t => { const a = parseStep(fill(t, vars)); return a.needsAI || NO_HEAL_STEP.test(a.kind); };
  async function tryHeal(i, r, action) {
    heals += 1;
    const elements = (await listElements(page).catch(() => [])).map(({ index, ...e }) => e);
    const pageText = await page.evaluate(() => document.body.innerText.replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n').slice(0, 3000)).catch(() => '');
    const texts = tc.steps.map(s => mask(fill(s.text, vars)));
    const { data, usage } = await healStep({ steps: texts, failedN: i + 1, error: r.error, url: page.url(), elements, pageText: mask(pageText) });
    const u = usage || {};
    aiTokens.in += u.input_tokens || 0; aiTokens.out += u.output_tokens || 0;
    aiTokens.cacheRead += u.cache_read_input_tokens || 0; aiTokens.cacheWrite += u.cache_creation_input_tokens || 0;
    aiTokens.calls += 1;
    const edit = data.case_edit, target = tc.steps[edit.step - 1];
    const ev = { type: 'heal', n: i + 1, verdict: data.verdict, change: data.change, explain: mask(data.explain), ok: false,
                 steps_now: data.steps_now.map(mask), case_edit: { op: edit.op, step: edit.step, steps: edit.steps.map(mask) } };
    if (data.verdict === 'heal') {
      const editBad = edit.op !== 'none' && (!target || edit.steps.some(unsafe)
        || (edit.op === 'replace' && /^assert/.test(parseStep(fill(target.text, vars)).kind)));
      if (!data.steps_now.length || data.steps_now.some(unsafe) || editBad) {
        ev.explain += ' (Not used: the steps it gave cannot run safely.)';
      } else {
        try {
          for (const t of data.steps_now) await runAction(page, parseStep(fill(t, vars)));
          if (/^assert/.test(action.kind)) await runAction(page, action);   // the check itself never changes
          ev.ok = true;
          ev.case_edit.target = target ? target.text : null;   // the step as written in the case (with its {{values}})
        } catch (e) {
          ev.explain += ` (Tried it, still failing: ${mask(e.message.split('\n')[0])})`;
        }
      }
    }
    emit(ev);
    return ev;
  }

  for (let i = 0; i < tc.steps.length; i++) {
    const s = tc.steps[i];
    const shown = mask(fill(s.text, vars));
    const cleanup = CLEANUP.test(s.section || '');
    const r = { n: i + 1, section: s.section, text: shown, status: 'skipped', ms: 0, error: '', shot: '', warnings: [] };
    if (failed && !cleanup) { results.push(r); emit({ type: 'step', ...r }); continue; }
    if (cleanup && /\{\{\s*[\w.-]+\s*\}\}/.test(fill(s.text, vars))) {
      r.warnings.push('Skipped: a value it needs was never set (the test stopped before that step).');
      results.push(r); emit({ type: 'step', ...r }); continue;
    }
    if (cleanup && !failed && keepOnPass) {   // passed: keep the data, remember how to remove it
      keptSteps.push(fill(s.text, vars));
      r.warnings.push('Kept: the test passed, so its data stays. You are asked before the next run whether to delete it.');
      results.push(r); emit({ type: 'step', ...r }); continue;
    }
    const wasFailed = failed;   // a clean-up step failing must not hide the test's own failure

    const action = parseStep(fill(s.text, vars));
    curStep = i + 1;
    if (action.kind === 'navigate') sinceNav = curStep;   // a new page: older server errors are not this page's
    const errFrom = serverErrors.length;
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
    } else if (action.kind === 'sqlWrite') {
      try {
        const res = await dbsteps.write(action.sql);
        r.db = `${res.affected} row(s) changed`;
        r.status = 'passed';
      } catch (e) {
        r.status = 'failed';
        r.error = mask(e.message.split('\n')[0]);
        failed = true;
      }
    } else if (action.kind === 'sqlRemember' || action.kind === 'sqlAssert') {
      try {
        const res = await dbsteps.query(action.sql);
        const got = res.value == null ? '(empty)' : res.value;
        if (action.kind === 'sqlRemember') {
          if (res.value == null) throw new Error(`The query returned nothing to remember as ${action.name}`);
          vars[action.name] = res.value;
          await dbsteps.remember(action.name, res.value);
          r.db = `${action.name} = ${res.value}`;
        } else if (action.rows) {
          r.db = `${res.count} row(s)`;
          if (action.rows === 'none' ? res.count !== 0 : res.count === 0) throw new Error(`Expected the query to return ${action.rows === 'none' ? 'no rows' : 'a row'}, got ${res.count}`);
        } else {
          r.db = `returned ${got}`;
          const ok = action.contains ? String(res.value ?? '').includes(action.value) : String(res.value ?? '') === action.value;
          if (!ok) throw new Error(`Database returned "${got}", expected ${action.contains ? 'it to contain ' : ''}"${action.value}"`);
        }
        r.status = 'passed';
      } catch (e) {
        r.status = 'failed';
        r.error = mask(e.message.split('\n')[0]);
        failed = true;
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
        if (HEAL && args.ai && !cleanup && heals < 3) {
          try {
            const h = await tryHeal(i, r, action);
            if (h.ok) { r.status = 'passed'; r.healed = h.explain; r.error = ''; failed = false; r.warnings.push(`Auto-healed: ${h.explain}`); }
            else r.ai = `${{ app_bug: 'Looks like an app bug', test_data: 'Test data missing here', unknown: 'Could not heal' }[h.verdict] || 'Not healed'}: ${h.explain}`;
          } catch (he) {
            r.ai = `Heal not possible: ${explain(he)}`;
          }
        }
      }
    }
    await Promise.race([Promise.all(pendingBodies), new Promise(res => setTimeout(res, 1500))]);
    serverErrors.slice(errFrom).forEach(x => r.warnings.push(`Server error ${x.status} on ${x.url}: ${x.text}`));
    if (r.status === 'failed') {   // the cause is often a server error a few steps before (the Search click)
      // errors while a page loads (Navigate step) are its background calls (dashboard alerts ...), not this failure
      const cause = serverErrors.filter(x => x.step > sinceNav && x.step <= r.n).pop();
      if (cause) r.server_error = `seen at step ${cause.step}: ${cause.url} answered ${cause.status}: ${cause.text}`;
    }
    if (cleanup && r.status === 'failed') {   // keep cleaning up; the run still fails ("data may be left")
      cleanupFailed = true;
      failed = wasFailed;
      r.error = `Clean-up failed, test data may be left behind: ${r.error}`;
    }
    r.ms = Date.now() - t0;
    r.shot = `step-${String(r.n).padStart(2, '0')}.png`;
    await page.screenshot({ path: path.join(runDir, r.shot) }).catch(() => { r.shot = ''; });
    results.push(r);
    emit({ type: 'step', ...r });
    const note = (r.server_error ? `SERVER ERROR (${r.server_error}) ` : '') + (r.error || (r.status === 'needs-ai' && r.ai) || '')
      + (r.healed ? `HEALED: ${r.healed}` : '');
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
  if (cleanupFailed) failed = true;
  if (keptSteps.length && !failed) emit({ type: 'cleanup_pending', steps: keptSteps });
  const status = failed ? 'FAILED' : needsAI ? 'PARTIAL' : 'PASSED';
  const summary = {
    healed: results.filter(r => r.healed).length,
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
