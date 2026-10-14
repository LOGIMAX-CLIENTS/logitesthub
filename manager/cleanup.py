"""Test data a passed run kept, and removing it before the next run.

A case's "## Clean up" section (Set up SQL DELETE ... steps) runs when the test FAILS, so the same data can be used
again at once. When the test PASSES its data is kept (people look at it); the runner reports the clean-up steps with
their values filled in (e.g. account 846) and they are stored here per case and environment. Before the next run of
that case the user is asked: delete that data first, or keep it. Delete = those exact steps run first, in a section
of their own; once they all passed the stored rows go away. Test Runs and schedules (nobody to ask) always delete.

Registered from app.py: init(con) at start; record() is called by db.record_finished_run for every finished run.
"""
import json
import os

PRE_SECTION = "Remove the last run's data"   # must not look like a "Clean up" section (those are kept on pass)

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_pending_cleanup (
  id INT AUTO_INCREMENT PRIMARY KEY, case_id INT NOT NULL, run_id INT NOT NULL, env_id INT NULL,
  steps MEDIUMTEXT NOT NULL, created_at VARCHAR(32) NOT NULL, clearing_run INT NULL,
  KEY ix_cpc_case (case_id)
) ENGINE=InnoDB
"""


def init(con):
    con.execute(SCHEMA)
    n = con.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='runs' AND COLUMN_NAME='env_id'").fetchone()['n']
    if not n:   # which environment a run used (its data lives there)
        con.execute('ALTER TABLE runs ADD COLUMN env_id INT NULL')


def pending(con, case_id, env_id):
    """Data passed runs of this case left on this environment, oldest first (not already being removed)."""
    return con.execute('SELECT p.*, r.started_at AS run_at, r.run_by FROM case_pending_cleanup p '
                       'LEFT JOIN runs r ON r.id=p.run_id WHERE p.case_id=? AND p.env_id <=> ? AND p.clearing_run IS NULL '
                       'ORDER BY p.id', (case_id, env_id)).fetchall()


def describe(rows):
    """'run #116 (13-10-2026 16:36, shreenath): 4 clean-up steps' per row, for the question shown to the user."""
    out = []
    for r in rows:
        n = len(json.loads(r['steps']))
        out.append(f"run #{r['run_id']}" + (f" ({str(r['run_at'])[:16]}, {r['run_by']})" if r['run_at'] else '')
                   + f": {n} clean-up step{'s' if n != 1 else ''}")
    return out


def section(rows):
    """The markdown section that removes the kept data, put in front of the case's own steps."""
    steps = [s for r in rows for s in json.loads(r['steps'])]
    return f'## {PRE_SECTION}\n' + '\n'.join(steps) + '\n' if steps else ''


def with_section(content, block):
    """content with block inserted after its front matter and title (so the login helper still comes first)."""
    if not block:
        return content
    lines = content.split('\n')
    i = 0
    if lines and lines[0].strip() == '---':   # front matter
        i = next((k + 1 for k in range(1, len(lines)) if lines[k].strip() == '---'), 0)
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith('# '):
        i += 1
    return '\n'.join(lines[:i] + ['', block] + lines[i:])


def prepare(con, case_id, env_id, run_id, content):
    """Puts the removal of this case's kept data in front of content and marks those rows as being removed by
    run_id. Returns (content, number of rows)."""
    rows = pending(con, case_id, env_id)
    if not rows:
        return content, 0
    with con:
        con.execute(f'UPDATE case_pending_cleanup SET clearing_run=? WHERE id IN ({",".join("?" * len(rows))})',
                    (run_id, *[r['id'] for r in rows]))
    return with_section(content, section(rows)), len(rows)


def mark(con, ids, case_id, run_id):
    """CLI: the client put these rows' steps in front of its run (only rows of that case)."""
    ids = [int(x) for x in ids if str(x).isdigit()]
    if ids:
        con.execute(f'UPDATE case_pending_cleanup SET clearing_run=? WHERE case_id=? AND id IN ({",".join("?" * len(ids))})',
                    (run_id, case_id, *ids))


def _pending_event(run_dir):
    path = os.path.join(run_dir or '', 'events.ndjson')
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        for line in f:
            if '"cleanup_pending"' in line:
                try:
                    return json.loads(line).get('steps') or None
                except ValueError:
                    return None
    return None


def record(con, run_id, run_dir, steps):
    """After a run: drop the kept data it removed (when its removal steps all passed) and store what it kept."""
    run = con.execute('SELECT case_id, env_id FROM runs WHERE id=?', (run_id,)).fetchone()
    if not run:
        return
    removing = con.execute('SELECT id FROM case_pending_cleanup WHERE clearing_run=?', (run_id,)).fetchall()
    if removing:
        pre = [s for s in steps if s.get('section') == PRE_SECTION]
        done = bool(pre) and all(s.get('status') == 'passed' for s in pre)
        con.execute('DELETE FROM case_pending_cleanup WHERE clearing_run=?' if done else
                    'UPDATE case_pending_cleanup SET clearing_run=NULL WHERE clearing_run=?', (run_id,))
    kept = _pending_event(run_dir)
    if kept:
        import db
        con.execute('INSERT INTO case_pending_cleanup(case_id, run_id, env_id, steps, created_at) VALUES (?,?,?,?,?)',
                    (run['case_id'], run_id, run['env_id'], json.dumps(kept), db.now()))
