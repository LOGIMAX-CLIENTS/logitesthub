"""Test Assistant: describe a test scenario in chat (Tamil / Tanglish / English); the AI shows what it understood and a
draft test case; the user edits it, saves it into the right module / sub-module, and runs it - all on one page.

The AI call goes through ai-bridge.js on this server (the Anthropic key never reaches the browser), uses the model from
Settings -> System, respects the monthly AI limits (budget.py) and is recorded in AI Usage (kind "chat").
Conversations are kept in MySQL (chat_threads / chat_messages), per project.

Registered from app.py with register(app, con, login_required, current_user, project_or_404, start_case_run).
"""
import json
import os
import re
import subprocess
import threading

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_threads (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, title VARCHAR(200) NOT NULL, created_by VARCHAR(64),
  created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL, case_id INT NULL,
  KEY ix_chat_threads_project (project_id, id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS chat_messages (
  id INT AUTO_INCREMENT PRIMARY KEY, thread_id INT NOT NULL, role VARCHAR(12) NOT NULL, text MEDIUMTEXT NOT NULL,
  data MEDIUMTEXT NULL, at VARCHAR(32) NOT NULL,
  KEY ix_chat_messages_thread (thread_id, id)
) ENGINE=InnoDB;
"""
MAX_MSG = 4000          # characters per user message
HISTORY = 14            # messages sent to the AI (latest)


# ---------------------------------------------------------------- AI context + call

def project_context(con, p, env_id=None):
    """What the AI needs to write a runnable case for this project: login helper yes/no, the module list with page
    links (so it picks the right screen and URL), the environment and the variable names it may use."""
    import envs
    lines = [f'Project: {p["name"]}' + (f' - {p["description"]}' if p.get('description') else '')]
    helper = (p.get('login_helper') or '').strip()
    lines.append('Login helper: YES - the project signs in before every test; write NO login steps.' if helper
                 else 'Login helper: NO - start the test with a "Sign in" section using {{username}} / {{password}}.')
    env = envs.get(con, env_id)   # the 'Run on' environment picked on the page (else the default)
    base = (env['base_url'] if env else '') or ''
    if env:
        lines.append(f'Environment the test will run on: {env["name"]}, base_url = {base}')
        from urllib.parse import urlparse
        tail = urlparse(base).path.strip('/').split('/')[-1] if base else ''
        if tail:   # e.g. eTail: base_url ends with /admin/ - the repo folder admin/ must not be repeated in page paths
            lines.append(f"{{{{base_url}}}} already ends with '/{tail}/'. Page paths go straight after it: "
                         f"{{{{base_url}}}}index.php/<controller>/<method> - never start a path with '{tail}/'.")
    if os.environ.get('LTH_AI_PROVIDER') == 'claude-code':
        # the subscription answers chat; screenshot-judged steps in a run still need API credits
        lines.append("Run-time AI: NOT available. Free-text checks cannot run now - use ONLY the exact step shapes listed "
                     "(no \"does not contain\", no \"... is visible in the header\" sentences). "
                     "Prefer: Assert the text 'X' is visible.")
    if p.get('repo_commit') and os.environ.get('LTH_AI_PROVIDER') == 'claude-code':
        lines.append(f"Code repository: {p.get('repo_url')} branch {p.get('repo_branch')} commit {p['repo_commit'][:10]} "
                     "(in your current folder - look the screen up there).")
    names = sorted(set(['base_url', 'username', 'password', 'branch'] + list(envs._base_values().keys())))
    lines.append('Variables you may use as {{name}}: ' + ', '.join(names[:60]))
    rows = con.execute('SELECT id, parent_id, name, link FROM folders WHERE project_id=? AND archived=0 ORDER BY sort, name',
                       (p['id'],)).fetchall()
    tops = {r['id']: r for r in rows if not r['parent_id']}
    out = []
    for r in rows:
        mod = tops.get(r['parent_id']) if r['parent_id'] else None
        if r['parent_id'] and not mod:
            continue
        name = f'{mod["name"]} > {r["name"]}' if mod else r['name']
        link = (r['link'] or '').strip()
        if base and link.startswith(base):
            link = link[len(base):]
        out.append(f'- {name}' + (f'  [{link}]' if link and link != '#' else ''))
    if out:
        lines.append(f'Module list (module > sub-module [page path after base_url]), {len(out)} entries:')
        lines += out[:900]
    else:
        lines.append('Module list: none yet (leave module / sub_module empty).')
    import lessons
    learned = lessons.prompt_text(con, p['id'])
    if learned:
        lines.append(learned)
    return '\n'.join(lines)


RUNNING = {}            # request key -> Popen of the AI turn in progress (the Stop button ends it)
_RUN_LOCK = threading.Lock()
STOPPED = 'stopped'


def _kill_tree(proc):
    """Ends the AI process and its children (node -> claude) at once."""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        pass


def stop_turn(key):
    with _RUN_LOCK:
        proc = RUNNING.get(key)
    if not proc:
        return False
    proc._lth_stopped = True
    _kill_tree(proc)
    return True


def ask_ai(messages, context, code_dir=None, key=None):
    """One Test Assistant turn through ai-bridge.js. Returns (data, usage, model, error); error STOPPED when stopped."""
    req = {'op': 'chat', 'messages': messages, 'context': context, 'codeDir': code_dir}
    try:
        proc = subprocess.Popen(['node', os.path.join(db.RUNNER_DIR, 'ai-bridge.js')], cwd=db.RUNNER_DIR, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return None, None, None, 'The AI service on this server did not answer.'
    if key:
        with _RUN_LOCK:
            RUNNING[key] = proc
    try:
        stdout, _ = proc.communicate(json.dumps(req), timeout=480)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return None, None, None, 'The AI took longer than 8 minutes, so it was stopped. Please try again.'
    finally:
        if key:
            with _RUN_LOCK:
                RUNNING.pop(key, None)
    if getattr(proc, '_lth_stopped', False):
        return None, None, None, STOPPED
    try:
        out = json.loads(stdout or '{}')
    except ValueError:
        return None, None, None, 'The AI service on this server did not answer.'
    if not out.get('ok'):
        return None, None, None, out.get('error') or 'The AI could not answer.'
    return out.get('data') or {}, out.get('usage') or {}, out.get('model'), None


def friendly_ai_error(err):
    if 'no API credits' in (err or ''):
        return ('AI is not available yet: the Anthropic account has no API credits. An admin can add credits '
                '(Anthropic Console), then record them in AI Usage -> Credits.')
    if 'API key invalid' in (err or ''):
        return 'AI is not available: the Anthropic API key on the LogiTestHub server is not valid. Ask an admin.'
    return err or 'The AI could not answer.'


# ---------------------------------------------------------------- draft -> test case

def draft_to_text(draft):
    """AI draft {title, steps:[{section, text}]} -> the editor text: '## Section' lines followed by one step per line."""
    out, sec = [], None
    for s in (draft or {}).get('steps') or []:
        if s.get('section') and s['section'] != sec:
            sec = s['section']
            out.append(('' if not out else '\n') + f'## {sec}')
        if (s.get('text') or '').strip():
            out.append(s['text'].strip())
    return '\n'.join(out)


def case_content(title, steps_text):
    body = '\n'.join(l.rstrip() for l in steps_text.replace('\r\n', '\n').split('\n')).strip()
    return f'---\nmode: testing\ntags: [assistant]\n---\n\n# {title}\n\n{body}\n'


def match_folder(con, pid, module, sub):
    """Folder id for the AI's module / sub-module names (case-insensitive); the sub-module wins, else the module."""
    if not module:
        return None
    m = con.execute('SELECT id FROM folders WHERE project_id=? AND parent_id=0 AND archived=0 AND LOWER(name)=LOWER(?)',
                    (pid, module.strip())).fetchone()
    if not m:
        return None
    if sub:
        s = con.execute('SELECT id FROM folders WHERE project_id=? AND parent_id=? AND archived=0 AND LOWER(name)=LOWER(?)',
                        (pid, m['id'], sub.strip())).fetchone()
        if s:
            return s['id']
    return m['id']


# ---------------------------------------------------------------- blueprint

def link_chat_usage(c, tid, cid):
    """Puts a conversation's AI usage rows (written before it was saved) on the test case it produced."""
    c.execute("UPDATE ai_usage SET case_id=? WHERE kind='chat' AND case_id IS NULL AND (note=? OR note LIKE ?)",
              (cid, f'Test Assistant chat #{tid}', f'Test Assistant chat #{tid} %'))


def _payload():
    """The page posts a form (so the app's CSRF check applies) with the data as JSON in a "payload" field."""
    try:
        d = json.loads(request.form.get('payload') or '{}')
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def register(app, con, login_required, current_user, project_or_404, start_case_run):
    c0 = db.connect()
    try:
        cur = c0._raw.cursor()
        for stmt in (s.strip() for s in SCHEMA.split(';')):
            if stmt:
                cur.execute(stmt)
    finally:
        c0.close()

    bp = Blueprint('assistant', __name__)

    def thread_or_404(pid, tid):
        t = con().execute('SELECT * FROM chat_threads WHERE id=? AND project_id=?', (tid, pid)).fetchone()
        return t or abort(404)

    def messages_of(tid):
        rows = con().execute('SELECT id, role, text, data, at FROM chat_messages WHERE thread_id=? ORDER BY id', (tid,)).fetchall()
        for r in rows:
            try:
                r['data'] = json.loads(r['data']) if r['data'] else None
            except ValueError:
                r['data'] = None
        return rows

    def add_message(tid, role, text, data=None):
        c = con()
        with c:
            mid = c.execute('INSERT INTO chat_messages(thread_id, role, text, data, at) VALUES (?,?,?,?,?)',
                            (tid, role, text, json.dumps(data, ensure_ascii=False) if data is not None else None, db.now())).lastrowid
            c.execute('UPDATE chat_threads SET updated_at=? WHERE id=?', (db.now(), tid))
        return mid

    @bp.route('/assistant')
    @login_required
    def home():
        p = con().execute('SELECT p.id FROM projects p LEFT JOIN chat_threads t ON t.project_id=p.id AND t.created_by=? '
                          'GROUP BY p.id ORDER BY MAX(t.updated_at) DESC, p.id LIMIT 1', (current_user()['username'],)).fetchone()
        if not p:   # the assistant works inside a project: say so instead of silently landing on Projects
            flash('The Test Assistant works inside a project. Create a project first, then open the Test Assistant.', 'info')
            return redirect(url_for('projects'))
        return redirect(url_for('assistant.page', pid=p['id']))

    @bp.route('/projects/<int:pid>/assistant')
    @login_required
    def page(pid):
        import budget
        import coderepo
        import envs
        p = project_or_404(pid)
        me = current_user()
        # admins (the top role) can see everyone's conversations, read-only; others see only their own
        import settings
        is_admin = settings.is_admin(me['role'])
        who = (request.args.get('who') or '').strip() if is_admin else ''
        where, params = ['t.project_id=?'], [pid]
        if who == 'all':
            pass
        elif who:
            where.append('t.created_by=?')
            params.append(who)
        else:
            where.append('t.created_by=?')
            params.append(me['username'])
        threads = con().execute(
            'SELECT t.*, u.name AS owner_name, cs.tc_no, '
            '(SELECT r.status FROM runs r WHERE r.case_id=t.case_id ORDER BY r.id DESC LIMIT 1) AS last_run '
            'FROM chat_threads t LEFT JOIN users u ON u.username=t.created_by LEFT JOIN cases cs ON cs.id=t.case_id '
            f'WHERE {" AND ".join(where)} ORDER BY t.updated_at DESC LIMIT 100', params).fetchall()
        tid = request.args.get('t', type=int)
        thread = next((t for t in threads if t['id'] == tid), None)
        if tid and not thread and is_admin:   # an admin opening someone else's chat from a link
            thread = thread_or_404(pid, tid)
        readonly = bool(thread and thread['created_by'] != me['username'])
        users = [r['created_by'] for r in con().execute(
            'SELECT DISTINCT created_by FROM chat_threads WHERE project_id=? ORDER BY created_by', (pid,))] if is_admin else []
        folders = con().execute('SELECT * FROM folders WHERE project_id=? AND archived=0 ORDER BY sort, name', (pid,)).fetchall()
        b = budget.status(con(), me)
        return render_template('assistant.html', p=p, threads=threads, thread=thread, readonly=readonly, who=who, users=users,
                               msgs=messages_of(thread['id']) if thread else [], folders=folders,
                               envs=envs.all_envs(con()), ai_ok=b['ok'], ai_msg=b['message'], code_ready=bool(coderepo.code_dir(p)),
                               code_provider_ok=os.environ.get('LTH_AI_PROVIDER') == 'claude-code',
                               projects=con().execute('SELECT id, name FROM projects ORDER BY name').fetchall())

    @bp.route('/assistant/activity')
    @login_required
    def activity():
        """Admins: who used the Test Assistant, what they asked, what they saved and ran, and what the AI cost."""
        import pricing
        import settings
        if not settings.is_admin(current_user()['role']):
            abort(403)
        c = con()
        pid = request.args.get('project', type=int)
        pw, pp = (' AND t.project_id=?', [pid]) if pid else ('', [])
        users = c.execute(
            'SELECT t.created_by AS username, MAX(u.name) AS name, MAX(u.department) AS department, COUNT(DISTINCT t.id) AS chats, '
            "(SELECT COUNT(*) FROM chat_messages m JOIN chat_threads t2 ON t2.id=m.thread_id WHERE m.role='user' "
            f"AND t2.created_by=t.created_by{pw.replace('t.', 't2.')}) AS messages, "
            'COUNT(DISTINCT t.case_id) AS saved, MAX(t.updated_at) AS last_at '
            f'FROM chat_threads t LEFT JOIN users u ON u.username=t.created_by WHERE 1=1{pw} GROUP BY t.created_by ORDER BY last_at DESC',
            pp + pp).fetchall()
        for u in users:   # runs of the test cases they saved from the chat, and their chat AI cost
            r = c.execute("SELECT COUNT(*) AS runs, SUM(r.status='PASSED') AS passed, SUM(r.status='FAILED') AS failed, "
                          "SUM(r.status NOT IN ('PASSED','FAILED')) AS other FROM runs r WHERE r.run_by=? AND r.case_id IN "
                          f"(SELECT t.case_id FROM chat_threads t WHERE t.created_by=? AND t.case_id IS NOT NULL{pw})",
                          [u['username'], u['username']] + pp).fetchone()
            a = c.execute("SELECT COALESCE(SUM(calls),0) AS calls, COALESCE(SUM(input_tokens+output_tokens+cache_read_tokens+cache_write_tokens),0) AS tok, "
                          "COALESCE(SUM(cost_usd),0) AS cost FROM ai_usage WHERE kind='chat' AND username=?"
                          + (' AND project_id=?' if pid else ''), [u['username']] + pp).fetchone()
            u.update({k: (r[k] or 0) for k in ('runs', 'passed', 'failed', 'other')}, calls=a['calls'], tok=a['tok'], cost=float(a['cost']))
        recent = c.execute(
            'SELECT t.id, t.project_id, t.title, t.created_by, t.created_at, t.updated_at, t.case_id, p.name AS project, cs.tc_no, '
            "(SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id=t.id AND m.role='user') AS messages, "
            '(SELECT r.status FROM runs r WHERE r.case_id=t.case_id ORDER BY r.id DESC LIMIT 1) AS last_run, '
            '(SELECT r.id FROM runs r WHERE r.case_id=t.case_id ORDER BY r.id DESC LIMIT 1) AS last_run_id '
            'FROM chat_threads t JOIN projects p ON p.id=t.project_id LEFT JOIN cases cs ON cs.id=t.case_id '
            f'WHERE 1=1{pw} ORDER BY t.updated_at DESC LIMIT 200', pp).fetchall()
        total = {k: sum(u[k] for u in users) for k in ('chats', 'messages', 'saved', 'runs', 'passed', 'failed', 'calls', 'tok', 'cost')}
        return render_template('assistant_activity.html', users=users, recent=recent, total=total, pid=pid,
                               projects=c.execute('SELECT id, name FROM projects ORDER BY name').fetchall(), inr=pricing.usd_inr(c))

    @bp.route('/projects/<int:pid>/assistant/send', methods=['POST'])
    @login_required
    def send(pid):
        import budget
        p = project_or_404(pid)
        me = current_user()
        body = _payload()
        text = re.sub(r'[ \t]+\n', '\n', str(body.get('text') or '')).strip()[:MAX_MSG]
        if not text:
            return jsonify({'ok': False, 'error': 'Type your scenario or question first.'}), 400
        tid = body.get('thread_id')
        if tid:
            t = thread_or_404(pid, int(tid))
            if t['created_by'] != me['username']:
                abort(403)
        else:
            c = con()
            with c:
                tid = c.execute('INSERT INTO chat_threads(project_id, title, created_by, created_at, updated_at) VALUES (?,?,?,?,?)',
                                (pid, re.sub(r'\s+', ' ', text)[:120], me['username'], db.now(), db.now())).lastrowid
        add_message(tid, 'user', text)
        b = budget.status(con(), me)
        if not b['ok']:
            msg = b['message'] + ' The Test Assistant needs AI, so it cannot answer until the limit is raised.'
            add_message(tid, 'system', msg)
            return jsonify({'ok': False, 'thread_id': tid, 'error': msg})
        # history for the AI: user words + the assistant's replies (with the draft it last showed, as edited by the user)
        hist = []
        for m in messages_of(tid)[-HISTORY:]:
            if m['role'] == 'user':
                hist.append({'role': 'user', 'text': m['text']})
            elif m['role'] == 'assistant':
                d = m['data'] or {}
                t = m['text']
                if d.get('draft'):
                    t += '\nDRAFT: ' + d['draft'].get('title', '') + '\n' + draft_to_text(d['draft'])
                hist.append({'role': 'assistant', 'text': t})
        edited = body.get('draft_text')
        if edited and hist and hist[-1]['role'] == 'user':
            hist.insert(len(hist) - 1, {'role': 'user', 'text': 'My current version of the draft (edited by me):\n'
                                        + str(body.get('draft_title') or '') + '\n' + str(edited)[:12000]})
        import coderepo
        key = f"{me['username']}:{str(body.get('req') or '')[:40]}"
        env_id = body.get('env_id') if str(body.get('env_id') or '').isdigit() else None
        data, usage, model, err = ask_ai(hist, project_context(con(), p, env_id), coderepo.code_dir(p), key)
        if err == STOPPED:
            return jsonify({'ok': False, 'stopped': True, 'thread_id': tid})
        if err:
            msg = friendly_ai_error(err)
            add_message(tid, 'system', msg)
            return jsonify({'ok': False, 'thread_id': tid, 'error': msg})
        draft = data.get('draft')
        u = data.get('understanding') or {}
        out = {'understanding': u, 'questions': data.get('questions') or [], 'draft': draft,
               'draft_text': draft_to_text(draft) if draft else '',
               'folder_id': match_folder(con(), pid, u.get('module'), u.get('sub_module'))}
        mid = add_message(tid, 'assistant', data.get('reply') or '', out)
        c = con()
        with c:
            u = usage or {}   # Anthropic usage names -> the names record_ai_usage reads
            db.record_ai_usage(c, 'chat', {'model': u.get('model') or model, 'calls': 1, 'in': u.get('input_tokens', 0),
                                           'out': u.get('output_tokens', 0), 'cacheRead': u.get('cache_read_input_tokens', 0),
                                           'cacheWrite': u.get('cache_creation_input_tokens', 0)},
                               username=me['username'], project_id=pid,
                               case_id=c.execute('SELECT case_id FROM chat_threads WHERE id=?', (tid,)).fetchone()['case_id'],
                               note=f'Test Assistant chat #{tid}' + (' (Claude subscription via Claude Code)' if u.get('via') == 'claude-code' else ''))
        return jsonify({'ok': True, 'thread_id': tid, 'message': {'id': mid, 'role': 'assistant', 'text': data.get('reply') or '', 'data': out},
                        'warn': b['message'] if b.get('warn') else ''})

    @bp.route('/projects/<int:pid>/assistant/stop', methods=['POST'])
    @login_required
    def stop(pid):
        """Stop button: ends this user's AI turn that is still thinking."""
        project_or_404(pid)
        req = str(_payload().get('req') or '')[:40]
        return jsonify({'ok': stop_turn(f"{current_user()['username']}:{req}")})

    @bp.route('/projects/<int:pid>/assistant/<int:tid>/edit-last', methods=['POST'])
    @login_required
    def edit_last(pid, tid):
        """Edit: takes back the last message the user sent (and any answer to it) so it can be changed and sent again."""
        t = thread_or_404(pid, tid)
        if t['created_by'] != current_user()['username']:
            abort(403)
        c = con()
        last = c.execute("SELECT id, text FROM chat_messages WHERE thread_id=? AND role='user' ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        if not last:
            return jsonify({'ok': False, 'error': 'Nothing to edit.'})
        with c:
            c.execute('DELETE FROM chat_messages WHERE thread_id=? AND id>=?', (tid, last['id']))
        return jsonify({'ok': True, 'text': last['text']})

    @bp.route('/projects/<int:pid>/assistant/<int:tid>/save', methods=['POST'])
    @login_required
    def save(pid, tid):
        import settings
        p = project_or_404(pid)
        t = thread_or_404(pid, tid)
        me = current_user()
        if t['created_by'] != me['username']:
            abort(403)
        body = _payload()
        title = re.sub(r'\s+', ' ', str(body.get('title') or '')).strip()[:200]
        steps = str(body.get('steps_text') or '').strip()
        lines = [l for l in steps.split('\n') if l.strip() and not l.strip().startswith('#')]
        if not title:
            return jsonify({'ok': False, 'error': 'Give the test case a title.'}), 400
        if not lines:
            return jsonify({'ok': False, 'error': 'The test case has no steps.'}), 400
        if len(steps) > 20000:
            return jsonify({'ok': False, 'error': 'The test case is too long (20,000 characters at most).'}), 400
        fid = body.get('folder_id')
        if fid:
            if not con().execute('SELECT 1 FROM folders WHERE id=? AND project_id=?', (int(fid), pid)).fetchone():
                return jsonify({'ok': False, 'error': 'That folder is not in this project.'}), 400
            fid = int(fid)
        else:
            fid = None
        want_run = bool(body.get('run'))
        if want_run and not settings.can(con(), me['role'], 'runs.run'):
            return jsonify({'ok': False, 'error': 'Your role cannot run tests. Save it, and ask a Tester to run it.'}), 403
        content = case_content(title, steps)
        c = con()
        with c:
            cid = db.add_case(c, pid, fid, title, content, me['username'])
            c.execute('UPDATE chat_threads SET case_id=? WHERE id=?', (cid, tid))
            link_chat_usage(c, tid, cid)   # the chat's AI cost now counts for this test case
        case = con().execute('SELECT * FROM cases WHERE id=?', (cid,)).fetchone()
        run_id, note = None, ''
        if want_run:
            run_id, bud = start_case_run(case, body.get('env_id'))
            if not bud['ok']:
                note = ' AI limit reached: AI steps are skipped, the other steps still run.'
        add_message(tid, 'system', f'Saved as TC-{case["tc_no"]}' + (' and started a run.' if run_id else '.') + note,
                    {'type': 'saved', 'case_id': cid, 'tc_no': case['tc_no'], 'run_id': run_id})
        return jsonify({'ok': True, 'case_id': cid, 'tc_no': case['tc_no'], 'run_id': run_id, 'note': note,
                        'case_url': url_for('case', cid=cid), 'run_url': url_for('case', cid=cid, run=run_id) if run_id else None})

    @bp.route('/projects/<int:pid>/assistant/run/<int:rid>')
    @login_required
    def run_status(pid, rid):
        project_or_404(pid)
        r = con().execute('SELECT r.id, r.case_id, r.status, r.passed, r.failed, r.needs_ai, r.skipped, r.total, r.error, r.duration_ms '
                          'FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.id=? AND c.project_id=?', (rid, pid)).fetchone()
        if not r:
            abort(404)
        return jsonify({'ok': True, **r, 'done': r['status'] not in ('running', 'queued'),
                        'url': url_for('case', cid=r['case_id'], run=rid)})

    @bp.route('/projects/<int:pid>/assistant/<int:tid>/delete', methods=['POST'])
    @login_required
    def delete(pid, tid):
        t = thread_or_404(pid, tid)
        if t['created_by'] != current_user()['username']:
            abort(403)
        c = con()
        with c:
            c.execute('DELETE FROM chat_messages WHERE thread_id=?', (tid,))
            c.execute('DELETE FROM chat_threads WHERE id=?', (tid,))
        return redirect(url_for('assistant.page', pid=pid))

    app.register_blueprint(bp)
