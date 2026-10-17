"""Tester CLI page (like Kane CLI) + the installable package.

  /cli            how to install and use `tester` (For Humans / For Agents)
  /cli/tester.tgz    the npm package, built from the runner folder with `npm pack` (no login: npm downloads it)

Registered from app.py with make_blueprint(login_required) so this module never imports app.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import threading

from flask import Blueprint, abort, render_template, request, send_file

import db

OUT_DIR = os.path.join(db.DATA, 'cli')
SOURCES = ['package.json', 'run.js', 'cli/*.js', 'cli/skill/*.md', 'cli/skill/references/*.md', 'lib/*.js', 'manager/static/logo.svg', 'manager/static/logo-lmx-*ball.png', 'manager/static/playwright-logo.svg']
_build_lock = threading.Lock()


def _version():
    with open(os.path.join(db.RUNNER_DIR, 'package.json'), encoding='utf-8') as f:
        return json.load(f)['version']


def _sources_mtime():
    files = [p for s in SOURCES for p in glob.glob(os.path.join(db.RUNNER_DIR, s))]
    return max(os.path.getmtime(p) for p in files)


def package_path():
    """Path of an up-to-date tarball, rebuilt when a runner/CLI file changed since the last build."""
    with _build_lock:
        tgz = os.path.join(OUT_DIR, f'logitesthub-cli-{_version()}.tgz')
        if os.path.isfile(tgz) and os.path.getmtime(tgz) >= _sources_mtime():
            return tgz
        os.makedirs(OUT_DIR, exist_ok=True)
        npm = shutil.which('npm') or shutil.which('npm.cmd')
        if not npm:
            raise RuntimeError('npm not found on the LogiTestHub server')
        proc = subprocess.run([npm, 'pack', '--pack-destination', OUT_DIR], cwd=db.RUNNER_DIR, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=120)
        if proc.returncode != 0 or not os.path.isfile(tgz):
            raise RuntimeError((proc.stderr or proc.stdout or 'npm pack failed').strip().splitlines()[-1][:300])
        return tgz


# the code a tester's CLI runs, plus the /tester skill it installs (same list in cli/lth.js: cliFingerprint)
CODE = ['run.js', 'cli/*.js', 'lib/*.js', 'cli/skill/*.md', 'cli/skill/references/*.md']
_fp = {}


def fingerprint():
    """Short hash of the CLI code this server hands out. The CLI compares it with its own to update itself
    (package.json keeps one version across changes, so the version cannot tell)."""
    import hashlib
    files = sorted({os.path.relpath(p, db.RUNNER_DIR).replace(os.sep, '/')
                    for s in CODE for p in glob.glob(os.path.join(db.RUNNER_DIR, s))})
    key = (tuple(files), max(os.path.getmtime(os.path.join(db.RUNNER_DIR, f)) for f in files))
    if _fp.get('key') != key:
        h = hashlib.sha256()
        for f in files:
            with open(os.path.join(db.RUNNER_DIR, f), 'rb') as fh:
                h.update(f.encode() + b'\n' + fh.read() + b'\n')
        _fp.update(key=key, value=h.hexdigest()[:16])
    return _fp['value']


def lan_ip():
    """This PC's address on the office network (no packet is sent: UDP connect only picks the route)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('10.255.255.255', 1))
            ip = s.getsockname()[0]
        return None if ip.startswith('127.') else ip
    except OSError:
        return None


def make_blueprint(login_required):
    bp = Blueprint('cli', __name__)

    @bp.route('/cli')
    @login_required
    def page():
        server, local_only = request.host_url.rstrip('/'), False
        if request.host.split(':')[0] in ('localhost', '127.0.0.1'):
            ip = lan_ip()   # opened on the server PC itself: other PCs need its LAN address, not localhost
            if ip:
                server = f"{request.scheme}://{ip}:{request.host.split(':')[1] if ':' in request.host else 80}"
            else:
                local_only = True
        return render_template('cli.html', server=server, version=_version(), local_only=local_only,
                               tab='agents' if request.args.get('tab') == 'agents' else 'humans')

    @bp.route('/cli/tester.tgz')
    @bp.route('/cli/lth.tgz')   # old address, before the rename
    def package():
        try:
            path = package_path()
        except Exception as e:   # shown to whoever runs npm install
            abort(503, description=f'Tester CLI package could not be built: {e}')
        return send_file(path, mimetype='application/gzip', as_attachment=True,
                         download_name=os.path.basename(path), max_age=0)

    return bp
