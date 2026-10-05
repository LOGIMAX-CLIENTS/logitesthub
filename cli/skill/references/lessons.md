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
