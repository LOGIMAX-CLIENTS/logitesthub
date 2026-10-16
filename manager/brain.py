"""The application's own knowledge brain (knowledge_brain/ inside the app repo, e.g. C:\\xampp\\htdocs\\wt-prod\\knowledge_brain).

Each module has a folder (Billing, Lot, Tagging ...) with MODULE_BRAIN, FUNCTIONAL_BRAIN, PERMISSION_MAP,
SETTINGS_REGISTER, SCREENS, BUSINESS_RULES ...; _SYSTEM/FILE_MODULE_MAP.md maps code files to modules and
_SYSTEM/FLOW_CHECKLISTS/ has one checklist per flow. Every doc's front matter has source_ref = the commit it was
measured at.

For a test this module finds the brain of the code being tested, picks the modules / flows the user talks about, and
says whether the brain is current: code files of that module changed after source_ref (git), or not committed yet
(a developer working on a new feature). Then the code wins where the two disagree.
"""
import os
import re
import subprocess

READ_ORDER = ['FUNCTIONAL_BRAIN.md', 'PERMISSION_MAP.md', 'SETTINGS_REGISTER.md', 'SCREENS.md', 'BUSINESS_RULES.md',
              'FIELD_DICTIONARY.md', 'MODULE_BRAIN.md']
# everyday words -> the module / flow they mean (score boost); the folder names alone miss these
ALIASES = {'sale': ['Billing'], 'sales': ['Billing'], 'bill': ['Billing'], 'invoice': ['Billing'], 'billing': ['Billing'],
           'cancel': ['_cancel_master'], 'tag': ['Tagging'], 'tagging': ['Tagging'], 'lot': ['Lot', 'lot_inward'],
           'estimation': ['Estimation', 'estimation_billing'], 'chit': ['scheme', 'payment'], 'scheme': ['scheme'],
           'installment': ['payment'], 'instalment': ['payment'], 'transfer': ['Branch Transfer', 'branch_transfer'],
           'grn': ['GRN'], 'karigar': ['Smith_Metal_Issue', 'Purchase_Order'], 'order': ['customer_order', 'Purchase_Order']}
STOP = {'the', 'and', 'for', 'pannu', 'panni', 'check', 'test', 'oru', 'la', 'ku', 'ah', 'list', 'page', 'screen', 'add', 'new'}


def find(code_root):
    """knowledge_brain/ in the code folder (or its parent repo), else None."""
    d = code_root
    for _ in range(3):
        if d and os.path.isdir(os.path.join(d, 'knowledge_brain')):
            return os.path.join(d, 'knowledge_brain')
        d = os.path.dirname(d) if d else None
    return None


def modules(brain):
    return sorted(x for x in os.listdir(brain) if os.path.isdir(os.path.join(brain, x)) and not x.startswith('_'))


def flows(brain):
    d = os.path.join(brain, '_SYSTEM', 'FLOW_CHECKLISTS')
    return sorted(x[:-3] for x in os.listdir(d) if x.endswith('.md')) if os.path.isdir(d) else []


def _words(s):
    return {w for w in re.findall(r'[a-z]{3,}', s.lower().replace('_', ' ')) if w not in STOP}


def match(brain, text, limit=2):
    """The modules and flow checklists whose names share words with text, best first."""
    want = _words(text)
    if not want:
        return [], []
    boost = {}
    for w in want:
        for target in ALIASES.get(w, []):
            boost[target] = boost.get(target, 0) + 2
    def score(name):
        ws = _words(name)
        return boost.get(name, 0) + len(ws & want) + sum(0.5 for w in ws for v in want if len(w) > 3 and (w.startswith(v) or v.startswith(w)) and w != v)
    mods = sorted(((score(m), m) for m in modules(brain)), reverse=True)
    fl = sorted(((score(f), f) for f in flows(brain)), reverse=True)
    return [m for s, m in mods if s > 0][:limit], [f for s, f in fl if s > 0][:limit]


def front(path):
    """{last_updated, source_ref, ...} from a brain doc's front matter."""
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            head = f.read(4000)
    except OSError:
        return {}
    m = re.match(r'---\s*\n(.*?)\n---', head, re.S)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        k, _, v = line.partition(':')
        if k.strip() and v.strip():
            out[k.strip()] = v.split('#')[0].strip()
    return out


def owned_files(brain, module):
    """Code files FILE_MODULE_MAP gives to module (owner)."""
    path = os.path.join(brain, '_SYSTEM', 'FILE_MODULE_MAP.md')
    out = []
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                cells = [c.strip() for c in line.split('|')]
                if len(cells) > 3 and cells[1].startswith('`'):
                    if module.lower() in [o.strip().lower() for o in cells[2].split(',')]:
                        out.append(cells[1].strip('`'))
    except OSError:
        pass
    return out


def _git(repo, *args):
    try:
        r = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=20)
        return r.stdout if r.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def staleness(brain, module):
    """{source_ref, last_updated, changed_since: [...], uncommitted: [...]} for the module's brain vs its code."""
    repo = os.path.dirname(brain)
    meta = front(os.path.join(brain, module, 'PERMISSION_MAP.md')) or front(os.path.join(brain, module, 'MODULE_BRAIN.md'))
    ref = meta.get('source_ref', '')
    files = owned_files(brain, module)
    out = {'source_ref': ref, 'last_updated': meta.get('last_updated', ''), 'changed_since': [], 'uncommitted': []}
    if not files or not os.path.isdir(os.path.join(repo, '.git')):
        return out
    if re.fullmatch(r'[0-9a-f]{7,40}', ref or ''):
        out['changed_since'] = [x for x in _git(repo, 'diff', '--name-only', f'{ref}..HEAD', '--', *files).splitlines() if x]
    out['uncommitted'] = [x[3:] for x in _git(repo, 'status', '--porcelain', '--', *files).splitlines() if len(x) > 3]
    return out


def context(code_root, text):
    """Text for the assistant: which brain docs to read for this conversation, and whether they are current."""
    brain = find(code_root)
    if not brain:
        return ''
    mods, fls = match(brain, text)
    rel = lambda p: os.path.relpath(p, os.path.dirname(brain)).replace('\\', '/')
    lines = [f'KNOWLEDGE BRAIN of the code under test: {rel(brain)}/ (step 1 of the order; read it before the code).']
    if not mods and not fls:
        lines.append(f'- No module matched the conversation. Modules: {", ".join(modules(brain)[:60])}. '
                     'Pick the right one and read its files below, or say no brain covers this.')
    for m in mods:
        files = [f for f in READ_ORDER if os.path.isfile(os.path.join(brain, m, f))]
        lines.append(f'- Module {m}: read {", ".join(rel(os.path.join(brain, m, f)) for f in files)} '
                     '(PERMISSION_MAP + SETTINGS_REGISTER for step 2, SCREENS for step 4).')
        st = staleness(brain, m)
        if st['changed_since'] or st['uncommitted']:
            lines.append(f'  ! The {m} brain may be OUT OF DATE: it was measured at {st["source_ref"] or "?"} '
                         f'({st["last_updated"] or "?"}); since then {len(st["changed_since"])} of its code file(s) changed'
                         + (f' and {len(st["uncommitted"])} have changes not committed yet (a feature in progress)'
                            if st['uncommitted'] else '')
                         + ': ' + ', '.join((st['uncommitted'] + st['changed_since'])[:8])
                         + '. Read those files: where the brain and the code disagree the CODE is right; say in "reply" '
                           'in one line which brain doc looks out of date.')
        else:
            lines.append(f'  Brain is current for its code ({st["source_ref"] or "no source_ref"}, {st["last_updated"] or "?"}).')
    for f in fls:
        lines.append(f'- Flow checklist: {rel(os.path.join(brain, "_SYSTEM", "FLOW_CHECKLISTS", f + ".md"))}')
    return '\n'.join(lines)
