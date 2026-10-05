"""Work shared by the web app and the API (no Flask here)."""
import json
import os
import re
import subprocess
import tempfile

import db


def bridge_step(req, timeout=180):
    """Asks Claude (via ai-bridge.js on this server) what to do for one step. Returns the bridge reply dict."""
    try:
        proc = subprocess.run(['node', os.path.join(db.RUNNER_DIR, 'ai-bridge.js')], input=json.dumps(dict(req, op='step')),
                              cwd=db.RUNNER_DIR, capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'AI step took too long.'}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {'ok': False, 'error': (proc.stderr or 'AI bridge failed').strip().splitlines()[-1][:300]}


def generate_cases(pid, folder_id, task, count, repo, username, diff_text=None):
    """Runs generate.js and imports what it wrote. Returns (ok, message, new_case_ids). Budget is checked by callers.
    diff_text: a git diff made elsewhere (the Tester CLI sends the developer's local changes)."""
    count = max(1, min(int(count or 6), 15))
    cmd = ['node', os.path.join(db.RUNNER_DIR, 'generate.js'), '--task', task, '--count', str(count)]
    import lessons
    with db.connect() as c:
        if (c.execute('SELECT login_helper FROM projects WHERE id=?', (pid,)).fetchone() or {}).get('login_helper'):
            cmd.append('--project-login')   # cases start after login: the project's login helper runs first
        learned = lessons.prompt_text(c, pid)
    diff_path = lessons_path = None
    if learned:   # mistakes made before in this project: the generated cases must not repeat them
        fd, lessons_path = tempfile.mkstemp(suffix='.txt', dir=db.DATA)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(learned)
        cmd += ['--lessons-file', lessons_path]
    if diff_text:
        fd, diff_path = tempfile.mkstemp(suffix='.diff', dir=db.DATA)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(diff_text)
        cmd += ['--diff-file', diff_path]
    elif repo:
        if not os.path.isdir(os.path.join(repo, '.git')):
            return False, f'"{repo}" is not a git repository.', []
        cmd += ['--repo', repo]
    try:
        proc = subprocess.run(cmd, cwd=db.RUNNER_DIR, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=900)
    except subprocess.TimeoutExpired:
        return False, 'AI generation took longer than 15 minutes and was stopped.', []
    finally:
        for tmp in (diff_path, lessons_path):
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
    m = re.search(r'Saved \d+ cases to (.+)', proc.stdout)
    if not m:
        msg = (proc.stderr or proc.stdout or 'Generation failed').strip().splitlines()[-1][:300]
        return False, f'Generate with AI failed: {msg}', []
    out_dir, new_ids = m.group(1).strip(), []
    um = re.search(r'^USAGE_JSON (\{.*\})$', proc.stdout, re.M)
    with db.connect() as c:
        for name in sorted(os.listdir(out_dir)):
            if name.endswith('_test.md'):
                with open(os.path.join(out_dir, name), encoding='utf-8') as fh:
                    text = fh.read().replace('\r\n', '\n').strip() + '\n'
                    title = db.case_title(text, '')
                    if title:
                        new_ids.append(db.add_case(c, pid, folder_id, title, text, username))
        if um and new_ids:   # one AI call made all the cases: split its tokens evenly across them
            usage = json.loads(um.group(1))
            for i, cid in enumerate(new_ids):
                db.record_ai_usage(c, 'generate', dict(usage, calls=usage.get('calls', 1) if i == 0 else 0),
                                   username=username, project_id=pid, case_id=cid,
                                   share=1 / len(new_ids), note=f'1 AI call generated {len(new_ids)} cases')
    return True, f'Generated {len(new_ids)} test case(s) with AI. Review them before running.', new_ids
