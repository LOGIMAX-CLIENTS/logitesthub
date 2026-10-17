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

// ---------------------------------------------------------------- self-update
// The server sends the hash of the CLI code it hands out (X-Tester-CLI). When it differs from this install's,
// `npm install -g <server>/cli/tester.tgz` runs once the command has ended (stderr only: --json stays clean).
// LTH_NO_AUTO_UPDATE=1 turns it off.
const CLI_CODE = ['run.js', 'cli', 'lib'];   // same files as manager/clitool.py CODE
function cliFingerprint() {
  const root = path.join(__dirname, '..');
  const files = [];
  for (const p of CLI_CODE) {
    const full = path.join(root, p);
    if (p.endsWith('.js')) { if (fs.existsSync(full)) files.push(p); continue; }
    for (const f of fs.existsSync(full) ? fs.readdirSync(full) : []) {
      if (f.endsWith('.js') && fs.statSync(path.join(full, f)).isFile()) files.push(`${p}/${f}`);
    }
  }
  files.sort();
  const h = require('crypto').createHash('sha256');
  for (const f of files) h.update(Buffer.concat([Buffer.from(f + '\n'), fs.readFileSync(path.join(root, f)), Buffer.from('\n')]));
  return h.digest('hex').slice(0, 16);
}
let pendingUpdate = null;   // server URL to update from
function noteServerCli(server, fp) {
  if (!fp || pendingUpdate || process.env.LTH_NO_AUTO_UPDATE === '1') return;
  try { if (fp !== cliFingerprint()) pendingUpdate = server.replace(/\/+$/, ''); } catch { /* not comparable: skip */ }
}
process.on('exit', () => {
  if (!pendingUpdate) return;
  const url = `${pendingUpdate}/cli/tester.tgz`;
  process.stderr.write(`\nA newer Tester CLI is on ${pendingUpdate}: updating (npm install -g ${url}) ...\n`);
  const r = require('child_process').spawnSync('npm', ['install', '-g', url],
    { stdio: ['ignore', 'ignore', 'inherit'], shell: process.platform === 'win32', timeout: 180000 });
  process.stderr.write(r.status === 0 ? 'Tester CLI updated: the next command uses it.\n'
    : `Update failed: run it yourself: npm install -g ${url}\n`);
});

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
    '--kind', '--symptom', '--fix', '--avoid', '--run', '--note', '--task']);
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
  if (wsGet('project')) c.project = wsGet('project');   // inside a workspace its project is the default
  return c;
}

async function api(c, method, p, body, isForm, soft = false) {   // soft: throw instead of ending the command
  const headers = { Authorization: 'Basic ' + Buffer.from(`${c.username}:${c.key}`).toString('base64') };
  if (body && !isForm) headers['Content-Type'] = 'application/json';
  let res;
  try {
    res = await fetch(c.server.replace(/\/+$/, '') + '/api/v1' + p, {
      method, headers, body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
      signal: AbortSignal.timeout(20 * 60 * 1000),
    });
  } catch (e) {
    const msg = `cannot reach LogiTestHub at ${c.server} (${e.cause?.code || e.message})`;
    if (soft) throw new Error(msg);
    fail(msg, 2, { outcome: 'not_completed', category: 'logitesthub_unreachable' });
  }
  noteServerCli(c.server, res.headers.get('x-tester-cli'));
  let j = {};
  try { j = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || j.ok === false) {
    const msg = j.message || j.error || `HTTP ${res.status}`;
    if (soft) throw new Error(`${msg}${res.status >= 500 ? ' (LogiTestHub server error / overloaded)' : ''}`);
    fail(res.status === 401 ? `${msg} Check your username / access key (tester login).` : msg, res.status === 429 ? 4 : 2, j);
  }
  return j;
}

const ask = q => new Promise(resolve => {
  const rl = require('readline').createInterface({ input: process.stdin, output: process.stderr });
  rl.question(q, a => { rl.close(); resolve(a.trim()); });
});

// ---------------------------------------------------------------- the workspace tests/ folder
// Like kane-cli (TestMu AI): the test cases live in the developer's own project, in tests/, next to the code they
// test, so they are reviewed and committed with it. tests/tester.json marks the folder and keeps the project's
// LogiTestHub project id and site URL, so later commands need no --project / --url.

const WS_DIR = 'tests';
const WS_FILE = 'tester.json';

function findWorkspace(from = process.cwd()) {   // the nearest folder above with tests/tester.json
  for (let d = path.resolve(from); ; d = path.dirname(d)) {
    if (fs.existsSync(path.join(d, WS_DIR, WS_FILE))) return d;
    if (path.dirname(d) === d) return null;
  }
}
function projectRoot(from = process.cwd()) {   // the code project we are in: git root, else a folder with a project file
  try {
    return path.resolve(execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: from, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim());
  } catch { /* not a git repository */ }
  const marks = ['package.json', 'composer.json', 'pyproject.toml', 'requirements.txt', 'pom.xml', 'go.mod', 'index.php'];
  for (let d = path.resolve(from); ; d = path.dirname(d)) {
    if (marks.some(m => fs.existsSync(path.join(d, m)))) return d;
    if (path.dirname(d) === d || d === os.homedir()) return null;
  }
}
function readWs(root) {
  try { return JSON.parse(fs.readFileSync(path.join(root, WS_DIR, WS_FILE), 'utf8')); } catch { return {}; }
}
function createWs(root, settings = {}) {
  const dir = path.join(root, WS_DIR);
  // helpers/: @import files (login.md ...). variables/: test data (base_url, username, password ...) - not committed
  fs.mkdirSync(path.join(dir, 'helpers'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'variables'), { recursive: true });
  const ws = { format: 'tester.workspace/v1', variables: 'variables/variables.json', ...readWs(root), ...settings };
  fs.writeFileSync(path.join(dir, WS_FILE), JSON.stringify(ws, null, 2) + '\n');
  const vfile = path.join(dir, ws.variables);
  if (!fs.existsSync(vfile)) fs.writeFileSync(vfile, JSON.stringify({
    base_url: { value: '' }, username: { value: '' }, password: { value: '', secret: true } }, null, 2) + '\n');
  const ignore = path.join(dir, '.gitignore');
  if (!fs.existsSync(ignore)) fs.writeFileSync(ignore, '# test data with passwords stays on this PC\nvariables/\n');
  const readme = path.join(dir, 'README.md');
  if (!fs.existsSync(readme)) fs.writeFileSync(readme, `# UI tests (Tester CLI / LogiTestHub)

Each \`*_test.md\` here is one test case in plain English, one step per line. Commit them with the code they test.

    tester run --file ${WS_DIR}/<name>_test.md     run one case
    tester test                          run every case in this folder
    tester run "<what to test>"          new case: runs it and saves it here
    tester case pull <id>                copy a LogiTestHub case here

\`helpers/\` holds shared steps for \`@import\` (e.g. \`@import ../helpers/login.md\`). \`variables/\` holds the test
data (\`{"username": {"value": ".."}, "password": {"value": "..", "secret": true}}\`): used by every run here, kept out of git.
LogiTestHub (Settings -> System) uses these two folders too when it runs on this PC.

\`${WS_FILE}\` keeps this project's LogiTestHub project and site URL. A \`case_id:\` line in a case's front matter links
the file to its LogiTestHub case, so its runs are saved under that case.
`);
  return ws;
}
// The workspace for this command. First use inside a code project: create tests/ there (no --no-save, not a dry run).
let WS;
function workspace(create = false) {
  if (WS !== undefined && !(create && !WS)) return WS;
  const root = findWorkspace();
  if (root) return (WS = { root, dir: path.join(root, WS_DIR), ...readWs(root) });
  const proj = create && !argv.includes('--no-save') && projectRoot();
  if (!proj) return (WS = null);
  createWs(proj);
  if (!JSON_OUT) console.error(`Created ${path.join(proj, WS_DIR)}${path.sep} for this project's test cases (first use here).`);
  return (WS = { root: proj, dir: path.join(proj, WS_DIR), created: true });
}
const wsGet = key => (workspace() || {})[key];
function wsSet(settings) {   // remember project / url for the next command in this workspace
  const ws = workspace();
  if (!ws) return;
  const cur = readWs(ws.root);
  if (Object.entries(settings).every(([k, v]) => v == null || cur[k] === v)) return;
  createWs(ws.root, Object.fromEntries(Object.entries(settings).filter(([, v]) => v != null)));
}

// The workspace's test data (tests/variables/...): values that are filled in, as { key: { value, secret } }
function wsVars() {
  const ws = workspace();
  if (!ws) return {};
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(ws.dir, ws.variables || 'variables/variables.json'), 'utf8'));
    return Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, v && typeof v === 'object' ? v : { value: v }])
      .filter(([, v]) => v.value != null && String(v.value) !== ''));
  } catch { return {}; }
}
// Tell LogiTestHub which tests/ folder was used last: when it runs on this PC, Settings -> System takes the
// variables and helpers from there. Never stops the command.
async function reportWs(c) {
  const ws = workspace();
  if (!ws || !c || !c.server) return;
  try { await api(c, 'POST', '/workspace', { tests_dir: ws.dir, root: ws.root, project: Number(readWs(ws.root).project) || undefined }, false, true); }
  catch { /* old server or no permission: nothing to do */ }
}

function urlOpt() {   // --url, else the workspace's site URL
  const u = flag('--url');
  return u && u !== true ? u : wsGet('url');
}

// front matter: read / set one "key: value" line (case_id links a file to its LogiTestHub case)
function frontGet(text, key) {
  const fm = (text.match(/^---\s*\n([\s\S]*?)\n---\s*\n/) || [])[1] || '';
  const m = fm.match(new RegExp(`^\\s*${key}\\s*:\\s*['"]?([^'"\\n]*?)['"]?\\s*$`, 'm'));
  return m ? m[1] : undefined;
}
function frontSet(text, key, value) {
  const m = text.match(/^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n/);
  const line = `${key}: ${value}`;
  if (!m) return `---\n${line}\n---\n\n${text}`;
  const re = new RegExp(`^\\s*${key}\\s*:.*$`, 'm');
  const fm = re.test(m[1]) ? m[1].replace(re, line) : `${line}\n${m[1]}`;
  return `---\n${fm}\n---\n` + text.slice(m[0].length);
}
const frontDel = (text, key) => text.replace(/^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n/, (all, fm) =>
  `---\n${fm.split('\n').filter(l => !new RegExp(`^\\s*${key}\\s*:`).test(l)).join('\n')}\n---\n`);

function slug(s) {
  return String(s || 'test').toLowerCase().replace(/\{\{.*?\}\}/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 50) || 'test';
}
// The file in tests/ for a case: the one already linked to it (case_id), else a new name that is not taken.
function caseFile(dir, name, caseId) {
  if (caseId && fs.existsSync(dir)) {
    for (const f of fs.readdirSync(dir).filter(x => x.endsWith('_test.md'))) {
      try { if (Number(frontGet(fs.readFileSync(path.join(dir, f), 'utf8'), 'case_id')) === Number(caseId)) return path.join(dir, f); } catch { /* skip */ }
    }
  }
  let p = path.join(dir, `${name}_test.md`);
  for (let n = 2; fs.existsSync(p); n++) p = path.join(dir, `${name}_${n}_test.md`);
  return p;
}
const rel = p => path.relative(process.cwd(), p) || p;

// tester init [--project <id|name>] [--url <site>]: make tests/ in this project now (it is also made on first use)
async function init() {
  const root = findWorkspace() || projectRoot() || process.cwd();
  const settings = {};
  if (flag('--project')) settings.project = (await resolveProject(needLogin(), flag('--project'))).id;
  if (urlOpt()) settings.url = urlOpt();
  const ws = createWs(root, settings);
  WS = undefined;   // read it again: it exists now
  const c = loadConfig();
  if (c.server && c.username && c.key) await reportWs(c);
  print({ ok: true, dir: path.join(root, WS_DIR), ...ws },
    `Test cases folder: ${path.join(root, WS_DIR)}${path.sep}` +
    `${ws.project ? `\n  LogiTestHub project: ${ws.project}` : '\n  Add the LogiTestHub project: tester init --project <id|name>'}` +
    `${ws.url ? `\n  Site URL: ${ws.url}` : '\n  Add the site URL:            tester init --url <site>'}` +
    `\nNext: tester run "<what to test, in plain English>"   (saved in ${WS_DIR}/ and in LogiTestHub)`);
}

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
  // a copy of each new case in tests/, linked by case_id (review it with the code; tester case push sends edits back)
  const ws = workspace(true);
  if (ws) {
    wsSet({ project: pid });
    await reportWs(c);
    for (const x of j.cases) {
      try {
        const full = (await api(c, 'GET', `/cases/${x.id}`, null, false, true)).case;
        const file = caseFile(ws.dir, `${x.tc.replace('-', '')}_${slug(x.title)}`, x.id);
        fs.writeFileSync(file, frontSet(full.content.replace(/\r\n/g, '\n'), 'case_id', x.id));
        x.file = file;
      } catch (e) { x.file_error = e.message; }
    }
  }
  print(j, `${j.message}\n` + j.cases.map(x => `  ${x.tc.padEnd(8)} ${x.title}\n           ${c.server}/cases/${x.id}` +
    (x.file ? `\n           ${rel(x.file)}` : '')).join('\n'));
}

// Plain English -> a test case the runner understands: one step per line / sentence.
function toCase(text, url, title) {
  const steps = text.split(/\r?\n|;|(?<=\.)\s+(?=[A-Z{])|\s+then\s+/i).map(s => s.trim().replace(/^[-*\d.)\s]+/, '')).filter(Boolean);
  if (url && !/^(navigate|go|open)\b/i.test(steps[0] || '')) steps.unshift('Navigate to {{start_url}}.');
  const name = title || text.replace(/\s+/g, ' ').trim().slice(0, 80);
  return ['---', 'mode: testing', 'tags: [cli]', '---', '', `# ${name}`, '', '## Steps', ...steps, ''].join('\n');
}

function buildVars() {
  const vars = wsVars();   // tests/variables/ first; --vars / --var / --secret override it
  if (flag('--vars')) Object.assign(vars, JSON.parse(fs.readFileSync(flag('--vars'), 'utf8')));
  for (const kv of flags('--var')) { const i = kv.indexOf('='); if (i > 0) vars[kv.slice(0, i)] = { value: kv.slice(i + 1) }; }
  for (const kv of flags('--secret')) { const i = kv.indexOf('='); if (i > 0) vars[kv.slice(0, i)] = { value: kv.slice(i + 1), secret: true }; }
  const url = urlOpt();
  // {{start_url}} = --url exactly as given (where a plain-English test starts). {{base_url}} = the same with a
  // trailing '/', because saved cases append paths to it ({{base_url}}index.php/...).
  if (url && url !== true) {
    vars.start_url = { value: url };
    vars.base_url = { value: url.endsWith('/') ? url : url + '/' };
  }
  return vars;
}

function runRunner(mdPath, varsPath, env, loginPath, baseDir) {
  return new Promise(resolve => {
    const args = [RUN_JS, mdPath, '--vars', varsPath];
    if (loginPath) args.push('--login', loginPath);
    if (baseDir) args.push('--base', baseDir);   // @import paths: the case file's own folder
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
    if (name === UPLOAD_FILE || !/^[\w.-]{1,120}$/.test(name) || !/\.(ndjson|webm|png|jpg|html|json)$/i.test(name)) continue;
    if (fs.statSync(p).isFile()) form.append('files', new Blob([fs.readFileSync(p)]), name);
  }
  return api(c, 'POST', '/runs', form, true, true);
}

// ---------------------------------------------------------------- the result of a run, as structured JSON
// outcome: passed | failed | partial | not_completed.  category says what the CLI could tell on its own:
//   environment  - the site could not be reached / timed out / server error (network, server down or overloaded):
//                  not a test result - run again later
//   needs_review - a real failure: the agent decides app_bug (development) or our side (test_outdated, test_data,
//                  agent_mistake) from the failing step, its screenshots and the code (skill: references/debug.md)
const UPLOAD_FILE = 'upload.json';   // what to send to LogiTestHub, kept so a failed upload can be retried
const ENV_RE = /net::ERR_|ERR_CONNECTION|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_ADDRESS_UNREACHABLE|ERR_TIMED_OUT|ERR_EMPTY_RESPONSE|Navigation timeout|page\.goto: Timeout|\b(502|503|504)\b|Bad Gateway|Service Unavailable|Gateway Time-?out|Too Many Requests/i;

function analyse(runDir, status) {
  let steps = [], end = {};
  try {
    for (const line of fs.readFileSync(path.join(runDir, 'events.ndjson'), 'utf8').split('\n')) {
      if (!line.trim()) continue;
      const e = JSON.parse(line);
      if (e.type === 'step') steps.push(e); else if (e.type === 'run_end') end = e;
    }
  } catch { /* no log: counts stay empty */ }
  const shot = s => (s && s.shot ? path.join(runDir, s.shot) : null);
  const i = steps.findIndex(s => s.status === 'failed');
  const f = i >= 0 ? steps[i] : null;
  const counts = { total: end.total ?? steps.length, passed: end.passed ?? 0, failed: end.failed ?? 0,
    skipped: end.skipped ?? 0, needs_ai: end.needsAI ?? 0 };
  const failed_step = f ? { n: f.n, section: f.section || null, text: f.text, error: f.error || '',
    screenshot: shot(f), screenshot_before: shot(steps[i - 1]) } : null;
  // a timeout on the very first page load, or a network / server error anywhere = the environment, not the test
  const env = f && (ENV_RE.test(f.error || '') || (/^Navigate to/i.test(f.text) && /Timeout/i.test(f.error || '') && i === 0));
  let outcome = { PASSED: 'passed', FAILED: 'failed', PARTIAL: 'partial' }[status] || 'not_completed';
  if (outcome === 'failed' && env) outcome = 'not_completed';
  const category = outcome === 'not_completed' ? 'environment' : outcome === 'failed' ? 'needs_review' : null;
  const summary = outcome === 'passed' ? `All ${counts.passed} steps passed.`
    : outcome === 'partial' ? `${counts.needs_ai} step(s) needed AI and did not run; the others passed.`
    : f ? `Step ${f.n} ${outcome === 'not_completed' ? 'could not run' : 'failed'}: ${f.text} - ${f.error || 'no error text'}`
    : 'The run did not finish.';
  return { outcome, category, summary, counts, failed_step };
}

async function run() {
  const c = needLogin();
  const caseRef = flag('--case');
  const file = flag('--file');
  const text = positional().join(' ').trim();
  const pid = Number(flag('--project') || c.project) || null;
  let content, caseId = null, title = flag('--title'), loginPid = pid, savedFile = null;

  if (caseRef) {   // a saved LogiTestHub test case
    caseId = Number(String(caseRef).replace(/^#/, ''));
    if (!caseId) fail('--case takes the test case id (the number in /cases/<id>; tester cases <project> lists them).');
    const j = await api(c, 'GET', `/cases/${caseId}`);
    content = j.case.content;
    loginPid = j.case.project_id;
    // keep a copy in the workspace tests/ the first time (a file already linked to the case is left as it is)
    const ws = workspace(true);
    if (ws) {
      const f = caseFile(ws.dir, `${j.case.tc.replace('-', '')}_${slug(j.case.title)}`, caseId);
      if (!fs.existsSync(f)) fs.writeFileSync(f, frontSet(content.replace(/\r\n/g, '\n'), 'case_id', caseId));
      savedFile = f;
    }
    if (/@import\s/.test(content)) console.error('Note: this case uses @import helpers; they must exist next to it on this PC, or run it from the LogiTestHub web page.');
  } else if (file && file !== true) {
    content = fs.readFileSync(file, 'utf8');
    savedFile = path.resolve(file);
    // a file from tests/ that is linked to its LogiTestHub case: the run is saved under that case
    const linked = Number(frontGet(content, 'case_id'));
    if (linked && !argv.includes('--new-case')) caseId = linked;
  } else if (text) {
    content = toCase(text, urlOpt(), title);
    const ws = workspace(true);   // first use in a project: makes tests/
    if (ws) {
      savedFile = caseFile(ws.dir, slug(title || text.split(/[.;\n]/)[0]));
      fs.writeFileSync(savedFile, content);
      if (!JSON_OUT) console.error(`Saved the test case as ${rel(savedFile)}`);
    }
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
  // A login helper value left out is typed literally ("{{username}}") and every step after it fails on the login page
  const loginMissing = [...new Set([...loginText.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map(x => x[1]))].filter(n => !vars[n]);
  if (loginMissing.length) fail(`the project's login helper needs ${loginMissing.join(', ')}: add ` +
    loginMissing.map(n => /pass|pwd|secret|pin/i.test(n) ? `--secret ${n}=<value>` : `--var ${n}=<value>`).join(' ') +
    ' (or --vars file.json, or --no-login).');

  // A passed run of this case may have kept its test data ("## Clean up" runs only after a failure): remove it first?
  let cleared = [];
  const envName = flag('--env') && flag('--env') !== true ? flag('--env') : '';
  if (caseId && !argv.includes('--no-upload') && !argv.includes('--keep')) {
    const k = await api(c, 'GET', `/cases/${caseId}/kept-data${envName ? '?env=' + encodeURIComponent(envName) : ''}`, null, false, true)
      .catch(() => ({ ids: [] }));
    if (k.ids && k.ids.length) {
      let clean = argv.includes('--clean');
      if (!clean) {
        if (JSON_OUT || !process.stdin.isTTY)
          fail(`a passed run of this case kept its test data (${k.runs.join('; ')}). Run again with --clean (delete it first) or --keep (leave it).`,
            2, { kept_data: k.runs });
        clean = /^y/i.test(await ask(`A passed run kept its test data (${k.runs.join('; ')}). Delete it before this run? [y/N] `));
      }
      if (clean) {   // its clean-up steps go first, in a section of their own, after the title
        const lines = content.replace(/\r\n/g, '\n').split('\n');
        let i = 0;
        if (lines[0] && lines[0].trim() === '---') i = Math.max(0, lines.findIndex((l, n) => n > 0 && l.trim() === '---') + 1);
        while (i < lines.length && !lines[i].trim()) i++;
        if (lines[i] && lines[i].startsWith('# ')) i++;
        content = [...lines.slice(0, i), '', k.section, ...lines.slice(i)].join('\n');
        cleared = k.ids;
      }
    }
  }

  fs.mkdirSync(RUNS, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lth-'));
  const mdPath = path.join(tmp, 'cli_test.md');
  const varsPath = path.join(tmp, 'vars.json');
  fs.writeFileSync(mdPath, content);
  fs.writeFileSync(varsPath, JSON.stringify(vars), { mode: 0o600 });
  const loginPath = loginText ? path.join(tmp, 'login.md') : null;
  if (loginPath) fs.writeFileSync(loginPath, loginText);
  const live = (running) => api(c, 'POST', '/task/running',
    { running, ...(flag('--task') && flag('--task') !== true ? { task: flag('--task') } : {}) }, false, true).catch(() => null);
  let res;
  await live(true);   // PM's Tester CLI button blinks while this runs (a task started from PM only)
  try {
    res = await runRunner(mdPath, varsPath, {
      LTH_RUNS_DIR: RUNS, LTH_SERVER: c.server, LTH_USER: c.username, LTH_KEY: c.key, LTH_CASE_ID: caseId || '',
      LTH_ENV: flag('--env') && flag('--env') !== true ? flag('--env') : '',   // database steps: which environment's DB
      // @import helpers: the workspace tests/helpers/ (not the PC-wide default)
      ...(workspace() ? { LTH_HELPERS_DIR: path.join(workspace().dir, 'helpers') } : {}),
    }, loginPath, savedFile ? path.dirname(savedFile) : null);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });   // the vars file can hold passwords
    await live(false);
  }
  const m = res.out.match(/REPORT : (.+)/);
  if (!m) {
    if (/Executable doesn't exist|playwright install/i.test(res.out + res.err)) fail('the browser is not installed yet. Run: tester setup');
    fail(`the run did not finish: ${(res.err || res.out).trim().split('\n').pop()}`, 5,
      { outcome: 'not_completed', category: 'run_stopped', summary: 'The browser run stopped before the end (crash, killed or out of memory). Run it again.' });
  }
  const report = m[1].trim();
  const runDir = path.dirname(report);
  const status = (res.out.match(/RESULT : (\w+)/) || [])[1] || 'ERROR';
  const result = { ok: true, status, ...analyse(runDir, status), report, run_dir: runDir };

  if (!argv.includes('--no-upload')) {
    if (!caseId && !pid) {
      result.upload = { ok: false, error: 'not saved: no --project given' };
      if (!JSON_OUT) console.log('\nNot saved to LogiTestHub: add --project <id> (tester projects) to keep this run there.');
    } else {
      const fields = caseId ? { case_id: caseId } : { project_id: pid, content: frontDel(content, 'case_id'), title };
      if (flag('--task') && flag('--task') !== true) fields.task = flag('--task');   // else the server uses your active task
      if (envName) fields.env = envName;   // the environment whose data the run used (kept-data questions follow it)
      if (cleared.length) fields.cleared = cleared.join(',');   // kept data this run removed first
      fs.writeFileSync(path.join(runDir, UPLOAD_FILE), JSON.stringify(fields));
      if (!JSON_OUT) console.log('\nUploading the run to LogiTestHub ...');
      result.upload = await uploadRun(c, runDir, fields, result);
      if (result.upload.ok && pid) saveConfig({ ...loadConfig(), project: pid });
      // link the new file to the case LogiTestHub just made, so its next runs go under the same case
      if (result.upload.ok && savedFile && !caseId && result.case_id && fs.existsSync(savedFile))
        fs.writeFileSync(savedFile, frontSet(fs.readFileSync(savedFile, 'utf8'), 'case_id', result.case_id));
    }
  }
  if (savedFile) result.file = savedFile;
  // remember what was given explicitly (not the last project used elsewhere on this PC)
  if (workspace()) wsSet({ project: typeof flag('--project') === 'string' ? pid : caseRef ? loginPid : undefined,
                           url: typeof flag('--url') === 'string' ? flag('--url') : undefined });
  await reportWs(c);
  if (!JSON_OUT && savedFile) console.log(` FILE   : ${rel(savedFile)}`);
  if (!JSON_OUT) console.log(` RESULT : ${result.outcome.toUpperCase()}${result.category ? ' (' + result.category + ')' : ''} - ${result.summary}`);
  print(result);
  process.exitCode = result.outcome === 'not_completed' ? 5 : ({ PASSED: 0, FAILED: 1, PARTIAL: 3 }[status] ?? 2);
}

// tester test [folder | file ...]: run every *_test.md in the workspace tests/ (or the given ones), one after the
// other, each exactly like `tester run --file` (same options). Exit code: the worst result.
async function testAll() {
  const ws = workspace();
  const given = positional();
  const files = [];
  for (const g of given.length ? given : ws ? [ws.dir] : []) {
    if (!fs.existsSync(g)) fail(`not found: ${g}`);
    if (fs.statSync(g).isDirectory()) {
      const walk = d => fs.readdirSync(d, { withFileTypes: true }).forEach(e => {
        const p = path.join(d, e.name);
        if (e.isDirectory() && !e.name.startsWith('.')) walk(p);
        else if (e.isFile() && e.name.endsWith('_test.md')) files.push(p);
      });
      walk(g);
    } else files.push(g);
  }
  if (!files.length) fail(ws ? `no *_test.md in ${rel(ws.dir)} yet. Make one: tester run "<what to test>"`
    : `no ${WS_DIR}/ folder here. Run inside your project (tester init), or: tester test <folder>`);
  files.sort();
  // the options for every run: everything except the command, its folder / file words and --json
  const skip = new Set(given);
  const pass = argv.slice(1).filter(a => a !== '--json' && !skip.has(a));
  const results = [];
  for (const [i, f] of files.entries()) {
    if (!JSON_OUT) console.log(`\n[${i + 1}/${files.length}] ${rel(f)}`);
    const out = await new Promise(resolve => {
      const child = spawn(process.execPath, [__filename, 'run', '--file', f, ...pass, '--json'], { stdio: ['ignore', 'pipe', 'inherit'] });
      let s = '';
      child.stdout.on('data', d => { s += d; });
      child.on('close', code => resolve({ code, s }));
    });
    let r;
    try { r = JSON.parse(out.s.slice(out.s.indexOf('{'))); } catch { r = { outcome: 'not_completed', summary: 'no result (see above)' }; }
    results.push({ file: f, exit: out.code, outcome: r.outcome || (r.ok === false ? 'error' : 'not_completed'),
      summary: r.summary || r.error || '', url: r.url, case_id: r.case_id });
    if (!JSON_OUT) console.log(`  ${(r.outcome || 'error').toUpperCase()} - ${r.summary || r.error || ''}${r.url ? `\n  ${r.url}` : ''}`);
  }
  const count = o => results.filter(r => r.outcome === o).length;
  const totals = { total: results.length, passed: count('passed'), failed: count('failed'), partial: count('partial'),
    not_completed: results.length - count('passed') - count('failed') - count('partial') };
  print({ ok: true, ...totals, results },
    `\n${totals.passed}/${totals.total} passed` + (totals.failed ? `, ${totals.failed} failed` : '') +
    (totals.partial ? `, ${totals.partial} partial` : '') + (totals.not_completed ? `, ${totals.not_completed} not completed` : ''));
  process.exitCode = totals.failed ? 1 : totals.not_completed ? 5 : totals.partial ? 3 : 0;
}

// upload a finished run; a network / server problem here never hides the run's own result
async function uploadRun(c, runDir, fields, result) {
  try {
    const up = await upload(c, runDir, fields);
    Object.assign(result, { run_id: up.run_id, case_id: up.case_id, url: up.url });
    if (!JSON_OUT) console.log(` VIEW   : ${up.url}`);
    return { ok: true };
  } catch (e) {
    if (!JSON_OUT) console.log(` Not saved to LogiTestHub: ${e.message}\n Try again later: tester upload "${runDir}"`);
    return { ok: false, error: e.message, retry: `tester upload "${runDir}"` };
  }
}

// tester upload <run dir>: send a run whose upload failed (LogiTestHub was unreachable or overloaded)
async function uploadCmd() {
  const c = needLogin();
  const runDir = positional()[0];
  if (!runDir || !fs.existsSync(path.join(runDir, 'events.ndjson'))) fail('usage: tester upload <run dir>   (the run_dir from the run result)');
  let fields;
  try { fields = JSON.parse(fs.readFileSync(path.join(runDir, UPLOAD_FILE), 'utf8')); } catch { fail(`no ${UPLOAD_FILE} in ${runDir}: this run was not meant to be saved (run it again with --project or --case)`); }
  let status = 'ERROR';
  try { status = JSON.parse(fs.readFileSync(path.join(runDir, 'events.ndjson'), 'utf8').trim().split('\n').pop()).status || status; } catch { /* keep ERROR */ }
  const result = { ok: true, status, ...analyse(runDir, status), run_dir: runDir };
  result.upload = await uploadRun(c, runDir, fields, result);
  print(result, result.upload.ok ? '' : `Not saved: ${result.upload.error}`);
  process.exitCode = result.upload.ok ? 0 : 2;
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
  tester init [--project <id|name>] [--url <site>]  make ${WS_DIR}/ in this project for its test cases (also made on first use)
  tester setup                                      install the headless browser (once)
  tester whoami | tester logout
  tester projects                                    list projects
  tester cases <project id>                          list test cases
  tester case pull <case id> [--out file.md]         save a case's steps to a file (to fix and run it)
  tester case push <case id> --file file.md [--dry-run] [--force] [--note "why"]
                                                     save the fixed steps back into LogiTestHub
  tester run "<test in plain English>" --url <site> [--project <id>]
  tester run --case <case id> --url <site>           run a saved test case
  tester run --case <id> ... --clean | --keep          a passed run kept its test data: delete it first, or leave it
  tester check --case <id> | --file x_test.md --env <name>   before a run: settings + test data there? (read-only)
  tester settings --project <id> --env <name>        live profile / retail settings: is the action allowed?
  tester brain [words] [--path <repo or knowledge_brain>]   the app's knowledge brain docs for a test + out-of-date check
  tester run --file my_test.md --url <site>          run a test case file
  tester test [folder | files]                       run every *_test.md in ${WS_DIR}/ (same options as run)
        [--headed] [--no-ai] [--no-upload] [--no-login] [--no-save] [--env <name>] [--var k=v] [--secret k=v] [--vars file.json]
        (--env: the LogiTestHub environment whose read-only database "Assert SQL" / "Remember SQL" steps use)
        (the project's login helper runs first; --no-login, or "login: none" in the case, skips it)
  tester generate "<task / bug / requirement>" --project <id> [--count 6] [--diff [base-branch]]
  tester upload <run dir>                            save a run whose upload failed (LogiTestHub unreachable / busy)
  tester usage                                       your AI use this month
  tester lessons <project id|name>                   lessons learned: read before writing / running a test
  tester task start PM-LMX-1741 | tester task stop | tester task   PM task: tests you create / run are linked to it
  tester task PM-LMX-1741                            the task's test cases and their latest results (+ the page PM shows)
  tester lesson add --project <id|name> --kind <app_bug|test_outdated|environment|test_data|agent_mistake>
        --title "<what went wrong>" --avoid "<rule for next time>" [--symptom ..] [--fix ..] [--case <id>] [--run <id>]
  tester skill install                               add the /tester skill to Claude Code (~/.claude/skills/tester)
  tester modules pull --project <id|name> [--out logitesthub.modules.json]     save the module tree as a file
  tester modules push <file> [--project <id|name>] [--dry-run]                  add the file's NEW modules (never deletes)
  tester modules build --rows menu.tsv --project <name> [--out file]            menu-table rows -> modules file
        [--id id_menu] [--label label] [--parent parent] [--link link] [--sort sort] [--active active] [--source text]

  --json   machine-readable output (for AI agents and CI)
  Exit codes: 0 passed, 1 failed, 3 partial (some steps need AI), 4 AI limit reached,
              5 not completed (site / network / server problem, or the run stopped: not a test result), 2 other errors.
  A run's JSON: status, outcome (passed|failed|partial|not_completed), category (environment|needs_review|...),
              summary, counts, failed_step {n, text, error, screenshot, screenshot_before}, url, upload {ok, error, retry}.
  Inside a project, test cases are saved as files in ${WS_DIR}/ (made the first time; ${WS_DIR}/${WS_FILE} keeps the
  LogiTestHub project and site URL, so --project / --url can be left out). --no-save: do not write a file.
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

// ---------------------------------------------------------------- case pull / push: a fix made while debugging goes back
// into the LogiTestHub case, so the next run (web, Test Run, a teammate, the next session) starts from the fixed steps.

const PULLED = path.join(HOME, 'pulled.json');   // case id -> updated_at of the version pulled (conflict check on push)
const BACKUPS = path.join(HOME, 'case-backups');

function lineDiff(a, b) {   // small LCS line diff: ['- old', '+ new'] (cases are tens of lines)
  const x = a.split('\n'), y = b.split('\n');
  const L = Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < x.length || j < y.length) {
    if (i < x.length && j < y.length && x[i] === y[j]) { i++; j++; }
    else if (j < y.length && (i >= x.length || L[i][j + 1] > L[i + 1][j])) out.push('+ ' + y[j++]);
    else out.push('- ' + x[i++]);
  }
  return out;
}

async function caseCmd() {
  const c = needLogin();
  const [sub, ref] = positional();
  const id = Number(String(ref || '').replace(/^#/, ''));
  if (!['pull', 'push'].includes(sub) || !id) fail('usage: tester case pull <case id> [--out file.md]  |  tester case push <case id> --file file.md [--dry-run] [--force] [--note "why"]');
  let pulled = {};
  try { pulled = JSON.parse(fs.readFileSync(PULLED, 'utf8')); } catch { /* none yet */ }
  const cur = (await api(c, 'GET', `/cases/${id}`)).case;

  if (sub === 'pull') {
    const ws = workspace(true);
    const out = (flag('--out') !== true && flag('--out'))
      || (ws ? rel(caseFile(ws.dir, `${cur.tc.replace('-', '')}_${slug(cur.title)}`, id)) : `${cur.tc}_test.md`);
    fs.writeFileSync(out, ws ? frontSet(cur.content.replace(/\r\n/g, '\n'), 'case_id', id) : cur.content);
    fs.mkdirSync(HOME, { recursive: true });
    fs.writeFileSync(PULLED, JSON.stringify({ ...pulled, [id]: cur.updated_at }, null, 2));
    await reportWs(c);
    return print({ ok: true, id, tc: cur.tc, title: cur.title, file: path.resolve(out), updated_at: cur.updated_at },
      `${cur.tc} ${cur.title}\nSaved to ${path.resolve(out)}. Edit it, run it (tester run --file ${out} --project ${cur.project_id}), then: tester case push ${id} --file ${out}`);
  }

  const file = flag('--file');
  if (!file || file === true) fail(`tester case push ${id} --file <the fixed _test.md>`);
  let content;
  try { content = frontDel(fs.readFileSync(file, 'utf8'), 'case_id').replace(/^﻿/, '').replace(/\r\n/g, '\n').trim() + '\n'; } catch (e) { fail(`cannot read ${file}: ${e.message}`); }
  const force = argv.includes('--force');
  const base = pulled[id];
  if (base && base !== cur.updated_at && !force)
    fail(`${cur.tc} was changed by ${cur.updated_by || 'someone'} at ${cur.updated_at}, after you pulled it. Pull it again (tester case pull ${id}) and redo your change, or push with --force.`, 2, { error: 'conflict' });
  const diff = lineDiff(cur.content.replace(/\r\n/g, '\n').trim() + '\n', content);
  if (!diff.length) return print({ ok: true, id, tc: cur.tc, changed: false }, `${cur.tc}: no change - LogiTestHub already has these steps.`);
  const shown = diff.slice(0, 60).join('\n') + (diff.length > 60 ? `\n… (+${diff.length - 60} more lines)` : '');
  if (argv.includes('--dry-run')) return print({ ok: true, id, tc: cur.tc, dry_run: true, diff }, `${cur.tc} ${cur.title} - DRY RUN, nothing saved\n${shown}`);

  fs.mkdirSync(BACKUPS, { recursive: true });   // the server keeps no history of steps: keep the old version here
  const backup = path.join(BACKUPS, `${cur.tc}-${new Date().toISOString().replace(/[:.]/g, '-')}.md`);
  fs.writeFileSync(backup, cur.content);
  const note = flag('--note');
  const j = await api(c, 'PUT', `/cases/${id}`, { content, expected_updated_at: force ? undefined : (base || cur.updated_at), note: note === true ? undefined : note });
  fs.writeFileSync(PULLED, JSON.stringify({ ...pulled, [id]: j.updated_at }, null, 2));
  print({ ok: true, id, tc: cur.tc, changed: j.changed, title: j.title || cur.title, diff, backup, url: `${c.server}/cases/${id}` },
    `Saved ${cur.tc} in LogiTestHub: ${c.server}/cases/${id}\n${shown}\nOld version kept in ${backup}`);
}

// ---------------------------------------------------------------- main

// PM task: while a task is active every test you create or run is linked to it (also started from the PM task page)
async function taskCmd() {
  const c = needLogin();
  const [sub, code] = positional();
  if (sub === 'start' && code) {
    const j = await api(c, 'POST', `/tasks/${encodeURIComponent(code)}/start`);
    return print(j, `Testing ${j.task}: every test you create or run is linked to it for ${j.hours} hours or until: tester task stop` +
      (j.stopped ? `\n(${j.stopped} was stopped)` : '') + `\n${j.url}`);
  }
  if (sub === 'stop') {
    const j = await api(c, 'POST', '/task/stop');
    return print(j, j.stopped ? `Stopped testing ${j.stopped}.` : 'No task was active.');
  }
  if (sub && sub !== 'show') {   // tester task PM-LMX-1741
    const j = await api(c, 'GET', `/tasks/${encodeURIComponent(sub)}`);
    return print(j, `${j.task}: ${j.cases} test case(s), ${j.passed} passed, ${j.failed} failed\n${j.url}\n` +
      j.tests.map(x =>`  ${x.tc.padEnd(8)} ${x.status.padEnd(8)} ${x.title}`).join('\n'));
  }
  const j = await api(c, 'GET', '/task');
  print(j, j.task ? `Active task: ${j.task} (tester task stop to end it)` : 'No active task. Start one: tester task start PM-LMX-1741');
}

// Precondition check + test data validation (fail fast): the case's leading database steps - settings, permissions,
// the data it picks - run read-only on the environment before a run. Exit 0 passed, 1 failed (do not run), 2 error.
async function check() {
  const c = needLogin();
  const caseRef = flag('--case'), file = flag('--file');
  const env = flag('--env') && flag('--env') !== true ? flag('--env') : (loadConfig().env || '');
  if (!env) fail('say which environment: tester check --case <id> --env <name> (its database is checked).');
  let body;
  if (caseRef && caseRef !== true) body = { case_id: Number(String(caseRef).replace(/^#/, '')) };
  else if (file && file !== true) body = { content: fs.readFileSync(file, 'utf8') };
  else fail('usage: tester check --case <id> --env <name>   |   tester check --file x_test.md --env <name>');
  const j = await api(c, 'POST', '/check', { ...body, env });
  const lines = (j.checks || []).map(x => `  ${x.ok ? 'OK  ' : 'FAIL'} step ${String(x.n).padStart(2)}  ${x.text.slice(0, 90)}` +
    (x.ok ? (x.got != null ? `  = ${String(x.got).slice(0, 40)}` : '') : `\n             ${x.problem}`));
  const el = j.elements || {};
  const elLine = el.checked ? (el.missing.length ? `  FAIL elements: ${el.missing.map(e => `step ${e.n} ${e.problem || e.missing.join(' ') + ' not in the code'}`).join('; ')}`
    : `  OK   elements: all ${el.checked} ids / classes found in the code`) : '';
  print(j, [`Precondition check on ${j.env}: ${String(j.status).toUpperCase()}`, ...lines, elLine, j.message].filter(Boolean).join('\n'));
  process.exitCode = j.status === 'failed' ? 1 : j.status === 'no_database' ? 2 : 0;
}

// Settings / permissions that decide whether a test can run (knowledge_brain/<project>/settings.json), read now
async function settingsCmd() {
  const c = needLogin();
  const env = flag('--env') && flag('--env') !== true ? flag('--env') : '';
  if (!env) fail('usage: tester settings --project <id|name> --env <name>');
  const p = await resolveProject(c, flag('--project') || c.project);
  const j = await api(c, 'GET', `/projects/${p.id}/settings?env=${encodeURIComponent(env)}`);
  print(j, j.text);
}

// The app repo's own knowledge_brain (inside the code the developer works on): which module / flow docs to read for a
// test, and whether they are out of date (code files of that module changed after the doc's source_ref, or not
// committed yet = a feature in progress). Found from the current folder upward; else the saved path (--path).
const BRAIN_READ = ['FUNCTIONAL_BRAIN.md', 'PERMISSION_MAP.md', 'SETTINGS_REGISTER.md', 'SCREENS.md', 'BUSINESS_RULES.md',
  'FIELD_DICTIONARY.md', 'MODULE_BRAIN.md'];
const BRAIN_ALIAS = { sale: ['Billing'], sales: ['Billing'], bill: ['Billing'], invoice: ['Billing'], billing: ['Billing'],
  cancel: ['_cancel_master'], tag: ['Tagging'], tagging: ['Tagging'], lot: ['Lot', 'lot_inward'],
  estimation: ['Estimation', 'estimation_billing'], chit: ['scheme', 'payment'], scheme: ['scheme'], installment: ['payment'],
  instalment: ['payment'], transfer: ['Branch Transfer', 'branch_transfer'], grn: ['GRN'],
  karigar: ['Smith_Metal_Issue', 'Purchase_Order'], order: ['customer_order', 'Purchase_Order'] };
const BRAIN_STOP = new Set(['the', 'and', 'for', 'pannu', 'panni', 'check', 'test', 'oru', 'list', 'page', 'screen', 'add', 'new']);
function findBrain() {
  let d = process.cwd();
  for (let i = 0; i < 6; i++) {
    if (fs.existsSync(path.join(d, 'knowledge_brain'))) return path.join(d, 'knowledge_brain');
    const up = path.dirname(d); if (up === d) break; d = up;
  }
  const saved = loadConfig().brain;
  return saved && fs.existsSync(saved) ? saved : null;
}
function brainWords(t) { return new Set((String(t).toLowerCase().replace(/_/g, ' ').match(/[a-z]{3,}/g) || []).filter(w => !BRAIN_STOP.has(w))); }
function brainMatch(names, want, boost) {
  return names.map(n => {
    const ws = brainWords(n); let sc = boost[n] || 0;
    for (const w of ws) { if (want.has(w)) sc += 1; else for (const v of want) if (w.length > 3 && (w.startsWith(v) || v.startsWith(w))) sc += 0.5; }
    return [sc, n];
  }).filter(x => x[0] > 0).sort((a, b) => b[0] - a[0]).slice(0, 2).map(x => x[1]);
}
function brainFront(file) {
  try { const m = fs.readFileSync(file, 'utf8').slice(0, 4000).match(/^---\s*\n([\s\S]*?)\n---/); const o = {};
    (m ? m[1].split('\n') : []).forEach(l => { const i = l.indexOf(':'); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 1).split('#')[0].trim(); }); return o;
  } catch { return {}; }
}
function brainStale(brain, mod) {
  const repo = path.dirname(brain);
  const meta = brainFront(path.join(brain, mod, 'PERMISSION_MAP.md'));
  const meta2 = Object.keys(meta).length ? meta : brainFront(path.join(brain, mod, 'MODULE_BRAIN.md'));
  const out = { source_ref: meta2.source_ref || '', last_updated: meta2.last_updated || '', changed_since: [], uncommitted: [] };
  let files = [];
  try {
    for (const line of fs.readFileSync(path.join(brain, '_SYSTEM', 'FILE_MODULE_MAP.md'), 'utf8').split('\n')) {
      const c = line.split('|').map(x => x.trim());
      if (c.length > 3 && c[1].startsWith('`') && c[2].split(',').map(x => x.trim().toLowerCase()).includes(mod.toLowerCase())) files.push(c[1].replace(/`/g, ''));
    }
  } catch { /* no map */ }
  if (!files.length || !fs.existsSync(path.join(repo, '.git'))) return out;
  const git = args => { try { return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }); } catch { return ''; } };
  if (/^[0-9a-f]{7,40}$/.test(out.source_ref)) out.changed_since = git(['diff', '--name-only', `${out.source_ref}..HEAD`, '--', ...files]).split('\n').filter(Boolean);
  out.uncommitted = git(['status', '--porcelain', '--', ...files]).split('\n').filter(x => x.length > 3).map(x => x.slice(3));
  return out;
}
async function brainCmd() {
  if (flag('--path') && flag('--path') !== true) {
    const want = path.resolve(flag('--path'));
    const dir = fs.existsSync(path.join(want, 'knowledge_brain')) ? path.join(want, 'knowledge_brain') : want;
    if (!fs.existsSync(dir)) fail(`no such folder: ${dir}`);
    saveConfig({ ...loadConfig(), brain: dir });
    if (!positional().length) return print({ ok: true, brain: dir }, `Saved: the knowledge brain is ${dir}`);
  }
  const brain = findBrain();
  if (!brain) fail('no knowledge_brain found here. Run this inside the app repo, or ask the user where it is and save it: tester brain --path <folder>', 3);
  const dirs = fs.readdirSync(brain).filter(x => !x.startsWith('_') && fs.statSync(path.join(brain, x)).isDirectory());
  const fdir = path.join(brain, '_SYSTEM', 'FLOW_CHECKLISTS');
  const flowsAll = fs.existsSync(fdir) ? fs.readdirSync(fdir).filter(x => x.endsWith('.md')).map(x => x.slice(0, -3)) : [];
  const text = positional().join(' ');
  if (!text) return print({ ok: true, brain, modules: dirs, flows: flowsAll }, `Knowledge brain: ${brain}\nModules: ${dirs.join(', ')}\nFlow checklists: ${flowsAll.join(', ')}`);
  const want = brainWords(text), boost = {};
  for (const w of want) for (const t of (BRAIN_ALIAS[w] || [])) boost[t] = (boost[t] || 0) + 2;
  const mods = brainMatch(dirs, want, boost), fls = brainMatch(flowsAll, want, boost);
  const res = { ok: true, brain, modules: mods.map(m => ({ module: m, read: BRAIN_READ.filter(f => fs.existsSync(path.join(brain, m, f))).map(f => path.join(brain, m, f)), ...brainStale(brain, m) })),
    flows: fls.map(f => path.join(fdir, f + '.md')) };
  const lines = [`Knowledge brain: ${brain}`];
  if (!mods.length && !fls.length) lines.push(`No module matched "${text}". Modules: ${dirs.join(', ')}`);
  for (const m of res.modules) {
    lines.push(`Module ${m.module}  (measured at ${m.source_ref || '?'}, ${m.last_updated || '?'})`);
    m.read.forEach(f => lines.push(`  read ${f}`));
    if (m.changed_since.length || m.uncommitted.length)
      lines.push(`  ! OUT OF DATE: ${m.changed_since.length} code file(s) changed since, ${m.uncommitted.length} not committed (feature in progress): ` +
        [...m.uncommitted, ...m.changed_since].slice(0, 8).join(', ') + '\n    -> read those files; where the doc and the code disagree, the code is right; tell the user which doc is out of date');
    else lines.push('  current: no code change since the doc');
  }
  res.flows.forEach(f => lines.push(`Flow checklist: ${f}`));
  print(res, lines.join('\n'));
}

const COMMANDS = { init, test: testAll, task: taskCmd, check, settings: settingsCmd, brain: brainCmd, login, logout, whoami, projects, cases, case: caseCmd, usage, generate, run, setup, help, modules, skill, lessons, lesson, upload: uploadCmd };
(async () => {
  const cmd = argv[0];
  if (!cmd || cmd === '--help' || cmd === '-h') return help();
  if (cmd === '--version' || cmd === '-v') return console.log(PKG.version);
  const fn = COMMANDS[cmd];
  if (!fn) fail(`unknown command "${cmd}". Try: tester help`);
  await fn();
})().catch(e => fail(e.message));
