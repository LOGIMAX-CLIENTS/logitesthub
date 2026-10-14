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

ROOT = os.path.join(db.RUNNER_DIR, 'knowledge_brain')
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
