# Writing a test case that runs the first time

Before writing: run `tester lessons <project> --json` and follow every lesson.

Step 1 - the app's knowledge brain lives INSIDE the app repo the developer works in (e.g.
`C:\xampp\htdocs\etail_development_src\knowledge_brain`). From the repo run `tester brain <words of the test> --json`:
it lists the module docs to read (FUNCTIONAL_BRAIN, PERMISSION_MAP, SETTINGS_REGISTER, SCREENS, BUSINESS_RULES ...) and
the flow checklist, and says when a doc is OUT OF DATE (code changed after its source_ref, or a feature not committed
yet). Not found -> ask the user where knowledge_brain is, then `tester brain --path <folder>` (it is saved). No doc for
the module -> say so and work from the code. Out of date -> read the changed files; where the doc and the code disagree
the code is right; tell the user in one line which doc is out of date. Branches the user did not choose (payment
mode ...): ask them. Then read the screen's code.

**Brain gap -> tell the developer.** The developer keeps the brain up to date (their brain-update script, before they
test). When the test needed something the module's docs do not say, or say wrongly, and you took it from the code (a
mandatory field or row check, a setting, a filter, a message, a form order, a table), do not edit the brain yourself.
End your report with one block per gap:
```
📘 Brain gap - <Module>/<DOC>.md
Missing: <one line, general: "Touch and UOM are mandatory in every ticked item row">
Code: <file:line>
Add: <the table row or 2-4 lines to paste, in that doc's style>
```
Also when a failure was caused by a wrong / missing doc. After they update the brain, read the doc again before the
next run.

## 1. Take everything from the code, never guess

For the screen under test open (Glob / Grep / Read):
- the **controller method** (page path = `index.php/<controller>/<method>` or the route in `routes.php`),
- the **view** it loads (field ids, labels, button ids, dropdown option texts),
- the **JS** the view uses (validation messages, what happens on save, AJAX success / error texts),
- the **model** (table and column names for SQL checks).

Use the real id in the step: `Type '10.500' into the Gross Weight field (#gross_wt).` A `(#id)` hint makes the step
exact and AI-free. Never invent an id; if the element has no id, use its visible label / button text.
A hint may use ids, classes (`.a.b`), tag names, `:first-child` / `:last-child` / `:nth-child(n)` and `>` only, e.g.
`(#billing_list tbody tr:first-child .btn-warning)`. Never `[attr]` or quotes: the runner fails such a step, and
`tester check` reports it before the run.

**Pick the most stable hint, in this order** (TC-13: a `*` found by `.col-sm-12 > .row > .col-sm-2:nth-child(5) > label
> .error` could have matched the item row's `*` and passed while the fix was missing):
1. `(#id)` of the element itself.
2. A class that only that element has (check with Grep in the view / JS that it is unique on the page).
3. A short path from the nearest id: `(#lot_form .category_req)`, `(#billing_list tbody tr:first-child .btn-warning)`.
4. Only when nothing else exists: a position path (`nth-child`, long `>` chains). Then say in the review, on that step:
   "position-based: a new field or another column order breaks it, and it may match another row".
When the element has no id and it is in code the developer is changing now (their own uncommitted fix, a new screen),
ask: "Add `id="<name>"` to it in your change? The test then finds exactly that element." Add it only after their yes,
and show the diff with the test. Never add ids to code nobody is changing for this task.

**A test for a bug fix must check the fix itself.** Before writing it, say in one line what the fix changes on the
screen (e.g. "Non-Tagged now shows `*` on Category, Purity, Gold Smith"). Put steps that check exactly that, and mark
them in the review: "⭐ steps 7-9 prove the fix: they fail without it". Steps that only check what worked before the fix
(an old validation message) may stay, but never as the only checks - such a test passes on the old code too.
Do not switch the developer's working tree to the old code to prove it (no `git stash` / checkout of their changes):
their uncommitted work could be lost.

## 2. Step shapes that run without AI

```
Navigate to {{base_url}}index.php/<controller>/<method>.
Type <value> into the <label> field.                  (+ (#id))
Type the keys '<text>' into the <label> field (#id).   one key at a time: for fields with key filters
Press Enter in the <label> field (#id).                real key events: keyup-driven autocompletes
Select '<option>' from the <label> dropdown.           Select2 aware (+ (#id))
Select the first available option in the <label> dropdown (#id).
Set the date range (#picker_id) from 'DD-MM-YYYY' to 'DD-MM-YYYY'.   list / report date filter (bootstrap daterangepicker)
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

- **Records taken from the database and then looked for on a list / report screen:** also Remember the record's
  date (DATE_FORMAT(<date col>,'%d-%m-%Y')), then set the screen's date filter to it before searching -
  lists default to today / this month / last 7 days, so an older record is not shown otherwise:
  Set the date range (#rpt_date_picker) from '{{rec_date}}' to '{{rec_date}}'.  then the screen's Search button.
- **Order before writing (stop when a step says it cannot run):** 1) `tester brain <words>` (app repo docs, out-of-date check); 2) `tester settings --project <id>
  --env <name> --json` - is the action allowed for this user here? If not, tell the user which setting to change
  (screen > field: now -> needed) and stop; 3) test data; 4) view / JS (the screen as those settings show it);
  5) write the case, then `tester check`.
- **Settings and permissions first.** Find in the code what the action depends on (profile permissions such as
  previous_bill_cancel, retail settings, the day-close date). Put an `Assert SQL ... returns '1'` per setting at the
  top of the case, and before the first run check them and the data picks with the read-only SQL. If a setting
  blocks it, tell the user in one line where to change it (screen > field) and the no-change alternative. eTail's
  "today" is the branch day-close date (ret_day_closing.entry_date), not CURDATE().
- **Finding a database record on a screen:** Before writing the steps that find a database record on a screen, read that screen's view + JS and list EVERY filter
  and search it applies (branch, date range, status, type, the list's own search box) and how it shows the record's
  number. Remember ALL of them from the database first - the record's branch, its date, and its number exactly as the
  screen shows it (e.g. CONCAT(branch short_name,'-',fin_year_code,'-SA-',sales_ref_no) = 'ATM-25-SA-00010', never only
  '00010': numbers repeat per branch) - then set each filter, Search, and check the row's Id.
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
