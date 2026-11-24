"""Proven steps: what already worked - and what never worked - on each page of a project, from its recent runs.

The Test Assistant guessed how a page behaves (which option a dropdown picks, how a customer autocomplete selects) and
testers found out run by run: one case failed 8 times, each time one step further. Every run already records, per
step, the page it ran on and whether it passed. Given to the AI as context, the sequences that worked on a page are
reused as they are, and steps that never passed are not written again - no extra browser run, no wait.

proven_text(con, pid, talk, case_id) -> the text block for the AI context ('' when there is nothing useful).
"""
import re
import threading
from urllib.parse import urlparse

import db

MAX_RUNS = 150         # latest finished runs of the project read
MAX_PAGES = 6          # pages given to the AI
MAX_WORKED = 30        # steps that worked, per page
MAX_FAILED = 6         # steps that never passed, per page
MAX_CHARS = 12000
SKIP = re.compile(r'^(navigate to|wait\b|remember sql|assert sql|set\s*up sql)', re.I)
WORD = re.compile(r'[a-z]{3,}')
STOP = set('the and into from for with field button box key type press click assert shows show contains visible text '
           'option first wait seconds item list select selected value page this that not are was has panni pannu '
           'oru enna illa'.split())
_CACHE, _LOCK = {}, threading.Lock()   # run id -> [(page, text, status, error)]: a finished run never changes


def page_of(url):
    """'https://site/staging/admin/index.php/payment/add?x=1' -> 'payment/add' (the controller / method part)."""
    p = urlparse(str(url or '')).path
    if '/index.php/' in p:   # CodeIgniter: controller / method, then ids
        return '/'.join([x for x in p.split('/index.php/', 1)[1].split('/') if x][:2])
    return '/'.join([x for x in p.split('/') if x][-2:])


def _steps(run_id, run_dir):
    with _LOCK:
        if run_id in _CACHE:
            return _CACHE[run_id]
    out = []
    for s in db.read_run_events(run_dir or '')[1]:
        text = str(s.get('text') or '').strip()
        if not text or not s.get('url') or SKIP.match(text):
            continue
        out.append((page_of(s['url']), text, s.get('status'), str(s.get('error') or '')))
    with _LOCK:
        if len(_CACHE) > 2000:
            _CACHE.clear()
        _CACHE[run_id] = out
    return out


def collect(con, pid):
    """{page: {'worked': {text: n}, 'failed': {text: error}, 'last': run id}} from the project's latest runs."""
    rows = con.execute("SELECT r.id, r.run_dir, r.case_id FROM runs r JOIN cases c ON c.id=r.case_id "
                       "WHERE c.project_id=? AND r.status IN ('PASSED','FAILED') AND r.run_dir IS NOT NULL "
                       "ORDER BY r.id DESC LIMIT ?", (pid, MAX_RUNS)).fetchall()
    pages = {}
    for r in reversed(rows):   # oldest first: the order of steps on a page stays the order a user does them
        for page, text, status, error in _steps(r['id'], r['run_dir']):
            if not page:
                continue
            pg = pages.setdefault(page, {'worked': {}, 'failed': {}, 'last': 0, 'cases': set()})
            pg['last'] = r['id']
            pg['cases'].add(r['case_id'])
            if status == 'passed':
                pg['worked'][text] = pg['worked'].get(text, 0) + 1
            elif status == 'failed':
                pg['failed'][text] = error
    for pg in pages.values():   # a step that passed in some run is not "never worked"
        pg['failed'] = {t: e for t, e in pg['failed'].items() if t not in pg['worked']}
    return pages


def proven_text(con, pid, talk='', case_id=None):
    try:
        pages = collect(con, pid)
    except Exception:   # never block the chat on this
        return ''
    if not pages:
        return ''
    words = set(WORD.findall((talk or '').lower())) - STOP

    def score(item):
        # testers name the screen by what is on it ("digi gold scheme join"), not by its path (account/add): match the
        # path's words, and the field / option words of the steps run there
        page, pg = item
        own = 10 if case_id and case_id in pg['cases'] else 0   # pages this chat's own test ran on come first
        path_hits = len(set(WORD.findall(page.lower().replace('_', ' '))) & words)
        vocab = set(WORD.findall(' '.join(list(pg['worked']) + list(pg['failed'])).lower())) - STOP
        return (own + 3 * path_hits + min(5, len(vocab & words)), pg['last'])
    ranked = [x for x in sorted(pages.items(), key=score, reverse=True) if score(x)[0] > 0][:MAX_PAGES]
    if not ranked:
        return ''
    out = ['PROVEN STEPS - from this project\'s latest runs on the real pages (values are examples from those runs; '
           'use {{variables}} for them):']
    for page, pg in ranked:
        worked = list(pg['worked'])[:MAX_WORKED]
        if not worked and not pg['failed']:
            continue
        out.append(f'Page {page}:')
        if worked:
            out.append('  worked (reuse these exact steps for the same action):')
            out += [f'   - {t[:220]}' for t in worked]
        if pg['failed']:
            out.append('  never passed (do not write these again unchanged):')
            out += [f'   - {t[:220]}  -> {e[:160]}' for t, e in list(pg['failed'].items())[-MAX_FAILED:]]
    text = '\n'.join(out)
    return text[:MAX_CHARS] if len(out) > 1 else ''
