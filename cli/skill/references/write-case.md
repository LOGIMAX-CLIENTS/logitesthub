# Writing a test case that runs the first time

Before writing: run `tester lessons <project> --json` and follow every lesson. If the LogiTestHub folder has a flow
doc for the screen (`knowledge_brain/<project>/*.md`, e.g. `knowledge_brain/retail/chit-scheme.md`), read it: it lists
the rules, branches and tables. Branches the user did not choose (payment mode ...): ask them. Then read the screen's code.

## 1. Take everything from the code, never guess

For the screen under test open (Glob / Grep / Read):
- the **controller method** (page path = `index.php/<controller>/<method>` or the route in `routes.php`),
- the **view** it loads (field ids, labels, button ids, dropdown option texts),
- the **JS** the view uses (validation messages, what happens on save, AJAX success / error texts),
- the **model** (table and column names for SQL checks).

Use the real id in the step: `Type '10.500' into the Gross Weight field (#gross_wt).` A `(#id)` hint makes the step
exact and AI-free. Never invent an id; if the element has no id, use its visible label / button text.

## 2. Step shapes that run without AI

```
Navigate to {{base_url}}index.php/<controller>/<method>.
Type <value> into the <label> field.                  (+ (#id))
Type the keys '<text>' into the <label> field (#id).   one key at a time: for fields with key filters
Press Enter in the <label> field (#id).                real key events: keyup-driven autocompletes
Select '<option>' from the <label> dropdown.           Select2 aware (+ (#id))
Select the first available option in the <label> dropdown (#id).
Click the <text> button.                               (+ (#id))
Click the <text> radio button.  /  Click the <text> checkbox.   by the text next to it (eTail radios share one id)
Wait 2 seconds.                                        only after an action that reloads / AJAX-fills a list
                                                       (never before picking from a suggestion list: assert the list
                                                       is visible instead - it waits as long as needed)
Assert the text '<text>' is visible.
Assert the <label> field (#id) shows '<value>'.
Assert the <label> (#id) is visible.
Assert the page title contains '<text>'.  /  Assert the current URL contains '<text>'.
Remember SQL "<SELECT ...>" as <name>.
Assert SQL "<SELECT ...>" returns '<value>'.  /  returns a row  /  returns no rows
Set up SQL "<INSERT / UPDATE / DELETE ... WHERE ...>".   test data only, where the environment allows it
```

`Set up SQL` changes data so a scenario can be tested today, e.g. move the last chit payment back a month so the
next instalment can be paid now:
```
Remember SQL "SELECT MAX(id_payment) FROM payment WHERE id_scheme_account = {{acc}}" as last_pay.
Set up SQL "UPDATE payment SET date_payment = DATE_SUB(date_payment, INTERVAL 31 DAY) WHERE id_payment = {{last_pay}}".
```
- Only on an environment with *Allow "Set up SQL" steps* ticked (Settings → Environments; never a live database).
- One statement per step; UPDATE / DELETE need a WHERE on the test's own rows (an id you remembered); max 50 rows.
- Table / column names from the model code, never guessed. Every statement is in the audit log.
Anything else is an AI step (needs API credits, judged from a screenshot, slower and less exact). Keep AI steps few;
when credits are not available, use only the shapes above.

## 3. Rules

- **Form fill order:** fill fields in the order a user does on screen (top to bottom, parent before child). A parent
  dropdown (Branch, Customer ...) often reloads and clears the fields after it. Find the order: 1) the flow doc's
  "Form fill order"; 2) not there → the code (view field order, JS handlers that reload / empty other fields);
  3) only if neither tells you → ask the user. Assert earlier picks before Save.
- **No login steps.** The project's login helper runs first. Testing the login page itself → `login: none` front matter.
- `{{variables}}` for environment values (`{{base_url}}`, `{{username}}`, `{{branch}}` ...). Never real passwords or customer data.
- Sections: `## Open the screen`, `## Enter data`, `## Check the result`. 5-25 steps. Title = one clear sentence.
- After a save, check the database (Assert SQL) - a success toast alone does not prove the row was written.
- Values compare as text exactly as MySQL returns them (`10.500`, `8.00`).
- Dropdowns filled by AJAX after another choice: `Wait 2 seconds.` (or Assert an option is visible) before selecting.
- Exact texts: copy assertion texts from the code including punctuation and spacing (e.g. `Warning! Enter Category Name .`).
- A test that saves / cancels / approves creates real records: tell the user before running it, never on a live
  environment unless they asked.
- One behaviour per test case. A test that depends on an earlier one uses `Remember SQL ... as x` there and `{{x}}` here.
- No `NaN`, `undefined`, `Infinity` may appear on screen: when a screen calculates totals, assert the total text.
- **Only steps in the body.** Every sentence under a `##` section (or before the first one) is run as a step; an
  explanation or "expected values are ..." becomes an AI step. Put notes in the chat / PR.
- **Clean up what the test made.** A test that creates records (account, lot, bill, payment ...) ends with a
  `## Clean up` section of `Set up SQL "DELETE ... WHERE <id> = {{remembered id}}"` steps, children first. It runs
  even when a step before it failed; a step whose `{{value}}` was never set is skipped (so it never deletes the wrong
  rows). A clean-up that fails makes the run FAILED ("test data may be left behind"). When the test PASSES the data is kept
  instead (people look at it); before the next run LogiTestHub asks whether to delete it. CLI: `tester run --case <id>
  --clean` (delete it first) or `--keep`; with --json and neither flag the run stops and lists the kept data: ask
  the user which one, never pick for them. Only where Set up SQL is allowed;
  take the tables from the flow doc ("Clean up") or find every table that holds the record's id.
- **Safe to run twice.** A test that saves changes the data it started from (stock, counts, next number). Read those
  values with `Remember SQL` at the start and assert `{{name}}`, never a number copied from today's database.
- Number inputs (`type=number`) cannot take letters; check letter filtering only on text inputs.
- Before selecting an option, make sure it is in the source the dropdown is filled from (the AJAX method's table),
  for the parent values the test picks.
