"""LogiTestHub API for the CLI / IDE.

Authentication: HTTP Basic with the LogiTestHub username and the user's access key
(Profile -> Access Key). The Anthropic key never leaves this server; clients only ever
hold their own access key, which can be regenerated (the old one stops working at once).

  curl -u <username>:<access-key> http://<server>:5050/api/v1/whoami
"""
import base64
import functools
import hashlib
import hmac
import secrets
import threading
import time

from flask import Blueprint, g, jsonify, request

import db

bp = Blueprint('api', __name__, url_prefix='/api/v1')

KEY_PREFIX = 'lth_'
_FAILS = {}                    # ip -> [timestamps] of failed logins (brute-force brake)
_FAILS_LOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 10, 300


# ---------------------------------------------------------------- keys

def hash_key(key):
    # Keys are 32 random bytes, so a plain SHA-256 is enough (no dictionary to guess from).
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def new_key(con, user_id):
    """Creates or replaces the user's access key. Returns the plain key (shown to the user once)."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    con.execute('INSERT INTO api_keys(user_id, key_hash, key_hint, created_at) VALUES (?,?,?,?) '
                'ON DUPLICATE KEY UPDATE key_hash=VALUES(key_hash), key_hint=VALUES(key_hint), '
                'created_at=VALUES(created_at), last_used_at=NULL, last_used_ip=NULL',
                (user_id, hash_key(key), key[-4:], db.now()))
    return key


def revoke_key(con, user_id):
    con.execute('DELETE FROM api_keys WHERE user_id=?', (user_id,))


# ---------------------------------------------------------------- auth

def _too_many_fails(ip):
    now = time.time()
    with _FAILS_LOCK:
        recent = [t for t in _FAILS.get(ip, []) if now - t < FAIL_WINDOW]
        _FAILS[ip] = recent
        return len(recent) >= MAX_FAILS


def _note_fail(ip):
    with _FAILS_LOCK:
        _FAILS.setdefault(ip, []).append(time.time())


def _error(status, message):
    resp = jsonify({'ok': False, 'error': message})
    resp.status_code = status
    if status == 401:
        resp.headers['WWW-Authenticate'] = 'Basic realm="LogiTestHub API"'
    return resp


def _credentials():
    auth = request.headers.get('Authorization', '')
    if not auth.lower().startswith('basic '):
        return None, None
    try:
        user, _, key = base64.b64decode(auth[6:].strip()).decode('utf-8').partition(':')
    except (ValueError, UnicodeDecodeError):
        return None, None
    return user.strip(), key.strip()


def api_auth(fn):
    """Allows the request only with a valid username + access key; logs every call."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        ip = request.remote_addr or '-'
        if _too_many_fails(ip):
            return _error(429, 'Too many failed logins. Wait 5 minutes and try again.')
        username, key = _credentials()
        if not username or not key:
            return _error(401, 'Send your LogiTestHub username and access key (HTTP Basic auth).')
        con = db.connect()
        try:
            row = con.execute('SELECT u.id, u.username, u.name, u.role, k.key_hash FROM users u '
                              'JOIN api_keys k ON k.user_id=u.id WHERE u.username=?', (username,)).fetchone()
            if not row or not hmac.compare_digest(row['key_hash'], hash_key(key)):
                _note_fail(ip)
                con.execute('INSERT INTO api_log(user_id, at, method, path, status, ip) VALUES (?,?,?,?,?,?)',
                            (row['id'] if row else None, db.now(), request.method, request.path, 401, ip))
                con.commit()
                return _error(401, 'Invalid username or access key.')
            g.api_user = dict(row)
            g.api_con = con
            resp = fn(*a, **kw)
            status = resp.status_code if hasattr(resp, 'status_code') else 200
            con.execute('UPDATE api_keys SET last_used_at=?, last_used_ip=? WHERE user_id=?', (db.now(), ip, row['id']))
            con.execute('INSERT INTO api_log(user_id, at, method, path, status, ip) VALUES (?,?,?,?,?,?)',
                        (row['id'], db.now(), request.method, request.path, status, ip))
            con.commit()
            return resp
        finally:
            con.close()
    return wrapper


# ---------------------------------------------------------------- endpoints (read-only for now)

@bp.get('/whoami')
@api_auth
def whoami():
    u = g.api_user
    return jsonify({'ok': True, 'username': u['username'], 'name': u['name'], 'role': u['role']})


@bp.get('/projects')
@api_auth
def projects():
    rows = g.api_con.execute(
        'SELECT p.id, p.name, p.description, (SELECT COUNT(*) FROM cases c WHERE c.project_id=p.id) AS cases '
        'FROM projects p ORDER BY p.name').fetchall()
    return jsonify({'ok': True, 'projects': [dict(r) for r in rows]})


@bp.get('/projects/<int:pid>/cases')
@api_auth
def cases(pid):
    if not g.api_con.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
        return _error(404, 'Project not found.')
    rows = g.api_con.execute(
        'SELECT c.id, c.tc_no, c.title, f.name AS folder, c.updated_at, c.updated_by FROM cases c '
        'LEFT JOIN folders f ON f.id=c.folder_id WHERE c.project_id=? ORDER BY c.tc_no', (pid,)).fetchall()
    return jsonify({'ok': True, 'cases': [dict(r, tc=f"TC-{r['tc_no']}") for r in rows]})


@bp.get('/cases/<int:cid>')
@api_auth
def case(cid):
    r = g.api_con.execute('SELECT c.id, c.tc_no, c.title, c.content, c.project_id, f.name AS folder FROM cases c '
                          'LEFT JOIN folders f ON f.id=c.folder_id WHERE c.id=?', (cid,)).fetchone()
    if not r:
        return _error(404, 'Test case not found.')
    return jsonify({'ok': True, 'case': dict(r, tc=f"TC-{r['tc_no']}")})


# ---------------------------------------------------------------- AI through the server (limits enforced here)

def _budget_or_error():
    """None when the caller may use AI now, else the JSON error response (HTTP 429)."""
    import budget
    s = budget.status(g.api_con, g.api_user)
    if s['ok']:
        return None
    resp = jsonify({'ok': False, 'error': 'ai_limit', 'message': s['message'], 'used_inr': round(s['used_inr'], 2),
                    'limit_inr': s['limit_inr'], 'resets_on': s['resets_on']})
    resp.status_code = 429
    return resp


def _usage_json(s):
    return {'used_inr': round(s['used_inr'], 2), 'limit_inr': s['limit_inr'], 'percent': s['pct'],
            'resets_on': s['resets_on'], 'warning': s['message'] if s['warn'] else None, 'blocked': not s['ok'],
            'blocked_reason': None if s['ok'] else s['message']}


@bp.get('/ai/usage')
@api_auth
def ai_usage():
    import budget
    return jsonify({'ok': True, **_usage_json(budget.status(g.api_con, g.api_user))})


@bp.post('/ai/generate')
@api_auth
def ai_generate():
    """{"project_id": 1, "task": "...", "count": 6, "folder_id": null} -> new test cases (saved for review, not run)."""
    import budget
    import services
    body = request.get_json(silent=True) or {}
    task = str(body.get('task') or '').strip()
    pid = body.get('project_id')
    folder_id = body.get('folder_id') or None
    if not isinstance(pid, int) or not g.api_con.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
        return _error(400, 'project_id must be the id of an existing project (see GET /api/v1/projects).')
    if folder_id is not None and (not isinstance(folder_id, int) or not g.api_con.execute(
            'SELECT 1 FROM folders WHERE id=? AND project_id=?', (folder_id, pid)).fetchone()):
        return _error(400, 'folder_id does not belong to this project.')
    if len(task) < 15:
        return _error(400, 'Describe the requirement / bug / change in at least one full sentence.')
    blocked = _budget_or_error()
    if blocked:
        return blocked
    ok, msg, ids = services.generate_cases(pid, folder_id, task, body.get('count') or 6, '', g.api_user['username'])
    if not ok:
        return _error(502, msg)
    rows = g.api_con.execute(f'SELECT id, tc_no, title FROM cases WHERE id IN ({",".join("?" * len(ids))})', ids).fetchall() if ids else []
    return jsonify({'ok': True, 'message': msg, 'cases': [dict(r, tc=f"TC-{r['tc_no']}") for r in rows],
                    'usage': _usage_json(budget.status(g.api_con, g.api_user))})


@bp.post('/ai/step')
@api_auth
def ai_step():
    """The CLI's browser runs on the developer's PC; it sends what the page shows, the server asks Claude.
    {"stepText": "...", "url": "...", "elements": [...], "shotB64": "<jpeg base64>", "case_id": 23}"""
    import budget
    import services
    body = request.get_json(silent=True) or {}
    step, shot = str(body.get('stepText') or '').strip(), body.get('shotB64') or ''
    elements = body.get('elements') if isinstance(body.get('elements'), list) else []
    if not step or not isinstance(shot, str) or not shot:
        return _error(400, 'stepText and shotB64 (JPEG screenshot, base64) are required.')
    if len(shot) > 6_000_000 or len(elements) > 300:
        return _error(413, 'Screenshot or element list too large (max ~4 MB JPEG, 300 elements).')
    blocked = _budget_or_error()
    if blocked:
        return blocked
    case_id = body.get('case_id') if isinstance(body.get('case_id'), int) else None
    project_id = None
    if case_id:
        r = g.api_con.execute('SELECT project_id FROM cases WHERE id=?', (case_id,)).fetchone()
        project_id, case_id = (r['project_id'], case_id) if r else (None, None)
    reply = services.bridge_step({'stepText': step[:2000], 'url': str(body.get('url') or '')[:2000],
                                  'elements': elements, 'shotB64': shot})
    if not reply.get('ok'):
        return _error(502, reply.get('error') or 'AI step failed.')
    u = reply.get('usage') or {}
    db.record_ai_usage(g.api_con, 'cli-step', {'model': reply.get('model'), 'calls': 1, 'in': u.get('input_tokens'),
                       'out': u.get('output_tokens'), 'cacheRead': u.get('cache_read_input_tokens'),
                       'cacheWrite': u.get('cache_creation_input_tokens')},
                       username=g.api_user['username'], project_id=project_id, case_id=case_id, note='via CLI / IDE')
    return jsonify({'ok': True, **(reply.get('data') or {}), 'usage': _usage_json(budget.status(g.api_con, g.api_user))})


@bp.app_errorhandler(404)
def _nf(e):
    # Unmatched /api/... URLs never reach the blueprint, so answer them here with JSON; other pages keep the HTML 404.
    if request.path.startswith('/api/'):
        return _error(404, 'Unknown API endpoint.')
    return e


@bp.app_errorhandler(405)
def _method(e):
    if request.path.startswith('/api/'):
        return _error(405, 'Method not allowed for this endpoint.')
    return e
