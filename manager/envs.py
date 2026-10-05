"""Test environments: Local / Dev / QA / Live ... each with its own base URL and login.

Test cases only say {{base_url}}, {{username}}, {{password}}, {{branch}}; the environment picked for a
run fills them in, on top of the shared variables file (the rest of the test data comes from there).
Passwords are stored encrypted (Fernet, key in data/env.key) and never sent back to the browser.
"""
import json
import os
import re
import tempfile

from cryptography.fernet import Fernet, InvalidToken

import db

BASE_VARS = 'F:/TestMU-Ai/.testmuai/variables/etail.json'   # shared test data (same default as run.js)
_KEY_FILE = os.path.join(db.DATA, 'env.key')
NAME_RE = re.compile(r'^[A-Za-z0-9 ._-]{2,40}$')

SCHEMA = """
CREATE TABLE IF NOT EXISTS environments (
  id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(40) NOT NULL UNIQUE, base_url VARCHAR(500) NOT NULL,
  username VARCHAR(120), password_enc TEXT, branch VARCHAR(120), note VARCHAR(255),
  is_default TINYINT NOT NULL DEFAULT 0, created_by VARCHAR(64), updated_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB
"""


def _fernet():
    if not os.path.exists(_KEY_FILE):
        os.makedirs(db.DATA, exist_ok=True)
        with open(_KEY_FILE, 'wb') as f:
            f.write(Fernet.generate_key())
    with open(_KEY_FILE, 'rb') as f:
        return Fernet(f.read().strip())


def encrypt(text):
    return _fernet().encrypt(text.encode('utf-8')).decode('ascii') if text else None


def decrypt(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except InvalidToken:
        return ''


def _base_values():
    try:
        with open(BASE_VARS, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def init(con):
    con.execute(SCHEMA)
    for table, col, ddl in (('runs', 'env_name', 'ALTER TABLE runs ADD COLUMN env_name VARCHAR(40) NULL'),
                            ('cases', 'qa_status', "ALTER TABLE cases ADD COLUMN qa_status VARCHAR(16) NOT NULL DEFAULT 'draft'"),
                            ('cases', 'qa_marked_by', 'ALTER TABLE cases ADD COLUMN qa_marked_by VARCHAR(64) NULL'),
                            ('cases', 'qa_marked_at', 'ALTER TABLE cases ADD COLUMN qa_marked_at VARCHAR(32) NULL'),
                            # "Set up modules" menu reader: custom login steps + the page that shows the menu
                            ('environments', 'login_steps', 'ALTER TABLE environments ADD COLUMN login_steps TEXT NULL'),
                            ('environments', 'menu_url', 'ALTER TABLE environments ADD COLUMN menu_url VARCHAR(500) NULL'),
                            # read-only database of the app under test, for "Assert SQL" / "Remember SQL" steps
                            ('environments', 'db_host', 'ALTER TABLE environments ADD COLUMN db_host VARCHAR(255) NULL, '
                             'ADD COLUMN db_port INT NULL, ADD COLUMN db_name VARCHAR(64) NULL, '
                             'ADD COLUMN db_user VARCHAR(64) NULL, ADD COLUMN db_password_enc TEXT NULL')):
        n = con.execute('SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                        'AND TABLE_NAME=? AND COLUMN_NAME=?', (table, col)).fetchone()['n']
        if not n:
            con.execute(ddl)
    # values a test remembers ("Remember SQL ... as last_lot") for the tests after it, per environment
    con.execute('CREATE TABLE IF NOT EXISTS run_memory (env_id INT NOT NULL, name VARCHAR(64) NOT NULL, '
                'value VARCHAR(1000) NULL, updated_by VARCHAR(64), updated_at VARCHAR(32) NOT NULL, '
                'PRIMARY KEY (env_id, name)) ENGINE=InnoDB')
    # first start only: create "Dev" from the shared variables file so existing runs keep working.
    # Remembered in settings, so an admin who deletes every environment does not get "Dev" back on restart.
    if con.execute("SELECT 1 FROM settings WHERE name='envs.seeded'").fetchone():
        return
    con.execute("INSERT INTO settings(name, value) VALUES ('envs.seeded', '1')")
    if not con.execute('SELECT COUNT(*) AS n FROM environments').fetchone()['n']:
        v = {k: (x.get('value') if isinstance(x, dict) else x) for k, x in _base_values().items()}
        if v.get('base_url'):
            con.execute('INSERT INTO environments(name, base_url, username, password_enc, branch, note, is_default, '
                        'created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                        ('Dev', v['base_url'], v.get('username'), encrypt(v.get('password') or ''), v.get('branch'),
                         'Created from the shared variables file', 1, 'system', db.now()))


def all_envs(con):
    return con.execute('SELECT id, name, base_url, username, branch, note, is_default, updated_at, login_steps, menu_url, '
                       'db_host, db_port, db_name, db_user, (db_password_enc IS NOT NULL) AS has_db_password, '
                       '(password_enc IS NOT NULL) AS has_password FROM environments ORDER BY is_default DESC, name').fetchall()


def get(con, env_id=None):
    """The environment by id, else the default one; None if there are none."""
    if env_id:
        r = con.execute('SELECT * FROM environments WHERE id=?', (env_id,)).fetchone()
        if r:
            return r
    return con.execute('SELECT * FROM environments ORDER BY is_default DESC, id LIMIT 1').fetchone()


def vars_file(env):
    """Writes a temporary variables JSON = shared variables + this environment's URL / login.
    Returns the path (caller deletes it). The password is marked secret so reports mask it."""
    data = _base_values()
    data['base_url'] = {'value': env['base_url']}
    if env['username']:
        data['username'] = {'value': env['username']}
    pw = decrypt(env['password_enc'])
    if pw:
        data['password'] = {'value': pw, 'secret': True}
    if env['branch']:
        data['branch'] = {'value': env['branch']}
    fd, path = tempfile.mkstemp(suffix='.vars.json', dir=db.DATA)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return path


# ---------------------------------------------------------------- read-only database of the app under test

SELECT_RE = re.compile(r'^\s*(select|with)\b', re.I)
WRITE_WORDS = re.compile(r'\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke|rename|lock|unlock|'
                         r'call|handler|load|set|into\s+outfile|into\s+dumpfile|sleep|benchmark|get_lock)\b', re.I)
MAX_ROWS = 20


class DbError(Exception):
    pass


def check_select(sql):
    """Only a single SELECT is allowed; anything that could write or hang is refused before it reaches MySQL."""
    s = (sql or '').strip().rstrip(';').strip()
    if not s or len(s) > 4000:
        raise DbError('The SQL is empty or too long (max 4000 characters).')
    if ';' in s or '--' in s or '/*' in s or '#' in s:
        raise DbError('Only one plain SELECT is allowed: no ";", comments or several statements.')
    if not SELECT_RE.match(s):
        raise DbError('Only SELECT queries are allowed (the test database is read-only).')
    if WRITE_WORDS.search(s):
        raise DbError('This query uses a word that is not allowed in a read-only check (e.g. INSERT, UPDATE, SLEEP).')
    return s


def db_configured(env):
    return bool(env and env['db_host'] and env['db_name'] and env['db_user'])


def db_query(env, sql):
    """Runs one SELECT on the environment's database in a READ ONLY session.
    -> {'columns': [...], 'rows': [[...], ...] (max MAX_ROWS), 'value': first cell as text or None, 'count': n}"""
    import pymysql
    if not db_configured(env):
        raise DbError(f'Environment "{env["name"] if env else "?"}" has no database set (Settings -> Environments -> Database).')
    s = check_select(sql)
    try:
        raw = pymysql.connect(host=env['db_host'], port=int(env['db_port'] or 3306), user=env['db_user'],
                              password=decrypt(env['db_password_enc']), database=env['db_name'], charset='utf8mb4',
                              connect_timeout=6, read_timeout=20, autocommit=True)
    except pymysql.MySQLError as e:
        raise DbError(f'Cannot connect to the test database ({e.args[1] if len(e.args) > 1 else e}).')
    try:
        with raw.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            try:   # query time limit: MySQL / MariaDB name it differently
                cur.execute('SET SESSION MAX_EXECUTION_TIME=15000')
            except pymysql.MySQLError:
                try:
                    cur.execute('SET SESSION max_statement_time=15')
                except pymysql.MySQLError:
                    pass   # read_timeout=20 still stops a slow query
            cur.execute(s)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS)
            more = cur.fetchall()
    except pymysql.MySQLError as e:
        raise DbError(f'The query failed: {e.args[1] if len(e.args) > 1 else e}')
    finally:
        raw.close()

    def text(v):
        if v is None:
            return None
        if hasattr(v, 'isoformat'):
            return v.isoformat(sep=' ') if hasattr(v, 'hour') else v.isoformat()
        return format(v, 'f') if hasattr(v, 'as_tuple') else str(v)   # Decimal keeps its scale: 10.500

    out = [[text(v) for v in r] for r in rows]
    return {'columns': cols, 'rows': out, 'value': out[0][0] if out and out[0] else None,
            'count': len(rows) + len(more)}


def normalise_url(url):
    url = (url or '').strip()
    if not re.match(r'^https?://[^\s/]+', url, re.I):
        return None
    return url if url.endswith('/') else url + '/'
