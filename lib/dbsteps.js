// "Remember SQL ... as name" / "Assert SQL ... returns 'value'": read-only checks on the app's database.
// The runner never sees the database password: it asks the LogiTestHub server, which runs the SELECT in a
// READ ONLY session for the environment (LTH_ENV). Auth: the run token of a server-started run, or the
// Tester CLI user's access key.

function server() {
  const s = process.env.LTH_SERVER;
  if (!s) throw new Error('Database steps need LogiTestHub: run from LogiTestHub, or with the Tester CLI (tester run --env <name>).');
  if (!process.env.LTH_ENV) throw new Error('Database steps need an environment: pick one for the run, or add --env <name> to tester run.');
  return s.replace(/\/+$/, '');
}

function headers() {
  const h = { 'Content-Type': 'application/json' };
  if (process.env.LTH_RUN_TOKEN) h['X-LTH-Run-Token'] = process.env.LTH_RUN_TOKEN;
  else if (process.env.LTH_USER && process.env.LTH_KEY) {
    h.Authorization = 'Basic ' + Buffer.from(`${process.env.LTH_USER}:${process.env.LTH_KEY}`).toString('base64');
  }
  return h;
}

async function call(method, path, body) {
  const res = await fetch(server() + '/api/v1' + path, {
    method, headers: headers(), body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(30000),
  });
  let j = {};
  try { j = await res.json(); } catch { /* not JSON */ }
  if (!res.ok || !j.ok) throw new Error(j.message || j.error || `LogiTestHub HTTP ${res.status}`);
  return j;
}

async function query(sql) {
  return call('POST', '/sql', { env: process.env.LTH_ENV, sql });
}

async function remember(name, value) {
  return call('POST', '/memory', { env: process.env.LTH_ENV, name, value });
}

// Values remembered by earlier tests in this environment; silently empty when there is no server / environment.
async function loadMemory() {
  if (!process.env.LTH_SERVER || !process.env.LTH_ENV) return {};
  try {
    return (await call('GET', `/memory?env=${encodeURIComponent(process.env.LTH_ENV)}`)).values || {};
  } catch { return {}; }
}

module.exports = { query, remember, loadMemory };
