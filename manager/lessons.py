"""Lessons learned per project: why a test failed or what went wrong while testing, and how to avoid it next time.

Written by people on the Lessons page or by an agent through the Tester CLI (`tester lesson add`), read back by the
agent before it writes or runs a test (`tester lessons`), and added to the Test Assistant / Generate with AI prompts so
AI-written test cases avoid the same mistakes. One teammate's lesson reaches everyone.

Registered from app.py with register(app, con, login_required, current_user, project_or_404).
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import db

KINDS = [   # key, label, meaning
    ('app_bug', 'App bug', 'The application is wrong: fix the code, keep the test.'),
    ('test_outdated', 'Test out of date', 'The screen changed or the step was wrong: wrong id, text, path or order.'),
    ('environment', 'Environment', 'Login, URL, server, database or browser problem - not the app, not the test.'),
    ('test_data', 'Test data', 'The data the test needs was missing or already used (stock, tag, customer, rate...).'),
    ('agent_mistake', 'How we test', 'A mistake in how the test was written or run (command, wait, selector, AI step).'),
]
KIND_KEYS = [k for k, _, _ in KINDS]
KIND_LABEL = {k: l for k, l, _ in KINDS}
LIMITS = {'title': 200, 'symptom': 2000, 'fix': 2000, 'avoid': 2000}
PROMPT_MAX = 40   # newest active lessons sent to the AI

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, kind VARCHAR(20) NOT NULL, title VARCHAR(200) NOT NULL,
  symptom TEXT NULL, fix TEXT NULL, avoid TEXT NULL, case_id INT NULL, run_id INT NULL, source VARCHAR(16) NOT NULL DEFAULT 'web',
  active TINYINT NOT NULL DEFAULT 1, hits INT NOT NULL DEFAULT 1,
  created_by VARCHAR(64) NULL, created_at VARCHAR(32) NOT NULL, updated_by VARCHAR(64) NULL, updated_at VARCHAR(32) NOT NULL,
  KEY ix_lessons_project (project_id, active, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
"""


def migrate(con):
    for stmt in SCHEMA.split(';'):
        if stmt.strip():
            con.execute(stmt)


def clean(data):
    """Validated lesson fields from a form / JSON body. Returns (values, errors)."""
    v, errs = {}, []
    for k, n in LIMITS.items():
        v[k] = ' '.join(str(data.get(k) or '').split()) if k == 'title' else str(data.get(k) or '').strip()
        if len(v[k]) > n:
            errs.append(f'{k.capitalize()}: at most {n} characters.')
        v[k] = v[k] or None
    v['kind'] = str(data.get('kind') or '').strip()
    if v['kind'] not in KIND_KEYS:
        errs.append('Kind: one of ' + ', '.join(KIND_KEYS) + '.')
    if not v['title']:
        errs.append('Title: say in one line what went wrong.')
    if not v['avoid'] and not v['fix']:
        errs.append('Fill "How to avoid it" (or at least the fix): that is what the next test reads.')
    for k in ('case_id', 'run_id'):
        try:
            v[k] = int(data.get(k)) if str(data.get(k) or '').strip() else None
        except (TypeError, ValueError):
            errs.append(f'{k}: a number.')
    return v, errs


def add(con, pid, v, username, source):
    """Adds a lesson, or - when the project already has an active one with the same title - counts it again
    and refreshes its text (the same mistake twice should be one lesson, louder). Returns (id, created)."""
    old = con.execute('SELECT id FROM lessons WHERE project_id=? AND active=1 AND LOWER(title)=LOWER(?)',
                      (pid, v['title'])).fetchone()
    now = db.now()
    if old:
        con.execute('UPDATE lessons SET hits=hits+1, kind=?, symptom=COALESCE(?, symptom), fix=COALESCE(?, fix), '
                    'avoid=COALESCE(?, avoid), case_id=COALESCE(?, case_id), run_id=COALESCE(?, run_id), '
                    'updated_by=?, updated_at=? WHERE id=?',
                    (v['kind'], v['symptom'], v['fix'], v['avoid'], v['case_id'], v['run_id'], username, now, old['id']))
        return old['id'], False
    lid = con.execute('INSERT INTO lessons(project_id, kind, title, symptom, fix, avoid, case_id, run_id, source, '
                      'created_by, created_at, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (pid, v['kind'], v['title'], v['symptom'], v['fix'], v['avoid'], v['case_id'], v['run_id'], source,
                       username, now, username, now)).lastrowid
    return lid, True


def rows(con, pid, active_only=True):
    return con.execute('SELECT * FROM lessons WHERE project_id=?' + (' AND active=1' if active_only else '') +
                       ' ORDER BY active DESC, hits DESC, id DESC', (pid,)).fetchall()


def as_json(r):
    return {'id': r['id'], 'kind': r['kind'], 'kind_label': KIND_LABEL.get(r['kind'], r['kind']), 'title': r['title'],
            'symptom': r['symptom'] or '', 'fix': r['fix'] or '', 'avoid': r['avoid'] or '', 'hits': r['hits'],
            'case_id': r['case_id'], 'run_id': r['run_id'], 'created_by': r['created_by'], 'updated_at': r['updated_at']}


def prompt_text(con, pid):
    """Lessons as prompt lines for the AI that writes test cases ('' when there are none). App bugs are left out:
    they say nothing about how to write a test."""
    rs = con.execute("SELECT title, avoid, fix FROM lessons WHERE project_id=? AND active=1 AND kind<>'app_bug' "
                     'ORDER BY hits DESC, id DESC LIMIT ?', (pid, PROMPT_MAX)).fetchall()
    if not rs:
        return ''
    out = ['Lessons learned in this project (mistakes made before - do not repeat them):']
    for r in rs:
        tip = ' '.join((r['avoid'] or r['fix'] or '').split())[:400]
        out.append(f'- {r["title"]}: {tip}')
    return '\n'.join(out)


def register(app, con, login_required, current_user, project_or_404):
    c0 = db.connect()
    try:
        with c0:
            migrate(c0)
    finally:
        c0.close()

    bp = Blueprint('lessons', __name__)

    def _lesson(pid, lid):
        r = con().execute('SELECT * FROM lessons WHERE id=? AND project_id=?', (lid, pid)).fetchone()
        return r or abort(404)

    @bp.route('/projects/<int:pid>/lessons')
    @login_required
    def page(pid):
        p = project_or_404(pid)
        show_all = request.args.get('all') == '1'
        kind = request.args.get('kind', '')
        rs = rows(con(), pid, active_only=not show_all)
        counts = {k: sum(1 for r in rs if r['kind'] == k) for k in KIND_KEYS}
        if kind in KIND_KEYS:
            rs = [r for r in rs if r['kind'] == kind]
        return render_template('lessons.html', p=p, lessons=rs, kinds=KINDS, kind_label=KIND_LABEL, kind=kind,
                               counts=counts, show_all=show_all, edit_id=request.args.get('edit', type=int))

    @bp.route('/projects/<int:pid>/lessons/new', methods=['POST'])
    @login_required
    def new(pid):
        project_or_404(pid)
        v, errs = clean(request.form)
        if errs:
            for e in errs:
                flash(e, 'danger')
            return redirect(url_for('lessons.page', pid=pid))
        c = con()
        with c:
            _, created = add(c, pid, v, current_user()['username'], 'web')
        flash('Lesson saved.' if created else 'Same lesson already there: counted it again and updated the text.', 'success')
        return redirect(url_for('lessons.page', pid=pid))

    @bp.route('/projects/<int:pid>/lessons/<int:lid>', methods=['POST'])
    @login_required
    def update(pid, lid):
        _lesson(pid, lid)
        action = request.form.get('action', 'save')
        c = con()
        u = current_user()['username']
        if action in ('archive', 'restore'):
            with c:
                c.execute('UPDATE lessons SET active=?, updated_by=?, updated_at=? WHERE id=?',
                          (1 if action == 'restore' else 0, u, db.now(), lid))
            flash('Lesson archived: no longer sent to the AI.' if action == 'archive' else 'Lesson restored.', 'success')
            return redirect(url_for('lessons.page', pid=pid, all='1' if action == 'archive' else None))
        if action == 'delete':
            import settings
            if not settings.is_admin(current_user()['role']):
                abort(403)   # others archive; delete is for admins
            with c:
                c.execute('DELETE FROM lessons WHERE id=?', (lid,))
            flash('Lesson deleted.', 'success')
            return redirect(url_for('lessons.page', pid=pid))
        v, errs = clean(request.form)
        if errs:
            for e in errs:
                flash(e, 'danger')
            return redirect(url_for('lessons.page', pid=pid, edit=lid))
        with c:
            c.execute('UPDATE lessons SET kind=?, title=?, symptom=?, fix=?, avoid=?, case_id=?, run_id=?, updated_by=?, '
                      'updated_at=? WHERE id=?', (v['kind'], v['title'], v['symptom'], v['fix'], v['avoid'], v['case_id'],
                                                  v['run_id'], u, db.now(), lid))
        flash('Lesson updated.', 'success')
        return redirect(url_for('lessons.page', pid=pid))

    app.register_blueprint(bp)
