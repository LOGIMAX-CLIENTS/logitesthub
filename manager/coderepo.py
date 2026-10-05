"""Code repository per project: a read-only copy of the application's source (GitHub, read access is enough) that the
Test Assistant searches to write exact steps (page URL, field ids, validation messages) for a scenario.

- LogiTestHub keeps its OWN copy in data/repos/p<project id>: latest commit only (--depth 1), and only the folders
  listed in "Paths" (sparse checkout) - never the developer's working folder.
- Sign-in to GitHub: the git credentials of this PC (git credential manager / gh), or a read-only token stored encrypted
  (sent per command as a header, never written into the copy's git config).
- Sync = fetch the branch tip and reset the copy to it. Nothing is ever pushed.

Registered from app.py with register(app, con, admin_required, project_or_404).
"""
import base64
import os
import re
import shutil
import subprocess
import threading

from flask import Blueprint, flash, redirect, render_template, request, url_for

import db

REPOS = os.path.join(db.DATA, 'repos')
URL_RE = re.compile(r'^https://[A-Za-z0-9.-]+/[\w.-]+/[\w.-]+?(\.git)?/?$')
BRANCH_RE = re.compile(r'^[\w./-]{1,100}$')
PATH_RE = re.compile(r'^[\w./ -]{1,200}$')
_LOCK = threading.Lock()
_RUNNING = set()

COLUMNS = [('repo_url', 'VARCHAR(300) NULL'), ('repo_branch', 'VARCHAR(100) NULL'), ('repo_paths', 'TEXT NULL'),
           ('repo_token_enc', 'TEXT NULL'), ('repo_commit', 'VARCHAR(64) NULL'), ('repo_synced_at', 'VARCHAR(32) NULL'),
           ('repo_status', 'VARCHAR(16) NULL'), ('repo_error', 'VARCHAR(500) NULL')]


def migrate(con):
    have = {r['Field'] for r in con.execute('SHOW COLUMNS FROM projects')}
    for col, ddl in COLUMNS:
        if col not in have:
            con.execute(f'ALTER TABLE projects ADD COLUMN {col} {ddl}')


def repo_dir(pid):
    return os.path.join(REPOS, f'p{int(pid)}')


def code_dir(p):
    """The synced copy for this project, or None when there is none yet."""
    d = repo_dir(p['id'])
    return d if p.get('repo_url') and p.get('repo_commit') and os.path.isdir(os.path.join(d, '.git')) else None


def _git(args, cwd=None, token=None, timeout=900):
    pre = ['git', '-c', 'core.longpaths=true', '-c', 'advice.detachedHead=false']
    if token:
        auth = base64.b64encode(f'x-access-token:{token}'.encode()).decode()
        # only the token: no fallback to this PC's personal GitHub login when the token is wrong or expired
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


def _set_status(pid, **kw):
    c = db.connect()
    try:
        with c:
            sets = ', '.join(f'{k}=?' for k in kw)
            c.execute(f'UPDATE projects SET {sets} WHERE id=?', (*kw.values(), pid))
    finally:
        c.close()


def sync(pid):
    """Clone (first time) or update the project's copy to the tip of its branch. Runs in a background thread."""
    import envs
    c = db.connect()
    try:
        p = c.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    finally:
        c.close()
    if not p or not p['repo_url']:
        return
    token = envs.decrypt(p['repo_token_enc']) if p['repo_token_enc'] else None
    branch = p['repo_branch'] or 'main'
    paths = [x.strip().strip('/') for x in (p['repo_paths'] or '').splitlines() if x.strip()]
    d = repo_dir(pid)
    try:
        os.makedirs(REPOS, exist_ok=True)
        fresh = not os.path.isdir(os.path.join(d, '.git'))
        if not fresh:
            current = _git(['remote', 'get-url', 'origin'], cwd=d)
            if current != p['repo_url']:   # the URL was changed: start a new copy
                shutil.rmtree(d, ignore_errors=True)
                fresh = True
        if fresh:
            _git(['clone', '--depth', '1', '--single-branch', '--branch', branch, '--filter=blob:none', '--no-checkout',
                  p['repo_url'], d], token=token)
        if paths:
            _git(['sparse-checkout', 'set', '--no-cone', *[f'/{x}/' if '.' not in os.path.basename(x) else f'/{x}' for x in paths]],
                 cwd=d, token=token)
        else:
            _git(['sparse-checkout', 'disable'], cwd=d, token=token)
        _git(['fetch', '--depth', '1', 'origin', branch], cwd=d, token=token)
        _git(['checkout', '-f', '-B', branch, 'FETCH_HEAD'], cwd=d, token=token)
        commit = _git(['rev-parse', 'HEAD'], cwd=d)
        _set_status(pid, repo_commit=commit, repo_synced_at=db.now(), repo_status='ok', repo_error=None)
    except subprocess.TimeoutExpired:
        _set_status(pid, repo_status='error', repo_error='The copy took longer than 15 minutes and was stopped.')
    except Exception as e:   # shown on the page as it is (git's own words, token masked)
        msg = str(e)
        if re.search(r'Authentication failed|could not read Username|403|Repository not found', msg):
            msg += ' - check the URL, and that this PC (or the token) has read access to the repository.'
        _set_status(pid, repo_status='error', repo_error=msg[:500])
    finally:
        with _LOCK:
            _RUNNING.discard(pid)


def start_sync(pid):
    with _LOCK:
        if pid in _RUNNING:
            return False
        _RUNNING.add(pid)
    _set_status(pid, repo_status='syncing', repo_error=None)
    threading.Thread(target=sync, args=(pid,), daemon=True).start()
    return True


def stats(p):
    d = code_dir(p)
    if not d:
        return None
    files, size = 0, 0
    for root, dirs, fs in os.walk(d):
        if '.git' in dirs:
            dirs.remove('.git')
        files += len(fs)
        size += sum(os.path.getsize(os.path.join(root, f)) for f in fs if os.path.isfile(os.path.join(root, f)))
    return {'files': files, 'mb': round(size / 1048576, 1)}


def register(app, con, admin_required, project_or_404):
    c0 = db.connect()
    try:
        with c0:
            migrate(c0)
        with c0:   # a sync that was running when the server stopped will not finish: show it as stopped
            c0.execute("UPDATE projects SET repo_status='error', repo_error='Stopped when the server restarted. Sync again.' "
                       "WHERE repo_status='syncing'")
    finally:
        c0.close()

    bp = Blueprint('coderepo', __name__)

    @bp.route('/projects/<int:pid>/code', methods=['GET', 'POST'])
    @admin_required
    def page(pid):
        import envs
        p = project_or_404(pid)
        if request.method == 'POST':
            f = request.form
            if f.get('action') == 'disconnect':
                shutil.rmtree(repo_dir(pid), ignore_errors=True)
                c = con()
                with c:
                    c.execute('UPDATE projects SET repo_url=NULL, repo_branch=NULL, repo_paths=NULL, repo_token_enc=NULL, '
                              'repo_commit=NULL, repo_synced_at=NULL, repo_status=NULL, repo_error=NULL WHERE id=?', (pid,))
                flash('Code repository disconnected and the local copy removed.', 'success')
                return redirect(url_for('coderepo.page', pid=pid))
            if f.get('action') == 'sync':
                flash('Sync started.' if start_sync(pid) else 'A sync is already running.', 'info')
                return redirect(url_for('coderepo.page', pid=pid))
            url = f.get('repo_url', '').strip()
            branch = f.get('repo_branch', '').strip() or 'main'
            paths = [x.strip().strip('/') for x in f.get('repo_paths', '').replace('\r', '').split('\n') if x.strip()]
            errs = []
            if not URL_RE.match(url):
                errs.append('Repository URL: an https address like https://github.com/<org>/<repo>.git')
            if not BRANCH_RE.match(branch):
                errs.append('Branch: letters, numbers and . _ / - only.')
            bad = [x for x in paths if not PATH_RE.match(x) or '..' in x]
            if bad or len(paths) > 40:
                errs.append('Paths: folder paths inside the repo, one per line (at most 40), e.g. admin/application')
            if errs:
                for e in errs:
                    flash(e, 'danger')
                return redirect(url_for('coderepo.page', pid=pid))
            tok = f.get('repo_token', '').strip()
            c = con()
            with c:
                c.execute('UPDATE projects SET repo_url=?, repo_branch=?, repo_paths=? WHERE id=?',
                          (url, branch, '\n'.join(paths) or None, pid))
                if tok:
                    c.execute('UPDATE projects SET repo_token_enc=? WHERE id=?', (envs.encrypt(tok), pid))
                if f.get('clear_token'):
                    c.execute('UPDATE projects SET repo_token_enc=NULL WHERE id=?', (pid,))
            start_sync(pid)
            flash('Saved. Copying the repository now: this page refreshes until it is done.', 'success')
            return redirect(url_for('coderepo.page', pid=pid))
        p = project_or_404(pid)
        return render_template('code_repo.html', p=p, stats=stats(p))

    app.register_blueprint(bp)
