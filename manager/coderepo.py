"""Code repository per ENVIRONMENT: a read-only copy of the application's source (GitHub, read access is enough) that
the Test Assistant searches to write exact steps (page URL, field ids, validation messages) for a scenario.

Each environment (Dev / QA / Live ...) names the repository and the branch running there - e.g. Dev = development,
Live = PRODUCTION - so the steps follow the code that the test will actually run against. It is set in the
environment's own form (Settings -> Environments); the Test Assistant reads the copy of the environment picked in
its "Run on" box.

- LogiTestHub keeps its OWN copy in data/repos/e<environment id>: the last HISTORY commits (file contents fetched only
  when read: --filter=blob:none; auto-heal reads who changed a field from them), and only the folders
  listed in "Paths" (sparse checkout) - never the developer's working folder.
- Sign-in to GitHub: the git credentials of this server (git credential manager / gh), or a read-only token stored
  encrypted (sent per command as a header, never written into the copy's git config).
- Sync = fetch the branch tip and reset the copy to it. Nothing is ever pushed.

Registered from app.py with register(app, con, admin_required, project_or_404).
"""
import base64
import os
import re
import shutil
import subprocess
import threading

from flask import Blueprint, abort, flash, redirect, request, url_for

import db

REPOS = os.path.join(db.DATA, 'repos')
URL_RE = re.compile(r'^https://[A-Za-z0-9.-]+/[\w.-]+/[\w.-]+?(\.git)?/?$')
BRANCH_RE = re.compile(r'^[\w./-]{1,100}$')
LOCAL_RE = re.compile(r'^([A-Za-z]:[\\/]|/)')   # a folder on this server, e.g. C:\xampp\htdocs\wt-prod (Local environment)
LOCAL = 'local'                               # repo_branch / repo_commit of a local folder: read in place, never copied
PATH_RE = re.compile(r'^[\w./ -]{1,200}$')
HISTORY = '100'
_LOCK = threading.Lock()
_RUNNING = set()

COLUMNS = [('repo_url', 'VARCHAR(300) NULL'), ('repo_branch', 'VARCHAR(100) NULL'), ('repo_paths', 'TEXT NULL'),
           ('repo_token_enc', 'TEXT NULL'), ('repo_commit', 'VARCHAR(64) NULL'), ('repo_synced_at', 'VARCHAR(32) NULL'),
           ('repo_status', 'VARCHAR(16) NULL'), ('repo_error', 'VARCHAR(500) NULL')]
_FIELDS = ', '.join(c for c, _ in COLUMNS)


def migrate(con):
    have = {r['Field'] for r in con.execute('SHOW COLUMNS FROM environments')}
    for col, ddl in COLUMNS:
        if col not in have:
            con.execute(f'ALTER TABLE environments ADD COLUMN {col} {ddl}')
    # once: the repository used to be set per project - hand it to the default environment (with its copy), so the
    # Test Assistant keeps reading code after the update
    if con.execute("SELECT 1 FROM settings WHERE name='coderepo.per_env'").fetchone():
        return
    con.execute("INSERT INTO settings(name, value) VALUES ('coderepo.per_env', '1')")
    have_p = {r['Field'] for r in con.execute('SHOW COLUMNS FROM projects')}
    env = con.execute('SELECT id, repo_url FROM environments ORDER BY is_default DESC, id LIMIT 1').fetchone()
    if 'repo_url' not in have_p or not env or env['repo_url']:
        return
    p = con.execute(f'SELECT id, {_FIELDS} FROM projects WHERE repo_url IS NOT NULL ORDER BY id LIMIT 1').fetchone()
    if not p:
        return
    con.execute(f'UPDATE environments SET {", ".join(c + "=?" for c, _ in COLUMNS)} WHERE id=?',
                (*[p[c] for c, _ in COLUMNS], env['id']))
    old, new = os.path.join(REPOS, f'p{p["id"]}'), repo_dir(env['id'])
    if os.path.isdir(old) and not os.path.exists(new):
        try:
            os.replace(old, new)
        except OSError:   # could not move it: the next sync makes a fresh copy
            con.execute("UPDATE environments SET repo_commit=NULL, repo_status=NULL WHERE id=?", (env['id'],))


def repo_dir(eid):
    return os.path.join(REPOS, f'e{int(eid)}')


def is_local(url):
    return bool(url and LOCAL_RE.match(url))


def code_dir(env):
    """The synced copy for this environment (or its local folder, read in place), or None when there is none yet."""
    if not env:
        return None
    if is_local(env.get('repo_url')):
        return env['repo_url'] if os.path.isdir(env['repo_url']) else None
    d = repo_dir(env['id'])
    return d if env.get('repo_url') and env.get('repo_commit') and os.path.isdir(os.path.join(d, '.git')) else None


def _git(args, cwd=None, token=None, timeout=900):
    pre = ['git', '-c', 'core.longpaths=true', '-c', 'advice.detachedHead=false']
    if token:
        auth = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        # only the token: no fallback to this server's own GitHub login when the token is wrong or expired
        pre += ['-c', 'credential.helper=', '-c', f'http.extraHeader=Authorization: Basic {auth}']
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')   # never wait for a password prompt
    p = subprocess.run(pre + args, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=timeout, env=env)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip().splitlines()
        msg = next((l for l in reversed(msg) if l.strip()), 'git failed')
        if token:
            msg = msg.replace(token, '••••')
        raise RuntimeError(msg[:400])
    return p.stdout.strip()


def _set_status(eid, **kw):
    c = db.connect()
    try:
        with c:
            sets = ', '.join(f'{k}=?' for k in kw)
            c.execute(f'UPDATE environments SET {sets} WHERE id=?', (*kw.values(), eid))
    finally:
        c.close()


def sync(eid):
    """Clone (first time) or update the environment's copy to the tip of its branch. Runs in a background thread."""
    import envs
    c = db.connect()
    try:
        e = c.execute('SELECT * FROM environments WHERE id=?', (eid,)).fetchone()
    finally:
        c.close()
    if not e or not e['repo_url'] or is_local(e['repo_url']):
        if e and is_local(e['repo_url']):   # nothing to copy: the folder is read where it is
            ok = os.path.isdir(e['repo_url'])
            _set_status(eid, repo_commit=LOCAL, repo_synced_at=db.now(), repo_status='ok' if ok else 'error',
                        repo_error=None if ok else 'The folder does not exist on the LogiTestHub server.')
        with _LOCK:
            _RUNNING.discard(eid)
        return
    token = envs.decrypt(e['repo_token_enc']) if e['repo_token_enc'] else None
    branch = e['repo_branch'] or 'main'
    paths = [x.strip().strip('/') for x in (e['repo_paths'] or '').splitlines() if x.strip()]
    d = repo_dir(eid)
    try:
        os.makedirs(REPOS, exist_ok=True)
        fresh = not os.path.isdir(os.path.join(d, '.git'))
        if not fresh:
            current = _git(['remote', 'get-url', 'origin'], cwd=d)
            if current != e['repo_url']:   # the URL was changed: start a new copy
                shutil.rmtree(d, ignore_errors=True)
                fresh = True
        if fresh:
            _git(['clone', '--depth', HISTORY, '--single-branch', '--branch', branch, '--filter=blob:none', '--no-checkout',
                  e['repo_url'], d], token=token)
        if paths:
            _git(['sparse-checkout', 'set', '--no-cone', *[f'/{x}/' if '.' not in os.path.basename(x) else f'/{x}' for x in paths]],
                 cwd=d, token=token)
        else:
            _git(['sparse-checkout', 'disable'], cwd=d, token=token)
        _git(['fetch', '--depth', HISTORY, 'origin', branch], cwd=d, token=token)
        _git(['checkout', '-f', '-B', branch, 'FETCH_HEAD'], cwd=d, token=token)
        commit = _git(['rev-parse', 'HEAD'], cwd=d)
        _set_status(eid, repo_commit=commit, repo_synced_at=db.now(), repo_status='ok', repo_error=None)
    except subprocess.TimeoutExpired:
        _set_status(eid, repo_status='error', repo_error='The copy took longer than 15 minutes and was stopped.')
    except Exception as ex:   # shown on the page as it is (git's own words, token masked)
        msg = str(ex)
        if re.search(r'Authentication failed|could not read Username|403|Repository not found', msg):
            msg += ' - check the URL, and that this server (or the token) has read access to the repository.'
        _set_status(eid, repo_status='error', repo_error=msg[:500])
    finally:
        with _LOCK:
            _RUNNING.discard(eid)


def start_sync(eid):
    with _LOCK:
        if eid in _RUNNING:
            return False
        _RUNNING.add(eid)
    _set_status(eid, repo_status='syncing', repo_error=None)
    threading.Thread(target=sync, args=(eid,), daemon=True).start()
    return True


def form_values(f, existing=None):
    """The 'Code repository' part of the environment form -> (column values, error). Blank URL = no repository."""
    import envs
    url = f.get('repo_url', '').strip()
    if not url:
        return {c: None for c, _ in COLUMNS}, None
    if is_local(url):   # the source folder on this server (e.g. the Local environment's XAMPP site): read in place
        url = os.path.normpath(url)
        if not os.path.isdir(url):
            return None, f'Code folder "{url}" does not exist on the LogiTestHub server.'
        return {'repo_url': url, 'repo_branch': LOCAL, 'repo_paths': None, 'repo_token_enc': None, 'repo_commit': LOCAL,
                'repo_synced_at': db.now(), 'repo_status': 'ok', 'repo_error': None}, None
    branch = f.get('repo_branch', '').strip() or 'main'
    paths = [x.strip().strip('/') for x in f.get('repo_paths', '').replace('\r', '').split('\n') if x.strip()]
    if not URL_RE.match(url):
        return None, ('Code repository: an https address like https://github.com/<org>/<repo>.git, '
                      'or a folder on this server like C:\\xampp\\htdocs\\wt-prod')
    if not BRANCH_RE.match(branch):
        return None, 'Code repository branch: letters, numbers and . _ / - only.'
    if [x for x in paths if not PATH_RE.match(x) or '..' in x] or len(paths) > 40:
        return None, 'Code repository paths: folder paths inside the repo, one per line (at most 40), e.g. admin/application'
    tok = f.get('repo_token', '').strip()
    tok_enc = envs.encrypt(tok) if tok else (existing['repo_token_enc'] if existing else None)
    if f.get('clear_repo_token'):
        tok_enc = None
    v = {'repo_url': url, 'repo_branch': branch, 'repo_paths': '\n'.join(paths) or None, 'repo_token_enc': tok_enc}
    for c in ('repo_commit', 'repo_synced_at', 'repo_status', 'repo_error'):   # sync state is kept as it is
        v[c] = existing[c] if existing else None
    return v, None


def needs_sync(old, new):
    """After saving the form: copy again when anything that decides the copy changed (or there is no copy yet)."""
    if not new['repo_url'] or is_local(new['repo_url']):
        return False
    return (not old or not old['repo_commit'] or
            any((old[c] or None) != (new[c] or None) for c in ('repo_url', 'repo_branch', 'repo_paths', 'repo_token_enc')))


def save(con, eid, v):
    """Store the form values; removing the URL also removes the local copy."""
    with con:
        con.execute(f'UPDATE environments SET {", ".join(c + "=?" for c, _ in COLUMNS)} WHERE id=?',
                    (*[v[c] for c, _ in COLUMNS], eid))
    if not v['repo_url'] or is_local(v['repo_url']):   # no copy needed (none, or read in place)
        shutil.rmtree(repo_dir(eid), ignore_errors=True)


def register(app, con, admin_required, project_or_404):
    c0 = db.connect()
    try:
        with c0:
            migrate(c0)
        with c0:   # a sync that was running when the server stopped will not finish: show it as stopped
            c0.execute("UPDATE environments SET repo_status='error', repo_error='Stopped when the server restarted. Sync again.' "
                       "WHERE repo_status='syncing'")
    finally:
        c0.close()

    bp = Blueprint('coderepo', __name__)

    @bp.route('/admin/environments/<int:eid>/code', methods=['POST'])
    def action(eid):
        import settings
        from flask import session
        me = con().execute('SELECT * FROM users WHERE id=?', (session.get('uid'),)).fetchone() if session.get('uid') else None
        e = con().execute('SELECT id, name, owner FROM environments WHERE id=?', (eid,)).fetchone() or abort(404)
        if not me or not (e['owner'] == me['username'] if e['owner'] else settings.is_admin(me['role'])):
            abort(404)
        if request.form.get('action') == 'sync':
            flash(f'Copying the code for {e["name"]}: this page refreshes until it is done.' if start_sync(eid)
                  else 'A sync is already running.', 'info')
        return redirect(url_for('environments') + f'#env{eid}')

    @bp.route('/projects/<int:pid>/code')
    @admin_required
    def page(pid):   # old address (the repository used to be per project): it is set per environment now
        project_or_404(pid)
        flash('The code repository is now set per environment: open an environment\'s Edit → Code repository.', 'info')
        return redirect(url_for('environments'))

    app.register_blueprint(bp)
