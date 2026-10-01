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
                            ('cases', 'qa_marked_at', 'ALTER TABLE cases ADD COLUMN qa_marked_at VARCHAR(32) NULL')):
        n = con.execute('SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                        'AND TABLE_NAME=? AND COLUMN_NAME=?', (table, col)).fetchone()['n']
        if not n:
            con.execute(ddl)
    # first start: create "Dev" from the shared variables file so existing runs keep working
    if not con.execute('SELECT COUNT(*) AS n FROM environments').fetchone()['n']:
        v = {k: (x.get('value') if isinstance(x, dict) else x) for k, x in _base_values().items()}
        if v.get('base_url'):
            con.execute('INSERT INTO environments(name, base_url, username, password_enc, branch, note, is_default, '
                        'created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                        ('Dev', v['base_url'], v.get('username'), encrypt(v.get('password') or ''), v.get('branch'),
                         'Created from the shared variables file', 1, 'system', db.now()))


def all_envs(con):
    return con.execute('SELECT id, name, base_url, username, branch, note, is_default, updated_at, '
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


def normalise_url(url):
    url = (url or '').strip()
    if not re.match(r'^https?://[^\s/]+', url, re.I):
        return None
    return url if url.endswith('/') else url + '/'
