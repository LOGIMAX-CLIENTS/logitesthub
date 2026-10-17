"""Coverage: per module (the app's menu, see modules.py) how many test cases it has, how they did in their latest run,
and which modules have no test at all - so the team knows where to write tests next.

A case counts by its LATEST run: passed / failed / partial (some steps needed AI). Cases never run, and cases whose
latest run is older than STALE_DAYS, are shown apart. No AI, no writes.

Registered from app.py with register(app, con, login_required, project_or_404).
"""
import datetime

from flask import Blueprint, render_template, request

import modules

STALE_DAYS = 30
FILTERS = [   # key, label, help
    ('', 'All', 'Every module'),
    ('none', 'No tests', 'Modules (and sub-modules) without a single test case: where to write tests next'),
    ('failing', 'Failing', 'A case whose latest run failed'),
    ('notrun', 'Not run', f'Cases never run, or not run in the last {STALE_DAYS} days'),
]


def _stats(con, pid):
    """folder id (None = no module) -> {cases, passed, failed, partial, never, stale, last}."""
    since = (datetime.datetime.now() - datetime.timedelta(days=STALE_DAYS)).strftime('%Y-%m-%d')
    rows = con.execute(
        'SELECT c.id, c.folder_id, r.status, r.started_at FROM cases c '
        'LEFT JOIN (SELECT case_id, MAX(id) AS mid FROM runs GROUP BY case_id) x ON x.case_id = c.id '
        'LEFT JOIN runs r ON r.id = x.mid WHERE c.project_id=?', (pid,)).fetchall()
    out = {}
    for r in rows:
        s = out.setdefault(r['folder_id'], {'cases': 0, 'passed': 0, 'failed': 0, 'partial': 0, 'never': 0, 'stale': 0, 'last': ''})
        s['cases'] += 1
        if not r['status']:
            s['never'] += 1
            continue
        s[{'PASSED': 'passed', 'FAILED': 'failed'}.get(r['status'], 'partial')] += 1
        if (r['started_at'] or '') < since:
            s['stale'] += 1
        s['last'] = max(s['last'], r['started_at'] or '')
    return out


def _sum(parts):
    t = {'cases': 0, 'passed': 0, 'failed': 0, 'partial': 0, 'never': 0, 'stale': 0, 'last': ''}
    for p in parts:
        for k in t:
            t[k] = max(t[k], p[k]) if k == 'last' else t[k] + p[k]
    return t


def _state(s):
    if not s['cases']:
        return 'none'
    if s['failed']:
        return 'failing'
    if s['never'] or s['stale']:
        return 'notrun'
    return 'partial' if s['partial'] else 'ok'


def register(app, con, login_required, project_or_404):
    bp = Blueprint('coverage', __name__)

    @bp.route('/projects/<int:pid>/coverage')
    @login_required
    def page(pid):
        p = project_or_404(pid)
        want = request.args.get('show', '')
        stats = _stats(con(), pid)
        empty = _sum([])
        folders = [dict(f) for f in con().execute(
            'SELECT id, parent_id, name, sort FROM folders WHERE project_id=? AND archived=0', (pid,)).fetchall()]
        mods = []
        for m in modules.tree(folders):
            kids = []
            for k in m['children']:
                ks = stats.get(k['id'], empty)
                kids.append(dict(k, s=ks, state=_state(ks)))
            own = stats.get(m['id'], empty)
            s = _sum([own] + [k['s'] for k in kids])
            states = {_state(s)} | {k['state'] for k in kids}
            if want and want not in states:
                continue
            if want:   # only the sub-modules the filter is about
                kids = [k for k in kids if k['state'] == want]
            mods.append(dict(m, s=s, own=own, state=_state(s), kids=kids, n_kids=len(m['children']),
                             kids_tested=sum(1 for k in m['children'] if stats.get(k['id'], empty)['cases'])))
        loose = stats.get(None)
        all_s = _sum(list(stats.values()))
        top = modules.tree(folders)
        tested = sum(1 for m in top if _sum([stats.get(m['id'], empty)] + [stats.get(k['id'], empty) for k in m['children']])['cases'])
        ran = all_s['passed'] + all_s['failed'] + all_s['partial']
        tiles = {'modules': len(top), 'tested': tested, 'cases': all_s['cases'], 'ran': ran,
                 'passed': all_s['passed'], 'failed': all_s['failed'], 'never': all_s['never'], 'stale': all_s['stale'],
                 'pass_pct': round(100 * all_s['passed'] / ran) if ran else None}
        return render_template('coverage.html', p=p, mods=mods, loose=loose, tiles=tiles, filters=FILTERS, want=want,
                               stale_days=STALE_DAYS)

    app.register_blueprint(bp)
