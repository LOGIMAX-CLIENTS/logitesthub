"""Plain English for the SQL steps of a case (Remember SQL / Assert SQL / Set up SQL).

The case page shows this sentence instead of the query; the query is one click away ("SQL" button). The sentence is
written by AI once per distinct step text and kept in sql_explain, so a page load costs nothing; until it is there (or
when AI is not available) a sentence made from the SQL itself is shown.

Registered from app.py: init(con) at start; the page asks POST /api/sql-explain.
"""
import hashlib
import json
import os
import re
import subprocess

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS sql_explain (
  h CHAR(40) PRIMARY KEY, english VARCHAR(600) NOT NULL, created_at VARCHAR(32) NOT NULL
) ENGINE=InnoDB
"""
STEP = re.compile(r'^(remember|assert|set\s*up) sql "(.+)"(.*)$', re.I | re.S)


def init(con):
    con.execute(SCHEMA)


def is_sql(text):
    return bool(STEP.match((text or '').strip()))


def generic(text):
    """The step without this run's values (ids, bill numbers, dates), so every run of a case shares one sentence."""
    t = re.sub(r"'[^']*\d{3,}[^']*'", "'X'", text.strip())
    return re.sub(r'\s+', ' ', re.sub(r'\b\d{3,}\b', 'N', t))


def _key(text):
    return hashlib.sha1(generic(text).encode('utf-8')).hexdigest()


def _table(sql):
    m = re.search(r'\b(?:from|update|into)\s+`?(\w+)`?', sql, re.I)
    return m.group(1) if m else 'the database'


def simple(text):
    """A sentence made from the SQL itself (no AI): what kind of step, which value, which table."""
    m = STEP.match((text or '').strip())
    if not m:
        return ''
    kind, sql, rest = m.group(1).lower(), m.group(2), m.group(3)
    t = _table(sql)
    if kind == 'remember':
        name = re.search(r'as\s+(\w+)', rest, re.I)
        return f'Reads {{{{{name.group(1)}}}}} from the database ({t}) for the next steps.' if name else f'Reads a value from {t}.'
    if kind == 'assert':
        v = re.search(r"returns\s+(['\"])(.*?)\1", rest, re.I)
        if v:
            return f"Checks in the database ({t}) that the value is '{v.group(2)}'."
        return f'Checks the database ({t}) has {"no matching row" if "no rows" in rest.lower() else "a matching row"}.'
    verb = sql.strip().split()[0].upper() if sql.strip() else ''
    return {'INSERT': f'Adds test data to {t}.', 'UPDATE': f'Changes test data in {t}.',
            'DELETE': f'Removes test data from {t}.'}.get(verb, f'Changes test data in {t}.')


def cached(con, texts):
    """{text: english} for the texts already explained."""
    keys = {_key(t): t for t in texts}
    if not keys:
        return {}
    rows = con.execute(f'SELECT h, english FROM sql_explain WHERE h IN ({",".join("?" * len(keys))})',
                       tuple(keys)).fetchall()
    return {keys[r['h']]: r['english'] for r in rows}


def _ask(steps, context=''):
    try:
        r = subprocess.run(['node', os.path.join(db.RUNNER_DIR, 'ai-bridge.js')], cwd=db.RUNNER_DIR,
                           input=json.dumps({'op': 'sql', 'steps': steps, 'context': context}), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=180)
        out = json.loads(r.stdout or '{}')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, None
    if not out.get('ok'):
        return None, None
    texts = (out.get('data') or {}).get('texts') or []
    return (texts if len(texts) == len(steps) else None), out


def explain(con, texts, username=None, project_id=None, case_id=None, context=''):
    """{text: {'english', 'ai'}} for every SQL step text: cached / new AI sentences, else the simple one."""
    texts = list(dict.fromkeys(t for t in texts if is_sql(t)))
    have = cached(con, texts)
    missing = [t for t in texts if t not in have]
    if missing:
        got, out = _ask([generic(t) for t in missing[:40]], context)
        if got:
            with con:
                for t, e in zip(missing, got):
                    e = (e or '').strip()[:600]
                    if e:
                        con.execute('REPLACE INTO sql_explain(h, english, created_at) VALUES (?,?,?)', (_key(t), e, db.now()))
                        have[t] = e
                u = out.get('usage') or {}
                db.record_ai_usage(con, 'sql_explain', {
                    'model': u.get('model') or out.get('model'), 'calls': 1, 'in': u.get('input_tokens', 0),
                    'out': u.get('output_tokens', 0), 'cacheRead': u.get('cache_read_input_tokens', 0),
                    'cacheWrite': u.get('cache_creation_input_tokens', 0), 'via': u.get('via')},
                    username=username, project_id=project_id, case_id=case_id, note=f'Plain English for {len(got)} SQL step(s)')
    return {t: {'english': have.get(t) or simple(t), 'ai': t in have} for t in texts}
