"""Auto-heal of Test Runs / schedules: when a screen changed on the environment (a new required field, a new id or
label), the runner (run.js, LTH_HEAL=1) fills / clicks what the AI found and the test goes on. Here, after the run:

- the change is kept in the test case (only when the run PASSED, and only when the step is in the case itself, not in
  the login helper or an @import), with the old content saved in case_heals;
- the commit that brought the change is looked up in the environment's code copy (who, when, which file);
- one plain-English message says what changed, who changed it and what was updated: case page and PM note.

Asserts are never changed by a heal (run.js refuses it), so a wrong result still fails as an app bug.
"""
import json
import os
import re

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_heals (
  id INT AUTO_INCREMENT PRIMARY KEY, run_id INT NOT NULL, case_id INT NOT NULL, env_name VARCHAR(40),
  message TEXT NOT NULL, changes TEXT, old_content MEDIUMTEXT, new_content MEDIUMTEXT, case_updated TINYINT NOT NULL DEFAULT 0,
  at VARCHAR(32) NOT NULL, KEY ix_case_heals_run (run_id), KEY ix_case_heals_case (case_id)
) ENGINE=InnoDB
"""
STEP_PREFIX = re.compile(r'^(\s*(?:\d+\.|[-*])\s+)?')


def init(con):
    con.execute(SCHEMA)


def heal_events(run_dir):
    out = []
    try:
        with open(os.path.join(run_dir, 'events.ndjson'), encoding='utf-8') as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get('type') == 'heal' and e.get('ok'):
                    out.append(e)
    except OSError:
        pass
    return out


def apply_edit(content, edit):
    """The case text with one heal edit applied, or (content, why not) when the step is not in the case exactly once."""
    target = (edit.get('target') or '').strip()
    lines = content.split('\n')
    hits = [i for i, l in enumerate(lines) if STEP_PREFIX.sub('', l, count=1).strip() == target]
    if not target or len(hits) != 1:
        return content, ('the step is not in the test case itself (login helper or an imported file)' if not hits
                         else 'the step is in the test case more than once')
    i = hits[0]
    new = [str(s).strip() for s in edit.get('steps') or [] if str(s).strip()]
    if edit.get('op') == 'insert_before':
        lines[i:i] = new
    elif edit.get('op') == 'replace':
        lines[i:i + 1] = new
    else:
        return content, 'nothing to change'
    return '\n'.join(lines), None


def commit_of(code_dir, name, token=None):
    """Who brought `name` (an id / name in the page) into the code: {sha, author, date, subject, file} or None.
    git blame of the line; when that cannot be read, the last commit that changed the file."""
    if not (code_dir and name and re.fullmatch(r'[\w.-]{2,80}', name)):
        return None
    import coderepo
    try:
        hit = coderepo._git(['grep', '-n', '-I', '-w', '-F', name, '--', '*.php', '*.js', '*.html'], cwd=code_dir, timeout=60)
    except Exception:   # not a git copy, or not found
        return None
    first = next((l for l in hit.splitlines() if l.count(':') >= 2), None)
    if not first:
        return None
    path, line = first.split(':', 2)[:2]
    try:
        out = coderepo._git(['blame', '--porcelain', '-L', f'{line},{line}', '--', path], cwd=code_dir, token=token, timeout=120)
    except Exception:
        out = ''
    if not out:
        try:
            sha, author, when, subject = coderepo._git(['log', '-1', '--format=%h%x1f%an%x1f%at%x1f%s', '--', path],
                                                       cwd=code_dir, timeout=60).split('\x1f', 3)
            from datetime import datetime
            return {'file': path.replace('\\', '/'), 'sha': sha, 'author': author, 'subject': subject,
                    'date': datetime.fromtimestamp(int(when)).strftime('%d-%m-%Y'), 'file_level': True}
        except Exception:
            return None
    info = {'file': path.replace('\\', '/')}
    for l in out.splitlines():
        if re.fullmatch(r'[0-9a-f]{40} .*', l) and 'sha' not in info:
            info['sha'] = l[:7]
        elif l.startswith('author '):
            info['author'] = l[7:]
        elif l.startswith('author-time '):
            from datetime import datetime
            info['date'] = datetime.fromtimestamp(int(l[12:])).strftime('%d-%m-%Y')
        elif l.startswith('summary '):
            info['subject'] = l[8:]
        elif l.startswith('boundary'):
            info['shallow'] = True   # the copy has only recent history: the change is in this commit or an older one
    return info if info.get('sha') else None


def _message(env_name, heals, commits, updated, why_not):
    parts = []
    for h, cm in zip(heals, commits):
        txt = re.sub(r'\\+"', '"', h.get('explain') or '').strip()
        if cm:
            who = f"commit {cm['sha']}" + (f" by {cm['author']}" if cm.get('author') else '') + \
                  (f" on {cm['date']}" if cm.get('date') else '') + (f': "{cm["subject"]}"' if cm.get('subject') else '') + \
                  f" ({cm['file']})" + (' or an older commit' if cm.get('shallow') else '') +                   (' - the last change to that file' if cm.get('file_level') else '')
            txt += f' Changed in {who}.'
        parts.append(txt)
    msg = f'Auto-healed on {env_name or "this environment"}: ' + ' '.join(parts)
    if updated:
        msg += ' The test case was updated: ' + '; '.join(updated) + '.'
    elif why_not:
        msg += f' The test case was not changed: {why_not}.'
    return msg


def after_run(run_id, run_dir):
    """Called when a run is recorded: keeps the heals of a passed run in its case and writes the message."""
    heals = heal_events(run_dir) if run_dir else []
    if not heals:
        return None
    import coderepo
    import envs
    import settings
    c = db.connect()
    try:
        r = c.execute('SELECT r.status, r.env_id, r.env_name, c.id AS case_id, c.tc_no, c.content '
                      'FROM runs r JOIN cases c ON c.id=r.case_id WHERE r.id=?', (run_id,)).fetchone()
        if not r:
            return None
        env = envs.get(c, r['env_id']) if r['env_id'] else None
        code = coderepo.code_dir(env)
        token = envs.decrypt(env['repo_token_enc']) if env and env.get('repo_token_enc') else None
        commits = [commit_of(code, (h.get('change') or {}).get('code_name'), token) for h in heals]
        content, updated, why_not = r['content'], [], None
        if r['status'] != 'PASSED':
            why_not = 'the run did not pass after the heal'
        else:
            for h in heals:
                e = h.get('case_edit') or {}
                new, why = apply_edit(content, e)
                if why:
                    why_not = why_not or why
                    continue
                content = new
                where = 'before' if e.get('op') == 'insert_before' else 'in place of'
                updated.append(f"added {where} \"{e.get('target')}\": " + ' '.join(f'"{s}"' for s in e.get('steps') or []))
        msg = _message(r['env_name'], heals, commits, updated, why_not)
        with c:
            if updated:
                c.execute("UPDATE cases SET content=?, updated_by='auto-heal', updated_at=? WHERE id=?", (content, db.now(), r['case_id']))
            c.execute('INSERT INTO case_heals(run_id, case_id, env_name, message, changes, old_content, new_content, case_updated, at) '
                      'VALUES (?,?,?,?,?,?,?,?,?)', (run_id, r['case_id'], r['env_name'], msg,
                                                     json.dumps([{**h, 'commit': cm} for h, cm in zip(heals, commits)], ensure_ascii=False),
                                                     r['content'], content if updated else None, 1 if updated else 0, db.now()))
        settings.write_audit('auto-heal', 'case_heal', 'test case', r['case_id'], 'ok', f"TC-{r['tc_no']}: {msg}"[:500])
        return msg
    finally:
        c.close()


def of_run(con, run_id):
    return con.execute('SELECT * FROM case_heals WHERE run_id=? ORDER BY id DESC LIMIT 1', (run_id,)).fetchone() if run_id else None
