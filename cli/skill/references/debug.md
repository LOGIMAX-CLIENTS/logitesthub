# A test failed: find why, fix it, record the lesson

Never just re-run a failed test hoping it passes, and never "fix" a test by deleting the check that failed.

## 1. Read the evidence

- `report` / `url` from the JSON result: the failing step, its error and its screenshot.
- `run_dir/events.ndjson`: every step with its status and error, in order.
- Look at the screenshot of the failing step **and** of the step before it: most failures start one step earlier
  (a save that did not happen, a page that did not load, a popup that was not closed).

## 2. Decide the kind (one of five)

| Kind | Signs | Fix |
|---|---|---|
| `app_bug` | The screen does the wrong thing: wrong total, error message, NaN, data not saved, JS error | Fix the application code (follow the repo's CLAUDE.md), keep the test as it is |
| `test_outdated` | Element not found, text not visible, wrong page path: the screen changed or the step was wrong | Take the real id / text / path from the code (view, JS, controller) and correct the step |
| `environment` | Login failed, ERR_CONNECTION_REFUSED, ERR_ABORTED, timeout, server error 500 on every page, DB connection refused | Check URL (`host:port`, not `host/port`), server running, environment values; not a code or test change |
| `test_data` | "No records", nothing to select, tag already sold, stock 0, duplicate number | Use data that exists (Remember SQL from an earlier test, or a SELECT for an available record) |
| `agent_mistake` | The way the test was written or run was wrong: login steps in the case, guessed id, missing wait, wrong command flag, AI step where a fixed shape works | Rewrite the step in a supported shape; follow `write-case.md` |

How to tell `app_bug` from `test_outdated`: open the screen's code. If the code has the id / text the step expects and the
page still does not show it → app bug. If the code has a different id / text → the test is out of date.

## 3. Fix and prove it

- Make the smallest fix (code **or** test, not both unless both were wrong).
- Run the same test again. Only a PASS after the fix proves it. Also run the neighbouring test that shares the screen.
- If it still fails, go back to step 1 with the new evidence. After 3 tries, stop and tell the user what you found.

## 4. Record the lesson (every time a failure taught something)

```
tester lesson add --project <id|name> --kind <kind> \
  --title "<what went wrong, one line>" \
  --symptom "<the error / failing step as seen>" \
  --fix "<what you changed>" \
  --avoid "<the rule that stops it happening again>" \
  --case <case id> --run <run id> --json
```
- `--avoid` is what the next test (and the AI that writes tests) reads: write it as a rule, e.g.
  "Branch is a Select2 dropdown: use `Select 'X' from the Branch dropdown (#branch_select)`, not Click + Type".
- Same title again = the server counts it again instead of duplicating it, so reuse the title of an existing lesson
  (`tester lessons`) when it is the same mistake.
- Skip a lesson only for a pure one-off (a typo you made in this message). Flaky network once = no lesson; twice = lesson.
- Never put passwords, keys, tokens or customer data in a lesson.

Then tell the user: the kind, the cause in one sentence, the fix, the passing run `url`, and that the lesson was saved.
