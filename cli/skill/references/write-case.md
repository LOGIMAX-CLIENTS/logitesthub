# Writing a test case that runs the first time

Before writing: run `tester lessons <project> --json` and follow every lesson. Then read the screen's code.

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
Assert the text '<text>' is visible.
Assert the <label> field (#id) shows '<value>'.
Assert the <label> (#id) is visible.
Assert the page title contains '<text>'.  /  Assert the current URL contains '<text>'.
Remember SQL "<SELECT ...>" as <name>.
Assert SQL "<SELECT ...>" returns '<value>'.  /  returns a row  /  returns no rows
```
Anything else is an AI step (needs API credits, judged from a screenshot, slower and less exact). Keep AI steps few;
when credits are not available, use only the shapes above.

## 3. Rules

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
