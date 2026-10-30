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


@bp.after_request
def _cli_version(resp):
    """Every answer says which CLI code this server hands out: an older CLI updates itself (cli/lth.js)."""
    try:
        import clitool
        resp.headers['X-Tester-CLI'] = clitool.fingerprint()
    except Exception:   # never break an answer over this
        pass
    return resp

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


@bp.post('/workspace')
@api_auth
def workspace_report():
    """{"tests_dir": "F:/xampp/htdocs/etail/tests", "root": ..., "project": 3}: the tester CLI's tests/ folder. When it
    is on this PC, Settings -> System uses its variables/ and helpers/ (unless fixed paths are saved there)."""
    import settings
    body = request.get_json(silent=True) or {}
    tests_dir = str(body.get('tests_dir') or '').strip()
    if not tests_dir or len(tests_dir) > 500:
        return _error(400, 'tests_dir is required.')
    used = settings.set_workspace(g.api_con, tests_dir, str(body.get('root') or '')[:500],
                                  body.get('project') if isinstance(body.get('project'), int) else None,
                                  g.api_user['username'])
    return jsonify({'ok': True, 'used': used})


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
    import onboard
    r = g.api_con.execute('SELECT c.id, c.tc_no, c.title, c.content, c.project_id, c.updated_at, c.updated_by, c.folder_id, '
                          'f.name AS folder FROM cases c LEFT JOIN folders f ON f.id=c.folder_id WHERE c.id=?', (cid,)).fetchone()
    if not r:
        return _error(404, 'Test case not found.')
    module, sub = onboard.folder_path(g.api_con, r['folder_id'])   # tester case pull files it under tests/<module>/<sub>/
    return jsonify({'ok': True, 'case': dict(r, tc=f"TC-{r['tc_no']}", module=module, sub_module=sub)})


@bp.put('/cases/<int:cid>')
@api_auth
def case_update(cid):
    """Saves corrected steps from the CLI / IDE (tester case push), so a fix made while debugging is not lost.
    expected_updated_at = the version the caller started from: someone else's newer edit is refused (409), not overwritten."""
    import settings
    data = request.get_json(silent=True) or {}
    content = str(data.get('content') or '').replace('\r\n', '\n').strip() + '\n'
    title = db.case_title(content, '')
    if not title:
        return _error(400, 'The case needs a title: a line starting with "# ".')
    r = g.api_con.execute('SELECT id, tc_no, content, updated_at, updated_by FROM cases WHERE id=?', (cid,)).fetchone()
    if not r:
        return _error(404, 'Test case not found.')
    expected = data.get('expected_updated_at')
    if expected and expected != r['updated_at']:
        return jsonify({'ok': False, 'error': 'conflict', 'updated_at': r['updated_at'], 'updated_by': r['updated_by'],
                        'message': f"TC-{r['tc_no']} was changed by {r['updated_by'] or 'someone'} at {r['updated_at']} "
                                   'after you pulled it. Pull it again and redo your change (or push with --force).'}), 409
    if content == r['content']:
        return jsonify({'ok': True, 'changed': False, 'updated_at': r['updated_at']})
    now = db.now()
    with g.api_con:
        g.api_con.execute('UPDATE cases SET title=?, content=?, updated_by=?, updated_at=? WHERE id=?',
                          (title, content, g.api_user['username'], now, cid))
    settings.write_audit(g.api_user['username'], 'api.case_update', 'test case', cid, 'ok',
                         f"TC-{r['tc_no']} steps updated from the CLI / IDE" + (f": {data['note'][:200]}" if data.get('note') else ''),
                         None, request.remote_addr)
    return jsonify({'ok': True, 'changed': True, 'title': title, 'updated_at': now})


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
    """Login steps the CLI puts in front of every case of this project (the same as web runs). ?env=<name>: that
    environment's own login steps when it has them (source "environment"), else the project's (source "project")."""
    r = g.api_con.execute('SELECT login_helper FROM projects WHERE id=?', (pid,)).fetchone()
    if not r:
        return _error(404, 'Project not found.')
    env = None
    if request.args.get('env'):
        env, err = _env_from(request.args.get('env'))
        if err:
            return err
    steps, source = db.login_steps(g.api_con, pid, env)
    return jsonify({'ok': True, 'login_helper': steps, 'source': source, 'project_helper': r['login_helper'] or ''})


@bp.put('/projects/<int:pid>/login-helper')
@api_auth
def project_login_helper_set(pid):
    """{"login_helper": "<steps>", "force": false}: the project's login steps (tester login-helper set). An existing
    helper is replaced only with force (every case of the project runs it)."""
    import settings
    body = request.get_json(silent=True) or {}
    r = g.api_con.execute('SELECT login_helper FROM projects WHERE id=?', (pid,)).fetchone()
    if not r:
        return _error(404, 'Project not found.')
    steps = str(body.get('login_helper') or '').replace('\r\n', '\n').strip()[:4000]
    if not steps:
        return _error(400, 'login_helper is empty: send the login steps.')
    if (r['login_helper'] or '').strip() and not body.get('force'):
        return jsonify({'ok': False, 'error': 'This project already has a login helper. Show it to the user; replace it '
                        'only if they say so (--force).', 'login_helper': r['login_helper']}), 409
    con = g.api_con
    with con:
        con.execute('UPDATE projects SET login_helper=? WHERE id=?', (steps, pid))
    settings.write_audit(g.api_user['username'], 'api.login_helper', 'project', pid, 'ok',
                         'Login helper ' + ('replaced' if r['login_helper'] else 'set') + ' from the CLI / IDE', None, request.remote_addr)
    return jsonify({'ok': True, 'replaced': bool(r['login_helper'])})


# ---------------------------------------------------------------- repo setup (tester init): repo -> project

@bp.post('/projects')
@api_auth
def project_create():
    """{"name": "Retail", "description": ""}: the project with that name (any case), made when it does not exist."""
    name = ' '.join(str((request.get_json(silent=True) or {}).get('name') or '').split())[:120]
    if not name:
        return _error(400, 'name is required.')
    con = g.api_con
    p = con.execute('SELECT id, name FROM projects WHERE LOWER(name)=LOWER(?)', (name,)).fetchone()
    if p:
        return jsonify({'ok': True, 'created': False, 'project': {'id': p['id'], 'name': p['name']}})
    with con:
        pid = con.execute('INSERT INTO projects(name, description, created_by, created_at) VALUES (?,?,?,?)',
                          (name, str((request.get_json(silent=True) or {}).get('description') or '')[:500],
                           g.api_user['username'], db.now())).lastrowid
    return jsonify({'ok': True, 'created': True, 'project': {'id': pid, 'name': name}})


@bp.get('/repo')
@api_auth
def repo_project():
    """?remote=<git remote URL>: the LogiTestHub project this repo was linked to (by any developer), and what it still needs."""
    import onboard
    key = onboard.repo_key(request.args.get('remote'))
    if not key:
        return _error(400, 'remote is required (the repo\'s git remote URL).')
    r = onboard.project_of_repo(g.api_con, key)
    return jsonify({'ok': True, 'repo': key, 'linked': bool(r),
                    **({'linked_by': r['linked_by'], 'linked_at': r['linked_at'], 'tests_remote': r['tests_remote'],
                        **onboard.status(g.api_con, r['id'])} if r else {})})


@bp.post('/repo/tests')
@api_auth
def repo_tests():
    """{"remote": "<code repo git remote>", "tests_remote": "<git remote of the folder with its test cases>"}: the next
    developer of the code repo is offered that repo to clone (tester init)."""
    import onboard
    body = request.get_json(silent=True) or {}
    key = onboard.repo_key(body.get('remote'))
    tests = re.sub(r'^([a-z+]+://)[^/@]+@', r'\1', str(body.get('tests_remote') or '').strip(), flags=re.I)   # never keep a token
    if not key or not tests:
        return _error(400, 'remote and tests_remote are required.')
    con = g.api_con
    with con:
        n = onboard.set_tests_remote(con, key, tests)
    if not n:
        return _error(404, 'This repo is not linked to a project yet (tester init --project ...).')
    return jsonify({'ok': True, 'repo': key, 'tests_remote': tests})


@bp.post('/repo')
@api_auth
def repo_link():
    """{"remote": "<git remote URL>", "project_id": 1}: this repo's tests go to that project, for every developer."""
    import onboard
    body = request.get_json(silent=True) or {}
    key = onboard.repo_key(body.get('remote'))
    pid = body.get('project_id')
    if not key or not isinstance(pid, int):
        return _error(400, 'remote and project_id are required.')
    if not _project(pid):
        return _error(404, 'Project not found.')
    con = g.api_con
    with con:
        onboard.link(con, key, pid, body.get('remote'), g.api_user['username'])
    return jsonify({'ok': True, 'repo': key, **onboard.status(con, pid)})


@bp.delete('/repo')
@api_auth
def repo_unlink():
    """?remote=<git remote URL>: the repo is no longer linked (it was linked to the wrong project); tester init asks again."""
    import onboard
    import settings
    key = onboard.repo_key(request.args.get('remote'))
    if not key:
        return _error(400, 'remote is required.')
    con = g.api_con
    onboard.ensure(con)
    with con:
        n = con.execute('DELETE FROM project_repos WHERE repo=?', (key,)).rowcount
    if n:
        settings.write_audit(g.api_user['username'], 'api.repo_unlink', 'repo', None, 'ok', f'Repo {key} unlinked from its project',
                             None, request.remote_addr)
    return jsonify({'ok': True, 'repo': key, 'unlinked': bool(n)})


@bp.get('/projects/<int:pid>/setup')
@api_auth
def project_setup(pid):
    """What the project still needs: login helper, modules (tester init shows it)."""
    import onboard
    if not _project(pid):
        return _error(404, 'Project not found.')
    return jsonify({'ok': True, **onboard.status(g.api_con, pid)})


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


# ---------------------------------------------------------------- live settings (tester settings)

@bp.get('/projects/<int:pid>/settings')
@api_auth
def live_settings(pid):
    """The settings / permissions lth_brain/<project>/settings.json lists, read now on --env (read-only)."""
    import knowledge
    p = _project(pid)
    if not p:
        return _error(404, 'Project not found.')
    env, err = _env_from(request.args.get('env'))
    if err:
        return err
    text = knowledge.settings_text(p['name'], env)
    return jsonify({'ok': True, 'env': env['name'], 'text': text or 'No settings.json for this project, or no database on this environment.'})


# ---------------------------------------------------------------- precondition check (tester check)

@bp.post('/check')
@api_auth
def precondition_check():
    """Precondition check + test data validation before a run (fail fast): the case's leading Remember SQL / Assert SQL
    steps - settings, permissions, the data it picks - run read-only on the environment's database, in order, up to the
    first screen step. Body: {"content": "<_test.md>"} or {"case_id": N}, plus {"env": "<name or id>"}."""
    import assistant
    import envs
    body = request.get_json(silent=True) or {}
    content = body.get('content')
    if body.get('case_id'):
        r = g.api_con.execute('SELECT content FROM cases WHERE id=?', (body.get('case_id'),)).fetchone()
        if not r:
            return _error(404, 'Test case not found.')
        content = r['content']
    if not content:
        return _error(400, 'Send the test case: content (the _test.md) or case_id.')
    env, err = _env_from(body.get('env'))
    if err:
        return err
    if env['db_on_pc']:
        return jsonify({'ok': True, 'env': env['name'], 'status': 'no_database', 'checks': [],
                        'message': f'The database of "{env["name"]}" is on your PC, so LogiTestHub cannot check it. The database '
                                   f'steps run on your PC when you run the test: tester run ... --env "{env["name"]}".'})
    if not envs.db_configured(env):
        return jsonify({'ok': True, 'env': env['name'], 'status': 'no_database', 'checks': [],
                        'message': f'"{env["name"]}" has no database, so nothing could be checked (database steps would fail there).'})
    steps = assistant.steps_of(content)
    checks = assistant.preflight({'title': '', 'steps': steps}, env)
    import coderepo
    import elementcheck
    elems = elementcheck.check({'steps': steps}, coderepo.code_dir(env))   # every (#id) / (.class) looked up in the code
    missing = [e for e in elems if not e['ok']]
    bad = next((c for c in checks if not c['ok']), None)
    if missing and not bad:
        bad = {'n': missing[0]['n'], 'problem': missing[0].get('problem') or
               'uses ' + ', '.join(missing[0]['missing']) + ' which is not in the code'}
    sql_total = sum(1 for s in steps if assistant.SQL_STEP.match(s['text']))
    return jsonify({'ok': True, 'env': env['name'], 'status': 'failed' if bad else 'passed', 'checks': checks,
                    'elements': {'checked': len(elems), 'missing': missing},
                    'checked': len(checks), 'sql_steps': sql_total,
                    'message': (f'Step {bad["n"]} {bad["problem"]}: do not run yet - fix the data / setting or the step.' if bad else
                                f'{len(checks)} database step(s) checked on {env["name"]}: settings and test data are there.'
                                if checks else 'No database steps before the first screen step: nothing to check.')})


# ---------------------------------------------------------------- data a passed run kept (tester run --clean / --keep)

@bp.get('/cases/<int:cid>/kept-data')
@api_auth
def kept_data(cid):
    """What passed runs of this case left on the environment (--env), and the section that removes it."""
    import cleanup
    env_row = _env_from(request.args.get('env'))[0] if request.args.get('env') else None
    rows = cleanup.pending(g.api_con, cid, env_row['id'] if env_row else None)
    return jsonify({'ok': True, 'ids': [r['id'] for r in rows], 'runs': cleanup.describe(rows), 'section': cleanup.section(rows)})


# ---------------------------------------------------------------- PM tasks (tester task ...)

@bp.get('/task')
@api_auth
def task_active():
    """The caller's active PM task (Start testing), or null."""
    import pmtasks
    return jsonify({'ok': True, 'task': pmtasks.active_task(g.api_con, g.api_user['username'])})


@bp.post('/tasks/<code>/start')
@api_auth
def task_start(code):
    import pmtasks
    c = pmtasks.norm(code)
    if not c:
        return _error(400, 'Task code like PM-LMX-1741.')
    old = pmtasks.start(g.api_con, g.api_user['username'], c)
    pmtasks.attach_later(c, g.api_user['username'])
    return jsonify({'ok': True, 'task': c, 'stopped': old, 'hours': pmtasks.ACTIVE_HOURS,
                    'url': pmtasks.task_url(pmtasks.public_base(), c, g.api_user['username'])})


@bp.post('/task/running')
@api_auth
def task_running():
    """The CLI at the start / end of a run: PM's Tester CLI button blinks while it runs. Body: {running, task?}."""
    import pmtasks
    f = request.get_json(silent=True) or {}
    code, state = pmtasks.run_live(g.api_con, g.api_user['username'], f.get('task'), bool(f.get('running')))
    return jsonify({'ok': True, 'task': code, 'pm': state})


@bp.post('/task/stop')
@api_auth
def task_stop():
    import pmtasks
    code = pmtasks.active_task(g.api_con, g.api_user['username'])
    pmtasks.stop(g.api_con, g.api_user['username'])
    return jsonify({'ok': True, 'stopped': code})


@bp.get('/tasks/<code>')
@api_auth
def task_get(code):
    """The task's test cases with their latest run, plus the PM link state."""
    import pmtasks
    c = pmtasks.norm(code)
    if not c:
        return _error(400, 'Task code like PM-LMX-1741.')
    me = g.api_user['username']
    rows = pmtasks.task_cases(g.api_con, c)
    link = g.api_con.execute('SELECT status, message FROM pm_task_links WHERE task_code=? AND attached_by=?', (c, me)).fetchone()
    base = pmtasks.public_base()
    return jsonify({'ok': True, 'task': c, 'url': pmtasks.task_url(base, c, me), **pmtasks.summary(rows),
                    'pm_link': dict(link) if link else None,
                    'tests': [{'id': r['id'], 'tc': f"TC-{r['tc_no']}", 'title': r['title'], 'status': r['run_status'] or 'NOT RUN',
                               'fail_step': r['fail_step'], 'fail_reason': r['fail_reason'],
                               'url': f"{base}/cases/{r['id']}" + (f"?run={r['run_id']}" if r['run_id'] else '')} for r in rows]})


# ---------------------------------------------------------------- environments (tester env list / tester env add)

def _env_json(e, me):
    return {'id': e['id'], 'name': e['name'], 'base_url': e['base_url'], 'mine': e['owner'] == me,
            'scope': 'only_me' if e['owner'] else 'shared', 'is_default': bool(e['is_default']), 'branch': e['branch'],
            'login_user': e['username'], 'has_login_password': bool(e['has_password']),
            'db': ({'host': e['db_host'], 'port': e['db_port'] or 3306, 'name': e['db_name'], 'user': e['db_user'],
                    'on_pc': bool(e['db_on_pc']), 'set_up_sql': bool(e['db_allow_write'])} if e['db_host'] else None)}


@bp.get('/environments')
@api_auth
def environments_list():
    """The shared environments and the caller's own (only-for-me) ones; never a password."""
    import envs
    me = g.api_user['username']
    return jsonify({'ok': True, 'environments': [_env_json(e, me) for e in envs.all_envs(g.api_con, me)]})


@bp.post('/environments')
@api_auth
def environment_save():
    """Adds or updates one of the CALLER's own environments (only they see it), by name:
    {"name", "base_url", "login_user"?, "login_password"?, "branch"?,
     "db": {"host", "port"?, "name", "user", "on_pc": true|false, "password"? (server-side DB only), "set_up_sql"?} | {"remove": true}}
    A database "on_pc" is the developer's own (e.g. XAMPP MySQL): the Tester CLI on that PC runs the SQL steps and
    keeps its password there - none is sent here. A field left out keeps its saved value."""
    import envs
    import settings
    me = g.api_user['username']
    b = request.get_json(silent=True) or {}
    con = g.api_con
    name = str(b.get('name') or '').strip()
    if not envs.NAME_RE.match(name):
        return _error(400, 'name: 2-40 letters, numbers, space, dot, dash or underscore (e.g. Local).')
    if con.execute('SELECT 1 FROM environments WHERE name=? AND owner IS NULL', (name,)).fetchone():
        return _error(400, f'"{name}" is a shared environment: give your own one another name (e.g. Local).')
    old = con.execute('SELECT * FROM environments WHERE name=? AND owner=?', (name, me)).fetchone()
    v = {}
    if 'base_url' in b or not old:
        url = envs.normalise_url(b.get('base_url'))
        if not url:
            return _error(400, 'base_url must start with http:// or https:// (e.g. http://localhost/etail/admin/).')
        v['base_url'] = url
    for key, col, n in (('login_user', 'username', 120), ('branch', 'branch', 120), ('note', 'note', 255)):
        if key in b:
            v[col] = str(b.get(key) or '').strip()[:n] or None
    if 'login_password' in b:
        v['password_enc'] = envs.encrypt(str(b.get('login_password') or '')) if b.get('login_password') else None
    d = b.get('db')
    if isinstance(d, dict) and d.get('remove'):
        v.update(db_host=None, db_port=None, db_name=None, db_user=None, db_password_enc=None, db_write_user=None,
                 db_write_password_enc=None, db_allow_write=0, db_on_pc=0)
    elif isinstance(d, dict):
        host, dbname, user = (str(d.get(k) or '').strip() for k in ('host', 'name', 'user'))
        port = d.get('port') or 3306
        if not (host and dbname and user):
            return _error(400, 'db: host, name and user are all needed.')
        if not str(port).isdigit() or not 0 < int(port) < 65536:
            return _error(400, 'db.port must be a number (e.g. 3306).')
        on_pc = bool(d.get('on_pc'))
        wuser = str(d.get('write_user') or '').strip()[:64]
        write = bool(d.get('set_up_sql') or wuser)
        if write and envs.LIVE_NAME.search(name):
            return _error(400, f'"{name}" looks like a live environment: "Set up SQL" (database writes) cannot be switched on there.')
        if on_pc:   # the CLI on the owner's PC connects; this server keeps no database password for it
            v.update(db_host=host[:255], db_port=int(port), db_name=dbname[:64], db_user=user[:64], db_password_enc=None,
                     db_write_user=(wuser or user[:64]) if write else None, db_write_password_enc=None,
                     db_allow_write=1 if write else 0, db_on_pc=1)
        else:
            if user.lower() in ('root', 'admin'):
                return _error(400, 'db.user: use a read-only user (SELECT only) for a database LogiTestHub connects to, never root / admin.')
            wpw = d.get('write_password')
            keep_wpw = old['db_write_password_enc'] if old and not old['db_on_pc'] and old['db_write_user'] == wuser else None
            if write and not (wuser and (wpw or keep_wpw)):
                return _error(400, '"Set up SQL" needs a write user and its password (db.write_user, db.write_password).')
            pw = d.get('password')
            v.update(db_host=host[:255], db_port=int(port), db_name=dbname[:64], db_user=user[:64], db_on_pc=0,
                     db_password_enc=envs.encrypt(str(pw)) if pw else (old['db_password_enc'] if old and not old['db_on_pc'] else None),
                     db_write_user=wuser if write else None,
                     db_write_password_enc=(envs.encrypt(str(wpw)) if wpw else keep_wpw) if write else None,
                     db_allow_write=1 if write else 0)
    with con:
        if old:
            if v:
                con.execute(f'UPDATE environments SET {", ".join(k + "=?" for k in v)}, updated_at=? WHERE id=?',
                            (*v.values(), db.now(), old['id']))
            eid, what = old['id'], 'updated'
        else:
            v.update(name=name, owner=me, created_by=me, updated_at=db.now(), is_default=0)
            eid = con.execute(f'INSERT INTO environments({", ".join(v)}) VALUES ({", ".join("?" for _ in v)})',
                              tuple(v.values())).lastrowid
            what = 'added'
    settings.write_audit(me, 'api.env_save', 'environment', eid, 'ok',
                         f'Personal environment "{name}" {what} from the Tester CLI'
                         + (' (database on the PC)' if v.get('db_on_pc') else ''), None, request.remote_addr)
    row = next(e for e in envs.all_envs(con, me) if e['id'] == eid)
    return jsonify({'ok': True, 'created': what == 'added', 'environment': _env_json(row, me)})


# ---------------------------------------------------------------- read-only checks on the app's database

def _env_from(value):
    import envs
    v = str(value or '').strip()
    if not v:
        return None, _error(400, 'Say which environment: --env <name> (Settings -> Environments).')
    me = g.api_user['username']
    row = g.api_con.execute('SELECT * FROM environments WHERE (id=? OR name=?) AND (owner IS NULL OR owner=?) '
                            'ORDER BY owner IS NULL LIMIT 1', (int(v) if v.isdigit() else -1, v, me)).fetchone()
    if not row:
        return None, _error(404, f'Environment "{v}" not found.')
    if not envs._usable(g.api_con, row, g.api_user):   # the caller's role must be allowed to use this environment
        return None, _error(403, f'Your role is not allowed to use the "{row["name"]}" environment.')
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


@bp.post('/sql-write')
@api_auth
def sql_write():
    """{"env": "Local", "sql": "UPDATE ... WHERE ..."} -> rows changed. Test-data setup ("Set up SQL" steps) on an
    environment where an admin allowed it; one statement, written to the audit log with who ran it."""
    import envs
    import settings
    body = request.get_json(silent=True) or {}
    env, err = _env_from(body.get('env'))
    if err:
        return err
    sql_text = str(body.get('sql') or '')
    try:
        r = envs.db_write(env, sql_text)
    except envs.DbError as e:
        settings.write_audit(g.api_user['username'], 'api.sql_write', 'environment', env['id'], 'failed',
                             f'Set up SQL on {env["name"]} refused / failed: {e} | {sql_text[:500]}', None, request.remote_addr)
        return _error(400, str(e))
    settings.write_audit(g.api_user['username'], 'api.sql_write', 'environment', env['id'], 'ok',
                         f'Set up SQL on {env["name"]}: {r["affected"]} row(s) changed'
                         + (f' (run {body["run"]})' if body.get('run') else '') + f' | {sql_text[:500]}', None, request.remote_addr)
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
    folder_note = None
    run_dir = os.path.join(db.RUNS_DIR, f"{datetime.now():%Y-%m-%d-%H-%M-%S}-cli-{secrets.token_hex(4)}")
    os.makedirs(run_dir)
    try:
        for x in files:
            x.save(os.path.join(run_dir, x.filename))
        summary, _ = db.read_run_events(run_dir)
        if not summary:
            raise ValueError('events.ndjson has no run_end line: the run did not finish.')
        with con:
            import onboard
            module, sub = f.get('module') or '', f.get('sub_module') or ''
            if not case_id:
                title = (f.get('title') or db.case_title(content, '') or 'CLI test')[:180]
                # the case file's module / sub-module (an existing folder only), else "CLI runs" as before
                folder = onboard.folder_for(con, pid, module, sub) if module else None
                if module and not folder:
                    folder_note = (f'Module "{module}' + (f' > {sub}' if sub else '') + '" is not in LogiTestHub: '
                                   'saved in "CLI runs". Add it (tester modules add), then run again to move the case.')
                folder = folder or db.get_or_create_folder(con, pid, 'CLI runs')
                case_id = db.add_case(con, pid, folder, title, content + '\n', user)
            elif module:   # an existing case still in "CLI runs" / no folder: move it to the module its file names
                cur = con.execute('SELECT c.project_id, c.folder_id, fo.name AS fname FROM cases c '
                                  'LEFT JOIN folders fo ON fo.id=c.folder_id WHERE c.id=?', (case_id,)).fetchone()
                target = onboard.folder_for(con, cur['project_id'], module, sub)
                if target and (not cur['folder_id'] or cur['fname'] == 'CLI runs'):
                    con.execute('UPDATE cases SET folder_id=? WHERE id=?', (target, case_id))
                elif not target:
                    folder_note = f'Module "{module}' + (f' > {sub}' if sub else '') + '" is not in LogiTestHub (tester modules add).'
            env_row = _env_from(f.get('env'))[0] if f.get('env') else None   # the --env whose database the run used
            run_id = con.execute("INSERT INTO runs(case_id, status, started_at, run_by, env_name, env_id) VALUES (?,?,?,?,?,?)",
                                 (case_id, 'running', db.to_local(summary.get('at')), user,
                                  (f"{env_row['name']} (CLI)" if env_row else 'Local (CLI)')[:40],
                                  env_row['id'] if env_row else None)).lastrowid
            # the developer's branch / commit: only when the folder the CLI ran in is this project's code repository
            # (a tests repository, or another app's code, says nothing about the code under test)
            import envs as _envs
            import onboard
            cb, cc, cr = (f.get('code_branch') or '').strip(), (f.get('code_commit') or '').strip().lower(), f.get('code_remote')
            linked = onboard.project_of_repo(con, onboard.repo_key(cr)) if cr and onboard.repo_key(cr) else None
            case_pid = con.execute('SELECT project_id FROM cases WHERE id=?', (case_id,)).fetchone()['project_id']
            if linked and linked['id'] == case_pid and _envs.SHA_RE.match(cc):   # a new case has no `cur`
                con.execute('UPDATE runs SET code_branch=?, code_commit=?, code_repo=? WHERE id=?',
                            (cb if _envs.BRANCH_RE.match(cb) else None, cc,
                             re.sub(r'^([a-z+]+://)[^/@]+@', r'\1', str(cr), flags=re.I)[:300], run_id))   # never keep a token
            import cleanup   # kept data the CLI removed at the start of this run (tester run --clean)
            cleanup.mark(con, (f.get('cleared') or '').split(','), case_id, run_id)
            db.record_finished_run(con, run_id, run_dir)
            import pmtasks
            task = pmtasks.tag(con, case_id, user, 'cli', f.get('task'))
            pmtasks.run_started(con, run_id, case_id, user, task)
    except Exception as e:   # nothing half-saved: remove the folder
        shutil.rmtree(run_dir, ignore_errors=True)
        return _error(400, f'Upload failed: {e}')
    import s3store
    s3store.offload_later(run_id)   # screenshots / video to S3 when it is set up
    if task:
        pmtasks.run_finished(run_id)   # the run's link goes to the PM task, as the user who ran it
    base = request.host_url.rstrip('/')
    return jsonify({'ok': True, 'run_id': run_id, 'case_id': case_id, 'status': summary.get('status'),
                    'url': f'{base}/cases/{case_id}?run={run_id}', 'task': task, 'folder_note': folder_note,
                    'task_url': f'{base}/tasks/{task}' if task else None})


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
