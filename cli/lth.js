#!/usr/bin/env node
// tester - the LogiTestHub CLI. Describe a test in plain English (or pick a saved test case), run it in a
// headless browser on this PC, and see the video + step screenshots in LogiTestHub.
// The Anthropic key never leaves the LogiTestHub server: AI steps and test generation go through it,
// with the user's AI limit applied there.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const PKG = require('../package.json');
const HOME = process.env.LTH_HOME || path.join(os.homedir(), '.lth');
const CONFIG = path.join(HOME, 'config.json');
const RUNS = path.join(HOME, 'runs');
const RUN_JS = path.join(__dirname, '..', 'run.js');

// ---------------------------------------------------------------- small helpers

const argv = process.argv.slice(2);
const JSON_OUT = argv.includes('--json');

function flag(name) {
  const i = argv.indexOf(name);
  return i >= 0 ? (argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : true) : undefined;
}
function flags(name) {   // repeatable: --var k=v --var k2=v2
  const out = [];
  argv.forEach((a, i) => { if (a === name && argv[i + 1]) out.push(argv[i + 1]); });
  return out;
}
function positional() {   // words that are not flags or flag values
  const takesValue = new Set(['--server', '--username', '--key', '--url', '--project', '--case', '--file', '--vars',
    '--var', '--secret', '--count', '--folder', '--diff', '--title', '--env',
    '--rows', '--id', '--label', '--parent', '--link', '--sort', '--active', '--out', '--source', '--root',
    '--kind', '--symptom', '--fix', '--avoid', '--run']);
  const out = [];
  for (let i = 1; i < argv.length; i++) {
    if (takesValue.has(argv[i])) { if (argv[i] !== '--diff' || (argv[i + 1] && !argv[i + 1].startsWith('--'))) i++; continue; }
    if (argv[i].startsWith('--')) continue;
    out.push(argv[i]);
  }
  return out;
}

function print(obj, human) {
  if (JSON_OUT) process.stdout.write(JSON.stringify(obj, null, 2) + '\n');
  else if (human) console.log(human);
}
function fail(message, code = 2, extra = {}) {
  if (JSON_OUT) process.stdout.write(JSON.stringify({ ok: false, error: message, ...extra }, null, 2) + '\n');
  else console.error(`tester: ${message}`);
  process.exit(code);
}

function loadConfig() {
  let c = {};
  try { c = JSON.parse(fs.readFileSync(CONFIG, 'utf8')); } catch { /* not logged in yet */ }
  if (process.env.LTH_SERVER) c.server = process.env.LTH_SERVER;
  if (process.env.LTH_USER) c.username = process.env.LTH_USER;
  if (process.env.LTH_KEY) c.key = process.env.LTH_KEY;
  return c;
}
function saveConfig(c) {
  fs.mkdirSync(HOME, { recursive: true });
  fs.writeFileSync(CONFIG, JSON.stringify(c, null, 2), { mode: 0o600 });
}
function needLogin() {
  const c = loadConfig();
  if (!c.server || !c.username || !c.key) fail('not logged in. Run: tester login --server <url> --username <you> --key <access key>');
  return c;
}

async function api(c, method, p, body, isForm) {
  const headers = { Authorization: 'Basic ' + Buffer.from(`${c.username}:${c.key}`).toString('base64') };
  if (body && !isForm) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(c.server.replace(/\/+$/, '') + '/api/v1' + p, {
      method, headers, body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
      signal: AbortSignal.timeout(20 * 60 * 1000),
    });
  } catch (e) {
    fail(`cannot reach LogiTestHub at ${c.server} (${e.cause?.code || e.message})`);
  }
  let j = {};
  try { j = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || j.ok === false) {
    const msg = j.message || j.error || `HTTP ${res.status}`;
    fail(res.status === 401 ? `${msg} Check your username / access key (tester login).` : msg, res.status === 429 ? 4 : 2, j);
  }
  return j;
}

const ask = q => new Promise(resolve => {
  const rl = require('readline').createInterface({ input: process.stdin, output: process.stderr });
  rl.question(q, a => { rl.close(); resolve(a.trim()); });
});

// ---------------------------------------------------------------- commands

async function login() {
  const c = loadConfig();
  const server = (flag('--server') || c.server || await ask('LogiTestHub URL (e.g. http://192.168.1.10:5050): ')).replace(/\/+$/, '');
  const username = flag('--username') || await ask('Username: ');
  const key = flag('--key') || await ask('Access key (Profile -> Username and Access Key): ');
  if (!server || !username || !key) fail('server, username and access key are all needed.');
  const who = await api({ server, username, key }, 'GET', '/whoami');
  saveConfig({ ...c, server, username, key });
  print({ ok: true, server, username: who.username, name: who.name, role: who.role },
    `Logged in to ${server} as ${who.name} (@${who.username}, ${who.role}). Saved in ${CONFIG}`);
}

function logout() {
  const c = loadConfig();
  delete c.key;
  saveConfig(c);
  print({ ok: true }, 'Logged out (access key removed from this PC).');
}

async function whoami() {
  const c = needLogin();
  const who = await api(c, 'GET', '/whoami');
  print({ ...who, server: c.server }, `${who.name} (@${who.username}, ${who.role}) on ${c.server}`);
}

async function projects() {
  const c = needLogin();
  const j = await api(c, 'GET', '/projects');
  print(j, j.projects.map(p => `${String(p.id).padStart(4)}  ${p.name}  (${p.cases} cases)`).join('\n') || 'No projects.');
}

async function cases() {
  const c = needLogin();
  const pid = Number(positional()[0] || flag('--project') || c.project);
  if (!pid) fail('usage: tester cases <project id>   (tester projects lists them)');
  const j = await api(c, 'GET', `/projects/${pid}/cases`);
  print(j, j.cases.map(x => `${String(x.id).padStart(6)}  ${x.tc.padEnd(8)} ${x.title}${x.folder ? '  [' + x.folder + ']' : ''}`).join('\n') || 'No test cases.');
}

async function usage() {
  const c = needLogin();
  const j = await api(c, 'GET', '/ai/usage');
  const inr = v => (v == null ? 'no limit' : '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  print(j, `AI used this month: ${inr(j.used_inr)} of ${inr(j.limit_inr)}${j.percent != null ? ` (${j.percent}%)` : ''}, resets on ${j.resets_on}`
    + (j.blocked ? `\nBLOCKED: ${j.blocked_reason}` : j.warning ? `\nWarning: ${j.warning}` : ''));
}

// Lessons learned (LogiTestHub -> project -> Lessons): read before writing / running a test, add after a failure is
// understood and fixed, so the same mistake does not happen twice - for anyone on the team.
const LESSON_KINDS = ['app_bug', 'test_outdated', 'environment', 'test_data', 'agent_mistake'];

async function lessons() {
  const c = needLogin();
  const p = await resolveProject(c, positional()[0] || flag('--project') || c.project);
  const j = await api(c, 'GET', `/projects/${p.id}/lessons`);
  print(j, j.lessons.map(l => `#${l.id} [${l.kind_label}] ${l.title}${l.hits > 1 ? ` (seen ${l.hits}x)` : ''}\n    avoid: ${l.avoid || l.fix}`).join('\n')
    || 'No lessons yet for this project.');
}

async function lesson() {
  const sub = positional()[0];
  if (sub !== 'add') fail('usage: tester lesson add --project <id|name> --kind <kind> --title "<what went wrong>" --avoid "<rule for next time>" [--symptom ..] [--fix ..] [--case <id>] [--run <id>]');
  const c = needLogin();
  const p = await resolveProject(c, flag('--project') || c.project);
  const kind = flag('--kind');
  if (!LESSON_KINDS.includes(kind)) fail(`--kind must be one of: ${LESSON_KINDS.join(', ')}`);
  const val = n => (typeof flag(n) === 'string' ? flag(n) : undefined);
  const body = { kind, title: val('--title'), symptom: val('--symptom'), fix: val('--fix'), avoid: val('--avoid'),
                 case_id: val('--case'), run_id: val('--run') };
  if (!body.title) fail('--title "<what went wrong, one line>" is needed.');
  const j = await api(c, 'POST', `/projects/${p.id}/lessons`, body);
  print(j, j.created ? `Lesson #${j.id} saved for the team.` : `Lesson #${j.id} already existed: counted it again and updated it.`);
}

function gitDiff(base) {
  try {
    const range = base && base !== true ? [`${base}...HEAD`] : ['HEAD'];
    return execFileSync('git', ['diff', '--no-color', ...range], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  } catch (e) {
    fail(`git diff failed here (${process.cwd()}): run lth inside your git repository. ${e.message.split('\n')[0]}`);
  }
}

async function generate() {
  const c = needLogin();
  const task = positional().join(' ').trim() || (flag('--file') && fs.readFileSync(flag('--file'), 'utf8'));
  const pid = Number(flag('--project') || c.project);
  if (!task || !pid) fail('usage: tester generate "<the task / bug / requirement>" --project <id> [--count 6] [--diff [base-branch]]');
  const body = { project_id: pid, task, count: Number(flag('--count')) || 6 };
  if (flag('--folder')) body.folder_id = Number(flag('--folder'));
  if (argv.includes('--diff')) {
    body.diff = gitDiff(flag('--diff'));
    if (!body.diff.trim()) console.error('Note: no code changes found by git diff; generating from the task text only.');
  }
  if (!JSON_OUT) console.error(`Generating test cases with AI on ${c.server} ... (this can take a few minutes)`);
  const j = await api(c, 'POST', '/ai/generate', body);
  saveConfig({ ...loadConfig(), project: pid });
  print(j, `${j.message}\n` + j.cases.map(x => `  ${x.tc.padEnd(8)} ${x.title}\n           ${c.server}/cases/${x.id}`).join('\n'));
}

// Plain English -> a test case the runner understands: one step per line / sentence.
function toCase(text, url, title) {
  const steps = text.split(/\r?\n|;|(?<=\.)\s+(?=[A-Z{])|\s+then\s+/i).map(s => s.trim().replace(/^[-*\d.)\s]+/, '')).filter(Boolean);
  if (url && !/^(navigate|go|open)\b/i.test(steps[0] || '')) steps.unshift('Navigate to {{start_url}}.');
  const name = title || text.replace(/\s+/g, ' ').trim().slice(0, 80);
  return ['---', 'mode: testing', 'tags: [cli]', '---', '', `# ${name}`, '', '## Steps', ...steps, ''].join('\n');
}

function buildVars() {
  const vars = {};
  if (flag('--vars')) Object.assign(vars, JSON.parse(fs.readFileSync(flag('--vars'), 'utf8')));
  for (const kv of flags('--var')) { const i = kv.indexOf('='); if (i > 0) vars[kv.slice(0, i)] = { value: kv.slice(i + 1) }; }
  for (const kv of flags('--secret')) { const i = kv.indexOf('='); if (i > 0) vars[kv.slice(0, i)] = { value: kv.slice(i + 1), secret: true }; }
  const url = flag('--url');
  // {{start_url}} = --url exactly as given (where a plain-English test starts). {{base_url}} = the same with a
  // trailing '/', because saved cases append paths to it ({{base_url}}index.php/...).
  if (url && url !== true) {
    vars.start_url = { value: url };
    vars.base_url = { value: url.endsWith('/') ? url : url + '/' };
  }
  return vars;
}

function runRunner(mdPath, varsPath, env, loginPath) {
  return new Promise(resolve => {
    const args = [RUN_JS, mdPath, '--vars', varsPath];
    if (loginPath) args.push('--login', loginPath);
    if (argv.includes('--headed')) args.push('--headed');
    if (argv.includes('--no-ai')) args.push('--no-ai');
    const child = spawn(process.execPath, args, { env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    child.stdout.on('data', d => { out += d; if (!JSON_OUT) process.stdout.write(d); });
    child.stderr.on('data', d => { err += d; if (!JSON_OUT) process.stderr.write(d); });
    child.on('close', code => resolve({ code, out, err }));
  });
}

async function upload(c, runDir, fields) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) if (v != null) form.append(k, String(v));
  for (const name of fs.readdirSync(runDir)) {
    const p = path.join(runDir, name);
    // Only the run's own files: a page the app opens in a new tab leaves an extra 'page@<hash>.webm' recording
    if (!/^[\w.-]{1,120}$/.test(name) || !/\.(ndjson|webm|png|jpg|html|json)$/i.test(name)) continue;
    if (fs.statSync(p).isFile()) form.append('files', new Blob([fs.readFileSync(p)]), name);
  }
  return api(c, 'POST', '/runs', form, true);
}

async function run() {
  const c = needLogin();
  const caseRef = flag('--case');
  const file = flag('--file');
  const text = positional().join(' ').trim();
  const pid = Number(flag('--project') || c.project) || null;
  let content, caseId = null, title = flag('--title'), loginPid = pid;

  if (caseRef) {   // a saved LogiTestHub test case
    caseId = Number(String(caseRef).replace(/^#/, ''));
    if (!caseId) fail('--case takes the test case id (the number in /cases/<id>; tester cases <project> lists them).');
    const j = await api(c, 'GET', `/cases/${caseId}`);
    content = j.case.content;
    loginPid = j.case.project_id;
    if (/@import\s/.test(content)) console.error('Note: this case uses @import helpers; they must exist next to it on this PC, or run it from the LogiTestHub web page.');
  } else if (file && file !== true) {
    content = fs.readFileSync(file, 'utf8');
  } else if (text) {
    content = toCase(text, flag('--url'), title);
  } else {
    fail('usage: tester run "<what to test, in plain English>" --url <site> [--project <id>]   |   tester run --case <id>   |   tester run --file case_test.md');
  }

  // The project's login helper runs first, exactly as in a web run (skip with --no-login or "login: none" in the case)
  let loginText = '';
  if (loginPid && !argv.includes('--no-login')) {
    try { loginText = (await api(c, 'GET', `/projects/${loginPid}/login-helper`)).login_helper || ''; }
    catch (e) { console.error(`Note: could not read the project's login helper (${e.message}); running without it.`); }
    if (!loginText) console.error("Note: this project has no login helper yet (LogiTestHub -> project -> Login helper), so the test must sign in by itself.");
  }
  const vars = buildVars();
  if (/\{\{\s*(base_url|start_url)\s*\}\}/.test(content + loginText) && !vars.base_url) fail('this test needs the site address: add --url <site address> (or --vars file.json).');

  fs.mkdirSync(RUNS, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lth-'));
  const mdPath = path.join(tmp, 'cli_test.md');
  const varsPath = path.join(tmp, 'vars.json');
  fs.writeFileSync(mdPath, content);
  fs.writeFileSync(varsPath, JSON.stringify(vars), { mode: 0o600 });
  const loginPath = loginText ? path.join(tmp, 'login.md') : null;
  if (loginPath) fs.writeFileSync(loginPath, loginText);
  let res;
  try {
    res = await runRunner(mdPath, varsPath, {
      LTH_RUNS_DIR: RUNS, LTH_SERVER: c.server, LTH_USER: c.username, LTH_KEY: c.key, LTH_CASE_ID: caseId || '',
      LTH_ENV: flag('--env') && flag('--env') !== true ? flag('--env') : '',   // database steps: which environment's DB
    }, loginPath);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });   // the vars file can hold passwords
  }
  const m = res.out.match(/REPORT : (.+)/);
  if (!m) {
    if (/Executable doesn't exist|playwright install/i.test(res.out + res.err)) fail('the browser is not installed yet. Run: tester setup');
    fail(`the run did not finish: ${(res.err || res.out).trim().split('\n').pop()}`);
  }
  const report = m[1].trim();
  const runDir = path.dirname(report);
  const status = (res.out.match(/RESULT : (\w+)/) || [])[1] || 'ERROR';
  const result = { ok: true, status, report, run_dir: runDir };

  if (!argv.includes('--no-upload')) {
    if (!caseId && !pid) {
      if (!JSON_OUT) console.log('\nNot saved to LogiTestHub: add --project <id> (tester projects) to keep this run there.');
    } else {
      if (!JSON_OUT) console.log('\nUploading the run to LogiTestHub ...');
      const up = await upload(c, runDir, caseId ? { case_id: caseId } : { project_id: pid, content, title });
      Object.assign(result, { run_id: up.run_id, case_id: up.case_id, url: up.url });
      if (pid) saveConfig({ ...loadConfig(), project: pid });
      if (!JSON_OUT) console.log(` VIEW   : ${up.url}`);
    }
  }
  print(result);
  process.exitCode = { PASSED: 0, FAILED: 1, PARTIAL: 3 }[status] ?? 2;
}

function setup() {
  console.log('Installing the headless browser (Chromium) for tester: one time, about 150 MB ...');
  // playwright's package.json 'exports' hides ./cli: locate cli.js next to its main file instead
  const cli = require('path').join(require('path').dirname(require.resolve('playwright')), 'cli.js');
  const r = require('child_process').spawnSync(process.execPath, [cli, 'install', 'chromium'], { stdio: 'inherit' });
  if (r.status !== 0) fail('browser install failed (see above).', r.status || 2);
  console.log('Done. Next: tester login');
}

// Installs the /tester skill for Claude Code (~/.claude/skills/tester), like kane-cli's skill: the agent then knows
// how to run and read Tester CLI tests. Re-run after updating the CLI to refresh it.
function skill() {
  const sub = positional()[0] || 'install';
  if (sub !== 'install') fail('usage: tester skill install');
  const os = require('os');
  const dest = path.join(os.homedir(), '.claude', 'skills', 'tester');
  fs.mkdirSync(dest, { recursive: true });
  fs.copyFileSync(path.join(__dirname, 'skill', 'SKILL.md'), path.join(dest, 'SKILL.md'));
  // references/ (write-case, debug, lessons ...): read by the agent only when it needs them; replaced as a whole
  fs.rmSync(path.join(dest, 'references'), { recursive: true, force: true });
  fs.cpSync(path.join(__dirname, 'skill', 'references'), path.join(dest, 'references'), { recursive: true });
  console.log(`Installed the /tester skill for Claude Code in ${dest}
Restart Claude Code (or start a new chat), then type /tester.`);
  // the old /lth skill (before the rename) would show twice in the / menu
  const old = path.join(os.homedir(), '.claude', 'skills', 'lth');
  if (fs.existsSync(path.join(old, 'SKILL.md'))) fs.rmSync(old, { recursive: true, force: true });
}

function help() {
  console.log(`Tester CLI ${PKG.version} - LogiTestHub

  tester login [--server <url>] [--username <u>] [--key <access key>]   connect to LogiTestHub
  tester setup                                       install the headless browser (once)
  tester whoami | tester logout
  tester projects                                    list projects
  tester cases <project id>                          list test cases
  tester run "<test in plain English>" --url <site> [--project <id>]
  tester run --case <case id> --url <site>           run a saved test case
  tester run --file my_test.md --url <site>          run a test case file
        [--headed] [--no-ai] [--no-upload] [--no-login] [--env <name>] [--var k=v] [--secret k=v] [--vars file.json]
        (--env: the LogiTestHub environment whose read-only database "Assert SQL" / "Remember SQL" steps use)
        (the project's login helper runs first; --no-login, or "login: none" in the case, skips it)
  tester generate "<task / bug / requirement>" --project <id> [--count 6] [--diff [base-branch]]
  tester usage                                       your AI use this month
  tester lessons <project id|name>                   lessons learned: read before writing / running a test
  tester lesson add --project <id|name> --kind <app_bug|test_outdated|environment|test_data|agent_mistake>
        --title "<what went wrong>" --avoid "<rule for next time>" [--symptom ..] [--fix ..] [--case <id>] [--run <id>]
  tester skill install                               add the /tester skill to Claude Code (~/.claude/skills/tester)
  tester modules pull --project <id|name> [--out logitesthub.modules.json]     save the module tree as a file
  tester modules push <file> [--project <id|name>] [--dry-run]                  add the file's NEW modules (never deletes)
  tester modules build --rows menu.tsv --project <name> [--out file]            menu-table rows -> modules file
        [--id id_menu] [--label label] [--parent parent] [--link link] [--sort sort] [--active active] [--source text]

  --json   machine-readable output (for AI agents and CI)
  Exit codes: 0 passed, 1 failed, 3 partial (some steps need AI), 4 AI limit reached, 2 other errors.
  Runs are kept in ${RUNS}`);
}

// ---------------------------------------------------------------- modules: the source-of-truth module tree
// The file (logitesthub.modules.json) is made from the application's own menu table / route files (usually by the
// IDE agent), then pushed. Push only ever ADDS modules / sub-modules the project does not have yet.

const MODULES_FILE = 'logitesthub.modules.json';
const FILE_FORMAT = 'logitesthub.modules/v1';

async function resolveProject(c, want) {
  if (!want) fail('say which project: --project <id or name>   (tester projects lists them)');
  if (/^\d+$/.test(String(want))) return { id: Number(want) };
  const j = await api(c, 'GET', '/projects');
  const p = j.projects.find(x => x.name.toLowerCase() === String(want).toLowerCase());
  if (!p) fail(`no project named "${want}". Projects: ${j.projects.map(x => x.name).join(', ')}`);
  return p;
}

function readRows(file) {   // JSON array, or TSV / CSV with a header line (e.g. mysql -B output)
  const text = fs.readFileSync(file, 'utf8').replace(/^﻿/, '');
  if (/^\s*[\[{]/.test(text)) {
    const j = JSON.parse(text);
    return Array.isArray(j) ? j : (j.rows || j.data || []);
  }
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  const sep = lines[0].includes('\t') ? '\t' : ',';
  const split = l => sep === '\t' ? l.split('\t') : (l.match(/("([^"]|"")*"|[^,]*)(,|$)/g) || []).map(x => x.replace(/,$/, '').replace(/^"|"$/g, '').replace(/""/g, '"'));
  const head = split(lines[0]).map(h => h.trim());
  return lines.slice(1).map(l => { const v = split(l); return Object.fromEntries(head.map((h, i) => [h, v[i] === 'NULL' ? null : v[i]])); });
}

// Parent -> child rows (any depth) -> modules with sub-modules. Deeper levels become "Group › Page" names,
// the same way the screen reader names them, so both ways give the same tree.
function rowsToModules(rows, o) {
  const col = (r, name) => (name in r ? r[name] : undefined);
  const pick = (r, names) => { for (const n of names) if (n && r[n] !== undefined) return r[n]; return undefined; };
  const isTop = v => v === undefined || v === null || ['', '0', 'null', 'none'].includes(String(v).trim().toLowerCase()) || String(v) === String(o.root ?? '');
  const off = v => ['0', 'false', 'no', 'n', 'inactive', 'disabled'].includes(String(v ?? '').trim().toLowerCase());
  const nodes = rows.filter(r => !o.active || !off(col(r, o.active))).map(r => ({
    id: String(pick(r, [o.id, 'id', 'id_menu'])), label: String(pick(r, [o.label, 'label', 'name', 'title']) ?? '').replace(/\s+/g, ' ').trim(),
    parent: pick(r, [o.parent, 'parent', 'parent_id']), link: String(pick(r, [o.link, 'link', 'url', 'path', 'route']) ?? '').trim(),
    sort: Number(pick(r, [o.sort, 'sort', 'sort_order', 'position', 'order']) ?? 0) || 0,
  })).filter(n => n.label && n.id !== 'undefined');
  const ids = new Set(nodes.map(n => n.id));
  const kids = new Map();
  for (const n of nodes) {
    const p = isTop(n.parent) || !ids.has(String(n.parent)) ? '__top' : String(n.parent);
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p).push(n);
  }
  const order = list => (list || []).slice().sort((a, b) => a.sort - b.sort || Number(a.id) - Number(b.id) || a.label.localeCompare(b.label));
  const realLink = l => l && !/^(#|javascript:)/i.test(l) ? l : '';
  const leaves = (n, prefix, seen) => {
    if (seen.has(n.id)) return [];   // a loop in the table: stop
    seen.add(n.id);
    const ch = order(kids.get(n.id));
    if (!ch.length) return [{ name: prefix + n.label, ...(realLink(n.link) ? { link: n.link } : {}) }];
    return ch.flatMap(c => leaves(c, prefix + n.label + ' › ', seen));
  };
  return order(kids.get('__top')).map(m => {
    const seenNames = new Set();   // the same page listed twice under one module (it happens in real menu tables): keep one
    const subs = order(kids.get(m.id)).flatMap(c => leaves(c, '', new Set([m.id])))
      .filter(x => !seenNames.has(x.name.toLowerCase()) && seenNames.add(x.name.toLowerCase()));
    return { name: m.label, ...(realLink(m.link) ? { link: m.link } : {}), subs };
  }).filter(m => m.subs.length || m.link);   // a caption row with no page and no children is left out
}

async function modules() {
  const sub = positional()[0];
  const outFile = flag('--out') || flag('--file') || MODULES_FILE;

  if (sub === 'build') {
    const rowsFile = flag('--rows');
    if (!rowsFile || rowsFile === true) fail('usage: tester modules build --rows menu.tsv --project <name> [--id id_menu] [--label label] [--parent parent] [--link link] [--sort sort] [--active active]');
    let rows;
    try { rows = readRows(rowsFile); } catch (e) { fail(`cannot read ${rowsFile}: ${e.message}`); }
    if (!rows.length) fail(`${rowsFile} has no rows`);
    const o = { id: flag('--id'), label: flag('--label'), parent: flag('--parent'), link: flag('--link'), sort: flag('--sort'), active: flag('--active'), root: flag('--root') };
    const mods = rowsToModules(rows, o);
    // a wrapper row (like eTail's "Home": no page, holds every module) hides the real top level: suggest --root
    if (!o.root) {
      const idOf = r => String(r[o.id || 'id'] ?? r.id_menu ?? r.id), parOf = r => r[o.parent || 'parent'] ?? r.parent_id;
      const tops = rows.filter(r => ['', '0', 'null', 'undefined'].includes(String(parOf(r) ?? '').trim().toLowerCase()));
      const wrap = tops.map(t => ({ t, n: rows.filter(r => String(parOf(r)) === idOf(t)).length }))
        .filter(x => x.n >= 10 && !/[a-z]/i.test(String(x.t[o.link || 'link'] ?? '').replace(/^#$/, ''))).sort((a, b) => b.n - a.n)[0];
      if (wrap && tops.length <= 5) console.error(`hint: "${wrap.t[o.label || 'label'] ?? wrap.t.label}" (id ${idOf(wrap.t)}) holds ${wrap.n} items and has no page of its own - ` +
        `it looks like a wrapper. To make its children the modules, add: --root ${idOf(wrap.t)}`);
    }
    const file = { format: FILE_FORMAT, project: typeof flag('--project') === 'string' ? flag('--project') : undefined,
      source: typeof flag('--source') === 'string' ? flag('--source') : `${path.basename(rowsFile)} (${rows.length} rows)`,
      generated_at: new Date().toISOString(), modules: mods };
    fs.writeFileSync(outFile, JSON.stringify(file, null, 2) + '\n');
    const ns = mods.reduce((a, m) => a + m.subs.length, 0);
    return print({ ok: true, file: outFile, modules: mods.length, subs: ns }, `Wrote ${outFile}: ${mods.length} modules, ${ns} sub-modules (from ${rows.length} rows).\nNext: tester modules push ${outFile} --dry-run`);
  }

  const c = needLogin();
  if (sub === 'pull') {
    const p = await resolveProject(c, flag('--project') || c.project);
    const j = await api(c, 'GET', `/projects/${p.id}/modules`);
    fs.writeFileSync(outFile, JSON.stringify(j.file, null, 2) + '\n');
    const ns = j.file.modules.reduce((a, m) => a + m.subs.length, 0);
    return print({ ok: true, file: outFile, modules: j.file.modules.length, subs: ns }, `Saved ${j.file.project}'s ${j.file.modules.length} modules, ${ns} sub-modules to ${outFile}`);
  }

  if (sub === 'push') {
    const file = positional()[1] || (flag('--file') !== true && flag('--file')) || MODULES_FILE;
    let data;
    try { data = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^﻿/, '')); } catch (e) { fail(`cannot read ${file}: ${e.message}`); }
    const p = await resolveProject(c, flag('--project') || data.project || c.project);
    const dry = !!flag('--dry-run');
    const j = await api(c, 'POST', `/projects/${p.id}/modules${dry ? '?dry_run=1' : ''}`, data);
    const list = (xs, n = 12) => xs.slice(0, n).join(', ') + (xs.length > n ? ` … (+${xs.length - n})` : '');
    const lines = [
      `${j.project} (project ${p.id})${dry ? ' - DRY RUN, nothing changed' : ''}`,
      `  New modules:      ${j.new_modules.length}${j.new_modules.length ? '  ' + list(j.new_modules) : ''}`,
      `  New sub-modules:  ${j.new_subs.length}${j.new_subs.length ? '  ' + list(j.new_subs, 8) : ''}`,
      `  Already there:    ${j.existing_modules} modules, ${j.existing_subs} sub-modules (unchanged)`,
      `  Only in LogiTestHub, not in the file: ${j.kept_not_in_file.length}${j.kept_not_in_file.length ? ' (kept - push never deletes): ' + list(j.kept_not_in_file, 6) : ''}`,
      ...(j.warnings || []).map(w => '  warning: ' + w),
      dry ? (j.new_modules.length + j.new_subs.length ? '\nRun again without --dry-run to add them.' : '\nNothing new to add.')
          : `\nAdded ${j.added_modules} module(s) and ${j.added_subs} sub-module(s).`,
    ];
    return print(j, lines.join('\n'));
  }

  fail('usage: tester modules pull --project <id|name> | tester modules push <file> [--dry-run] | tester modules build --rows <file> --project <name>');
}

// ---------------------------------------------------------------- main

const COMMANDS = { login, logout, whoami, projects, cases, usage, generate, run, setup, help, modules, skill, lessons, lesson };
(async () => {
  const cmd = argv[0];
  if (!cmd || cmd === '--help' || cmd === '-h') return help();
  if (cmd === '--version' || cmd === '-v') return console.log(PKG.version);
  const fn = COMMANDS[cmd];
  if (!fn) fail(`unknown command "${cmd}". Try: tester help`);
  await fn();
})().catch(e => fail(e.message));
