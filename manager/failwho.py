"""Who should fix a failed run: the environment, the test, or the application (a bug, or a fix that is not on that
environment yet). A best guess shown on the run's Fail reason page, with the reason it was made, never a verdict.

Rules, in order:
  1. The site or its setup broke the run (cannot reach it, no database set, server restarted ...) -> Environment.
  2. The failed step names an element (#id / .class) that the environment's code does not have:
       the case passed on a developer PC -> Fix not on <env> yet (merge / deploy the developer's branch);
       otherwise                         -> Test (it looks for an element the application does not have).
  3. The element is in the code and an Assert / Verify step failed -> App bug (the screen does not do what the
     test expects).
  4. Anything else -> Not sure (look at the screenshot: right screen = fix the test, wrong screen = app bug).
"""
import re

ENV_RE = re.compile(r'has no database|net::ERR_|ERR_(CONNECTION|NAME_NOT_RESOLVED|TIMED_OUT|ADDRESS|CERT|SSL)|'
                    r'connection refused|could not (connect|reach|open the site)|goto: Timeout|\b50[234]\b|'
                    r'Stopped when the server restarted|Runner crashed|no such host|site (is )?down', re.I)
ASSERT_RE = re.compile(r'^\s*(assert|verify|check|expect)\b', re.I)

PAREN_SEL = re.compile(r'\(([#.][^()]*)\)')   # every "(#id ...)" / "(.class ...)" hint, wherever it is in the step


def missing_elements(step_text, idx):
    """The ids / classes a step names that are not in the code index (the Tester Assistant's element check rules:
    library-made elements and row-counter ids count as present). Every hint in the step, not only a final one."""
    import elementcheck as ec
    if not idx or not step_text:
        return []
    out = []
    for sel in PAREN_SEL.findall(step_text):
        if '{{' in sel:
            continue
        for kind, name in ec._parts(sel):
            if kind == '#':
                base = ec.LIB_ID_SUFFIX.sub('', name)
                built = any(name.startswith(p) and re.fullmatch(r'[_-]?\d+', name[len(p):]) for p in idx.get('id_prefixes', ()))
                if not (name in idx['ids'] or base in idx['ids'] or built or ec.LIB_ID.match(name)):
                    out.append('#' + name)
            elif not (name in idx['classes'] or ec.LIB_CLASS.match(name)):
                out.append('.' + name)
    return list(dict.fromkeys(out))


KINDS = {   # kind -> (label, who fixes)
    'env': ('Environment', 'Environment / server owner'),
    'not_merged': ('Fix not on {env} yet', 'Developer: merge / deploy the branch'),
    'test': ('Test', 'Tester: update the test'),
    'app': ('App bug', 'Developer'),
    'unknown': ('Not sure', 'Look at the screenshot'),
}


def classify(status, fail_text, fail_reason, error, env_name, local_status, code_checked, missing):
    """{kind, label, who, why}. code_checked: the environment's code was available to look elements up in;
    missing: the failed step's ids / classes not found in that code."""
    env_name = env_name or 'this environment'
    msg = ' '.join(x for x in (fail_reason, error) if x).strip().rstrip('.')

    def out(kind, why):
        label, who = KINDS[kind]
        return {'kind': kind, 'label': label.format(env=env_name), 'who': who, 'why': why}

    if ENV_RE.search(msg):
        return out('env', f'The run could not use the site or its setup: "{msg[:160]}".')
    if missing:
        names = ', '.join(missing)
        if local_status == 'PASSED':
            return out('not_merged', f'The test looks for {names}, which is not in {env_name}\'s code, and the same case '
                                     'passed on a developer PC: the change is on the developer\'s branch, not here yet.')
        return out('test', f'The test looks for {names}, which is not in {env_name}\'s code and it has not passed on a '
                           'developer PC: the test names an element the application does not have (or a feature not '
                           'built yet).')
    if status == 'ERROR':
        return out('unknown', f'The run could not finish: "{msg[:160]}". Open the run to see how far it got.')
    if code_checked and fail_text and ASSERT_RE.match(fail_text):
        return out('app', f'The element is in {env_name}\'s code, but the check failed: the screen does not show what '
                          'the test expects.')
    if not code_checked:
        return out('unknown', f'No code for {env_name} to look the element up in (Settings > Environments > Code '
                              'repository). Look at the screenshot: right screen = fix the test, wrong screen = app bug.')
    return out('unknown', 'A step that acts on the screen failed. Look at the screenshot: if the screen is right, fix '
                          'the test; if it is wrong, it is an app bug.')
