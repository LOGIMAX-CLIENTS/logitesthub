---
name: tester
description: LogiTestHub (Tester CLI) — run plain-English UI tests in a headless browser and save the run (video, step screenshots, PASS/FAIL) in LogiTestHub; run saved test cases; generate test cases for a code change (git diff); list projects/cases; sync an app's menu into LogiTestHub modules. Use after changing UI code to verify it in a real browser, when the user asks to test / run / verify a page or flow, to run a LogiTestHub case by id, or to create test cases for a task, bug or requirement. Learns from failures: reads the project's lessons before testing and records a lesson after a failure is understood, so the same mistake does not happen twice.
---

# LogiTestHub — `tester` CLI

`tester` runs UI tests written as plain-English steps in a headless Chromium, records a video and a screenshot per
step, and uploads the run to LogiTestHub (the team's test manager). The Anthropic key stays on the LogiTestHub
server; AI steps and generation count against the user's monthly AI limit there.

**Always add `--json`** and read the result from stdout. Never print, echo or store the access key or any
`--secret` value.

## 0. Check the connection first

```
tester whoami --json
```
- Not logged in / cannot reach the server → stop and tell the user to run (in their own terminal, not in chat):
  `tester login --server http://<logitesthub-pc>:5050 --username <them> --key <access key>`
  (access key: LogiTestHub → Profile → Username and Access Key). Never ask them to paste the key into the chat.
- `tester` not found → `npm install -g http://<logitesthub-pc>:5050/cli/tester.tgz` then `tester setup` (one time browser download).
- "browser is not installed" → `tester setup`.

## Learn from mistakes (every time)

1. **Before** writing or running a test: `tester lessons <project> --json` and follow every lesson. Also read
   `references/lessons.md` once per session (mistakes that come with the CLI).
2. **Writing** a test case: follow `references/write-case.md` (ids from the code, supported step shapes).
   Project Retail (eTail): also `references/etail.md`.
3. **After a FAILED / PARTIAL / ERROR run**: follow `references/debug.md` - find the kind (app bug, test out of
   date, environment, test data, how we test), fix, run again until it passes, then
   `tester lesson add ...` so the next test (yours or a teammate's) avoids it.
4. Also record a lesson when **you** made a mistake the user had to correct (wrong command, wrong URL, wrong
   assumption about a screen): kind `agent_mistake`.

## 1. Find where the test belongs

```
tester projects --json              # project ids
tester cases <project id> --json    # existing cases (id, TC no, title, folder)
```
Prefer running an existing case when one covers the change; otherwise write a new plain-English test.

## 2. Login is automatic — never write login steps

Every LogiTestHub project has a **login helper** (LogiTestHub → Test Case → project → *Login helper*). It runs in front
of every test case of that project — web runs, Test Runs / schedules and `tester run` — so:

- **Do not write login steps** (no username/password/Sign In lines, no `## Sign in` section, no `@import login`).
  Start the test on the page under test, e.g. `Navigate to {{base_url}}index.php/<controller>/<method>.`
- Pass the login values the helper uses: `--var username=<user> --secret password=<pw>` (plus `--var branch=...` if it uses one).
- Testing the **login page itself** (valid / invalid login): put `login: none` in the front matter so the helper is skipped:
  ```
  ---
  login: none
  ---
  ```
- If `tester` prints that the project has no login helper, tell the user to add one there (do not inline login steps instead).
- `--no-login` skips the helper for one run.

## 3. Run a test

Plain English, one step per sentence (these shapes run without AI — prefer them):
- `Navigate to <url>.`  ·  `Navigate to {{base_url}}index.php/<controller>/<method>.`
- `Type <value> into the <label> field.`
- `Select '<option>' from the <label> dropdown.`
- `Click the <text> button.`
- `Assert the text '<text>' is visible.`  ·  `Assert the page title contains '<text>'.`  ·  `Assert the current URL contains '<text>'.`

Other free-text checks ("Check that the total is 1,000.00") are judged by AI from a screenshot.

```
tester run "Type {{user}} into the Username field. Type {{pw}} into the Password field. Click the Login button. Assert the text 'Dashboard' is visible." \
  --url <site login/start url> --var user=<username> --secret pw=<password> --project <id> --json
tester run --case <case id> --url <site base url> --json          # saved LogiTestHub case
tester run --file path/to/x_test.md --url <site base url> --json  # a _test.md file
```
Options: `--headed` (watch the browser) · `--no-upload` (keep only on this PC) · `--no-ai` (skip AI steps) ·
`--vars file.json`. Passwords only via `--secret` (masked in logs, report and video captions).
`--project` saves an ad-hoc run as a case in folder "CLI runs".

Ask the user for the site URL and login if you do not know them; do not guess credentials.

### Database checks (read-only)

When the environment has a read-only database (LogiTestHub → Settings → Environments → Database), check what the
screen saved — this is stronger than reading a success message:
```
Remember SQL "SELECT MAX(lot_no) FROM ret_lot_inwards WHERE created_on >= NOW() - INTERVAL 10 MINUTE" as last_lot.
Assert SQL "SELECT gross_wt FROM ret_lot_inwards_detail WHERE lot_no = {{last_lot}}" returns '10.500'.
Assert SQL "SELECT 1 FROM ret_taging WHERE tag_lot_id = {{last_lot}}" returns a row.
```
- Run with `--env <environment name>` (web runs use the environment picked for the run).
- Only one plain SELECT per step: no `;`, comments, INSERT/UPDATE/… (refused). The SQL goes in double quotes.
- Take table / column names from the module's model code (`application/models/*_model.php`), never guess.
- `Remember ... as name` keeps the value for later tests in the same environment: the next test uses `{{name}}`
  (e.g. lot add remembers `last_lot`, tagging selects `'{{last_lot}}'`).
- Values compare as text exactly as MySQL returns them (`10.500`, `8.00`, `300`).

## 4. Read the result

stdout JSON: `{ ok, status, report, run_dir, run_id, case_id, url }`.

| Exit | Meaning | Do |
|---|---|---|
| 0 | PASSED | Report it with the `url` |
| 1 | FAILED | Follow `references/debug.md`: failing step + screenshot → kind → fix code or test → run again → `tester lesson add` |
| 3 | PARTIAL | Some steps needed AI and could not run (e.g. no API credits) — say which; rewrite them in the shapes above if possible |
| 4 | AI limit reached | Stop and tell the user (admin can add extra in LogiTestHub) |
| 2 | Other error | Show the message (bad URL, not logged in, case not found) |

Always give the user the LogiTestHub `url` so they can watch the recording.

## 5. Generate test cases for a change

Run inside the git repository so the cases cover the code that changed:
```
tester generate "<what changed / the bug / the requirement>" --project <id> --diff --json           # uncommitted changes
tester generate "<...>" --project <id> --diff main --json                                            # branch vs main
```
Cases are saved in LogiTestHub for review (not run). Tell the user to review them, then run with `tester run --case`.

## 6. Modules (menu → LogiTestHub folders)

Only when the app's menu changed and the user asks. Build from the app's menu table, preview, then push:
```
tester modules build --rows menu.tsv --project <name> --id <id col> --label <label col> --parent <parent col> --link <link col> --sort <sort col> --active <active col> [--root <wrapper row id>]
tester modules push logitesthub.modules.json --dry-run
tester modules push logitesthub.modules.json        # only after the user agrees with the dry-run
```
Push only adds; it never deletes or renames.

## Other

`tester lessons <project> --json` (lessons learned) · `tester lesson add --project .. --kind .. --title .. --avoid ..` ·
`tester usage --json` (AI use this month) · `tester help` (all options). Runs are kept locally in `~/.lth/runs`.
