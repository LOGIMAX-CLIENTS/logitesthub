"""User Report: per user, test cases created, test runs executed and AI tokens used in a period.
Clicking a count drills down to the matching test cases / runs. Admin only.

Registered from app.py with make_blueprint(con, admin_required) so this module never imports app
(app.py runs as __main__; importing it again would start a second copy).
"""
import csv
import io
import re
from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
RUN_STATUSES = ('PASSED', 'FAILED', 'PARTIAL', 'ERROR', 'running')
NO_USER = '-'      # drill-down key for rows whose created_by / run_by was never recorded


def _date(s, default):
    """A real YYYY-MM-DD date from the query string, else the default (2026-99-99 matches the pattern but is no date)."""
    if DATE_RE.match(s or ''):
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            pass
    return default.isoformat()


def _filters(a):
    """Period + project from the query string; defaults to the last 30 days like AI Usage."""
    d_from = _date(a.get('from'), date.today() - timedelta(days=29))
    d_to = _date(a.get('to'), date.today())
    today = date.today().isoformat()
    d_from, d_to = min(d_from, today), min(d_to, today)   # no future dates: nothing can have happened yet
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    d_end = (date.fromisoformat(d_to) + timedelta(days=1)).isoformat()   # timestamps are 'YYYY-MM-DD HH:MM:SS' text
    return d_from, d_to, d_end, a.get('project', type=int)


def _user_cond(col, user):
    return (f'{col} IS NULL', []) if user == NO_USER else (f'{col}=?', [user])


def _csv(name, header, rows):
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(header)
    wr.writerows(rows)
    from flask import current_app
    return current_app.response_class(buf.getvalue(), mimetype='text/csv',
                                      headers={'Content-Disposition': f'attachment; filename={name}.csv'})


def make_blueprint(con, admin_required):
    bp = Blueprint('reports', __name__, url_prefix='/reports')

    def summary(d_from, d_end, pid, dept=''):
        c = con()
        pc, pp = (' AND c.project_id=?', [pid]) if pid else ('', [])
        cases = c.execute('SELECT c.created_by AS u, COUNT(*) AS n FROM cases c '
                          f'WHERE c.created_at >= ? AND c.created_at < ?{pc} GROUP BY c.created_by',
                          [d_from, d_end] + pp).fetchall()
        runs = c.execute("SELECT r.run_by AS u, COUNT(*) AS n, SUM(r.status='PASSED') AS passed, "
                         "SUM(r.status='FAILED') AS failed, SUM(r.status NOT IN ('PASSED','FAILED')) AS other "
                         'FROM runs r JOIN cases c ON c.id=r.case_id '
                         f'WHERE r.started_at >= ? AND r.started_at < ?{pc} GROUP BY r.run_by',
                         [d_from, d_end] + pp).fetchall()
        ai = c.execute('SELECT u.username AS u, SUM(u.calls) AS calls, SUM(u.input_tokens) AS tin, '
                       'SUM(u.output_tokens) AS tout, SUM(u.cost_usd) AS cost FROM ai_usage u '
                       f'WHERE u.at >= ? AND u.at < ?{" AND u.project_id=?" if pid else ""} GROUP BY u.username',
                       [d_from, d_end] + pp).fetchall()
        people = {r['username']: r['name'] for r in c.execute('SELECT username, name FROM users')}
        depts = {r['username']: r['department'] for r in c.execute('SELECT username, department FROM users')}
        rows = {}

        def row(u):
            key = u if u else NO_USER
            if key not in rows:
                rows[key] = {'key': key, 'username': u, 'name': people.get(u) if u else None, 'is_user': u in people,
                             'department': depts.get(u) if u else None,
                             'cases': 0, 'runs': 0, 'passed': 0, 'failed': 0, 'other': 0,
                             'calls': 0, 'tin': 0, 'tout': 0, 'cost': 0.0}
            return rows[key]

        for u in people:
            row(u)
        for r in cases:
            row(r['u'])['cases'] = r['n']
        for r in runs:
            row(r['u']).update(runs=r['n'], passed=r['passed'] or 0, failed=r['failed'] or 0, other=r['other'] or 0)
        for r in ai:
            row(r['u']).update(calls=r['calls'] or 0, tin=r['tin'] or 0, tout=r['tout'] or 0, cost=float(r['cost'] or 0))
        out = sorted(rows.values(), key=lambda r: (-(r['cases'] + r['runs'] + r['calls']), r['key']))
        if dept:   # one department ('-' = users without a department, incl. system rows)
            out = [r for r in out if (r['department'] or '-') == dept]
        total = {k: sum(r[k] for r in out) for k in ('cases', 'runs', 'passed', 'failed', 'other', 'calls', 'tin', 'tout', 'cost')}
        return out, total

    def case_rows(user, d_from, d_end, pid):
        cond, params = _user_cond('c.created_by', user)
        where = [cond, 'c.created_at >= ?', 'c.created_at < ?']
        params += [d_from, d_end]
        if pid:
            where.append('c.project_id=?')
            params.append(pid)
        return con().execute(
            'SELECT c.id, c.tc_no, c.title, c.created_at, p.name AS project, f.name AS folder, '
            '(SELECT COUNT(*) FROM runs x WHERE x.case_id=c.id) AS runs, lr.status AS last_status, lr.started_at AS last_run, '
            'COALESCE(au.calls, 0) AS calls, COALESCE(au.tok, 0) AS tok, COALESCE(au.cost, 0) AS cost '
            'FROM cases c JOIN projects p ON p.id=c.project_id LEFT JOIN folders f ON f.id=c.folder_id '
            'LEFT JOIN runs lr ON lr.id=(SELECT MAX(id) FROM runs WHERE case_id=c.id) '
            'LEFT JOIN (SELECT case_id, SUM(calls) AS calls, SUM(input_tokens + output_tokens) AS tok, SUM(cost_usd) AS cost '
            '           FROM ai_usage WHERE case_id IS NOT NULL GROUP BY case_id) au ON au.case_id=c.id '
            f'WHERE {" AND ".join(where)} ORDER BY c.id DESC LIMIT 1000', params).fetchall()

    def run_rows(user, d_from, d_end, pid, status):
        cond, params = _user_cond('r.run_by', user)
        where = [cond, 'r.started_at >= ?', 'r.started_at < ?']
        params += [d_from, d_end]
        if pid:
            where.append('c.project_id=?')
            params.append(pid)
        if status:
            where.append('r.status=?')
            params.append(status)
        return con().execute(
            'SELECT r.id, r.case_id, r.status, r.started_at, r.duration_ms, r.passed, r.failed, r.total, r.error, '
            'c.tc_no, c.title, p.name AS project, '
            'COALESCE(au.calls, 0) AS calls, COALESCE(au.tok, 0) AS tok, COALESCE(au.cost, 0) AS cost '
            'FROM runs r JOIN cases c ON c.id=r.case_id JOIN projects p ON p.id=c.project_id '
            'LEFT JOIN (SELECT run_id, SUM(calls) AS calls, SUM(input_tokens + output_tokens) AS tok, SUM(cost_usd) AS cost '
            '           FROM ai_usage WHERE run_id IS NOT NULL GROUP BY run_id) au ON au.run_id=r.id '
            f'WHERE {" AND ".join(where)} ORDER BY r.id DESC LIMIT 1000', params).fetchall()

    @bp.route('/users')
    @admin_required
    def users():
        import pricing
        a = request.args
        d_from, d_to, d_end, pid = _filters(a)
        detail = a.get('detail', '')
        user = a.get('user', '').strip()
        status = a.get('status', '') if a.get('status', '') in RUN_STATUSES else ''
        if detail and (detail not in ('cases', 'runs') or not user):
            abort(400)
        projects = con().execute('SELECT id, name FROM projects ORDER BY name').fetchall()
        import settings
        dept = a.get('dept', '').strip()
        dept = dept if dept in settings.DEPARTMENTS or dept == '-' else ''
        ctx = dict(dept=dept, d_from=d_from, d_to=d_to, today=date.today().isoformat(), pid=pid, projects=projects, inr=pricing.usd_inr(con()),
                   detail=detail, user=user, status=status, statuses=RUN_STATUSES, no_user=NO_USER)
        fmt_user = '(not recorded)' if user == NO_USER else user
        csv_out = a.get('format') == 'csv'

        if detail == 'cases':
            rows = case_rows(user, d_from, d_end, pid)
            if csv_out:
                return _csv(f'user-{fmt_user}-cases-{d_from}-to-{d_to}',
                            ['Test case', 'Title', 'Project', 'Folder', 'Created', 'Runs', 'Last result', 'AI calls', 'AI tokens', 'Cost USD'],
                            [[f"TC-{r['tc_no']}", r['title'], r['project'], r['folder'] or '', r['created_at'], r['runs'],
                              r['last_status'] or 'Never run', r['calls'], r['tok'], f"{r['cost']:.4f}"] for r in rows])
            return render_template('user_report.html', rows=rows, **ctx)
        if detail == 'runs':
            rows = run_rows(user, d_from, d_end, pid, status)
            if csv_out:
                return _csv(f'user-{fmt_user}-runs-{d_from}-to-{d_to}',
                            ['Run', 'Test case', 'Title', 'Project', 'Result', 'Started', 'Steps passed', 'Steps total',
                             'Duration ms', 'AI calls', 'AI tokens', 'Cost USD'],
                            [[r['id'], f"TC-{r['tc_no']}", r['title'], r['project'], r['status'], r['started_at'],
                              r['passed'] if r['passed'] is not None else '', r['total'] if r['total'] is not None else '',
                              r['duration_ms'] if r['duration_ms'] is not None else '', r['calls'], r['tok'], f"{r['cost']:.4f}"]
                             for r in rows])
            return render_template('user_report.html', rows=rows, **ctx)

        rows, total = summary(d_from, d_end, pid, dept)
        if csv_out:
            return _csv(f'user-report-{d_from}-to-{d_to}',
                        ['User', 'Name', 'Department', 'Test cases created', 'Test runs', 'Passed', 'Failed', 'Other', 'AI calls',
                         'Input tokens', 'Output tokens', 'Total tokens', 'Cost USD', f'Cost INR (@{pricing.usd_inr(con()):g})'],
                        [[r['username'] or '(not recorded)', r['name'] or '', r['department'] or '', r['cases'], r['runs'], r['passed'], r['failed'],
                          r['other'], r['calls'], r['tin'], r['tout'], r['tin'] + r['tout'], f"{r['cost']:.4f}",
                          f"{r['cost'] * pricing.usd_inr(con()):.2f}"] for r in rows])
        return render_template('user_report.html', rows=rows, total=total, **ctx)

    return bp
