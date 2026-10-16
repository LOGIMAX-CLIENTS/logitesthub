"""Element check: the "(#id)" / "(.class)" hints a draft's steps use must exist in the application's code.

A wrong id only shows up minutes into a run ("element not found"). Here every hint is looked up in the code folder of
the environment (views, JS) in about a second: ids from id="..." / '#id' in JS, classes from class="..." / '.cls' in JS.
Elements a library makes at run time (Select2, jQuery UI, DataTables, daterangepicker, Bootstrap) are not in the
application's code and are accepted by name. Ids a row template builds from a prefix and a counter
(id="from_touch_'+i+'", '#from_touch_'+i, id="from_touch_<?= $i ?>", `from_touch_${i}`) are kept as prefixes, so
#from_touch_2 is found. The index is built once per folder and rebuilt when files change.
"""
import json
import os
import re
import subprocess
import threading

EXTS = ('.php', '.html', '.htm', '.js', '.tpl')
SKIP_DIRS = {'.git', 'node_modules', 'vendor', 'uploads', 'cache', 'logs', 'plugins', 'bower_components', 'dist', 'min'}
LIB_CLASS = re.compile(r'^(ui-|select2|dataTables_|daterangepicker|dt-|modal|btn|fa|form-|table|col-|row$|bootstrap|toast|'
                       r'dropdown|input-group|panel|box|nav|tab|active|in$|show$|fade|ranges|applyBtn|cancelBtn|calendar)')
LIB_ID_SUFFIX = re.compile(r'_(filter|wrapper|length|info|paginate|processing)$')   # DataTables builds these from the table id
LIB_ID = re.compile(r'^(select2-|ui-id-|DataTables_)')
_CACHE, _LOCK = {}, threading.Lock()
ID_ATTR = re.compile(r'''\bid\s*=\s*["']([A-Za-z][\w-]*)["']''')
CLASS_ATTR = re.compile(r'''\bclass\s*=\s*["']([^"']+)["']''')
JS_SEL = re.compile(r'''[#.]([A-Za-z][\w-]*)''')
ID_PREFIX = re.compile(r"""(?:\bid\s*=\s*\\?["']|["'`]#)([A-Za-z][\w-]{2,})(?:\\?["'`]\s*\+|\$\{|<\?|\{\{)""")


def _scan(root):
    ids, classes, prefixes, newest, count = set(), set(), set(), 0, 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith('.')]
        for f in files:
            if not f.lower().endswith(EXTS) or f.endswith('.min.js'):
                continue
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
                if st.st_size > 3_000_000:
                    continue
                newest, count = max(newest, st.st_mtime), count + 1
                with open(p, encoding='utf-8', errors='ignore') as fh:
                    text = fh.read()
            except OSError:
                continue
            ids.update(ID_ATTR.findall(text))
            prefixes.update(ID_PREFIX.findall(text))
            for cl in CLASS_ATTR.findall(text):
                classes.update(cl.split())
            if f.endswith('.js'):   # ids / classes the JS builds or queries ('#id', ".cls", class="..." in strings)
                for m in JS_SEL.finditer(text):
                    (ids if text[m.start()] == '#' else classes).add(m.group(1))
    return {'ids': ids, 'classes': classes, 'id_prefixes': prefixes, 'newest': newest, 'count': count}


REFRESH_S = 600   # a background rescan at most every 10 minutes
_BUILDING = set()


def _disk_path(root):
    import hashlib
    import db
    return os.path.join(db.DATA, f'element_index_{hashlib.md5(root.lower().encode()).hexdigest()[:12]}.json')


def _build(root):
    import json
    import time
    try:
        fresh = _scan(root)
        fresh['checked'] = time.time()
        with _LOCK:
            _CACHE[root] = fresh
        with open(_disk_path(root), 'w', encoding='utf-8') as f:
            json.dump({'ids': sorted(fresh['ids']), 'classes': sorted(fresh['classes']),
                       'id_prefixes': sorted(fresh['id_prefixes']), 'count': fresh['count'],
                       'checked': fresh['checked']}, f)
    finally:
        with _LOCK:
            _BUILDING.discard(root)


def warm(root):
    """Starts a background (re)build of the index for root unless one is running."""
    if not root or not os.path.isdir(root):
        return
    with _LOCK:
        if root in _BUILDING:
            return
        _BUILDING.add(root)
    threading.Thread(target=_build, args=(root,), daemon=True, name='lth-element-index').start()


def index(root):
    """{ids, classes} of the code folder: from memory, else from disk (last build), else None while a first build runs
    in the background. Stale copies are refreshed in the background, never while a user waits."""
    import json
    import time
    if not root or not os.path.isdir(root):
        return None
    with _LOCK:
        hit = _CACHE.get(root)
    if not hit and os.path.isfile(_disk_path(root)):
        try:
            with open(_disk_path(root), encoding='utf-8') as f:
                d = json.load(f)
            hit = {'ids': set(d['ids']), 'classes': set(d['classes']), 'id_prefixes': set(d.get('id_prefixes', [])),
                   'count': d.get('count'), 'checked': d.get('checked', 0)}
            with _LOCK:
                _CACHE[root] = hit
        except (OSError, ValueError, KeyError):
            hit = None
    if not hit or hit['checked'] < time.time() - REFRESH_S:
        warm(root)
    return hit


HINT = re.compile(r'\(([#.][^()]*(?:\([^()]*\)[^()]*)*)\)\s*\.?\s*$')


def hints(step_text):
    """The selector hint of a step ("... (#branch_select)." -> '#branch_select'), else None."""
    if re.match(r'^(remember|assert|set\s*up) sql\b', step_text, re.I):
        return None
    m = HINT.search(step_text.strip())
    return m.group(1).strip() if m else None


def _parts(sel):
    """'#billing_list tbody tr:first-child .btn-warning' -> [('#','billing_list'), ('.','btn-warning')]"""
    out = []
    for tok in re.findall(r'([#.])([A-Za-z][\w-]*)', re.sub(r':[\w-]+(\([^)]*\))?', ' ', sel)):
        out.append(tok)
    return out


STEPCHECK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib', 'stepcheck.js')


def runner_reads(texts):
    """How the runner itself parses each step (lib/stepcheck.js -> parseStep), or None when node is not there.
    The checks must agree with the runner: TC-75 passed this check with a selector the runner could not read."""
    try:
        r = subprocess.run(['node', STEPCHECK], input=json.dumps(texts), capture_output=True, text=True,
                           encoding='utf-8', timeout=20)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def check(draft, code_root):
    """[{n, text, selector, ok, missing, problem}] for every step with a hint: the runner must read the hint, and its
    ids / classes must be in the code (that part is skipped when there is no code to check against)."""
    if not draft:
        return []
    idx = index(code_root)
    steps = [str(st.get('text') or '') for st in draft.get('steps') or []]
    reads = runner_reads(steps) or [None] * len(steps)
    out = []
    for n, (text, rd) in enumerate(zip(steps, reads), 1):
        sel = hints(text)
        if rd and rd.get('kind') == 'badHint':
            out.append({'n': n, 'text': text[:200], 'selector': rd.get('hint') or sel, 'ok': False,
                        'missing': [f'({rd.get("hint") or sel})'],
                        'problem': f'the runner cannot read the selector ({rd.get("hint") or sel}): use ids, classes, '
                                   'tags, :first-child / :nth-child(n), > only'})
            continue
        if not idx or not sel or '{{' in sel:
            continue
        missing = []
        for kind, name in _parts(sel):
            if kind == '#':
                base = LIB_ID_SUFFIX.sub('', name)
                built = any(name.startswith(p) and re.fullmatch(r'[_-]?\d+', name[len(p):])   # prefix + row counter
                            for p in idx.get('id_prefixes', ()))
                if not (name in idx['ids'] or base in idx['ids'] or built or LIB_ID.match(name)):
                    missing.append('#' + name)
            elif not (name in idx['classes'] or LIB_CLASS.match(name)):
                missing.append('.' + name)
        out.append({'n': n, 'text': text[:200], 'selector': sel, 'ok': not missing, 'missing': missing,
                    'problem': f'uses {", ".join(missing)} which is not in the code' if missing else ''})
    return out
