"""Setting up a developer's repo for LogiTestHub from the Tester CLI (tester init): which LogiTestHub project the repo
belongs to, and which module / sub-module each test case goes in.

- repo -> project: the first developer of a repo picks (or creates) the project once; it is kept here by the repo's
  git remote, so the next developer of that repo is never asked again (test cases may live in another repo, so
  tests/tester.json alone cannot carry it).
- module / sub-module: a case file says "module: <Module>" / "sub_module: <Sub>" in its front matter; a new case
  uploaded from the CLI goes in that folder (only an existing one: new modules are added on purpose with
  tester modules add / push), else in "CLI runs" as before.
"""
import re

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_repos (
  repo VARCHAR(255) NOT NULL PRIMARY KEY, project_id INT NOT NULL, remote VARCHAR(500) NULL,
  linked_by VARCHAR(64) NULL, linked_at VARCHAR(32) NOT NULL,
  KEY ix_project_repos_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
"""
_ready = False


def ensure(con):
    global _ready
    if not _ready:
        con.execute(SCHEMA)
        _ready = True


def repo_key(remote):
    """git remote URL -> 'host/owner/name' in lower case: https, ssh and token forms of one repo give the same key."""
    r = str(remote or '').strip()
    if not r:
        return ''
    r = re.sub(r'^[a-z+]+://', '', r, flags=re.I)        # https:// ssh:// git://
    r = re.sub(r'^[^@/]+@', '', r)                        # user@ / token@
    r = re.sub(r'^([^/:]+):(?!\d+/)', r'\1/', r)          # git@host:owner/name (not host:port/)
    r = re.sub(r':\d+/', '/', r)                          # host:port/
    r = re.sub(r'\.git/?$', '', r).strip('/')
    return r.lower()[:255]


def project_of_repo(con, repo):
    ensure(con)
    return con.execute('SELECT p.id, p.name, r.linked_by, r.linked_at FROM project_repos r JOIN projects p ON p.id=r.project_id '
                       'WHERE r.repo=?', (repo,)).fetchone()


def link(con, repo, pid, remote, username):
    ensure(con)
    con.execute('INSERT INTO project_repos(repo, project_id, remote, linked_by, linked_at) VALUES (?,?,?,?,?) '
                'ON DUPLICATE KEY UPDATE project_id=VALUES(project_id), remote=VALUES(remote), linked_by=VALUES(linked_by), '
                'linked_at=VALUES(linked_at)', (repo, pid, (remote or '')[:500] or None, username, db.now()))


def folder_for(con, pid, module, sub=None):
    """The folder id of 'module' (or its sub-module 'sub'), matched by name ignoring case; None when it does not exist."""
    module, sub = ' '.join(str(module or '').split()), ' '.join(str(sub or '').split())
    if not module:
        return None
    m = con.execute('SELECT id FROM folders WHERE project_id=? AND parent_id=0 AND archived=0 AND LOWER(name)=LOWER(?)',
                    (pid, module)).fetchone()
    if not m or not sub:
        return m['id'] if m else None
    s = con.execute('SELECT id FROM folders WHERE project_id=? AND parent_id=? AND archived=0 AND LOWER(name)=LOWER(?)',
                    (pid, m['id'], sub)).fetchone()
    return s['id'] if s else None


def folder_path(con, folder_id):
    """(module, sub_module) names of a folder; ('', '') when none."""
    if not folder_id:
        return '', ''
    f = con.execute('SELECT name, parent_id FROM folders WHERE id=?', (folder_id,)).fetchone()
    if not f:
        return '', ''
    if not f['parent_id']:
        return f['name'], ''
    p = con.execute('SELECT name FROM folders WHERE id=?', (f['parent_id'],)).fetchone()
    return (p['name'] if p else ''), f['name']


def status(con, pid):
    """What a project still needs before tests can be written in it (shown by tester init)."""
    p = con.execute('SELECT id, name, login_helper FROM projects WHERE id=?', (pid,)).fetchone()
    n_mod = con.execute('SELECT COUNT(*) AS n FROM folders WHERE project_id=? AND parent_id=0 AND archived=0 '
                        "AND name<>'CLI runs'", (pid,)).fetchone()['n']
    n_sub = con.execute('SELECT COUNT(*) AS n FROM folders WHERE project_id=? AND parent_id<>0 AND archived=0', (pid,)).fetchone()['n']
    todo = []
    if not (p['login_helper'] or '').strip():
        todo.append('login_helper')
    if not n_mod:
        todo.append('modules')
    return {'project': {'id': p['id'], 'name': p['name']}, 'login_helper': bool((p['login_helper'] or '').strip()),
            'modules': n_mod, 'sub_modules': n_sub, 'todo': todo}
