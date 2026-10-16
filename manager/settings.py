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
import re
import shutil
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
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def email_problem(con, email, uid=None):
    """Why an account email cannot be used (None when it can). Emails are unique: a PM click must find one account."""
    if len(email) > 254 or not EMAIL_RE.match(email):
        return 'Email: a valid address, e.g. name@logimaxindia.com.'
    r = con.execute('SELECT username FROM users WHERE LOWER(email)=? AND id<>?', (email.lower(), uid or 0)).fetchone()
    return f'Email {email} is already used by @{r["username"]}.' if r else None
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
    'project_login': 'cases.edit', 'api.sql': 'runs.run', 'api.sql_write': 'runs.run', 'api.memory_set': 'runs.run', 'modules.setup': 'cases.edit', 'modules.read': 'cases.edit', 'modules.parse': 'cases.edit',
    'modules.apply': 'cases.edit', 'modules.shot': 'cases.edit', 'modules.parse_file': 'cases.edit',
    'api.modules_push': 'cases.edit', 'api.case_update': 'cases.edit',
    'assistant.send': 'ai.generate', 'assistant.save': 'cases.create',
    'lessons.new': 'cases.edit', 'lessons.update': 'cases.edit', 'api.lessons_add': 'runs.run',
    'api.workspace_report': 'runs.run',
}

SYSTEM = [   # key, label, help, default
    # '' = automatic: the tests/ folder of the project the tester CLI last worked in (see _auto_paths)
    ('sys.vars_file', 'Variables file', 'JSON with base_url, username, password, branch... used when a run has no environment. '
     'Leave empty to use tests/variables/ of the project where test cases were last created.', ''),
    ('sys.helpers_dir', 'Shared helpers folder', 'Where "@import ../helpers/login.md" is found for cases that have no folder of their own. '
     'Leave empty to use tests/helpers/ of the project where test cases were last created.', ''),
    ('sys.ai_provider', 'AI provider', 'Anthropic API uses the API key on this server and its credits. Claude subscription runs the '
     'Test Assistant and Generate with AI through Claude Code signed in on this PC (for testing; screenshot steps in runs '
     'still use the API).', 'api'),
    ('sys.ai_model', 'API model', 'Model for the Anthropic API: AI steps in runs, and the Test Assistant / Generate with AI when '
     'the provider is the API.', 'claude-opus-5-5'),
    ('sys.claude_model', 'Claude subscription model', 'Model for Claude Code (provider: Claude subscription). Empty = the API model.', ''),
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
    if v in (None, '') and key in AUTO_KEYS:
        return _auto_paths(con)[key]
    return v if v not in (None, '') else SYS_DEFAULT.get(key)


# ---------------------------------------------------------------- the project's tests/ folder (tester CLI workspace)
# The tester CLI keeps test cases in the developer's project, in tests/ (tests/tester.json, tests/helpers/,
# tests/variables/). Each CLI command there reports that folder (POST /api/v1/workspace); when it is on this PC, an
# empty "Variables file" / "Shared helpers folder" means that folder's variables and helpers. With no such folder
# yet, LogiTestHub's own data/tests/ is used, so nothing points into another product's folders.

AUTO_KEYS = ('sys.vars_file', 'sys.helpers_dir')
WORKSPACE_KEY = 'sys.workspace'   # JSON: {tests_dir, root, project, by, at}
LEGACY = {   # where these lived before (TestMU-Ai); their files are copied in once so existing cases keep working
    'sys.vars_file': 'F:/TestMU-Ai/.testmuai/variables/etail.json',
    'sys.helpers_dir': 'F:/TestMU-Ai/.testmuai/tests/Retail/etail-lot-inward/helpers',
}


def workspace(con):
    import budget
    try:
        ws = json.loads(budget.get_setting(con, WORKSPACE_KEY) or '{}')
    except ValueError:
        ws = {}
    return ws if ws.get('tests_dir') and os.path.isfile(os.path.join(ws['tests_dir'], 'tester.json')) else {}


def set_workspace(con, tests_dir, root, project, username):
    """Called by the CLI. Only a folder on this PC counts (a CLI on another PC reports a path we cannot read)."""
    import budget
    tests_dir = os.path.abspath(tests_dir)
    if not os.path.isfile(os.path.join(tests_dir, 'tester.json')):
        return False
    old = workspace(con)
    budget.set_setting(con, WORKSPACE_KEY, json.dumps({'tests_dir': tests_dir, 'root': root, 'project': project,
                                                       'by': username, 'at': db.now()}))
    con.commit()
    if old.get('tests_dir') != tests_dir:
        apply_system(con)
    return True


def _filled(vars_file):
    """True when the variables file has at least one value (the CLI's new template has only empty ones)."""
    try:
        with open(vars_file, encoding='utf-8') as f:
            raw = json.load(f)
        return any(str(v.get('value', '') if isinstance(v, dict) else v).strip() for v in raw.values())
    except (OSError, ValueError, AttributeError):
        return False


def _auto_paths(con):
    ws = workspace(con)
    tests = ws.get('tests_dir') or os.path.join(db.DATA, 'tests')
    helpers = os.path.join(tests, 'helpers')
    vdir = os.path.join(tests, 'variables')
    name = 'variables.json'
    try:
        with open(os.path.join(tests, 'tester.json'), encoding='utf-8') as f:
            name = os.path.basename(json.load(f).get('variables') or '') or name
    except (OSError, ValueError, AttributeError):
        if os.path.isdir(vdir):
            name = next((x for x in sorted(os.listdir(vdir)) if x.endswith('.json')), name)
    vars_file = os.path.join(vdir, name)
    try:
        os.makedirs(helpers, exist_ok=True)
        os.makedirs(vdir, exist_ok=True)
        old_h = LEGACY['sys.helpers_dir']   # first time: bring the helpers (login.md ...) and variables over
        if os.path.isdir(old_h) and not any(x.endswith('.md') for x in os.listdir(helpers)):
            for x in os.listdir(old_h):
                if os.path.isfile(os.path.join(old_h, x)):
                    shutil.copy2(os.path.join(old_h, x), os.path.join(helpers, x))
        if not _filled(vars_file):   # missing, or the CLI's empty template
            if os.path.isfile(LEGACY['sys.vars_file']):
                shutil.copy2(LEGACY['sys.vars_file'], vars_file)
            elif not os.path.isfile(vars_file):
                with open(vars_file, 'w', encoding='utf-8') as f:
                    f.write('{}\n')
    except OSError:
        pass   # read-only folder: the paths are still shown, the runner reports what is missing
    return {'sys.vars_file': vars_file.replace('\\', '/'), 'sys.helpers_dir': helpers.replace('\\', '/'),
            'source': ws}


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


def apply_system(con):
    """Pushes the System settings to the places that read them: db.VARS_FILE for the web app, and
    environment variables inherited by every runner / generator process started afterwards."""
    import envs
    db.VARS_FILE = envs.BASE_VARS = _get(con, 'sys.vars_file')
    os.environ['LTH_VARS_FILE'] = db.VARS_FILE
    os.environ['LTH_HELPERS_DIR'] = _get(con, 'sys.helpers_dir')
    os.environ['ETAIL_AI_MODEL'] = _get(con, 'sys.ai_model')
    cm = _get(con, 'sys.claude_model') or _CLAUDE_MODEL_STARTED_WITH   # lib/ai.js: LTH_CLAUDE_MODEL, else ETAIL_AI_MODEL
    if cm:
        os.environ['LTH_CLAUDE_MODEL'] = cm
    else:
        os.environ.pop('LTH_CLAUDE_MODEL', None)
    os.environ['LTH_AI_PROVIDER'] = _get(con, 'sys.ai_provider')
    import budget   # AI keys saved here; none = what the server was started with (its env file / start script)
    for name, (env, _, _, _) in SECRETS.items():
        v = envs.decrypt(budget.get_setting(con, name) or '') or _SECRETS_STARTED_WITH[env]
        if v:
            os.environ[env] = v
        else:
            os.environ.pop(env, None)


# ---------------------------------------------------------------- AI keys (Settings -> System)
# Pasted in Settings -> System, stored encrypted (envs.encrypt), never shown again; an empty box keeps the saved one.
# - Anthropic API key: AI steps in runs, Generate with AI, and the Test Assistant when the provider is the API.
# - Claude subscription token: the Test Assistant / Generate with AI run `claude -p` (lib/ai.js) when the provider is
#   the Claude subscription. A server has no browser to sign in with, so it uses a long-lived token made on any PC
#   with `claude setup-token` (CLAUDE_CODE_OAUTH_TOKEN).
SECRETS = {   # setting -> (environment variable, pattern, label, where to get it)
    # sk-ant-api03-... (Console API key), sk-ant-usr-... and other sk-ant- kinds; not a subscription token (sk-ant-oat)
    'sys.anthropic_key': ('ANTHROPIC_API_KEY', re.compile(r'^sk-ant-(?!oat)[a-z]+\d*-[A-Za-z0-9_\-]{20,}$'), 'Anthropic API key',
                          'console.anthropic.com -> API keys (it starts with sk-ant-, not sk-ant-oat)'),
    'sys.claude_token': ('CLAUDE_CODE_OAUTH_TOKEN', re.compile(r'^sk-ant-oat\d\d-[A-Za-z0-9_\-]{20,}$'), 'Claude subscription token',
                         '`claude setup-token` (it starts with sk-ant-oat)'),
}
_SECRETS_STARTED_WITH = {env: os.environ.get(env) for env, _, _, _ in SECRETS.values()}
_CLAUDE_MODEL_STARTED_WITH = os.environ.get('LTH_CLAUDE_MODEL')


def mask(key):
    """sk-ant-api03-AbC...9AAA: enough to tell which key it is (match the hint in the Anthropic Console), not to use it."""
    return f'{key[:14]}...{key[-4:]}' if key and len(key) > 24 else '...'


def secret_source(con, name):
    """Which key is in use (masked) and where it comes from, for System information."""
    import budget
    env = SECRETS[name][0]
    key = os.environ.get(env)
    if not key:
        return None
    where = 'saved here (Settings -> System)' if budget.get_setting(con, name) else f'from the server settings ({env})'
    return f'{mask(key)} · {where}'


def _check(con, name):
    """The last Test of the key in use: {'ok', 'at', 'msg'} or None (never tested, or the key changed since)."""
    import budget
    try:
        return json.loads(budget.get_setting(con, name + '_check') or 'null')
    except ValueError:
        return None


def anthropic_test(key=None):
    """Checks an Anthropic API key (typed, else the one in use) by listing models: no tokens used. Returns (ok, message)."""
    import urllib.error
    import urllib.request
    key = key or os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        return False, 'No Anthropic API key on this server: paste one and Save.'
    req = urllib.request.Request('https://api.anthropic.com/v1/models?limit=1',
                                 headers={'x-api-key': key, 'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, 'Connected to the Anthropic API: the key works.'
    except urllib.error.HTTPError as e:
        return False, ('The Anthropic API refused this key (wrong, revoked or disabled).' if e.code in (401, 403)
                       else f'The Anthropic API answered HTTP {e.code}.')
    except (urllib.error.URLError, TimeoutError) as e:
        return False, f'Cannot reach api.anthropic.com from this server ({getattr(e, "reason", e)}).'


def claude_cli():
    """The claude executable, found the way lib/ai.js finds it; None when it is not installed."""
    if os.environ.get('LTH_CLAUDE_CLI'):
        return os.environ['LTH_CLAUDE_CLI']
    appdata = os.environ.get('APPDATA')
    for f in ([os.path.join(appdata, 'npm', 'node_modules', '@anthropic-ai', 'claude-code', 'bin', n) for n in ('claude.exe', 'claude')]
              if appdata else []) + ['/usr/local/bin/claude', '/usr/bin/claude']:
        if os.path.isfile(f):
            return f
    return shutil.which('claude')


def claude_test(token=None, model=None):
    """Asks Claude Code for one word with the token (typed, else the one in use). Returns (ok, message)."""
    exe = claude_cli()
    if not exe:
        return False, ('Claude Code is not installed on this server. Install it: sudo npm install -g @anthropic-ai/claude-code '
                       '(Windows: npm install -g @anthropic-ai/claude-code).')
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}   # else it bills the API
    if token:
        env['CLAUDE_CODE_OAUTH_TOKEN'] = token
    try:
        model = model or os.environ.get('LTH_CLAUDE_MODEL') or os.environ.get('ETAIL_AI_MODEL')   # as lib/ai.js
        p = subprocess.run([exe, '-p', 'Reply with the single word OK.', '--output-format', 'json', '--tools', '',
                            '--no-session-persistence', '--setting-sources', ''] + (['--model', model] if model else []),
                           capture_output=True, text=True,
                           timeout=90, env=env, cwd=db.RUNNER_DIR)
    except subprocess.TimeoutExpired:
        return False, 'Claude Code did not answer in 90 seconds.'
    except OSError as e:
        return False, f'Could not start Claude Code ({exe}): {e}'
    try:
        j = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        j = {}
    if p.returncode == 0 and not j.get('is_error'):
        model = re.sub(r'\[.*\]$', '', next((m for m in (j.get('modelUsage') or {}) if 'haiku' not in m.lower()), ''))
        return True, f'Connected to the Claude subscription{f" ({model})" if model else ""}. Answer: {str(j.get("result", "")).strip()[:40]}'
    why = str(j.get('result') or p.stderr or p.stdout or f'exit code {p.returncode}').strip()[:300]
    if re.search(r'401|invalid|expired|authenticat|log ?in', why, re.I):
        why += ' - the token is wrong or expired: make a new one with `claude setup-token`.'
    return False, f'Claude Code answered with an error: {why}'


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
        ('Anthropic API key', secret_source(con, 'sys.anthropic_key') or 'Not set: AI steps and Generate with AI will not work'),
        ('Claude Code CLI', claude_cli() or 'not installed: the Claude subscription provider cannot answer'),
        ('Claude subscription token', secret_source(con, 'sys.claude_token') or 'Not set: on a server, paste one under AI provider'),
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
        # the old TestMU-Ai paths saved here before: back to automatic (the project's tests/ folder), once
        import budget
        for k, old in LEGACY.items():
            if (budget.get_setting(c0, k) or '').replace('\\', '/').rstrip('/') == old:
                budget.set_setting(c0, k, '')
        c0.commit()
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
    @bp.route('/users/<int:uid>/email', methods=['POST'])
    @admin_required
    def user_email(uid):
        """The email PM knows this person by: a PM "Tester CLI" click from that email starts the task for this account."""
        u = con().execute('SELECT id, username, email FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            abort(404)
        email = request.form.get('email', '').strip().lower() or None
        problem = email and email_problem(con(), email, uid)
        if problem:
            flash(problem, 'danger')
        elif email != u['email']:
            c = con()
            with c:
                c.execute('UPDATE users SET email=? WHERE id=?', (email, uid))
            flash(f'@{u["username"]}: email {"set to " + email if email else "cleared"}.', 'success')
        return redirect(url_for('users', dept=request.form.get('back_dept') or None))

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
        import budget
        vals = {k: _get(c, k) for k, _, _, _ in SYSTEM}
        for k in AUTO_KEYS:   # empty = automatic: the box stays empty and shows the folder in use
            vals[k] = budget.get_setting(c, k) or ''
        return render_template('settings_system.html', fields=SYSTEM, vals=vals, models=list(pricing.PRICES), providers=AI_PROVIDERS,
                               info=_system_info(c), auto=_auto_paths(c), auto_keys=AUTO_KEYS,
                               saved={n: bool(budget.get_setting(c, n)) for n in SECRETS},
                               in_use={n: secret_source(c, n) for n in SECRETS},
                               checks={n: _check(c, n) for n in SECRETS})

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
            if new['sys.vars_file']:   # empty = automatic
                with open(new['sys.vars_file'], encoding='utf-8') as fh:
                    if not isinstance(json.load(fh), dict):
                        raise ValueError
        except (OSError, ValueError):
            errors.append(f'Variables file: "{new["sys.vars_file"]}" is not a readable JSON file.')
        if new['sys.helpers_dir'] and not os.path.isdir(new['sys.helpers_dir']):
            errors.append(f'Shared helpers folder: "{new["sys.helpers_dir"]}" does not exist.')
        if new['sys.ai_provider'] not in AI_PROVIDERS:
            errors.append('AI provider: choose one from the list.')
        if new['sys.ai_model'] not in pricing.PRICES:
            errors.append('API model: choose one from the list.')
        if new['sys.claude_model'] and new['sys.claude_model'] not in pricing.PRICES:
            errors.append('Claude subscription model: choose one from the list.')
        if not new['sys.audit_days'].isdigit() or not 7 <= int(new['sys.audit_days']) <= 365:
            errors.append('Keep audit log: a whole number of days from 7 to 365.')
        secrets = {n: re.sub(r'\s+', '', f.get(n, '')) for n in SECRETS}   # empty = keep the saved one
        for n, v in secrets.items():
            if v and not SECRETS[n][1].match(v):
                errors.append(f'{SECRETS[n][2]}: paste the whole key from {SECRETS[n][3]}.')
        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('settings.system'))
        old = {k: (budget.get_setting(c, k) or '') if k in AUTO_KEYS else _get(c, k) for k in new}
        import envs
        cleared = {n for n in SECRETS if f.get(n + '_clear')}
        with c:
            for k, v in new.items():
                budget.set_setting(c, k, v)
            for n in SECRETS:
                if secrets[n] or n in cleared:
                    budget.set_setting(c, n, '' if n in cleared else envs.encrypt(secrets[n]))
                    budget.set_setting(c, n + '_check', '')   # a new key has not been tested yet
        apply_system(c)
        changed = {k: [old[k], v] for k, v in new.items() if str(old[k]) != v}
        for n in SECRETS:   # the key itself is never written to the audit log
            if secrets[n] or n in cleared:
                changed[n] = ['', 'removed' if n in cleared else 'replaced']
        me = current_user()
        write_audit(me['username'], 'system_save', 'settings', None, 'ok',
                    'System settings saved' if changed else 'System settings saved (no change)',
                    {'changed': changed} if changed else None, request.remote_addr)
        flash('System settings saved. New runs use them straight away.' if changed else 'Nothing changed.', 'success')
        return redirect(url_for('settings.system'))

    @bp.route('/system/key-test/<which>', methods=['POST'])
    @super_required
    def system_key_test(which):
        """Test buttons: the key typed in the box, else the one in use. A typed key that works is saved straight away
        (one that fails is not); the result is kept and shown under the box."""
        import budget
        import envs
        name = {'anthropic': 'sys.anthropic_key', 'claude': 'sys.claude_token'}.get(which) or abort(404)
        v = re.sub(r'\s+', '', request.form.get(name, ''))
        if v and not SECRETS[name][1].match(v):
            flash(f'{SECRETS[name][2]}: paste the whole key from {SECRETS[name][3]}.', 'danger')
            return redirect(url_for('settings.system'))
        if not v and not os.environ.get(SECRETS[name][0]):
            flash(f'{SECRETS[name][2]}: paste a key in the box first, then Test.', 'warning')
            return redirect(url_for('settings.system'))
        import pricing   # the Claude model picked on the page (saved or not), else the API model picked there
        picked = next((m for m in (request.form.get('sys.claude_model'), request.form.get('sys.ai_model')) if m in pricing.PRICES), None)
        ok, msg = anthropic_test(v or None) if which == 'anthropic' else claude_test(v or None, picked)
        c = con()
        with c:
            if ok and v:
                budget.set_setting(c, name, envs.encrypt(v))
            if ok or not v:   # a typed key that failed says nothing about the key in use
                budget.set_setting(c, name + '_check', json.dumps({'ok': ok, 'at': db.now(), 'msg': msg[:300]}))
        if ok and v:
            apply_system(c)
            write_audit(current_user()['username'], 'system_save', 'settings', None, 'ok', f'{SECRETS[name][2]} tested and saved',
                        {name: ['', 'replaced']}, request.remote_addr)
        flash(msg + (' Saved: it is in use now.' if ok and v else ' Not saved.' if v else ''), 'success' if ok else 'danger')
        return redirect(url_for('settings.system'))

    # ------------------------------------------------------------ Integrations: PM (task links, test report link)
    def _pm_vals(c):
        import budget
        import pmtasks
        return {'url': budget.get_setting(c, 'pm.mcp_url') or '', 'public': budget.get_setting(c, 'pm.public_url') or '',
                'has_key': bool(budget.get_setting(c, 'pm.mcp_key')),
                'env_key': bool(pmtasks._STARTED_WITH['LTH_PM_MCP_KEY']), 'in_use_url': pmtasks.pm_url(),
                'in_use_public': os.environ.get('LTH_PUBLIC_URL') or '', 'default_url': pmtasks.PM_URL_DEFAULT,
                'key_set_at': budget.get_setting(c, 'pm.mcp_key_at') or '',
                'cli_poll': budget.get_setting(c, 'pm.cli_poll') == '1', 'cli_since': budget.get_setting(c, 'pm.cli_since') or ''}

    def _http_url(v):
        return v.startswith(('http://', 'https://')) and ' ' not in v

    @bp.route('/integrations', methods=['GET'])
    @admin_required
    def integrations():
        import pmtasks
        return render_template('settings_integrations.html', pm=_pm_vals(con()), need=pmtasks.NEEDED_TOOLS)

    @bp.route('/integrations', methods=['POST'])
    @super_required
    def integrations_save():
        import budget
        import envs
        import pmtasks
        c = con()
        f = request.form
        url, public, key = f.get('pm_url', '').strip(), f.get('public_url', '').strip().rstrip('/'), f.get('pm_token', '').strip()
        errors = [m for bad, m in [
            (url and not _http_url(url), 'PM MCP URL: an http:// or https:// address.'),
            (public and not _http_url(public), 'LogiTestHub address: an http:// or https:// address.'),
            (key and (' ' in key or len(key) < 10), 'PM key: paste the whole key PM gave you (it starts with pmk_).'),
        ] if bad]
        if errors:
            for e in errors:
                flash(e, 'danger')
            return redirect(url_for('settings.integrations'))
        before = _pm_vals(c)
        with c:
            budget.set_setting(c, 'pm.mcp_url', url)
            budget.set_setting(c, 'pm.public_url', public)
            budget.set_setting(c, 'pm.cli_poll', '1' if f.get('cli_poll') else '')
            if key:
                budget.set_setting(c, 'pm.mcp_key', envs.encrypt(key))
                budget.set_setting(c, 'pm.mcp_key_at', db.now())
            elif f.get('remove_key'):
                budget.set_setting(c, 'pm.mcp_key', '')
                budget.set_setting(c, 'pm.mcp_key_at', '')
        pmtasks.apply_settings(c)
        changed = {k: [o, n] for k, o, n in [('pm.mcp_url', before['url'], url), ('pm.public_url', before['public'], public),
                                             ('pm.cli_poll', before['cli_poll'], bool(f.get('cli_poll')))] if o != n}
        if key or f.get('remove_key'):
            changed['pm.mcp_key'] = ['••••' if before['has_key'] else '', '••••' if key else '']
        me = current_user()
        write_audit(me['username'], 'integrations_save', 'settings', None, 'ok',
                    'PM integration saved' if changed else 'PM integration saved (no change)',
                    {'changed': changed} if changed else None, request.remote_addr)
        flash('PM integration saved. It is used straight away.' if changed else 'Nothing changed.', 'success')
        return redirect(url_for('settings.integrations'))

    @bp.route('/integrations/test', methods=['POST'])
    @admin_required
    def integrations_test():
        """Connects with what is in the boxes (a typed key, else the saved one) without saving: lists the key's tools."""
        import pmtasks
        f = request.form
        url = f.get('pm_url', '').strip() or pmtasks.pm_url()
        key = f.get('pm_token', '').strip() or os.environ.get('LTH_PM_MCP_KEY')
        if not _http_url(url):
            flash('PM MCP URL: an http:// or https:// address.', 'danger')
            return redirect(url_for('settings.integrations'))
        try:
            tools = pmtasks.pm_tools(key, url)
        except pmtasks.PmError as e:
            flash(f'Could not connect to {url}: {e}', 'danger')
            return redirect(url_for('settings.integrations'))
        missing = [t for t in pmtasks.NEEDED_TOOLS if t not in tools]
        if missing:
            flash(f'Connected, but this key cannot call {", ".join(missing)}. Ask PM for a key with these tools. '
                  f'Allowed: {", ".join(tools) or "none"}.', 'warning')
        else:
            flash(f'Connected to PM. This key can call: {", ".join(tools)}.' + ('' if f.get('pm_token', '').strip() == '' else ' Save to keep this key.'), 'success')
        return redirect(url_for('settings.integrations'))

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
