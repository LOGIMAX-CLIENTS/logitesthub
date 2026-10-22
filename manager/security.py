"""Security scan: quick, safe checks against an environment's URL (and its synced code), so the team fixes the easy
things before a VAPT / pentest and catches a regression when a fixed one comes back.

What it checks (all read-only, a handful of normal GET requests - never an attack):
  - Security headers  (HSTS, X-Frame-Options / CSP frame-ancestors, X-Content-Type-Options, CSP, Referrer-Policy,
                       server / framework version leak)
  - Cookie flags      (HttpOnly, Secure, SameSite on the Set-Cookie of the home page)
  - HTTPS / TLS       (protocol version, certificate expiry, hostname match, http -> https redirect)
  - Exposed files     (/.env, /.git/config, /phpinfo.php ... returned with real content)
  - Known vulnerable libraries (the environment's synced code: composer.lock / package-lock.json / requirements.txt
                       looked up in OSV.dev)

Admin / superadmin only. An environment whose name or URL looks like production ("prod" / "live") is refused - run it
on UAT / staging. Each scan backs its findings with evidence and a one-line fix; a background thread does the work and
the page refreshes until it is done.

Registered from app.py with register(app, con, admin_required, current_user).
"""
import json
import os
import re
import socket
import ssl
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from flask import Blueprint, flash, redirect, render_template, request, url_for

import db

UA = 'LogiTestHub-SecurityScan/1.0'
TIMEOUT = 8
PROD_RE = re.compile(r'(^|[^a-z])(prod|production|live)([^a-z]|$)', re.I)
SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3, 'pass': 4}

SCHEMA = """
CREATE TABLE IF NOT EXISTS security_scans (
  id INT AUTO_INCREMENT PRIMARY KEY, env_id INT NULL, env_name VARCHAR(80), target VARCHAR(500) NOT NULL,
  status VARCHAR(12) NOT NULL DEFAULT 'running', started_by VARCHAR(64), started_at VARCHAR(32) NOT NULL,
  finished_at VARCHAR(32) NULL, high INT NOT NULL DEFAULT 0, medium INT NOT NULL DEFAULT 0, low INT NOT NULL DEFAULT 0,
  passed INT NOT NULL DEFAULT 0, findings MEDIUMTEXT NULL, error VARCHAR(500) NULL,
  KEY ix_security_scans_at (started_at)
) ENGINE=InnoDB;
"""

_LOCK = threading.Lock()
_RUNNING = set()        # env ids (or targets) with a scan going now


def init(con):
    con.execute(SCHEMA)
    # a scan running when the server stopped will never finish: show it as stopped
    with con:
        con.execute("UPDATE security_scans SET status='error', error='Stopped when the server restarted.' WHERE status='running'")


def is_production(name, url):
    return bool(PROD_RE.search(name or '') or PROD_RE.search(url or ''))


# ---------------------------------------------------------------- HTTP helper (follows or not; never raises on 4xx/5xx)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _fetch(url, method='GET', headers=None, follow=True, max_body=40000):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req, timeout=TIMEOUT)
        body = r.read(max_body) or b''
        return {'status': r.status, 'headers': {k.lower(): v for k, v in r.headers.items()},
                'set_cookie': r.headers.get_all('Set-Cookie') or [], 'body': body, 'url': r.geturl()}
    except urllib.error.HTTPError as e:
        body = b''
        try:
            body = e.read(max_body) or b''
        except Exception:
            pass
        hd = {k.lower(): v for k, v in (e.headers or {}).items()}
        return {'status': e.code, 'headers': hd, 'location': hd.get('location'),
                'set_cookie': (e.headers.get_all('Set-Cookie') if e.headers else []) or [], 'body': body, 'url': url}


def _finding(check, severity, title, detail='', fix=''):
    return {'check': check, 'severity': severity, 'title': title, 'detail': detail, 'fix': fix}


# ---------------------------------------------------------------- the checks (each returns a list of findings)

def check_headers(home):
    out, hd = [], home['headers']
    https = home['url'].lower().startswith('https')
    wants = [
        ('strict-transport-security', 'low' if https else 'info', 'HSTS header missing',
         'Strict-Transport-Security tells browsers to always use HTTPS.', 'Add: Strict-Transport-Security: max-age=31536000; includeSubDomains'),
        ('x-content-type-options', 'low', 'X-Content-Type-Options missing',
         'Without "nosniff" the browser may guess a response type (MIME sniffing).', 'Add: X-Content-Type-Options: nosniff'),
        ('content-security-policy', 'low', 'Content-Security-Policy missing',
         'A CSP limits what scripts/resources can load (helps against XSS).', 'Add a Content-Security-Policy header suited to the app.'),
        ('referrer-policy', 'info', 'Referrer-Policy missing',
         'Controls how much of the URL is sent to other sites.', 'Add: Referrer-Policy: strict-origin-when-cross-origin'),
    ]
    for key, sev, title, detail, fix in wants:
        if key not in hd:
            out.append(_finding('headers', sev, title, detail, fix))
    # clickjacking: either X-Frame-Options or a CSP frame-ancestors
    if 'x-frame-options' not in hd and 'frame-ancestors' not in hd.get('content-security-policy', '').lower():
        out.append(_finding('headers', 'medium', 'Clickjacking protection missing',
                            'No X-Frame-Options and no CSP frame-ancestors: the page can be framed by another site.',
                            'Add: X-Frame-Options: SAMEORIGIN (or a CSP frame-ancestors).'))
    # version disclosure
    for key, label in (('server', 'Server'), ('x-powered-by', 'X-Powered-By'), ('x-aspnet-version', 'X-AspNet-Version')):
        val = hd.get(key, '')
        if val and re.search(r'\d', val):
            out.append(_finding('headers', 'low', f'{label} reveals software/version',
                               f'{label}: {val}', f'Hide or trim the {label} header so the exact version is not exposed.'))
    if not out:
        out.append(_finding('headers', 'pass', 'Security headers look fine', 'HSTS, anti-clickjacking, nosniff, CSP and version hiding are in place.'))
    return out


def check_cookies(home):
    out = []
    https = home['url'].lower().startswith('https')
    cookies = home.get('set_cookie') or []
    if not cookies:
        return [_finding('cookies', 'info', 'No cookies set on the home page', 'Nothing to check here (cookies may be set only after login).')]
    bad = False
    for c in cookies:
        name = c.split('=', 1)[0].strip()
        low = c.lower()
        if 'httponly' not in low:
            out.append(_finding('cookies', 'medium', f'Cookie "{name}" has no HttpOnly flag',
                               'Script on the page can read it (session theft risk if it is a session cookie).', 'Set the HttpOnly flag on this cookie.')); bad = True
        if https and 'secure' not in low:
            out.append(_finding('cookies', 'medium', f'Cookie "{name}" has no Secure flag',
                               'It can be sent over plain HTTP.', 'Set the Secure flag on this cookie.')); bad = True
        if 'samesite' not in low:
            out.append(_finding('cookies', 'low', f'Cookie "{name}" has no SameSite attribute',
                               'A SameSite attribute helps against CSRF.', 'Add SameSite=Lax (or Strict) to this cookie.')); bad = True
    if not bad:
        out.append(_finding('cookies', 'pass', 'Cookie flags look fine', 'HttpOnly / Secure / SameSite are set on the home-page cookies.'))
    return out


def check_tls(target):
    out = []
    u = urlparse(target)
    host = u.hostname
    if u.scheme != 'https':
        return [_finding('tls', 'medium', 'Site is not served over HTTPS', f'The environment URL is {u.scheme}://...', 'Serve the site over HTTPS and redirect HTTP to it.')]
    port = u.port or 443
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                ver = ss.version()
                cert = ss.getpeercert()
        if ver in ('TLSv1', 'TLSv1.1', 'SSLv3'):
            out.append(_finding('tls', 'high', f'Weak TLS protocol allowed ({ver})', 'Old TLS versions are broken.', 'Turn off TLS 1.0/1.1; allow only TLS 1.2 and 1.3.'))
        na = cert.get('notAfter') if cert else None
        if na:
            exp = datetime.strptime(na, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
            days = (exp - datetime.now(timezone.utc)).days
            if days < 0:
                out.append(_finding('tls', 'high', 'TLS certificate has expired', f'Expired {-days} day(s) ago.', 'Renew the certificate now.'))
            elif days < 14:
                out.append(_finding('tls', 'medium', f'TLS certificate expires in {days} day(s)', f'Expires {na}.', 'Renew the certificate soon.'))
            elif days < 30:
                out.append(_finding('tls', 'low', f'TLS certificate expires in {days} day(s)', f'Expires {na}.', 'Plan the certificate renewal.'))
    except ssl.SSLCertVerificationError as e:
        out.append(_finding('tls', 'high', 'TLS certificate is not valid', str(e)[:200], 'Fix the certificate (wrong host, self-signed, or an incomplete chain).'))
    except Exception as e:
        out.append(_finding('tls', 'info', 'Could not complete the TLS check', str(e)[:200], ''))
    # http -> https redirect
    try:
        httpurl = urlunparse(('http', u.netloc.split(':')[0], u.path or '/', '', '', ''))
        r = _fetch(httpurl, follow=False)
        loc = (r.get('location') or r['headers'].get('location') or '')
        if not (r['status'] in (301, 302, 307, 308) and loc.lower().startswith('https')):
            out.append(_finding('tls', 'medium', 'HTTP is not redirected to HTTPS', f'http:// returned {r["status"]} (location: {loc or "none"}).',
                               'Redirect all HTTP requests to HTTPS.'))
    except Exception:
        pass
    if not out:
        out.append(_finding('tls', 'pass', 'HTTPS / TLS look fine', 'Modern TLS, a valid certificate with time to spare, and HTTP redirects to HTTPS.'))
    return out


EXPOSED = [   # path, a signature that must be IN the body for it to count (avoids SPA catch-all 200s)
    ('/.env', re.compile(r'(?mi)^[A-Z0-9_]+=')), ('/.git/config', re.compile(r'\[core\]|\[remote')),
    ('/.git/HEAD', re.compile(r'ref:\s*refs/')), ('/phpinfo.php', re.compile(r'phpinfo\(\)|PHP Version', re.I)),
    ('/composer.json', re.compile(r'"require"\s*:')), ('/package.json', re.compile(r'"dependencies"\s*:|"name"\s*:')),
    ('/.htaccess', re.compile(r'Rewrite|Deny|Options', re.I)), ('/server-status', re.compile(r'Apache Server Status', re.I)),
    ('/backup.sql', re.compile(r'(?i)INSERT INTO|CREATE TABLE')), ('/web.config', re.compile(r'<configuration')),
]


def check_exposed(target):
    out = []
    base = target.rstrip('/')
    for path, sig in EXPOSED:
        try:
            r = _fetch(base + path, follow=True)
        except Exception:
            continue
        if r['status'] == 200 and r.get('body') and sig.search(r['body'][:40000].decode('utf-8', 'replace')):
            sev = 'high' if path in ('/.env', '/.git/config', '/.git/HEAD', '/backup.sql', '/web.config') else 'medium'
            out.append(_finding('exposed', sev, f'Exposed file: {path}', f'{base + path} returns real content (status 200).',
                               f'Block {path} at the web server / move it out of the web root.'))
    if not out:
        out.append(_finding('exposed', 'pass', 'No sensitive files exposed', 'Checked common leaks (.env, .git, backups, phpinfo, server-status, ...).'))
    return out


def _parse_deps(code_dir):
    """(ecosystem, name, version) for lockfiles found in the synced code; best-effort, bounded."""
    deps = []
    def add(eco, name, ver):
        if name and ver and len(deps) < 400:
            deps.append((eco, str(name), re.sub(r'^[^0-9]*', '', str(ver))))
    cl = os.path.join(code_dir, 'composer.lock')
    if os.path.isfile(cl):
        try:
            d = json.load(open(cl, encoding='utf-8'))
            for pkg in (d.get('packages', []) + d.get('packages-dev', [])):
                add('Packagist', pkg.get('name'), pkg.get('version'))
        except Exception:
            pass
    pl = os.path.join(code_dir, 'package-lock.json')
    if os.path.isfile(pl):
        try:
            d = json.load(open(pl, encoding='utf-8'))
            for path, pkg in (d.get('packages') or {}).items():
                if path.startswith('node_modules/'):
                    add('npm', path.split('node_modules/')[-1], pkg.get('version'))
            for name, info in (d.get('dependencies') or {}).items():
                add('npm', name, info.get('version') if isinstance(info, dict) else info)
        except Exception:
            pass
    rq = os.path.join(code_dir, 'requirements.txt')
    if os.path.isfile(rq):
        try:
            for line in open(rq, encoding='utf-8'):
                m = re.match(r'^\s*([A-Za-z0-9._-]+)\s*==\s*([0-9][\w.]*)', line)
                if m:
                    add('PyPI', m.group(1), m.group(2))
        except Exception:
            pass
    # de-dup
    seen, uniq = set(), []
    for d in deps:
        if d not in seen:
            seen.add(d); uniq.append(d)
    return uniq


def check_cve(code_dir):
    if not code_dir:
        return [_finding('cve', 'info', 'No code repository for this environment',
                        'Link a code repository on the environment (Settings → Environments) to check libraries for known vulnerabilities.')]
    deps = _parse_deps(code_dir)
    if not deps:
        return [_finding('cve', 'info', 'No lock file found in the code',
                        'Looked for composer.lock / package-lock.json / requirements.txt in the synced code.')]
    queries = [{'package': {'name': n, 'ecosystem': e}, 'version': v} for e, n, v in deps]
    try:
        body = json.dumps({'queries': queries}).encode()
        req = urllib.request.Request('https://api.osv.dev/v1/querybatch', data=body,
                                     headers={'User-Agent': UA, 'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=TIMEOUT + 6) as r:
            res = json.loads(r.read())
    except Exception as e:
        return [_finding('cve', 'info', 'Could not reach the vulnerability database (OSV.dev)', str(e)[:200],
                        'Checked later; needs outbound HTTPS to api.osv.dev.')]
    out = []
    for (eco, name, ver), row in zip(deps, res.get('results', [])):
        vulns = row.get('vulns') or []
        if vulns:
            ids = ', '.join(sorted(v.get('id', '') for v in vulns)[:6])
            out.append(_finding('cve', 'medium', f'{name} {ver} has known vulnerabilities',
                               f'{eco} package · {len(vulns)} advisory(ies): {ids}', f'Update {name} to a fixed version.'))
    out.sort(key=lambda f: f['title'].lower())
    if not out:
        out.append(_finding('cve', 'pass', f'No known vulnerabilities in {len(deps)} libraries', 'Checked against OSV.dev.'))
    return out


# ---------------------------------------------------------------- the scan (background thread)

def _run(scan_id, env_id, target, code_dir):
    findings = []
    try:
        home = _fetch(target, follow=True)
        findings += check_headers(home)
        findings += check_cookies(home)
        findings += check_tls(home['url'])
        findings += check_exposed(target)
        findings += check_cve(code_dir)
        findings.sort(key=lambda f: SEV_ORDER.get(f['severity'], 9))
        counts = {s: sum(1 for f in findings if f['severity'] == s) for s in ('high', 'medium', 'low', 'pass')}
        c = db.connect()
        try:
            with c:
                c.execute('UPDATE security_scans SET status=?, finished_at=?, high=?, medium=?, low=?, passed=?, findings=? WHERE id=?',
                          ('done', db.now(), counts['high'], counts['medium'], counts['low'], counts['pass'],
                           json.dumps(findings, ensure_ascii=False), scan_id))
        finally:
            c.close()
    except Exception as e:
        c = db.connect()
        try:
            with c:
                c.execute("UPDATE security_scans SET status='error', finished_at=?, error=? WHERE id=?",
                          (db.now(), str(e)[:500], scan_id))
        finally:
            c.close()
    finally:
        with _LOCK:
            _RUNNING.discard(env_id if env_id is not None else target)


def start_scan(con, env, user, code_dir):
    target = env['base_url']
    key = env['id'] if env.get('id') is not None else target
    with _LOCK:
        if key in _RUNNING:
            return None, 'A scan is already running for this environment.'
        _RUNNING.add(key)
    with con:
        sid = con.execute('INSERT INTO security_scans(env_id, env_name, target, status, started_by, started_at) '
                          'VALUES (?,?,?,?,?,?)', (env.get('id'), env['name'], target, 'running', user, db.now())).lastrowid
    threading.Thread(target=_run, args=(sid, env.get('id'), target, code_dir), name=f'lth-secscan-{sid}', daemon=True).start()
    return sid, None


# ---------------------------------------------------------------- blueprint

def register(app, con, admin_required, current_user):
    with db.connect() as _c:
        init(_c)
    bp = Blueprint('security', __name__)

    def _scan_rows():
        rows = con().execute('SELECT * FROM security_scans ORDER BY id DESC LIMIT 50').fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d['findings'] = json.loads(r['findings']) if r['findings'] else []
            except ValueError:
                d['findings'] = []
            out.append(d)
        return out

    @bp.route('/security')
    @admin_required
    def page():
        import envs
        all_envs = [e for e in envs.all_envs(con()) if e['owner'] is None]   # shared environments only
        marked = [dict(e, is_prod=is_production(e['name'], e['base_url'])) for e in all_envs]
        return render_template('security.html', envs=marked, scans=_scan_rows())

    @bp.route('/security/scan', methods=['POST'])
    @admin_required
    def scan():
        import coderepo
        import envs
        eid = request.form.get('env_id')
        e = envs.get(con(), int(eid)) if str(eid or '').isdigit() else None
        if not e or e['owner'] is not None:
            flash('Pick a shared environment to scan.', 'danger')
            return redirect(url_for('security.page'))
        if is_production(e['name'], e['base_url']):
            flash(f'"{e["name"]}" looks like production - security scans run on UAT / staging only.', 'danger')
            return redirect(url_for('security.page'))
        sid, err = start_scan(con(), e, current_user()['username'], coderepo.code_dir(e))
        flash(err or f'Scanning {e["name"]}: this page refreshes until it is done.', 'danger' if err else 'info')
        return redirect(url_for('security.page') + (f'#scan{sid}' if sid else ''))

    app.register_blueprint(bp)
