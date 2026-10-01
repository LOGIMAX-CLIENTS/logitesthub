"""LogiTestHub - projects, folders, test cases and run recordings.

  python app.py            # serves on http://<this-pc>:5050 for the office LAN
"""
import functools
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import envs
import pricing
import services

app = Flask(__name__)
db.init()
with db.connect() as _c:
    envs.init(_c)   # environments table, runs.env_name, cases.qa_status


def recover_interrupted_runs():
    """Runs still 'running' / 'queued' when the server starts were cut off by the restart: nothing will ever
    finish them, and they would block their test case ('already running'). Close them out."""
    with db.connect() as c:
        n = c.execute("UPDATE runs SET status='ERROR', error=? WHERE status IN ('running', 'queued')",
                      ('Stopped: the LogiTestHub server restarted before this run finished. Run it again.',)).rowcount
        c.execute("UPDATE cases SET code_status=NULL, code_msg=? WHERE code_status='running'",
                  ('Sample execution stopped by a server restart. Run it again.',))
    if n:
        print(f' * marked {n} run(s) interrupted by the restart as ERROR')

_key_file = os.path.join(db.DATA, 'secret.key')
if not os.path.exists(_key_file):
    with open(_key_file, 'w') as f:
        f.write(secrets.token_hex(32))
with open(_key_file) as f:
    app.secret_key = f.read().strip()
app.config.update(MAX_CONTENT_LENGTH=10 * 1024 * 1024,   # CLI AI steps send a screenshot
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  TEMPLATES_AUTO_RELOAD=True)   # page edits show on refresh, no restart

SAFE_FILE = re.compile(r'^[\w.-]+$')
RUN_LOCK = threading.Semaphore(1)   # one browser run at a time on this PC


# ---------------------------------------------------------------- helpers

def con():
    if 'con' not in g:
        g.con = db.connect()
    return g.con


@app.teardown_appcontext
def _close(_exc):
    c = g.pop('con', None)
    if c is not None:
        c.close()


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not session.get('uid') or not current_user():   # also signs out a removed account
            session.pop('uid', None)
            return redirect(url_for('login', next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @functools.wraps(fn)
    @login_required
    def wrapper(*a, **kw):
        if current_user()['role'] != 'admin':
            abort(403)
        return fn(*a, **kw)
    return wrapper


def current_user():
    uid = session.get('uid')
    return con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() if uid else None


@app.before_request
def _csrf():
    if request.method == 'POST' and not request.path.startswith('/api/'):   # API uses access keys, not cookies
        sent = request.form.get('csrf', '')
        if not sent or not secrets.compare_digest(sent, session.get('csrf', '')):
            abort(400, 'Form expired - reload the page and try again.')


@app.context_processor
def _globals():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(16)
    me = current_user()
    nav_projects = con().execute('SELECT id, name FROM projects ORDER BY name').fetchall() if me else []
    env_list = envs.all_envs(con()) if me else []
    my_key = con().execute('SELECT key_hint FROM api_keys WHERE user_id=?', (me['id'],)).fetchone() if me else None
    return {'csrf': session['csrf'], 'me': me, 'nav_projects': nav_projects, 'my_key': my_key, 'env_list': env_list}


def project_or_404(pid):
    p = con().execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    return p or abort(404)


def case_or_404(cid):
    c = con().execute(
        'SELECT c.*, p.name AS project_name, f.name AS folder_name FROM cases c '
        'JOIN projects p ON p.id=c.project_id LEFT JOIN folders f ON f.id=c.folder_id WHERE c.id=?',
        (cid,)).fetchone()
    return c or abort(404)


def fmt_secs(ms):
    return f'{(ms or 0) / 1000:.1f}s'


app.jinja_env.filters['secs'] = fmt_secs

_VAR_RE = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')


def var_chips(text):
    """Escapes a step and wraps {{variables}} in chips for display."""
    from markupsafe import Markup, escape
    safe = str(escape(text))
    return Markup(_VAR_RE.sub(lambda m: f'<span class="var-chip" title="From the variables file">{m.group(1)}</span>', safe))


app.jinja_env.filters['varchips'] = var_chips


def time_ago(ts):
    """'2026-09-30 21:01:00' -> '4 hours ago'."""
    from datetime import datetime
    try:
        secs = (datetime.now() - datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S')).total_seconds()
    except ValueError:
        return ts or ''
    for size, unit in ((86400 * 365, 'year'), (86400 * 30, 'month'), (86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if secs >= size:
            n = int(secs // size)
            return f'{n} {unit}{"s" if n != 1 else ""} ago'
    return 'just now'


app.jinja_env.filters['ago'] = time_ago


# ---------------------------------------------------------------- auth

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = con().execute('SELECT * FROM users WHERE username=?', (request.form.get('username', '').strip(),)).fetchone()
        if u and check_password_hash(u['pw_hash'], request.form.get('password', '')):
            session.clear()
            session['uid'] = u['id']
            nxt = request.args.get('next', '')
            return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else url_for('home'))
        flash('Wrong username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------- profile + accounts

USERNAME_RE = re.compile(r'^[A-Za-z0-9._-]{3,30}$')
MIN_PW = 8


def _password_problem(pw, confirm):
    if len(pw) < MIN_PW:
        return f'Password must be at least {MIN_PW} characters.'
    if pw != confirm:
        return 'The two passwords do not match.'
    return None


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    me = current_user()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'details':
            name = request.form.get('name', '').strip()[:60]
            if not name:
                flash('Name is required.', 'danger')
            else:
                with con():
                    con().execute('UPDATE users SET name=? WHERE id=?', (name, me['id']))
                flash('Profile updated.', 'success')
        elif action in ('key_new', 'key_revoke'):
            import api
            with con():
                if action == 'key_new':
                    session['new_api_key'] = api.new_key(con(), me['id'])   # shown once, then dropped
                    flash('New access key created. Copy it now: it will not be shown again. The old key has stopped working.', 'success')
                else:
                    api.revoke_key(con(), me['id'])
                    flash('Access key revoked. The CLI / IDE can no longer connect with it.', 'success')
            return redirect(url_for('profile') + '#access-key')
        elif action == 'password':
            if not check_password_hash(me['pw_hash'], request.form.get('current', '')):
                flash('Current password is wrong.', 'danger')
            else:
                problem = _password_problem(request.form.get('new', ''), request.form.get('confirm', ''))
                if problem:
                    flash(problem, 'danger')
                else:
                    with con():
                        con().execute('UPDATE users SET pw_hash=? WHERE id=?',
                                      (generate_password_hash(request.form['new']), me['id']))
                    flash('Password changed.', 'success')
        return redirect(url_for('profile'))
    stats = con().execute(
        'SELECT (SELECT COUNT(*) FROM runs WHERE run_by=?) AS runs, '
        '(SELECT COUNT(*) FROM cases WHERE created_by=?) AS cases', (me['username'], me['username'])).fetchone()
    key = con().execute('SELECT key_hint, created_at, last_used_at, last_used_ip FROM api_keys WHERE user_id=?',
                        (me['id'],)).fetchone()
    import budget
    return render_template('profile.html', stats=stats, min_pw=MIN_PW, key=key, ai=budget.status(con(), me),
                           new_key=session.pop('new_api_key', None), api_base=request.host_url.rstrip('/') + '/api/v1')


@app.route('/users')
@admin_required
def users():
    import budget
    rows = con().execute('SELECT id, username, name, role, created_at FROM users ORDER BY username').fetchall()
    ai = {r['id']: budget.status(con(), r) for r in rows}
    return render_template('users.html', users=rows, min_pw=MIN_PW, ai=ai,
                           default_limit=budget.get_setting(con(), budget.S_USER_DEFAULT), plan=budget.plan(con()))


def _inr_or_none(raw):
    raw = (raw or '').replace(',', '').strip()
    if raw == '':
        return None, None
    try:
        v = float(raw)
    except ValueError:
        return None, 'Enter a number (rupees) or leave it blank for no limit.'
    if v < 0 or v > 10_000_000:
        return None, 'Limit must be between 0 and 1,00,00,000.'
    return round(v, 2), None


@app.route('/users/<int:uid>/ai-extra', methods=['POST'])
@admin_required
def user_ai_extra(uid):
    """One-off extra AI budget for this month only (heavy workload); next month the user is back to the normal limit."""
    import budget
    u = con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() or abort(404)
    val, err = _inr_or_none(request.form.get('amount_inr'))
    note = request.form.get('note', '').strip()[:255]
    pl = budget.plan(con())
    if err or not val or val > 100000:
        flash('Enter the extra amount in rupees (1 to 1,00,000).', 'danger')
    elif pl['reserve_inr'] is not None and val > pl['reserve_inr'] + 0.005:
        flash(f'Only {budget.inr(max(0, pl["reserve_inr"]))} is left in the reserve. Add Anthropic credits on the AI Usage page, '
              f'or give a smaller extra.', 'danger')
    elif not note:
        flash('Add a short reason (e.g. "GRN release testing") so the AI Usage report explains the extra spend.', 'danger')
    else:
        with con():
            con().execute('INSERT INTO ai_topups(user_id, month, amount_inr, note, added_by, at) VALUES (?,?,?,?,?,?)',
                          (uid, budget.this_month(), val, note, current_user()['username'], db.now()))
        s = budget.status(con(), u)
        left = budget.plan(con())['reserve_inr']
        flash(f'Added {budget.inr(val)} extra AI budget for "{u["username"]}" this month (from the reserve'
              + (f', {budget.inr(left)} left' if left is not None else '') + '). '
              + (f'New limit {budget.inr(s["limit_inr"])}, resets {s["resets_on"]}.' if s['limit_inr'] is not None
                 else 'They have no limit, so the extra only shows in the report.'), 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:uid>/ai-extra/<int:tid>/delete', methods=['POST'])
@admin_required
def user_ai_extra_delete(uid, tid):
    with con():
        con().execute('DELETE FROM ai_topups WHERE id=? AND user_id=?', (tid, uid))
    flash('Extra budget removed.', 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:uid>/ai-limit', methods=['POST'])
@admin_required
def user_ai_limit(uid):
    u = con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() or abort(404)
    mode = request.form.get('mode')   # 'default' = follow the default limit, 'custom' = use the amount
    val, err = _inr_or_none(request.form.get('monthly_inr'))
    if mode == 'custom' and err:
        flash(err, 'danger')
        return redirect(url_for('users'))
    with con():
        if mode == 'default':
            con().execute('DELETE FROM ai_limits WHERE user_id=?', (uid,))
        else:
            con().execute('INSERT INTO ai_limits(user_id, monthly_inr, updated_by, updated_at) VALUES (?,?,?,?) '
                          'ON DUPLICATE KEY UPDATE monthly_inr=VALUES(monthly_inr), updated_by=VALUES(updated_by), '
                          'updated_at=VALUES(updated_at)', (uid, val, current_user()['username'], db.now()))
    shown = 'the default limit' if mode == 'default' else ('no limit' if val is None else f'₹{val:,.2f} per month')
    flash(f'AI limit for "{u["username"]}" set to {shown}.', 'success')
    return redirect(url_for('users'))


@app.route('/users/new', methods=['POST'])
@admin_required
def user_new():
    f = request.form
    username, name = f.get('username', '').strip(), f.get('name', '').strip()[:60]
    role = 'admin' if f.get('role') == 'admin' else 'member'
    problem = (None if USERNAME_RE.match(username) else
               'Username: 3-30 letters, numbers, dot, dash or underscore.') or \
              (None if name else 'Full name is required.') or \
              _password_problem(f.get('password', ''), f.get('confirm', ''))
    if not problem and con().execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
        problem = f'Username "{username}" is already taken.'
    if problem:
        flash(problem, 'danger')
    else:
        with con():
            con().execute('INSERT INTO users(username, name, pw_hash, role, created_at) VALUES (?,?,?,?,?)',
                          (username, name, generate_password_hash(f['password']), role, db.now()))
        flash(f'Account "{username}" created. Share the password with them privately.', 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:uid>/password', methods=['POST'])
@admin_required
def user_reset_password(uid):
    u = con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() or abort(404)
    problem = _password_problem(request.form.get('password', ''), request.form.get('confirm', ''))
    if problem:
        flash(problem, 'danger')
    else:
        with con():
            con().execute('UPDATE users SET pw_hash=? WHERE id=?', (generate_password_hash(request.form['password']), uid))
        flash(f'Password reset for "{u["username"]}".', 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:uid>/role', methods=['POST'])
@admin_required
def user_role(uid):
    u = con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() or abort(404)
    if u['id'] == current_user()['id']:
        flash('You cannot change your own role.', 'danger')
    else:
        new = 'member' if u['role'] == 'admin' else 'admin'
        with con():
            con().execute('UPDATE users SET role=? WHERE id=?', (new, uid))
        flash(f'"{u["username"]}" is now {new}.', 'success')
    return redirect(url_for('users'))


@app.route('/users/<int:uid>/delete', methods=['POST'])
@admin_required
def user_delete(uid):
    u = con().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone() or abort(404)
    if u['id'] == current_user()['id']:
        flash('You cannot remove your own account.', 'danger')
    else:
        with con():
            con().execute('DELETE FROM users WHERE id=?', (uid,))
        flash(f'Account "{u["username"]}" removed. Their past runs and test cases stay.', 'success')
    return redirect(url_for('users'))


# ---------------------------------------------------------------- home

@app.route('/')
@login_required
def home():
    c = con()
    counts = c.execute(
        'SELECT (SELECT COUNT(*) FROM projects) AS projects, (SELECT COUNT(*) FROM cases) AS cases, '
        '(SELECT COUNT(*) FROM runs) AS runs, '
        "(SELECT COUNT(*) FROM runs WHERE status='PASSED') AS passed, "
        "(SELECT COUNT(*) FROM runs WHERE status='FAILED') AS failed, "
        "(SELECT COUNT(*) FROM runs WHERE status IN ('PARTIAL','ERROR')) AS other").fetchone()
    recent = c.execute(
        'SELECT r.*, cs.title, cs.tc_no, p.name AS project_name FROM runs r '
        'JOIN cases cs ON cs.id=r.case_id JOIN projects p ON p.id=cs.project_id '
        'ORDER BY r.id DESC LIMIT 10').fetchall()
    # Test cases tile: straight into the project when there is only one, else the project picker
    only = c.execute('SELECT id FROM projects LIMIT 2').fetchall()
    cases_url = url_for('project', pid=only[0]['id']) if len(only) == 1 else url_for('projects')
    ai_daily = None
    if current_user()['role'] == 'admin':   # AI spend is admin-only, like the AI Usage report
        from datetime import date, timedelta
        import pricing
        start = date.today() - timedelta(days=29)
        rows = {r['day']: r for r in c.execute(
            'SELECT SUBSTR(at, 1, 10) AS day, SUM(cost_usd) AS cost, SUM(input_tokens + output_tokens) AS tokens, '
            'SUM(calls) AS calls FROM ai_usage WHERE at >= ? GROUP BY SUBSTR(at, 1, 10)', (start.isoformat(),)).fetchall()}
        days = [(start + timedelta(days=i)).isoformat() for i in range(30)]
        ai_daily = {'inr': pricing.usd_inr(con()), 'days': [
            {'day': d, 'cost': round(float(rows[d]['cost'] or 0), 6) if d in rows else 0,
             'tokens': int(rows[d]['tokens'] or 0) if d in rows else 0,
             'calls': int(rows[d]['calls'] or 0) if d in rows else 0} for d in days]}
    return render_template('home.html', counts=counts, recent=recent, ai_daily=ai_daily, cases_url=cases_url)


RUN_STATUSES = ('PASSED', 'FAILED', 'PARTIAL', 'ERROR', 'running', 'queued')
RUN_GROUPS = {'other': ('PARTIAL', 'ERROR')}   # home "Partial / error" tile


@app.route('/runs')
@login_required
def runs_list():
    status = request.args.get('status', '')
    where, params = '', []
    if status in RUN_STATUSES:
        where, params = 'WHERE r.status=?', [status]
    elif status in RUN_GROUPS:
        where, params = 'WHERE r.status IN (%s)' % ','.join('?' * len(RUN_GROUPS[status])), list(RUN_GROUPS[status])
    else:
        status = ''
    rows = con().execute(
        'SELECT r.*, cs.title, cs.tc_no, p.name AS project_name FROM runs r '
        'JOIN cases cs ON cs.id=r.case_id JOIN projects p ON p.id=cs.project_id '
        f'{where} ORDER BY r.id DESC LIMIT 200', params).fetchall()
    return render_template('runs.html', runs=rows, status=status, statuses=RUN_STATUSES)


# ---------------------------------------------------------------- projects

@app.route('/projects')
@login_required
def projects():
    rows = con().execute(
        'SELECT p.*, (SELECT COUNT(*) FROM cases c WHERE c.project_id=p.id) AS n_cases, '
        '(SELECT COUNT(*) FROM runs r JOIN cases c ON c.id=r.case_id WHERE c.project_id=p.id) AS n_runs, '
        '(SELECT MAX(c.updated_at) FROM cases c WHERE c.project_id=p.id) AS last_update '
        'FROM projects p ORDER BY p.name').fetchall()
    return render_template('projects.html', projects=rows)


@app.route('/projects/new', methods=['POST'])
@login_required
def project_new():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Project name is required.', 'danger')
        return redirect(url_for('projects'))
    try:
        with con():
            pid = con().execute('INSERT INTO projects(name, description, created_by, created_at) VALUES (?,?,?,?)',
                                (name, request.form.get('description', '').strip(), current_user()['username'], db.now())).lastrowid
    except Exception:
        flash(f'A project named "{name}" already exists.', 'danger')
        return redirect(url_for('projects'))
    return redirect(url_for('project', pid=pid))


@app.route('/projects/<int:pid>')
@login_required
def project(pid):
    p = project_or_404(pid)
    tab = 'runs' if request.args.get('tab') == 'runs' else 'cases'
    folder_id = request.args.get('folder', type=int)
    q = request.args.get('q', '').strip()
    # n = cases in folder, ran = cases in folder run at least once (shown as ran/n)
    folders = con().execute(
        'SELECT f.*, COUNT(c.id) AS n, COUNT(DISTINCT r.case_id) AS ran FROM folders f '
        'LEFT JOIN cases c ON c.folder_id=f.id LEFT JOIN runs r ON r.case_id=c.id '
        'WHERE f.project_id=? GROUP BY f.id ORDER BY f.name', (pid,)).fetchall()
    active_folders = [f for f in folders if not f['archived']]
    archived_folders = [f for f in folders if f['archived']]
    current = next((f for f in folders if f['id'] == folder_id), None)
    if folder_id and not current:
        folder_id = None
    # "All Test Cases" leaves out cases inside archived folders
    live = 'c.project_id=? AND (c.folder_id IS NULL OR c.folder_id NOT IN (SELECT id FROM folders WHERE archived=1))'
    total = con().execute(f'SELECT COUNT(*) FROM cases c WHERE {live}', (pid,)).fetchone()[0]
    n_runs = con().execute('SELECT COUNT(*) FROM runs r JOIN cases c ON c.id=r.case_id WHERE c.project_id=?',
                           (pid,)).fetchone()[0]
    cases, runs, dups = [], [], []
    f = {k: request.args.get(k, '').strip() for k in ('sort', 'status', 'tag', 'source', 'by', 'qa')}
    if f['sort'] not in SORTS:
        f['sort'] = 'newest'
    if f['status'] not in STATUS_FILTERS:
        f['status'] = ''
    all_tags, all_authors = [], []
    if tab == 'cases':
        where, params = [live if not folder_id else 'c.project_id=?'], [pid]
        if folder_id:
            where.append('c.folder_id=?')
            params.append(folder_id)
        if q:
            where.append("(c.title LIKE ? OR CONCAT('TC-', c.tc_no) LIKE ?)")
            params += [f'%{q}%', f'%{q}%']
        if f['status'] == 'never':
            where.append('lr.id IS NULL')
        elif f['status']:
            where.append('lr.status=?')
            params.append(f['status'])
        if f['source'] == 'imported':
            where.append('c.source_path IS NOT NULL')
        elif f['source'] == 'web':
            where.append('c.source_path IS NULL')
        if f['by']:
            where.append('c.created_by=?')
            params.append(f['by'])
        if f['qa'] in ('ready', 'draft'):
            where.append('c.qa_status=?')
            params.append(f['qa'])
        rows = con().execute(
            'SELECT c.*, f.name AS folder_name, lr.status AS last_status, lr.started_at AS last_run '
            'FROM cases c LEFT JOIN folders f ON f.id=c.folder_id '
            'LEFT JOIN runs lr ON lr.id=(SELECT MAX(id) FROM runs WHERE case_id=c.id) '
            f'WHERE {" AND ".join(where)} ORDER BY {SORTS[f["sort"]][1]}', params).fetchall()
        tag_map = {r['id']: case_tags(r['content']) for r in rows}
        all_tags = sorted({t for ts in tag_map.values() for t in ts})
        cases = [r for r in rows if not f['tag'] or f['tag'] in tag_map[r['id']]]
        all_authors = [r[0] for r in con().execute(
            'SELECT DISTINCT created_by FROM cases WHERE project_id=? AND created_by IS NOT NULL ORDER BY 1', (pid,))]
        if request.args.get('view') == 'dups':
            dups = find_duplicates(pid)
    else:
        where, params = ['cs.project_id=?'], [pid]
        if folder_id:
            where.append('cs.folder_id=?')
            params.append(folder_id)
        runs = con().execute(
            'SELECT r.*, cs.title, cs.tc_no FROM runs r JOIN cases cs ON cs.id=r.case_id '
            f'WHERE {" AND ".join(where)} ORDER BY r.id DESC LIMIT 200', params).fetchall()
    return render_template('project.html', p=p, tab=tab, folders=active_folders, archived=archived_folders,
                           current=current, cases=cases, runs=runs, folder_id=folder_id, q=q, total=total,
                           n_runs=n_runs, f=f, sorts=SORTS, status_filters=STATUS_FILTERS, all_tags=all_tags,
                           all_authors=all_authors, dups=dups, view=request.args.get('view', ''))


SORTS = {   # key -> (label, ORDER BY); fixed whitelist, never built from the request
    'newest': ('Newest first', 'c.tc_no DESC'),
    'oldest': ('Oldest first', 'c.tc_no ASC'),
    'title': ('Title A-Z', 'c.title ASC'),
    'updated': ('Recently updated', 'c.updated_at DESC'),
    'lastrun': ('Recently run', 'lr.started_at IS NULL, lr.started_at DESC'),
}
STATUS_FILTERS = {'never': 'Never run', 'PASSED': 'Passed', 'FAILED': 'Failed', 'PARTIAL': 'Partial', 'ERROR': 'Error'}


def _norm_steps(content):
    body = re.sub(r'^---.*?\n---\s*\n', '', content or '', flags=re.S)
    return '\n'.join(l.strip().lower() for l in body.splitlines() if l.strip() and not l.startswith('#') and not l.startswith('>'))


def find_duplicates(pid):
    """Groups of cases in the project with the same title or the same steps."""
    rows = con().execute('SELECT c.id, c.tc_no, c.title, c.content, f.name AS folder_name FROM cases c '
                         'LEFT JOIN folders f ON f.id=c.folder_id WHERE c.project_id=? ORDER BY c.tc_no', (pid,)).fetchall()
    groups, seen = [], set()
    for key, reason in ((lambda r: re.sub(r'\W+', ' ', r['title']).strip().lower(), 'Same title'),
                        (lambda r: _norm_steps(r['content']), 'Same steps')):
        buckets = {}
        for r in rows:
            k = key(r)
            if k:
                buckets.setdefault(k, []).append(r)
        for items in buckets.values():
            ids = frozenset(r['id'] for r in items)
            if len(items) > 1 and ids not in seen:
                seen.add(ids)
                groups.append({'reason': reason, 'cases': items})
    return groups


def _import_md(pid, folder_id, text, user):
    text = text.replace('\r\n', '\n').strip() + '\n'
    title = db.case_title(text, '')
    if not title:
        return None
    return db.add_case(con(), pid, folder_id, title, text, user)


@app.route('/projects/<int:pid>/cases/import', methods=['POST'])
@login_required
def cases_import(pid):
    project_or_404(pid)
    folder_id = request.form.get('folder_id', type=int) or None
    if folder_id:
        folder_or_404(pid, folder_id)
    added, skipped = 0, []
    with con():
        for fs in request.files.getlist('files'):
            name = os.path.basename(fs.filename or '')
            if not name.endswith('.md'):
                skipped.append(name or '(no name)')
                continue
            try:
                text = fs.read(512 * 1024).decode('utf-8')
            except UnicodeDecodeError:
                skipped.append(name)
                continue
            if _import_md(pid, folder_id, text, current_user()['username']):
                added += 1
            else:
                skipped.append(name)
    flash(f'Imported {added} test case(s).' + (f' Skipped (not a test case .md): {", ".join(skipped)}' if skipped else ''),
          'success' if added else 'warning')
    return redirect(url_for('project', pid=pid, folder=folder_id))


@app.route('/projects/<int:pid>/cases/generate', methods=['POST'])
@login_required
def cases_generate(pid):
    """Generate with AI: runs generate.js, then imports the cases it wrote into the chosen folder."""
    import budget
    project_or_404(pid)
    task = request.form.get('task', '').strip()
    folder_id = request.form.get('folder_id', type=int) or None
    if folder_id:
        folder_or_404(pid, folder_id)
    back = redirect(url_for('project', pid=pid, folder=folder_id))
    if len(task) < 15:
        flash('Describe the requirement / bug / change in at least one full sentence.', 'danger')
        return back
    b = budget.status(con(), current_user())
    if not b['ok']:
        flash(b['message'], 'danger')
        return back
    ok, msg, _ids = services.generate_cases(pid, folder_id, task, request.form.get('count', type=int) or 6,
                                   request.form.get('repo', '').strip(), current_user()['username'])
    flash(msg, 'success' if ok else 'danger')
    return redirect(url_for('project', pid=pid, folder=folder_id, sort='newest')) if ok else back


@app.route('/projects/<int:pid>/cases/bulk', methods=['POST'])
@login_required
def cases_bulk(pid):
    project_or_404(pid)
    ids = [int(x) for x in request.form.getlist('ids') if x.isdigit()]
    back = request.form.get('back') or url_for('project', pid=pid)
    if not back.startswith('/'):
        back = url_for('project', pid=pid)
    rows = con().execute(f'SELECT * FROM cases WHERE project_id=? AND id IN ({",".join("?" * len(ids))})',
                         [pid, *ids]).fetchall() if ids else []
    if not rows:
        flash('Select at least one test case.', 'warning')
        return redirect(back)
    action = request.form.get('action')
    if action in ('qa_ready', 'qa_draft'):
        st = 'ready' if action == 'qa_ready' else 'draft'
        with con():
            con().executemany('UPDATE cases SET qa_status=?, qa_marked_by=?, qa_marked_at=? WHERE id=?',
                              [(st, current_user()['username'], db.now(), r['id']) for r in rows])
        flash(f'Marked {len(rows)} test case(s) as {"Ready for QA" if st == "ready" else "Draft"}.', 'success')
    elif action == 'move':
        fid = request.form.get('folder_id', type=int) or None
        if fid:
            folder_or_404(pid, fid)
        with con():
            con().executemany('UPDATE cases SET folder_id=? WHERE id=?', [(fid, r['id']) for r in rows])
        flash(f'Moved {len(rows)} test case(s).', 'success')
    elif action == 'run':
        import budget
        b = budget.status(con(), current_user())
        env = envs.get(con(), request.form.get('env_id', type=int))
        started = 0
        for r in rows:
            if con().execute("SELECT 1 FROM runs WHERE case_id=? AND status IN ('running', 'queued')", (r['id'],)).fetchone():
                continue
            with con():
                rid = con().execute('INSERT INTO runs(case_id, status, started_at, run_by, env_name) VALUES (?,?,?,?,?)',
                                    (r['id'], 'running', db.now(), current_user()['username'], env['name'] if env else None)).lastrowid
            threading.Thread(target=_run_case, args=(rid, r['content'], not b['ok'], case_base_dir(r), env['id'] if env else None),
                             daemon=True).start()
            started += 1
        flash(f'Queued {started} run(s) on {env["name"] if env else "the default environment"}. They run one after another on this PC.'
              + (' ' + b['message'] + ' AI steps will be skipped.' if not b['ok'] else ''), 'warning' if not b['ok'] else 'info')
    else:
        flash('Unknown action.', 'danger')
    return redirect(back)


def folder_or_404(pid, fid):
    f = con().execute('SELECT * FROM folders WHERE id=? AND project_id=?', (fid, pid)).fetchone()
    return f or abort(404)


@app.route('/projects/<int:pid>/folders/new', methods=['POST'])
@login_required
def folder_new(pid):
    project_or_404(pid)
    name = request.form.get('name', '').strip()[:80]
    if name:
        with con():
            fid = db.get_or_create_folder(con(), pid, name)
        return redirect(url_for('project', pid=pid, folder=fid))
    return redirect(url_for('project', pid=pid))


@app.route('/projects/<int:pid>/folders/<int:fid>/rename', methods=['POST'])
@login_required
def folder_rename(pid, fid):
    folder_or_404(pid, fid)
    name = request.form.get('name', '').strip()[:80]
    if not name:
        flash('Folder name is required.', 'danger')
    elif con().execute('SELECT 1 FROM folders WHERE project_id=? AND name=? AND id<>?', (pid, name, fid)).fetchone():
        flash(f'A folder named "{name}" already exists.', 'danger')
    else:
        with con():
            con().execute('UPDATE folders SET name=? WHERE id=?', (name, fid))
    return redirect(url_for('project', pid=pid, folder=fid))


@app.route('/projects/<int:pid>/folders/<int:fid>/archive', methods=['POST'])
@login_required
def folder_archive(pid, fid):
    f = folder_or_404(pid, fid)
    archive = 0 if f['archived'] else 1
    with con():
        con().execute('UPDATE folders SET archived=? WHERE id=?', (archive, fid))
    flash(f'Folder "{f["name"]}" {"archived" if archive else "restored"}.', 'success')
    return redirect(url_for('project', pid=pid, folder=None if archive else fid))


# ---------------------------------------------------------------- test cases

TEMPLATE_CASE = """---
mode: testing
max_steps: 25
timeout: 180
tags: []
variables: {}
---

# <Test case title>

## Sign in to the admin panel
Navigate to {{base_url}}.
Type {{username}} into the User Name field.
Type {{password}} into the Password field.
Select '{{branch}}' from the Select Branch dropdown.
Click the Sign In button.
Assert the page title contains 'Dashboard'.

## <What this test does>
"""


@app.route('/projects/<int:pid>/cases/new', methods=['GET', 'POST'])
@login_required
def case_new(pid):
    p = project_or_404(pid)
    folders = con().execute('SELECT * FROM folders WHERE project_id=? ORDER BY name', (pid,)).fetchall()
    if request.method == 'POST':
        content = request.form.get('content', '').replace('\r\n', '\n').strip() + '\n'
        title = db.case_title(content, '')
        if not title or title.startswith('<'):
            flash('Give the test a title: a line starting with "# ".', 'danger')
            return render_template('case_edit.html', p=p, folders=folders, content=content, case=None,
                                   folder_id=request.form.get('folder_id', type=int))
        with con():
            cid = db.add_case(con(), pid, request.form.get('folder_id', type=int) or None, title, content,
                              current_user()['username'])
        return redirect(url_for('case', cid=cid))
    return render_template('case_edit.html', p=p, folders=folders, content=TEMPLATE_CASE, case=None,
                           folder_id=request.args.get('folder', type=int))


@app.route('/cases/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def case_edit(cid):
    c = case_or_404(cid)
    p = project_or_404(c['project_id'])
    folders = con().execute('SELECT * FROM folders WHERE project_id=? ORDER BY name', (p['id'],)).fetchall()
    if request.method == 'POST':
        content = request.form.get('content', '').replace('\r\n', '\n').strip() + '\n'
        title = db.case_title(content, '')
        if not title:
            flash('Give the test a title: a line starting with "# ".', 'danger')
            return render_template('case_edit.html', p=p, folders=folders, content=content, case=c, folder_id=c['folder_id'])
        with con():
            con().execute('UPDATE cases SET title=?, content=?, folder_id=?, updated_by=?, updated_at=? WHERE id=?',
                          (title, content, request.form.get('folder_id', type=int) or None,
                           current_user()['username'], db.now(), cid))
        return redirect(url_for('case', cid=cid))
    return render_template('case_edit.html', p=p, folders=folders, content=c['content'], case=c, folder_id=c['folder_id'])


def run_view_data(run):
    """Steps + artifact names for one run, safe for templates."""
    if not run or not run['run_dir'] or not os.path.isdir(run['run_dir']):
        return [], False
    _, steps = db.read_run_events(run['run_dir'])
    has_video = os.path.isfile(os.path.join(run['run_dir'], 'video.webm'))
    return steps, has_video


_STATUS_RANK = {'failed': 3, 'needs-ai': 2, 'skipped': 1, 'passed': 0}


def group_steps(steps):
    """Consecutive steps with the same section -> [{title, steps, status, passed}] for the summary cards."""
    groups = []
    for s in steps:
        title = s.get('section') or 'Steps'
        if not groups or groups[-1]['title'] != title:
            groups.append({'title': title, 'steps': []})
        groups[-1]['steps'].append(s)
    for g in groups:
        g['status'] = max((st.get('status', 'skipped') for st in g['steps']), key=lambda x: _STATUS_RANK.get(x, 1))
        g['passed'] = sum(1 for st in g['steps'] if st.get('status') == 'passed')
    return groups


def case_tags(content):
    """tags: [login, smoke] from the _test.md front matter."""
    m = re.search(r'^---\s*\n(.*?)\n---', content or '', re.S)
    t = re.search(r'^tags:\s*\[(.*?)\]', m.group(1), re.M) if m else None
    return [x.strip().strip('\'"') for x in t.group(1).split(',') if x.strip()] if t else []


def run_has(run, name):
    """True when a finished run folder holds this artifact (e.g. poster.png from newer runs)."""
    return bool(run and run['run_dir'] and SAFE_FILE.match(name) and os.path.isfile(os.path.join(run['run_dir'], name)))


app.jinja_env.globals.update(group_steps=group_steps, case_tags=case_tags, run_has=run_has)


@app.route('/cases/<int:cid>')
@login_required
def case(cid):
    c = case_or_404(cid)
    runs = con().execute('SELECT * FROM runs WHERE case_id=? ORDER BY id DESC', (cid,)).fetchall()
    run_id = request.args.get('run', type=int)
    run = next((r for r in runs if r['id'] == run_id), runs[0] if runs else None)
    steps, has_video = run_view_data(run)
    tab = request.args.get('tab', 'summary')
    if tab not in ('summary', 'testcase', 'code', 'runs'):
        tab = 'summary'
    gen, gen_error = generate_code(c)   # cached; also drives the Verified/Unverified pill
    any_running = any(r['status'] in ('running', 'queued') for r in runs)
    cstate = code_state(c, gen)
    return render_template('case.html', c=c, runs=runs, run=run, steps=steps, has_video=has_video,
                           tab=tab, any_running=any_running, public=False, gen=gen, gen_error=gen_error,
                           cstate=cstate, code_open=bool(request.args.get('item') or request.args.get('file')),
                           file=request.args.get('file') if request.args.get('file') in CODE_FILES else 'test.py')


_CODE_CACHE = {}   # (case id, updated_at) -> generated result; a case edit changes the key


def generate_code(c):
    """Runs codegen.js (same step parser as the runner) -> ({title, steps, code}, error)."""
    key = (c['id'], c['updated_at'], hash(c['content']))
    if key in _CODE_CACHE:
        return _CODE_CACHE[key], None
    try:
        proc = subprocess.run(['node', os.path.join(db.RUNNER_DIR, 'codegen.js'), '--id', f"TC-{c['tc_no']}",
                               '--vars', db.VARS_FILE] + (['--base', case_base_dir(c)] if case_base_dir(c) else []),
                              input=c['content'], cwd=db.RUNNER_DIR, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=30)
        if proc.returncode != 0:
            return None, 'Code generator failed on this test case.'
        result = json.loads(proc.stdout)
    except FileNotFoundError:
        return None, 'Node.js not found on this PC.'
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, 'Code generator did not respond.'
    result['hash'] = hashlib.sha256(result['files']['test.py'].encode('utf-8')).hexdigest()
    if len(_CODE_CACHE) > 500:
        _CODE_CACHE.clear()
    _CODE_CACHE[key] = result
    return result, None


def code_state(c, gen):
    """'running' | 'verified' | 'failed' | 'unverified' for the code as it is now."""
    if not gen or c['code_hash'] != gen['hash']:
        return 'unverified'
    return {'running': 'running', 'PASSED': 'verified', 'FAILED': 'failed'}.get(c['code_status'], 'unverified')


CODE_FILES = {'test.py': 'text/x-python', 'requirements.txt': 'text/plain', '.env.example': 'text/plain'}


@app.route('/cases/<int:cid>/code/<path:name>')
@login_required
def case_code_file(cid, name):
    if name not in CODE_FILES:
        abort(404)
    gen, err = generate_code(case_or_404(cid))
    if err:
        abort(500, err)
    return app.response_class(gen['files'][name], mimetype=CODE_FILES[name],
                              headers={'Content-Disposition': f'attachment; filename={name}'})


def _verify_code(cid, files, code_hash, used_vars):
    """Background worker: runs the generated test.py headless with values from the variables file."""
    status, msg = 'FAILED', ''
    with RUN_LOCK, tempfile.TemporaryDirectory(dir=db.DATA) as work:
        try:
            values, secrets_ = db.load_vars()
        except (OSError, ValueError):
            values, secrets_ = {}, []
            msg = 'Variables file could not be read'
        if not msg:
            for name in ('test.py', 'requirements.txt'):
                with open(os.path.join(work, name), 'w', encoding='utf-8') as f:
                    f.write(files[name])
            env = {k: v for k, v in os.environ.items() if not k.startswith('LTH_')}
            env.update({'LTH_' + re.sub(r'\W', '_', k).upper(): values[k] for k in used_vars if k in values})
            env['HEADLESS'] = 'true'
            env['PYTHONIOENCODING'] = 'utf-8'
            try:
                proc = subprocess.run([sys.executable, 'test.py'], cwd=work, env=env, capture_output=True,
                                      text=True, encoding='utf-8', errors='replace', timeout=600)
                out = (proc.stdout + '\n' + proc.stderr).strip().splitlines()
                status = 'PASSED' if proc.returncode == 0 else 'FAILED'
                msg = next((l for l in reversed(out) if l.startswith(('PASSED', 'FAILED', 'Missing'))), out[-1] if out else '')
            except subprocess.TimeoutExpired:
                msg = 'Sample execution took longer than 10 minutes and was stopped'
        for s in secrets_:
            msg = msg.replace(s, '******')
    with db.connect() as c:
        c.execute('UPDATE cases SET code_status=?, code_msg=?, code_verified_at=? WHERE id=? AND code_hash=?',
                  (status, msg[:500], db.now(), cid, code_hash))


@app.route('/cases/<int:cid>/verify', methods=['POST'])
@login_required
def case_verify(cid):
    c = case_or_404(cid)
    gen, err = generate_code(c)
    if err:
        flash(err, 'danger')
        return redirect(url_for('case', cid=cid, tab='code', item=1))
    if code_state(c, gen) == 'running':
        flash('Sample execution is already running.', 'warning')
        return redirect(url_for('case', cid=cid, tab='code', item=1))
    with con():
        con().execute("UPDATE cases SET code_hash=?, code_status='running', code_msg=NULL, code_verified_by=? WHERE id=?",
                      (gen['hash'], current_user()['username'], cid))
    threading.Thread(target=_verify_code, args=(cid, gen['files'], gen['hash'], gen['vars']), daemon=True).start()
    flash('Sample execution started. This page refreshes until it finishes.', 'info')
    return redirect(url_for('case', cid=cid, tab='code', item=1))


@app.route('/cases/<int:cid>/title', methods=['POST'])
@login_required
def case_title_update(cid):
    """Inline rename from the summary header: rewrites the '# Title' line of the test case."""
    c = case_or_404(cid)
    title = ' '.join(request.form.get('title', '').split())[:200]
    if not title:
        return {'ok': False, 'error': 'Title cannot be empty.'}, 400
    if title == c['title']:
        return {'ok': True, 'title': title}
    content, n = re.subn(r'(?m)^# (?!#).*$', lambda _: '# ' + title, c['content'], count=1)
    if not n:   # no title line yet: put one after the front matter
        m = re.match(r'---\s*\n.*?\n---\s*\n', c['content'], re.S)
        at = m.end() if m else 0
        content = c['content'][:at] + f'\n# {title}\n' + c['content'][at:]
    with con():
        con().execute('UPDATE cases SET title=?, content=?, updated_by=?, updated_at=? WHERE id=?',
                      (title, content, current_user()['username'], db.now(), cid))
    return {'ok': True, 'title': title}


def case_base_dir(c):
    """Folder of the case's original _test.md, so '@import ../helpers/login.md' resolves like it does there.
    None for cases made or uploaded in the web app: the runner then uses the shared helpers folder."""
    d = os.path.dirname(c['source_path']) if c['source_path'] else None
    return d if d and os.path.isdir(d) else None


def _run_case(run_id, content, no_ai=False, base_dir=None, env_id=None):
    """Background worker: writes the case to a temp file and runs run.js on it."""
    with RUN_LOCK:
        fd, path = tempfile.mkstemp(suffix='_test.md', dir=db.DATA)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        run_dir, err, vars_path = None, None, None
        try:
            with db.connect() as c:
                env = envs.get(c, env_id)
            vars_path = envs.vars_file(env) if env else None
            cmd = (['node', os.path.join(db.RUNNER_DIR, 'run.js'), path] + (['--no-ai'] if no_ai else [])
                   + (['--base', base_dir] if base_dir else []) + (['--vars', vars_path] if vars_path else []))
            proc = subprocess.run(cmd,
                                  cwd=db.RUNNER_DIR, capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=1800)
            m = re.search(r'REPORT : (.+)', proc.stdout)
            if m:
                run_dir = os.path.dirname(m.group(1).strip())
            else:
                err = (proc.stderr or proc.stdout or 'Runner failed').strip().splitlines()[-1][:300]
        except subprocess.TimeoutExpired:
            err = 'Run took longer than 30 minutes and was stopped'
        except FileNotFoundError:
            err = 'Node.js not found on this PC'
        finally:
            os.remove(path)
            if vars_path and os.path.exists(vars_path):
                os.remove(vars_path)   # holds the environment password
        with db.connect() as c:
            if run_dir:
                db.record_finished_run(c, run_id, run_dir)
            else:
                c.execute("UPDATE runs SET status='ERROR', error=? WHERE id=?", (err, run_id))


@app.route('/cases/<int:cid>/run', methods=['POST'])
@login_required
def case_run(cid):
    c = case_or_404(cid)
    if con().execute("SELECT 1 FROM runs WHERE case_id=? AND status IN ('running', 'queued')", (cid,)).fetchone():
        flash('This test is already running.', 'warning')
        return redirect(url_for('case', cid=cid))
    import budget
    b = budget.status(con(), current_user())
    env = envs.get(con(), request.form.get('env_id', type=int))
    with con():
        run_id = con().execute('INSERT INTO runs(case_id, status, started_at, run_by, env_name) VALUES (?,?,?,?,?)',
                               (cid, 'running', db.now(), current_user()['username'], env['name'] if env else None)).lastrowid
    threading.Thread(target=_run_case, args=(run_id, c['content'], not b['ok'], case_base_dir(c), env['id'] if env else None),
                     daemon=True).start()
    if not b['ok']:
        flash(b['message'] + ' This run skips AI steps; the other steps still run.', 'warning')
    else:
        flash('Run started. This page refreshes until it finishes.' + (' ' + b['message'] if b['warn'] else ''), 'info')
    return redirect(url_for('case', cid=cid, run=run_id))


# ---------------------------------------------------------------- run artifacts + sharing

def _send_artifact(run, name):
    if not run or not run['run_dir'] or not SAFE_FILE.match(name):
        abort(404)
    return send_from_directory(run['run_dir'], name, conditional=True)


@app.route('/runs/<int:rid>/file/<name>')
@login_required
def run_file(rid, name):
    return _send_artifact(con().execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone(), name)


@app.route('/runs/<int:rid>/share', methods=['POST'])
@login_required
def run_share(rid):
    run = con().execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone() or abort(404)
    if not run['share_token']:
        with con():
            con().execute('UPDATE runs SET share_token=? WHERE id=?', (secrets.token_urlsafe(18), rid))
    return redirect(url_for('case', cid=run['case_id'], run=rid, share=1))   # reopens the share popup


SHARE_DAYS = (7, 15, 30)


@app.route('/runs/<int:rid>/share-link', methods=['POST'])
@login_required
def run_share_link(rid):
    """Share popup 'Copy Link': creates (or reuses) the run's link and sets its expiry. Returns JSON."""
    from datetime import datetime, timedelta
    run = con().execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone()
    if not run:
        return {'ok': False, 'error': 'Run not found.'}, 404
    if run['status'] in ('running', 'queued'):
        return {'ok': False, 'error': 'Wait for the run to finish before sharing it.'}, 400
    days = request.form.get('days', type=int)
    if days not in SHARE_DAYS:
        return {'ok': False, 'error': 'Choose 7, 15 or 30 days.'}, 400
    token = run['share_token'] or secrets.token_urlsafe(18)
    expires = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    with con():
        con().execute('UPDATE runs SET share_token=?, share_expires=? WHERE id=?', (token, expires, rid))
    return {'ok': True, 'url': url_for('shared', token=token, _external=True), 'expires': expires[:16], 'days': days}


@app.route('/runs/<int:rid>/unshare', methods=['POST'])
@login_required
def run_unshare(rid):
    run = con().execute('SELECT * FROM runs WHERE id=?', (rid,)).fetchone() or abort(404)
    with con():
        con().execute('UPDATE runs SET share_token=NULL, share_expires=NULL WHERE id=?', (rid,))
    flash('Share link removed.', 'success')
    return redirect(url_for('case', cid=run['case_id'], run=rid))


def _shared_run(token):
    run = con().execute('SELECT * FROM runs WHERE share_token=?', (token,)).fetchone()
    if not run:
        abort(404)
    if run['share_expires'] and run['share_expires'] < db.now():
        abort(410, 'This share link has expired. Ask the sender for a new link.')
    return run


@app.route('/s/<token>')
def shared(token):
    run = _shared_run(token)
    c = case_or_404(run['case_id'])
    steps, has_video = run_view_data(run)
    return render_template('case.html', c=c, runs=[run], run=run, steps=steps, has_video=has_video,
                           tab='summary', any_running=False, public=True, token=token)


@app.route('/s/<token>/file/<name>')
def shared_file(token, name):
    return _send_artifact(_shared_run(token), name)


# ---------------------------------------------------------------- environments (admins manage, everyone picks)

def _env_form(existing=None):
    f = request.form
    name, url = f.get('name', '').strip(), envs.normalise_url(f.get('base_url'))
    if not envs.NAME_RE.match(name):
        return None, 'Name: 2-40 letters, numbers, space, dot, dash or underscore (e.g. QA, Dev, Local).'
    if not url:
        return None, 'Base URL must start with http:// or https:// (e.g. https://qa.example.com/admin/).'
    clash = con().execute('SELECT id FROM environments WHERE name=?', (name,)).fetchone()
    if clash and (not existing or clash['id'] != existing['id']):
        return None, f'An environment named "{name}" already exists.'
    pw = f.get('password', '')
    pw_enc = (existing['password_enc'] if existing else None) if pw == '' else envs.encrypt(pw)
    if f.get('clear_password'):
        pw_enc = None
    return {'name': name, 'base_url': url, 'username': f.get('username', '').strip()[:120] or None,
            'password_enc': pw_enc, 'branch': f.get('branch', '').strip()[:120] or None,
            'note': f.get('note', '').strip()[:255] or None}, None


@app.route('/admin/environments', methods=['GET', 'POST'])
@admin_required
def environments():
    if request.method == 'POST':
        v, err = _env_form()
        if err:
            flash(err, 'danger')
        else:
            with con():
                first = not con().execute('SELECT COUNT(*) AS n FROM environments').fetchone()['n']
                con().execute('INSERT INTO environments(name, base_url, username, password_enc, branch, note, is_default, '
                              'created_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                              (v['name'], v['base_url'], v['username'], v['password_enc'], v['branch'], v['note'],
                               1 if first else 0, current_user()['username'], db.now()))
            flash(f'Environment "{v["name"]}" added.', 'success')
        return redirect(url_for('environments'))
    return render_template('environments.html', envs=envs.all_envs(con()))


@app.route('/admin/environments/<int:eid>', methods=['POST'])
@admin_required
def environment_update(eid):
    e = con().execute('SELECT * FROM environments WHERE id=?', (eid,)).fetchone() or abort(404)
    action = request.form.get('action')
    with con():
        if action == 'default':
            con().execute('UPDATE environments SET is_default=(id=?)', (eid,))
            flash(f'"{e["name"]}" is now the default environment.', 'success')
        elif action == 'delete':
            if e['is_default']:
                flash('Make another environment the default before deleting this one.', 'danger')
            else:
                con().execute('DELETE FROM environments WHERE id=?', (eid,))
                flash(f'Environment "{e["name"]}" deleted. Past runs keep its name.', 'success')
        else:
            v, err = _env_form(e)
            if err:
                flash(err, 'danger')
            else:
                con().execute('UPDATE environments SET name=?, base_url=?, username=?, password_enc=?, branch=?, note=?, '
                              'updated_at=? WHERE id=?', (v['name'], v['base_url'], v['username'], v['password_enc'],
                                                          v['branch'], v['note'], db.now(), eid))
                flash(f'Environment "{v["name"]}" saved.', 'success')
    return redirect(url_for('environments'))


@app.route('/cases/<int:cid>/qa', methods=['POST'])
@login_required
def case_qa(cid):
    case_or_404(cid)
    st = 'ready' if request.form.get('status') == 'ready' else 'draft'
    with con():
        con().execute('UPDATE cases SET qa_status=?, qa_marked_by=?, qa_marked_at=? WHERE id=?',
                      (st, current_user()['username'], db.now(), cid))
    flash('Marked Ready for QA: scheduled QA runs will include it.' if st == 'ready' else 'Moved back to Draft.', 'success')
    return redirect(request.referrer if (request.referrer or '').startswith(request.host_url) else url_for('case', cid=cid))


# ---------------------------------------------------------------- AI budget (admins only)

@app.route('/admin/ai-budget', methods=['POST'])
@admin_required
def ai_budget_save():
    import budget
    vals = {}
    for field, name in (('user_default', budget.S_USER_DEFAULT), ('org', budget.S_ORG)):
        v, err = _inr_or_none(request.form.get(field))
        if err:
            flash(err, 'danger')
            return redirect(url_for('ai_usage') + '#budget')
        vals[name] = '' if v is None else f'{v:g}'
    org = float(vals[budget.S_ORG]) if vals[budget.S_ORG] else None
    default = float(vals[budget.S_USER_DEFAULT]) if vals[budget.S_USER_DEFAULT] else None
    pl = budget.plan(con())
    if org is not None and pl['balance_inr'] is not None:
        can_promise = pl['balance_inr'] + pl['org_used_inr'] - pl['extras_inr']   # this month's spend is already out of the balance
        if org > can_promise + 0.005:
            flash(f'Organisation limit {budget.inr(org)} is more than the credits can cover this month '
                  f'({budget.inr(max(0, can_promise))}). Add credits or lower the limit.', 'danger')
            return redirect(url_for('ai_usage') + '#budget')
    if org is not None and default is not None and default > org:
        flash('Default per user cannot be more than the whole organisation limit.', 'danger')
        return redirect(url_for('ai_usage') + '#budget')
    with con():
        for name, v in vals.items():
            budget.set_setting(con(), name, v)
    pl = budget.plan(con())
    flash('AI limits saved. They apply to the next AI call.'
          + (f' Reserve kept aside: {budget.inr(pl["reserve_inr"])}.' if pl['reserve_inr'] is not None else ''), 'success')
    return redirect(url_for('ai_usage') + '#budget')


def _rate_or_error(raw):
    raw = (raw or '').replace(',', '').strip()
    try:
        v = round(float(raw), 4)
    except ValueError:
        return None, 'Enter what 1 US dollar cost you in rupees (e.g. 87.25), from your card or bank statement.'
    if not 40 <= v <= 250:
        return None, 'The ₹ per $ rate looks wrong. Enter the rupees you paid for 1 US dollar (e.g. 87.25).'
    return v, None


@app.route('/admin/ai-credits', methods=['POST'])
@admin_required
def ai_credit_add():
    raw = request.form.get('amount_usd', '').replace(',', '').strip()
    try:
        amount = round(float(raw), 2)
    except ValueError:
        amount = 0
    when = request.form.get('date', '')
    rate, rate_err = _rate_or_error(request.form.get('rate_inr'))
    if not (0 < amount <= 100000):
        flash('Enter the credit amount in US dollars, as shown in the Anthropic Console (e.g. 20).', 'danger')
    elif rate_err:
        flash(rate_err, 'danger')
    elif not DATE_RE.match(when):
        flash('Pick the date the credits were bought.', 'danger')
    else:
        with con():
            con().execute('INSERT INTO ai_credits(at, amount_usd, rate_inr, note, added_by) VALUES (?,?,?,?,?)',
                          (when + ' 00:00:00', amount, rate, request.form.get('note', '').strip()[:255], current_user()['username']))
        flash(f'Recorded ${amount:,.2f} of Anthropic credits at ₹{rate:g} per $ (₹{amount * rate:,.2f}). '
              f'All ₹ figures now use ₹{pricing.usd_inr(con()):.2f} per $.', 'success')
    return redirect(url_for('ai_usage') + '#budget')


@app.route('/admin/ai-credits/<int:cid>/rate', methods=['POST'])
@admin_required
def ai_credit_rate(cid):
    """Set the ₹ per $ of an older credit entry that was saved without one."""
    rate, err = _rate_or_error(request.form.get('rate_inr'))
    if err:
        flash(err, 'danger')
    else:
        with con():
            con().execute('UPDATE ai_credits SET rate_inr=? WHERE id=?', (rate, cid))
        flash(f'Rate saved. All ₹ figures now use ₹{pricing.usd_inr(con()):.2f} per $.', 'success')
    return redirect(url_for('ai_usage') + '#budget')


@app.route('/admin/ai-credits/<int:cid>/delete', methods=['POST'])
@admin_required
def ai_credit_delete(cid):
    with con():
        con().execute('DELETE FROM ai_credits WHERE id=?', (cid,))
    flash('Credit entry removed.', 'success')
    return redirect(url_for('ai_usage') + '#budget')


# ---------------------------------------------------------------- AI usage report (admins only)

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


@app.route('/admin/ai-usage')
@admin_required
def ai_usage():
    import csv
    import io
    from datetime import date, timedelta
    import pricing
    a = request.args
    d_from = a.get('from', '') if DATE_RE.match(a.get('from', '')) else (date.today() - timedelta(days=29)).isoformat()
    d_to = a.get('to', '') if DATE_RE.match(a.get('to', '')) else date.today().isoformat()
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    where, params = ['u.at >= ?', 'u.at < ?'], [d_from, (date.fromisoformat(d_to) + timedelta(days=1)).isoformat()]
    pid = a.get('project', type=int)
    if pid:
        where.append('u.project_id=?')
        params.append(pid)
    user = a.get('user', '').strip()
    if user:
        where.append('u.username=?')
        params.append(user)
    kind = a.get('kind', '')
    if kind in ('run', 'generate', 'cli-step'):
        where.append('u.kind=?')
        params.append(kind)
    w = ' AND '.join(where)
    agg = ('SUM(u.calls) AS calls, SUM(u.input_tokens) AS tin, SUM(u.output_tokens) AS tout, '
           'SUM(u.cache_read_tokens + u.cache_write_tokens) AS tcache, SUM(u.cost_usd) AS cost')
    c = con()
    totals = c.execute(f'SELECT COUNT(*) AS events, {agg}, COUNT(DISTINCT u.case_id) AS cases FROM ai_usage u WHERE {w}', params).fetchone()
    by_case = c.execute(
        f'SELECT u.case_id, MAX(cs.tc_no) AS tc_no, MAX(cs.title) AS title, MAX(p.name) AS project, COUNT(DISTINCT u.run_id) AS runs, '
        f"SUM(u.kind='generate') AS gens, MAX(u.at) AS last_at, {agg} FROM ai_usage u "
        f'LEFT JOIN cases cs ON cs.id=u.case_id LEFT JOIN projects p ON p.id=u.project_id '
        f'WHERE {w} GROUP BY u.case_id ORDER BY cost DESC LIMIT 500', params).fetchall()
    if a.get('format') == 'csv':
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(['Test case', 'Title', 'Project', 'Runs with AI', 'Generated', 'AI calls', 'Input tokens',
                     'Output tokens', 'Cache tokens', 'Cost USD', f'Cost INR (@{pricing.usd_inr(con()):g})', 'Last used'])
        for r in by_case:
            wr.writerow([f"TC-{r['tc_no']}" if r['tc_no'] else '(deleted)', r['title'] or '', r['project'] or '', r['runs'],
                         r['gens'], r['calls'], r['tin'], r['tout'], r['tcache'], f"{r['cost'] or 0:.4f}",
                         f"{(r['cost'] or 0) * pricing.usd_inr(con()):.2f}", r['last_at']])
        return app.response_class(buf.getvalue(), mimetype='text/csv',
                                  headers={'Content-Disposition': f'attachment; filename=ai-usage-{d_from}-to-{d_to}.csv'})
    by_user = c.execute(f'SELECT u.username, {agg} FROM ai_usage u WHERE {w} GROUP BY u.username ORDER BY cost DESC', params).fetchall()
    by_day = c.execute(f'SELECT substr(u.at, 1, 10) AS day, {agg} FROM ai_usage u WHERE {w} GROUP BY day ORDER BY day', params).fetchall()
    recent = c.execute(
        f'SELECT u.*, cs.tc_no, cs.title FROM ai_usage u LEFT JOIN cases cs ON cs.id=u.case_id '
        f'WHERE {w} ORDER BY u.id DESC LIMIT 50', params).fetchall()
    projects = c.execute('SELECT id, name FROM projects ORDER BY name').fetchall()
    users = [r[0] for r in c.execute('SELECT DISTINCT username FROM ai_usage WHERE username IS NOT NULL ORDER BY 1')]
    import budget
    bud = {'plan': budget.plan(c), 'credits': budget.credits(c), 'user_default': budget.get_setting(c, budget.S_USER_DEFAULT) or '',
           'org': budget.get_setting(c, budget.S_ORG) or '', 'org_used_inr': budget._spent_inr(c),
           'month': budget.month_bounds(), 'today': date.today().isoformat()}
    return render_template('ai_usage.html', bud=bud, totals=totals, by_case=by_case, by_user=by_user, by_day=by_day,
                           recent=recent, projects=projects, users=users, d_from=d_from, d_to=d_to, pid=pid,
                           user=user, kind=kind, inr=pricing.usd_inr(con()), prices=pricing.PRICES)


def money_filter(usd, inr_rate=None):
    usd = usd or 0
    return f'${usd:,.4f}' if usd < 1 else f'${usd:,.2f}'


def tokens_filter(n):
    n = int(n or 0)
    return f'{n / 1_000_000:.2f}M' if n >= 1_000_000 else (f'{n / 1000:.1f}K' if n >= 1000 else str(n))


def inr_filter(usd, rate):
    """USD -> '₹1,23,456.78' (Indian digit grouping)."""
    v = f'{(usd or 0) * rate:.2f}'
    whole, frac = v.split('.')
    neg = whole.startswith('-')
    whole = whole.lstrip('-')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ','.join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = f'{head},{tail}'
    return f"{'-' if neg else ''}₹{whole}.{frac}"


app.jinja_env.filters.update(usd=money_filter, tok=tokens_filter, inr=inr_filter)


def case_ai_cost(cid):
    r = con().execute('SELECT COALESCE(SUM(calls),0) AS calls, COALESCE(SUM(input_tokens),0) AS tin, '
                      'COALESCE(SUM(output_tokens),0) AS tout, COALESCE(SUM(cost_usd),0) AS cost FROM ai_usage WHERE case_id=?',
                      (cid,)).fetchone()
    return dict(r)


app.jinja_env.globals['case_ai_cost'] = case_ai_cost


from api import bp as api_bp   # noqa: E402  (CLI / IDE API, access-key auth)
app.register_blueprint(api_bp)

def start_case_runs(c, cases, env, username, exec_id=None):
    """Queues cases to run strictly one after another, in the given order (used by Test Runs and schedules).
    Every case runs whatever happened to the one before it (pass, fail or error). Returns the run ids."""
    items = []
    for case in cases:
        rid = c.execute('INSERT INTO runs(case_id, status, started_at, run_by, env_name, exec_id) VALUES (?,?,?,?,?,?)',
                        (case['id'], 'queued', db.now(), username, env['name'] if env else None, exec_id)).lastrowid
        items.append((rid, case))

    def worker():
        import budget
        for rid, case in items:
            with db.connect() as w:
                if w.execute('SELECT status FROM runs WHERE id=?', (rid,)).fetchone()['status'] != 'queued':
                    continue   # cancelled meanwhile
                w.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (db.now(), rid))
                u = w.execute('SELECT id, username FROM users WHERE username=?', (username,)).fetchone()
                no_ai = not budget.status(w, u)['ok'] if u else False   # re-checked per case: the limit can be hit mid-run
            try:
                _run_case(rid, case['content'], no_ai, case_base_dir(case), env['id'] if env else None)
            except Exception as ex:   # never stop the queue because one case broke
                with db.connect() as w:
                    w.execute("UPDATE runs SET status='ERROR', error=? WHERE id=?", (f'Runner crashed: {ex}'[:300], rid))

    threading.Thread(target=worker, name=f'lth-queue-{exec_id}', daemon=True).start()
    return [rid for rid, _ in items]


import testruns   # noqa: E402  (Test Runs + Scheduled Runs; gets its dependencies passed in)
with db.connect() as _c:
    testruns.init(_c)
testruns_bp = testruns.make_blueprint({'con': con, 'login_required': login_required, 'current_user': current_user,
                                       'start_case_runs': start_case_runs})
app.register_blueprint(testruns_bp)

import reports   # noqa: E402  (User Report; gets con/admin_required passed in instead of importing app)
app.register_blueprint(reports.make_blueprint(con, admin_required))


if __name__ == '__main__':
    recover_interrupted_runs()      # before the scheduler, so it never sees stale 'running' rows
    testruns_bp.start_scheduler()   # checks due schedules every 30 s while this server runs
    port = int(os.environ.get('PORT', '5050'))
    print(f' * LogiTestHub on http://localhost:{port}  (LAN: http://<this-pc-ip>:{port})')
    app.run(host='0.0.0.0', port=port, threaded=True, debug=False)
