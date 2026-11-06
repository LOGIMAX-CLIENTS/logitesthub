"""Pre-run check of a Test Run on one environment: will its test cases find their data there?

Cases are often written on a developer's PC, against that PC's database. On Dev / QA a value the case picks may not
exist ("ZT CARAT" uom made only on one PC, a purity whose rate is inactive), and the run then fails at that step.
Before a run (the "Check on <env>" button, and every Scheduled Run just before it starts) each case is checked,
read-only, on the environment's database:

1. Remember SQL steps that pick existing test data run as they are ({{name}} filled from the steps before them):
   no row means the case will stop there.
2. Fixed values picked in dropdowns (Select 'ZT CARAT' from the UOM dropdown): AI writes one SELECT per value that
   must already exist in the app's masters (not values the case creates itself, not fixed screen options such as
   '+'), and the SELECT runs here. The SELECTs are kept per case text + database, so AI runs again only when the case
   changes.

Nothing is changed: the database session is READ ONLY (envs.db_query) and no screen is opened. The result is a
guess before the run, not a verdict: the run itself still decides PASS / FAIL, and auto-heal still handles screens.
"""
import hashlib
import json
import os
import re
import subprocess
import threading

import db
import envs

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS prechecks (
  id INT AUTO_INCREMENT PRIMARY KEY, test_run_id INT NOT NULL, env_id INT NULL, env_name VARCHAR(40), exec_id INT NULL,
  started_by VARCHAR(64), at VARCHAR(32) NOT NULL, done_at VARCHAR(32) NULL, status VARCHAR(10) NOT NULL DEFAULT 'running',
  result MEDIUMTEXT, KEY ix_prechecks_run (test_run_id, id), KEY ix_prechecks_exec (exec_id)
) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS precheck_ai (
  h CHAR(40) PRIMARY KEY, checks MEDIUMTEXT NOT NULL, at VARCHAR(32) NOT NULL
) ENGINE=InnoDB""",
)
VERSION = '1'   # part of the AI cache key: bump when the prompt changes

STEP_PREFIX = re.compile(r'^\s*(?:\d+\.|[-*])\s+')
REMEMBER = re.compile(r'^remember sql "(.+)" as ([A-Za-z_]\w*)\.?$', re.I)
SETUP = re.compile(r'^set ?up sql "(.+)"\.?$', re.I)
ASSERT = re.compile(r'^assert sql "(.+)" ((?:returns|contains) .+)$', re.I)
SELECT_STEP = re.compile(r"^select (['\"])(.+?)\1 from the (.+?) dropdown", re.I)
# a click that saves / creates something: test data read after it may be what the test made
SAVES = re.compile(r'^click\b.*\b(save|submit|update|create|add(?!\s*(?:a\s+|new\s+)?row)|approve|confirm|generate|finish)\b', re.I)
VAR = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')
WORD = re.compile(r'[a-z][a-z0-9]{2,}')


def init(con):
    for s in SCHEMA:
        con.execute(s)


# ---------------------------------------------------------------- the steps of a case

def case_steps(content):
    """[(line_no, text)] of the case's own steps: no front matter, headings, comments or blank lines."""
    out, front = [], False
    for i, raw in enumerate((content or '').replace('\r\n', '\n').split('\n'), 1):
        line = raw.strip()
        if i == 1 and line == '---':
            front = True
            continue
        if front:
            front = line != '---'
            continue
        if not line or line.startswith('#') or line.startswith('>') or line.startswith('<!--'):
            continue
        out.append((i, STEP_PREFIX.sub('', line, count=1).strip()))
    return out


def fill(text, values):
    return VAR.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def env_values(env):
    """{{base_url}}, {{branch}} ... as the runner has them (never the password: SQL does not need it)."""
    vals = {k: str(v.get('value', '') if isinstance(v, dict) else v) for k, v in envs._base_values().items()}
    vals['base_url'] = env['base_url'] or ''
    for k in ('username', 'branch'):
        if env[k]:
            vals[k] = env[k]
    return {k: v for k, v in vals.items() if k != 'password'}


def short(sql, n=160):
    s = re.sub(r'\s+', ' ', sql).strip()
    return s if len(s) <= n else s[:n - 1] + '…'


# ---------------------------------------------------------------- 1. Remember SQL

def check_sql(env, steps, values):
    """Runs the Remember SQL steps in order. -> (items, remembered values)."""
    items, after_save, after_setup = [], None, None
    for line, text in steps:
        if SAVES.match(text) and not re.search(r'add.?row', text, re.I) and not after_save:
            after_save = text
        if SETUP.match(text) and not after_setup:
            after_setup = text
        a = ASSERT.match(text)
        if a and not (after_save or after_setup):   # a check of the data the test starts from (later ones check its result)
            items.append(check_assert(env, line, text, fill(a.group(1), values), a.group(2)))
            continue
        m = REMEMBER.match(text)
        if not m:
            continue
        sql, name = fill(m.group(1), values), m.group(2)
        later = after_save or after_setup   # may read what the test itself saves / sets up: an empty answer is fine then
        left = VAR.findall(sql)
        if left:
            items.append({'line': line, 'step': text, 'kind': 'sql', 'level': 'skip',
                          'text': f'Not checked: it needs {{{{{left[0]}}}}}, which only the run itself gives (a screen step, '
                                  f'or an earlier SQL step that did not answer here).'})
            continue
        try:
            r = envs.db_query(env, sql)
        except envs.DbError as e:
            items.append({'line': line, 'step': text, 'kind': 'sql', 'level': 'fail',
                          'text': f'This query fails on {env["name"]}: {str(e)[:300]}'})
            continue
        if r['count'] and r['value'] not in (None, ''):
            values[name] = r['value']
            items.append({'line': line, 'step': text, 'kind': 'sql', 'level': 'ok',
                          'text': f'Finds {{{{{name}}}}} = {str(r["value"])[:60]}'})
        elif later:
            items.append({'line': line, 'step': text, 'kind': 'sql', 'level': 'skip',
                          'text': f'No row yet: fine if it reads what the test saves first ("{short(later, 80)}").'})
        else:
            items.append({'line': line, 'step': text, 'kind': 'sql', 'level': 'fail',
                          'text': f'No matching row on {env["name"]}: the case will stop here. The test data it needs is not there.'})
    return items, values


def check_assert(env, line, text, sql, tail):
    """An Assert SQL step before the test changes anything: is the starting data as the case expects?"""
    item = {'line': line, 'step': text, 'kind': 'sql'}
    if VAR.search(sql):
        return {**item, 'level': 'skip', 'text': 'Not checked: it uses a value only the run itself gives.'}
    try:
        r = envs.db_query(env, sql)
    except envs.DbError as e:
        return {**item, 'level': 'fail', 'text': f'This query fails on {env["name"]}: {str(e)[:300]}'}
    t = tail.strip().rstrip('.').strip()
    v = re.match(r'^(returns|contains)\s+([\'"])(.*)\2$', t, re.I)
    if v:
        got = '' if r['value'] is None else str(r['value'])
        ok = (v.group(3) in got) if v.group(1).lower() == 'contains' else got.strip() == v.group(3).strip()
        return {**item, 'level': 'ok' if ok else 'fail',
                'text': f"Gives '{got}' as expected." if ok else
                        f"Gives '{got}' on {env['name']}, the case expects '{v.group(3)}': the data it starts from is different there."}
    want_rows = 'no rows' not in t.lower()
    ok = bool(r['count']) == want_rows
    return {**item, 'level': 'ok' if ok else 'fail',
            'text': 'As expected.' if ok else (f'No row on {env["name"]}: the data the case starts from is not there.' if want_rows
                                               else f'{env["name"]} already has a matching row: the case expects none.')}


# ---------------------------------------------------------------- 2. fixed dropdown values

def tables(env):
    """{table: [columns]} of the environment's database (information_schema, read-only)."""
    import pymysql
    raw = pymysql.connect(host=env['db_host'], port=int(env['db_port'] or 3306), user=env['db_user'],
                          password=envs.decrypt(env['db_password_enc']), database=env['db_name'], charset='utf8mb4',
                          connect_timeout=6, read_timeout=30, autocommit=True)
    try:
        with raw.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION READ ONLY')
            cur.execute('SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                        'ORDER BY TABLE_NAME, ORDINAL_POSITION')
            out = {}
            for t, col in cur.fetchall():
                out.setdefault(t, []).append(col)
            return out
    finally:
        raw.close()


def relevant(schema, picks, steps):
    """The tables most likely to hold the picked values: words of the dropdown / value / element in the table name,
    and the tables the case's own SQL reads. At most 30, with their columns."""
    words = set()
    for p in picks:
        words |= set(WORD.findall(f"{p['target']} {p['value']} {p.get('css') or ''}".lower()))
    words -= {'the', 'select', 'dropdown', 'field', 'modal', 'body', 'row', 'child', 'nth', 'add', 'edit', 'list', 'form'}
    named = set()
    for _, t in steps:
        named |= {m.lower() for m in re.findall(r'\b(?:from|join)\s+`?(\w+)`?', t, re.I)}
    scored = []
    for t in schema:
        tl = t.lower()
        s = (5 if tl in named else 0) + sum(1 for w in words if w in tl) + sum(
            0.3 for w in words for col in schema[t] if w in col.lower())
        if s >= 1:
            scored.append((s, t))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return {t: schema[t] for _, t in scored[:30]}


def _ask(title, steps, picks, schema_part, all_tables):
    req = {'op': 'precheck', 'title': title, 'steps': [f'L{ln}: {t}' for ln, t in steps][:400],
           'picks': picks, 'tables': schema_part, 'allTables': all_tables[:1500]}
    try:
        r = subprocess.run(['node', os.path.join(db.RUNNER_DIR, 'ai-bridge.js')], cwd=db.RUNNER_DIR, input=json.dumps(req),
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
        out = json.loads(r.stdout or '{}')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, None, 'The AI service on this server did not answer.'
    if not out.get('ok'):
        return None, None, out.get('error') or 'The AI could not answer.'
    return (out.get('data') or {}).get('checks') or [], out, None


def check_picks(con, env, case, steps, values, username=None, schema_cache=None):
    """Fixed values picked in dropdowns that must already exist on the environment. -> (items, note)."""
    picks = []
    for line, text in steps:
        m = SELECT_STEP.match(text)
        if m and not VAR.search(m.group(2)):
            css = re.search(r'\(([#.][^()]*)\)\s*\.?$', text)
            picks.append({'line': line, 'value': m.group(2), 'target': m.group(3), 'css': css.group(1) if css else ''})
    if not picks:
        return [], None
    h = hashlib.sha1(f"{VERSION}|{env['db_host']}|{env['db_name']}|{case['content']}".encode('utf-8')).hexdigest()
    row = con.execute('SELECT checks FROM precheck_ai WHERE h=?', (h,)).fetchone()
    if row:
        checks = json.loads(row['checks'])
    else:
        try:
            if schema_cache is not None and 'schema' not in schema_cache:
                schema_cache['schema'] = tables(env)
            schema = schema_cache['schema'] if schema_cache is not None else tables(env)
        except Exception as e:   # noqa: BLE001 - any connection problem: say it, do not stop the check
            return [], f'Dropdown values not checked: cannot read the database tables ({str(e)[:160]}).'
        checks, out, err = _ask(case['title'], steps, picks, relevant(schema, picks, steps), sorted(schema))
        if err:
            return [], f'Dropdown values not checked: {err}'
        with con:
            con.execute('REPLACE INTO precheck_ai(h, checks, at) VALUES (?,?,?)', (h, json.dumps(checks), db.now()))
            u = out.get('usage') or {}
            db.record_ai_usage(con, 'precheck', {
                'model': u.get('model') or out.get('model'), 'calls': 1, 'in': u.get('input_tokens', 0),
                'out': u.get('output_tokens', 0), 'cacheRead': u.get('cache_read_input_tokens', 0),
                'cacheWrite': u.get('cache_creation_input_tokens', 0), 'via': u.get('via')},
                username=username, project_id=case['project_id'], case_id=case['id'],
                note=f'Pre-run check of {len(picks)} dropdown value(s) on {env["name"]}')
    items = []
    by_line = {p['line']: p for p in picks}
    for ch in checks:
        p = by_line.get(ch.get('line'))
        if not p or not (ch.get('sql') or '').strip():
            continue
        sql = fill(ch['sql'], values)
        what = (ch.get('what') or f"'{p['value']}'").strip()[:160]
        step = next((t for ln, t in steps if ln == p['line']), '')
        try:
            r = envs.db_query(env, sql)
        except envs.DbError as e:
            items.append({'line': p['line'], 'step': step, 'kind': 'pick', 'level': 'skip', 'sql': sql,
                          'text': f'Could not check {what}: {str(e)[:200]}'})
            continue
        if r['count']:
            items.append({'line': p['line'], 'step': step, 'kind': 'pick', 'level': 'ok', 'sql': sql,
                          'text': f'{what} is there.'})
        else:
            items.append({'line': p['line'], 'step': step, 'kind': 'pick', 'level': 'fail', 'sql': sql,
                          'text': f"{what} is not on {env['name']} (or not active): the dropdown will not have "
                                  f"'{p['value']}'. Change the step to a value {env['name']} has, or add it there."})
    return items, None


# ---------------------------------------------------------------- one case, a whole Test Run

def check_case(con, env, case, username=None, schema_cache=None):
    steps = case_steps(case['content'])
    out = {'case_id': case['id'], 'tc': case['tc_no'], 'title': case['title'], 'items': [], 'notes': []}
    uses_sql = any(REMEMBER.match(t) or SETUP.match(t) or t.lower().startswith('assert sql') for _, t in steps)
    if not envs.db_configured(env):
        if uses_sql:
            out['items'].append({'line': 0, 'step': '', 'kind': 'env', 'level': 'fail',
                                 'text': f'The case checks the database, but "{env["name"]}" has no database set.'})
        else:
            out['notes'].append('Not checked: this environment has no database set.')
        return finish(out)
    values = env_values(env)
    items, values = check_sql(env, steps, values)
    out['items'] += items
    picks, note = check_picks(con, env, case, steps, values, username, schema_cache)
    out['items'] += picks
    if note:
        out['notes'].append(note)
    out['items'].sort(key=lambda x: x['line'])
    return finish(out)


def finish(out):
    lv = [i['level'] for i in out['items']]
    out['level'] = 'fail' if 'fail' in lv else 'skip' if out['notes'] else 'ok'   # a part not checked is not a ✓
    return out


def reach(env):
    """'' when the environment's database answers, else why not."""
    try:
        envs.db_query(env, 'SELECT 1')
        return ''
    except envs.DbError as e:
        return f'Cannot check: the database of {env["name"]} did not answer ({str(e)[:200]}).'


def run(con, tr_id, cases, env, username=None, exec_id=None, pid=None):
    """Checks every case; saves the result in prechecks (id = pid, made by start()). -> the result dict."""
    res = {'env': env['name'] if env else None, 'cases': []}
    if not env:
        res['error'] = 'The Test Run has no environment.'
    elif envs.db_configured(env) and (down := reach(env)):
        res['error'] = down   # one message for the environment, not every case "failing" for it
    else:
        cache = {}
        for c in cases:
            try:
                res['cases'].append(check_case(con, env, c, username, cache))
            except Exception as e:   # noqa: BLE001 - one case's problem must not stop the others
                res['cases'].append(finish({'case_id': c['id'], 'tc': c['tc_no'], 'title': c['title'], 'items': [],
                                            'notes': [f'Not checked: {str(e)[:200]}']}))
    res['fail'] = sum(1 for c in res['cases'] if c['level'] == 'fail')
    res['partial'] = sum(1 for c in res['cases'] if c['level'] == 'skip')
    with con:
        if pid:
            con.execute("UPDATE prechecks SET status='done', done_at=?, result=?, exec_id=COALESCE(?, exec_id) WHERE id=?",
                        (db.now(), json.dumps(res), exec_id, pid))
        else:
            con.execute("INSERT INTO prechecks(test_run_id, env_id, env_name, exec_id, started_by, at, done_at, status, result) "
                        "VALUES (?,?,?,?,?,?,?,'done',?)", (tr_id, env['id'] if env else None, res['env'], exec_id,
                                                           username, db.now(), db.now(), json.dumps(res)))
    return res


def start(tr_id, cases, env, username):
    """The "Check" button: runs in the background, the page shows it when done. -> precheck id."""
    c = db.connect()
    with c:
        pid = c.execute("INSERT INTO prechecks(test_run_id, env_id, env_name, started_by, at, status) VALUES (?,?,?,?,?,'running')",
                        (tr_id, env['id'] if env else None, env['name'] if env else None, username, db.now())).lastrowid
    cases = [dict(x) for x in cases]

    def work():
        c2 = db.connect()
        try:
            run(c2, tr_id, cases, env, username, pid=pid)
        except Exception as e:   # noqa: BLE001
            with c2:
                c2.execute("UPDATE prechecks SET status='done', done_at=?, result=? WHERE id=?",
                           (db.now(), json.dumps({'error': str(e)[:300], 'cases': []}), pid))
        finally:
            c2.close()
    threading.Thread(target=work, name=f'lth-precheck-{pid}', daemon=True).start()
    c.close()
    return pid


def load(con, row):
    if not row:
        return None
    d = dict(row)
    try:
        d['result'] = json.loads(row['result']) if row['result'] else None
    except ValueError:
        d['result'] = None
    return d


def latest(con, tr_id):
    """The newest check made with the Check button (a schedule's check belongs to its run)."""
    return load(con, con.execute('SELECT * FROM prechecks WHERE test_run_id=? AND exec_id IS NULL ORDER BY id DESC LIMIT 1',
                                 (tr_id,)).fetchone())


def for_exec(con, exec_id):
    return load(con, con.execute('SELECT * FROM prechecks WHERE exec_id=? ORDER BY id DESC LIMIT 1', (exec_id,)).fetchone())
