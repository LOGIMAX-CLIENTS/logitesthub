## LogiTestHub modules sync

LogiTestHub (test management) arranges this application's test cases as **module → sub-module**, the same as the app's
menu. Keep it in sync from the source of truth, never by guessing from screenshots.

- **Source of truth for this app:** <menu table `…` with columns … / route file `…`>   ← fill in per application
- **File:** `logitesthub.modules.json` in the repo root (format `logitesthub.modules/v1`, see LogiTestHub
  `docs/MODULES_SYNC.md`). Commit it together with any menu change.
- **When the menu changes** (a page added, renamed or moved in the menu):
  1. Regenerate the file from the source of truth (`tester modules build --rows <export> …`, or write it from the route config).
  2. `tester modules push logitesthub.modules.json --dry-run` and show the user the "New modules / New sub-modules" lines.
  3. Only after the user agrees: `tester modules push logitesthub.modules.json`.
- Push only adds; it never deletes or renames in LogiTestHub. If something was renamed in the app, tell the user: the old
  name stays in LogiTestHub until someone renames or archives it there.
- Never put passwords, keys or customer data in the file: names and links only.
