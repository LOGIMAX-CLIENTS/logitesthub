# LogiTestHub (POC)

Kane-CLI-style testing for eTail: generate test cases from a task/bug/requirement, run them
headless, get a report with screen recording + per-step screenshots.

## Setup (once)
    npm install
    npx playwright install chromium
    setx ANTHROPIC_API_KEY "sk-ant-..."   # then reopen the terminal

Variables (base_url, username, password, branch, ...) are read from
`F:/TestMU-Ai/.testmuai/variables/etail.json` - nothing secret is stored here.
Passwords marked `"secret": true` are masked as ****** in logs and reports.

## 1. Generate test cases
    node generate.js --task "GRN entry must block negative pcs across lots" --repo C:/xampp/htdocs/wt-prod --count 6

- `--repo` adds the code change: uncommitted changes (`git diff HEAD`), or `--base PRODUCTION` for a branch.
- `--paths admin/application/controllers` narrows a large diff.
- Output: `generated/<date>-<name>/NN-<case>_test.md` + `CASES.md`. **Review before running.**

## 2. Run
    node run.js <case_test.md>          # one case
    node run-all.js <folder>            # every _test.md in a folder + summary page
    add --headed to watch the browser, --no-ai to skip AI steps

Output per run: `runs/<time>-<case>/report.html`, `video.webm`, `step-NN.png`, `events.ndjson`.

## How steps run
- Standard sentences (Navigate / Type ... into the X field / Select 'X' from the Y dropdown /
  Click the X button / Assert the page title ... / Assert the text 'X' is visible) run directly: no AI cost.
- Any other sentence goes to Claude with a screenshot + list of page elements (AI step).
- Model: `claude-opus-5-5`; override with `ETAIL_AI_MODEL`.

## Result
- PASSED: every step passed. FAILED: a step failed; later steps are skipped.
- PARTIAL: nothing failed, but some free-text steps could not run (AI off / no credits).

## LogiTestHub web app (like TestMu Test Manager)
Projects → folders → test cases → runs, with the video and step screenshots of every run.

    cd manager
    pip install -r requirements.txt
    python manage.py create-user <username> "<Full Name>" --admin      # asks for a password
    python manage.py import-folder --project Retail --path F:/TestMU-Ai/.testmuai/tests/Retail
    python manage.py import-runs                                        # attach runs/ folders
    python app.py                                                       # http://localhost:5050

Database: MySQL Server 8.4 LTS, Windows service `MySQL84` (starts automatically), port **3307**,
localhost only, database `logitesthub`. XAMPP's MariaDB on 3306 is separate and not used by this app.
Server config: `C:\ProgramData\MySQL\MySQL Server 8.4\my.ini`. Connection settings are in `manager/data/mysql.json`
(`host, port, user, password, database`), or env vars `LTH_DB_HOST / LTH_DB_PORT / LTH_DB_USER /
LTH_DB_PASSWORD / LTH_DB_NAME`. Tables are created on first start. `python migrate_sqlite.py` copies the
old `data/manager.db` (SQLite) into an empty MySQL database once.

- Other PCs on the office LAN open `http://<this-pc-ip>:5050` (allow port 5050 in Windows Firewall).
- **Run test** runs the case headless on this PC (one run at a time) and records it.
- **Share link** makes one run viewable without login; **Stop sharing** turns it off.
- Data: `manager/data/manager.db` (SQLite). Importing copies test text in; TestMU-Ai files are not changed.
- POC only: Flask's built-in server, no HTTPS. For the team version, run behind a proper server.
