# Lessons that come with the Tester CLI

Mistakes seen while building and using the Tester CLI, and the easy ones to make. The project's own lessons are on
the server: `tester lessons <project> --json` (read them too - they are newer and more specific).

1. **Text matched a hidden element.** `Assert the text 'Dashboard' is visible` matched a hidden menu item.
   Avoid: assert text that is unique to the visible page area, or use `(#id)` with `is visible`.
2. **Navigate right after login → ERR_ABORTED.** The app was still redirecting. The runner now retries once; if it
   still fails add `Wait 2 seconds.` before the Navigate. Kind: `environment`, not an app bug.
3. **Login steps inside a case.** The login helper already signed in, so the case's own login steps find no login form.
   Avoid: never write login steps. Only login-page tests use `login: none`.
4. **Guessed element ids.** Avoid: take ids from the view / JS file (Grep the label text, read the `id=` next to it).
5. **Clicking a Select2 box and typing.** The hidden `<select>` does not change. Avoid: `Select ... from the X dropdown (#id)`.
6. **Dependent dropdown still empty.** Avoid: `Wait 2 seconds.` after choosing the parent (AJAX fills the child).
7. **Success toast but nothing saved.** Avoid: after a save, prove it with `Assert SQL`.
8. **Assert SQL value format.** MySQL returns `10.500`, not `10.5`. Avoid: compare with the exact text MySQL returns.
9. **Wrong URL `localhost/5050`.** Gives "connection refused". Host and port are joined with a colon: `localhost:5050`.
10. **Run without the project.** A run that did not get the project id skipped the login helper and every step failed
    on the login page. Avoid: always pass `--project <id>` (or run a saved case with `--case`).
11. **AI steps with no credits.** The run ends PARTIAL. Avoid: use the fixed step shapes in `write-case.md`.
12. **Test chain broken.** Tagging needs the lot the lot-add case remembered (`{{last_lot}}`). Avoid: run dependent
    cases in order (a Test Run keeps TC order) and say in the case header which case must run first.

## From the runs of 2026-10-06 .. 2026-10-08 (Retail)

13. **The fix stayed in the run, the case kept the bug.** Login values, radio ids, Order Place and the Sub Design step
    were fixed in the morning runs, then the evening runs failed on exactly the same steps. Avoid: after a fix passes,
    `tester case push <id> --file ..` (or save the `_test.md`) before you finish (SKILL.md, step 5).
14. **Login helper ran without values.** `{{username}}` was typed literally and step 7 failed with
    `Expected title contains "Dashboard", got "LOGIMAX | Login"` (4 runs). Avoid: pass `--var username=..
    --secret password=..` on every run; the CLI now refuses to start without them.
15. **Notes under the title became steps.** "Read-only report test ... Expected values are ..." ran as two AI steps
    and the run ended PARTIAL. Avoid: only step-shaped lines in the case body.
16. **Three kinds of radio, three shapes.** `Click the Purchase Items radio button.` not found (it has its own id
    `#type3`); `Click the Order Place radio button.` not found (it is a `<label class="btn">` in a button group, so
    `Click the Order Place button (#approve).`). Bare radios that share one id stay text only (`Non-Tagged`).
    Avoid: open the view and look at the `<input type="radio">` and its parent before writing the step.
17. **Row-built inputs have no label.** `Select 'HAND BAG' from the Item Name dropdown.` → "Dropdown not found";
    the packaging row is built by JS and its list is empty until Cost Centre is chosen. Avoid: choose the parent first
    and use the class from the JS: `(.id_other_item)`, `(.no_of_pcs)`.
18. **Stock used up by the previous run.** A test that saves Branch Transfer packaging hard-coded GIFT BOX / LUNCH BOX
    and `'exceeds available pieces (5)'`; the earlier run had moved that stock, the save was refused and
    `Remember SQL ... as bt_pack_id` "returned nothing". Avoid: `Remember SQL` the available pieces first and assert
    with `{{avail}}`; pick items that have stock with a SELECT; a test that saves must be safe to run again.
19. **"Remember SQL returned nothing" is not an SQL problem.** The save before it did not happen. Avoid: look at the
    screenshot of the save step (refused, `Form Already Submitted`, validation message) before changing the query.
20. **Option missing from a filtered dropdown.** `Select 'MACHINE BRACELET' from the Select Sub Design dropdown` timed
    out: the list comes from `ret_sub_design_mapping`, not from the reorder setting. Avoid: check the option exists in
    the dropdown's source table (read the controller's AJAX method) before selecting it.
21. **Same steps failed, then passed.** `Assert the text 'Available Pieces is' is visible.` timed out once and passed
    on the next run without a change. A toast / alert shows for a few seconds and its check fires on keyup or blur.
    Avoid: assert right after the action (no Wait in between), and run a fixed test twice before calling it fixed.
22. **Letters into a number field.** `Cannot type text into input[type=number]`: the browser refuses, the app never
    sees it. Avoid: test letter filtering only on text inputs; for number inputs assert the field stays empty.
23. **Wrong app at the URL.** Step 2 got the title "WinBrain — Knowledge Intelligence Platform": the URL pointed at
    another app on localhost. Avoid: kind `environment`; fix `--url` (check the login title first), not the test.
24. **Date range picker presets.** "Last 30 Days" / "Custom Range" in `.daterangepicker` could not be clicked by
    text or CSS hint (6 runs on 2026-10-03). Avoid: do not spend more than 2 runs on it; check the filtered rows with
    SQL, or report that the picker needs a step shape the runner does not have yet.
26. **Claude fixed a timing failure with a fixed Wait and called it fixed after one pass.** Run 110 failed on the
    autocomplete; the fix Claude wrote was `Wait 2 seconds.` + ArrowDown + Enter, it passed once (run 116) and was
    reported fixed; run 119 failed on the same pick. Avoid (agent_mistake): when a step failed because something had
    not appeared yet, never fix it with a fixed `Wait N seconds.` - wait for the thing itself (`Assert the ... (css)
    is visible.`). And a timing fix is proved only by TWO passing runs in a row; say "passed once, not proven" until then.
25. **Fixed wait before an autocomplete pick is flaky.** TC-69 passed one day and failed the next on the same steps:
    `Wait 2 seconds.` then ArrowDown + Enter; the list came late, nothing was picked, the next dropdown was empty
    ("has no options to pick"). Applies to EVERY suggestion field. Avoid: `Assert the suggestion list
    (.ui-autocomplete li) is visible.` between typing and ArrowDown + Enter.
