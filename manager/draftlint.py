"""Draft lint: step shapes that keep failing, caught in the draft before anyone runs it.

The Assistant is told not to assert a hidden field or a dropdown's value against an id it guessed with SQL - the page
picks its own option (TC-24: #scheme 192 vs 53, TC-25: #scheme_account 16364 vs 15647) - and still wrote such steps.
A rule in a prompt is a request; this check is code: the step goes back to the AI with the fix, like a wrong element
id does (assistant.py auto-heal), and if it survives, the tester sees a warning on the draft.

check(draft) -> [{'n', 'text', 'problem'}]
"""
import re

ASSERT_VALUE = re.compile(r"^assert (?:that )?(?:the )?(?P<label>.+?) \((?P<css>#[\w-]+)\) "
                          r"(?:shows|equals|has the value|has value) (?P<q>['\"])(?P<val>.*)(?P=q)\.?$", re.I)
REMEMBER = re.compile(r'^remember sql ".+" as (\w+)\.?$', re.I)
SELECT2 = re.compile(r'#select2-([\w-]+?)-container')
VAR = re.compile(r'^\{\{\s*(\w+)\s*\}\}$')


def check(draft):
    steps = [str(s.get('text') or '').strip() for s in (draft or {}).get('steps') or []]
    from_sql = {m.group(1) for m in map(REMEMBER.match, steps) if m}
    select2 = {x for t in steps for x in SELECT2.findall(t)}   # #select2-scheme-container => #scheme is a Select2 dropdown
    out = []
    for n, text in enumerate(steps, 1):
        m = ASSERT_VALUE.match(text)
        if not m:
            continue
        el_id, label, val = m.group('css')[1:], m.group('label').lower(), m.group('val').strip()
        v = VAR.match(val)
        guessed = (v and v.group(1) in from_sql) or val.isdigit()   # an id from Remember SQL, or a bare number
        hidden = el_id.startswith('id_') or 'hidden' in label
        dropdown = el_id in select2 or 'dropdown' in label
        if guessed and (hidden or dropdown):
            kind = 'hidden field' if hidden else 'dropdown'
            out.append({'n': n, 'text': text[:200], 'problem':
                        f'checks the {kind} #{el_id} equals {val}, an id guessed with SQL - the page picks its own value'})
    return out


def note(problems):
    """The message that sends the draft back to the AI."""
    return ('STEP CHECK (code, not a suggestion): ' + '; '.join(f'step {p["n"]} {p["problem"]}' for p in problems[:8]) +
            '. Replace each: after picking, assert the VISIBLE chosen text (e.g. Assert the Scheme box '
            '(#select2-scheme-container) contains \'{{sch_name}}\'); for a hidden field, drop the step. Prove ids AFTER '
            'Save with Assert SQL on the record this test saved. Do not write a dropdown / hidden value check against an '
            'id from Remember SQL again.')
