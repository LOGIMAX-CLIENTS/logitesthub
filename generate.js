#!/usr/bin/env node
// Generates _test.md cases for a task / bug / requirement, optionally grounded in a code diff.
// Usage:
//   node generate.js --task "GRN entry must block negative pcs" [--task-file req.txt]
//                    [--repo C:/xampp/htdocs/wt-prod] [--base PRODUCTION] [--paths admin/...]
//                    [--count 6] [--name grn-negative-pcs]
// With --repo and no --base, uncommitted changes (git diff HEAD) are used.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { generateCases, explain, MODEL } = require('./lib/ai');

const VARS_FILE = process.env.LTH_VARS_FILE || 'F:/TestMU-Ai/.testmuai/variables/etail.json';   // Settings -> System
const LOGIN_HELPER = require('path').join(process.env.LTH_HELPERS_DIR || 'F:/TestMU-Ai/.testmuai/tests/Retail/etail-lot-inward/helpers', 'login.md');
const MAX_DIFF_CHARS = 400000;

function parseArgs(argv) {
  const a = { task: '', repo: '', base: '', paths: [], count: 6, name: '' };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = argv[i + 1];
    if (k === '--task') { a.task = v; i++; }
    else if (k === '--task-file') { a.task = fs.readFileSync(v, 'utf8'); i++; }
    else if (k === '--repo') { a.repo = v; i++; }
    else if (k === '--base') { a.base = v; i++; }
    else if (k === '--paths') { a.paths.push(v); i++; }
    else if (k === '--count') { a.count = Number(v) || 6; i++; }
    else if (k === '--name') { a.name = v; i++; }
    else if (k === '--diff-file') { a.diffFile = v; i++; }
    else if (k === '--lessons-file') { a.lessons = fs.readFileSync(v, 'utf8'); i++; }   // project lessons from LogiTestHub
    else if (k === '--project-login') { a.projectLogin = true; }   // the project has a login helper: cases start after login   // diff sent by the lth CLI (made on the developer's PC)
  }
  return a;
}

function readDiff(repo, base, paths) {
  const range = base ? [`${base}...HEAD`] : ['HEAD'];
  const out = execFileSync('git', ['-C', repo, 'diff', '--no-color', ...range, '--', ...paths], {
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  });
  return out;
}

const slugify = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) || 'case';

function toMarkdown(c) {
  const lines = ['---', 'mode: testing', 'max_steps: 30', 'timeout: 300', `tags: [${c.kind}, generated]`, 'variables: {}', '---', '', `# ${c.title}`, '', `> ${c.kind} · ${c.why}`, ''];
  for (const s of c.sections) {
    lines.push(`## ${s.heading}`);
    for (const step of s.steps) lines.push(step);
    lines.push('');
  }
  return lines.join('\n');
}

(async () => {
  const args = parseArgs(process.argv.slice(2));
  if (!args.task.trim()) {
    console.error('Usage: node generate.js --task "<what changed / the bug / the requirement>" [--repo <path>] [--base <branch>] [--paths <dir>] [--count N]');
    process.exit(2);
  }
  if (!process.env.ANTHROPIC_API_KEY) {
    console.error('ANTHROPIC_API_KEY is not set.');
    process.exit(2);
  }

  let diff = '';
  if (args.diffFile || args.repo) {
    diff = args.diffFile ? fs.readFileSync(args.diffFile, 'utf8') : readDiff(args.repo, args.base, args.paths);
    if (!diff.trim()) console.warn('Note: the diff is empty; generating from the task text only.');
    if (diff.length > MAX_DIFF_CHARS) {
      console.error(`The diff is ${diff.length} characters, too large to send whole. Narrow it with --paths <folder or file>.`);
      process.exit(2);
    }
  }

  const variableNames = Object.keys(JSON.parse(fs.readFileSync(VARS_FILE, 'utf8')));
  // With a project login helper the runner signs in by itself, so the cases must not contain login steps.
  const loginSteps = args.projectLogin ? '' : (fs.existsSync(LOGIN_HELPER)
    ? fs.readFileSync(LOGIN_HELPER, 'utf8').split(/\r?\n/).filter(l => l.trim() && !l.startsWith('#')).join('\n') : '');

  console.log(`Generating ~${args.count} cases with ${MODEL}${diff ? ` from the task + ${diff.split('\n').length} diff lines` : ' from the task'} ...`);
  let result;
  try {
    result = await generateCases({ requirement: args.task, diff, variableNames, loginSteps, count: args.count, lessons: args.lessons });
  } catch (e) {
    console.error(explain(e));
    process.exitCode = 1; // not process.exit(): let the SDK close its stream handles first
    return;
  }

  const stamp = new Date().toISOString().slice(0, 10);
  const dir = path.join(__dirname, 'generated', `${stamp}-${slugify(args.name || args.task.split('\n')[0])}`);
  fs.mkdirSync(dir, { recursive: true });
  const index = [`# Generated test cases`, '', `Task: ${args.task.trim()}`, '', '| # | Case | Kind | Protects |', '| --- | --- | --- | --- |'];
  result.cases.forEach((c, i) => {
    const file = `${String(i + 1).padStart(2, '0')}-${slugify(c.slug)}_test.md`;
    fs.writeFileSync(path.join(dir, file), toMarkdown(c));
    index.push(`| ${i + 1} | [${c.title}](${file}) | ${c.kind} | ${c.why} |`);
    console.log(`  ${String(i + 1).padStart(2)}. [${c.kind}] ${c.title}`);
  });
  fs.writeFileSync(path.join(dir, 'CASES.md'), index.join('\n') + '\n');

  console.log(`\nSaved ${result.cases.length} cases to ${dir}`);
  console.log(`Tokens: in ${result.usage.input_tokens}, out ${result.usage.output_tokens}`);
  // One machine-readable line for LogiTestHub's AI usage report.
  console.log('USAGE_JSON ' + JSON.stringify({ model: MODEL, calls: 1, in: result.usage.input_tokens || 0, out: result.usage.output_tokens || 0,
    cacheRead: result.usage.cache_read_input_tokens || 0, cacheWrite: result.usage.cache_creation_input_tokens || 0 }));
  console.log('Review them, then run:  node run-all.js "' + dir + '"');
})();
