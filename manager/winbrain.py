"""WinBrain (the Logimax knowledge base): an app bug a tester found becomes a triage there.

A tester with no PM task has nobody to send a failed run to. "Send to Brain" on the run card files it in WinBrain as a
triage owned by the same person (matched by email): the bug, the steps the run executed with their results, and the
screenshot of the failure. The tester reviews it there and raises the PM task; nothing here creates a task.

WinBrain's analysis can take minutes, so the request runs in a thread and the run card polls for the outcome. WinBrain
keeps one triage per run: a retry, or a second click, answers with the same one.

The other way: "Run in LogiTestHub" on a triage there asks this server to run the triage's steps (POST /api/winbrain/verify).
They run as a test case on the environment whose address is the client's staging site, with that environment's own
login, and the verdicts go back to the triage when the run ends. No client password crosses over.

Requests are signed as WinBrain's peer LTH: X-Integration-Key lth-1 and the shared secret from Settings -> Integrations
(src/lib/integrations/hmac.ts there, protocol v2).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request

import db

KEY_ID = 'lth-1'
TRIAGE_PATH = '/api/integrations/lth/triage'
CATALOG_PATH = '/api/integrations/lth/catalog'
RESULT_PATH = '/api/integrations/lth/verification'
VERIFY_PATH = '/api/winbrain/verify'
SECTION = 'WinBrain verification'   # the case's section: tells the triage's steps from the login helper's
FOLDER = 'WinBrain triages'
MAX_SKEW = 300            # seconds, as WinBrain
REPORT_TRIES = 4
MAX_STEPS = 100           # WinBrain's limit
MAX_SHOT = 700_000        # WinBrain's limit for one screenshot, in bytes
ASK_FOR = 300             # seconds to keep asking while WinBrain analyses
ASK_EVERY = 15
SEND_TIMEOUT = 150        # past WinBrain's nginx (120 s): it answers 504 and keeps working
STALE_SENDING = 600       # a server restart killed the thread: the button comes back

SCHEMA = """
CREATE TABLE IF NOT EXISTS wb_run_links (
  run_id INT PRIMARY KEY, username VARCHAR(64), status VARCHAR(16) NOT NULL, message VARCHAR(500), url VARCHAR(500),
  chat_id VARCHAR(40), at VARCHAR(32) NOT NULL, epoch INT NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS wb_verify_runs (
  run_id INT PRIMARY KEY, session_id VARCHAR(36) NOT NULL, run_token VARCHAR(128) NOT NULL, steps MEDIUMTEXT NOT NULL,
  base VARCHAR(500), status VARCHAR(16) NOT NULL, message VARCHAR(500), at VARCHAR(32) NOT NULL, KEY ix_wb_verify_session (session_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS wb_verify_cases (
  session_id VARCHAR(36) PRIMARY KEY, case_id INT NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS wb_nonces (
  nonce CHAR(32) PRIMARY KEY, epoch INT NOT NULL, KEY ix_wb_nonces_epoch (epoch)
) ENGINE=InnoDB
"""


class WinbrainError(Exception):
    pass


def init(con):
    for stmt in (s.strip() for s in SCHEMA.split(';')):
        if stmt:
            con.execute(stmt)


# ---------------------------------------------------------------- settings

def settings(con):
    import budget
    import envs
    try:
        mapping = json.loads(budget.get_setting(con, 'wb.map') or '{}')
    except ValueError:
        mapping = {}
    return {'url': (budget.get_setting(con, 'wb.url') or os.environ.get('LTH_WINBRAIN_URL') or '').rstrip('/'),
            'secret': envs.decrypt(budget.get_setting(con, 'wb.secret')) or os.environ.get('LTH_WINBRAIN_SECRET') or '',
            'projects': mapping.get('projects') or {}, 'envs': mapping.get('envs') or {}}


def enabled(con, project_id):
    """The button shows only when it can work: connected, and this project is mapped to a WinBrain product."""
    s = settings(con)
    return bool(s['url'] and s['secret'] and s['projects'].get(str(project_id)))


def _instance(con):
    """Keeps run numbers of two LogiTestHub servers apart in WinBrain's one-triage-per-run rule."""
    import budget
    v = budget.get_setting(con, 'wb.instance')
    if not v:
        v = secrets.token_hex(4)
        with con:
            budget.set_setting(con, 'wb.instance', v)
    return v


# ---------------------------------------------------------------- signed requests

def sign(secret, method, path, body, timestamp, nonce, key_id=KEY_ID):
    """WinBrain's v2 signature: HMAC-SHA256 of 2.{key}.{timestamp}.{nonce}.{METHOD}.{path}.{sha256(body)}."""
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    base = f'2.{key_id}.{timestamp}.{nonce}.{method.upper()}.{path}.{digest}'
    return hmac.new(secret.encode('utf-8'), base.encode('utf-8'), hashlib.sha256).hexdigest()


def _call(url, secret, method, path, body=''):
    """(HTTP status, parsed answer). A retry needs a new signature: a nonce works once."""
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    req = urllib.request.Request(url + path, data=body.encode('utf-8') if body else None, method=method, headers={
        'Content-Type': 'application/json', 'Accept': 'application/json', 'X-Integration-Key': KEY_ID,
        'X-Timestamp': timestamp, 'X-Signature-Version': '2', 'X-Nonce': nonce,
        'X-Signature': sign(secret, method, path, body, timestamp, nonce)})
    try:
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT) as resp:
            return resp.status, _json(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise WinbrainError(f'WinBrain not reachable ({getattr(e, "reason", e)})')


def _json(raw):
    try:
        out = json.loads(raw or b'{}')
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


def catalog(url, secret):
    """The products and clients this secret can see (Settings -> Integrations -> Test connection)."""
    status, out = _call(url, secret, 'GET', CATALOG_PATH)
    if status != 200 or not out.get('status'):
        raise WinbrainError('Key not accepted by WinBrain.' if status == 401 else f'WinBrain answered HTTP {status}.')
    return out['data']['products']


# ---------------------------------------------------------------- run -> payload

def _shot(run, name):
    """A step's screenshot as WinBrain takes it, or None: too big or gone is missing evidence, not a failed send."""
    import s3store
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(os.path.splitext(name or '')[1].lower())
    if not mime:
        return None
    try:
        path = os.path.join(run['run_dir'], name)
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                raw = f.read(MAX_SHOT + 1)
        else:   # moved to Amazon S3
            with urllib.request.urlopen(s3store.link(run, name), timeout=20) as resp:
                raw = resp.read(MAX_SHOT + 1)
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return None
    return {'mime': mime, 'data': base64.b64encode(raw).decode('ascii')} if len(raw) <= MAX_SHOT else None


def steps_of(run, events):
    """The steps the run reached, as WinBrain's verdicts. Steps after the failure never ran, so they are not sent: they
    are one line on the failed step. The screenshots: the failure and the step before it."""
    ran = [s for s in events if s.get('status') != 'skipped']
    not_reached = len(events) - len(ran)
    out, verdict = [], {'passed': 'pass', 'failed': 'fail'}
    for s in ran[-MAX_STEPS:]:
        result = verdict.get(s.get('status'), 'blocked')
        note = db.explain_step_error(s.get('text'), s.get('error'), s.get('status')) if result != 'pass' else ''
        if result == 'fail' and not_reached:
            note = f'{note.rstrip(". ")}. {not_reached} later step(s) not reached.'.lstrip('. ')
        out.append({'text': s.get('text') or '', 'result': result, 'note': note, 'screen': s.get('section') or '',
                    'shot': s.get('shot') or ''})
    failed = next((i for i, s in enumerate(out) if s['result'] == 'fail'), len(out) - 1)
    for i, s in enumerate(out):
        name = s.pop('shot')
        if i in (failed, failed - 1) and name:
            shot = _shot(run, name)
            if shot:
                s['screenshot'] = shot
    return out


_RUN_SQL = ('SELECT r.id, r.case_id, r.status, r.run_by, r.run_dir, r.env_id, r.env_name, c.project_id, c.title, c.tc_no, u.email '
            'FROM runs r JOIN cases c ON c.id=r.case_id LEFT JOIN users u ON u.username=r.run_by WHERE r.id=?')


def _link_row(con, run_id):
    return con.execute('SELECT status, message, url, chat_id, epoch FROM wb_run_links WHERE run_id=?', (run_id,)).fetchone()


def _save(con, run_id, username, status, message, url=None, chat_id=None):
    with con:
        con.execute('INSERT INTO wb_run_links(run_id, username, status, message, url, chat_id, at, epoch) VALUES (?,?,?,?,?,?,?,?) '
                    'ON DUPLICATE KEY UPDATE username=VALUES(username), status=VALUES(status), message=VALUES(message), '
                    'url=VALUES(url), chat_id=VALUES(chat_id), at=VALUES(at), epoch=VALUES(epoch)',
                    (run_id, username, status, message[:500], url, chat_id, db.now(), int(time.time())))


def run_link(con, run_id):
    """What the run card shows: None, or status 'sending' / 'sent' / 'failed' with a message and the triage's address."""
    r = _link_row(con, run_id)
    if not r:
        return None
    if r['status'] == 'sending' and time.time() - r['epoch'] > STALE_SENDING:
        return {'status': 'failed', 'message': 'Not sent: the server restarted while sending. Send it again.', 'url': None}
    return {'status': r['status'], 'message': r['message'], 'url': r['url']}


def start(con, project_id, run_id, username, bug, base):
    """The tester's click. Refuses what can never work straight away, then sends in a thread. Returns (ok, message)."""
    r = con.execute(_RUN_SQL, (run_id,)).fetchone()
    if not r or r['project_id'] != project_id or r['run_by'] != username:
        return False, 'This run is not one of yours in this project.'
    if r['status'] in ('running', 'queued'):
        return False, 'The run has not finished yet.'
    if r['status'] == 'PASSED' or not bug:
        return False, 'Only a failure the assistant found to be an app bug goes to WinBrain.'
    if not r['email']:
        return False, 'Your email is not filled in (Settings -> Users): WinBrain finds your account by it.'
    if not enabled(con, project_id):
        return False, 'WinBrain is not set up for this project (Settings -> Integrations).'
    if not base:
        return False, 'No LogiTestHub address for the run link (Settings -> Integrations).'
    link = run_link(con, run_id)
    if link and link['status'] in ('sending', 'sent'):
        return True, link['message']
    _save(con, run_id, username, 'sending', 'WinBrain is analysing it…')
    threading.Thread(target=_send, args=(run_id, username, bug, base), daemon=True).start()
    return True, 'WinBrain is analysing it…'


def _send(run_id, username, bug, base):
    con = db.connect()
    try:
        try:
            url, chat_id = deliver(con, run_id, bug, base)
            _save(con, run_id, username, 'sent', f'Triage {chat_id or ""} is ready: review it and raise the task there.'.replace('  ', ' '),
                  url, chat_id)
        except WinbrainError as e:
            _save(con, run_id, username, 'failed', f'Not sent: {e}')
        except Exception as e:
            print(f' * WinBrain send of run {run_id}: {e}')
            _save(con, run_id, username, 'failed', 'Not sent: unexpected error, see the server log.')
    finally:
        con.close()


def deliver(con, run_id, bug, base):
    """Files the run in WinBrain and waits for the answer. Returns (triage address, triage id)."""
    r = con.execute(_RUN_SQL, (run_id,)).fetchone()
    s = settings(con)
    events = db.read_run_events(r['run_dir'])[1] if r['run_dir'] else []
    steps = steps_of(r, events)
    if not steps:
        raise WinbrainError('this run has no recorded steps.')
    body = json.dumps({
        'runRef': f'lth-{_instance(con)}-{run_id}', 'userEmail': r['email'],
        'productSlug': s['projects'].get(str(r['project_id'])), 'clientSlug': s['envs'].get(str(r['env_id'])) or None,
        'bug': bug, 'testTitle': f'[TC-{r["tc_no"]}] {r["title"]}', 'runUrl': f'{base}/cases/{r["case_id"]}?run={run_id}',
        'envName': r['env_name'] or '', 'steps': steps})
    deadline = time.time() + ASK_FOR
    while True:
        try:
            status, out = _call(s['url'], s['secret'], 'POST', TRIAGE_PATH, body)
        except WinbrainError:
            if time.time() > deadline:
                raise
            status, out = 503, {}
        if status == 200 and out.get('status'):
            d = out['data']
            return s['url'] + d['path'], d.get('chatId')
        if status not in (202, 429, 502, 503, 504):
            raise WinbrainError(out.get('error') if isinstance(out.get('error'), str) else
                                'WinBrain did not accept the key.' if status == 401 else f'WinBrain answered HTTP {status}.')
        if time.time() > deadline:
            raise WinbrainError('WinBrain is still analysing it. Send it again in a few minutes.')
        time.sleep(ASK_EVERY)


# ---------------------------------------------------------------- WinBrain asks: run a triage's steps

def signature_ok(con, secret, method, path, body, headers):
    """The request is WinBrain's: right key, fresh, signed with the secret, and this signature not seen before."""
    timestamp, nonce, given = headers.get('X-Timestamp', ''), headers.get('X-Nonce', ''), headers.get('X-Signature', '')
    if not secret or headers.get('X-Integration-Key') != KEY_ID or headers.get('X-Signature-Version') != '2':
        return False
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_SKEW or len(nonce) != 32:
        return False
    if not hmac.compare_digest(sign(secret, method, path, body, timestamp, nonce), given):
        return False
    with con:   # the replay check comes last: only a caller holding the secret may fill this table
        con.execute('DELETE FROM wb_nonces WHERE epoch<?', (int(time.time()) - 2 * MAX_SKEW,))
        if con.execute('SELECT 1 FROM wb_nonces WHERE nonce=?', (nonce,)).fetchone():
            return False
        con.execute('INSERT INTO wb_nonces(nonce, epoch) VALUES (?,?)', (nonce, int(time.time())))
    return True


def _host(url):
    return (urllib.parse.urlsplit(url or '').hostname or '').lower()


def case_content(title, steps):
    """One step per line. 'Do this:' keeps WinBrain's text an AI browser step: a line that happened to read like one of
    our own commands (Remember SQL ..., @import ...) must not run as that command."""
    lines = [f'Do this: {" ".join(str(s["text"]).split())}' for s in steps]
    return f'# {title}\n\n## {SECTION}\n' + '\n'.join(lines) + '\n'


def accept_run(con, q, start_case_runs, base):
    """WinBrain's request. Returns (HTTP status, answer). The checks here are what make it safe to drive a browser:
    the project is the one mapped to this product, and the environment is on the client's staging host."""
    import envs
    steps = q.get('steps')
    if not isinstance(steps, list) or not steps or not all(isinstance(x, dict) and x.get('id') and x.get('text') for x in steps):
        return 400, {'ok': False, 'error': 'steps is required.'}
    if not q.get('sessionId') or not q.get('runToken'):
        return 400, {'ok': False, 'error': 'sessionId and runToken are required.'}
    if not base:
        return 422, {'ok': False, 'error': 'LogiTestHub has no address for its run links (Settings -> Integrations).'}
    s = settings(con)
    project_id = next((int(p) for p, slug in s['projects'].items() if slug == q.get('productSlug')), None)
    if not project_id or not con.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        return 422, {'ok': False, 'error': f'No LogiTestHub project is mapped to product "{q.get("productSlug")}" (Settings -> Integrations).'}
    user = con.execute('SELECT id, username FROM users WHERE LOWER(email)=LOWER(?)', (q.get('userEmail') or '',)).fetchone()
    if not user:
        return 403, {'ok': False, 'error': f'LogiTestHub has no account with the email {q.get("userEmail")} (Settings -> Users).'}
    host = _host(q.get('stagingBaseUrl'))
    env = next((e for e in envs.all_envs(con) if host and _host(e['base_url']) == host and not e['owner']
                and e['project_id'] in (None, project_id)), None)
    if not env:
        return 422, {'ok': False, 'error': f'No LogiTestHub environment is on {host or "this client"}\'s staging address (Environments).'}
    if envs.LIVE_NAME.search(env['name'] or ''):
        return 422, {'ok': False, 'error': f'Environment "{env["name"]}" is named as a live site: verification runs on staging only.'}
    title = (f'[{q.get("chatId")}] ' if q.get('chatId') else '') + 'Triage verification'
    content = case_content(title, steps)
    with con:
        row = con.execute('SELECT case_id FROM wb_verify_cases WHERE session_id=?', (q['sessionId'],)).fetchone()
        if row:
            con.execute('UPDATE cases SET content=?, updated_by=?, updated_at=? WHERE id=?', (content, user['username'], db.now(), row['case_id']))
            case_id = row['case_id']
        else:
            folder = db.get_or_create_folder(con, project_id, FOLDER)
            case_id = db.add_case(con, project_id, folder, title, content, user['username'])
            con.execute('INSERT INTO wb_verify_cases(session_id, case_id) VALUES (?,?)', (q['sessionId'], case_id))
    case = con.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
    run_id = start_case_runs(con, [case], env, user['username'])[0]
    with con:
        con.execute('INSERT INTO wb_verify_runs(run_id, session_id, run_token, steps, base, status, at) VALUES (?,?,?,?,?,?,?)',
                    (run_id, q['sessionId'], str(q['runToken'])[:128],
                     json.dumps([{'id': str(x['id']), 'report': bool(x.get('report'))} for x in steps]), base, 'running', db.now()))
    return 202, {'ok': True, 'run_id': run_id, 'url': f'{base}/cases/{case_id}?run={run_id}'}


def verdicts(run, events, asked):
    """What the run found for the steps WinBrain asked about. Returns (steps, run state, message). Lines of the case's
    section are in the order they were sent; the login helper's steps come before them under their own section."""
    mine = [e for e in events if e.get('section') == SECTION]
    outside = next((e for e in events if e.get('section') != SECTION and e.get('status') == 'failed'), None)
    stopped = None
    if outside:
        stopped = db.explain_step_error(outside.get('text'), outside.get('error'), 'failed')
    elif run['status'] == 'ERROR' or (not events and run['status'] != 'PASSED'):
        stopped = run['error'] or 'The run did not finish.'
    out, not_reached, failed_at = [], 0, None
    for i, a in enumerate(asked):
        e = mine[i] if i < len(mine) else {}
        st = e.get('status')
        if st in ('skipped', None):
            not_reached += a['report']
        if not a['report']:
            continue
        if st == 'passed':
            step = {'id': a['id'], 'result': 'pass', 'note': ''}
        elif st == 'failed':
            step = {'id': a['id'], 'result': 'fail', 'note': db.explain_step_error(e.get('text'), e.get('error'), 'failed')}
            failed_at = failed_at if failed_at is not None else len(out)
        elif st in ('skipped', None):
            step = {'id': a['id'], 'result': 'blocked', 'note': f'Not reached: {stopped}' if stopped else 'Not reached: an earlier step failed.'}
        else:
            step = {'id': a['id'], 'result': 'blocked', 'note': db.explain_step_error(e.get('text'), e.get('error'), st) or 'The AI could not judge this step.'}
        step.update(screen=e.get('section') or '', shot=e.get('shot') or '')
        out.append(step)
    for i, step in enumerate(out):
        name = step.pop('shot')
        if failed_at is not None and i in (failed_at, failed_at - 1) and name:
            shot = _shot(run, name)
            if shot:
                step['screenshot'] = shot
    if stopped:
        return out, 'failed', f'The run stopped before the steps: {stopped}'
    if not_reached:
        return out, 'failed', f'{not_reached} step(s) not reached: an earlier step failed.'
    return out, 'completed', ''


def run_finished(run_id):
    threading.Thread(target=report_run, args=(run_id,), daemon=True).start()


def report_run(run_id):
    """Sends a finished run's verdicts to the triage that asked for it. Not one of WinBrain's runs: nothing to do."""
    con = db.connect()
    try:
        link = con.execute('SELECT session_id, run_token, steps, base FROM wb_verify_runs WHERE run_id=?', (run_id,)).fetchone()
        if not link:
            return
        run = con.execute('SELECT r.id, r.status, r.run_dir, r.error, r.case_id FROM runs r WHERE r.id=?', (run_id,)).fetchone()
        events = db.read_run_events(run['run_dir'])[1] if run['run_dir'] else []
        steps, state, message = verdicts(run, events, json.loads(link['steps']))
        s = settings(con)
        body = json.dumps({'sessionId': link['session_id'], 'runToken': link['run_token'], 'runState': state, 'message': message,
                           'runUrl': f'{link["base"]}/cases/{run["case_id"]}?run={run_id}', 'steps': steps})
        outcome, note = 'failed', ''
        for attempt in range(REPORT_TRIES):
            try:
                status, out = _call(s['url'], s['secret'], 'POST', RESULT_PATH, body)
            except WinbrainError as e:
                status, out = 503, {'error': str(e)}
            note = out.get('error') if isinstance(out.get('error'), str) else f'HTTP {status}'
            if status == 200:
                outcome, note = 'sent', ''
                break
            if status not in (429, 502, 503, 504):   # refused: asking again gets the same answer
                break
            time.sleep(ASK_EVERY * (attempt + 1))
        with con:
            con.execute('UPDATE wb_verify_runs SET status=?, message=? WHERE run_id=?', (outcome, note[:500], run_id))
        if outcome != 'sent':
            print(f' * WinBrain result of run {run_id}: {note}')
    finally:
        con.close()


def register(app, start_case_runs):
    bp = Blueprint('winbrain', __name__, url_prefix='/api/winbrain')

    @bp.route('/verify', methods=['POST'])
    def verify():
        import pmtasks
        body = request.get_data(as_text=True)
        con = db.connect()
        try:
            if not signature_ok(con, settings(con)['secret'], 'POST', VERIFY_PATH, body, request.headers):
                return jsonify({'ok': False, 'error': 'Invalid integration credentials.'}), 401
            try:
                q = json.loads(body)
            except ValueError:
                return jsonify({'ok': False, 'error': 'Body must be JSON.'}), 400
            status, out = accept_run(con, q if isinstance(q, dict) else {}, start_case_runs, pmtasks.public_base())
            return jsonify(out), status
        finally:
            con.close()

    app.register_blueprint(bp)
