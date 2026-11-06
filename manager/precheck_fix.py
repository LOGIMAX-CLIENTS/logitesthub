"""Suggest a fix for a case the pre-run check says will fail on an environment (precheck.py).

The data a case picks is missing there (no Sales Return tag with carat stones, a uom made only on one PC). AI looks at
the environment's data with read-only SELECTs (a few rounds), then proposes new steps that pick data the environment
has while keeping what the test is about. Before it is shown, the changed case is checked again (precheck.check_case):
only a change that passes that check is offered. Nothing changes until the user presses OK; the old text is kept in
precheck_fixes. When the environment has no data that fits the test, it says which record to create there instead.
"""
import json
import os
import re
import subprocess
import threading

import db
import envs
import precheck
import settings

SCHEMA = """CREATE TABLE IF NOT EXISTS precheck_fixes (
  id INT AUTO_INCREMENT PRIMARY KEY, precheck_id INT NOT NULL, case_id INT NOT NULL, env_id INT NULL, env_name VARCHAR(40),
  status VARCHAR(10) NOT NULL DEFAULT 'working', why TEXT, edits MEDIUMTEXT, old_content MEDIUMTEXT, new_content MEDIUMTEXT,
  asked_by VARCHAR(64), at VARCHAR(32) NOT NULL, done_at VARCHAR(32), applied_by VARCHAR(64), applied_at VARCHAR(32),
  KEY ix_pf_check (precheck_id, case_id)
) ENGINE=InnoDB"""
ROUNDS = 4          # AI turns: look at the data, propose, fix a proposal that still fails
MAX_PROBES = 6      # SELECTs per turn
PREFIX = re.compile(r'^(\s*(?:\d+\.|[-*])\s+)?')


def init(con):
    con.execute(SCHEMA)


def replace_lines(content, edits):
    """The case text with each {line, step} put in place of that line (its "1. " / "- " kept); step '' removes it."""
    lines = content.replace('\r\n', '\n').split('\n')
    for e in sorted(edits, key=lambda x: -int(x['line'])):
        i = int(e['line']) - 1
        if not 0 <= i < len(lines):
            raise ValueError(f'line {e["line"]} is not in the test case')
        step = (e.get('step') or '').strip()
        if step:
            lines[i] = (PREFIX.match(lines[i]).group(1) or '') + step
        else:
            del lines[i]
    return '\n'.join(lines)


def _bridge(req):
    try:
        r = subprocess.run(['node', os.path.join(db.RUNNER_DIR, 'ai-bridge.js')], cwd=db.RUNNER_DIR, input=json.dumps(req),
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        out = json.loads(r.stdout or '{}')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, 'The AI service on this server did not answer.'
    if not out.get('ok'):
        return None, out.get('error') or 'The AI could not answer.'
    return out, None


def _rows(r):
    cols = r['columns']
    return [dict(zip(cols, row)) for row in r['rows'][:10]]


def suggest(con, env, case, problems, username=None):
    """-> {'status': 'suggested' | 'nofix' | 'error', 'why', 'edits': [{line, old, new}], 'new_content'}"""
    steps = precheck.case_steps(case['content'])
    try:
        schema = precheck.tables(env)
    except Exception as e:   # noqa: BLE001
        return {'status': 'error', 'why': f'Cannot read the database tables of {env["name"]}: {str(e)[:200]}'}
    picks = [{'target': '', 'value': '', 'css': ''}] + [
        {'target': p['step'], 'value': '', 'css': ''} for p in problems if p.get('kind') == 'pick']
    part = precheck.relevant(schema, picks, steps)
    history, usage = [], []
    try:
        for _ in range(ROUNDS):
            out, err = _bridge({'op': 'precheckfix', 'title': case['title'], 'env': env['name'],
                                'steps': [f'L{ln}: {t}' for ln, t in steps][:400], 'problems': problems,
                                'tables': part, 'allTables': sorted(schema)[:1500], 'history': history})
            if err:
                return {'status': 'error', 'why': err}
            usage.append(out)
            d = out.get('data') or {}
            if (d.get('no_fix') or '').strip() and not d.get('edits'):
                return {'status': 'nofix', 'why': d['no_fix'].strip()}
            if d.get('edits'):
                by_line = dict(steps)
                bad = [e for e in d['edits'] if int(e.get('line') or 0) not in by_line]
                if bad:
                    history.append({'note': f'Line(s) {", ".join(str(e.get("line")) for e in bad)} are not steps of the case. '
                                            'Use the L<number> of an existing step.'})
                    continue
                new = replace_lines(case['content'], d['edits'])
                again = precheck.check_case(con, env, {**case, 'content': new}, username)
                still = [i for i in again['items'] if i['level'] == 'fail']
                if not still:
                    edits = [{'line': int(e['line']), 'old': by_line[int(e['line'])], 'new': (e.get('step') or '').strip()}
                             for e in d['edits']]
                    return {'status': 'suggested', 'why': (d.get('why') or '').strip(), 'edits': edits, 'new_content': new}
                history.append({'proposal': d['edits'],
                                'still_fails': [{'line': i['line'], 'step': i['step'], 'why': i['text']} for i in still]})
                continue
            probes = [p for p in (d.get('probes') or []) if p.strip()][:MAX_PROBES]
            if not probes:
                return {'status': 'nofix', 'why': (d.get('why') or 'No change found that keeps what the test checks.').strip()}
            for sql in probes:
                try:
                    r = envs.db_query(env, sql)
                    history.append({'sql': sql, 'rows': _rows(r), 'count': r['count']})
                except envs.DbError as e:
                    history.append({'sql': sql, 'error': str(e)[:300]})
        return {'status': 'nofix', 'why': f'No change found in {ROUNDS} tries that {env["name"]} has data for. '
                                          'The data the test needs may have to be created there.'}
    finally:
        for out in usage:
            u = out.get('usage') or {}
            with con:
                db.record_ai_usage(con, 'precheck_fix', {
                    'model': u.get('model') or out.get('model'), 'calls': 1, 'in': u.get('input_tokens', 0),
                    'out': u.get('output_tokens', 0), 'cacheRead': u.get('cache_read_input_tokens', 0),
                    'cacheWrite': u.get('cache_creation_input_tokens', 0), 'via': u.get('via')},
                    username=username, project_id=case['project_id'], case_id=case['id'],
                    note=f'Suggest a fix for TC-{case["tc_no"]} on {env["name"]}')


def start(pre, case, env, username):
    """Background: the page shows "Finding data on <env>..." and refreshes until it is done. -> fix id."""
    res = pre.get('result') or {}
    mine = next((x for x in res.get('cases') or [] if x['case_id'] == case['id']), None)
    problems = [{'line': i['line'], 'step': i['step'], 'kind': i['kind'], 'why': i['text']}
                for i in (mine or {}).get('items') or [] if i['level'] == 'fail']
    c = db.connect()
    with c:
        fid = c.execute("INSERT INTO precheck_fixes(precheck_id, case_id, env_id, env_name, status, old_content, asked_by, at) "
                        "VALUES (?,?,?,?,'working',?,?,?)", (pre['id'], case['id'], env['id'], env['name'], case['content'],
                                                           username, db.now())).lastrowid
    c.close()
    case = dict(case)

    def work():
        c2 = db.connect()
        try:
            r = suggest(c2, env, case, problems, username) if problems else {'status': 'nofix', 'why': 'Nothing failed in the check.'}
        except Exception as e:   # noqa: BLE001
            r = {'status': 'error', 'why': str(e)[:300]}
        try:
            with c2:
                c2.execute('UPDATE precheck_fixes SET status=?, why=?, edits=?, new_content=?, done_at=? WHERE id=?',
                           (r['status'], r.get('why'), json.dumps(r.get('edits') or [], ensure_ascii=False),
                            r.get('new_content'), db.now(), fid))
        finally:
            c2.close()
    threading.Thread(target=work, name=f'lth-precheck-fix-{fid}', daemon=True).start()
    return fid


def of_check(con, precheck_id):
    """{case_id: newest fix row (edits parsed)} for one check."""
    out = {}
    for r in con.execute('SELECT * FROM precheck_fixes WHERE precheck_id=? ORDER BY id', (precheck_id,)).fetchall():
        d = dict(r)
        try:
            d['edits'] = json.loads(r['edits'] or '[]')
        except ValueError:
            d['edits'] = []
        out[r['case_id']] = d
    return out


def apply(con, fid, username):
    """OK: the case gets the suggested text, only when nobody changed it since. -> (ok, message, case row)."""
    f = con.execute('SELECT * FROM precheck_fixes WHERE id=?', (fid,)).fetchone()
    if not f or f['status'] != 'suggested' or not f['new_content']:
        return False, 'This suggestion cannot be applied (already applied, dismissed or not ready).', None
    case = con.execute('SELECT * FROM cases WHERE id=?', (f['case_id'],)).fetchone()
    if not case:
        return False, 'The test case was deleted.', None
    if (case['content'] or '').replace('\r\n', '\n') != (f['old_content'] or '').replace('\r\n', '\n'):
        return False, f'TC-{case["tc_no"]} was changed after the suggestion was made: press Suggest a fix again.', case
    with con:
        con.execute('UPDATE cases SET content=?, updated_by=?, updated_at=? WHERE id=?',
                    (f['new_content'], username, db.now(), case['id']))
        con.execute("UPDATE precheck_fixes SET status='applied', applied_by=?, applied_at=? WHERE id=?", (username, db.now(), fid))
    settings.write_audit(username, 'precheck_fix', 'test case', case['id'], 'ok',
                         f'TC-{case["tc_no"]}: data for {f["env_name"]} - {(f["why"] or "")[:300]}')
    return True, f'TC-{case["tc_no"]} changed. Run it to see it pass on {f["env_name"]}.', case


def dismiss(con, fid):
    with con:
        con.execute("UPDATE precheck_fixes SET status='dismissed' WHERE id=? AND status IN ('suggested','nofix','error')", (fid,))
