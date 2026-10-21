"""Lessons learned per project: why a test failed or what went wrong while testing, and how to avoid it next time.

Written by people on the Lessons page or by an agent through the Tester CLI (`tester lesson add`), read back by the
agent before it writes or runs a test (`tester lessons`), and added to the Test Assistant / Generate with AI prompts so
AI-written test cases avoid the same mistakes. One teammate's lesson reaches everyone.

Every lesson counts straight away - a lesson the Test Assistant writes after explaining a failure is saved by itself
(nobody has to remember to click Save), so no one's testing waits for a review. The admin reviews them afterwards
(Lessons -> To review): keep it as a lesson, move it into the /tester skill or fix it in the code, or reject it (no
longer sent to the AI). "Download for review" gives the list as Markdown to hand over. A lesson whose text changes
goes back to To review.

Registered from app.py with register(app, con, login_required, current_user, project_or_404).
"""
from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

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


REVIEWS = [   # key, label, what it means
    ('new', 'To review', 'Not reviewed yet. It is already sent to the AI.'),
    ('keep', 'Keep', 'Reviewed: right, stays a lesson.'),
    ('skill', 'Into skill', 'Reviewed: goes into the /tester skill (then it can be archived here).'),
    ('code', 'Fix in code', 'Reviewed: the runner / prompt / app should handle it (then it can be archived here).'),
    ('rejected', 'Rejected', 'Reviewed: wrong or not useful. Archived - no longer sent to the AI.'),
]
REVIEW_KEYS = [k for k, _, _ in REVIEWS]
REVIEW_LABEL = {k: l for k, l, _ in REVIEWS}


def migrate(con):
    for stmt in SCHEMA.split(';'):
        if stmt.strip():
            con.execute(stmt)
    have = {r['COLUMN_NAME'] for r in con.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='lessons'")}
    if 'review' not in have:   # every lesson is reviewed later by the admin; existing ones start as To review
        con.execute("ALTER TABLE lessons ADD COLUMN review VARCHAR(16) NOT NULL DEFAULT 'new', "
                    "ADD COLUMN review_note VARCHAR(500) NULL, ADD COLUMN reviewed_by VARCHAR(64) NULL, "
                    "ADD COLUMN reviewed_at VARCHAR(32) NULL")


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
    and refreshes its text (the same mistake twice should be one lesson, louder). Returns (id, created).
    A changed text goes back to To review. A lesson the admin rejected is only counted again: it stays rejected."""
    rej = con.execute("SELECT id FROM lessons WHERE project_id=? AND active=0 AND review='rejected' AND LOWER(title)=LOWER(?)",
                      (pid, v['title'])).fetchone()
    old = con.execute('SELECT id, fix, avoid FROM lessons WHERE project_id=? AND active=1 AND LOWER(title)=LOWER(?)',
                      (pid, v['title'])).fetchone()
    now = db.now()
    if rej and not old:
        con.execute('UPDATE lessons SET hits=hits+1 WHERE id=?', (rej['id'],))
        return rej['id'], False
    if old:
        changed = (v['fix'] and v['fix'] != old['fix']) or (v['avoid'] and v['avoid'] != old['avoid'])
        con.execute('UPDATE lessons SET hits=hits+1, kind=?, symptom=COALESCE(?, symptom), fix=COALESCE(?, fix), '
                    'avoid=COALESCE(?, avoid), case_id=COALESCE(?, case_id), run_id=COALESCE(?, run_id), '
                    'updated_by=?, updated_at=?' + (", review='new'" if changed else '') + ' WHERE id=?',
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
        review = request.args.get('review', '')
        rs = rows(con(), pid, active_only=not show_all and review != 'rejected')
        counts = {k: sum(1 for r in rs if r['kind'] == k) for k in KIND_KEYS}
        to_review = con().execute("SELECT COUNT(*) AS n FROM lessons WHERE project_id=? AND review='new'", (pid,)).fetchone()['n']
        if kind in KIND_KEYS:
            rs = [r for r in rs if r['kind'] == kind]
        if review in REVIEW_KEYS:
            rs = [r for r in rs if r['review'] == review]
        return render_template('lessons.html', p=p, lessons=rs, kinds=KINDS, kind_label=KIND_LABEL, kind=kind,
                               counts=counts, show_all=show_all, edit_id=request.args.get('edit', type=int),
                               reviews=REVIEWS, review_label=REVIEW_LABEL, review=review, to_review=to_review)

    def _admin():   # reviewing / downloading lessons is the super admin's
        import settings
        if not settings.is_super(current_user()['role']):
            abort(403)

    @bp.route('/projects/<int:pid>/lessons/<int:lid>/review', methods=['POST'])
    @login_required
    def set_review(pid, lid):
        """Admin's review of one lesson. Rejected = archived (no longer sent to the AI); any other keeps it active."""
        _admin()
        _lesson(pid, lid)
        st = request.form.get('review', '')
        if st not in REVIEW_KEYS:
            abort(400)
        note = ' '.join(request.form.get('note', '').split())[:500] or None
        c = con()
        with c:
            c.execute('UPDATE lessons SET review=?, review_note=COALESCE(?, review_note), reviewed_by=?, reviewed_at=?, active=? '
                      'WHERE id=?', (st, note, current_user()['username'], db.now(), 0 if st == 'rejected' else 1, lid))
        flash(f'Lesson #{lid}: {REVIEW_LABEL[st]}.' + (' Archived: no longer sent to the AI.' if st == 'rejected' else ''), 'success')
        return redirect(request.form.get('next') or url_for('lessons.page', pid=pid, review='new'))

    @bp.route('/projects/<int:pid>/lessons/review.md')
    @login_required
    def download(pid):
        """The lessons to review (or ?review=all / keep / skill / ...) as one Markdown file to hand over for review."""
        _admin()
        p = project_or_404(pid)
        st = request.args.get('review', 'new')
        rs = con().execute(
            'SELECT l.*, c.tc_no, c.title AS case_title FROM lessons l LEFT JOIN cases c ON c.id=l.case_id WHERE l.project_id=?'
            + ('' if st == 'all' else ' AND l.review=?') + ' ORDER BY l.hits DESC, l.id', (pid,) if st == 'all' else (pid, st)).fetchall()
        import pmtasks
        server = pmtasks.public_base() or request.host_url.rstrip('/')
        out = [f'# LogiTestHub lessons - {REVIEW_LABEL.get(st, "all")} ({len(rs)})', '',
               f'Server: {server} · Project: {p["name"]} (#{pid}) · Exported {db.now()[:16]} by {current_user()["username"]}', '',
               'For each lesson decide: **keep** (stays a lesson) / **skill** (move into the /tester skill) / '
               '**code** (fix in the runner, prompt or app) / **reject** (wrong). Mark it on the Lessons page '
               f'({server}/projects/{pid}/lessons?review=new).', '']
        for r in rs:
            out += [f'## L{r["id"]} · {KIND_LABEL.get(r["kind"], r["kind"])} · {r["title"]}', '',
                    f'- Review: {REVIEW_LABEL.get(r["review"], r["review"])}' + (f' ({r["review_note"]})' if r['review_note'] else '')
                    + f' · {"sent to the AI" if r["active"] else "archived"} · seen {r["hits"]}x',
                    f'- From: {r["source"]} · by {r["created_by"] or "-"} {(r["created_at"] or "")[:16]}'
                    + (f' · updated by {r["updated_by"]} {(r["updated_at"] or "")[:16]}' if r['updated_at'] != r['created_at'] else ''),
                    *([f'- Test case: TC-{r["tc_no"]} {r["case_title"]} ({server}/cases/{r["case_id"]}'
                       + (f'?run={r["run_id"]}' if r['run_id'] else '') + ')'] if r['case_id'] and r['tc_no'] else []),
                    '']
            for label, k in (('How it showed up', 'symptom'), ('Fix', 'fix'), ('How to avoid it', 'avoid')):
                if r[k]:
                    out += [f'**{label}:** {r[k].strip()}', '']
        name = f'lessons-{st}-{p["name"].lower().replace(" ", "-")}-{db.now()[:10]}.md'
        return Response('\n'.join(out), mimetype='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})

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
        import settings   # an edit by someone other than the super admin is reviewed again
        with c:
            c.execute('UPDATE lessons SET kind=?, title=?, symptom=?, fix=?, avoid=?, case_id=?, run_id=?, updated_by=?, '
                      'updated_at=?' + ('' if settings.is_super(current_user()['role']) else ", review='new'") + ' WHERE id=?',
                      (v['kind'], v['title'], v['symptom'], v['fix'], v['avoid'], v['case_id'], v['run_id'], u, db.now(), lid))
        flash('Lesson updated.', 'success')
        return redirect(url_for('lessons.page', pid=pid))

    app.register_blueprint(bp)
