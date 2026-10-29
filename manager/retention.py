"""Run video retention: a run's recording (*.webm, ~6 MB a run) is removed once the run is older than the days set in
Settings > System (passed runs and other runs each have their own number, 7 by default). Everything else of the run
stays: its result, steps, screenshots and Fail reason. Without this the run folders fill the server's disk (5 GB in a
week on prod, 2026-10-29). Checked every few hours by a background thread; the case page simply shows no Video tab.
"""
import glob
import os
import threading
import time
from datetime import datetime, timedelta

import db

EVERY_S = 6 * 3600
_STARTED = False


def days(con):
    """(passed_days, failed_days) from Settings > System."""
    import settings
    return (settings._int(settings._get(con, 'sys.video_days_passed'), 7, 1, 365),
            settings._int(settings._get(con, 'sys.video_days_failed'), 7, 1, 365))


def sweep(con=None):
    """Removes the videos of runs older than their retention. Returns (files removed, bytes freed)."""
    own = con is None
    con = con or db.connect()
    try:
        passed, failed = days(con)
        now = datetime.now()
        cut = {True: (now - timedelta(days=passed)).strftime('%Y-%m-%d %H:%M:%S'),
               False: (now - timedelta(days=failed)).strftime('%Y-%m-%d %H:%M:%S')}
        rows = con.execute("SELECT id, status, run_dir, started_at FROM runs WHERE run_dir IS NOT NULL "
                           "AND status NOT IN ('running', 'queued') AND started_at < ?", (max(cut.values()),)).fetchall()
    finally:
        if own:
            con.close()
    return remove_videos(rows, cut)


def remove_videos(rows, cut):
    """rows: runs (status, run_dir, started_at); cut: {passed?: 'YYYY-MM-DD HH:MM:SS'}. Removes the *.webm of each run
    started before its cut-off. Returns (files removed, bytes freed)."""
    n = freed = 0
    for r in rows:
        if (r['started_at'] or '') >= cut[r['status'] == 'PASSED'] or not os.path.isdir(r['run_dir'] or ''):
            continue
        for p in glob.glob(os.path.join(r['run_dir'], '*.webm')):   # the recording, and a page@*.webm of a new tab
            try:
                size = os.path.getsize(p)
                os.remove(p)
                n, freed = n + 1, freed + size
            except OSError:
                pass
    return n, freed


def start():
    """Background thread: a sweep a few minutes after start, then every EVERY_S seconds."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True

    def loop():
        time.sleep(300)   # let the server finish starting
        while True:
            try:
                n, freed = sweep()
                if n:
                    print(f' * run videos: removed {n} past their retention ({freed / 1e6:.0f} MB freed)')
            except Exception as ex:   # never stop the server over a cleanup
                print(' * run video cleanup error:', ex)
            time.sleep(EVERY_S)
    threading.Thread(target=loop, daemon=True, name='lth-video-retention').start()
