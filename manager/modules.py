"""Modules -> sub-modules for a project's test cases (like the application's own menu).

A module is a folder with parent_id = 0; a sub-module is a folder whose parent_id is its module. Test cases sit
in either. "Set up modules" fills the tree in one go: read the menu from the application (logs in with an
Environment, read-only - see menu-crawl.js) or type it as an indented list.

Registered from app.py with register(app, con, login_required, current_user, project_or_404).
"""
import json
import os
import re
import subprocess

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

import db

MAX_NAME = 160


def migrate(con):
    """folders: + parent_id (0 = module / top level), link, sort; names unique per parent, not per project."""
    cols = {r['Field'] for r in con.execute('SHOW COLUMNS FROM folders')}
    if 'parent_id' not in cols:
        con.execute('ALTER TABLE folders ADD COLUMN parent_id INT NOT NULL DEFAULT 0 AFTER project_id')
    if 'link' not in cols:
        con.execute('ALTER TABLE folders ADD COLUMN link VARCHAR(500) NULL')
    if 'sort' not in cols:
        con.execute('ALTER TABLE folders ADD COLUMN sort INT NOT NULL DEFAULT 0')
    if 'import_key' not in cols:   # TestMU folder name this module / sub-module took over (manage.py import-folder)
        con.execute('ALTER TABLE folders ADD COLUMN import_key VARCHAR(191) NULL')
    keys = {r['Key_name'] for r in con.execute('SHOW INDEX FROM folders')}
    if 'ux_folders_parent_name' not in keys:
        con.execute('ALTER TABLE folders ADD UNIQUE KEY ux_folders_parent_name (project_id, parent_id, name)')
    if 'ux_folders_name' in keys:
        con.execute('ALTER TABLE folders DROP INDEX ux_folders_name')
    if 'ix_folders_parent' not in keys:
        con.execute('ALTER TABLE folders ADD KEY ix_folders_parent (parent_id)')


def tree(folders):
    """Flat folder rows (with n / ran counts) -> [module dict with 'children', totals]. Modules keep menu order."""
    by_parent = {}
    for f in folders:
        by_parent.setdefault(f['parent_id'] or 0, []).append(f)
    order = lambda f: (f['sort'] or 0, f['name'].lower())
    mods = []
    for m in sorted(by_parent.get(0, []), key=order):
        kids = sorted(by_parent.get(m['id'], []), key=order)
        mods.append(dict(m, children=kids, total=m.get('n', 0) + sum(k.get('n', 0) for k in kids),
                         total_ran=m.get('ran', 0) + sum(k.get('ran', 0) for k in kids)))
    return mods


def folder_options(folders):
    """[(id, label)] in tree order for dropdowns: 'Module', then 'Module › Sub-module' under it."""
    out = []
    for m in tree(folders):
        out.append((m['id'], m['name']))
        out += [(k['id'], f"{m['name']} › {k['name']}") for k in m['children']]
    return out


def label_map(folders):
    """folder id -> 'Module › Sub-module' (or just 'Module')."""
    names = {f['id']: f for f in folders}
    out = {}
    for f in folders:
        p = names.get(f['parent_id']) if f['parent_id'] else None
        out[f['id']] = f"{p['name']} › {f['name']}" if p else f['name']
    return out


def descendants(con, pid, fid):
    """The folder and its sub-modules (ids)."""
    return [fid] + [r['id'] for r in con.execute('SELECT id FROM folders WHERE project_id=? AND parent_id=?', (pid, fid))]


def parse_typed(text):
    """Indented list -> [{name, subs:[{name}]}]. A line starting with space / tab / '-' / '*' is a sub-module
    of the line above it; 'Module > Sub' on one line works too."""
    mods, cur = [], None
    for raw in (text or '').splitlines():
        if not raw.strip():
            continue
        sub = raw[:1] in (' ', '\t') or raw.lstrip()[:1] in ('-', '*', '•')
        name = re.sub(r'^[\s\-*•]+', '', raw).strip()[:MAX_NAME]
        if not name:
            continue
        if not sub and re.search(r'\s[>›/]\s', name):   # "Retail Catalog > Category"
            m, s = re.split(r'\s[>›/]\s', name, maxsplit=1)
            cur = next((x for x in mods if x['name'].lower() == m.strip().lower()), None)
            if not cur:
                cur = {'name': m.strip(), 'link': '', 'subs': []}
                mods.append(cur)
            cur['subs'].append({'name': s.strip(), 'link': ''})
        elif sub and cur is not None:
            cur['subs'].append({'name': name, 'link': ''})
        else:
            cur = {'name': name, 'link': '', 'subs': []}
            mods.append(cur)
    return mods


FILE_FORMAT = 'logitesthub.modules/v1'
MAX_MODULES, MAX_SUBS = 2000, 20000


def normalize_file(data):
    """A modules file (dict with "modules", or just the list) -> (modules, errors, warnings).
    modules = [{name, link, subs: [{name, link}]}]; a sub-module may also be written as a plain string.
    Names are trimmed; duplicates (same name, any case) are merged, never doubled."""
    errors, warnings = [], []
    if isinstance(data, dict):
        fmt = data.get('format')
        if fmt and fmt != FILE_FORMAT:
            warnings.append(f'format is "{fmt}", expected "{FILE_FORMAT}": read anyway')
        items = data.get('modules')
    else:
        items = data
    if not isinstance(items, list):
        return [], ['The file needs a "modules" list: {"format": "%s", "modules": [{"name": "...", "subs": [...]}]}' % FILE_FORMAT], warnings
    out, by_name, n_subs = [], {}, 0

    def text(v, limit, where):
        if v is None:
            return ''
        if not isinstance(v, (str, int, float)):
            errors.append(f'{where}: must be text')
            return ''
        v = re.sub(r'\s+', ' ', str(v)).strip()
        if len(v) > limit:
            warnings.append(f'{where}: longer than {limit} characters, cut')
            v = v[:limit]
        return v

    for i, m in enumerate(items, 1):
        if isinstance(m, str):
            m = {'name': m}
        if not isinstance(m, dict):
            errors.append(f'module #{i}: must be an object like {{"name": "...", "subs": [...]}}')
            continue
        name = text(m.get('name'), MAX_NAME, f'module #{i} name')
        if not name:
            errors.append(f'module #{i}: "name" is missing')
            continue
        mod = by_name.get(name.lower())
        if not mod:
            mod = {'name': name, 'link': text(m.get('link'), 500, f'"{name}" link'), 'subs': []}
            by_name[name.lower()] = mod
            out.append(mod)
        have = {s['name'].lower() for s in mod['subs']}
        subs = m.get('subs') or []
        if not isinstance(subs, list):
            errors.append(f'"{name}": "subs" must be a list')
            continue
        for j, s in enumerate(subs, 1):
            if isinstance(s, str):
                s = {'name': s}
            if not isinstance(s, dict):
                errors.append(f'"{name}" sub-module #{j}: must be text or {{"name": "..."}}')
                continue
            sname = text(s.get('name'), MAX_NAME, f'"{name}" sub-module #{j} name')
            if not sname:
                errors.append(f'"{name}" sub-module #{j}: "name" is missing')
                continue
            if sname.lower() in have:
                continue
            have.add(sname.lower())
            mod['subs'].append({'name': sname, 'link': text(s.get('link'), 500, f'"{name} > {sname}" link')})
            n_subs += 1
    if len(out) > MAX_MODULES or n_subs > MAX_SUBS:
        errors.append(f'Too large: {len(out)} modules / {n_subs} sub-modules (limit {MAX_MODULES} / {MAX_SUBS}).')
    if not out and not errors:
        errors.append('The file has no modules.')
    return out, errors, warnings


def plan(con, pid, mods):
    """What a push would do. Only additions; what is in the project but not in the file is listed and kept."""
    rows = con.execute('SELECT id, parent_id, name FROM folders WHERE project_id=?', (pid,)).fetchall()
    top = {r['name'].lower(): r for r in rows if not r['parent_id']}
    kids = {}
    for r in rows:
        if r['parent_id']:
            kids.setdefault(r['parent_id'], set()).add(r['name'].lower())
    new_mods, new_subs, same_mods, same_subs = [], [], 0, 0
    file_names = set()
    for m in mods:
        file_names.add(m['name'].lower())
        ex = top.get(m['name'].lower())
        if ex:
            same_mods += 1
        else:
            new_mods.append(m['name'])
        have = kids.get(ex['id'], set()) if ex else set()
        for s in m['subs']:
            if s['name'].lower() in have:
                same_subs += 1
            else:
                new_subs.append(f"{m['name']} › {s['name']}")
    only_in_project = sorted(r['name'] for k, r in top.items() if k not in file_names)
    return {'new_modules': new_mods, 'new_subs': new_subs, 'existing_modules': same_mods, 'existing_subs': same_subs,
            'kept_not_in_file': only_in_project}


def export_file(con, p):
    """The project's current tree in the modules-file format (archived folders left out)."""
    rows = con.execute('SELECT id, parent_id, name, link FROM folders WHERE project_id=? AND archived=0 '
                       'ORDER BY sort, name', (p['id'],)).fetchall()
    mods = tree([dict(r, n=0, ran=0, sort=0) for r in rows])
    return {'format': FILE_FORMAT, 'project': p['name'], 'source': 'LogiTestHub export', 'exported_at': db.now(),
            'modules': [{'name': m['name'], **({'link': m['link']} if m.get('link') else {}),
                         'subs': [{'name': k['name'], **({'link': k['link']} if k.get('link') else {})} for k in m['children']]}
                        for m in mods]}


def apply_tree(con, pid, mods):
    """Creates the modules / sub-modules that do not exist yet (matched by name, case-insensitive).
    Returns (modules_added, subs_added). Existing folders and their test cases are never touched."""
    added_m = added_s = 0
    existing = {(r['parent_id'], r['name'].lower()): r['id']
                for r in con.execute('SELECT id, parent_id, name FROM folders WHERE project_id=?', (pid,))}
    for i, m in enumerate(mods):
        name = (m.get('name') or '').strip()[:MAX_NAME]
        if not name:
            continue
        mid = existing.get((0, name.lower()))
        if not mid:
            mid = con.execute('INSERT INTO folders(project_id, parent_id, name, link, sort) VALUES (?,?,?,?,?)',
                              (pid, 0, name, (m.get('link') or '')[:500] or None, (i + 1) * 10)).lastrowid
            existing[(0, name.lower())] = mid
            added_m += 1
        for j, s in enumerate(m.get('subs') or []):
            sname = (s.get('name') or '').strip()[:MAX_NAME]
            if not sname or (mid, sname.lower()) in existing:
                continue
            sid = con.execute('INSERT INTO folders(project_id, parent_id, name, link, sort) VALUES (?,?,?,?,?)',
                              (pid, mid, sname, (s.get('link') or '')[:500] or None, (j + 1) * 10)).lastrowid
            existing[(mid, sname.lower())] = sid
            added_s += 1
    return added_m, added_s


SHOTS = os.path.join(db.DATA, 'menu-shots')
SHOT_RE = re.compile(r'^menu-[0-9a-f]{16}\.png$')


def _prune_shots(days=7):
    import time
    if not os.path.isdir(SHOTS):
        return
    cutoff = time.time() - days * 86400
    for f in os.listdir(SHOTS):
        fp = os.path.join(SHOTS, f)
        if SHOT_RE.match(f) and os.path.getmtime(fp) < cutoff:
            os.remove(fp)


def crawl(env_id=None, menu_url=None):
    """Reads the application's menu with the given environment (no AI). Uses the environment's own Login steps and
    Menu page URL when set; menu_url overrides the latter for one read. Always keeps a screenshot of what the
    reader saw (data/menu-shots, 7 days). Returns the reader's dict (ok, stage, reason, warning, strategy, modules, shot)."""
    import secrets
    import tempfile
    import envs
    c = db.connect()
    try:
        env = envs.get(c, env_id)
    finally:
        c.close()
    if not env:
        return {'ok': False, 'stage': 'setup', 'reason': 'Add an environment first (Settings -> Environments): it gives the URL and login.'}
    os.makedirs(SHOTS, exist_ok=True)
    _prune_shots()
    shot = os.path.join(SHOTS, f'menu-{secrets.token_hex(8)}.png')
    vf = envs.vars_file(env)
    steps_file = None
    cmd = ['node', os.path.join(db.RUNNER_DIR, 'menu-crawl.js'), '--vars', vf, '--shot', shot]
    if env.get('login_steps'):
        fd, steps_file = tempfile.mkstemp(suffix='.login.md', dir=db.DATA)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(env['login_steps'])
        cmd += ['--login-steps', steps_file]
    page = (menu_url or '').strip() or env.get('menu_url')
    if page:
        cmd += ['--url', page]
    try:
        p = subprocess.run(cmd, cwd=db.RUNNER_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        try:
            out = json.loads(p.stdout or '{}')
        except ValueError:
            out = {'ok': False, 'stage': 'open', 'reason': (p.stderr or 'Menu reader failed').strip().splitlines()[-1][:300]}
    except subprocess.TimeoutExpired:
        out = {'ok': False, 'stage': 'open', 'reason': 'The application took longer than 5 minutes to sign in and show its menu.'}
    except FileNotFoundError:
        out = {'ok': False, 'stage': 'open', 'reason': 'Node.js not found on this PC.'}
    finally:
        os.remove(vf)
        if steps_file:
            os.remove(steps_file)
    out['env'] = env['name']
    out['menu_url'] = page or ''
    out['error'] = out.get('reason')   # older callers read "error"
    if not (out.get('shot') and os.path.isfile(os.path.join(SHOTS, out['shot']))):
        out.pop('shot', None)
    return out


def register(app, con, login_required, current_user, project_or_404):
    c0 = db.connect()
    try:
        with c0:
            migrate(c0)
    finally:
        c0.close()

    bp = Blueprint('modules', __name__)
    app.jinja_env.globals['folder_options'] = folder_options

    @bp.route('/projects/<int:pid>/modules', methods=['GET'])
    @login_required
    def setup(pid):
        p = project_or_404(pid)
        import envs
        c = con()
        rows = c.execute('SELECT id, parent_id, name FROM folders WHERE project_id=?', (pid,)).fetchall()
        names = {r['id']: r['name'] for r in rows}
        existing = {}   # module (lower case) -> [sub-module (lower case)], for the New / Already added labels
        for r in rows:
            if not r['parent_id']:
                existing.setdefault(r['name'].lower(), [])
        for r in rows:
            if r['parent_id'] and r['parent_id'] in names:
                existing.setdefault(names[r['parent_id']].lower(), []).append(r['name'].lower())
        return render_template('modules_setup.html', p=p, envs=envs.all_envs(c), n_folders=len(rows),
                               new=request.args.get('new') == '1', existing=existing)

    @bp.route('/projects/<int:pid>/modules/read', methods=['POST'])
    @login_required
    def read(pid):
        project_or_404(pid)
        menu_url = request.form.get('menu_url', '').strip()[:500]
        if menu_url and not (re.match(r'^https?://', menu_url, re.I) or not re.match(r'^[a-z]+:', menu_url, re.I)):
            return jsonify({'ok': False, 'stage': 'setup', 'reason': 'Menu page URL must be http(s)://... or a path like index.php/admin/dashboard.'}), 400
        out = crawl(request.form.get('env_id', type=int), menu_url)
        if out.get('shot'):
            out['shot_url'] = url_for('modules.shot', pid=pid, name=out['shot'])
        return jsonify(out), 200   # a failed read is still a normal answer: the page shows the reason

    @bp.route('/projects/<int:pid>/modules/shot/<name>')
    @login_required
    def shot(pid, name):
        from flask import abort, send_from_directory
        project_or_404(pid)
        if not SHOT_RE.match(name):
            abort(404)
        return send_from_directory(SHOTS, name, mimetype='image/png', max_age=0)

    @bp.route('/projects/<int:pid>/modules/parse', methods=['POST'])
    @login_required
    def parse(pid):
        project_or_404(pid)
        mods = parse_typed(request.form.get('text', ''))
        if not mods:
            return jsonify({'ok': False, 'error': 'Type at least one module.'}), 400
        return jsonify({'ok': True, 'modules': mods})

    @bp.route('/projects/<int:pid>/modules/file', methods=['POST'])
    @login_required
    def parse_file(pid):
        """Import modules file: a logitesthub.modules/v1 JSON file -> the same preview as Read menu / Type them."""
        project_or_404(pid)
        f = request.files.get('file')
        if not f or not f.filename:
            return jsonify({'ok': False, 'error': 'Choose a .json modules file.'}), 400
        raw = f.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            return jsonify({'ok': False, 'error': 'The file is larger than 5 MB.'}), 400
        try:
            data = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeDecodeError, ValueError) as e:
            return jsonify({'ok': False, 'error': f'Not a valid JSON file: {e}'}), 400
        mods, errors, warnings = normalize_file(data)
        if errors:
            return jsonify({'ok': False, 'error': 'The file has problems: ' + '; '.join(errors[:5]) + (' ...' if len(errors) > 5 else '')}), 400
        return jsonify({'ok': True, 'modules': mods, 'warnings': warnings,
                        'source': data.get('source') if isinstance(data, dict) else None,
                        'file_project': data.get('project') if isinstance(data, dict) else None})

    @bp.route('/projects/<int:pid>/modules/apply', methods=['POST'])
    @login_required
    def apply(pid):
        project_or_404(pid)
        try:
            mods = json.loads(request.form.get('tree', '[]'))
            assert isinstance(mods, list) and all(isinstance(m, dict) for m in mods)
        except (ValueError, AssertionError):
            flash('Nothing to create: the module list was not readable.', 'danger')
            return redirect(url_for('modules.setup', pid=pid))
        c = con()
        with c:
            am, asub = apply_tree(c, pid, mods)
        flash(f'Created {am} module{"s" if am != 1 else ""} and {asub} sub-module{"s" if asub != 1 else ""}.'
              + (' Modules that already existed were kept as they are.' if (am + asub) < len(mods) + sum(len(m.get("subs") or []) for m in mods) else ''),
              'success')
        return redirect(url_for('project', pid=pid))

    app.register_blueprint(bp)
