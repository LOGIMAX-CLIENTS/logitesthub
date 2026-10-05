"""Settings: Roles & Permissions, System, Audit Log (+ the shared tab bar for Users / Environments).

Permissions are enforced centrally in a before_request hook (endpoint -> permission), so a direct URL,
form post or CLI call is refused too - hiding a button is never the only guard. Admin always has every
permission; Viewer / Tester are configurable in the matrix. Every web POST is written to the audit log
with who, what, the result message and the before -> after values of the record it changed.

Registered from app.py with register(app, con, admin_required, current_user) so this module never
imports app (app.py runs as __main__).
"""
import functools
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta

from flask import (Blueprint, abort, flash, g, jsonify, message_flashed, redirect, render_template, request,
                   session, url_for)

import db

ROLES = [   # key stored in users.role -> label, meaning
    ('viewer', 'Viewer', 'Looks at projects, test cases and run results. Changes nothing.'),
    ('support', 'Support', 'Reproduces customer issues: adds and runs tests, shares run links.'),
    ('developer', 'Developer', 'Writes and runs tests, uses AI, checks code fixes.'),
    ('member', 'Tester', 'Writes and runs tests.'),
    ('admin', 'Admin', 'Users, environments and AI usage; permissions set by the Super Admin.'),
    ('superadmin', 'Super Admin', 'Everything, including Roles & Permissions and System. Cannot be limited.'),
]
ROLE_LABEL = {k: label for k, label, _ in ROLES}
ROLE_KEYS = [k for k, _, _ in ROLES]
ROLE_ICONS = {   # svg paths for the Roles & Permissions header
    'viewer': '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    'support': '<path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="14" width="4" height="6" rx="1.5"/><rect x="17" y="14" width="4" height="6" rx="1.5"/>',
    'developer': '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    'member': '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/>',
    'admin': '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    'superadmin': '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.3 7.5 9.5 4.3-1.2 7.5-4.9 7.5-9.5V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
}
ADMIN_ROLES = ('admin', 'superadmin')   # may open Settings (users, environments, AI usage)


def is_admin(role):
    return role in ADMIN_ROLES


def is_super(role):
    return role == 'superadmin'

# Departments a user can belong to (Users page, User Report, Test Assistant activity). Add a name here to offer it.
DEPARTMENTS = ['Developer', 'Implementation', 'Support']
# Accent colours a user can pick in Profile -> Appearance (the background is always white). key -> label, main, gradient end
ACCENTS = [('green', 'Green', '#07a190', '#32bf6d'), ('blue', 'Blue', '#2563eb', '#3b9bf6'), ('purple', 'Purple', '#6d3df5', '#a855f7'),
           ('orange', 'Orange', '#e8650f', '#f59e0b')]
ACCENT_KEYS = [k for k, _, _, _ in ACCENTS]
EDITABLE_ROLES = ('viewer', 'support', 'developer', 'member', 'admin')

PERMS = [   # key, label, what it covers
    ('cases.create', 'Create & import test cases', 'Add Test Case, Import _test.md files'),
    ('cases.edit', 'Edit test cases & folders', 'Edit steps / title, QA status, move; new, rename, archive folders'),
    ('ai.generate', 'Generate test cases with AI', 'Generate with AI on the web and from the CLI (uses Anthropic credits)'),
    ('runs.run', 'Run tests', 'Run a test case, Run selected, Verify code, AI steps from the CLI'),
    ('testruns.manage', 'Manage test runs & schedules', 'Create, edit, delete and execute test runs; schedules'),
    ('runs.share', 'Share run links', 'Public links to a run report (with expiry)'),
    ('projects.create', 'Create projects', 'New project'),
    ('reports.view', 'View User Report', 'Cases, runs and AI tokens per user, with cost'),
]
PERM_KEYS = [p[0] for p in PERMS]
# Viewer: nothing. Tester: what members could always do (so nothing changes until an admin edits the matrix).
DEFAULTS = {'viewer': set(), 'member': set(PERM_KEYS) - {'reports.view'},
            'developer': set(PERM_KEYS) - {'reports.view'},
            'support': {'cases.create', 'runs.run', 'runs.share'},
            'admin': set(PERM_KEYS)}

ENDPOINT_PERM = {
    'case_new': 'cases.create', 'cases_import': 'cases.create',
    'case_edit': 'cases.edit', 'case_title_update': 'cases.edit', 'case_qa': 'cases.edit',
    'folder_new': 'cases.edit', 'folder_rename': 'cases.edit', 'folder_archive': 'cases.edit',
    'cases_generate': 'ai.generate', 'api.ai_generate': 'ai.generate',
    'case_run': 'runs.run', 'case_verify': 'runs.run', 'testruns.run_now': 'runs.run', 'api.ai_step': 'runs.run', 'api.run_upload': 'runs.run',
    'testruns.create': 'testruns.manage', 'testruns.edit': 'testruns.manage', 'testruns.delete': 'testruns.manage',
    'testruns.set_cases': 'testruns.manage', 'testruns.schedule_new': 'testruns.manage',
    'testruns.schedule_action': 'testruns.manage',
    'run_share': 'runs.share', 'run_share_link': 'runs.share', 'run_unshare': 'runs.share',
    'project_new': 'projects.create',
    'reports.users': 'reports.view',
    'project_login': 'cases.edit', 'api.sql': 'runs.run', 'api.memory_set': 'runs.run', 'modules.setup': 'cases.edit', 'modules.read': 'cases.edit', 'modules.parse': 'cases.edit',
    'modules.apply': 'cases.edit', 'modules.shot': 'cases.edit', 'modules.parse_file': 'cases.edit',
    'api.modules_push': 'cases.edit',
    'assistant.send': 'ai.generate', 'assistant.save': 'cases.create',
    'lessons.new': 'cases.edit', 'lessons.update': 'cases.edit', 'api.lessons_add': 'runs.run',
}

SYSTEM = [   # key, label, help, default
    ('sys.vars_file', 'Variables file', 'JSON with base_url, username, password, branch... used when a run has no environment.',
     'F:/TestMU-Ai/.testmuai/variables/etail.json'),
    ('sys.helpers_dir', 'Shared helpers folder', 'Where "@import ../helpers/login.md" is found for cases that have no folder of their own.',
     'F:/TestMU-Ai/.testmuai/tests/Retail/etail-lot-inward/helpers'),
    ('sys.ai_provider', 'AI provider', 'Anthropic API uses the API key on this server and its credits. Claude subscription runs the '
     'Test Assistant and Generate with AI through Claude Code signed in on this PC (for testing; screenshot steps in runs '
     'still use the API).', 'api'),
    ('sys.ai_model', 'AI model', 'Claude model for AI steps and Generate with AI.', 'claude-opus-5-5'),
    ('sys.audit_days', 'Keep audit log (days)', 'Older audit entries are removed automatically (7 to 365).', '90'),
]
SYS_DEFAULT = {k: d for k, _, _, d in SYSTEM}
AI_PROVIDERS = {'api': 'Anthropic API (credits)', 'claude-code': 'Claude subscription via Claude Code (testing)'}

SCHEMA = """
CREATE TABLE IF NOT EXISTS role_perms (
  role VARCHAR(16) NOT NULL, perm VARCHAR(48) NOT NULL, allowed TINYINT NOT NULL,
  PRIMARY KEY (role, perm)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS audit_log (
  id INT AUTO_INCREMENT PRIMARY KEY, at VARCHAR(32) NOT NULL, username VARCHAR(64), action VARCHAR(64) NOT NULL,
  entity VARCHAR(32) NOT NULL, entity_id INT NULL, result VARCHAR(8) NOT NULL, message VARCHAR(500),
  changes MEDIUMTEXT, ip VARCHAR(64),
  KEY ix_audit_at (at), KEY ix_audit_entity (entity, id), KEY ix_audit_user (username, id)
) ENGINE=InnoDB;
"""

# endpoint -> (entity, table, view arg holding the id). Rows are snapshotted before and after the POST.
AUDIT_TARGET = {
    'user_role': ('user', 'users', 'uid'), 'user_delete': ('user', 'users', 'uid'),
    'settings.user_department': ('user', 'users', 'uid'),
    'user_reset_password': ('user', 'users', 'uid'), 'user_ai_limit': ('user', 'users', 'uid'),
    'user_ai_extra': ('user', 'users', 'uid'), 'user_ai_extra_delete': ('user', 'users', 'uid'),
    'environment_update': ('environment', 'environments', 'eid'), 'project_login': ('project', 'projects', 'pid'),
    'case_edit': ('test case', 'cases', 'cid'), 'case_title_update': ('test case', 'cases', 'cid'),
    'case_qa': ('test case', 'cases', 'cid'), 'case_run': ('test case', 'cases', 'cid'),
    'case_verify': ('test case', 'cases', 'cid'),
    'folder_rename': ('folder', 'folders', 'fid'), 'folder_archive': ('folder', 'folders', 'fid'),
    'testruns.edit': ('test run', 'test_runs', 'tid'), 'testruns.delete': ('test run', 'test_runs', 'tid'),
    'testruns.set_cases': ('test run', 'test_runs', 'tid'), 'testruns.run_now': ('test run', 'test_runs', 'tid'),
    'run_share': ('run', 'runs', 'rid'), 'run_share_link': ('run', 'runs', 'rid'), 'run_unshare': ('run', 'runs', 'rid'),
    'ai_credit_delete': ('ai credit', 'ai_credits', 'cid'), 'ai_credit_rate': ('ai credit', 'ai_credits', 'cid'),
    'lessons.update': ('lesson', 'lessons', 'lid'),
}
ENTITY_BY_PREFIX = [   # fallback entity from the endpoint name
    ('user', 'user'), ('environment', 'environment'), ('lessons.', 'lesson'), ('ai_', 'ai budget'), ('case', 'test case'),
    ('folder', 'folder'), ('project', 'project'), ('testruns.schedule', 'schedule'), ('testruns.', 'test run'),
    ('run_', 'run'), ('settings.', 'settings'), ('login', 'sign-in'), ('logout', 'sign-in'), ('profile', 'profile'),
]
SECRET_PARTS = ('pass', 'secret', 'token', 'hash', 'enc')     # any column / field containing these
SECRET_FIELDS = ('csrf', 'current', 'confirm', 'new', 'key')    # password-change form fields, exact names
LONG_FIELDS = ('content',)


# ---------------------------------------------------------------- permissions

def role_perms(con):
    """{role: set(perms)} for the editable roles: stored matrix over the defaults."""
    out = {r: set(DEFAULTS[r]) for r in EDITABLE_ROLES}
    for r in con.execute('SELECT role, perm, allowed FROM role_perms'):
        if r['role'] in out and r['perm'] in PERM_KEYS:
            (out[r['role']].add if r['allowed'] else out[r['role']].discard)(r['perm'])
    return out


def can(con, role, perm):
    if role == 'superadmin':
        return True
    cache = g.setdefault('_perms', None)
    if cache is None:
        cache = g._perms = role_perms(con)
    return perm in cache.get(role, set())


def _needed_perm():
    ep = request.endpoint or ''
    if ep == 'cases_bulk' and request.method == 'POST':
        return 'runs.run' if request.form.get('action') == 'run' else 'cases.edit'
    return ENDPOINT_PERM.get(ep)


def _api_role(con):
    """Role of the user named in the API Basic-auth header. Only ever used to refuse: api_auth still
    checks the key, so a wrong name cannot gain anything."""
    import api
    username, _ = api._credentials()
    r = con.execute('SELECT role FROM users WHERE username=?', (username,)).fetchone() if username else None
    return r['role'] if r else None


# ---------------------------------------------------------------- audit

def _safe(col, val):
    c = col.lower()
    if c in SECRET_FIELDS or any(w in c for w in SECRET_PARTS):
        return '••••' if val not in (None, '') else val
    if c in LONG_FIELDS and val is not None:
        return f'({len(str(val))} characters)'
    if isinstance(val, str) and len(val) > 200:
        return val[:200] + '…'
    return val


def _snapshot(con, table, rid):
    if not rid:
        return None
    try:
        r = con.execute(f'SELECT * FROM {table} WHERE id=?', (rid,)).fetchone()   # table comes from AUDIT_TARGET only
    except Exception:
        return None
    return {k: _safe(k, v) for k, v in r.items()} if r else None


def _diff(before, after):
    if before is None and after is None:
        return None
    if after is None:
        return {'deleted': before}
    if before is None:
        return {'created': after}
    ch = {}
    for k in after:
        if before.get(k) != after.get(k):
            ch[k] = [before.get(k), after.get(k)]
    if 'content' in ch:   # lengths are equal when only the text changed
        ch['content'] = ['(changed)', after.get('content')]
    return ch


def write_audit(username, action, entity, entity_id=None, result='ok', message=None, changes=None, ip=None):
    con = db.connect()
    try:
        days = _int(_get(con, 'sys.audit_days'), 90, 7, 365)
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        con.execute('DELETE FROM audit_log WHERE at < ? LIMIT 500', (cutoff,))
        con.execute('INSERT INTO audit_log(at, username, action, entity, entity_id, result, message, changes, ip) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    (db.now(), username, action[:64], entity[:32], entity_id, result, (message or '')[:500] or None,
                     json.dumps(changes, default=str, ensure_ascii=False) if changes else None, ip))
    finally:
        con.close()


def _form_summary():
    out = {}
    for k in request.form.keys():
        vals = request.form.getlist(k)
        v = vals if len(vals) > 1 else vals[0]
        if k in ('csrf', 'back'):
            continue
        out[k] = _safe(k, v if not isinstance(v, list) else ', '.join(vals)[:200])
    if request.files:
        out['files'] = ', '.join(f.filename for f in request.files.getlist('files') if f.filename)[:200]
    return out


# ---------------------------------------------------------------- system settings

def _get(con, key):
    import budget
    v = budget.get_setting(con, key)
    return v if v not in (None, '') else SYS_DEFAULT.get(key)


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


def apply_system(con):
    """Pushes the System settings to the places that read them: db.VARS_FILE for the web app, and
    environment variables inherited by every runner / generator process started afterwards."""
    db.VARS_FILE = _get(con, 'sys.vars_file')
    os.environ['LTH_VARS_FILE'] = db.VARS_FILE
    os.environ['LTH_HELPERS_DIR'] = _get(con, 'sys.helpers_dir')
    os.environ['ETAIL_AI_MODEL'] = _get(con, 'sys.ai_model')
    os.environ['LTH_AI_PROVIDER'] = _get(con, 'sys.ai_provider')


_INFO = {}


def _system_info(con):
    if not _INFO:
        try:
            _INFO['node'] = subprocess.run(['node', '-v'], capture_output=True, text=True, timeout=10).stdout.strip() or '-'
        except Exception:
            _INFO['node'] = 'not found'
        try:
            with open(os.path.join(db.RUNNER_DIR, 'node_modules', 'playwright', 'package.json'), encoding='utf-8') as f:
                _INFO['playwright'] = json.load(f).get('version', '-')
        except Exception:
            _INFO['playwright'] = 'not installed'
    cfg = db.db_config()
    v = con.execute('SELECT VERSION() AS v').fetchone()['v']
    runs = sum(1 for d in os.scandir(db.RUNS_DIR) if d.is_dir()) if os.path.isdir(db.RUNS_DIR) else 0
    return [
        ('Database', f"MySQL {v} · {cfg['host']}:{cfg['port']} · {cfg['database']}"),
        ('Unified_DB (PM sign-in)', (lambda u: f"{u['user']} @ {u['host']}:{u['port']} · {u['database']} (read-only)")(db.db_config('unified'))
            if db.configured('unified') else 'Not set: sign-in with local LogiTestHub accounts only'),
        ('Python', f'{platform.python_version()} ({sys.executable})'),
        ('Node.js', _INFO['node']),
        ('Playwright', _INFO['playwright']),
        ('Anthropic API key', 'Set on this server' if os.environ.get('ANTHROPIC_API_KEY') else 'Not set: AI steps and Generate with AI will not work'),
        ('Runner folder', db.RUNNER_DIR),
        ('Run recordings', f'{runs} run folders in {db.RUNS_DIR}'),
    ]


def dmy(ts, seconds=False):
    """'2026-10-01 14:09:33' -> '01-10-2026 14:09' (day-month-year). Anything else is shown as it is."""
    if not ts:
        return '-'
    s = str(ts)
    try:
        d = datetime.strptime(s[:19] if len(s) >= 19 else s[:10], '%Y-%m-%d %H:%M:%S' if len(s) >= 19 else '%Y-%m-%d')
    except ValueError:
        return s
    if len(s) < 19:
        return d.strftime('%d-%m-%Y')
    return d.strftime('%d-%m-%Y %H:%M:%S' if seconds else '%d-%m-%Y %H:%M')


# ---------------------------------------------------------------- blueprint + hooks

def register(app, con, admin_required, current_user):
    c0 = db.connect()
    try:
        cur = c0._raw.cursor()
        for stmt in (s.strip() for s in SCHEMA.split(';')):
            if stmt:
                cur.execute(stmt)
        if not c0.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                          "AND TABLE_NAME='users' AND COLUMN_NAME='department'").fetchone()['n']:
            cur.execute('ALTER TABLE users ADD COLUMN department VARCHAR(40) NULL')
        if not c0.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                          "AND TABLE_NAME='users' AND COLUMN_NAME='accent'").fetchone()['n']:
            cur.execute('ALTER TABLE users ADD COLUMN accent VARCHAR(16) NULL')   # Profile -> Appearance; NULL = green
        # Super Admin role added later: the admins who ran everything until now become Super Admins (once)
        if not c0.execute("SELECT COUNT(*) AS n FROM users WHERE role='superadmin'").fetchone()['n']:
            cur.execute("UPDATE users SET role='superadmin' WHERE role='admin'")
            c0._raw.commit()
        apply_system(c0)
    finally:
        c0.close()

    bp = Blueprint('settings', __name__, url_prefix='/settings')
    app.jinja_env.filters['dmy'] = dmy

    @app.context_processor
    def _perm_globals():
        me = current_user()
        return {'can': (lambda perm: bool(me) and can(con(), me['role'], perm)), 'role_label': ROLE_LABEL,
                'roles': ROLES, 'departments': DEPARTMENTS,
                'accents': ACCENTS, 'accent': (me.get('accent') if me and me.get('accent') in ACCENT_KEYS else 'green'),
                'is_admin': bool(me) and is_admin(me['role']), 'is_super': bool(me) and is_super(me['role'])}

    def super_required(fn):
        """Roles & Permissions changes and System settings: Super Admin only."""
        @functools.wraps(fn)
        @admin_required
        def wrapper(*a, **kw):
            if not is_super(current_user()['role']):
                if request.method == 'POST' and request.endpoint == 'settings.roles_toggle':
                    return jsonify({'ok': False, 'message': 'Only a Super Admin can change permissions.'}), 403
                flash('Only a Super Admin can change this.', 'danger')
                return redirect(request.referrer or url_for('users'))
            return fn(*a, **kw)
        return wrapper

    # ---- audit every web change
    def _on_flash(sender, message, category, **extra):
        if g.get('_audit') is not None:
            g._audit['flashes'].append((category, message))
    message_flashed.connect(_on_flash, app, weak=False)

    @app.before_request
    def _audit_start():
        if request.method != 'POST' or request.path.startswith(('/api/', '/static/')):
            return None
        ep = request.endpoint or ''
        target = AUDIT_TARGET.get(ep)
        before = None
        if target:
            before = _snapshot(con(), target[1], (request.view_args or {}).get(target[2]))
        g._audit = {'flashes': [], 'before': before, 'target': target}
        return None

    @app.after_request
    def _audit_end(resp):
        a = g.get('_audit')
        if a is None or g.get('_audit_done') or (request.endpoint or '') in ('settings.roles_toggle', 'settings.system_save'):
            return resp
        ep = request.endpoint or '?'
        target = a['target']
        entity = target[0] if target else next((e for p, e in ENTITY_BY_PREFIX if ep.startswith(p)), 'other')
        rid = (request.view_args or {}).get(target[2]) if target else next(
            (v for v in (request.view_args or {}).values() if isinstance(v, int)), None)
        failed = resp.status_code >= 400 or any(c == 'danger' for c, _ in a['flashes'])
        msg = '; '.join(m for _, m in a['flashes'])[:500] or None
        changes = None
        if target:
            changes = _diff(a['before'], _snapshot(db_con := db.connect(), target[1], rid))
            db_con.close()
        form = _form_summary()
        if ep == 'login':
            who = request.form.get('username', '').strip()[:64] or None
            form = {}
        else:
            me = current_user()
            who = me['username'] if me else None
        try:
            write_audit(who, ep, entity, rid, 'failed' if failed else 'ok', msg,
                        {'changed': changes, 'form': form} if (changes or form) else None, request.remote_addr)
        except Exception:
            app.logger.exception('audit log write failed')   # never break the user's action over the log
        return resp

    # ---- permission guard (web + API). Registered after _audit_start so refused attempts are logged too.
    @app.before_request
    def _guard():
        perm = _needed_perm()
        if not perm:
            return None
        if (request.endpoint or '').startswith('api.'):
            role = _api_role(con())
            if role and not can(con(), role, perm):
                return jsonify({'ok': False, 'error': 'forbidden',
                                'message': f'Your role ({ROLE_LABEL.get(role, role)}) is not allowed to do this. Ask an admin.'}), 403
            return None
        me = current_user()
        if not me or can(con(), me['role'], perm):
            return None   # signed out: the route's own login check redirects to the sign-in page
        label = dict((k, l) for k, l, _ in PERMS)[perm]
        flash(f'Your role ({ROLE_LABEL.get(me["role"], me["role"])}) does not allow "{label}". Ask an admin.', 'danger')
        back = request.referrer if request.referrer and request.referrer != request.url else url_for('home')
        return redirect(back)

    # ---- pages
    @bp.route('/users/<int:uid>/department', methods=['POST'])
    @admin_required
    def user_department(uid):
        u = con().execute('SELECT id, username FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            abort(404)
        dept = request.form.get('department', '').strip()
        if dept and dept not in DEPARTMENTS:
            flash('Choose a department from the list.', 'danger')
        else:
            c = con()
            with c:
                c.execute('UPDATE users SET department=? WHERE id=?', (dept or None, uid))
            flash(f'@{u["username"]}: department {"set to " + dept if dept else "cleared"}.', 'success')
        return redirect(url_for('users', dept=request.form.get('back_dept') or None))

    @bp.route('/')
    @admin_required
    def index():
        return redirect(url_for('users'))

    @bp.route('/roles')
    @admin_required
    def roles():
        c = con()
        counts = {r['role']: r['n'] for r in c.execute('SELECT role, COUNT(*) AS n FROM users GROUP BY role')}
        return render_template('settings_roles.html', perms=PERMS, matrix=role_perms(c), counts=counts,
                               editable=EDITABLE_ROLES, defaults=DEFAULTS, role_icons=ROLE_ICONS)

    @bp.route('/roles/toggle', methods=['POST'])
    @super_required
    def roles_toggle():
        role, perm = request.form.get('role', ''), request.form.get('perm', '')
        if role not in EDITABLE_ROLES or perm not in PERM_KEYS:
            return jsonify({'ok': False, 'message': 'Unknown role or permission.'}), 400
        c = con()
        was = perm in role_perms(c)[role]
        with c:
            c.execute('INSERT INTO role_perms(role, perm, allowed) VALUES (?,?,?) '
                      'ON DUPLICATE KEY UPDATE allowed=VALUES(allowed)', (role, perm, 0 if was else 1))
        me = current_user()
        write_audit(me['username'], 'roles_toggle', 'role', None, 'ok',
                    f'{ROLE_LABEL[role]}: "{dict((k, l) for k, l, _ in PERMS)[perm]}" {"removed" if was else "allowed"}',
                    {'changed': {f'{role}.{perm}': ['allowed' if was else 'not allowed', 'not allowed' if was else 'allowed']}},
                    request.remote_addr)
        return jsonify({'ok': True, 'allowed': not was})

    @bp.route('/roles/reset', methods=['POST'])
    @super_required
    def roles_reset():
        c = con()
        with c:
            c.execute('DELETE FROM role_perms')
        flash('Permissions are back to the defaults.', 'success')
        return redirect(url_for('settings.roles'))

    @bp.route('/system', methods=['GET'])
    @admin_required
    def system():
        import pricing
        c = con()
        vals = {k: _get(c, k) for k, _, _, _ in SYSTEM}
        return render_template('settings_system.html', fields=SYSTEM, vals=vals, models=list(pricing.PRICES), providers=AI_PROVIDERS,
                               info=_system_info(c))

    @bp.route('/system', methods=['POST'])
    @super_required
    def system_save():
        import budget
        import pricing
        c = con()
        f = request.form
        new = {k: f.get(k, '').strip() for k, _, _, _ in SYSTEM}
        errors = []
        try:
            with open(new['sys.vars_file'], encoding='utf-8') as fh:
                if not isinstance(json.load(fh), dict):
                    raise ValueError
        except (OSError, ValueError):
            errors.append(f'Variables file: "{new["sys.vars_file"]}" is not a readable JSON file.')
        if not os.path.isdir(new['sys.helpers_dir']):
            errors.append(f'Shared helpers folder: "{new["sys.helpers_dir"]}" does not exist.')
        if new['sys.ai_provider'] not in AI_PROVIDERS:
            errors.append('AI provider: choose one from the list.')
        if new['sys.ai_model'] not in pricing.PRICES:
            errors.append('AI model: choose one from the list.')
        if not new['sys.audit_days'].isdigit() or not 7 <= int(new['sys.audit_days']) <= 365:
            errors.append('Keep audit log: a whole number of days from 7 to 365.')
        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('settings.system'))
        old = {k: _get(c, k) for k in new}
        with c:
            for k, v in new.items():
                budget.set_setting(c, k, v)
        apply_system(c)
        changed = {k: [old[k], v] for k, v in new.items() if str(old[k]) != v}
        me = current_user()
        write_audit(me['username'], 'system_save', 'settings', None, 'ok',
                    'System settings saved' if changed else 'System settings saved (no change)',
                    {'changed': changed} if changed else None, request.remote_addr)
        flash('System settings saved. New runs use them straight away.' if changed else 'Nothing changed.', 'success')
        return redirect(url_for('settings.system'))

    @bp.route('/audit')
    @admin_required
    def audit():
        c = con()
        a = request.args
        where, params = [], []
        for col, key in (('entity', 'entity'), ('action', 'action'), ('username', 'user'), ('result', 'result')):
            v = a.get(key, '').strip()
            if v:
                where.append(f'{col}=?')
                params.append(v)
        w = ('WHERE ' + ' AND '.join(where)) if where else ''
        page = max(1, a.get('page', 1, type=int))
        size = 50
        total = c.execute(f'SELECT COUNT(*) FROM audit_log {w}', params).fetchone()[0]
        rows = c.execute(f'SELECT * FROM audit_log {w} ORDER BY id DESC LIMIT {size} OFFSET {(page - 1) * size}',
                         params).fetchall()
        for r in rows:
            try:
                r['changes'] = json.loads(r['changes']) if r['changes'] else None
            except ValueError:
                r['changes'] = None
        opts = {col: [x[0] for x in c.execute(f'SELECT DISTINCT {col} FROM audit_log WHERE {col} IS NOT NULL ORDER BY 1')]
                for col in ('entity', 'action', 'username')}
        days = _int(_get(c, 'sys.audit_days'), 90, 7, 365)
        return render_template('settings_audit.html', rows=rows, total=total, page=page, size=size, opts=opts,
                               f={k: a.get(k, '') for k in ('entity', 'action', 'user', 'result')}, days=days)

    app.register_blueprint(bp)
