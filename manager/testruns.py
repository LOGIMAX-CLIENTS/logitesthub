"""Test Runs (a named group of test cases run together) and Scheduled Runs (run a Test Run later / every day / every week).

A Test Run picks its cases either
  * dynamically: every case in a project (optionally one folder), optionally only those marked Ready for QA -
    new cases are picked up automatically, which is what a nightly QA schedule wants; or
  * as a fixed list chosen on the project page.
Each time it runs we create an "execution" and one normal run per case (runs.exec_id), so every case keeps its
own video / steps. Schedules run on this server, so they can reach Dev / QA / Live but never a developer's localhost.

Registered from app.py with make_blueprint(deps) so this module never imports app (app.py runs as __main__).
"""
import re
import threading
import time
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db
import envs

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS test_runs (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, name VARCHAR(160) NOT NULL, description VARCHAR(500),
  mode VARCHAR(10) NOT NULL DEFAULT 'dynamic', folder_id INT NULL, only_ready TINYINT NOT NULL DEFAULT 0,
  env_id INT NULL, archived TINYINT NOT NULL DEFAULT 0, created_by VARCHAR(64), created_at VARCHAR(32) NOT NULL,
  updated_by VARCHAR(64), updated_at VARCHAR(32) NOT NULL,
  CONSTRAINT fk_tr_project FOREIGN KEY (project_id) REFERENCES projects(id)
) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS test_run_cases (
  test_run_id INT NOT NULL, case_id INT NOT NULL, PRIMARY KEY (test_run_id, case_id),
  CONSTRAINT fk_trc_run FOREIGN KEY (test_run_id) REFERENCES test_runs(id) ON DELETE CASCADE,
  CONSTRAINT fk_trc_case FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS test_run_execs (
  id INT AUTO_INCREMENT PRIMARY KEY, test_run_id INT NOT NULL, started_at VARCHAR(32) NOT NULL, started_by VARCHAR(64),
  schedule_id INT NULL, env_name VARCHAR(40), total INT NOT NULL DEFAULT 0, note VARCHAR(255),
  KEY ix_tre_run (test_run_id, id),
  CONSTRAINT fk_tre_run FOREIGN KEY (test_run_id) REFERENCES test_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS schedules (
  id INT AUTO_INCREMENT PRIMARY KEY, test_run_id INT NOT NULL, kind VARCHAR(8) NOT NULL, run_at VARCHAR(32) NULL,
  at_time VARCHAR(5) NULL, weekdays VARCHAR(20) NULL, env_id INT NULL, active TINYINT NOT NULL DEFAULT 1,
  next_run_at VARCHAR(32) NULL, last_run_at VARCHAR(32) NULL, last_exec_id INT NULL, last_note VARCHAR(255),
  created_by VARCHAR(64), created_at VARCHAR(32) NOT NULL,
  CONSTRAINT fk_sch_run FOREIGN KEY (test_run_id) REFERENCES test_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB""",
)
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
MISSED_AFTER = timedelta(hours=6)   # server was off longer than this: skip the missed slot instead of running late


def init(con):
    for stmt in SCHEMA:
        con.execute(stmt)
    n = con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='runs' AND COLUMN_NAME='exec_id'").fetchone()['n']
    if not n:
        con.execute('ALTER TABLE runs ADD COLUMN exec_id INT NULL, ADD KEY ix_runs_exec (exec_id)')


# ---------------------------------------------------------------- cases, results, schedule maths

def resolve_cases(con, tr, ignore_ready=False):
    """The cases this Test Run would run right now (ignore_ready=True: every chosen case, runnable or not)."""
    live_folder = '(c.folder_id IS NULL OR c.folder_id NOT IN (SELECT id FROM folders WHERE archived=1))'
    if tr['mode'] == 'fixed':
        sql = ('SELECT c.* FROM test_run_cases t JOIN cases c ON c.id=t.case_id WHERE t.test_run_id=?', [tr['id']])
    else:
        where, params = ['c.project_id=?', live_folder], [tr['project_id']]
        if tr['folder_id']:
            where.append('c.folder_id=?')
            params.append(tr['folder_id'])
        sql = (f'SELECT c.* FROM cases c WHERE {" AND ".join(where)}', params)
    q, params = sql
    if tr['only_ready'] and not ignore_ready:
        q += " AND c.qa_status='ready'"
    return con.execute(q + ' ORDER BY c.tc_no', params).fetchall()


def exec_summary(con, exec_ids):
    """{exec_id: {total, passed, failed, other, running, status}} from the runs of each execution."""
    if not exec_ids:
        return {}
    rows = con.execute(
        'SELECT exec_id, COUNT(*) AS total, SUM(status=\'PASSED\') AS passed, SUM(status=\'FAILED\') AS failed, '
        'SUM(status IN (\'PARTIAL\', \'ERROR\')) AS other, SUM(status IN (\'running\', \'queued\')) AS running FROM runs '
        f'WHERE exec_id IN ({",".join("?" * len(exec_ids))}) GROUP BY exec_id', list(exec_ids)).fetchall()
    out = {}
    for r in rows:
        d = {k: int(r[k] or 0) for k in ('total', 'passed', 'failed', 'other', 'running')}
        d['status'] = ('running' if d['running'] else 'PASSED' if d['passed'] == d['total']
                       else 'FAILED' if d['failed'] else 'PARTIAL')
        out[r['exec_id']] = d
    return out


def next_run(kind, run_at, at_time, weekdays, after):
    """Next 'YYYY-MM-DD HH:MM:SS' strictly after `after` (datetime), or None when there is none."""
    if kind == 'once':
        return run_at if run_at and run_at > after.strftime('%Y-%m-%d %H:%M:%S') else None
    h, m = map(int, at_time.split(':'))
    days = [int(x) for x in (weekdays or '').split(',') if x != ''] if kind == 'weekly' else list(range(7))
    for i in range(0, 8):
        d = after.date() + timedelta(days=i)
        cand = datetime(d.year, d.month, d.day, h, m)
        if cand > after and d.weekday() in days:
            return cand.strftime('%Y-%m-%d %H:%M:%S')
    return None


def describe(s):
    if s['kind'] == 'once':
        return f"Once · {s['run_at'][:16]}"
    if s['kind'] == 'daily':
        return f"Every day · {s['at_time']}"
    days = [DAYS[int(x)] for x in (s['weekdays'] or '').split(',') if x != '']
    return f"Every {', '.join(days)} · {s['at_time']}"


# ---------------------------------------------------------------- blueprint

def make_blueprint(deps):
    """deps: con, login_required, current_user, start_case_runs(con, cases, env, username, exec_id) -> run ids."""
    con, login_required, current_user, start_case_runs = (deps['con'], deps['login_required'], deps['current_user'],
                                                          deps['start_case_runs'])
    bp = Blueprint('testruns', __name__)

    @bp.app_template_filter('until')
    def until(ts):
        """'2026-10-01 21:00:00' -> 'in 3 hours' (or 'due now')."""
        try:
            secs = (datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S') - datetime.now()).total_seconds()
        except ValueError:
            return ''
        if secs <= 60:
            return 'due now'
        for size, unit in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
            if secs >= size:
                n = int(secs // size)
                return f'in {n} {unit}{"s" if n != 1 else ""}'
        return 'soon'

    def tr_or_404(tid):
        tr = con().execute('SELECT t.*, p.name AS project_name, f.name AS folder_name, e.name AS env_name '
                           'FROM test_runs t JOIN projects p ON p.id=t.project_id LEFT JOIN folders f ON f.id=t.folder_id '
                           'LEFT JOIN environments e ON e.id=t.env_id WHERE t.id=?', (tid,)).fetchone()
        return tr or abort(404)

    def execute(c, tr, username, env_id=None, schedule_id=None):
        """Starts one execution. Returns (exec_id or None, message)."""
        cases = resolve_cases(c, tr)
        if not cases:
            return None, ('No test cases match this Test Run' + (' (none are Ready for QA)' if tr['only_ready'] else '') + '.')
        env = envs.get(c, env_id or tr['env_id'])
        with c:
            eid = c.execute('INSERT INTO test_run_execs(test_run_id, started_at, started_by, schedule_id, env_name, total) '
                            'VALUES (?,?,?,?,?,?)', (tr['id'], db.now(), username, schedule_id,
                                                     env['name'] if env else None, len(cases))).lastrowid
        busy = {r['case_id'] for r in c.execute(
            f"SELECT case_id FROM runs WHERE status IN ('running', 'queued') AND case_id IN ({','.join('?' * len(cases))})",
            [x['id'] for x in cases]).fetchall()}
        todo = [x for x in cases if x['id'] not in busy]
        skipped = len(cases) - len(todo)
        start_case_runs(c, todo, env, username, eid)
        msg = (f'Started {len(todo)} test case(s) on {env["name"] if env else "the default environment"}: '
               'they run one after another in the order shown.')
        if skipped:
            msg += f' {skipped} skipped: already running.'
        return eid, msg

    # ---- pages ----
    @bp.route('/test-runs')
    @login_required
    def index():
        c = con()
        tab = 'scheduled' if request.args.get('tab') == 'scheduled' else 'runs'
        q = request.args.get('q', '').strip()
        creator = request.args.get('by', '').strip()
        like = f'%{q}%'
        runs = c.execute('SELECT t.*, p.name AS project_name, f.name AS folder_name, e.name AS env_name, '
                         '(SELECT MAX(id) FROM test_run_execs x WHERE x.test_run_id=t.id) AS last_exec, '
                         '(SELECT COUNT(*) FROM test_run_execs x WHERE x.test_run_id=t.id) AS n_execs, '
                         '(SELECT COUNT(*) FROM schedules s WHERE s.test_run_id=t.id AND s.active=1) AS n_sched '
                         'FROM test_runs t JOIN projects p ON p.id=t.project_id LEFT JOIN folders f ON f.id=t.folder_id '
                         'LEFT JOIN environments e ON e.id=t.env_id WHERE t.archived=0 AND (?=\'\' OR t.name LIKE ?) '
                         'AND (?=\'\' OR t.created_by=?) ORDER BY t.updated_at DESC', (q, like, creator, creator)).fetchall()
        summ = exec_summary(c, [r['last_exec'] for r in runs if r['last_exec']])
        counts = {r['id']: len(resolve_cases(c, r)) for r in runs}
        status = request.args.get('status', '')
        if status:
            runs = [r for r in runs if (summ.get(r['last_exec'], {}).get('status') if r['last_exec'] else 'never') == status]
        scheds = c.execute('SELECT s.*, t.name AS run_name, t.project_id, e.name AS env_name FROM schedules s '
                           'JOIN test_runs t ON t.id=s.test_run_id LEFT JOIN environments e ON e.id=s.env_id '
                           'WHERE (?=\'\' OR t.name LIKE ?) ORDER BY s.active DESC, s.next_run_at', (q, like)).fetchall()
        s_summ = exec_summary(c, [s['last_exec_id'] for s in scheds if s['last_exec_id']])
        projects = c.execute('SELECT id, name FROM projects ORDER BY name').fetchall()
        folders = c.execute('SELECT id, project_id, name FROM folders WHERE archived=0 ORDER BY name').fetchall()
        creators = [r[0] for r in c.execute('SELECT DISTINCT created_by FROM test_runs WHERE created_by IS NOT NULL ORDER BY 1')]
        all_runs = c.execute('SELECT id, name FROM test_runs WHERE archived=0 ORDER BY name').fetchall()
        return render_template('test_runs.html', tab=tab, runs=runs, summ=summ, counts=counts, scheds=scheds,
                               s_summ=s_summ, describe=describe, projects=projects, folders=folders, q=q, by=creator,
                               status=status, creators=creators, all_runs=all_runs, days=DAYS,
                               today=date.today().isoformat(), total=len(runs))

    @bp.route('/test-runs/<int:tid>')
    @login_required
    def detail(tid):
        c = con()
        tr = tr_or_404(tid)
        cases = resolve_cases(c, tr, ignore_ready=True)
        skip_ids = {x['id'] for x in cases if tr['only_ready'] and x['qa_status'] != 'ready'}
        n_run = len(cases) - len(skip_ids)
        execs = c.execute('SELECT * FROM test_run_execs WHERE test_run_id=? ORDER BY id DESC LIMIT 30', (tid,)).fetchall()
        summ = exec_summary(c, [e['id'] for e in execs])
        sel = request.args.get('exec', type=int) or (execs[0]['id'] if execs else None)
        results = {}
        if sel:
            for r in c.execute('SELECT * FROM runs WHERE exec_id=?', (sel,)).fetchall():
                results[r['case_id']] = r
        scheds = c.execute('SELECT s.*, e.name AS env_name FROM schedules s LEFT JOIN environments e ON e.id=s.env_id '
                           'WHERE s.test_run_id=? ORDER BY s.id', (tid,)).fetchall()
        any_running = any(r['status'] in ('running', 'queued') for r in results.values())
        folders = c.execute('SELECT id, name FROM folders WHERE project_id=? AND archived=0 ORDER BY name', (tr['project_id'],)).fetchall()
        pick_cases = c.execute(
            'SELECT c.id, c.tc_no, c.title, c.folder_id, c.qa_status, lr.status AS last FROM cases c '
            'LEFT JOIN runs lr ON lr.id=(SELECT MAX(id) FROM runs WHERE case_id=c.id) '
            'WHERE c.project_id=? AND (c.folder_id IS NULL OR c.folder_id NOT IN (SELECT id FROM folders WHERE archived=1)) '
            'ORDER BY c.tc_no', (tr['project_id'],)).fetchall()
        picker = {'folders': [{'id': f['id'], 'name': f['name']} for f in folders],
                  'cases': [{'id': x['id'], 'tc': x['tc_no'], 'title': x['title'], 'folder': x['folder_id'],
                             'ready': x['qa_status'] == 'ready', 'last': x['last']} for x in pick_cases],
                  'selected': [x['id'] for x in cases] if tr['mode'] == 'fixed' else [x['id'] for x in cases if x['id'] not in skip_ids]}
        return render_template('test_run.html', tr=tr, folders=folders, picker=picker, skip_ids=skip_ids, n_run=n_run, cases=cases, execs=execs, summ=summ, sel=sel, results=results,
                               scheds=scheds, describe=describe, any_running=any_running, days=DAYS,
                               today=date.today().isoformat())

    # ---- create / edit / run ----
    def _read_form(existing=None):
        f = request.form
        name = f.get('name', '').strip()[:160]
        pid = f.get('project_id', type=int) or (existing['project_id'] if existing else None)
        if not name:
            return None, 'Give the Test Run a name (e.g. "Nightly QA - Retail").'
        if not pid or not con().execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone():
            return None, 'Pick a project.'
        folder_id = f.get('folder_id', type=int) or None
        if folder_id and not con().execute('SELECT 1 FROM folders WHERE id=? AND project_id=?', (folder_id, pid)).fetchone():
            return None, 'That folder is not in the chosen project.'
        env_id = f.get('env_id', type=int) or None
        if env_id and not con().execute('SELECT 1 FROM environments WHERE id=?', (env_id,)).fetchone():
            env_id = None
        return {'name': name, 'project_id': pid, 'folder_id': folder_id, 'env_id': env_id,
                'only_ready': 1 if f.get('only_ready') else 0, 'description': f.get('description', '').strip()[:500] or None}, None

    @bp.route('/test-runs/new', methods=['POST'])
    @login_required
    def create():
        v, err = _read_form()
        back = request.form.get('back') or url_for('testruns.index')
        if err:
            flash(err, 'danger')
            return redirect(back if back.startswith('/') else url_for('testruns.index'))
        ids = [int(x) for x in request.form.getlist('ids') if x.isdigit()]
        pick = request.form.get('selection') == 'pick'
        mode = 'fixed' if ids or pick else 'dynamic'
        user = current_user()['username']
        c = con()
        with c:
            tid = c.execute('INSERT INTO test_runs(project_id, name, description, mode, folder_id, only_ready, env_id, created_by, '
                            'created_at, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (v['project_id'], v['name'], v['description'], mode, None if mode == 'fixed' else v['folder_id'],
                             v['only_ready'], v['env_id'], user, db.now(), user, db.now())).lastrowid
            if ids:
                ok = [r['id'] for r in c.execute(f'SELECT id FROM cases WHERE project_id=? AND id IN ({",".join("?" * len(ids))})',
                                                 [v['project_id'], *ids]).fetchall()]
                c.executemany('INSERT INTO test_run_cases(test_run_id, case_id) VALUES (?,?)', [(tid, i) for i in ok])
        flash(f'Test Run "{v["name"]}" created.' + (' Now pick its test cases.' if pick and not ids else ''), 'success')
        return redirect(url_for('testruns.detail', tid=tid, pick=1) if pick and not ids else url_for('testruns.detail', tid=tid))

    @bp.route('/test-runs/<int:tid>/edit', methods=['POST'])
    @login_required
    def edit(tid):
        tr = tr_or_404(tid)
        v, err = _read_form(tr)
        if err:
            flash(err, 'danger')
        else:
            with con():
                con().execute('UPDATE test_runs SET name=?, description=?, folder_id=?, only_ready=?, env_id=?, updated_by=?, '
                              'updated_at=? WHERE id=?', (v['name'], v['description'], v['folder_id'] if tr['mode'] == 'dynamic' else None,
                                                          v['only_ready'], v['env_id'], current_user()['username'], db.now(), tid))
            flash('Test Run saved.', 'success')
        return redirect(url_for('testruns.detail', tid=tid))

    @bp.route('/test-runs/<int:tid>/cases', methods=['POST'])
    @login_required
    def set_cases(tid):
        """Case picker: the Test Run becomes a fixed list of exactly these cases (from any folders)."""
        tr = tr_or_404(tid)
        ids = [int(x) for x in request.form.getlist('ids') if x.isdigit()]
        c = con()
        ok = [r['id'] for r in c.execute(f'SELECT id FROM cases WHERE project_id=? AND id IN ({",".join("?" * len(ids))})',
                                         [tr['project_id'], *ids]).fetchall()] if ids else []
        with c:
            c.execute('DELETE FROM test_run_cases WHERE test_run_id=?', (tid,))
            c.executemany('INSERT INTO test_run_cases(test_run_id, case_id) VALUES (?,?)', [(tid, i) for i in ok])
            only_ready = 1 if request.form.get('only_ready') else 0
            c.execute("UPDATE test_runs SET mode='fixed', folder_id=NULL, only_ready=?, updated_by=?, updated_at=? WHERE id=?",
                      (only_ready, current_user()['username'], db.now(), tid))
        n_f = len({r['folder_id'] for r in c.execute(f'SELECT DISTINCT folder_id FROM cases WHERE id IN ({",".join("?" * len(ok))})', ok).fetchall()}) if ok else 0
        skipped = c.execute(f"SELECT COUNT(*) AS n FROM cases WHERE qa_status<>'ready' AND id IN ({','.join('?' * len(ok))})", ok).fetchone()['n'] if ok and only_ready else 0
        flash(f'{len(ok)} test case(s) from {n_f} folder(s) saved in this Test Run.'
              + (f' {skipped} of them are still Draft and will be skipped because "Ready for QA only" is on.' if skipped else ''),
              'warning' if skipped else 'success')
        return redirect(url_for('testruns.detail', tid=tid))

    @bp.route('/test-runs/<int:tid>/delete', methods=['POST'])
    @login_required
    def delete(tid):
        tr = tr_or_404(tid)
        with con():
            con().execute('UPDATE test_runs SET archived=1, updated_at=? WHERE id=?', (db.now(), tid))
            con().execute('UPDATE schedules SET active=0 WHERE test_run_id=?', (tid,))
        flash(f'Test Run "{tr["name"]}" deleted and its schedules stopped. Past results stay on each test case.', 'success')
        return redirect(url_for('testruns.index'))

    @bp.route('/test-runs/<int:tid>/execute', methods=['POST'])
    @login_required
    def run_now(tid):
        tr = tr_or_404(tid)
        eid, msg = execute(con(), tr, current_user()['username'], request.form.get('env_id', type=int))
        flash(msg, 'info' if eid else 'warning')
        return redirect(url_for('testruns.detail', tid=tid, exec=eid) if eid else url_for('testruns.detail', tid=tid))

    # ---- schedules ----
    @bp.route('/schedules/new', methods=['POST'])
    @login_required
    def schedule_new():
        f = request.form
        tid = f.get('test_run_id', type=int)
        tr = con().execute('SELECT * FROM test_runs WHERE id=? AND archived=0', (tid,)).fetchone() if tid else None
        back = f.get('back') if (f.get('back') or '').startswith('/') else url_for('testruns.index', tab='scheduled')
        kind = f.get('kind')
        at_time = f.get('at_time', '').strip()
        run_at, weekdays = None, None
        if not tr:
            err = 'Pick the Test Run to schedule.'
        elif kind not in ('once', 'daily', 'weekly'):
            err = 'Pick how often it runs.'
        elif not TIME_RE.match(at_time):
            err = 'Pick a time (HH:MM).'
        else:
            err = None
            if kind == 'once':
                d = f.get('run_date', '')
                try:
                    run_at = datetime.strptime(f'{d} {at_time}', '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M:%S')
                except ValueError:
                    err = 'Pick the date for the run.'
                if run_at and run_at <= db.now():
                    err = 'That date and time is already past.'
            elif kind == 'weekly':
                days = sorted({int(x) for x in f.getlist('weekdays') if x.isdigit() and 0 <= int(x) <= 6})
                weekdays = ','.join(map(str, days))
                if not days:
                    err = 'Pick at least one day of the week.'
        if err:
            flash(err, 'danger')
            return redirect(back)
        env_id = f.get('env_id', type=int) or tr['env_id']
        nxt = next_run(kind, run_at, at_time, weekdays, datetime.now())
        with con():
            con().execute('INSERT INTO schedules(test_run_id, kind, run_at, at_time, weekdays, env_id, active, next_run_at, '
                          'created_by, created_at) VALUES (?,?,?,?,?,?,1,?,?,?)',
                          (tid, kind, run_at, at_time, weekdays, env_id, nxt, current_user()['username'], db.now()))
        flash(f'Scheduled "{tr["name"]}": next run {nxt[:16] if nxt else "-"}. This PC / server must be on at that time.', 'success')
        return redirect(back)

    @bp.route('/schedules/<int:sid>/<action>', methods=['POST'])
    @login_required
    def schedule_action(sid, action):
        s = con().execute('SELECT s.*, t.name AS run_name FROM schedules s JOIN test_runs t ON t.id=s.test_run_id WHERE s.id=?',
                          (sid,)).fetchone() or abort(404)
        back = request.form.get('back') if (request.form.get('back') or '').startswith('/') else url_for('testruns.index', tab='scheduled')
        with con():
            if action == 'pause':
                con().execute('UPDATE schedules SET active=0 WHERE id=?', (sid,))
                flash(f'Schedule for "{s["run_name"]}" paused.', 'success')
            elif action == 'resume':
                nxt = next_run(s['kind'], s['run_at'], s['at_time'], s['weekdays'], datetime.now())
                if not nxt:
                    flash('This one-time schedule is already past. Create a new schedule.', 'warning')
                else:
                    con().execute('UPDATE schedules SET active=1, next_run_at=? WHERE id=?', (nxt, sid))
                    flash(f'Schedule resumed: next run {nxt[:16]}.', 'success')
            elif action == 'delete':
                con().execute('DELETE FROM schedules WHERE id=?', (sid,))
                flash('Schedule deleted.', 'success')
            else:
                abort(404)
        return redirect(back)

    # ---- the scheduler ----
    def tick():
        now = datetime.now()
        c = db.connect()   # autocommit: each statement lands at once, so the run threads see their rows
        try:
            due = c.execute('SELECT * FROM schedules WHERE active=1 AND next_run_at IS NOT NULL AND next_run_at <= ?',
                            (now.strftime('%Y-%m-%d %H:%M:%S'),)).fetchall()
            for s in due:
                nxt = next_run(s['kind'], s['run_at'], s['at_time'], s['weekdays'], now)
                late = now - datetime.strptime(s['next_run_at'], '%Y-%m-%d %H:%M:%S')
                # claim the slot first so a slow run can never start twice
                claimed = c.execute('UPDATE schedules SET next_run_at=?, active=?, last_run_at=? WHERE id=? AND next_run_at=?',
                                    (nxt, 1 if nxt else 0, now.strftime('%Y-%m-%d %H:%M:%S'), s['id'], s['next_run_at'])).rowcount
                if not claimed:
                    continue
                tr = c.execute('SELECT * FROM test_runs WHERE id=? AND archived=0', (s['test_run_id'],)).fetchone()
                if not tr:
                    c.execute('UPDATE schedules SET active=0, last_note=? WHERE id=?', ('Test Run was deleted', s['id']))
                elif late > MISSED_AFTER:
                    c.execute('UPDATE schedules SET last_note=? WHERE id=?',
                              (f'Missed {s["next_run_at"][:16]}: server was off', s['id']))
                else:
                    eid, msg = execute(c, tr, s['created_by'] or 'schedule', s['env_id'], s['id'])
                    c.execute('UPDATE schedules SET last_exec_id=COALESCE(?, last_exec_id), last_note=? WHERE id=?',
                              (eid, msg[:255], s['id']))
        finally:
            c.close()

    def loop():
        while True:
            try:
                tick()
            except Exception as e:   # keep the scheduler alive; the next tick retries
                print(' * scheduler error:', e)
            time.sleep(30)

    bp.start_scheduler = lambda: threading.Thread(target=loop, name='lth-scheduler', daemon=True).start()
    bp.tick = tick
    return bp
