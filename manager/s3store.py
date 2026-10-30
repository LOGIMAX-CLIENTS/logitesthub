"""Run files on Amazon S3: the screenshots and video of a finished run go to the bucket and leave this server disk;
only events.ndjson (the steps, a few KB) stays, so the run page, Fail reason and history read as before. The browser
loads a file straight from S3 through a signed link (the bucket stays private). Older runs move over in the background.

Switched on by manager/data/aws.env (or the same environment variables), written by hand on the server:
  AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=ap-south-1  LTH_S3_BUCKET=...
  LTH_S3_PREFIX=prod     (optional; default: this install's folder name, so staging and prod never mix)
Without it everything stays on the disk, as before. Signing is AWS Signature V4 with the standard library: no boto3.
A file whose upload fails stays on the disk and is tried again by the next sweep.
"""
import hashlib
import hmac
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import db

ENV_FILE = os.path.join(db.DATA, 'aws.env')
KEEP = ('events.ndjson', 'report.html')   # the steps (run page, Fail reason, auto-heal, clean-up) + a few KB: stay
SETTLE_S = 120              # a run finished this long ago is offloaded by the sweep (the run itself offloads at once)
EVERY_S = 300
BATCH = 60                  # runs per sweep, so a first sweep over months of old runs never blocks for long
LINK_S = 3600               # a signed link works this long (a page left open: reload it)
_LOCK = threading.Lock()
_BUSY = set()
_cfg = {'mtime': None, 'vals': {}}


# ---------------------------------------------------------------- settings
def _file_vals():
    try:
        m = os.path.getmtime(ENV_FILE)
    except OSError:
        return {}
    if m != _cfg['mtime']:
        vals = {}
        with open(ENV_FILE, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    vals[k.strip()] = v.strip().strip('"\'')
        _cfg.update(mtime=m, vals=vals)
    return _cfg['vals']


def config():
    """{'key', 'secret', 'region', 'bucket', 'prefix'} or None when S3 is not set up."""
    f = _file_vals()
    get = lambda k: os.environ.get(k) or f.get(k) or ''   # noqa: E731
    c = {'key': get('AWS_ACCESS_KEY_ID'), 'secret': get('AWS_SECRET_ACCESS_KEY'), 'region': get('AWS_DEFAULT_REGION'),
         'bucket': get('LTH_S3_BUCKET'),
         'prefix': (get('LTH_S3_PREFIX') or os.path.basename(db.RUNNER_DIR.rstrip('\\/')) or 'lth').strip('/')}
    return c if all(c[k] for k in ('key', 'secret', 'region', 'bucket')) else None


def enabled():
    return config() is not None


def switched_on(con=None):
    """Set up and switched on in Settings > System. Off: new runs stay on the disk; files already on S3 still load
    and are still deleted after their days."""
    if not enabled():
        return False
    import settings
    own = con is None
    con = con or db.connect()
    try:
        return settings._get(con, 'sys.s3_on') == '1'
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------- AWS Signature V4
def _hmac(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()


def _signing_key(secret, day, region):
    k = _hmac(('AWS4' + secret).encode('utf-8'), day)
    return _hmac(_hmac(_hmac(k, region), 's3'), 'aws4_request')


def _q(s, safe='-_.~'):
    return urllib.parse.quote(s, safe=safe)


def _host(c):
    return f"{c['bucket']}.s3.{c['region']}.amazonaws.com"


def _object_path(key):
    return '/' + _q(key, safe='-_.~/')


def presign(c, key, expires=LINK_S, now=None, host=None):
    """A GET link to a private object, valid `expires` seconds."""
    now = now or datetime.now(timezone.utc)
    amz, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    scope = f"{day}/{c['region']}/s3/aws4_request"
    host = host or _host(c)
    path = _object_path(key)
    params = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256', 'X-Amz-Credential': f"{c['key']}/{scope}", 'X-Amz-Date': amz,
              'X-Amz-Expires': str(expires), 'X-Amz-SignedHeaders': 'host'}
    query = '&'.join(f'{_q(k)}={_q(v)}' for k, v in sorted(params.items()))
    canonical = f'GET\n{path}\n{query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD'
    to_sign = f'AWS4-HMAC-SHA256\n{amz}\n{scope}\n' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    sig = hmac.new(_signing_key(c['secret'], day, c['region']), to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'https://{host}{path}?{query}&X-Amz-Signature={sig}'


def _request(c, method, key, body=b'', content_type=None, timeout=120):
    """One signed request; returns (status, body bytes). Raises urllib errors other than an HTTP status."""
    now = datetime.now(timezone.utc)
    amz, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
    scope = f"{day}/{c['region']}/s3/aws4_request"
    host, path = _host(c), _object_path(key)
    payload = hashlib.sha256(body).hexdigest()
    canonical = (f'{method}\n{path}\n\nhost:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{amz}\n\n'
                 f'host;x-amz-content-sha256;x-amz-date\n{payload}')
    to_sign = f'AWS4-HMAC-SHA256\n{amz}\n{scope}\n' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    sig = hmac.new(_signing_key(c['secret'], day, c['region']), to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {'x-amz-date': amz, 'x-amz-content-sha256': payload,
               'Authorization': f"AWS4-HMAC-SHA256 Credential={c['key']}/{scope}, "
                                f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"}
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(f'https://{host}{path}', data=body if method == 'PUT' else None, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _aws_error(status, body):
    import re
    code = re.search(rb'<Code>(.*?)</Code>', body or b'')
    msg = re.search(rb'<Message>(.*?)</Message>', body or b'')
    return f"S3 answered {status}" + (f": {code.group(1).decode()}" if code else '') + (f" - {msg.group(1).decode()}" if msg else '')


def put_file(c, key, path):
    with open(path, 'rb') as f:
        body = f.read()
    status, out = _request(c, 'PUT', key, body, mimetypes.guess_type(path)[0] or 'application/octet-stream')
    if status != 200:
        raise OSError(_aws_error(status, out))


def delete(c, key):
    status, out = _request(c, 'DELETE', key)
    if status not in (200, 204, 404):
        raise OSError(_aws_error(status, out))


def test():
    """Settings > System 'Test S3': writes, reads back and deletes a small file. Returns (ok, message)."""
    c = config()
    if not c:
        return False, f'Not set up: {ENV_FILE} needs AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION and LTH_S3_BUCKET.'
    key = f"{c['prefix']}/_lth_test.txt"
    body = f'LogiTestHub S3 test {datetime.now():%Y-%m-%d %H:%M:%S}'.encode()
    try:
        status, out = _request(c, 'PUT', key, body, 'text/plain', timeout=20)
        if status != 200:
            return False, 'Upload failed. ' + _aws_error(status, out)
        status, out = _request(c, 'GET', key, timeout=20)
        if status != 200 or out != body:
            return False, 'Read back failed. ' + _aws_error(status, out)
        delete(c, key)
    except (OSError, urllib.error.URLError) as e:
        return False, f'Could not reach S3 ({_host(c)}): {getattr(e, "reason", e)}'
    return True, f"Connected: wrote, read and deleted a test file in s3://{c['bucket']}/{c['prefix']}/."


# ---------------------------------------------------------------- run files
def key_of(c, run_dir, name):
    return f"{c['prefix']}/runs/{os.path.basename(run_dir.rstrip(chr(92) + '/'))}/{name}"


def names(run):
    """Files of this run kept on S3."""
    return [x for x in (run['s3_files'] or '').split(',') if x] if run and 's3_files' in run.keys() else []


def has(run, name):
    """True when the run's file is on this disk or on S3."""
    if not run or not run['run_dir']:
        return False
    return os.path.isfile(os.path.join(run['run_dir'], name)) or name in names(run)


def link(run, name):
    """Signed S3 link for a run file kept there, else None."""
    c = config()
    return presign(c, key_of(c, run['run_dir'], name)) if c and name in names(run) else None


def _ours(run_dir):
    """Only run folders of this install (a test server on a copy never moves another install's runs)."""
    try:
        return os.path.commonpath([os.path.abspath(run_dir), os.path.abspath(db.RUNS_DIR)]) == os.path.abspath(db.RUNS_DIR)
    except ValueError:
        return False


def offload(run_id):
    """Moves the files of one finished run to S3. Returns (files moved, bytes freed)."""
    c = config()
    if not c or not switched_on():
        return 0, 0
    with _LOCK:
        if run_id in _BUSY:
            return 0, 0
        _BUSY.add(run_id)
    try:
        with db.connect() as con:
            run = con.execute('SELECT id, status, run_dir, s3_files FROM runs WHERE id=?', (run_id,)).fetchone()
        if not run or run['status'] in ('running', 'queued') or not run['run_dir'] or not os.path.isdir(run['run_dir']) \
                or not _ours(run['run_dir']):
            return 0, 0
        done, moved, freed = names(run), 0, 0
        for n in sorted(os.listdir(run['run_dir'])):
            p = os.path.join(run['run_dir'], n)
            if n in KEEP or n.endswith('.tmp') or not os.path.isfile(p):
                continue
            try:
                put_file(c, key_of(c, run['run_dir'], n), p)
            except (OSError, urllib.error.URLError) as e:
                print(f' * S3: run {run_id} {n} stays on disk ({getattr(e, "reason", e)})')
                continue
            if n not in done:
                done.append(n)
            with db.connect() as con, con:   # recorded before the file goes, so the page never points at nothing
                con.execute('UPDATE runs SET s3_files=? WHERE id=?', (','.join(done), run_id))
            size = os.path.getsize(p)
            try:
                os.remove(p)
                moved, freed = moved + 1, freed + size
            except OSError:
                pass
        return moved, freed
    finally:
        with _LOCK:
            _BUSY.discard(run_id)


def offload_later(run_id):
    """After a run: move its files without holding up the caller."""
    if switched_on():
        threading.Thread(target=offload, args=(run_id,), daemon=True, name=f'lth-s3-run-{run_id}').start()


def days(con):
    """(passed_days, failed_days) a run's files are kept on S3, from Settings > System."""
    import settings
    return (settings._int(settings._get(con, 'sys.s3_days_passed'), 90, 1, 3650),
            settings._int(settings._get(con, 'sys.s3_days_failed'), 365, 1, 3650))


def expire(con):
    """Deletes the S3 files of runs past their days. Returns files deleted."""
    c = config()
    if not c:
        return 0
    passed, failed = days(con)
    now = datetime.now()
    cut = {True: (now - timedelta(days=passed)).strftime('%Y-%m-%d %H:%M:%S'),
           False: (now - timedelta(days=failed)).strftime('%Y-%m-%d %H:%M:%S')}
    rows = con.execute("SELECT id, status, run_dir, s3_files, started_at FROM runs WHERE s3_files IS NOT NULL "
                       "AND s3_files<>'' AND started_at < ? LIMIT 500", (max(cut.values()),)).fetchall()
    n = 0
    for r in rows:
        if (r['started_at'] or '') >= cut[r['status'] == 'PASSED'] or not _ours(r['run_dir'] or ''):
            continue
        left = names(r)
        for name in list(left):
            try:
                delete(c, key_of(c, r['run_dir'], name))
                left.remove(name)
                n += 1
            except (OSError, urllib.error.URLError) as e:
                print(f' * S3: could not delete run {r["id"]} {name} ({getattr(e, "reason", e)})')
        with con:
            con.execute('UPDATE runs SET s3_files=? WHERE id=?', (','.join(left), r['id']))
    return n


def sweep():
    """Moves finished runs still on the disk (newest first) and deletes expired ones. Returns (moved, freed, expired)."""
    if not enabled():
        return 0, 0, 0
    settle = (datetime.now() - timedelta(seconds=SETTLE_S)).strftime('%Y-%m-%d %H:%M:%S')
    with db.connect() as con:
        rows = con.execute("SELECT id, run_dir FROM runs WHERE run_dir IS NOT NULL AND status NOT IN ('running', 'queued') "
                           'AND started_at < ? ORDER BY id DESC', (settle,)).fetchall()
        gone = expire(con)
        if not switched_on(con):
            return 0, 0, gone
    todo = [r['id'] for r in rows if _ours(r['run_dir']) and os.path.isdir(r['run_dir'])
            and any(n not in KEEP for n in os.listdir(r['run_dir']))]
    moved = freed = 0
    for rid in todo[:BATCH]:
        m, f = offload(rid)
        moved, freed = moved + m, freed + f
    return moved, freed, gone


_STARTED = False


def start():
    """Background thread: a sweep every EVERY_S seconds while S3 is set up (checked each time: aws.env can come later)."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def loop():
        time.sleep(60)
        while True:
            try:
                moved, freed, gone = sweep()
                if moved or gone:
                    print(f' * S3: moved {moved} run files ({freed / 1e6:.0f} MB freed), deleted {gone} past their days')
            except Exception as ex:   # never stop the server over storage
                print(' * S3 sweep error:', ex)
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name='lth-s3').start()
