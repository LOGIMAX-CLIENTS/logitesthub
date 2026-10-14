"""Test environments: Local / Dev / QA / Live ... each with its own base URL and login.

Test cases only say {{base_url}}, {{username}}, {{password}}, {{branch}}; the environment picked for a
run fills them in, on top of the shared variables file (the rest of the test data comes from there).
Passwords are stored encrypted (Fernet, key in data/env.key) and never sent back to the browser.

Shared environments (owner NULL: Dev / QA / Live) are added by admins and everyone can pick them.
Personal environments (owner = a username: e.g. "Local" = that user's own XAMPP site) are seen and used only by
their owner - in the run pickers, the Tester Assistant, the CLI (--env) and schedules never use them.
"""
import json
import os
import re
import tempfile

from cryptography.fernet import Fernet, InvalidToken

import db

BASE_VARS = db.VARS_FILE   # shared test data: Settings -> System (settings.apply_system keeps it current)
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
                             'ADD COLUMN db_user VARCHAR(64) NULL, ADD COLUMN db_password_enc TEXT NULL'),
                            # test-data setup ("Set up SQL" steps): a separate write user, off unless switched on
                            ('environments', 'db_allow_write', 'ALTER TABLE environments ADD COLUMN db_write_user VARCHAR(64) NULL, '
                             'ADD COLUMN db_write_password_enc TEXT NULL, ADD COLUMN db_allow_write TINYINT NOT NULL DEFAULT 0'),
                            # personal environments: owner = username (only they see it); NULL = shared
                            ('environments', 'owner', 'ALTER TABLE environments ADD COLUMN owner VARCHAR(64) NULL')):
        n = con.execute('SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                        'AND TABLE_NAME=? AND COLUMN_NAME=?', (table, col)).fetchone()['n']
        if not n:
            con.execute(ddl)
    # names are unique per owner (each user may have their own "Local"), so the old table-wide UNIQUE goes
    for ix in con.execute("SHOW INDEX FROM environments WHERE Column_name='name' AND Non_unique=0").fetchall():
        con.execute(f"ALTER TABLE environments DROP INDEX `{ix['Key_name']}`")
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


def _username(user):
    return user if isinstance(user, str) else (user['username'] if user else None)


def visible_sql(user):
    """WHERE part + params: the shared environments and the user's own personal ones."""
    return '(owner IS NULL OR owner=?)', [_username(user) or '']


def visible(env, user):
    return bool(env) and (env['owner'] is None or env['owner'] == _username(user))


def all_envs(con, user=None):
    """Shared environments first (default on top), then the user's personal ones. user=None: every environment."""
    where, params = ('WHERE ' + visible_sql(user)[0], visible_sql(user)[1]) if user else ('', [])
    return con.execute('SELECT id, name, base_url, username, branch, note, is_default, updated_at, login_steps, menu_url, '
                       'db_host, db_port, db_name, db_user, (db_password_enc IS NOT NULL) AS has_db_password, '
                       'db_write_user, (db_write_password_enc IS NOT NULL) AS has_db_write_password, db_allow_write, '
                       '(password_enc IS NOT NULL) AS has_password, repo_url, repo_branch, repo_paths, repo_commit, '
                       'repo_synced_at, repo_status, repo_error, (repo_token_enc IS NOT NULL) AS has_repo_token, owner, created_by '
                       f'FROM environments {where} ORDER BY owner IS NOT NULL, is_default DESC, name', params).fetchall()


def get(con, env_id=None, user=None):
    """The environment by id, else the default (shared) one; None if there are none.
    With user: only an environment that user may see (shared, or their own) - another user's personal id gives the
    default instead. Without user (background runs, already checked when they started): any environment."""
    if env_id:
        r = con.execute('SELECT * FROM environments WHERE id=?', (env_id,)).fetchone()
        if r and (user is None or visible(r, user)):
            return r
    return con.execute('SELECT * FROM environments ORDER BY owner IS NOT NULL, is_default DESC, id LIMIT 1').fetchone()


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


# ---------------------------------------------------------------- test-data setup ("Set up SQL" steps)
# e.g. move a chit payment date back a month so the next instalment can be paid today. Only on environments
# where an admin switched it on, with a separate write user, one statement at a time, every statement audited.

WRITE_START = re.compile(r'^\s*(update|insert|delete)\b', re.I)
WRITE_BLOCKED = re.compile(r'\b(drop|alter|create|truncate|grant|revoke|rename|lock|unlock|call|handler|load|'
                           r'into\s+outfile|into\s+dumpfile|sleep|benchmark|get_lock|shutdown|kill)\b|@@', re.I)
MAX_WRITE_ROWS = 50
LIVE_NAME = re.compile(r'\b(live|prod|production)\b', re.I)


def check_write(sql):
    """One INSERT / UPDATE / DELETE; UPDATE / DELETE must have a WHERE. Returns the statement or raises DbError."""
    s = (sql or '').strip().rstrip(';').strip()
    if not s:
        raise DbError('Empty SQL.')
    if len(s) > 4000:
        raise DbError('SQL too long (max 4000 characters).')
    if ';' in s or '--' in s or '/*' in s or '#' in s:
        raise DbError('Only one statement, without comments (no ; -- /* #).')
    m = WRITE_START.match(s)
    if not m:
        raise DbError('A "Set up SQL" step must be one INSERT, UPDATE or DELETE.')
    if WRITE_BLOCKED.search(s):
        raise DbError('This statement uses a word that is not allowed in test-data setup (e.g. DROP, ALTER, TRUNCATE).')
    if m.group(1).lower() in ('update', 'delete') and not re.search(r'\bwhere\b', s, re.I):
        raise DbError(f'{m.group(1).upper()} needs a WHERE, so it only changes the test\'s own rows.')
    return s


def db_write_configured(env):
    return bool(env and env['db_host'] and env['db_name'] and env['db_allow_write'] and env['db_write_user'])


def db_write(env, sql):
    """Runs one INSERT / UPDATE / DELETE with the environment's write user, in a transaction that is rolled back
    when it would change more than MAX_WRITE_ROWS rows. -> {'affected': n}"""
    import pymysql
    if not db_write_configured(env):
        raise DbError(f'Environment "{env["name"] if env else "?"}" does not allow test-data setup '
                      '(Settings -> Environments -> Database -> Allow "Set up SQL" steps, with a write user).')
    if LIVE_NAME.search(env['name'] or ''):
        raise DbError(f'"{env["name"]}" looks like a live environment: test-data setup is never run there.')
    s = check_write(sql)
    try:
        raw = pymysql.connect(host=env['db_host'], port=int(env['db_port'] or 3306), user=env['db_write_user'],
                              password=decrypt(env['db_write_password_enc']), database=env['db_name'], charset='utf8mb4',
                              connect_timeout=6, read_timeout=20, write_timeout=20, autocommit=False)
    except pymysql.MySQLError as e:
        raise DbError(f'Cannot connect to the test database with the write user ({e.args[1] if len(e.args) > 1 else e}).')
    try:
        with raw.cursor() as cur:
            try:
                cur.execute('SET SESSION innodb_lock_wait_timeout=10')
            except pymysql.MySQLError:
                pass
            n = cur.execute(s)
        if n > MAX_WRITE_ROWS:
            raw.rollback()
            raise DbError(f'This would change {n} rows (max {MAX_WRITE_ROWS}): nothing was changed. Narrow the WHERE.')
        raw.commit()
    except pymysql.MySQLError as e:
        raw.rollback()
        raise DbError(f'The statement failed: {e.args[1] if len(e.args) > 1 else e}')
    finally:
        raw.close()
    return {'affected': n}


def normalise_url(url):
    url = (url or '').strip()
    if not re.match(r'^https?://[^\s/]+', url, re.I):
        return None
    return url if url.endswith('/') else url + '/'
