"""Tester App: live manual testing of a mobile app on a real phone / tablet (LambdaTest / TestMu AI device cloud).

Upload an APK / IPA, pick brand -> model -> OS version, Start: LogiTestHub opens an Appium session on that real device
in the LambdaTest cloud and the page shows its screen (~2 frames a second) and sends taps, swipes, text and keys.
The LambdaTest key stays on this server (Settings -> Integrations); the browser talks only to LogiTestHub.

- Apps: uploaded to LambdaTest's real-device storage (kept there ~60 days); this table keeps who uploaded what.
- Devices: LambdaTest's real-device list, grouped by brand (cached for 10 minutes).
- Sessions: one row per live session. A background thread starts it (~30 s); a session nobody looked at for
  IDLE_END seconds (tab closed) or that ran MAX_MINUTES is ended, so it does not hold a parallel slot.

Registered from app.py with register(app, con, login_required, admin_required, current_user).
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

import db

UPLOAD_URL = 'https://manual-api.lambdatest.com/app/upload/realDevice'
DELETE_URL = 'https://manual-api.lambdatest.com/app/delete'
DEVICES_URL = 'https://mobile-api.lambdatest.com/mobile-automation/api/v1/list?region=us&os={os}'
CONCURRENCY_URL = 'https://mobile-api.lambdatest.com/mobile-automation/api/v1/org/concurrency'
HUB = 'https://mobile-hub.lambdatest.com/wd/hub'
MAX_APP_MB = 300
IDLE_END = 120          # seconds without the page asking for the screen -> the session is ended
MAX_MINUTES = 30
APP_RE = re.compile(r'\.(apk|aab|ipa)$', re.I)
BRANDS = {'galaxy': 'Samsung', 'samsung': 'Samsung', 'pixel': 'Google', 'moto': 'Motorola', 'motorola': 'Motorola',
          'oneplus': 'OnePlus', 'vivo': 'Vivo', 'oppo': 'Oppo', 'redmi': 'Xiaomi', 'xiaomi': 'Xiaomi', 'poco': 'Xiaomi',
          'mi': 'Xiaomi', 'huawei': 'Huawei', 'honor': 'Honor', 'realme': 'Realme', 'nokia': 'Nokia', 'microsoft': 'Microsoft',
          'iphone': 'iPhone', 'ipad': 'iPad', 'lg': 'LG', 'sony': 'Sony', 'nothing': 'Nothing', 'tecno': 'Tecno', 'infinix': 'Infinix'}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS mobile_apps (
  id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL, platform VARCHAR(10) NOT NULL, lt_app VARCHAR(120) NOT NULL,
  size_kb INT NULL, uploaded_by VARCHAR(64), uploaded_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS mobile_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(64) NOT NULL, app_id INT NULL, app_name VARCHAR(255),
  platform VARCHAR(10) NOT NULL, device VARCHAR(120) NOT NULL, os_version VARCHAR(20) NOT NULL,
  lt_session VARCHAR(80) NULL, status VARCHAR(12) NOT NULL DEFAULT 'starting', error VARCHAR(500) NULL,
  width INT NULL, height INT NULL, started_at VARCHAR(32) NOT NULL, live_at VARCHAR(32) NULL, ended_at VARCHAR(32) NULL,
  last_seen DOUBLE NULL, KEY ix_mobile_sessions_user (username, id)
) ENGINE=InnoDB""",
]


class LtError(Exception):
    pass


# ---------------------------------------------------------------- credentials (Settings -> Integrations)

def creds(con):
    import budget
    import envs
    user = budget.get_setting(con, 'lt.username') or ''
    key = envs.decrypt(budget.get_setting(con, 'lt.key') or '')
    return (user, key) if user and key else None


def _auth(c):
    return 'Basic ' + base64.b64encode(f'{c[0]}:{c[1]}'.encode()).decode()


def _http(c, method, url, body=None, headers=None, timeout=60, raw=False):
    h = {'Authorization': _auth(c), 'Accept': 'application/json'}
    if headers:
        h.update(headers)
    if body is not None and not isinstance(body, (bytes, bytearray)):
        body, h['Content-Type'] = json.dumps(body).encode(), 'application/json'
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        text = (e.read() or b'')[:600].decode('utf8', 'replace')
        try:
            j = json.loads(text)
            v = j.get('value') if isinstance(j.get('value'), dict) else {}
            text = v.get('message') or j.get('message') or j.get('error') or text
        except ValueError:
            pass
        if e.code == 401:
            text = 'LambdaTest refused the username / access key (Settings -> Integrations -> LambdaTest).'
        raise LtError(str(text)[:400])
    except Exception as e:   # network, timeout
        raise LtError(f'Cannot reach LambdaTest ({e}).')
    if raw:
        return data
    try:
        return json.loads(data or b'{}')
    except ValueError:
        raise LtError('Unexpected reply from LambdaTest.')


def concurrency(c):
    return (_http(c, 'GET', CONCURRENCY_URL, timeout=20).get('data') or {})


# ---------------------------------------------------------------- devices, grouped by brand

_DEV_CACHE = {}


def brand_of(name):
    w = (name or '').split()
    return BRANDS.get(w[0].lower(), w[0]) if w else 'Other'


def devices(c, os_name):
    """[{"brand", "models": [{"name", "versions": [...newest first]}]}], the brand with the most devices first."""
    hit = _DEV_CACHE.get(os_name)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    rows = _http(c, 'GET', DEVICES_URL.format(os=os_name), timeout=30)
    models = {}
    for d in rows if isinstance(rows, list) else []:
        n, v = str(d.get('deviceName') or '').strip(), str(d.get('platformVersion') or '').strip()
        if n and v:
            models.setdefault(n, set()).add(v)
    ver = lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r'\D+', v) if x]
    out = {}
    for n, vs in models.items():
        out.setdefault(brand_of(n), []).append({'name': n, 'versions': sorted(vs, key=ver, reverse=True)})
    for b in out:
        out[b].sort(key=lambda m: (-ver(m['versions'][0])[0] if ver(m['versions'][0]) else 0, m['name']))
    out = [{'brand': b, 'models': m} for b, m in sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    _DEV_CACHE[os_name] = (time.time(), out)
    return out


# ---------------------------------------------------------------- apps

def upload_app(c, filename, data):
    b = '----lth' + uuid.uuid4().hex
    body = (f'--{b}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{filename}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="appFile"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + data + f'\r\n--{b}--\r\n'.encode()
    r = _http(c, 'POST', UPLOAD_URL, body=body, headers={'Content-Type': f'multipart/form-data; boundary={b}'}, timeout=600)
    if not r.get('app_url'):
        raise LtError(r.get('message') or 'LambdaTest did not return an app id.')
    return r['app_url'], (r.get('type') or '').lower()


# ---------------------------------------------------------------- one live session (Appium on the real device)

def _wd(c, method, path, body=None, timeout=60, raw=False):
    return _http(c, method, HUB + path, body=body, timeout=timeout, raw=raw)


def _set(sid, **kw):
    cx = db.connect()
    try:
        with cx:
            cx.execute(f'UPDATE mobile_sessions SET {", ".join(k + "=?" for k in kw)} WHERE id=?', (*kw.values(), sid))
    finally:
        cx.close()


def _start(sid, c, platform, device, version, lt_app, app_name, username):
    caps = {'capabilities': {'firstMatch': [{}], 'alwaysMatch': {'platformName': platform, 'lt:options': {
        'w3c': True, 'isRealMobile': True, 'deviceName': device, 'platformVersion': version, 'app': lt_app,
        'build': 'LogiTestHub Tester App', 'name': f'{app_name} - {username} (live)', 'idleTimeout': 300,
        'video': True, 'autoGrantPermissions': True, 'autoAcceptAlerts': True}}}}
    try:
        s = _wd(c, 'POST', '/session', caps, timeout=360)
        v = s.get('value') or {}
        lt_session = v.get('sessionId') or s.get('sessionId')
        if not lt_session:
            raise LtError(v.get('message') or 'The device did not start.')
        try:
            rect = _wd(c, 'GET', f'/session/{lt_session}/window/rect', timeout=30).get('value') or {}
        except LtError:
            rect = {}
        _set(sid, lt_session=lt_session, status='live', live_at=db.now(), last_seen=time.time(),
             width=rect.get('width'), height=rect.get('height'))
    except LtError as e:
        _set(sid, status='error', error=str(e)[:500], ended_at=db.now())
    except Exception as e:   # never leave a session "starting" forever
        _set(sid, status='error', error=f'Could not start: {e}'[:500], ended_at=db.now())


def end_session(c, row, why=None):
    if row['lt_session'] and row['status'] == 'live' and c:
        try:
            _wd(c, 'DELETE', f'/session/{row["lt_session"]}', timeout=30)
        except LtError:
            pass
    _set(row['id'], status='ended', ended_at=db.now(), **({'error': why} if why else {}))


def _reaper():
    """Ends sessions whose page is gone (no screen request for IDLE_END s) or that ran MAX_MINUTES."""
    while True:
        time.sleep(30)
        cx = db.connect()
        try:
            c = creds(cx)
            rows = cx.execute("SELECT * FROM mobile_sessions WHERE status='live'").fetchall()
            now = time.time()
            for r in rows:
                too_old = r['live_at'] and (now - time.mktime(time.strptime(r['live_at'][:19], '%Y-%m-%d %H:%M:%S'))) > MAX_MINUTES * 60
                if (r['last_seen'] and now - r['last_seen'] > IDLE_END) or too_old:
                    end_session(c, r, 'Ended: the page was closed / idle.' if not too_old else f'Ended after {MAX_MINUTES} minutes.')
        except Exception:
            pass
        finally:
            cx.close()


def _tap(c, ls, x, y):
    _wd(c, 'POST', f'/session/{ls}/actions', {'actions': [{'type': 'pointer', 'id': 'f1', 'parameters': {'pointerType': 'touch'},
        'actions': [{'type': 'pointerMove', 'duration': 0, 'x': x, 'y': y}, {'type': 'pointerDown', 'button': 0},
                    {'type': 'pause', 'duration': 60}, {'type': 'pointerUp', 'button': 0}]}]}, timeout=30)


def _swipe(c, ls, x1, y1, x2, y2, ms):
    _wd(c, 'POST', f'/session/{ls}/actions', {'actions': [{'type': 'pointer', 'id': 'f1', 'parameters': {'pointerType': 'touch'},
        'actions': [{'type': 'pointerMove', 'duration': 0, 'x': x1, 'y': y1}, {'type': 'pointerDown', 'button': 0},
                    {'type': 'pointerMove', 'duration': max(100, min(ms, 2000)), 'x': x2, 'y': y2}, {'type': 'pointerUp', 'button': 0}]}]},
        timeout=30)


def _type(c, ls, text):
    el = _wd(c, 'POST', f'/session/{ls}/element/active', {}, timeout=30).get('value') or {}
    eid = next(iter(el.values()), None) if isinstance(el, dict) else None
    if not eid:
        raise LtError('Tap a text field first, then type.')
    _wd(c, 'POST', f'/session/{ls}/element/{eid}/value', {'text': text, 'value': list(text)}, timeout=30)


ANDROID_KEYS = {'back': 4, 'home': 3, 'recent': 187, 'enter': 66}


def _key(c, ls, platform, key):
    if platform == 'android':
        _wd(c, 'POST', f'/session/{ls}/appium/device/press_keycode', {'keycode': ANDROID_KEYS[key]}, timeout=30)
    elif key == 'home':
        _wd(c, 'POST', f'/session/{ls}/execute/sync', {'script': 'mobile: pressButton', 'args': [{'name': 'home'}]}, timeout=30)
    else:
        raise LtError('iPhone / iPad has no Back key: use the app\'s own back button.')


# ---------------------------------------------------------------- pages

def register(app, con, login_required, admin_required, current_user):
    cx = db.connect()
    try:
        for s in SCHEMA:
            cx.execute(s)
        with cx:   # a session that was starting / live when the server stopped is gone
            cx.execute("UPDATE mobile_sessions SET status='ended', ended_at=?, error='Ended when the server restarted.' "
                       "WHERE status IN ('starting','live')", (db.now(),))
    finally:
        cx.close()
    threading.Thread(target=_reaper, name='lth-mobile-reaper', daemon=True).start()
    bp = Blueprint('mobile', __name__)

    def body():   # the page posts a form (the app's CSRF check applies) with the data as JSON in "payload"
        try:
            d = json.loads(request.form.get('payload') or '{}')
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def need_creds():
        c = creds(con())
        if not c:
            abort(Response(json.dumps({'ok': False, 'error': 'LambdaTest is not connected (Settings -> Integrations).'}),
                           400, mimetype='application/json'))
        return c

    def my_session(sid):
        r = con().execute('SELECT * FROM mobile_sessions WHERE id=?', (sid,)).fetchone() or abort(404)
        if r['username'] != current_user()['username']:
            abort(404)
        return r

    @bp.route('/tester-app')
    @login_required
    def page():
        import settings
        me = current_user()
        c = creds(con())
        apps = con().execute('SELECT * FROM mobile_apps ORDER BY id DESC LIMIT 100').fetchall()
        mine = con().execute('SELECT * FROM mobile_sessions WHERE username=? ORDER BY id DESC LIMIT 15', (me['username'],)).fetchall()
        return render_template('tester_app.html', connected=bool(c), apps=apps, sessions=mine,
                               is_admin=settings.is_admin(me['role']))

    @bp.route('/tester-app/devices')
    @login_required
    def devices_json():
        c = need_creds()
        os_name = 'ios' if request.args.get('os') == 'ios' else 'android'
        try:
            return jsonify({'ok': True, 'brands': devices(c, os_name)})
        except LtError as e:
            return jsonify({'ok': False, 'error': str(e)}), 502

    @bp.route('/tester-app/apps', methods=['POST'])
    @login_required
    def app_upload():
        request.max_content_length = MAX_APP_MB * 1024 * 1024
        c = creds(con())
        if not c:
            flash('LambdaTest is not connected yet (Settings -> Integrations).', 'danger')
            return redirect(url_for('mobile.page'))
        f = request.files.get('app')
        name = os.path.basename((f.filename if f else '') or '').strip()
        if not f or not APP_RE.search(name):
            flash('Pick an Android .apk / .aab or an iOS .ipa file.', 'danger')
            return redirect(url_for('mobile.page'))
        data = f.read()
        try:
            lt_app, kind = upload_app(c, name, data)
        except LtError as e:
            flash(f'Upload failed: {e}', 'danger')
            return redirect(url_for('mobile.page'))
        platform = 'ios' if name.lower().endswith('.ipa') or kind == 'ios' else 'android'
        cx = con()
        with cx:
            cx.execute('INSERT INTO mobile_apps(name, platform, lt_app, size_kb, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?)',
                       (name[:255], platform, lt_app, len(data) // 1024, current_user()['username'], db.now()))
        flash(f'"{name}" uploaded: pick a device and Start.', 'success')
        return redirect(url_for('mobile.page'))

    @bp.route('/tester-app/apps/<int:aid>/delete', methods=['POST'])
    @login_required
    def app_delete(aid):
        import settings
        a = con().execute('SELECT * FROM mobile_apps WHERE id=?', (aid,)).fetchone() or abort(404)
        me = current_user()
        if a['uploaded_by'] != me['username'] and not settings.is_admin(me['role']):
            abort(403)
        c = creds(con())
        if c:
            try:
                _http(c, 'DELETE', DELETE_URL, body={'appIds': a['lt_app'].replace('lt://', '')}, timeout=30)
            except LtError:
                pass   # expired / already gone there
        with con():
            con().execute('DELETE FROM mobile_apps WHERE id=?', (aid,))
        flash(f'"{a["name"]}" deleted.', 'success')
        return redirect(url_for('mobile.page'))

    @bp.route('/tester-app/sessions', methods=['POST'])
    @login_required
    def session_start():
        c = need_creds()
        me = current_user()
        b = body()
        a = con().execute('SELECT * FROM mobile_apps WHERE id=?', (b.get('app_id'),)).fetchone()
        device, version = str(b.get('device') or '').strip(), str(b.get('os_version') or '').strip()
        if not a or not device or not version:
            return jsonify({'ok': False, 'error': 'Pick an app, a device and an OS version.'}), 400
        if con().execute("SELECT 1 FROM mobile_sessions WHERE username=? AND status IN ('starting','live')", (me['username'],)).fetchone():
            return jsonify({'ok': False, 'error': 'You already have a device open: end it first (Recent sessions).'}), 409
        try:
            cc = concurrency(c)
            if cc.get('max_concurrency') is not None and int(cc.get('running') or 0) >= int(cc['max_concurrency']):
                return jsonify({'ok': False, 'error': f'All {cc["max_concurrency"]} LambdaTest device slots are in use. Try again in a few minutes.'}), 409
        except LtError:
            pass
        with con():
            sid = con().execute('INSERT INTO mobile_sessions(username, app_id, app_name, platform, device, os_version, status, started_at, last_seen) '
                                'VALUES (?,?,?,?,?,?,?,?,?)', (me['username'], a['id'], a['name'], a['platform'], device[:120], version[:20],
                                                              'starting', db.now(), time.time())).lastrowid
        threading.Thread(target=_start, args=(sid, c, a['platform'], device, version, a['lt_app'], a['name'], me['username']),
                         name=f'lth-mobile-{sid}', daemon=True).start()
        return jsonify({'ok': True, 'url': url_for('mobile.live', sid=sid)})

    @bp.route('/tester-app/sessions/<int:sid>')
    @login_required
    def live(sid):
        return render_template('tester_app_live.html', s=my_session(sid), idle_end=IDLE_END, max_minutes=MAX_MINUTES)

    @bp.route('/tester-app/sessions/<int:sid>/status')
    @login_required
    def status(sid):
        r = my_session(sid)
        if r['status'] == 'starting':
            with con():
                con().execute('UPDATE mobile_sessions SET last_seen=? WHERE id=?', (time.time(), sid))
        return jsonify({'ok': True, 'status': r['status'], 'error': r['error'], 'width': r['width'], 'height': r['height']})

    @bp.route('/tester-app/sessions/<int:sid>/screen')
    @login_required
    def screen(sid):
        r = my_session(sid)
        if r['status'] != 'live':
            return jsonify({'ok': False, 'status': r['status'], 'error': r['error']}), 409
        c = need_creds()
        try:
            v = _wd(c, 'GET', f'/session/{r["lt_session"]}/screenshot', timeout=30).get('value')
        except LtError as e:
            return jsonify({'ok': False, 'error': str(e)}), 502
        with con():
            con().execute('UPDATE mobile_sessions SET last_seen=? WHERE id=?', (time.time(), sid))
        return Response(base64.b64decode(v or ''), mimetype='image/png', headers={'Cache-Control': 'no-store'})

    @bp.route('/tester-app/sessions/<int:sid>/act', methods=['POST'])
    @login_required
    def act(sid):
        r = my_session(sid)
        if r['status'] != 'live':
            return jsonify({'ok': False, 'error': 'The device is not live.'}), 409
        c = need_creds()
        b = body()
        ls, kind = r['lt_session'], b.get('kind')
        num = lambda k: int(float(b.get(k) or 0))
        try:
            if kind == 'tap':
                _tap(c, ls, num('x'), num('y'))
            elif kind == 'swipe':
                _swipe(c, ls, num('x1'), num('y1'), num('x2'), num('y2'), num('ms') or 300)
            elif kind == 'type':
                _type(c, ls, str(b.get('text') or '')[:500])
            elif kind == 'key' and b.get('key') in ANDROID_KEYS:
                _key(c, ls, r['platform'], b['key'])
            else:
                return jsonify({'ok': False, 'error': 'Unknown action.'}), 400
        except LtError as e:
            return jsonify({'ok': False, 'error': str(e)}), 502
        with con():
            con().execute('UPDATE mobile_sessions SET last_seen=? WHERE id=?', (time.time(), sid))
        return jsonify({'ok': True})

    @bp.route('/tester-app/sessions/<int:sid>/end', methods=['POST'])
    @login_required
    def end(sid):
        r = my_session(sid)
        if r['status'] in ('starting', 'live'):
            end_session(creds(con()), r)
        return jsonify({'ok': True}) if request.form.get('payload') is not None else redirect(url_for('mobile.page'))

    # ---- LambdaTest connection (admins), shown on Settings -> Integrations
    @bp.route('/tester-app/lambdatest', methods=['POST'])
    @admin_required
    def lt_save():
        import budget
        import envs
        user, key = request.form.get('lt_username', '').strip(), request.form.get('lt_key', '').strip()
        cx = con()
        with cx:
            if request.form.get('remove'):
                budget.set_setting(cx, 'lt.username', '')
                budget.set_setting(cx, 'lt.key', '')
            else:
                if user:
                    budget.set_setting(cx, 'lt.username', user[:120])
                if key:
                    budget.set_setting(cx, 'lt.key', envs.encrypt(key))
        c = creds(cx)
        if c:
            try:
                cc = concurrency(c)
                flash(f'Connected to LambdaTest: {cc.get("max_concurrency", "?")} real-device slot(s), {cc.get("running", 0)} in use.', 'success')
            except LtError as e:
                flash(f'Saved, but LambdaTest refused it: {e}', 'danger')
        else:
            flash('LambdaTest disconnected.' if request.form.get('remove') else 'Type the username and the access key.', 'info')
        return redirect(url_for('settings.integrations') + '#lambdatest')

    app.register_blueprint(bp)
