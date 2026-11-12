"""Run files on Amazon S3: the screenshots and video of a finished run go to the bucket and leave this server disk;
only events.ndjson (the steps, a few KB) stays, so the run page, Fail reason and history read as before. The browser
loads a file straight from S3 through a signed link (the bucket stays private). Older runs move over in the background.

Where the AWS access comes from, first found wins:
  1. Settings > System > S3 access key (a superadmin pastes it; tested before it is saved, the secret encrypted);
  2. manager/data/aws.env (or the same environment variables), written by hand on the server:
       AWS_ACCESS_KEY_ID=...  AWS_SECRET_ACCESS_KEY=...  AWS_DEFAULT_REGION=ap-south-1  LTH_S3_BUCKET=...
  3. the EC2 instance's IAM role (instance profile): AWS hands out short-lived keys itself, so there is no key that
     someone can delete or forget to rotate (2026-11-12: the aws.env key was deleted in IAM and every run page broke).
  Region and bucket come from Settings, else aws.env (the region also from the instance). LTH_S3_PREFIX=prod is
  optional; default: this install's folder name, so staging and prod never mix.
Without it everything stays on the disk, as before. Signing is AWS Signature V4 with the standard library: no boto3.
A file whose upload fails stays on the disk and is tried again by the next sweep.
"""
import hashlib
import hmac
import json
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
AGAIN_S = 30                # older runs still waiting: the next sweep starts soon instead of after EVERY_S
BATCH = 60                  # runs per sweep, so a first sweep over months of old runs never blocks for long
WORKERS = 4                 # runs uploaded at the same time (each one file at a time): light on the server
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


UI_KEYS = {'key': 's3.key_id', 'secret': 's3.secret', 'region': 's3.region', 'bucket': 's3.bucket'}
_ui = {'at': 0.0, 'vals': {}}
UI_TTL = 30          # Settings values are read again after this many seconds (a save resets it at once)
_role = {'creds': None, 'exp': 0.0, 'miss': 0.0}
IMDS = 'http://169.254.169.254'
ROLE_RETRY_S = 600   # not on EC2 / no role attached: look again after 10 minutes, so pages never wait for it


def _ui_vals():
    """S3 access saved in Settings > System (secret decrypted), cached UI_TTL seconds."""
    if time.time() - _ui['at'] > UI_TTL:
        import budget
        import envs
        vals = {}
        try:
            with db.connect() as con:
                for k, name in UI_KEYS.items():
                    v = budget.get_setting(con, name) or ''
                    vals[k] = envs.decrypt(v) if k == 'secret' and v else v
        except Exception:   # noqa: BLE001 - no database yet: fall back to aws.env / the role
            vals = {}
        _ui.update(at=time.time(), vals=vals)
    return _ui['vals']


def forget_ui():
    _ui['at'] = 0.0


def _imds(path, token, method='GET', headers=None, timeout=1.0):
    h = {'X-aws-ec2-metadata-token': token} if token else {}
    h.update(headers or {})
    req = urllib.request.Request(IMDS + path, method=method, headers=h, data=b'' if method == 'PUT' else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')


def _role_creds():
    """Short-lived keys of the EC2 instance's IAM role (IMDSv2), cached until 5 minutes before they expire.
    -> {'key', 'secret', 'token', 'role', 'region'} or None (not on EC2, or no role attached)."""
    now = time.time()
    if _role['creds'] and now < _role['exp'] - 300:
        return _role['creds']
    if now - _role['miss'] < ROLE_RETRY_S:
        return None
    try:
        tok = _imds('/latest/api/token', None, 'PUT', {'X-aws-ec2-metadata-token-ttl-seconds': '21600'})
        role = _imds('/latest/meta-data/iam/security-credentials/', tok).strip().splitlines()[0]
        j = json.loads(_imds('/latest/meta-data/iam/security-credentials/' + role, tok))
        try:
            region = _imds('/latest/meta-data/placement/region', tok).strip()
        except (OSError, urllib.error.URLError):
            region = ''
        exp = datetime.strptime(j['Expiration'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
    except (OSError, urllib.error.URLError, ValueError, KeyError, IndexError):
        _role.update(creds=None, miss=now)
        return None
    _role.update(creds={'key': j['AccessKeyId'], 'secret': j['SecretAccessKey'], 'token': j['Token'], 'role': role,
                        'region': region}, exp=exp, miss=0.0)
    return _role['creds']


def config():
    """{'key', 'secret', 'token', 'region', 'bucket', 'prefix', 'source'} or None when S3 is not set up."""
    f = _file_vals()
    get = lambda k: os.environ.get(k) or f.get(k) or ''   # noqa: E731
    ui = _ui_vals()
    c = {'region': ui.get('region') or get('AWS_DEFAULT_REGION'), 'bucket': ui.get('bucket') or get('LTH_S3_BUCKET'),
         'prefix': (get('LTH_S3_PREFIX') or os.path.basename(db.RUNNER_DIR.rstrip('\\/')) or 'lth').strip('/'),
         'token': ''}
    if ui.get('key') and ui.get('secret'):
        c.update(key=ui['key'], secret=ui['secret'], source='Settings > System')
    elif get('AWS_ACCESS_KEY_ID') and get('AWS_SECRET_ACCESS_KEY'):
        c.update(key=get('AWS_ACCESS_KEY_ID'), secret=get('AWS_SECRET_ACCESS_KEY'), source=os.path.basename(ENV_FILE))
    elif c['bucket'] and (r := _role_creds()):   # only looked for once a bucket is set
        c.update(key=r['key'], secret=r['secret'], token=r['token'], source=f"EC2 IAM role {r['role']}")
        c['region'] = c['region'] or r['region']
    else:
        return None
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
    if c.get('token'):   # the IAM role's short-lived keys need their session token in the link
        params['X-Amz-Security-Token'] = c['token']
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
    tok = c.get('token') or ''
    signed = 'host;x-amz-content-sha256;x-amz-date' + (';x-amz-security-token' if tok else '')
    canonical = (f'{method}\n{path}\n\nhost:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{amz}\n'
                 + (f'x-amz-security-token:{tok}\n' if tok else '') + f'\n{signed}\n{payload}')
    to_sign = f'AWS4-HMAC-SHA256\n{amz}\n{scope}\n' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    sig = hmac.new(_signing_key(c['secret'], day, c['region']), to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {'x-amz-date': amz, 'x-amz-content-sha256': payload,
               'Authorization': f"AWS4-HMAC-SHA256 Credential={c['key']}/{scope}, "
                                f"SignedHeaders={signed}, Signature={sig}"}
    if tok:
        headers['x-amz-security-token'] = tok
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


def test(c=None):
    """Settings > System 'Test S3': writes, reads back and deletes a small file. Returns (ok, message).
    c: a key typed in Settings, tested before it is saved; default the one in use."""
    c = c or config()
    if not c:
        return False, ('Not set up: put the access key in Settings > System > S3 access key, or in '
                       f'{ENV_FILE} (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION, LTH_S3_BUCKET), '
                       'or attach an IAM role to this EC2 instance and set the bucket.')
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


# ---------------------------------------------------------------- health: a red banner while S3 does not work
# Run screenshots / videos load from S3 through signed links, so a key deleted in AWS breaks every run page at once,
# and nobody sees why (2026-11-12: InvalidAccessKeyId). The sweep tests S3 every EVERY_S seconds and a failed upload
# is noted too; the problem and since when are kept in settings, and base.html shows them until S3 works again.
ERR, SINCE = 's3.health_error', 's3.health_since'
FIXES = {
    'InvalidAccessKeyId': 'the AWS access key in use no longer exists in AWS (deleted or made inactive in IAM). '
                          'Activate it in IAM, or create a new access key and paste it in Settings > System > '
                          'S3 access key.',
    'SignatureDoesNotMatch': 'the AWS secret key in use is wrong. Paste the access key and its secret again in '
                             'Settings > System > S3 access key.',
    'ExpiredToken': "the EC2 IAM role's keys expired and were not renewed. Restart LogiTestHub; check the role.",
    'RequestTimeTooSkewed': "this server's clock is wrong. Fix the time (timedatectl / NTP).",
    'AccessDenied': "the AWS key has no permission on the bucket any more. Check the IAM user's policy (s3:PutObject, "
                    's3:GetObject, s3:DeleteObject on the bucket).',
    'NoSuchBucket': 'the bucket in manager/data/aws.env (LTH_S3_BUCKET) does not exist.',
    'AccountProblem': 'AWS reports a problem with the account (billing or a suspended account).',
}


def note_health(ok, message='', con=None):
    """Keeps S3's state for the banner: a problem keeps its first time ("since"), a working S3 clears it."""
    import budget
    own = con is None
    con = con or db.connect()
    try:
        with con:
            if ok:
                if budget.get_setting(con, ERR):
                    budget.set_setting(con, ERR, '')
                    budget.set_setting(con, SINCE, '')
                    print(' * S3: working again')
            else:
                if not budget.get_setting(con, SINCE):
                    budget.set_setting(con, SINCE, db.now())
                budget.set_setting(con, ERR, message[:500])
    finally:
        if own:
            con.close()


def check_health():
    """test() (write, read, delete a small file) while S3 is set up, also when switched off: the files already on S3
    still load through this key. Not set up: no banner."""
    if not enabled():
        note_health(True)
        return True, ''
    ok, msg = test()
    note_health(ok, msg)
    return ok, msg


def health(con):
    """None while S3 works, else {'message', 'since', 'fix'} for the banner."""
    import budget
    import re
    err = budget.get_setting(con, ERR)
    if not err:
        return None
    code = re.search(r': (\w+) -', err)
    fix = FIXES.get(code.group(1)) if code else None
    return {'message': err, 'since': (budget.get_setting(con, SINCE) or '')[:16],
            'fix': fix or 'see the message; Settings > System > Test S3 checks it again.'}


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
                note_health(False, f'Upload failed. {getattr(e, "reason", e)}')
                break   # the next file would fail the same way; the next sweep tries again
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
    """Moves finished runs still on the disk (newest first) and deletes expired ones.
    Returns (moved, freed, expired, more): more = runs still waiting after this sweep."""
    if not enabled():
        return 0, 0, 0, False
    settle = (datetime.now() - timedelta(seconds=SETTLE_S)).strftime('%Y-%m-%d %H:%M:%S')
    with db.connect() as con:
        rows = con.execute("SELECT id, run_dir FROM runs WHERE run_dir IS NOT NULL AND status NOT IN ('running', 'queued') "
                           'AND started_at < ? ORDER BY id DESC', (settle,)).fetchall()
        gone = expire(con)
        if not switched_on(con):
            return 0, 0, gone, False
    todo = [r['id'] for r in rows if _ours(r['run_dir']) and os.path.isdir(r['run_dir'])
            and any(n not in KEEP for n in os.listdir(r['run_dir']))]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(WORKERS, thread_name_prefix='lth-s3-up') as pool:
        done = list(pool.map(offload, todo[:BATCH]))
    return sum(m for m, _ in done), sum(f for _, f in done), gone, len(todo) > BATCH


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
                ok, msg = check_health()
                if not ok:
                    print(' * S3 not working:', msg)
                moved, freed, gone, more = sweep() if ok else (0, 0, 0, False)
                if moved or gone:
                    print(f' * S3: moved {moved} run files ({freed / 1e6:.0f} MB freed), deleted {gone} past their days'
                          + (' - more to move' if more else ''))
            except Exception as ex:   # never stop the server over storage
                print(' * S3 sweep error:', ex)
                more = False
            time.sleep(AGAIN_S if more else EVERY_S)
    threading.Thread(target=loop, daemon=True, name='lth-s3').start()
