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

Database: MySQL Server 8.0 (installed with the MySQL Installer), Windows service `MySQL80` (starts
automatically), port **3306**, database `logitesthub`, app user `logitesthub`. Keep XAMPP's MySQL stopped: it
also uses 3306. In PowerShell connect with `--host=127.0.0.1 --port=3306` (PowerShell splits `-h127.0.0.1`).
Server config: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`. Connection settings are in `manager/data/mysql.json`
(`host, port, user, password, database`), or env vars `LTH_DB_HOST / LTH_DB_PORT / LTH_DB_USER /
LTH_DB_PASSWORD / LTH_DB_NAME`. Tables are created on first start. `python migrate_sqlite.py` copies the
old `data/manager.db` (SQLite) into an empty MySQL database once.

- Other PCs on the office LAN open `http://<this-pc-ip>:5050` (allow port 5050 in Windows Firewall).
- **Run test** runs the case headless on this PC (one run at a time) and records it.
- **Share link** makes one run viewable without login; **Stop sharing** turns it off.
- Data: MySQL database `logitesthub` (see Database above). Importing copies test text in; TestMU-Ai files are not changed.
- POC only: Flask's built-in server, no HTTPS. For the team version, run behind a proper server.

## Modules -> sub-modules ("Set up modules", no AI)
A project's repository is a tree: module -> sub-module (like the application's own menu). Project page ->
repository ⋯ -> "Set up modules from the app…", or automatically after creating a project.

- **Read from the application (one run)**: logs in with an Environment and reads its menu (`menu-crawl.js`,
  logic in `lib/menu.js`). Login order: the Environment's own **Login steps** (Settings -> Environments ->
  Advanced) -> eTail's login steps -> any login form it finds (also email-then-password forms). It opens
  collapsed / icon-only sidebars, then reads with six readers (nested lists, dropdowns / collapses /
  accordions, menu & tree roles, headings with links, click-only menus, plain links) and keeps the best.
- When it cannot read, it says why (wrong login, OTP, CAPTCHA, no menu on that page...) and shows a screenshot.
  If the menu is on another page, set **Menu page URL** (on the Environment or in the box on the page).
- **Type them** is always there as the fallback.

Regression checks (run after changing `lib/menu.js`):

    cd manager
    python manage.py check-menus                 # 18 sample apps + live Dev (eTail) and staging (PM)
    python manage.py check-menus --samples-only  # only the sample apps (no network)
    node ../tests/menu-regression.js --serve 5099  # serve the sample apps to try them in the UI

Live expectations are in `manager/data/menu-live.json` (not in git: it names your own apps' modules). Start from `tests/menu-live.example.json` and add a block per Environment.

### Modules from the source of truth (main method)
The reliable way: build `logitesthub.modules.json` from the application's own menu table / route files and push it with
the CLI (`tester modules build / push --dry-run / push / pull`), or upload it on the web ("Import modules file", option 1).
Push only adds; it never deletes. Full guide and per-app recipes: `docs/MODULES_SYNC.md`; section to paste into an
application's CLAUDE.md: `docs/CLAUDE-modules-section.md`. Example: `examples/etail.modules.json` (built from eTail's
`menu` table: 58 modules, 442 sub-modules).
