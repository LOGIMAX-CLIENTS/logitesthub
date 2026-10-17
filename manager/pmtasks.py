"""PM tasks <-> test cases.

A PM task (e.g. PM-LMX-1741) gets the test cases written for it:
- Developer: the PM task page has "Start testing" / "Stop testing" buttons -> /tasks/<code>/start and /stop. While a
  user's task is active, every test case they create or run (web, Tester Assistant, Tester CLI) is tagged with it.
  One active task per user; it stops by itself after ACTIVE_HOURS.
- Reviewer: the PM task in Review shows a "Test Assistant" button (reviewer only). PM queues the click (MCP tool
  pm_lth_assistant_requests) and opens /projects/<pid>/assistant?task=<code> in a new tab: a chat bound to that task.
  Every test case saved from that chat is tagged with it. When a run ends, its card has "Send to PM" for a PASSED run
  or for a failure the assistant found to be an app bug (a test-side failure is fixed and run again instead); only
  when the queue holds that reviewer's click for the task. Nothing from that chat goes to PM by itself.
- /tasks/<code> lists the task's test cases and their latest runs. That one link is attached to the PM task (PM MCP
  tool pm_task_test_report_attach), so PM prefills it as the "Test report link" when the task goes to Review.

PM MCP: LTH_PM_MCP_URL (default https://pm.logimaxindia.com/api/mcp/rpc/) and a key with pm:write in LTH_PM_MCP_KEY
(a service key for LogiTestHub; the person who tested is sent as submitted_by_member_id). No key: nothing is sent and
the task page shows that the link is not attached yet.

Registered from app.py with register(app, con, login_required, current_user); start_cli_poller() reads
PM's Tester CLI queue (see below).
"""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, has_request_context, redirect, render_template, request, url_for

import db

CODE_RE = re.compile(r'^[A-Z][A-Z0-9]{0,9}(-[A-Z0-9]{1,12}(\.[0-9]{1,4})*){1,4}$')   # PM-LMX-1741, PM-LMX-1322-7769, sub-task PM-S-LMX-1799.1
ACTIVE_HOURS = 12
PM_URL_DEFAULT = 'https://pm.logimaxindia.com/api/mcp/rpc/'
# the PM MCP tools LogiTestHub's key must be allowed to call (Settings -> Integrations -> Test connection checks them)
NEEDED_TOOLS = ('pm_lth_cli_requests', 'pm_lth_assistant_requests', 'pm_lth_task_events', 'pm_lth_run_status',
                'pm_task_test_report_attach', 'pm_members_search')
# Settings -> Integrations (pm.* settings) override these; an empty box there leaves what the server was started with
ENV_KEYS = {'pm.mcp_url': 'LTH_PM_MCP_URL', 'pm.mcp_key': 'LTH_PM_MCP_KEY', 'pm.public_url': 'LTH_PUBLIC_URL'}
_STARTED_WITH = {e: os.environ.get(e) for e in ENV_KEYS.values()}


def pm_url():
    return os.environ.get('LTH_PM_MCP_URL') or PM_URL_DEFAULT


def apply_settings(con):
    """Pushes the PM integration settings into the environment (read on every PM call, so no restart)."""
    import budget
    import envs
    for name, env in ENV_KEYS.items():
        v = budget.get_setting(con, name) or ''
        if name == 'pm.mcp_key':
            v = envs.decrypt(v)
        v = v or _STARTED_WITH[env]
        if v:
            os.environ[env] = v
        else:
            os.environ.pop(env, None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_run_links (
  run_id INT PRIMARY KEY, task_code VARCHAR(40) NOT NULL, username VARCHAR(64), url VARCHAR(500),
  status VARCHAR(16) NOT NULL, message VARCHAR(500), at VARCHAR(32) NOT NULL, KEY ix_pm_run_links_task (task_code, run_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS case_tasks (
  case_id INT NOT NULL, task_code VARCHAR(40) NOT NULL, added_by VARCHAR(64), added_at VARCHAR(32) NOT NULL,
  source VARCHAR(16), PRIMARY KEY (case_id, task_code), KEY ix_case_tasks_code (task_code)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS user_tasks (
  username VARCHAR(64) PRIMARY KEY, task_code VARCHAR(40) NOT NULL, started_at VARCHAR(32) NOT NULL,
  expires_at VARCHAR(32) NOT NULL, source VARCHAR(16) NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pm_task_links (
  task_code VARCHAR(40) NOT NULL, url VARCHAR(500), status VARCHAR(16), message VARCHAR(500),
  attached_by VARCHAR(64) NOT NULL, at VARCHAR(32), PRIMARY KEY (task_code, attached_by)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pm_cli_requests (
  request_id VARCHAR(64) PRIMARY KEY, task_code VARCHAR(40) NOT NULL, member_id VARCHAR(64), email VARCHAR(254), requested_at VARCHAR(40),
  username VARCHAR(64), status VARCHAR(16) NOT NULL, message VARCHAR(500), at VARCHAR(32) NOT NULL,
  KEY ix_pm_cli_requests_task (task_code)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pm_assistant_requests (
  request_id VARCHAR(64) PRIMARY KEY, task_code VARCHAR(40) NOT NULL, member_id VARCHAR(64), email VARCHAR(254), requested_at VARCHAR(40),
  username VARCHAR(64), status VARCHAR(16) NOT NULL, message VARCHAR(500), at VARCHAR(32) NOT NULL,
  KEY ix_pm_assistant_requests_task (task_code, username)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pm_task_events (
  event_id VARCHAR(64) PRIMARY KEY, task_code VARCHAR(40), from_status VARCHAR(64), to_status VARCHAR(64),
  changed_at VARCHAR(40), email VARCHAR(254), stopped VARCHAR(500), at VARCHAR(32) NOT NULL,
  KEY ix_pm_task_events_task (task_code)
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
    pk = [r['COLUMN_NAME'] for r in con.execute(
        "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='pm_task_links' AND CONSTRAINT_NAME='PRIMARY'")]
    if not con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                       "AND TABLE_NAME='pm_cli_requests' AND COLUMN_NAME='email'").fetchone()['n']:
        con.execute('ALTER TABLE pm_cli_requests ADD COLUMN email VARCHAR(254) NULL AFTER member_id')
    if not con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                       "AND TABLE_NAME='user_tasks' AND COLUMN_NAME='source'").fetchone()['n']:
        con.execute('ALTER TABLE user_tasks ADD COLUMN source VARCHAR(16) NULL')   # 'pm_click' = started by PM's Tester CLI button
    if not con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                       "AND TABLE_NAME='pm_run_links' AND COLUMN_NAME='via'").fetchone()['n']:
        con.execute('ALTER TABLE pm_run_links ADD COLUMN via VARCHAR(16) NULL')   # 'cli' / 'assistant': which PM click sends it
    if pk == ['task_code']:   # one link per task before; now one per tester, so each one's work reaches PM as theirs
        con.execute("DELETE FROM pm_task_links WHERE attached_by IS NULL")
        con.execute('ALTER TABLE pm_task_links MODIFY attached_by VARCHAR(64) NOT NULL, '
                    'DROP PRIMARY KEY, ADD PRIMARY KEY (task_code, attached_by)')


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


def start(con, username, code, source='local'):
    """Makes code the user's active task (a task started before is replaced). Returns the task it replaced.
    source 'pm_click' = PM's Tester CLI button: only such a task sends links to PM. Starting the same task again
    locally (web, `tester task start`) keeps it a PM click."""
    old = active_task(con, username)
    if old == code and pm_clicked(con, username, code):
        source = 'pm_click'
    exp = (datetime.now() + timedelta(hours=ACTIVE_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
    with con:
        con.execute('REPLACE INTO user_tasks(username, task_code, started_at, expires_at, source) VALUES (?,?,?,?,?)',
                    (username, code, db.now(), exp, source))
    return old if old != code else None


def pm_clicked(con, username, code):
    """True when code is the user's active task and it was started by their own Tester CLI click in PM.
    The rule: links go to PM only then, so with no click nothing is sent, whatever is tested."""
    if not (username and code) or active_task(con, username) != code:
        return False
    r = con.execute('SELECT source FROM user_tasks WHERE username=?', (username,)).fetchone()
    return bool(r) and r['source'] == 'pm_click'


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


def task_cases(con, code, by=None):
    """The task's test cases, each with its latest run. by: only one tester's - the cases they linked or ran for
    this task, with their own latest run."""
    sel = ('SELECT c.id, c.tc_no, c.title, c.project_id, p.name AS project, t.added_by, t.added_at, t.source, '
           'r.id AS run_id, r.status AS run_status, r.started_at AS run_at, r.run_by, r.passed, r.total, r.fail_step, r.fail_reason '
           'FROM case_tasks t JOIN cases c ON c.id=t.case_id JOIN projects p ON p.id=c.project_id ')
    if not by:
        return con.execute(sel + 'LEFT JOIN runs r ON r.id=(SELECT MAX(id) FROM runs WHERE case_id=c.id) '
                           'WHERE t.task_code=? ORDER BY t.added_at', (code,)).fetchall()
    return con.execute(
        sel + 'LEFT JOIN runs r ON r.id=(SELECT MAX(l.run_id) FROM pm_run_links l JOIN runs r2 ON r2.id=l.run_id '
              '                    WHERE l.task_code=t.task_code AND l.username=? AND r2.case_id=c.id) '
              'WHERE t.task_code=? AND (t.added_by=? OR r.id IS NOT NULL) ORDER BY t.added_at', (by, code, by)).fetchall()


def task_url(base, code, username):
    """The tester's own page for the task: the link sent to PM as theirs."""
    return f'{base}/tasks/{code}?by={urllib.parse.quote(username)}'


def summary(rows):
    st = [r['run_status'] for r in rows]
    return {'cases': len(rows), 'passed': st.count('PASSED'), 'failed': st.count('FAILED') + st.count('ERROR'),
            'other': sum(1 for s in st if s not in ('PASSED', 'FAILED', 'ERROR')), }


def public_base():
    """The LogiTestHub address sent to PM: Settings -> Integrations, else the address the browser used (None outside a request)."""
    base = os.environ.get('LTH_PUBLIC_URL') or (request.host_url if has_request_context() else '')
    return base.rstrip('/') or None


# ---------------------------------------------------------------- PM MCP client (JSON-RPC over HTTP)

class PmError(Exception):
    pass


def _post(body, key, session_id=None, url=None):
    req = urllib.request.Request(url or pm_url(), data=json.dumps(body).encode(), method='POST', headers={
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


def _session(key, url=None):
    key = key or os.environ.get('LTH_PM_MCP_KEY')
    if not key:
        raise PmError('No PM MCP key on this server (Settings -> Integrations).')
    init, sid = _post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'LogiTestHub', 'version': '1'}}}, key, url=url)
    if init and init.get('error'):
        raise PmError(f'PM MCP: {init["error"].get("message")}')
    _post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, key, sid, url)
    return key, sid


def pm_tools(key=None, url=None):
    """The PM MCP tools this key may call (Settings -> Integrations -> Test connection)."""
    key, sid = _session(key, url)
    out, _ = _post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}, key, sid, url)
    if not out or out.get('error'):
        raise PmError(f'PM MCP: {(out or {}).get("error", {}).get("message", "no answer")}')
    return sorted(t.get('name', '') for t in (out.get('result') or {}).get('tools') or [])


def pm_call(tool, args, key=None, url=None):
    """Calls one PM MCP tool and returns its result as a dict (the tool's JSON text)."""
    key, sid = _session(key, url)
    out, _ = _post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, key, sid, url)
    if not out or out.get('error'):
        raise PmError(f'PM MCP: {(out or {}).get("error", {}).get("message", "no answer")}')
    res = out.get('result') or {}
    text = ' '.join(c.get('text', '') for c in res.get('content') or [] if c.get('type') == 'text')
    if res.get('isError'):
        raise PmError(f'PM: {text[:300]}')
    if res.get('structuredContent'):
        return res['structuredContent']
    # some PM servers put a summary line before the JSON ("5 member(s).\n\n[truncated: ...] {...}")
    i = text.find('{')
    try:
        return json.JSONDecoder().raw_decode(text, i)[0] if i >= 0 else {'text': text}
    except ValueError:
        return {'text': text}


def _member_id(con, username):
    """The PM member id of a LogiTestHub user: their PM sign-in id, else a unique PM name match."""
    u = con.execute('SELECT name, pm_api_member_id FROM users WHERE username=?', (username,)).fetchone()
    if not u:
        return None
    if u['pm_api_member_id']:
        return u['pm_api_member_id']
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
    """Attaches the task page link to the PM task (PM prefills it at Review). Records the outcome; never raises.
    Only for a task the user started with their own Tester CLI click in PM: otherwise nothing is sent (False)."""
    con = db.connect()
    if not pm_clicked(con, username, code):
        con.close()
        return False
    url = task_url(base, code, username)   # per tester: PM ignores the same URL sent again for a task
    try:
        s = summary(task_cases(con, code, username))
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
    return True


# ---------------------------------------------------------------- every run of a task -> PM, as the user who ran it

def assistant_task(con, username, case_id):
    """The PM task of the user's Test Assistant chat that made this test case (opened from PM's button), else None."""
    r = con.execute('SELECT task_code FROM chat_threads WHERE case_id=? AND created_by=? AND task_code IS NOT NULL '
                    'ORDER BY id DESC LIMIT 1', (case_id, username)).fetchone()
    return r['task_code'] if r else None


def assistant_clicked(con, username, code):
    """True when PM's queue held this user's Test Assistant click on the task (the reviewer's own click)."""
    return bool(con.execute("SELECT 1 FROM pm_assistant_requests WHERE task_code=? AND username=? AND status='ready' LIMIT 1",
                            (code, username)).fetchone())


def review_check(con, username, code):
    """Is the signed-in user the PM reviewer who clicked Test Assistant on this task? (None = yes, else why not.)
    PM and LogiTestHub sign in separately: the click is matched to a LogiTestHub account by its PM email, so someone
    signed in here as another person (or whose email differs) cannot send this task's runs to PM."""
    if assistant_clicked(con, username, code):
        return None
    try:
        poll_assistant_requests()   # the click may still be in PM's queue
    except Exception:
        pass
    if assistant_clicked(con, username, code):
        return None
    q = con.execute('SELECT * FROM pm_assistant_requests WHERE task_code=? ORDER BY requested_at DESC LIMIT 1', (code,)).fetchone()
    if not q:
        return (f'No Test Assistant click on {code} has reached LogiTestHub from PM yet (or Settings -> Integrations does not '
                'pick up PM clicks), so runs here cannot be sent to PM. Open this chat with the Test Assistant button in PM.')
    if q['username']:
        return (f'{code} was opened in PM by {q["email"]}, who is "{q["username"]}" here, but you are signed in as "{username}". '
                f'Sign in to LogiTestHub as {q["username"]} to send runs to PM, or click Test Assistant in PM with your own PM account.')
    return (f'{code} was opened in PM by {q["email"] or "someone"}, but no LogiTestHub account has that email, and yours '
            f'("{username}") is not the PM reviewer. Ask an admin to set your email in Settings -> Users exactly as in PM.')


def task_thread(con, username, code, pid):
    """The user's Test Assistant chat for a PM task in a project (made the first time). Returns its id."""
    t = con.execute('SELECT id FROM chat_threads WHERE created_by=? AND task_code=? AND project_id=? ORDER BY id DESC LIMIT 1',
                    (username, code, pid)).fetchone()
    if t:
        return t['id']
    with con:
        return con.execute('INSERT INTO chat_threads(project_id, title, created_by, created_at, updated_at, task_code) '
                           'VALUES (?,?,?,?,?,?)', (pid, f'{code}: test cases', username, db.now(), db.now(), code)).lastrowid


def run_started(con, run_id, case_id, username, code):
    """A run of a test case linked to the user's PM task: noted now (task, user, link), sent to PM when it ends.
    Taken at the start, so a run still goes to the right task and person if they stop or switch task meanwhile.
    - a test case from the user's Test Assistant chat for a PM task: that task (checked against their own click in PM
      when the run ends, as the click may still be on its way here);
    - else the task the user started with their own Tester CLI click in PM.
    With no click, nothing goes to PM."""
    via, asst = None, assistant_task(con, username, case_id) if run_id else None
    if asst:
        code, via = asst, 'assistant'
    elif run_id and code and pm_clicked(con, username, code):
        via = 'cli'
    if not via:
        return
    base = public_base()
    # Tester CLI: one link per case and tester that always opens their latest passed run (PM cannot replace a link,
    # so a re-run adds nothing there). Test Assistant: the run itself, as the reviewer may send a failed run (app bug).
    url = (f'{base}/cases/{case_id}?by={urllib.parse.quote(username)}&latest=passed' if via == 'cli' else
           f'{base}/cases/{case_id}?run={run_id}') if base else None
    con.execute('REPLACE INTO pm_run_links(run_id, task_code, username, url, status, message, at, via) VALUES (?,?,?,?,?,?,?,?)',
                (run_id, code, username, url, 'pending',
                 'Sent to PM if the run passes.' if via == 'cli' else 'When it ends: Send to PM in the chat.', db.now(), via))


_RUN_SQL = ('SELECT l.task_code, l.username, l.url, l.via, l.status AS link_status, r.status, r.passed, r.total, r.case_id, '
            'c.tc_no, c.title FROM pm_run_links l JOIN runs r ON r.id=l.run_id JOIN cases c ON c.id=r.case_id WHERE l.run_id=?')


def _attach_run(con, run_id, r, note):
    """Sends one run's link to its PM task as the PM member who ran it and records the outcome. Never raises."""
    try:
        if not r['url']:
            raise PmError('No LogiTestHub address for PM users (Settings -> Integrations): link not sent.')
        member = _member_id(con, r['username'])
        if not member:
            raise PmError(f'Could not find "{r["username"]}" among PM members: link not sent.')
        # PM files the link by provider: Test Assistant runs in its "Test Assistant" box, the rest under Tester CLI
        provider = 'LogiTestHub Assistant' if r['via'] == 'assistant' else 'LogiTestHub'
        res = pm_call('pm_task_test_report_attach', {'task': r['task_code'], 'url': r['url'], 'provider': provider,
                                                     'submitted_by_member_id': member, 'note': note[:2000]})
        status, msg = 'attached', ('Already attached.' if res.get('already_attached') else 'Sent to the PM task.')
    except PmError as e:
        status, msg = ('no_key' if 'No PM MCP key' in str(e) else 'error'), str(e)
    except Exception as e:   # never break a run over PM
        status, msg = 'error', f'PM attach failed: {e}'[:400]
    with con:
        con.execute('UPDATE pm_run_links SET status=?, message=?, at=? WHERE run_id=?', (status, msg[:500], db.now(), run_id))
    return status, msg


_LIVE, _LIVE_LOCK = {}, threading.Lock()   # task code -> CLI runs going now (PM shows one flag per task)


def run_live(con, username, code, running):
    """A Tester CLI run started (running=True) or ended: PM's Tester CLI button blinks while any run of the task is
    going. Only for a task the user started with their own Tester CLI click in PM. PM clears a flag left on for 20
    minutes. Returns (task, sent or the reason not)"""
    code = norm(code) or active_task(con, username)
    if not code or not pm_clicked(con, username, code):
        return code, 'not a task started from PM'
    with _LIVE_LOCK:
        n = max(0, _LIVE.get(code, 0) + (1 if running else -1))
        _LIVE[code] = n
        if not running and n:
            return code, f'{n} other run(s) of {code} still going'
    try:
        pm_call('pm_lth_run_status', {'task': code, 'running': bool(running)})
        return code, 'sent'
    except Exception as e:   # never stop a run over PM
        return code, f'PM: {e}'[:200]


def send_run(run_id):
    """When a run ends. Tester CLI: attaches its link to the PM task, submitted by the PM member who ran it - only a
    PASSED run, and only the first one per case and tester (a case is re-run until it passes, and PM keeps every link
    it gets). Test Assistant (reviewer): nothing is sent by itself - the reviewer sends it from the chat (push_run)."""
    con = db.connect()
    try:
        r = con.execute(_RUN_SQL, (run_id,)).fetchone()
        if not r:
            return
        if r['via'] == 'assistant':
            msg = ('Passed: click Send to PM in the chat.' if r['status'] == 'PASSED' else
                   f'{r["status"]}: fix the test and run again, or send it to PM if the assistant found an app bug.')
            with con:
                con.execute("UPDATE pm_run_links SET status='waiting', message=?, at=? WHERE run_id=?", (msg, db.now(), run_id))
            return
        sent = con.execute("SELECT l.run_id FROM pm_run_links l JOIN runs r ON r.id=l.run_id WHERE l.task_code=? AND l.username=? "
                           "AND r.case_id=? AND l.status='attached' AND l.run_id<>? AND l.url=? LIMIT 1",   # same link: PM has it
                           (r['task_code'], r['username'], r['case_id'], run_id, r['url'])).fetchone()
        skip = (f'{r["status"]}: only a passed run is sent to PM.' if r['status'] != 'PASSED' else
                f'Already in PM: its link opens the latest passed run (this one).' if sent else None)
        if skip:
            with con:
                con.execute("UPDATE pm_run_links SET status='skipped', message=?, at=? WHERE run_id=?", (skip, db.now(), run_id))
            return
        healed = con.execute('SELECT message FROM case_heals WHERE run_id=? ORDER BY id DESC LIMIT 1', (run_id,)).fetchone()
        _attach_run(con, run_id, r, f'[TC-{r["tc_no"]}] {r["title"]}: {r["status"]} {r["passed"] or 0}/{r["total"] or 0}'
                    + (f' - {healed["message"]}' if healed else ''))
    finally:
        con.close()


def push_run(run_id, username, bug=None):
    """The reviewer's "Send to PM" on a run of their Test Assistant chat: a PASSED run, or a failed one the assistant
    found to be an app bug (bug = what is wrong). Only after their own Test Assistant click on the task in PM.
    Returns (ok, message)."""
    con = db.connect()
    try:
        r = con.execute(_RUN_SQL, (run_id,)).fetchone()
        if not r or r['via'] != 'assistant' or r['username'] != username:
            return False, 'This run is not from your Test Assistant chat for a PM task.'
        if r['link_status'] == 'attached':
            return True, 'Already sent to PM.'
        if r['status'] in ('running', 'queued'):
            return False, 'The run has not finished yet.'
        if r['status'] != 'PASSED' and not bug:
            return False, 'Only a passed run, or a failure the assistant found to be an app bug, goes to PM.'
        if not assistant_clicked(con, username, r['task_code']):
            try:
                poll_assistant_requests()   # the click may not have been read yet
            except Exception:
                pass
            if not assistant_clicked(con, username, r['task_code']):
                return False, (f'No Test Assistant click by you on {r["task_code"]} in PM yet (or PM has another email for you '
                               'than Settings -> Users). Open the chat from the Test Assistant button in PM.')
        head = f'[TC-{r["tc_no"]}] {r["title"]}: {r["status"]} {r["passed"] or 0}/{r["total"] or 0}'
        status, msg = _attach_run(con, run_id, r, head if r['status'] == 'PASSED' else f'{head} - App bug: {bug}')
        return status == 'attached', msg
    finally:
        con.close()


def run_pm(con, run_id):
    """The run's PM link state for the chat's run card (None when the run does not go to PM)."""
    return con.execute('SELECT task_code, via, status, message FROM pm_run_links WHERE run_id=?', (run_id,)).fetchone()


def run_finished(run_id):
    threading.Thread(target=send_run, args=(run_id,), daemon=True).start()


def run_links(con, code, by=None):
    return con.execute('SELECT l.*, r.status AS run_status, r.case_id, c.tc_no FROM pm_run_links l JOIN runs r ON r.id=l.run_id '
                       'JOIN cases c ON c.id=r.case_id WHERE l.task_code=?' + (' AND l.username=?' if by else '') +
                       ' ORDER BY l.run_id DESC LIMIT 200', (code, by) if by else (code,)).fetchall()


def attach_later(code, username):
    base = public_base()
    threading.Thread(target=attach, args=(code, username, base), daemon=True).start()


# ---------------------------------------------------------------- PM's "Tester CLI" / "Test Assistant" buttons -> queue -> here
# PM does not call LogiTestHub: each button puts a request in a PM queue (task + who clicked: member id and email) and
# this server reads it (pm_lth_cli_requests / pm_lth_assistant_requests). The clicker is found by email only
# (Settings -> Users).
# - Tester CLI: starts the task for that LogiTestHub account and attaches their link, so the work reaches PM as the
#   person who clicked, whoever is signed in to the browser.
# - Test Assistant (task's reviewer, task in Review): PM also opens the assistant page for the task. The request is
#   only recorded here: it lets that reviewer's PASSED runs of the test cases from their chat go to PM.
# Only one LogiTestHub server should read the queues: Settings -> Integrations -> "Pick up clicks from PM".

POLL_SECONDS = 10


def lth_user(con, email, member_id=None):
    """The LogiTestHub username whose email (Settings -> Users) is the PM clicker's email; None when there is none.
    The PM member id is kept on that account, as PM needs it to show who attached a link."""
    email = str(email or '').strip().lower()
    if not email:
        return None
    r = con.execute('SELECT username, pm_api_member_id FROM users WHERE LOWER(email)=?', (email,)).fetchone()
    if not r:
        return None
    if member_id and r['pm_api_member_id'] != member_id and not con.execute(
            'SELECT 1 FROM users WHERE pm_api_member_id=?', (member_id,)).fetchone():
        with con:
            con.execute('UPDATE users SET pm_api_member_id=? WHERE username=?', (member_id, r['username']))
    return r['username']


def _record(con, table, q, code, user, status, msg):
    with con:
        con.execute(f'REPLACE INTO {table}(request_id, task_code, member_id, email, requested_at, username, status, message, at) '
                    'VALUES (?,?,?,?,?,?,?,?,?)', (q.get('request_id'), code or str(q.get('task_code'))[:40],
                                                   q.get('requested_by_member_id'), q.get('requested_by_email'),
                                                   q.get('requested_at'), user, status, msg[:500], db.now()))


def take_cli_request(con, q, base):
    """One queued Tester CLI request -> start the task for its sender and attach their link. Records the outcome."""
    code, member, email = norm(q.get('task_code')), q.get('requested_by_member_id'), q.get('requested_by_email')
    user, status = None, 'error'
    try:
        when = datetime.fromisoformat(str(q.get('requested_at'))[:26])
        if not code:
            msg = f'Not a task code: {q.get("task_code")}'
        elif datetime.now() - when > timedelta(hours=ACTIVE_HOURS):
            status, msg = 'expired', f'Older than {ACTIVE_HOURS} hours: not started.'
        else:
            user = lth_user(con, email, member)
            if not email:
                status, msg = 'no_email', 'PM sent no email for who clicked (requested_by_email): not started.'
            elif not user:
                status, msg = 'no_account', f'No LogiTestHub account with the email {email} (Settings -> Users).'
            else:
                old = start(con, user, code, 'pm_click')
                attach(code, user, base)
                status, msg = 'started', f'Started for {user}.' + (f' {old} was stopped.' if old else '')
    except Exception as e:   # one bad request must not stop the queue
        msg = f'Could not take the request: {e}'[:400]
    _record(con, 'pm_cli_requests', q, code, user, status, msg)


def take_assistant_request(con, q, base=None):
    """One queued Test Assistant request (the reviewer's click; PM opened the assistant page for them) -> recorded for
    their LogiTestHub account: their passed runs of this task's test cases now go to PM. Nothing is started or attached."""
    code, email = norm(q.get('task_code')), q.get('requested_by_email')
    user, status = None, 'error'
    try:
        user = lth_user(con, email, q.get('requested_by_member_id'))
        if not code:
            msg = f'Not a task code: {q.get("task_code")}'
        elif not email:
            status, msg = 'no_email', 'PM sent no email for who clicked (requested_by_email).'
        elif not user:
            status, msg = 'no_account', f'No LogiTestHub account with the email {email} (Settings -> Users).'
        else:
            status, msg = 'ready', f'Review by {user}: each passed run of the test cases from their chat goes to PM.'
            # PM shows Test Assistant only on a task in Review: its testing is over, so it stops for every tester (a
            # task sent back to Development starts again with their next Tester CLI click)
            with con:
                done = [x['username'] for x in con.execute('SELECT username FROM user_tasks WHERE task_code=?', (code,))]
                con.execute('DELETE FROM user_tasks WHERE task_code=?', (code,))
            if done:
                msg += f' In Review: testing stopped for {", ".join(done)}.'
    except Exception as e:   # one bad request must not stop the queue
        msg = f'Could not take the request: {e}'[:400]
    _record(con, 'pm_assistant_requests', q, code, user, status, msg)


def _poll(tool, table, since_key, take, need_base):
    """Reads one PM queue once (when this server picks it up) and takes each new request. Returns how many."""
    import budget
    con = db.connect()
    try:
        if budget.get_setting(con, 'pm.cli_poll') != '1' or not os.environ.get('LTH_PM_MCP_KEY'):
            return 0
        base = public_base()
        if need_base and not base:
            return 0   # links PM users can open need Settings -> Integrations -> LogiTestHub address
        since = budget.get_setting(con, since_key) or ''
        res = pm_call(tool, {'since': since, 'limit': 50})
        n = 0
        for q in res.get('requests') or []:
            if not con.execute(f'SELECT 1 FROM {table} WHERE request_id=?', (q.get('request_id'),)).fetchone():
                take(con, q, base)
                n += 1
            since = max(since, str(q.get('requested_at') or ''))
        with con:
            budget.set_setting(con, since_key, since)
        return n
    finally:
        con.close()


def poll_cli_requests():
    return _poll('pm_lth_cli_requests', 'pm_cli_requests', 'pm.cli_since', take_cli_request, True)


def poll_assistant_requests():
    return _poll('pm_lth_assistant_requests', 'pm_assistant_requests', 'pm.assistant_since', take_assistant_request, False)


# ---------------------------------------------------------------- PM task moved on (Review ...) -> its testing stops
# PM puts each process change of a task in a queue (pm_lth_task_events). When a task leaves Development / Testing
# for Review or later, testing it is over: it stops for every tester. Sent back to Development, the tester's next
# Tester CLI click starts it again.
# PM's event: {event_id, task_code, from_status, to_status, changed_by_email, changed_at}; from_status and
# changed_by_email may be "" (a first handover / a member PM could not resolve); every process change comes, in
# (changed_at, event_id) order, and `since` is inclusive (the last changed_at is sent back, its events skipped by id).
TESTING_DONE = {'review', 'deployment', 'delivery'}   # PM's processes after Development / Testing


def take_task_event(con, q):
    """One queued process change of a PM task. Records it; stops the task for every tester when it moved to Review
    or later."""
    code, to = norm(q.get('task_code')), str(q.get('to_status') or '')
    stopped = []
    if code and to.strip().lower() in TESTING_DONE:
        with con:
            stopped = [x['username'] for x in con.execute('SELECT username FROM user_tasks WHERE task_code=?', (code,))]
            con.execute('DELETE FROM user_tasks WHERE task_code=?', (code,))
    with con:
        con.execute('REPLACE INTO pm_task_events(event_id, task_code, from_status, to_status, changed_at, email, stopped, at) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (str(q.get('event_id'))[:64], code or None, str(q.get('from_status') or '')[:64], to[:64],
                     str(q.get('changed_at') or '')[:40], str(q.get('changed_by_email') or '')[:254],
                     ', '.join(stopped)[:500] or None, db.now()))
    if stopped:
        print(f' * PM: {code} moved to {to}: testing stopped for {", ".join(stopped)}')


def poll_task_events():
    """Reads PM's task-event queue once (when this server picks up PM clicks). Returns how many were new."""
    import budget
    con = db.connect()
    try:
        if budget.get_setting(con, 'pm.cli_poll') != '1' or not os.environ.get('LTH_PM_MCP_KEY'):
            return 0
        since = budget.get_setting(con, 'pm.events_since') or ''
        res = pm_call('pm_lth_task_events', {'since': since, 'limit': 50})
        n = 0
        for q in res.get('events') or []:
            eid = str(q.get('event_id') or '')
            if eid and not con.execute('SELECT 1 FROM pm_task_events WHERE event_id=?', (eid[:64],)).fetchone():
                take_task_event(con, q)
                n += 1
            since = max(since, str(q.get('changed_at') or ''))
        with con:
            budget.set_setting(con, 'pm.events_since', since)
        return n
    finally:
        con.close()


def _poll_loop():
    import time
    wait = {}   # queue -> when to try again after an error (PM down, key expired, key without that tool)
    while True:
        for name, fn in (('Tester CLI', poll_cli_requests), ('Test Assistant', poll_assistant_requests),
                         ('task events', poll_task_events)):
            if wait.get(name, 0) > time.time():
                continue
            try:
                fn()
            except Exception as e:
                print(f' * PM {name} queue: {e}')
                wait[name] = time.time() + 60
        time.sleep(POLL_SECONDS)


def start_cli_poller():
    threading.Thread(target=_poll_loop, name='pm-cli-requests', daemon=True).start()


# ---------------------------------------------------------------- pages

def register(app, con, login_required, current_user):
    with db.connect() as c0:
        init(c0)
        apply_settings(c0)
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
        by = (request.args.get('by') or '').strip() or None   # one tester's page: the link PM got from them
        rows = task_cases(con(), code, by)
        link = con().execute('SELECT * FROM pm_task_links WHERE task_code=? AND attached_by=?', (code, me['username'])).fetchone()
        links = con().execute('SELECT * FROM pm_task_links WHERE task_code=? ORDER BY at', (code,)).fetchall()
        testers = [r['u'] for r in con().execute(
            'SELECT added_by AS u FROM case_tasks WHERE task_code=? UNION SELECT username FROM pm_run_links WHERE task_code=? '
            'UNION SELECT attached_by FROM pm_task_links WHERE task_code=?', (code, code, code)) if r['u']]
        cli_reqs = con().execute('SELECT * FROM pm_cli_requests WHERE task_code=? ORDER BY requested_at DESC LIMIT 20',
                                 (code,)).fetchall()
        asst_reqs = con().execute('SELECT * FROM pm_assistant_requests WHERE task_code=? ORDER BY requested_at DESC LIMIT 20',
                                  (code,)).fetchall()
        return render_template('task.html', code=code, rows=rows, s=summary(rows), link=link, links=links, by=by, cli_reqs=cli_reqs,
                               asst_reqs=asst_reqs,
                               testers=sorted(set(testers)), run_links=run_links(con(), code, by),
                               mine=active_task(con(), me['username']) == code, hours=ACTIVE_HOURS,
                               clicked=pm_clicked(con(), me['username'], code),
                               pm_key=bool(os.environ.get('LTH_PM_MCP_KEY')))

    @bp.route('/tasks/<code>/start')
    @login_required
    def start_task(code):
        code = code_or_404(code)
        me = current_user()
        old = start(con(), me['username'], code)
        flash(f'Testing {code}: every test case you create or run is linked to it until you stop (or {ACTIVE_HOURS} hours).'
              + (f' {old} was stopped.' if old else '')
              + ('' if pm_clicked(con(), me['username'], code) else
                 ' Links go to PM only when you start the task with the Tester CLI button in PM.'), 'success')
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
        me = current_user()['username']
        if not attach(code, me, public_base()):
            flash('Not sent: links go to PM only for a task you started with the Tester CLI button in PM.', 'warning')
            return redirect(url_for('pmtasks.page', code=code))
        r = con().execute('SELECT status, message FROM pm_task_links WHERE task_code=? AND attached_by=?', (code, me)).fetchone()
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
        return redirect(url_for('assistant.page', pid=pid, t=task_thread(con(), me['username'], code, pid)))

    app.register_blueprint(bp)
