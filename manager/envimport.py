"""Import environments from a CSV: the client sheet (Google Sheets / Excel -> Download as CSV) read as it is, one
environment per row. app.py checks every row with the same rules as the Add form, shows what it would add
("Check"), and adds the rows only on "Import". The file is read in memory and never written to disk.

Columns are found by their heading, so the sheet needs no reshaping:
- name:     "client_name" / "Name" / "Environment"
- URL + login: a "... URL ..." column holding "https://site/admin/ - user | password" (or just the URL)
- database: a "... db ..." column with "Host : x", "port - 3306", "username - u", "password - p", "DB : name"
  lines, or the five values "host, port, db name, db user, db password" in that order
- code:     "Git repo" (and an optional "Branch" / "Paths" column; no branch = the repository's default branch)
- project:  a "Project" column, else a project named like the client name or client code
Plain columns named like the form fields (base_url, username, password, db_host, db_port, db_name, db_user,
db_password, repo_url, repo_branch, repo_paths) are used as they are.
"""
import csv
import io
import re

NAME_BAD = re.compile(r'[^A-Za-z0-9 ._-]+')
PLAIN = ('name', 'base url', 'username', 'password', 'db host', 'db port', 'db name', 'db user', 'db password',
         'repo url', 'repo branch', 'repo paths', 'project')
DB_KEYS = [('db_host', r'host(?:\s*name)?|server'), ('db_port', r'port'), ('db_name', r'db(?:\s*name)?|database(?:\s*name)?'),
           ('db_user', r'(?:db\s*)?user(?:\s*name)?'), ('db_password', r'(?:db\s*)?pass(?:word)?|pwd')]
_SEP = r'\s*[:=\-–]\s*'


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
        elif 'git' in k or k in ('repo', 'repository'):
            out[h] = 'repo_url'
        elif 'branch' in k:
            out[h] = 'repo_branch'
        elif 'path' in k:
            out[h] = 'repo_paths'
        elif re.search(r'\bdb\b|database', k):
            out[h] = 'db'
        elif 'url' in k:
            out[h] = 'url'
    return out


def url_and_login(text):
    """'https://site/admin/ - user | password' -> (url, user, password); the login part is optional."""
    text = (text or '').strip()
    m = re.match(r'(https?://\S+)(.*)$', text, re.S)
    if not m:
        return None, None, None
    url, rest = m.group(1), m.group(2).strip()
    rest = re.sub(r'^[-–:,|]\s*', '', rest)
    if not rest:
        return url, None, None
    keyed = {k: v for k, v in re.findall(r'(?im)\b(user(?:\s*name)?|pass(?:word)?)' + _SEP + r'(\S+)', rest)}
    if keyed:
        user = next((v for k, v in keyed.items() if k.lower().startswith('user')), None)
        pw = next((v for k, v in keyed.items() if k.lower().startswith('pass')), None)
        return url, user, pw
    user, _, pw = rest.partition('|') if '|' in rest else rest.partition(' / ')
    return url, user.strip() or None, pw.strip() or None


def db_details(text):
    """The database cell -> {db_host, db_port, db_name, db_user, db_password} (only what it holds)."""
    text = (text or '').strip()
    out = {}
    for line in re.split(r'[\r\n]+', text):
        for field, words in DB_KEYS:
            m = re.match(r'\s*(?:' + words + r')' + _SEP + r'(.+?)\s*$', line, re.I)
            if m and field not in out:
                out[field] = m.group(1)
                break
    if not out:   # "host, port, db name, db user, db password"
        parts = [p.strip() for p in text.split(',')]
        if len(parts) == 5:
            out = dict(zip(('db_host', 'db_port', 'db_name', 'db_user', 'db_password'), parts))
    host = out.get('db_host') or ''
    hm = re.match(r'^(.+):(\d{2,5})$', host)
    if hm:   # host:port
        out['db_host'] = hm.group(1)
        out.setdefault('db_port', hm.group(2))
    return {k: v for k, v in out.items() if v}


def clean_name(text):
    return re.sub(r'\s+', ' ', NAME_BAD.sub(' ', (text or '').replace('&', ' and '))).strip()[:40].strip()


def parse(raw, projects, default_branch):
    """CSV bytes -> ([{line, name, form, note}], error). form = the values the Add form would post (shared).
    projects: rows with id / name; default_branch(url) -> branch name or None."""
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
        notes = []
        url, user, pw = url_and_login(got.get('url'))
        form['base_url'] = got.get('base_url') or url or ''
        form['username'] = got.get('username') or user or ''
        form['password'] = got.get('password') or pw or ''
        dbv = db_details(got.get('db'))
        for f in ('db_host', 'db_port', 'db_name', 'db_user', 'db_password'):
            form[f] = got.get(f) or dbv.get(f, '')
        if form['db_host'] and not form['db_port']:
            form['db_port'] = '3306'
        repo = got.get('repo_url', '')
        repo = repo.split()[0].rstrip('/') if repo else ''
        form['repo_url'] = repo
        branch = got.get('repo_branch', '')
        if repo and not branch:
            if repo not in branches:
                branches[repo] = default_branch(repo)
            branch = branches[repo] or ''
            notes.append(f'branch {branch} (the repository\'s default)' if branch else
                         'branch not found on GitHub (no access?): main is tried')
        form['repo_branch'] = branch
        pid = None
        for cand in (got.get('project'), got.get('name'), got.get('code')):
            pid = by_name.get(re.sub(r'\s+', ' ', (cand or '').strip().lower()))
            if pid:
                break
        form['project_id'] = str(pid) if pid else ''
        if got.get('project') and not pid:
            notes.append(f'project "{got["project"]}" not found: Any project')
        rows.append({'line': n, 'name': form['name'] or '(no name)', 'form': form, 'notes': notes})
    if not rows:
        return [], 'The CSV has headings but no rows.'
    return rows, None
