"""Admin commands.

  python manage.py create-user <username> "<Full Name>" [--admin]
  python manage.py set-password <username>
  python manage.py import-folder --project Retail --path F:/TestMU-Ai/.testmuai/tests/Retail
  python manage.py import-runs                 # attach existing runs/ folders to imported cases
"""
import argparse
import getpass
import os
import sys

from werkzeug.security import generate_password_hash

import db


def create_user(args):
    pw = getpass.getpass(f'Password for {args.username}: ')
    if len(pw) < 8:
        sys.exit('Password must be at least 8 characters.')
    if pw != getpass.getpass('Repeat password: '):
        sys.exit('Passwords do not match.')
    with db.connect() as con:
        if con.execute('SELECT 1 FROM users WHERE username=?', (args.username,)).fetchone():
            sys.exit(f'User {args.username} already exists.')
        con.execute('INSERT INTO users(username, name, pw_hash, role, created_at) VALUES (?,?,?,?,?)',
                    (args.username, args.name, generate_password_hash(pw), 'admin' if args.admin else 'member', db.now()))
    print(f'Created {"admin" if args.admin else "member"} {args.username}.')


def set_password(args):
    pw = getpass.getpass(f'New password for {args.username}: ')
    if len(pw) < 8:
        sys.exit('Password must be at least 8 characters.')
    if pw != getpass.getpass('Repeat password: '):
        sys.exit('Passwords do not match.')
    with db.connect() as con:
        if not con.execute('UPDATE users SET pw_hash=? WHERE username=?',
                           (generate_password_hash(pw), args.username)).rowcount:
            sys.exit(f'No user {args.username}.')
    print(f'Password changed for {args.username}.')


def import_folder(args):
    """Imports every *_test.md under --path. Folder = first directory level (the module).
    Skips helpers/ and output-*/ directories. Re-importing updates cases already imported."""
    root = os.path.abspath(args.path)
    if not os.path.isdir(root):
        sys.exit(f'Not a folder: {root}')
    added = updated = 0
    with db.connect() as con:
        row = con.execute('SELECT id FROM projects WHERE name=?', (args.project,)).fetchone()
        project_id = row['id'] if row else con.execute(
            'INSERT INTO projects(name, description, created_by, created_at) VALUES (?,?,?,?)',
            (args.project, args.description or '', 'import', db.now())).lastrowid
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != 'helpers' and not d.startswith('output-') and not d.startswith('.'))
            for fn in sorted(filenames):
                if not fn.endswith('_test.md'):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                parts = rel.split(os.sep)
                folder = parts[0] if len(parts) > 1 else None
                with open(full, encoding='utf-8') as f:
                    content = f.read()
                title = db.case_title(content, fn[:-8])
                source = os.path.normcase(os.path.abspath(full))
                existing = con.execute('SELECT id FROM cases WHERE source_path=?', (source,)).fetchone()
                if existing:
                    con.execute('UPDATE cases SET title=?, content=?, updated_by=?, updated_at=? WHERE id=?',
                                (title, content, 'import', db.now(), existing['id']))
                    updated += 1
                else:
                    folder_id = db.get_or_create_folder(con, project_id, folder)
                    db.add_case(con, project_id, folder_id, title, content, 'import', source)
                    added += 1
    print(f'Project {args.project}: {added} cases added, {updated} updated.')


def import_runs(_args):
    """Links finished runs in runs/ to the imported case whose file they ran."""
    linked = skipped = 0
    if not os.path.isdir(db.RUNS_DIR):
        sys.exit('No runs folder yet.')
    with db.connect() as con:
        for name in sorted(os.listdir(db.RUNS_DIR)):
            run_dir = os.path.join(db.RUNS_DIR, name)
            if not os.path.isdir(run_dir):
                continue
            if con.execute('SELECT 1 FROM runs WHERE run_dir=?', (run_dir,)).fetchone():
                continue
            summary, _ = db.read_run_events(run_dir)
            if not summary or not summary.get('file'):
                skipped += 1
                continue
            source = os.path.normcase(os.path.abspath(summary['file']))
            case = con.execute('SELECT id FROM cases WHERE source_path=?', (source,)).fetchone()
            if not case:
                skipped += 1
                continue
            started = db.to_local(summary.get('at'))
            run_id = con.execute('INSERT INTO runs(case_id, status, started_at, run_by) VALUES (?,?,?,?)',
                                 (case['id'], 'running', started, 'cli')).lastrowid
            db.record_finished_run(con, run_id, run_dir)
            linked += 1
    print(f'{linked} runs linked, {skipped} skipped (no matching case).')


def main():
    db.init()
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    u = sub.add_parser('create-user')
    u.add_argument('username')
    u.add_argument('name')
    u.add_argument('--admin', action='store_true')
    u.set_defaults(fn=create_user)
    sp = sub.add_parser('set-password')
    sp.add_argument('username')
    sp.set_defaults(fn=set_password)
    i = sub.add_parser('import-folder')
    i.add_argument('--project', required=True)
    i.add_argument('--path', required=True)
    i.add_argument('--description', default='')
    i.set_defaults(fn=import_folder)
    r = sub.add_parser('import-runs')
    r.set_defaults(fn=import_runs)
    args = p.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
