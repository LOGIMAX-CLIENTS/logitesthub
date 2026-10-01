"""One-time copy of the old SQLite store (data/manager.db) into MySQL, keeping every id.

  python migrate_sqlite.py

Refuses to run if the MySQL tables already hold data, so it cannot overwrite or duplicate anything.
"""
import os
import sqlite3
import sys

import db

TABLES = ['users', 'projects', 'folders', 'cases', 'runs', 'api_keys', 'api_log', 'ai_usage']   # parent first


def main():
    if not os.path.isfile(db.SQLITE_PATH):
        sys.exit(f'No SQLite file at {db.SQLITE_PATH}')
    db.init()
    src = sqlite3.connect(db.SQLITE_PATH)
    src.row_factory = sqlite3.Row
    dst = db.connect()
    busy = [t for t in TABLES if dst.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]]
    if busy:
        sys.exit(f'MySQL already has data in: {", ".join(busy)}. Nothing copied.')
    mysql_cols = {t: {r['Field'] for r in dst.execute(f'SHOW COLUMNS FROM {t}')} for t in TABLES}
    summary = []
    with dst:
        dst.execute('SET FOREIGN_KEY_CHECKS=0')
        for t in TABLES:
            rows = src.execute(f'SELECT * FROM {t}').fetchall()
            if not rows:
                summary.append((t, 0))
                continue
            cols = [c for c in rows[0].keys() if c in mysql_cols[t]]
            dst.executemany(f'INSERT INTO {t} ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})',
                            [tuple(r[c] for c in cols) for r in rows])
            summary.append((t, len(rows)))
        dst.execute('SET FOREIGN_KEY_CHECKS=1')
    for t, n in summary:
        got = dst.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t:10} sqlite={n:5}  mysql={got:5}  {"OK" if got == n else "MISMATCH"}')
    dst.close()


if __name__ == '__main__':
    main()
