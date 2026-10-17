"""Brain gaps: what the application's knowledge_brain was missing (or had wrong) while a module was tested.

The brain is written by another team, so LogiTestHub never edits it. When the Test Assistant needed something the
brain did not say (a mandatory field, a setting, a filter ...) and had to find it in the code, it records a gap: the
module, the brain doc, what is missing, where the code shows it (file:line), a ready-to-paste text for the doc, and
the words the updated doc must contain. The super admin copies or downloads the open gaps for the brain team.

When the brain team says they updated it, "Check again" reads the brain (syncing the environment's code copy first
when it is a git copy) and says per gap: updated (every word is now in the module's docs, with file:line) or still
missing. No AI is used for the check.

Registered from app.py with register(app, con, login_required, current_user, project_or_404).
"""
import json
import os
import re

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

import db

STATUSES = [   # key, label, meaning
    ('open', 'Open', 'The brain does not have it yet: give it to the brain team.'),
    ('updated', 'Updated', 'Check again found every word in the brain: the brain team added it.'),
    ('closed', 'Closed', 'Not needed / will not be added. No longer listed for the brain team.'),
]
STATUS_LABEL = {k: l for k, l, _ in STATUSES}
LIMITS = {'module': 120, 'doc': 120, 'screen': 300, 'missing': 300, 'detail': 3000, 'evidence': 1000, 'suggested': 4000}

SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_gaps (
  id INT AUTO_INCREMENT PRIMARY KEY, project_id INT NOT NULL, module VARCHAR(120) NOT NULL, doc VARCHAR(120) NULL,
  screen VARCHAR(300) NULL, missing VARCHAR(300) NOT NULL, detail TEXT NULL, evidence TEXT NULL, suggested TEXT NULL,
  terms TEXT NULL, status VARCHAR(16) NOT NULL DEFAULT 'open', hits INT NOT NULL DEFAULT 1, source VARCHAR(16) NOT NULL DEFAULT 'web',
  env_id INT NULL, thread_id INT NULL, case_id INT NULL, brain_ref VARCHAR(120) NULL,
  checked_at VARCHAR(32) NULL, checked_by VARCHAR(64) NULL, check_result TEXT NULL,
  created_by VARCHAR(64) NULL, created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL,
  KEY ix_brain_gaps_project (project_id, status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
"""


def migrate(con):
    for stmt in SCHEMA.split(';'):
        if stmt.strip():
            con.execute(stmt)


def clean(data):
    """Validated gap fields from the assistant's JSON or a form. Returns (values, errors)."""
    v, errs = {}, []
    for k, n in LIMITS.items():
        v[k] = (' '.join(str(data.get(k) or '').split()) if k in ('module', 'doc', 'missing', 'screen')
                else str(data.get(k) or '').strip())[:n] or None
    terms = data.get('terms') or []
    if isinstance(terms, str):
        terms = re.split(r'[,\n]', terms)
    v['terms'] = [t for t in dict.fromkeys(' '.join(str(t).split()) for t in terms) if 2 <= len(t) <= 80][:8]
    if not v['module']:
        errs.append('Module: the knowledge_brain module folder (e.g. Smith_Metal_Issue).')
    if not v['missing']:
        errs.append('What is missing: one line.')
    if not v['terms']:
        errs.append('Words to look for: at least one word the updated brain must contain (used by Check again).')
    return v, errs


def brain_ref(brain_dir, module):
    """'<last_updated> @ <source_ref>' of the module's brain now ('' when it has no front matter)."""
    import brain
    for f in ('MODULE_BRAIN.md', 'PERMISSION_MAP.md', 'FUNCTIONAL_BRAIN.md'):
        m = brain.front(os.path.join(brain_dir, module, f))
        if m:
            return f"{m.get('last_updated', '?')} @ {m.get('source_ref', '?')}"
    return ''


def add(con, pid, v, username, source, env_id=None, thread_id=None, case_id=None, brain_dir=None):
    """Adds a gap, or counts an open one with the same module + what is missing again. Returns (id, created)."""
    old = con.execute("SELECT id FROM brain_gaps WHERE project_id=? AND status='open' AND LOWER(module)=LOWER(?) "
                      'AND LOWER(missing)=LOWER(?)', (pid, v['module'], v['missing'])).fetchone()
    now = db.now()
    if old:
        con.execute('UPDATE brain_gaps SET hits=hits+1, updated_at=? WHERE id=?', (now, old['id']))
        return old['id'], False
    ref = brain_ref(brain_dir, v['module']) if brain_dir else None
    gid = con.execute('INSERT INTO brain_gaps(project_id, module, doc, screen, missing, detail, evidence, suggested, terms, '
                      'source, env_id, thread_id, case_id, brain_ref, created_by, created_at, updated_at) '
                      'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (pid, v['module'], v['doc'], v['screen'], v['missing'], v['detail'], v['evidence'], v['suggested'],
                       json.dumps(v['terms']), source, env_id, thread_id, case_id, ref or None, username, now, now)).lastrowid
    return gid, True


def _envs_with_brain(con, module, first_env=None):
    """[(env row, brain dir)] whose code has knowledge_brain/<module>/, the gap's own environment first."""
    import brain
    import coderepo
    out = []
    rows = con.execute('SELECT * FROM environments ORDER BY id').fetchall()
    rows = sorted(rows, key=lambda e: e['id'] != first_env)
    for e in rows:
        b = brain.find(coderepo.code_dir(dict(e)))
        if b and os.path.isdir(os.path.join(b, module)):
            out.append((dict(e), b))
    return out


def _sync(env):
    """Brings a git copy of the environment's code up to date (the brain team's push), when nothing else syncs it."""
    import coderepo
    if not env.get('repo_url') or coderepo.is_local(env['repo_url']):
        return ''
    with coderepo._LOCK:
        if env['id'] in coderepo._RUNNING:
            return f"the code copy of {env['name']} is syncing right now: check again in a minute"
        coderepo._RUNNING.add(env['id'])
    coderepo.sync(env['id'])   # clears _RUNNING itself
    c = db.connect()
    try:
        e = c.execute('SELECT repo_status, repo_error FROM environments WHERE id=?', (env['id'],)).fetchone()
    finally:
        c.close()
    return f"sync of {env['name']} failed: {e['repo_error']}" if e and e['repo_status'] == 'error' else ''


def check(con, gap, synced=None):
    """Reads the brain again. Returns {status, found: {term: 'file:line'}, missing: [terms], where, ref, note}."""
    terms = json.loads(gap['terms'] or '[]')
    places = _envs_with_brain(con, gap['module'], gap['env_id'])
    if synced is not None:   # once per environment per "Check all"
        for e, _ in places:
            if e['id'] not in synced:
                synced[e['id']] = _sync(e)
    notes = [n for n in (synced or {}).values() if n]
    places = _envs_with_brain(con, gap['module'], gap['env_id']) if synced is not None else places
    if not places:
        return {'status': 'open', 'found': {}, 'missing': terms, 'where': '', 'ref': '',
                'note': f'No knowledge_brain/{gap["module"]}/ in the code of any environment.' + (' ' + '; '.join(notes) if notes else '')}
    env, bdir = places[0]
    mdir = os.path.join(bdir, gap['module'])
    files = sorted(f for f in os.listdir(mdir) if f.endswith(('.md', '.txt', '.sql')))
    if gap['doc'] in files:   # the doc named in the gap first
        files.remove(gap['doc'])
        files.insert(0, gap['doc'])
    found = {}
    for fn in files:
        try:
            with open(os.path.join(mdir, fn), encoding='utf-8', errors='ignore') as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for t in terms:
            if t in found:
                continue
            tl = t.lower()
            for i, ln in enumerate(lines, 1):
                if tl in ln.lower():
                    found[t] = f'{gap["module"]}/{fn}:{i}'
                    break
    missing = [t for t in terms if t not in found]
    ref = brain_ref(bdir, gap['module'])
    note = '' if not missing else ('The brain has not changed since the gap was found.' if ref and ref == gap['brain_ref']
                                   else 'Not all words are in the brain yet.')
    return {'status': 'updated' if not missing else 'open', 'found': found, 'missing': missing,
            'where': f"{env['name']}: {bdir}", 'ref': ref, 'note': ' '.join(x for x in [note, *notes] if x)}


def as_markdown(gaps, project, server, who):
    out = [f'# Knowledge brain gaps - {project["name"]} ({len(gaps)})', '',
           f'From LogiTestHub ({server}) · exported {db.now()[:16]} by {who}', '',
           'Found while testing: the Test Assistant needed these and had to read them from the code because the brain '
           'did not say them. Please add each one to the named doc. When done, tell the tester: LogiTestHub checks the '
           'brain again for the words listed under "Check looks for".', '']
    for g in gaps:
        terms = json.loads(g['terms'] or '[]')
        out += [f'## G{g["id"]} · {g["module"]} · {g["doc"] or "(any doc)"}', '',
                f'**Missing:** {g["missing"]}', '']
        if g['screen']:
            out += [f'**Screen:** {g["screen"]}', '']
        if g['detail']:
            out += [f'**What the code does:** {g["detail"]}', '']
        if g['evidence']:
            out += [f'**Code:** {g["evidence"]}', '']
        if g['suggested']:
            out += ['**Suggested text for the doc:**', '', g['suggested'], '']
        out += [f'**Check looks for:** {", ".join("`" + t + "`" for t in terms)}', '',
                f'_Found {(g["created_at"] or "")[:10]} by {g["created_by"] or "-"} · seen {g["hits"]}x'
                + (f' · brain then: {g["brain_ref"]}' if g['brain_ref'] else '') + '_', '']
    return '\n'.join(out)


def register(app, con, login_required, current_user, project_or_404):
    c0 = db.connect()
    try:
        with c0:
            migrate(c0)
    finally:
        c0.close()

    bp = Blueprint('brain_gaps', __name__)

    def _super():   # the brain gaps list is the super admin's (it goes to the brain team)
        import settings
        if not settings.is_super(current_user()['role']):
            abort(403)

    def _gaps(pid, status):
        q = 'SELECT g.*, c.tc_no, c.title AS case_title FROM brain_gaps g LEFT JOIN cases c ON c.id=g.case_id WHERE g.project_id=?'
        return con().execute(q + ('' if status == 'all' else ' AND g.status=?') + ' ORDER BY g.module, g.id',
                             (pid,) if status == 'all' else (pid, status)).fetchall()

    def _save_check(gid, r):
        c = con()
        with c:
            c.execute('UPDATE brain_gaps SET status=?, checked_at=?, checked_by=?, check_result=?, updated_at=? WHERE id=?',
                      (r['status'], db.now(), current_user()['username'], json.dumps(r), db.now(), gid))

    @bp.route('/projects/<int:pid>/brain-gaps')
    @login_required
    def page(pid):
        _super()
        p = project_or_404(pid)
        status = request.args.get('status', 'open')
        status = status if status in STATUS_LABEL or status == 'all' else 'open'
        rows = []
        for g in _gaps(pid, status):
            g = dict(g)
            g['terms_list'] = json.loads(g['terms'] or '[]')
            g['result'] = json.loads(g['check_result']) if g['check_result'] else None
            rows.append(g)
        counts = {r['status']: r['n'] for r in con().execute(
            'SELECT status, COUNT(*) AS n FROM brain_gaps WHERE project_id=? GROUP BY status', (pid,))}
        server = request.host_url.rstrip('/')
        md = as_markdown(_gaps(pid, 'open'), p, server, current_user()['username'])
        return render_template('brain_gaps.html', p=p, gaps=rows, status=status, statuses=STATUSES,
                               status_label=STATUS_LABEL, counts=counts, md=md)

    @bp.route('/projects/<int:pid>/brain-gaps.md')
    @login_required
    def download(pid):
        _super()
        p = project_or_404(pid)
        st = request.args.get('status', 'open')
        server = request.host_url.rstrip('/')
        name = f'brain-gaps-{p["name"].lower().replace(" ", "-")}-{db.now()[:10]}.md'
        return Response(as_markdown(_gaps(pid, st), p, server, current_user()['username']),
                        mimetype='text/markdown; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @bp.route('/projects/<int:pid>/brain-gaps/check', methods=['POST'])
    @login_required
    def check_all(pid):
        """Check again: every open gap (or one, gid=...) against the brain as it is now."""
        _super()
        project_or_404(pid)
        gid = request.form.get('gid', type=int)
        gaps = [g for g in _gaps(pid, 'all') if (g['id'] == gid if gid else g['status'] == 'open')]
        synced, done = {}, []
        for g in gaps:
            r = check(con(), g, synced)
            _save_check(g['id'], r)
            done.append((g, r))
        up = [g for g, r in done if r['status'] == 'updated']
        still = [g for g, r in done if r['status'] != 'updated']
        if not done:
            flash('No open gaps to check.', 'info')
        else:
            flash(f'Checked {len(done)} gap{"s" if len(done) != 1 else ""}: {len(up)} updated in the brain'
                  + (f' ({", ".join("G" + str(g["id"]) for g in up)})' if up else '')
                  + f', {len(still)} still missing' + (f' ({", ".join("G" + str(g["id"]) for g in still)})' if still else '') + '.',
                  'success' if not still else 'warning')
        return redirect(url_for('brain_gaps.page', pid=pid, status=request.form.get('status') or 'open'))

    @bp.route('/projects/<int:pid>/brain-gaps/<int:gid>', methods=['POST'])
    @login_required
    def set_status(pid, gid):
        _super()
        project_or_404(pid)
        st = request.form.get('status', '')
        if st not in STATUS_LABEL:
            abort(400)
        c = con()
        with c:
            c.execute('UPDATE brain_gaps SET status=?, updated_at=? WHERE id=? AND project_id=?', (st, db.now(), gid, pid))
        flash(f'Gap G{gid}: {STATUS_LABEL[st]}.', 'success')
        return redirect(url_for('brain_gaps.page', pid=pid, status=request.form.get('back') or 'open'))

    app.register_blueprint(bp)
