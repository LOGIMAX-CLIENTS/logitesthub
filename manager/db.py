"""MySQL storage for the Test Manager (projects, folders, test cases, runs, users).

Connection settings: data/mysql.json, one entry per database (like CodeIgniter's $db['default'] / $db['unified']):
  {"default": {host, port, user, password, database},      <- LogiTestHub's own DB (read/write)
   "unified": {host, port, user, password, database, ...}}  <- Unified_DB (PM Members), read-only, optional
A flat {host, port, ...} file is still read as "default". Env overrides: LTH_DB_HOST / _PORT / _USER /
_PASSWORD / _NAME / _SSL_CA for default, LTH_UNIFIED_DB_HOST / ... for unified. See mysql.example.json.
Optional "ssl_ca": path to a CA bundle; the connection then uses TLS (Amazon RDS: global-bundle.pem).
Call sites keep the sqlite3-style API: '?' placeholders, row['col'] / row[0], and
`with con:` = one transaction (commit on success, rollback on error; the connection stays open).
"""
import decimal
import json
import os
import re
from datetime import datetime

import pymysql

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
DB_CONFIG = os.path.join(DATA, 'mysql.json')
SQLITE_PATH = os.path.join(DATA, 'manager.db')   # old storage; only read by migrate_sqlite.py
RUNNER_DIR = os.path.dirname(BASE)            # F:\etail-test-ai (run.js lives here)
RUNS_DIR = os.path.join(RUNNER_DIR, 'runs')

# Timestamps stay 'YYYY-MM-DD HH:MM:SS' strings: the app compares and slices them as text.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, name VARCHAR(120) NOT NULL,
  pw_hash VARCHAR(255) NOT NULL, role VARCHAR(16) NOT NULL DEFAULT 'member', created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS projects (
  id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(191) NOT NULL UNIQUE, description TEXT NOT NULL,
  next_tc INT NOT NULL DEFAULT 1, created_by VARCHAR(64), created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS folders (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, name VARCHAR(191) NOT NULL,
  archived TINYINT NOT NULL DEFAULT 0,
  UNIQUE KEY ux_folders_name (project_id, name),
  CONSTRAINT fk_folders_project FOREIGN KEY (project_id) REFERENCES projects(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS cases (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, folder_id INT NULL, tc_no INT NOT NULL,
  title VARCHAR(500) NOT NULL, content MEDIUMTEXT NOT NULL, source_path VARCHAR(500) NULL,
  created_by VARCHAR(64), updated_by VARCHAR(64), updated_at VARCHAR(32) NOT NULL, created_at VARCHAR(32),
  code_hash VARCHAR(64), code_status VARCHAR(16), code_msg TEXT, code_verified_at VARCHAR(32),
  code_verified_by VARCHAR(64),
  UNIQUE KEY ux_cases_tc (project_id, tc_no),
  UNIQUE KEY ux_cases_source (source_path),
  KEY ix_cases_folder (project_id, folder_id),
  CONSTRAINT fk_cases_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_cases_folder FOREIGN KEY (folder_id) REFERENCES folders(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS runs (
  id INT AUTO_INCREMENT PRIMARY KEY, case_id INT NOT NULL, status VARCHAR(16) NOT NULL, run_dir VARCHAR(500) NULL,
  started_at VARCHAR(32) NOT NULL, duration_ms INT, passed INT, failed INT, needs_ai INT, skipped INT, total INT,
  run_by VARCHAR(64), share_token VARCHAR(64) NULL, share_expires VARCHAR(32) NULL, error TEXT,
  UNIQUE KEY ux_runs_share (share_token),
  UNIQUE KEY ux_runs_dir (run_dir),
  KEY ix_runs_case (case_id, id),
  CONSTRAINT fk_runs_case FOREIGN KEY (case_id) REFERENCES cases(id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS api_keys (
  user_id INT PRIMARY KEY, key_hash VARCHAR(64) NOT NULL, key_hint VARCHAR(16) NOT NULL,
  created_at VARCHAR(32) NOT NULL, last_used_at VARCHAR(32), last_used_ip VARCHAR(64),
  CONSTRAINT fk_api_keys_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS api_log (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT, at VARCHAR(32) NOT NULL, method VARCHAR(10), path VARCHAR(500),
  status INT, ip VARCHAR(64),
  KEY ix_api_log_user (user_id, id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ai_usage (
  id INT AUTO_INCREMENT PRIMARY KEY, at VARCHAR(32) NOT NULL, username VARCHAR(64), project_id INT, case_id INT,
  run_id INT, kind VARCHAR(16) NOT NULL, model VARCHAR(64), calls INT NOT NULL DEFAULT 0,
  input_tokens BIGINT NOT NULL DEFAULT 0, output_tokens BIGINT NOT NULL DEFAULT 0,
  cache_read_tokens BIGINT NOT NULL DEFAULT 0, cache_write_tokens BIGINT NOT NULL DEFAULT 0,
  cost_usd DOUBLE NOT NULL DEFAULT 0, note TEXT,
  KEY ix_ai_usage_case (case_id),
  KEY ix_ai_usage_at (at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ai_credits (
  id INT AUTO_INCREMENT PRIMARY KEY, at VARCHAR(32) NOT NULL, amount_usd DOUBLE NOT NULL, note VARCHAR(255),
  added_by VARCHAR(64)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ai_limits (
  user_id INT PRIMARY KEY, monthly_inr DOUBLE NULL, updated_by VARCHAR(64), updated_at VARCHAR(32),
  CONSTRAINT fk_ai_limits_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ai_topups (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL, month VARCHAR(7) NOT NULL, amount_inr DOUBLE NOT NULL,
  note VARCHAR(255), added_by VARCHAR(64), at VARCHAR(32) NOT NULL,
  KEY ix_ai_topups_user (user_id, month),
  CONSTRAINT fk_ai_topups_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS settings (
  name VARCHAR(64) PRIMARY KEY, value TEXT
) ENGINE=InnoDB;
"""


class Row(dict):
    """dict row that also allows row[0], like sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def _plain(v):
    # SUM() comes back as Decimal from MySQL; the app does int/float maths on it.
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


class Cursor:
    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = cur.lastrowid
        self.rowcount = cur.rowcount

    @staticmethod
    def _row(r):
        return None if r is None else Row((k, _plain(v)) for k, v in r.items())

    def fetchone(self):
        return self._row(self._cur.fetchone())

    def fetchall(self):
        return [self._row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


def _sql(query):
    # sqlite '?' placeholders -> pymysql '%s'; a literal '%' in the SQL text must be doubled
    return query.replace('%', '%%').replace('?', '%s')


class Connection:
    """sqlite3-compatible wrapper: execute()/executemany() on the connection, `with con:` = one transaction."""
    def __init__(self, raw):
        self._raw = raw
        self._depth = 0

    def execute(self, query, params=()):
        cur = self._raw.cursor()
        cur.execute(_sql(query), tuple(params))
        return Cursor(cur)

    def executemany(self, query, seq):
        cur = self._raw.cursor()
        cur.executemany(_sql(query), [tuple(p) for p in seq])
        return Cursor(cur)

    def commit(self):
        if not self._depth:
            self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        if not self._depth:
            self._raw.begin()
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if not self._depth:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        return False


DB_DEFAULTS = {
    'default': {'host': '127.0.0.1', 'port': 3306, 'user': 'root', 'password': '', 'database': 'logitesthub'},
    'unified': {'host': '', 'port': 3306, 'user': '', 'password': '', 'database': 'Unified_DB'},
}
DB_ENV_PREFIX = {'default': 'LTH_DB_', 'unified': 'LTH_UNIFIED_DB_'}


def db_config(name='default'):
    """Settings of one named connection. Unknown name -> KeyError."""
    cfg = dict(DB_DEFAULTS[name])
    if os.path.isfile(DB_CONFIG):
        with open(DB_CONFIG, encoding='utf-8') as f:
            raw = json.load(f)
        named = isinstance(raw.get('default'), dict)
        if named:
            cfg.update(raw.get(name) or {})
        elif name == 'default':   # old flat file = the default connection
            cfg.update(raw)
    pre = DB_ENV_PREFIX[name]
    for key, env in (('host', 'HOST'), ('port', 'PORT'), ('user', 'USER'), ('password', 'PASSWORD'), ('database', 'NAME'),
                     ('ssl_ca', 'SSL_CA')):
        if os.environ.get(pre + env):
            cfg[key] = os.environ[pre + env]
    cfg['port'] = int(cfg['port'])
    return cfg


def configured(name):
    cfg = db_config(name)
    return bool(cfg['host'] and cfg['user'] and cfg['database'])


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def to_local(iso_utc):
    """Runner timestamps are UTC ISO strings ('...Z'); the app shows local time."""
    if not iso_utc:
        return now()
    try:
        return datetime.fromisoformat(iso_utc.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return now()


def connect(name='default', read_only=False):
    """connect() = LogiTestHub's own DB. connect('unified', read_only=True) = Unified_DB (PM Members)."""
    cfg = db_config(name)
    ssl = {'ca': cfg['ssl_ca']} if cfg.get('ssl_ca') else None   # e.g. Amazon RDS: TLS checked against the RDS CA bundle
    raw = pymysql.connect(host=cfg['host'], port=cfg['port'], user=cfg['user'], password=cfg['password'],
                          database=cfg['database'], charset='utf8mb4', autocommit=True, ssl=ssl,
                          cursorclass=pymysql.cursors.DictCursor, connect_timeout=10 if name == 'default' else 6,
                          read_timeout=None if name == 'default' else 8)
    if read_only:
        with raw.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')   # any write on this connection fails
    return Connection(raw)


def init():
    con = connect()
    try:
        cur = con._raw.cursor()
        for stmt in (s.strip() for s in SCHEMA.split(';')):
            if stmt:
                cur.execute(stmt)
        # columns added after the first MySQL release
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='ai_credits' AND COLUMN_NAME='rate_inr'")
        if not cur.fetchone()['n']:
            cur.execute('ALTER TABLE ai_credits ADD COLUMN rate_inr DOUBLE NULL AFTER amount_usd')
        # Why a run failed, kept on the run so history lists can show it without reading files
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='runs' AND COLUMN_NAME='fail_reason'")
        if not cur.fetchone()['n']:
            cur.execute('ALTER TABLE runs ADD COLUMN fail_step INT NULL, ADD COLUMN fail_text VARCHAR(500) NULL, '
                        'ADD COLUMN fail_reason VARCHAR(500) NULL')
            cur.execute("SELECT id, run_dir FROM runs WHERE run_dir IS NOT NULL AND status IN ('FAILED', 'PARTIAL')")
            for r in cur.fetchall():
                f = run_failure(read_run_events(r['run_dir'])[1]) if os.path.isdir(r['run_dir']) else None
                if f:
                    cur.execute('UPDATE runs SET fail_step=%s, fail_text=%s, fail_reason=%s WHERE id=%s',
                                (f['step'], f['text'], f['reason'], r['id']))
        # Login helper per project: put in front of every case of that project when it runs
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='projects' AND COLUMN_NAME='login_helper'")
        if not cur.fetchone()['n']:
            cur.execute('ALTER TABLE projects ADD COLUMN login_helper TEXT NULL')
        # PM sign-in: 'local' accounts keep their own password; 'pm' accounts store none (PM checks it)
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='users' AND COLUMN_NAME='pm_member_id'")
        if not cur.fetchone()['n']:
            cur.execute("ALTER TABLE users ADD COLUMN auth_source VARCHAR(8) NOT NULL DEFAULT 'local' AFTER role, "
                        "ADD COLUMN pm_member_id VARCHAR(64) NULL AFTER auth_source, "
                        "ADD COLUMN pm_login VARCHAR(254) NULL AFTER pm_member_id, "
                        "ADD UNIQUE KEY uq_users_pm (pm_member_id)")
    finally:
        con.close()


def case_title(content, fallback='Untitled test'):
    for line in content.splitlines():
        if line.startswith('# '):
            return line[2:].strip()
    return fallback


def get_or_create_folder(con, project_id, name):
    if not name:
        return None
    row = con.execute('SELECT id FROM folders WHERE project_id=? AND ((parent_id=0 AND name=?) OR import_key=?) '
                      'ORDER BY import_key IS NULL LIMIT 1', (project_id, name, name)).fetchone()   # import_key: moved under a module
    if row:
        return row['id']
    return con.execute('INSERT INTO folders(project_id, parent_id, name) VALUES (?,0,?)', (project_id, name)).lastrowid


def login_helper_file(con, project_id):
    """The project's login helper written to a temp file for run.js / codegen.js --login (caller deletes it),
    or None when the project has none."""
    import tempfile
    row = con.execute('SELECT login_helper FROM projects WHERE id=?', (project_id,)).fetchone() if project_id else None
    if not row or not (row['login_helper'] or '').strip():
        return None
    fd, path = tempfile.mkstemp(suffix='_login.md', dir=DATA)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(row['login_helper'])
    return path


def add_case(con, project_id, folder_id, title, content, user, source_path=None):
    tc = con.execute('SELECT next_tc FROM projects WHERE id=? FOR UPDATE', (project_id,)).fetchone()['next_tc']
    con.execute('UPDATE projects SET next_tc = next_tc + 1 WHERE id=?', (project_id,))
    return con.execute(
        'INSERT INTO cases(project_id, folder_id, tc_no, title, content, source_path, created_by, updated_by, updated_at, created_at)'
        ' VALUES (?,?,?,?,?,?,?,?,?,?)',
        (project_id, folder_id, tc, title, content, source_path, user, user, now(), now())).lastrowid


VAR_RE = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')
VARS_FILE = 'F:/TestMU-Ai/.testmuai/variables/etail.json'


def load_vars(path=None):
    """Same rules as the runner's lib/vars.js: {key: {value, secret}} with nested {{refs}} resolved.
    Returns (values, secret_values). Default: the Variables file from Settings -> System."""
    with open(path or VARS_FILE, encoding='utf-8') as f:
        raw = json.load(f)
    vals = {k: str(v.get('value', '') if isinstance(v, dict) else v) for k, v in raw.items()}
    for _ in range(5):
        vals = {k: VAR_RE.sub(lambda m: vals.get(m.group(1), m.group(0)), v) for k, v in vals.items()}
    secrets = [vals[k] for k, v in raw.items() if isinstance(v, dict) and v.get('secret') and len(vals[k]) >= 3]
    return vals, secrets


def read_run_events(run_dir):
    """Returns (summary dict or None, [step dicts]) from a run folder's events.ndjson."""
    path = os.path.join(run_dir, 'events.ndjson')
    summary, steps, start = None, [], None
    if not os.path.isfile(path):
        return None, []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get('type') == 'run_start':
                start = ev
            elif ev.get('type') == 'step':
                steps.append(ev)
            elif ev.get('type') == 'run_end':
                summary = ev
    if summary is not None and start is not None:
        summary.setdefault('file', start.get('file'))
    return summary, steps


def explain_step_error(text, error, status='failed'):
    """The runner's technical error -> one plain sentence a tester understands."""
    e, t = (error or '').strip(), (text or '').strip()
    if status == 'needs-ai':
        return 'This step is written in free text, so it needs AI, and AI was not available for this run' + (
            f' ({e.replace("AI: ", "")}).' if e else '.') + ' Rewrite it in a standard step sentence, or run with AI credits.'
    m = re.search(r'Expected title (?:is|contains) "(.*?)", got "(.*?)"', e)
    if m:
        hint = ' The browser is still on the login page: sign-in failed or the session ended.' if 'login' in m.group(2).lower() else ''
        return f'The page title was "{m.group(2)}", but the test expected "{m.group(1)}". The app did not reach that page.{hint}'
    m = re.match(r"assert (?:the )?text ['\"](.+?)['\"] is visible", t, re.I)
    if m and 'timeout' in e.lower():
        return f'The text "{m.group(1)}" did not appear on the screen within 10 seconds.'
    m = re.search(r'Expected (.+?) \(.*?\) to (show|contain) "(.*?)", got "(.*?)"', e)
    if m:
        want = f'"{m.group(3)}"' if m.group(3) else 'empty'
        return f'The {m.group(1)} showed "{m.group(4)}", but the test expected it to {m.group(2)} {want}.'
    m = re.search(r'Dropdown "(.+?)".*has no options', e)
    if m:
        return f'The {m.group(1)} was empty, so nothing could be selected: the data it lists is missing, or it loads from an earlier choice.'
    m = re.search(r'"(.+?)" .*not found', e)
    if m:
        return f'"{m.group(1)}" was not found on the page: it is missing, hidden, or named differently now.'
    if 'URL' in e and 'contain' in e:
        return f'The page address was not the expected one. {e}'
    if 'timeout' in e.lower():
        return 'The page did not respond in time (waited 10 seconds) for this step.'
    if 'NaN' in e or 'undefined' in e:
        return f'The page shows an invalid value: {e}'
    return e or 'This step failed.'


def run_failure(steps):
    """First failed step (else the first step that needed AI) -> {step, text, reason}, or None."""
    s = next((x for x in steps if x.get('status') == 'failed'), None) or \
        next((x for x in steps if x.get('status') == 'needs-ai'), None)
    if not s:
        return None
    return {'step': s.get('n'), 'text': (s.get('text') or '')[:500],
            'reason': explain_step_error(s.get('text'), s.get('error') or s.get('ai'), s.get('status'))[:500]}


def record_finished_run(con, run_id, run_dir):
    summary, steps = read_run_events(run_dir)
    if not summary:
        con.execute("UPDATE runs SET status='ERROR', run_dir=?, error=? WHERE id=?",
                    (run_dir, 'Runner produced no result', run_id))
        return
    con.execute(
        'UPDATE runs SET status=?, run_dir=?, duration_ms=?, passed=?, failed=?, needs_ai=?, skipped=?, total=?, '
        'fail_step=?, fail_text=?, fail_reason=? WHERE id=?',
        (summary['status'], run_dir, summary.get('durationMs'), summary.get('passed'), summary.get('failed'),
         summary.get('needsAI'), summary.get('skipped'), summary.get('total'),
         *((lambda f: (f['step'], f['text'], f['reason']) if f else (None, None, None))(run_failure(steps))), run_id))
    ai = summary.get('aiTokens') or {}
    if ai.get('calls') or ai.get('in') or ai.get('out'):
        run = con.execute('SELECT r.run_by, c.id AS case_id, c.project_id FROM runs r JOIN cases c ON c.id=r.case_id '
                          'WHERE r.id=?', (run_id,)).fetchone()
        record_ai_usage(con, 'run', ai, username=run['run_by'], project_id=run['project_id'],
                        case_id=run['case_id'], run_id=run_id)


def record_ai_usage(con, kind, u, username=None, project_id=None, case_id=None, run_id=None, share=1.0, note=None):
    """u = {model, calls, in, out, cacheRead, cacheWrite}. share < 1 splits one AI call across several cases."""
    import pricing
    model = u.get('model') or pricing.DEFAULT_MODEL
    t = {k: int(round((u.get(k) or 0) * share)) for k in ('in', 'out', 'cacheRead', 'cacheWrite')}
    cost = pricing.cost_usd(model, t['in'], t['out'], t['cacheRead'], t['cacheWrite'])
    con.execute('INSERT INTO ai_usage(at, username, project_id, case_id, run_id, kind, model, calls, input_tokens, '
                'output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (now(), username, project_id, case_id, run_id, kind, model, int(u.get('calls') or 0),
                 t['in'], t['out'], t['cacheRead'], t['cacheWrite'], cost, note))
