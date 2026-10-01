const fs = require('fs');
const path = require('path');

// Shared helpers (login.md, ...) for cases that have no folder of their own, e.g. uploaded in the web app.
const DEFAULT_HELPERS = process.env.LTH_HELPERS_DIR || 'F:/TestMU-Ai/.testmuai/tests/Retail/etail-lot-inward/helpers';

// "@import ../helpers/login.md" -> that file's lines, in place (nested imports too).
// Looked up relative to baseDir (the case's own folder), then by file name in the shared helpers folder.
// A missing helper throws: running on without the login steps would only fail later with a confusing error.
function expandImports(text, baseDir, seen = [], depth = 0) {
  return text.split(/\r?\n/).map(raw => {
    const m = raw.trim().match(/^@import\s+(.+?)\s*$/);
    if (!m) return raw;
    const ref = m[1].replace(/^["']|["']$/g, '');
    const tries = [baseDir && path.resolve(baseDir, ref), path.join(DEFAULT_HELPERS, path.basename(ref))].filter(Boolean);
    const file = tries.find(f => fs.existsSync(f) && fs.statSync(f).isFile());
    if (!file) throw new Error(`Helper not found: ${ref} (looked in ${tries.join(' , ')})`);
    if (seen.includes(file) || depth >= 5) throw new Error(`Helper imports itself in a loop: ${ref}`);
    const helper = fs.readFileSync(file, 'utf8').replace(/^---[\s\S]*?\n---\s*\n/, '')
      .split(/\r?\n/).filter(l => !/^# /.test(l.trim())).join('\n');   // a helper's own "# " title must not replace the case title
    return expandImports(helper, path.dirname(file), [...seen, file], depth + 1);
  }).join('\n');
}

// Reads a TestMU-Ai _test.md: front matter is skipped, "# " is the title,
// "## " starts a section, every other non-empty line is one step.
function parseCase(text, baseDir) {
  const body = expandImports(text.replace(/^---[\s\S]*?\n---\s*\n/, ''), baseDir);
  let title = 'Untitled test', section = '';
  const steps = [];
  for (const raw of body.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (/^# /.test(line)) { title = line.slice(2).trim(); continue; }
    if (/^##+ /.test(line)) { section = line.replace(/^##+ /, '').trim(); continue; }
    steps.push({ section, text: line.replace(/^(\d+\.|[-*])\s+/, '') });
  }
  return { title, steps };
}

module.exports = { parseCase, expandImports };
