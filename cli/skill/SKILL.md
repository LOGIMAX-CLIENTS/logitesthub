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

## Two team rules (every user, every IDE)

**Rule 1 — Review before run.** A test case you wrote, generated or changed is **never run straight away**.
1. Save it in the tests folder, `<tests>/<module>/<sub-module>/<name>_test.md` (section 0b; do not use `tester run "<plain English>"` for a new test: that runs at once).
2. Show the user the case for review, then stop and wait:
   ```
   Test case ready for review: <tests>/<module>/<sub-module>/<name>_test.md
   Site: <url / environment>      Signs in as: <user variable>      Saves data: no | yes (what, on which environment)
   | # | Section | Step |
   |---|---------|------|
   | 1 | Sign in | Navigate to {{base_url}}... |
   ...
   Any changes? Tell me what to change, or say "ok" to start the run.
   ```
3. The user asks for changes → make them, show the case again, wait again. Run only after an explicit **ok** (ok / run /
   go / start).
4. The CLI holds you to it: `tester run --file` on a new or changed `_test.md` stops with exit **6**,
   `"error": "review_first"` and `review` = {title, steps [{n, section, text}], saves_data, saves_data_steps}. Show
   that as the table above (Saves data from `saves_data`), wait for the ok, then run the same command with
   `--approved`. Never add `--approved` before the user's ok. An unchanged approved file runs without it; cases pulled
   from LogiTestHub (`tester case pull`, `tester run --case`) are already approved.
- A user's own "run TC-12" / "run <tests>/x_test.md" (a case they did not ask you to write or change) is already the ok.
- While fixing a failed run you change steps → show what changed (old → new) and ask again before the next run,
  unless the user said to keep fixing until it passes.

**Rule 2 — Report every run the same way** (passed or failed), with the fields from the run's JSON (section 4):
```
**<title>** — PASSED | FAILED | NOT COMPLETED | PARTIAL
| Steps | Passed | Failed | Skipped | Duration |
|-------|--------|--------|---------|----------|
| 8     | 7      | 1      | 0       | 42s      |

Failed at step 5 (<section>): <step text>
  Error: <error>    Expected: ...    Actual: ...    Cause: development bug | our side | environment (+ why)

| # | Step | Result | Time |      <- every step from `steps` (for 40+ steps: the failed one and 3 around it, plus the counts)
|---|------|--------|------|
| 1 | Navigate to ... | passed | 1.2s |
...
LogiTestHub: <url>
```
- The LogiTestHub `url` is **always** in the report. Not saved (`upload.ok: false`) → say so and give `upload.retry`.
- After the report, the verdict JSON block (section 4).

## 0. Check the connection first

```
tester whoami --json
```
- Not logged in / cannot reach the server → stop and tell the user to run (in their own terminal, not in chat):
  `tester login --server http://<logitesthub-pc>:5050 --username <them> --key <access key>`
  (access key: LogiTestHub → Profile → Username and Access Key). Never ask them to paste the key into the chat.
- `tester` not found → `npm install -g http://<logitesthub-pc>:5050/cli/tester.tgz`, then `tester --version`: when
  it says the CLI is not set up, show the user those next steps (`tester setup`, `tester login` in their own terminal)
  and wait. `--version` is `<number>+<code hash>`: a different hash = different code, even when the number is the same.
- "browser is not installed" → `tester setup`.

## 0b. Set the repo up (first use in a repo, and whenever `tester init` lists a to-do)

Run `tester init --json` in the developer's repo before the first test, then do every item of its `todo`, in order:
1. **Project.** `project_needed` / `no_such_project` (exit 7): the repo is not linked yet. Ask the user once which
   LogiTestHub project it belongs to (show `projects`). Then `tester init --project <id or name>`; a name that is not
   there → ask "create it?" and add `--create`. The link is kept on the server by the repo's git remote, so the next
   developer of this repo is **never asked again** - do not ask when `tester init` already gives a project.
   The user says the project is wrong → `tester init --unlink`, then ask again.
2. **Tests folder** (`tests_dir_needed`, exit 8): where this repo's test cases are kept **on this PC**. Ask the user,
   with the suggestion from the JSON: "Shall I keep the test cases in `<suggested>`? (a folder of their own, shown
   next to the code in the IDE) Or tell me another path." Then `tester init --tests-dir "<their answer>"`. The CLI
   makes it, remembers it for this repo and adds it to the IDE workspace as a second folder (`ide` in the result:
   `workspace` / `code --add` = done; `manual` = tell the user to open that folder in their IDE). Asked once per PC.
   The tests folder can hold **several projects**: each project's cases, `tester.json`, `helpers/` and `variables/` are
   in a folder named after the LogiTestHub project (`F:\Tester_CLI\Retail\`, `F:\Tester_CLI\Chit_Fund\`); the CLI
   makes it (`project_dir` in the JSON). Below, `<tests>` = that **project folder** (`dir` in the result).
   Never offer a whole drive (`F:\`) or the home folder: the CLI refuses them (`tests_dir_too_wide`); the user names a
   drive → offer a new folder in it (`F:\Tester_CLI`).
   - **`project_folder_needed`** (exit 8): an older tests folder has this project's cases at its top. Ask: "Move this
     project's test cases into `<project_dir>`? (nothing is deleted; in git it shows as renames)" → `tester init --move`.
   - **`tests_dir_flat`** (exit 8): the folder still has another project's cases at its top: run `tester init --move`
     in that project's repo first, then this one again.
   - **The team already has a tests repo** (`tests_remote` in the JSON): ask "The team keeps the test cases in
     `<tests_remote>`. Clone it to `<suggested>`?" - `tester init --tests-dir "<folder>"` clones it (the user's own
     git login) and adds it to the workspace. Clone failed (no access): tell the user to get access or clone it themselves.
   - **Not shared yet** (`shared: false`): after the first cases are written, offer to make `<tests>` a git repo and push
     it (the user gives / creates the remote), then `tester init` again: LogiTestHub then offers it to the next developer.
   - `tester.json` in `<tests>` is shared (project, site URL only); `variables/` (passwords) stays out of git.
3. **Login helper** (`login_helper` in `todo`): find the app's login page in the code (form ids, button text), write
   the steps with `{{username}}` / `{{password}}` (never real values) in `<tests>/helpers/login.md`, show them to the user
   (Rule 1: review), then `tester login-helper set --file <tests>/helpers/login.md`. An existing helper (409): show both
   and replace only if the user says so (`--force`). From then on test cases have **no** login steps.
4. **Modules** (`modules` in `todo`): `tester modules read --url <the app page that shows the full menu> --var
   username=.. --secret password=.. --json` signs in on this PC (localhost works) and reads menu → sub-menu. Show the
   user the list (`menu`), then `tester modules push logitesthub.modules.json`. It only adds; nothing is renamed or deleted.
   The reader takes the first menu it finds: when the list is not the app's main menu (e.g. header dropdowns, a user
   name), never push it - build it from the app's menu table instead (`tester modules build --rows <menu rows>`, rows
   from a read-only SELECT of the menu table the menu code reads), show it, then push.

**Every test case goes in its module.** Before writing a case, `tester modules pull --json` and pick the module /
sub-module that is the screen's menu → sub-menu in the app (the menu code / menu table, not a guess). Put it in the
front matter and save the file in that folder:
```
---
module: Retail Catalog
sub_module: MC & VA Settings
---
```
→ `<tests>/retail_catalog/mc_va_settings/<name>_test.md`. LogiTestHub files the case in that module when it is saved.
The screen is new and not in LogiTestHub yet (a module being developed): ask the user "add module X > Y?", then
`tester modules add "X" "Y"`, then save the case there. A run result with `folder_note` = the module was not found:
add it, run again (the case moves there).

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
5. **Save the fix where the test lives.** A fix made only in the run (a temp copy, an edited command) is lost: the
   next session runs the old steps and fails the same way. In the runs of 2026-10-08 every evening failure repeated
   a morning failure that had been fixed only in the run. For a saved case, fix it like this:
   ```
   tester case pull <case id> --json                     # writes <tests>/<module>/<sub-module>/TC<n>_<title>_test.md (the "file" in the JSON) and remembers the version
   # edit the file, then prove the fix:
   tester run --file TC-<n>_test.md --project <id> --url <site> --var username=.. --secret password=.. --json
   tester case push <case id> --file TC-<n>_test.md --dry-run --json   # check the diff: only your fix
   tester case push <case id> --file TC-<n>_test.md --note "<what was fixed>" --json
   ```
   - Push only after the fixed run PASSED, and only the steps you changed (never delete a failing check to pass).
   - An `app_bug` is not fixed in the case: leave the case as it is.
   - Push says *changed by someone after you pulled* → pull again, redo the change. `--force` only if the user says so.
   - The old version is kept in `~/.lth/case-backups/` (say where, in the report).
   - For a `_test.md` that is not a LogiTestHub case, just save the edited file.

### Pre-run checklist (every run, before you press Enter)

Reading lessons is not enough: on 2026-10-08 the same four mistakes came back hours after their lessons were saved.
Check the case line by line against this list **and** each lesson's `avoid`:

- [ ] Login values passed: `--var username=.. --secret password=..` (or `--vars file.json`). The CLI stops when they are missing.
- [ ] No prose in the body: every sentence under a `##` section is run as a step. Notes go in the chat / PR, not the case.
- [ ] Every element has a hint taken from the view code: `(#id)`, or a class for row-built inputs `(.no_of_pcs)`.
- [ ] Radios: unique id → `radio button (#id)`; shared id → text only; `label.btn` button group → `button (#id)`.
- [ ] Dependent / filtered dropdowns: the parent (Cost Centre, Product, Design) is chosen first, then `Wait 2 seconds.`
- [ ] The option text exists in the data the dropdown is built from (check with SQL), not in a setting or another table.
- [ ] No hard-coded stock, counts or ids that a save changes: `Remember SQL` the current value and assert `{{value}}`.
- [ ] Assertion texts copied from the JS / view (exact spacing and punctuation), not guessed.
- [ ] A test that saves: tell the user, and only on Local / QA.

## Where the test cases live: the tests folder

Test cases are files in the repo's tests folder (`<tests>`, chosen in section 0b, `dir` in `tester init`): usually a
folder of its own next to the repo (e.g. `C:\xampp\htdocs\etail_development_src-tests`), open in the IDE as a second
folder. Older setups use `<repo>/tests/`; both work the same.
- `<tests>/tester.json`: LogiTestHub project id + site URL, so `--project` / `--url` can be left out.
- Cases go in `<tests>/<module>/<sub-module>/` with `module:` / `sub_module:` in the front matter (section 0b). A
  `case_id: <id>` line links the file to its LogiTestHub case: `tester run --file <file>` saves the run under that case.
- `tester test --json` runs every `*_test.md` in `<tests>` (same options as `run`); exit code = the worst result.
- Fix a failing case **in its file**, run it, then `tester case push <case_id> --file <file>`.
- No tests folder yet (`tester init` not done): a case is not saved as a file - run `tester init` first.
- `--no-save`: do not write a file (a one-off check).
- `<tests>/helpers/`: `@import` files (`@import ../helpers/login.md`). `<tests>/variables/variables.json`: test data
  (base_url, username, password as `{"value": .., "secret": true}`), used by every run there, kept out of git - with it
  filled in, `--var` / `--secret` are not needed. The user fills the password in themselves, never in the chat.

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
- If `tester` prints that the project has no login helper, set it up first (section 0b, step 2) - do not inline login steps instead.
- `--no-login` skips the helper for one run.

## 3. Preflight check, then run

**Before every run of a case that has database steps**, run the preflight check (precondition check + test data
validation, fail fast). It runs the case's leading `Remember SQL` / `Assert SQL` steps - settings, permissions and the
data it picks - read-only on the environment, in seconds, no browser:
```
tester check --case <id> --env <name> --json        # or --file x_test.md
```
- `passed` -> run it. `failed` (exit 1) -> **do not run**: a 5-minute run would stop at the same step. Fix it first:
  data missing -> make the data in the test (or pick it differently); a setting / permission blocks it -> tell the user
  in one line where to change it (screen > field) and the no-change alternative. Then check again.
- `no_database` (exit 2) -> that environment cannot run database steps: use one that has a database.
- Dry runs of the whole case are not possible (Save buttons write data); this check is the safe part.


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
#   ^ a quick one-off the user typed themselves. A test case you write: save the _test.md, review it (Rule 1), then --file
tester run --case <case id> --url <site base url> --json          # saved LogiTestHub case
tester run --file path/to/x_test.md --url <site base url> --json  # a _test.md file
```
Options: `--headed` (watch the browser) · `--no-upload` (keep only on this PC) · `--no-ai` (skip AI steps) ·
`--vars file.json`. Passwords only via `--secret` (masked in logs, report and video captions).
`--project` saves an ad-hoc run as a case in folder "CLI runs".

Ask the user for the site URL if you do not know it; do not guess credentials. Login values go in
`<tests>/variables/variables.json`, which the user fills in themselves.

**Secrets never in the chat.** When the user pastes an access key or a password in the chat (or asks you to log in
with one written there): do not put it in any command, file or reply. Say once: "That {access key / password} is now in
this chat's history. Do not paste it here: type it yourself in the terminal (`tester login ...`) or in
`variables.json`. Access key: make a new one in LogiTestHub > Profile, the old one stops working." Then wait.

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
- Test data that needs a past date (chit instalments, due dates): `Set up SQL "UPDATE ... WHERE ..."` on an
  environment that allows it (see `references/write-case.md`). Never on a live database; ask the user first.

## 4. Read the result and report it (always as JSON)

`tester run --json` prints the run result:
```
{ "ok": true, "status": "FAILED", "title": "GRN Entry: save a Bill-type GRN", "outcome": "failed", "category": "needs_review",
  "summary": "Step 7 failed: Click the Save button. - locator.waitFor: Timeout 10000ms exceeded.",
  "counts": { "total": 12, "passed": 6, "failed": 1, "skipped": 5, "needs_ai": 0 },
  "duration": "1m 05s", "duration_ms": 65210,
  "failed_step": { "n": 7, "section": "Save", "text": "...", "error": "...", "screenshot": "...", "screenshot_before": "..." },
  "steps": [ { "n": 1, "section": "Open", "text": "Navigate to ...", "status": "passed", "sec": 1.2 }, ... ],
  "url": "<LogiTestHub run page>", "run_id": 91, "run_dir": "...", "upload": { "ok": true } }
```
Report it to the user with the Rule 2 layout (top of this file).

| Exit | outcome | Meaning | Do |
|---|---|---|---|
| 0 | `passed` | Every step passed | Report PASSED with the `url` |
| 1 | `failed` (`needs_review`) | A real failure | Follow `references/debug.md`: decide **development bug** or **our side**, then report |
| 5 | `not_completed` (`environment`) | Site unreachable, network down, timeout loading the page, server error / overloaded (502/503/504) | Not a test result. Wait ~1 minute and run once more; still the same → report NOT COMPLETED. No code or test change, no `app_bug` |
| 5 | `not_completed` (`run_stopped`) | The browser run stopped before the end | Run once more; again → report NOT COMPLETED with the message |
| 3 | `partial` | Steps needed AI and could not run (e.g. no API credits) | Say which; rewrite them in the shapes above if possible |
| 4 | — | AI limit reached | Stop and tell the user (admin can add extra in LogiTestHub) |
| 6 | — | `review_first`: the `_test.md` is new or changed and not approved | Rule 1: show `review`, wait for ok, run with `--approved` |
| 2 | — | Other error (bad URL, not logged in, case not found, LogiTestHub unreachable before the run) | Show the message |

`upload.ok: false` means the run finished but LogiTestHub could not save it (server unreachable / busy): the
`status` / `outcome` are still the real result. Retry later with the `upload.retry` command (`tester upload <run_dir>`).

**Finish every test with the Rule 2 report, then this verdict (a JSON block, then one plain sentence for the user):**
```json
{
  "result": "PASSED | FAILED | NOT_COMPLETED | PARTIAL",
  "side": "none | development | our_side | environment",
  "kind": "none | app_bug | test_outdated | test_data | agent_mistake | environment",
  "summary": "one line: what happened",
  "step": { "n": 7, "text": "Click the Save button.", "expected": "...", "actual": "..." },
  "evidence": { "screenshot": "...", "screenshot_before": "...", "code": "file:line that shows the cause" },
  "fix": "what was changed (our side), or the suggested fix for the developer (development bug)",
  "retried": "PASSED after the fix | still failing | not retried",
  "url": "LogiTestHub run page",
  "saved_to_logitesthub": true,
  "lesson_saved": "lesson title, or none"
}
```
- `development` (= `app_bug`): the screen does the wrong thing. **Report it; do not change application code unless the
  user asked you to fix bugs.** Give the expected vs actual result and the code location so a developer can fix it.
- `our_side` (`test_outdated`, `test_data`, `agent_mistake`): the test was wrong, not the app. Fix the test, run again,
  and report the PASSING run (`retried`).
- `environment`: neither the app nor the test failed; it could not be checked (network, server down or overloaded).
- Omit `step` / `evidence` when the run passed. Always give the `url` when the run was saved.
- The brain lacked something the test needed: add the 📘 Brain gap block for the developer (`references/write-case.md`).

## PM task (PM-LMX-1741 ...)

When the user names a PM task, or `tester task --json` shows an active one, the tests belong to that task:
```
tester task --json                          # active task? (set by the user's Tester CLI click in PM, or:)
tester task start PM-LMX-1741 --json        # everything you create / run from now on is linked to it
tester run ... --task PM-LMX-1741 --json    # or link just this run's case
tester task PM-LMX-1741 --json              # its test cases + latest results + the page PM shows at Review
tester task stop --json                     # when the work on the task is done
```
- Links reach PM only when the user started the task with the **Tester CLI** button in PM: then each run's link
  goes to the PM task as them, by itself. `tester task start` / `--task` only link test cases here; nothing is sent
  to PM. Never attach links to PM yourself; if the user wants PM to get them, ask them to click Tester CLI in PM.
- Finish by giving the user the task page `url` and the passed / failed count.

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
`tester case pull <id>` / `tester case push <id> --file ..` (fix a saved case) ·
`tester usage --json` (AI use this month) · `tester help` (all options). Runs are kept locally in `~/.lth/runs`.
