# Syncing an application's modules into LogiTestHub

A LogiTestHub project's repository is a tree: **module → sub-module**, the same as the application's own menu.
The tree should come from the application's **source of truth**, not from guessing: its menu table, or its
route / menu configuration in code. The IDE agent (Claude Code) reads that source, writes one file, and pushes it with
the `tester` CLI.

| Method | When | Reliability |
|---|---|---|
| **Modules file** (`tester modules build` / `push`, or "Import modules file" on the web) | You have the code or the database | Exact. **Main method.** |
| Read from the application (web, "Read menu") | No code / DB access, menu visible on screen | Good. Misses pages the login cannot see. |
| Type them (web) | Small or unusual apps | Manual |

**Push only ever adds.** Modules and sub-modules that are already in the project are left exactly as they are; items in
LogiTestHub that are not in the file are listed as "kept" and never deleted, renamed or moved. Pushing the same file twice
adds nothing the second time.

## The file: `logitesthub.modules.json`

Keep it in the application's repository root and commit it with the menu change that it describes.

```json
{
  "format": "logitesthub.modules/v1",
  "project": "Retail",
  "source": "eTail menu table (settings/menu)",
  "modules": [
    { "name": "Retail Catalog", "subs": [
        { "name": "Category", "link": "admin_ret_catalog/category/list" },
        { "name": "Product",  "link": "admin_ret_catalog/ret_product/list" } ] },
    { "name": "Dashboard", "link": "admin/dashboard" }
  ]
}
```

- `project`: the LogiTestHub project name (used when `--project` is not given).
- `modules[].name` is required; `link` is optional. A module with no sub-modules is fine (a direct page).
- `subs` may also be plain strings: `"subs": ["Category", "Product"]`.
- Deeper menus (3+ levels) become `"Group › Page"` sub-module names, the same names the screen reader uses.
- Names are trimmed; the same name twice (any case) is merged.

## Commands

```
tester login --server http://<logitesthub-pc>:5050 --username <you> --key <access key>   # once (Profile -> Access key)
tester modules build --rows menu.tsv --project <name> [column options]   # menu-table rows -> logitesthub.modules.json
tester modules push logitesthub.modules.json --dry-run                    # what WOULD be added (changes nothing)
tester modules push logitesthub.modules.json                              # add the new ones
tester modules pull --project <name> --out current.json                   # the tree LogiTestHub has now
```

`build` reads rows exported from any parent → child menu table: a JSON array, or TSV / CSV with a header line
(`mysql -B` output works as is). Column options, with their defaults:

| Option | Meaning | Default tried |
|---|---|---|
| `--id` | row id | `id`, `id_menu` |
| `--label` | name shown in the menu | `label`, `name`, `title` |
| `--parent` | parent row id (0 / empty = top) | `parent`, `parent_id` |
| `--link` | page path / URL | `link`, `url`, `path`, `route` |
| `--sort` | menu order | `sort`, `sort_order`, `position` |
| `--active` | only rows where this column is on (1 / true / yes) | (all rows) |
| `--root <id>` | the children of this row are the modules (a wrapper row like eTail's "Home") | (top rows) |

If the top level is a single wrapper row, `build` prints a hint with the right `--root`.
Captions with no page and no children (e.g. "Opening Master" with link `#`) are left out.

Permission: pushing needs "Edit test cases & folders" (Testers and Admins by default); anyone can pull. Every push is
in Settings → Audit Log.

## Recipes

### eTail (any client)
Menu table `menu` (`id_menu, label, link, parent, sort, active`), wrapper row `Home` = id 1.

```
mysql -B -e "SELECT id_menu,label,link,parent,sort,active FROM menu" <client_db> > menu.tsv
tester modules build --rows menu.tsv --project Retail --id id_menu --label label --parent parent \
    --link link --sort sort --active active --root 1 --source "eTail menu table (<client>)"
tester modules push logitesthub.modules.json --dry-run
tester modules push logitesthub.modules.json
```
No DB access? The same rows come from the app's own Menu settings JSON (logged in as admin):
`<base_url>index.php/settings/menu/ajax_list` → save the `data` array as `menu.json` and use `--rows menu.json`.

Tested 2026-10-01 against dev.retail: 523 rows → 58 modules, 442 sub-modules; all 56 modules / 428 sub-modules that the
screen reader had found were in it, plus Loyalty, NetSuite Sync Review and 14 pages the `developer` login cannot see.

### An app with a menu table (e.g. StarGold "Menu Master")
Find the table behind the app's menu admin screen, export id / label / parent / link / sort / active, then `build` with the
matching column names (and `--root` if `build` suggests it).

### An app whose menu is in code (React / Angular / Vue / Laravel ...)
The IDE agent reads the route or menu config (e.g. `src/config/menu.ts`, `routes/*.js`, `app-routing.module.ts`) and writes
`logitesthub.modules.json` directly in the format above: menu groups → modules, their pages → sub-modules, path → `link`.
Then `tester modules push --dry-run` and `push`.

## For the IDE agent

Paste [CLAUDE-modules-section.md](CLAUDE-modules-section.md) into the application's `CLAUDE.md` and fill in the
"Source of truth" line for that app.
