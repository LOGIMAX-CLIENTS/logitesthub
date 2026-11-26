"""Import environments from a CSV: the client sheet (Google Sheets / Excel -> Download as CSV) read as it is, one
environment per row. app.py checks every row with the same rules as the Add form, shows what it would add
("Check"), and adds the rows only on "Import". The file is read in memory and never written to disk.

Columns are found by their heading, so the sheet needs no reshaping:
- name:     "client_name" / "Name" / "Environment"
- URL + login: a "... URL ..." column: "https://site/admin/ - user | password", "https://site/admin/ - user password",
  "... user / password", "... username: u password: p", or just the URL
- database: a "... db ..." column: "Host : x", "port - 3306", "username - u", "password - p", "DB : name" - one per
  line, or on one line split by "|" / ","; "IP :" for the host; or the five values "host, port, db name, db user,
  db password" in that order, split by "," or "|" or lines (cPanel sheets)
- code:     "Git repo" (and an optional "Branch" / "Paths" column; no branch = the repository's default branch)
- project:  a "Project" column, else a project named like the client name or client code
- token:    a "git token" column (read-only GitHub token for that row), else the one typed on the import form, else one
            saved on an environment of the same GitHub org
- SQL:      a "SQL" column ON / OFF: "Set up SQL" with the row's database user (else the import form's tick decides);
            "db write user" / "db write password" columns give a separate write user
Plain columns named like the form fields (base_url, username, password, db_host, db_port, db_name, db_user,
db_password, repo_url, repo_branch, repo_paths) are used as they are.

A password is never shown back: a cell that cannot be read cleanly makes its row an error that names the column,
never its text (it may hold a password).
"""
import csv
import io
import re

NAME_BAD = re.compile(r'[^A-Za-z0-9 ._-]+')
HOST_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$')
IDENT_RE = re.compile(r'^[\w.$-]{1,64}$')
USER_RE = re.compile(r'^\S{1,120}$')
PLAIN = ('name', 'base url', 'username', 'password', 'db host', 'db port', 'db name', 'db user', 'db password',
         'db write user', 'db write password', 'repo url', 'repo branch', 'repo paths', 'repo token', 'project')
_DB_FIELDS = ('db_host', 'db_port', 'db_name', 'db_user', 'db_password')
DB_KEYS = [('db_host', r'host(?:\s*name)?|server|ip(?:\s*address)?'), ('db_port', r'port'), ('db_name', r'db(?:\s*name)?|database(?:\s*name)?'),
           ('db_user', r'(?:db\s*)?user(?:\s*name)?'), ('db_password', r'(?:db\s*)?pass(?:word)?|pwd')]
_KEY_WORDS = r'(?:host|server|ip|port|db|database|user|username|pass|password|pwd)\b(?:\s*name)?'
_SEP = r'\s*[:=\-–]\s*'
# a "|" / "," / ";" splits the db cell only when a "key :" follows, so a password holding one stays whole
_DB_SPLIT = re.compile(r'[\r\n]+|\s*[|,;]\s*(?=' + _KEY_WORDS + _SEP + ')', re.I)
DB_HELP = ('write it as Host : … / DB : … / username - … / password - … (one per line, or split by "|"), '
           'or "host, port, db name, db user, db password"')


def _key(h):
    return re.sub(r'[^a-z0-9]+', ' ', (h or '').lower()).strip()


def _columns(headers):
    """heading -> what it holds ('name', 'url', 'db', 'repo', 'branch', 'paths', 'project', 'code' or a plain field)."""
    out = {}
    for h in headers:
        k = _key(h)
        if k in PLAIN:
            out[h] = k.replace(' ', '_')
        elif k in ('client name', 'environment', 'environment name'):
            out[h] = 'name'
        elif k == 'client code':
            out[h] = 'code'
        elif 'token' in k:    # before "git": "git token" is the token, not the repository
            out[h] = 'repo_token'
        elif k in ('sql', 'set up sql', 'setup sql', 'sql setup'):
            out[h] = 'sql'
        elif 'branch' in k:   # before "git": "Git branch" is the branch, not the repository
            out[h] = 'repo_branch'
        elif 'git' in k or k in ('repo', 'repository'):
            out[h] = 'repo_url'
        elif 'path' in k:
            out[h] = 'repo_paths'
        elif re.search(r'\bdb\b|database', k):
            out[h] = 'db'
        elif 'url' in k:
            out[h] = 'url'
    return out


def _unquote(v):
    return (v or '').strip().strip('\'"`').strip()


def url_and_login(text):
    """The URL cell -> (url, user, password, problem). problem: why the login part could not be read (never its text)."""
    text = (text or '').strip()
    m = re.match(r'(https?://[^\s|]+)(.*)$', text, re.S)
    if not m:
        return None, None, None, None
    url, rest = m.group(1), m.group(2).strip()
    rest = re.sub(r'^[-–:,|&·/]+\s*', '', rest).strip()
    if not rest:
        return url, None, None, None
    keyed = re.findall(r'(?i)\b(user(?:\s*name)?|login|pass(?:word)?|pwd)' + _SEP + r'(\S+)', rest)
    if keyed:
        user = next((_unquote(v) for k, v in keyed if not re.match(r'(?i)pass|pwd', k)), None)
        pw = next((_unquote(v) for k, v in keyed if re.match(r'(?i)pass|pwd', k)), None)
    elif '|' in rest or ' / ' in rest:
        user, _, pw = rest.partition('|') if '|' in rest else rest.partition(' / ')
        user, pw = _unquote(user), _unquote(pw)
    else:
        parts = rest.split()
        if len(parts) == 1:
            user, pw = _unquote(parts[0]), None
        elif len(parts) == 2:   # "user password", also written "user/ password"
            user, pw = _unquote(parts[0]).rstrip('/'), _unquote(parts[1])
        else:
            return url, None, None, 'Login: could not tell the user from the password - write it as "url - user | password".'
    if user and not USER_RE.match(user):
        return url, None, None, 'Login: could not tell the user from the password - write it as "url - user | password".'
    return url, user or None, pw or None, None


def db_details(text):
    """The database cell -> ({db_host, db_port, db_name, db_user, db_password}, problem). problem names what is
    missing, never the cell's text."""
    text = (text or '').strip()
    if not text:
        return {}, None
    out = {}
    for seg in _DB_SPLIT.split(text):
        seg = seg.strip().strip('|,;').strip()
        if not seg:
            continue
        for field, words in DB_KEYS:
            m = re.match(r'(?:' + words + r')' + _SEP + r'(.+?)\s*$', seg, re.I)
            if m:
                out.setdefault(field, _unquote(m.group(1)))
                break
        else:   # no "key :": the host written alone (first, before the keyed parts)
            if 'db_host' not in out and not out and re.match(r'^(?:[a-z]+://)?[A-Za-z0-9.-]+(?::\d+)?/?$', seg):
                out['db_host'] = seg
    if not out:   # "host, port, db name, db user, db password": split by "," or "|" or lines (cPanel sheets)
        parts = [_unquote(p) for p in re.split(r'[,|\r\n]+', text) if p.strip()]
        if len(parts) == 5 and parts[1].isdigit():
            out = dict(zip(_DB_FIELDS, parts))
    host = re.sub(r'^[a-z]+://', '', out.get('db_host') or '', flags=re.I).strip().rstrip('/')
    hm = re.match(r'^(.+):(\d{2,5})$', host)
    if hm:
        host = hm.group(1)
        out.setdefault('db_port', hm.group(2))
    if host:
        out['db_host'] = host
    out = {k: v for k, v in out.items() if v}
    missing = [label for f, label in (('db_host', 'host'), ('db_name', 'database name'), ('db_user', 'username'))
               if not out.get(f)]
    bad = (out.get('db_host') and not HOST_RE.match(out['db_host'])) or \
          any(out.get(f) and not IDENT_RE.match(out[f]) for f in ('db_name', 'db_user')) or \
          (out.get('db_port') and not out['db_port'].isdigit())
    if missing or bad:
        what = f'no {", ".join(missing)} found' if missing else 'a value could not be read'
        return {}, f'Database column: {what} - {DB_HELP}.'
    return out, None


def clean_name(text):
    return re.sub(r'\s+', ' ', NAME_BAD.sub(' ', (text or '').replace('&', ' and '))).strip()[:40].strip()


def parse(raw, projects, default_branch, token_for=None, token=None, sql_same_user=False):
    """CSV bytes -> ([{line, name, form, notes, error}], error). form = the values the Add form would post (shared).
    projects: rows with id / name; default_branch(url, token) -> branch name or None;
    token_for(url) -> (token, where it came from) or (None, None): a saved read-only token for that GitHub org;
    token: a read-only GitHub token typed on the import form, used for every row instead;
    sql_same_user: turn on "Set up SQL" with each row's database user (when it has a password), unless the row has
    its own write user columns."""
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    cols = _columns(reader.fieldnames or [])
    if 'name' not in cols.values():
        return [], 'No name column: the CSV needs a "client_name" (or "Name") heading in its first row.'
    if not {'url', 'base_url'} & set(cols.values()):
        return [], 'No URL column: the CSV needs a "Staging URL" (or "base_url") heading.'
    by_name = {re.sub(r'\s+', ' ', p['name'].strip().lower()): p['id'] for p in projects}
    rows, branches = [], {}
    for n, row in enumerate(reader, start=2):
        got = {}
        for h, what in cols.items():
            v = (row.get(h) or '').strip()
            if v:
                got.setdefault(what, v)
        if not any(got.values()):
            continue
        form = {'scope': 'shared', 'name': clean_name(got.get('name')), 'repo_paths': got.get('repo_paths', '')}
        notes, problems = [], []
        url, user, pw, lp = url_and_login(got.get('url'))
        if lp and not got.get('username'):
            problems.append(lp)
        form['base_url'] = got.get('base_url') or url or ''
        form['username'] = got.get('username') or user or ''
        form['password'] = got.get('password') or pw or ''
        dbv, dp = db_details(got.get('db'))
        if dp and not got.get('db_host'):
            problems.append(dp)
        for f in _DB_FIELDS:
            form[f] = got.get(f) or dbv.get(f, '')
        if form['db_host'] and not form['db_port']:
            form['db_port'] = '3306'
        # "Set up SQL": a SQL column (ON / OFF) decides per row, else the import form's tick
        sql_cell = (got.get('sql') or '').strip().lower()
        want_sql = sql_cell in ('on', 'yes', 'y', 'true', '1') if sql_cell else (sql_same_user or bool(got.get('db_write_user')))
        wuser, wpw = got.get('db_write_user', ''), got.get('db_write_password', '')
        if not wuser and want_sql and form['db_host']:
            wuser, wpw = form['db_user'], form['db_password']
        on = want_sql and bool(form['db_host'] and wuser and wpw)
        form['db_write_user'], form['db_write_password'] = (wuser, wpw) if on else ('', '')
        form['db_allow_write'] = '1' if on else ''
        if want_sql and not on:
            notes.append('Set up SQL left off: ' + ('no database in this row' if not form['db_host']
                                                    else 'no database password for the write user'))
        repo = got.get('repo_url', '')
        repo = repo.split()[0].rstrip('/') if repo else ''
        form['repo_url'] = repo
        tok, src = got.get('repo_token') or '', None
        if repo and not tok and token:
            tok, src = token, 'the import form'
        elif repo and not tok and token_for:
            tok, src = token_for(repo)
        branch = got.get('repo_branch', '')
        if repo and not branch:
            if repo not in branches:   # the token first; when it cannot read the repo, this server's own sign-in
                b = default_branch(repo, tok or None)
                if not b and tok:
                    b2 = default_branch(repo, None)
                    branches[repo] = (b2, None) if b2 else (None, tok)   # the token is kept only if it is not the problem
                else:
                    branches[repo] = (b, tok)
            branch, tok = branches[repo][0] or '', branches[repo][1]
            notes.append(f'branch {branch} (the repository\'s default: add a Branch column if staging runs another)' if branch
                         else 'branch: the repository could not be read (no access?) - main is tried; add a Branch column')
        if tok and src:
            notes.append('GitHub token: the one typed on the import form' if src == 'the import form'
                         else f'GitHub token: the one saved on "{src}"')
        form['repo_token'] = tok or ''
        form['repo_branch'] = branch
        pid = None
        for cand in (got.get('project'), got.get('name'), got.get('code')):
            pid = by_name.get(re.sub(r'\s+', ' ', (cand or '').strip().lower()))
            if pid:
                break
        form['project_id'] = str(pid) if pid else ''
        if got.get('project') and not pid:
            notes.append(f'project "{got["project"]}" not found: Any project')
        rows.append({'line': n, 'name': form['name'] or '(no name)', 'form': form, 'notes': notes,
                     'client': re.sub(r'\s+', ' ', got.get('name', '')).strip()[:191],   # a new project's name
                     'error': ' '.join(problems) or None})
    if not rows:
        return [], 'The CSV has headings but no rows.'
    return rows, None


def summary(f):
    """What a row holds, for the result table: only values that cannot be a password (each matched a strict pattern)."""
    parts = [f['base_url'] or 'no URL']
    if f['username']:
        parts.append(f'login {f["username"]}' + (' + password' if f['password'] else ', no password')
                     if USER_RE.match(f['username']) else 'login set')
    if f['db_host']:
        safe = lambda v, rx: v if v and rx.match(v) else '?'
        parts.append(f'database {safe(f["db_name"], IDENT_RE)} as {safe(f["db_user"], IDENT_RE)} on {safe(f["db_host"], HOST_RE)}'
                     + (' + password' if f['db_password'] else ', no password'))
        if f.get('db_allow_write'):
            w = f['db_write_user']
            parts.append(f'Set up SQL on as {w if IDENT_RE.match(w or "") else "?"}')
    if f['repo_url']:
        parts.append(f'code {f["repo_url"].rsplit("/", 1)[-1]}' + (f' ({f["repo_branch"]})' if f['repo_branch'] else ''))
    return ' · '.join(parts)
