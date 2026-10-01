// Loads a TestMU-Ai variables file ({ key: { value, secret? } }) and resolves {{key}} placeholders.
// Secret values are remembered so every log, screenshot caption and report can mask them.
const fs = require('fs');

function loadVars(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const vars = {};
  const secrets = [];
  for (const [k, v] of Object.entries(raw)) {
    vars[k] = v && typeof v === 'object' ? String(v.value ?? '') : String(v ?? '');
    if (v && v.secret) secrets.push(k);
  }
  // Resolve nested references like "{{base_url}}index.php/..." (bounded passes, no infinite loops).
  for (let pass = 0; pass < 5; pass++) {
    for (const k of Object.keys(vars)) vars[k] = fill(vars[k], vars);
  }
  const secretValues = secrets.map(k => vars[k]).filter(s => s && s.length >= 3);
  return { vars, secretValues };
}

function fill(text, vars) {
  return String(text).replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m, k) => (k in vars ? vars[k] : m));
}

function makeMasker(secretValues) {
  return text => {
    let out = String(text ?? '');
    for (const s of secretValues) out = out.split(s).join('******');
    return out;
  };
}

module.exports = { loadVars, fill, makeMasker };
