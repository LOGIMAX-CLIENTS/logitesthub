"""knowledge_brain: flow documents per project (one Markdown file per module flow), written from the application's
code and kept up to date as tests teach us more. The Test Assistant reads the docs that match the conversation, so it
knows a flow's screens, rules, branches and tables before it writes a test - and asks the user to pick a branch
instead of guessing.

Layout:  <runner folder>/knowledge_brain/<project folder>/<flow>.md   (project folder = project name, lower-case,
spaces -> '-'; e.g. "Retail" -> knowledge_brain/retail/). A doc starts with "# Title" and a "keywords:" line.
"""
import os
import re

import db

ROOT = os.path.join(db.RUNNER_DIR, 'lth_brain')   # LogiTestHub's own notes per project (settings.json); the app's brain is knowledge_brain/ in its repo
MAX_CHARS = 16000      # flow text sent to the AI per message (the best-matching docs first)
WORD = re.compile(r'[a-z0-9]{3,}')


def project_dir(project_name):
    return os.path.join(ROOT, re.sub(r'[^a-z0-9]+', '-', (project_name or '').lower()).strip('-') or 'default')


def docs(project_name):
    """[{name, title, keywords, text}] for every flow doc of the project (README.md excluded)."""
    folder = project_dir(project_name)
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.md') or fn.lower() == 'readme.md':
            continue
        try:
            with open(os.path.join(folder, fn), encoding='utf-8') as f:
                text = f.read()
        except OSError:
            continue
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith('# ')), fn[:-3])
        kw = re.search(r'^keywords:\s*(.+)$', text, re.M | re.I)
        keywords = {k.strip().lower() for k in (kw.group(1) if kw else '').split(',') if k.strip()}
        out.append({'name': fn, 'title': title, 'keywords': keywords, 'text': text})
    return out


def for_conversation(project_name, conversation_text):
    """Prompt text: the list of flow docs + the full text of the ones the conversation is about ('' when none)."""
    all_docs = docs(project_name)
    if not all_docs:
        return ''
    said = (conversation_text or '').lower()
    words = set(WORD.findall(said))

    def score(d):
        hits = sum(3 for k in d['keywords'] if (k in said if ' ' in k else k in words))
        return hits + len(words & set(WORD.findall(d['title'].lower())))

    ranked = sorted(((score(d), d) for d in all_docs), key=lambda x: -x[0])
    lines = ['Flow documents (knowledge_brain) for this project: ' + '; '.join(d['title'] for d in all_docs) + '.']
    used = 0
    for s, d in ranked:
        if s <= 0 or used + len(d['text']) > MAX_CHARS:
            continue
        lines.append(f'--- FLOW DOC: {d["title"]} ({d["name"]}) ---\n{d["text"].strip()}\n--- END FLOW DOC ---')
        used += len(d['text'])
    if used:
        lines.append('Use the flow docs above: follow their screens, rules and tables. Where the doc lists branches '
                     '(payment modes, scheme types, with / without advance ...) that the user did not choose, ask them '
                     'with "choices" before writing the draft. Trust the code over the doc if they disagree, and say so.')
    return '\n'.join(lines)


# ---------------------------------------------------------------- live settings (knowledge_brain/<project>/settings.json)

def settings_text(project_name, env, max_chars=14000):
    """The settings / permissions the project's settings.json lists, read now from env's database (read-only), as text
    for the assistant: they decide which options a screen shows and whether the action is allowed at all."""
    import json
    import time
    import envs
    d = project_dir(project_name)
    path = os.path.join(d, 'settings.json') if d else None
    if not envs.db_configured(env):
        return ''
    key = (path or project_name, env['id'], int(time.time() // 120))   # cached 2 minutes: settings change rarely
    if key in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[key]
    sources = []
    if path and os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                sources = json.load(f).get('sources') or []
        except (OSError, ValueError):
            sources = []
    if not sources:   # a project without settings.json: find its settings / permission tables by name
        sources = discover_sources(env)
    if not sources:
        return ''
    lines = [f'LIVE SETTINGS on "{env["name"]}" (read just now from its database; step 2 of READINESS uses these, '
             'do not guess them):']
    for s in sources:
        sql = str(s.get('sql') or '').replace('{{username}}', str(env['username'] or '').replace("'", ''))
        try:
            r = envs.db_query(env, sql)
        except envs.DbError as e:
            lines.append(f'- {s.get("label")}: could not read ({e})')
            continue
        rows, cols = r.get('rows') or [], r.get('columns') or []
        if not rows:
            lines.append(f'- {s.get("label")}: no row')
        elif s.get('pairs'):
            lines.append(f'- {s.get("label")}: ' + '; '.join(f'{a}={str(b)[:40]}' for a, b in rows))
        elif len(cols) == 1 and len(rows) == 1:
            lines.append(f'- {s.get("label")}: {rows[0][0]}')
        else:
            lines.append(f'- {s.get("label")}: ' + '; '.join(f'{c}={str(v)[:40]}' for c, v in zip(cols, rows[0])))
    text = '\n'.join(lines)[:max_chars]
    _SETTINGS_CACHE.clear()
    _SETTINGS_CACHE[key] = text
    return text


_SETTINGS_CACHE = {}


SETTINGS_TABLE = re.compile(r'(setting|config|permission|privilege|role|profile|preference|option)s?$|^(settings?|config)', re.I)


def discover_sources(env, max_tables=8):
    """Settings / permission tables of an app with no settings.json, found by name in its database (read-only):
    name/value tables are read as pairs, one-row tables whole. Small tables only (a settings table is not big)."""
    import envs
    try:
        r = envs.db_query(env, "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                               "AND TABLE_ROWS BETWEEN 1 AND 400 ORDER BY TABLE_NAME")
    except envs.DbError:
        return []
    out = []
    for name, rows in r.get('rows') or []:
        if not SETTINGS_TABLE.search(str(name)) or not re.fullmatch(r'\w+', str(name)):
            continue
        try:
            cols = [c.lower() for c in envs.db_query(env, f'SELECT * FROM `{name}` LIMIT 1').get('columns') or []]
        except envs.DbError:
            continue
        key = next((c for c in ('name', 'key', 'setting', 'setting_name', 'option_name', 'code') if c in cols), None)
        val = next((c for c in ('value', 'val', 'setting_value', 'option_value') if c in cols), None)
        if key and val:
            out.append({'label': f'{name} (name = value, found by name)', 'sql': f'SELECT `{key}`, `{val}` FROM `{name}` ORDER BY 1',
                        'pairs': True})
        elif int(rows or 0) == 1:
            out.append({'label': f'{name} (one row, found by name)', 'sql': f'SELECT * FROM `{name}` LIMIT 1'})
        if len(out) >= max_tables:
            break
    return out
