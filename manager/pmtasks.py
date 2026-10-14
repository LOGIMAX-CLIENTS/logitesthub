"""PM tasks <-> test cases.

A PM task (e.g. PM-LMX-1741) gets the test cases written for it:
- Developer: the PM task page has "Start testing" / "Stop testing" buttons -> /tasks/<code>/start and /stop. While a
  user's task is active, every test case they create or run (web, Tester Assistant, Tester CLI) is tagged with it.
  One active task per user; it stops by itself after ACTIVE_HOURS.
- Reviewer: the PM task in Review shows a "Tester Assistant" button -> /tasks/<code>/assistant opens a chat bound to
  that task; every test case saved from that chat is tagged with it.
- /tasks/<code> lists the task's test cases and their latest runs. That one link is attached to the PM task (PM MCP
  tool pm_task_test_report_attach), so PM prefills it as the "Test report link" when the task goes to Review.

PM MCP: LTH_PM_MCP_URL (default https://pm.logimaxindia.com/api/mcp/rpc/) and a key with pm:write in LTH_PM_MCP_KEY
(a service key for LogiTestHub; the person who tested is sent as submitted_by_member_id). No key: nothing is sent and
the task page shows that the link is not attached yet.

Registered from app.py with register(app, con, login_required, current_user).
"""
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db

CODE_RE = re.compile(r'^[A-Z][A-Z0-9]{0,9}(-[A-Z0-9]{1,12}){1,4}$')   # PM-LMX-1741, PM-LMX-1322-7769
ACTIVE_HOURS = 12
PM_URL = os.environ.get('LTH_PM_MCP_URL', 'https://pm.logimaxindia.com/api/mcp/rpc/')

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_tasks (
  case_id INT NOT NULL, task_code VARCHAR(40) NOT NULL, added_by VARCHAR(64), added_at VARCHAR(32) NOT NULL,
  source VARCHAR(16), PRIMARY KEY (case_id, task_code), KEY ix_case_tasks_code (task_code)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS user_tasks (
  username VARCHAR(64) PRIMARY KEY, task_code VARCHAR(40) NOT NULL, started_at VARCHAR(32) NOT NULL,
  expires_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pm_task_links (
  task_code VARCHAR(40) PRIMARY KEY, url VARCHAR(500), status VARCHAR(16), message VARCHAR(500),
  attached_by VARCHAR(64), at VARCHAR(32)
) ENGINE=InnoDB
"""


def init(con):
    for stmt in (s.strip() for s in SCHEMA.split(';')):
        if stmt:
            con.execute(stmt)
    n = con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='chat_threads' AND COLUMN_NAME='task_code'").fetchone()['n']
    if not n:   # a Tester Assistant chat opened from a PM task
        con.execute('ALTER TABLE chat_threads ADD COLUMN task_code VARCHAR(40) NULL')


def norm(code):
    c = str(code or '').strip().upper()
    return c if CODE_RE.match(c) else None


# ---------------------------------------------------------------- active task per user

def active_task(con, username):
    """The user's task while it is active (not stopped, not expired), else None."""
    if not username:
        return None
    r = con.execute('SELECT task_code, expires_at FROM user_tasks WHERE username=?', (username,)).fetchone()
    if not r:
        return None
    if r['expires_at'] < db.now():
        with con:
            con.execute('DELETE FROM user_tasks WHERE username=?', (username,))
        return None
    return r['task_code']


def start(con, username, code):
    """Makes code the user's active task (a task started before is replaced). Returns the task it replaced."""
    old = active_task(con, username)
    exp = (datetime.now() + timedelta(hours=ACTIVE_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    with con:
        con.execute('REPLACE INTO user_tasks(username, task_code, started_at, expires_at) VALUES (?,?,?,?)',
                    (username, code, db.now(), exp))
    return old if old != code else None


def stop(con, username, code=None):
    """Stops the user's active task (only that code when given). True when something was stopped."""
    with con:
        if code:
            return con.execute('DELETE FROM user_tasks WHERE username=? AND task_code=?', (username, code)).rowcount > 0
        return con.execute('DELETE FROM user_tasks WHERE username=?', (username,)).rowcount > 0


# ---------------------------------------------------------------- tagging test cases

def tag(con, case_id, username, source, code=None):
    """Links a test case to a task: the one given, else the user's active task. Returns the code or None."""
    code = norm(code) or active_task(con, username)
    if not (code and case_id):
        return None
    con.execute('INSERT IGNORE INTO case_tasks(case_id, task_code, added_by, added_at, source) VALUES (?,?,?,?,?)',
                (case_id, code, username, db.now(), source))
    return code


def codes_of(con, case_id):
    return [r['task_code'] for r in con.execute('SELECT task_code FROM case_tasks WHERE case_id=? ORDER BY added_at',
                                                (case_id,))]


def task_cases(con, code):
    """The task's test cases, each with its latest run."""
    return con.execute(
        'SELECT c.id, c.tc_no, c.title, c.project_id, p.name AS project, t.added_by, t.added_at, t.source, '
        'r.id AS run_id, r.status AS run_status, r.started_at AS run_at, r.run_by, r.passed, r.total, r.fail_step, r.fail_reason '
        'FROM case_tasks t JOIN cases c ON c.id=t.case_id JOIN projects p ON p.id=c.project_id '
        'LEFT JOIN runs r ON r.id=(SELECT MAX(id) FROM runs WHERE case_id=c.id) '
        'WHERE t.task_code=? ORDER BY t.added_at', (code,)).fetchall()


def summary(rows):
    st = [r['run_status'] for r in rows]
    return {'cases': len(rows), 'passed': st.count('PASSED'), 'failed': st.count('FAILED') + st.count('ERROR'),
            'other': sum(1 for s in st if s not in ('PASSED', 'FAILED', 'ERROR')), }


def public_base():
    return (os.environ.get('LTH_PUBLIC_URL') or request.host_url).rstrip('/')


# ---------------------------------------------------------------- PM MCP client (JSON-RPC over HTTP)

class PmError(Exception):
    pass


def _post(body, key, session_id=None):
    req = urllib.request.Request(PM_URL, data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'Authorization': f'Bearer {key}', **({'Mcp-Session-Id': session_id} if session_id else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw, sid, ctype = resp.read().decode('utf-8', 'replace'), resp.headers.get('Mcp-Session-Id'), resp.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        raise PmError(f'PM MCP answered HTTP {e.code}' + (' (key not accepted)' if e.code in (401, 403) else ''))
    except (urllib.error.URLError, TimeoutError) as e:
        raise PmError(f'PM MCP not reachable ({getattr(e, "reason", e)})')
    if not raw.strip():
        return None, sid
    if 'text/event-stream' in ctype:   # take the last JSON "data:" line
        datas = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith('data:')]
        raw = datas[-1] if datas else '{}'
    return json.loads(raw), sid


def pm_call(tool, args, key=None):
    """Calls one PM MCP tool and returns its result as a dict (the tool's JSON text)."""
    key = key or os.environ.get('LTH_PM_MCP_KEY')
    if not key:
        raise PmError('No PM MCP key on this server (LTH_PM_MCP_KEY).')
    init, sid = _post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'LogiTestHub', 'version': '1'}}}, key)
    if init and init.get('error'):
        raise PmError(f'PM MCP: {init["error"].get("message")}')
    _post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, key, sid)
    out, _ = _post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, key, sid)
    if not out or out.get('error'):
        raise PmError(f'PM MCP: {(out or {}).get("error", {}).get("message", "no answer")}')
    res = out.get('result') or {}
    text = ' '.join(c.get('text', '') for c in res.get('content') or [] if c.get('type') == 'text')
    if res.get('isError'):
        raise PmError(f'PM: {text[:300]}')
    try:
        return res.get('structuredContent') or json.loads(text)
    except ValueError:
        return {'text': text}


def _member_id(con, username):
    """The PM member id of a LogiTestHub user: their PM sign-in id, else a unique PM name match."""
    u = con.execute('SELECT name, pm_member_id FROM users WHERE username=?', (username,)).fetchone()
    if not u:
        return None
    if u['pm_member_id']:
        return u['pm_member_id']
    # PM matches first / last name separately: "Shreenath" finds nothing, "Shree" finds "Shree Nath". So search with
    # the start of the name and keep the one whose name is the same without spaces / case.
    want = re.sub(r'[^a-z]', '', (u['name'] or username).lower())
    for q in dict.fromkeys([u['name'] or username, (u['name'] or username)[:5]]):
        res = pm_call('pm_members_search', {'query': q, 'limit': 50})
        same = [m for m in res.get('members') or [] if m.get('is_active', True)
                and re.sub(r'[^a-z]', '', str(m.get('name') or '').lower()) == want]
        if len(same) == 1:
            return same[0].get('member_id')
    return None


def attach(code, username, base):
    """Attaches the task page link to the PM task (PM prefills it at Review). Records the outcome; never raises."""
    con = db.connect()
    url = f'{base}/tasks/{code}'
    try:
        s = summary(task_cases(con, code))
        member = _member_id(con, username)
        if not member:
            raise PmError(f'Could not find "{username}" among PM members: link not attached.')
        note = f'LogiTestHub: {s["cases"]} test case(s), {s["passed"]} passed, {s["failed"]} failed (live page)'
        res = pm_call('pm_task_test_report_attach', {'task': code, 'url': url, 'submitted_by_member_id': member,
                                                    'provider': 'LogiTestHub', 'note': note})
        status, msg = 'attached', ('Already attached.' if res.get('already_attached') else 'Attached to the PM task.')
    except PmError as e:
        status, msg = ('no_key' if 'No PM MCP key' in str(e) else 'error'), str(e)
    except Exception as e:   # never break the user's click over PM
        status, msg = 'error', f'PM attach failed: {e}'[:400]
    try:
        with con:
            con.execute('REPLACE INTO pm_task_links(task_code, url, status, message, attached_by, at) VALUES (?,?,?,?,?,?)',
                        (code, url, status, msg[:500], username, db.now()))
    finally:
        con.close()


def attach_later(code, username):
    base = public_base()
    threading.Thread(target=attach, args=(code, username, base), daemon=True).start()


# ---------------------------------------------------------------- pages

def register(app, con, login_required, current_user):
    with db.connect() as c0:
        init(c0)
    bp = Blueprint('pmtasks', __name__)

    def code_or_404(code):
        return norm(code) or abort(404)

    @app.context_processor
    def _active_task():
        me = current_user() if request.endpoint and request.endpoint != 'static' else None
        return {'active_task': active_task(con(), me['username']) if me else None}

    @bp.route('/tasks/<code>')
    @login_required
    def page(code):
        code = code_or_404(code)
        me = current_user()
        rows = task_cases(con(), code)
        link = con().execute('SELECT * FROM pm_task_links WHERE task_code=?', (code,)).fetchone()
        return render_template('task.html', code=code, rows=rows, s=summary(rows), link=link,
                               mine=active_task(con(), me['username']) == code, hours=ACTIVE_HOURS,
                               pm_key=bool(os.environ.get('LTH_PM_MCP_KEY')))

    @bp.route('/tasks/<code>/start')
    @login_required
    def start_task(code):
        code = code_or_404(code)
        me = current_user()
        old = start(con(), me['username'], code)
        flash(f'Testing {code}: every test case you create or run is linked to it until you stop (or {ACTIVE_HOURS} hours).'
              + (f' {old} was stopped.' if old else ''), 'success')
        attach_later(code, me['username'])
        return redirect(url_for('pmtasks.page', code=code))

    @bp.route('/tasks/<code>/stop')
    @login_required
    def stop_task(code):
        code = code_or_404(code)
        stopped = stop(con(), current_user()['username'], code)
        flash(f'Stopped testing {code}: new test cases are not linked to it any more.' if stopped
              else f'{code} was not your active task.', 'info')
        return redirect(url_for('pmtasks.page', code=code))

    @bp.route('/tasks/<code>/attach', methods=['POST'])
    @login_required
    def attach_now(code):
        code = code_or_404(code)
        attach(code, current_user()['username'], public_base())
        r = con().execute('SELECT status, message FROM pm_task_links WHERE task_code=?', (code,)).fetchone()
        flash(r['message'] if r else 'Done.', 'success' if r and r['status'] == 'attached' else 'warning')
        return redirect(url_for('pmtasks.page', code=code))

    @bp.route('/tasks/<code>/assistant')
    @login_required
    def assistant(code):
        """Reviewer's button: a Tester Assistant chat for this task (reused if they already have one)."""
        code = code_or_404(code)
        me = current_user()
        pid = request.args.get('project', type=int)
        if not pid or not con().execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
            r = con().execute('SELECT project_id FROM chat_threads WHERE created_by=? ORDER BY updated_at DESC LIMIT 1',
                              (me['username'],)).fetchone() or con().execute('SELECT id AS project_id FROM projects ORDER BY id LIMIT 1').fetchone()
            pid = r['project_id'] if r else None
        if not pid:
            flash('Create a project first, then open the Tester Assistant.', 'info')
            return redirect(url_for('pmtasks.page', code=code))
        t = con().execute('SELECT id FROM chat_threads WHERE created_by=? AND task_code=? AND project_id=? ORDER BY id DESC LIMIT 1',
                          (me['username'], code, pid)).fetchone()
        if t:
            tid = t['id']
        else:
            c = con()
            with c:
                tid = c.execute('INSERT INTO chat_threads(project_id, title, created_by, created_at, updated_at, task_code) '
                                'VALUES (?,?,?,?,?,?)', (pid, f'{code}: test cases', me['username'], db.now(), db.now(), code)).lastrowid
        attach_later(code, me['username'])
        return redirect(url_for('assistant.page', pid=pid, t=tid))

    app.register_blueprint(bp)
