// "Remember SQL ... as name" / "Assert SQL ... returns 'value'": read-only checks on the app's database.
// Normally the runner never sees the database password: it asks the LogiTestHub server, which runs the SELECT in a
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

// ---- a database on this PC (tester env add: the developer's own XAMPP MySQL). LogiTestHub cannot reach it, so the
// Tester CLI passes its details in LTH_LOCAL_DB and the SQL runs here, with the same rules the server applies.
const SELECT_RE = /^\s*(select|with)\b/i;
const READ_BLOCKED = /\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke|rename|lock|unlock|call|handler|load|set|into\s+outfile|into\s+dumpfile|sleep|benchmark|get_lock)\b/i;
const WRITE_START = /^\s*(update|insert|delete)\b/i;
const WRITE_BLOCKED = /\b(drop|alter|create|truncate|grant|revoke|rename|lock|unlock|call|handler|load|into\s+outfile|into\s+dumpfile|sleep|benchmark|get_lock|shutdown|kill)\b|@@/i;
const LIVE_NAME = /\b(live|prod|production)\b/i;
const MAX_ROWS = 20, MAX_WRITE_ROWS = 50;

function localDb() {
  if (!process.env.LTH_LOCAL_DB) return null;
  try { return JSON.parse(process.env.LTH_LOCAL_DB); } catch { return null; }
}

function oneStatement(sql) {
  const s = String(sql || '').trim().replace(/;\s*$/, '').trim();
  if (!s || s.length > 4000) throw new Error('The SQL is empty or too long (max 4000 characters).');
  if (/;|--|\/\*|#/.test(s)) throw new Error('Only one plain statement is allowed: no ";", comments or several statements.');
  return s;
}

async function connect(d) {
  let mysql;
  try { mysql = require('mysql2/promise'); } catch {
    throw new Error('This environment\'s database is on this PC, but the MySQL driver is missing: reinstall the Tester CLI (npm install -g <LogiTestHub address>/cli/tester.tgz).');
  }
  try {
    return await mysql.createConnection({ host: d.host, port: Number(d.port) || 3306, user: d.user, password: d.password || '',
      database: d.name, connectTimeout: 6000, dateStrings: true, supportBigNumbers: true, bigNumberStrings: true });
  } catch (e) {
    throw new Error(`Cannot connect to the database on this PC (${e.code || e.message}). Is MySQL running? Check: tester env list`);
  }
}

const cell = v => (v == null ? null : Buffer.isBuffer(v) ? v.toString('utf8') : String(v));

async function localQuery(d, sql) {
  const s = oneStatement(sql);
  if (!SELECT_RE.test(s)) throw new Error('Only SELECT queries are allowed (the test database is read-only).');
  if (READ_BLOCKED.test(s)) throw new Error('This query uses a word that is not allowed in a read-only check (e.g. INSERT, UPDATE, SLEEP).');
  const conn = await connect(d);
  try {
    await conn.query('SET SESSION TRANSACTION READ ONLY');
    await conn.query('SET SESSION MAX_EXECUTION_TIME=15000').catch(() => conn.query('SET SESSION max_statement_time=15').catch(() => {}));
    let rows, fields;
    try { [rows, fields] = await conn.query({ sql: s, rowsAsArray: true, timeout: 20000 }); }
    catch (e) { throw new Error(`The query failed: ${e.sqlMessage || e.message}`); }
    const out = rows.slice(0, MAX_ROWS).map(r => r.map(cell));
    return { columns: (fields || []).map(f => f.name), rows: out, value: out[0] ? out[0][0] : null, count: rows.length, on_pc: true };
  } finally { await conn.end().catch(() => {}); }
}

async function localWrite(d, sql) {
  const env = process.env.LTH_ENV || '';
  if (!d.set_up_sql) throw new Error(`"Set up SQL" is off for "${env}": turn it on with tester env add --name "${env}" ... --set-up-sql`);
  if (LIVE_NAME.test(env)) throw new Error(`"${env}" looks like a live environment: test-data setup is never run there.`);
  const s = oneStatement(sql);
  const m = s.match(WRITE_START);
  if (!m) throw new Error('A "Set up SQL" step must be one INSERT, UPDATE or DELETE.');
  if (WRITE_BLOCKED.test(s)) throw new Error('This statement uses a word that is not allowed in test-data setup (e.g. DROP, ALTER, TRUNCATE).');
  if (/^(update|delete)$/i.test(m[1]) && !/\bwhere\b/i.test(s)) throw new Error(`${m[1].toUpperCase()} needs a WHERE, so it only changes the test's own rows.`);
  // the write user when one was given (tester env add --db-write-user), else the database user
  const conn = await connect(d.write_user ? { ...d, user: d.write_user, password: d.write_password } : d);
  try {
    await conn.beginTransaction();
    await conn.query('SET SESSION innodb_lock_wait_timeout=10').catch(() => {});
    let res;
    try { [res] = await conn.query({ sql: s, timeout: 20000 }); }
    catch (e) { await conn.rollback().catch(() => {}); throw new Error(`The statement failed: ${e.sqlMessage || e.message}`); }
    if (res.affectedRows > MAX_WRITE_ROWS) {
      await conn.rollback();
      throw new Error(`This would change ${res.affectedRows} rows (max ${MAX_WRITE_ROWS}): nothing was changed. Narrow the WHERE.`);
    }
    await conn.commit();
    return { affected: res.affectedRows, on_pc: true };
  } finally { await conn.end().catch(() => {}); }
}

async function query(sql) {
  const d = localDb();
  if (d) return localQuery(d, sql);
  return call('POST', '/sql', { env: process.env.LTH_ENV, sql });
}

// "Set up SQL ..." - one INSERT / UPDATE / DELETE for test data, only where the environment allows it
async function write(sql) {
  const d = localDb();
  if (d) return localWrite(d, sql);
  return call('POST', '/sql-write', { env: process.env.LTH_ENV, sql, run: process.env.LTH_RUN_ID || undefined });
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

module.exports = { query, write, remember, loadMemory };
