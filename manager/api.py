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
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime

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


RUN_TOKENS = {}   # token -> (username, expires_at): runner processes started by the server itself
_RUN_TOKENS_LOCK = threading.Lock()


def issue_run_token(username, seconds=3600):
    tok = secrets.token_urlsafe(32)
    with _RUN_TOKENS_LOCK:
        now = time.time()
        for k in [k for k, (_, exp) in RUN_TOKENS.items() if exp < now]:
            RUN_TOKENS.pop(k, None)
        RUN_TOKENS[tok] = (username, now + seconds)
    return tok


def revoke_run_token(tok):
    with _RUN_TOKENS_LOCK:
        RUN_TOKENS.pop(tok, None)


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
        tok = request.headers.get('X-LTH-Run-Token', '')
        if tok:   # runner started by this server (web run / Test Run); only from this machine
            with _RUN_TOKENS_LOCK:
                hit = RUN_TOKENS.get(tok)
            if not hit or hit[1] < time.time() or ip not in ('127.0.0.1', '::1'):
                return _error(401, 'Run token not valid.')
            con = db.connect()
            try:
                row = con.execute('SELECT id, username, name, role FROM users WHERE username=?', (hit[0],)).fetchone()
                if not row:
                    return _error(401, 'Run token not valid.')
                g.api_user, g.api_con = dict(row), con
                return fn(*a, **kw)
            finally:
                con.close()
        if _too_many_fails(ip):
            return _error(429, 'Too many failed logins. Wait 5 minutes and try again.')
        username, key = _credentials()
        if not username or not key:
            return _error(401, 'Send your LogiTestHub username and access key (HTTP Basic auth).')
        con = db.connect()
        try:
            row = con.execute('SELECT u.id, u.username, u.name, u.role, u.auth_source, u.pm_member_id, k.key_hash FROM users u '
                              'JOIN api_keys k ON k.user_id=u.id WHERE u.username=?', (username,)).fetchone()
            if not row or not hmac.compare_digest(row['key_hash'], hash_key(key)):
                _note_fail(ip)
                con.execute('INSERT INTO api_log(user_id, at, method, path, status, ip) VALUES (?,?,?,?,?,?)',
                            (row['id'] if row else None, db.now(), request.method, request.path, 401, ip))
                con.commit()
                return _error(401, 'Invalid username or access key.')
            if row['auth_source'] == 'pm':
                import pm_auth
                if pm_auth.still_active_cached(row['pm_member_id']) is False:
                    return _error(403, 'Your PM account is inactive, so LogiTestHub access is blocked.')
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


# ---------------------------------------------------------------- modules (tester modules pull / push)

def _project(pid):
    return g.api_con.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()


@bp.get('/projects/<int:pid>/modules')
@api_auth
def modules_pull(pid):
    """The project's module -> sub-module tree as a logitesthub.modules/v1 file."""
    import modules
    p = _project(pid)
    if not p:
        return _error(404, 'Project not found.')
    return jsonify({'ok': True, 'file': modules.export_file(g.api_con, p)})


@bp.post('/projects/<int:pid>/modules')
@api_auth
def modules_push(pid):
    """Adds the modules / sub-modules of a logitesthub.modules/v1 file that the project does not have yet.
    Never deletes, renames or moves anything. ?dry_run=1 only reports what would be added."""
    import modules
    import settings
    p = _project(pid)
    if not p:
        return _error(404, 'Project not found.')
    data = request.get_json(silent=True)
    if data is None:
        return _error(400, 'Send the modules file as JSON (Content-Type: application/json).')
    mods, errors, warnings = modules.normalize_file(data)
    if errors:
        return jsonify({'ok': False, 'error': 'The file has problems: ' + '; '.join(errors[:5]), 'errors': errors[:50]}), 400
    dry = request.args.get('dry_run') in ('1', 'true', 'yes')
    plan = modules.plan(g.api_con, pid, mods)
    added = (0, 0)
    if not dry and (plan['new_modules'] or plan['new_subs']):
        con = g.api_con
        with con:
            added = modules.apply_tree(con, pid, mods)
        settings.write_audit(g.api_user['username'], 'api.modules_push', 'project', pid, 'ok',
                             f'Modules file pushed from the CLI / IDE: {added[0]} module(s) and {added[1]} sub-module(s) added'
                             + (f' ({data.get("source")})' if isinstance(data, dict) and data.get('source') else ''),
                             {'changed': {'modules added': [None, ', '.join(plan['new_modules'][:30])],
                                          'sub-modules added': [None, f"{len(plan['new_subs'])}"]}}, request.remote_addr)
    return jsonify({'ok': True, 'dry_run': dry, 'project': p['name'], 'warnings': warnings, **plan,
                    'added_modules': added[0], 'added_subs': added[1]})


@bp.get('/projects/<int:pid>/login-helper')
@api_auth
def project_login_helper(pid):
    """Login steps the CLI puts in front of every case of this project (the same as web runs)."""
    r = g.api_con.execute('SELECT login_helper FROM projects WHERE id=?', (pid,)).fetchone()
    if not r:
        return _error(404, 'Project not found.')
    return jsonify({'ok': True, 'login_helper': r['login_helper'] or ''})


# ---------------------------------------------------------------- lessons learned (tester lessons / tester lesson add)

@bp.get('/projects/<int:pid>/lessons')
@api_auth
def lessons_list(pid):
    """The project's active lessons, most seen first: the /tester skill reads them before writing or running a test."""
    import lessons
    if not _project(pid):
        return _error(404, 'Project not found.')
    return jsonify({'ok': True, 'lessons': [lessons.as_json(r) for r in lessons.rows(g.api_con, pid)]})


@bp.post('/projects/<int:pid>/lessons')
@api_auth
def lessons_add(pid):
    """Records a lesson after a failure was understood and fixed. Same title again = counted again, not duplicated."""
    import lessons
    import settings
    if not _project(pid):
        return _error(404, 'Project not found.')
    v, errs = lessons.clean(request.get_json(silent=True) or {})
    if errs:
        return jsonify({'ok': False, 'error': ' '.join(errs), 'kinds': lessons.KIND_KEYS}), 400
    with g.api_con:
        lid, created = lessons.add(g.api_con, pid, v, g.api_user['username'], 'cli')
    settings.write_audit(g.api_user['username'], 'api.lessons_add', 'lesson', lid, 'ok',
                         ('Lesson added' if created else 'Lesson seen again') + f' from the CLI / IDE: {v["title"]}',
                         None, request.remote_addr)
    return jsonify({'ok': True, 'id': lid, 'created': created})


# ---------------------------------------------------------------- read-only checks on the app's database

def _env_from(value):
    import envs
    v = str(value or '').strip()
    if not v:
        return None, _error(400, 'Say which environment: --env <name> (Settings -> Environments).')
    row = g.api_con.execute('SELECT * FROM environments WHERE id=? OR name=?',
                            (int(v) if v.isdigit() else -1, v)).fetchone()
    if not row:
        return None, _error(404, f'Environment "{v}" not found.')
    return row, None


@bp.post('/sql')
@api_auth
def sql():
    """{"env": "Local", "sql": "SELECT ..."} -> first value + up to 20 rows, run in a READ ONLY session."""
    import envs
    body = request.get_json(silent=True) or {}
    env, err = _env_from(body.get('env'))
    if err:
        return err
    try:
        r = envs.db_query(env, body.get('sql'))
    except envs.DbError as e:
        return _error(400, str(e))
    return jsonify({'ok': True, 'env': env['name'], **r})


@bp.get('/memory')
@api_auth
def memory():
    """Values remembered by earlier tests in this environment ({{last_lot}} ...)."""
    env, err = _env_from(request.args.get('env'))
    if err:
        return err
    rows = g.api_con.execute('SELECT name, value FROM run_memory WHERE env_id=?', (env['id'],)).fetchall()
    return jsonify({'ok': True, 'env': env['name'], 'values': {r['name']: r['value'] for r in rows}})


@bp.post('/memory')
@api_auth
def memory_set():
    body = request.get_json(silent=True) or {}
    env, err = _env_from(body.get('env'))
    if err:
        return err
    name, value = str(body.get('name') or ''), body.get('value')
    if not re.match(r'^[A-Za-z_][\w.-]{0,63}$', name):
        return _error(400, 'Name: letters, numbers, _ . - (max 64).')
    with g.api_con:
        g.api_con.execute('REPLACE INTO run_memory(env_id, name, value, updated_by, updated_at) VALUES (?,?,?,?,?)',
                          (env['id'], name, None if value is None else str(value)[:1000], g.api_user['username'], db.now()))
    return jsonify({'ok': True})


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
    diff = body.get('diff') if isinstance(body.get('diff'), str) else ''
    if len(diff) > 400_000:
        return _error(413, 'The diff is too large (max 400,000 characters). Narrow it to the changed folder or files.')
    ok, msg, ids = services.generate_cases(pid, folder_id, task, body.get('count') or 6, '', g.api_user['username'],
                                           diff_text=diff or None)
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


# ---------------------------------------------------------------- runs from the Tester CLI

RUN_FILE = re.compile(r'^[\w.-]{1,120}$')
RUN_EXT = ('.ndjson', '.webm', '.png', '.jpg', '.html', '.json')
MAX_UPLOAD = 400 * 1024 * 1024


@bp.post('/runs')
@api_auth
def run_upload():
    """A run made by `tester run` on the developer's PC (multipart). Either case_id (an existing case), or
    project_id + content (+ title) for a plain-English test: that one is saved as a case in folder "CLI runs".
    Files: events.ndjson (required), video.webm, report.html, step screenshots."""
    request.max_content_length = MAX_UPLOAD   # recordings are bigger than the app-wide 10 MB form limit
    f = request.form
    files = request.files.getlist('files')
    events = next((x for x in files if x.filename == 'events.ndjson'), None)
    if not events:
        return _error(400, 'events.ndjson is required (the runner writes it in the run folder).')
    bad = [x.filename for x in files if not RUN_FILE.match(x.filename or '') or not x.filename.lower().endswith(RUN_EXT)]
    if bad:
        return _error(400, f'Unexpected file name(s): {", ".join(bad[:5])}')
    con, user = g.api_con, g.api_user['username']
    case_id = f.get('case_id', type=int)
    if case_id:
        row = con.execute('SELECT id, project_id FROM cases WHERE id=?', (case_id,)).fetchone()
        if not row:
            return _error(404, 'Test case not found.')
    else:
        pid, content = f.get('project_id', type=int), (f.get('content') or '').replace('\r\n', '\n').strip()
        if not pid or not con.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
            return _error(400, 'project_id must be an existing project (tester projects) when no case_id is sent.')
        if not content or len(content) > 100_000:
            return _error(400, 'content (the test case markdown) is required, max 100,000 characters.')
    run_dir = os.path.join(db.RUNS_DIR, f"{datetime.now():%Y-%m-%d-%H-%M-%S}-cli-{secrets.token_hex(4)}")
    os.makedirs(run_dir)
    try:
        for x in files:
            x.save(os.path.join(run_dir, x.filename))
        summary, _ = db.read_run_events(run_dir)
        if not summary:
            raise ValueError('events.ndjson has no run_end line: the run did not finish.')
        with con:
            if not case_id:
                title = (f.get('title') or db.case_title(content, '') or 'CLI test')[:180]
                folder = db.get_or_create_folder(con, pid, 'CLI runs')
                case_id = db.add_case(con, pid, folder, title, content + '\n', user)
            run_id = con.execute("INSERT INTO runs(case_id, status, started_at, run_by, env_name) VALUES (?,?,?,?,?)",
                                 (case_id, 'running', db.to_local(summary.get('at')), user,
                                  (f.get('env') or 'Local (CLI)')[:40])).lastrowid
            db.record_finished_run(con, run_id, run_dir)
    except Exception as e:   # nothing half-saved: remove the folder
        shutil.rmtree(run_dir, ignore_errors=True)
        return _error(400, f'Upload failed: {e}')
    base = request.host_url.rstrip('/')
    return jsonify({'ok': True, 'run_id': run_id, 'case_id': case_id, 'status': summary.get('status'),
                    'url': f'{base}/cases/{case_id}?run={run_id}'})


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
