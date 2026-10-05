# eTail (Logimax retail jewellery ERP) - testing facts

Read when the project is **Retail** (eTail, PHP CodeIgniter 2, MySQL). Also read the eTail repo's own CLAUDE.md.

## Login and pages
- Login helper (runs first): opens `{{base_url}}index.php/admin/dashboard`, types `{{username}}` / `{{password}}`,
  selects the first branch in `#branch_select`, clicks Sign In, asserts title 'Dashboard'. Write no login steps.
- `{{base_url}}` ends with `/admin/` (e.g. `http://localhost/wt-prod/admin/`). Pages are
  `{{base_url}}index.php/<controller>/<method>[/add]`, e.g. `index.php/admin_ret_lot/lot_inward/add`,
  `index.php/admin_ret_tagging/tagging/add`. Take the path from the controller / menu link, not from memory.
- After login eTail redirects; a Navigate right after it can get ERR_ABORTED (the runner retries once).

## Screens
- Almost every `<select>` is **Select2** (the real select is hidden). Use `Select '<text>' from the X dropdown (#id)` or
  `Select the first available option in the X dropdown (#id)` - never Click the box and Type.
- Radio buttons (e.g. Stock Type Tagged / Non-Tagged) have no <label> and share one id: write
  `Click the Non-Tagged radio button.` (found by its text), never `(#stock_type)`.
- Dropdowns that depend on another (Category → Purity, Product → Design → Sub Design, Lot → Product) are filled by
  AJAX: `Wait 2 seconds.` after the parent choice.
- Line items are added to a preview table first (`Add Item` / `Add`), then saved with `Save All` (or similar). Check
  both: the row in the preview, then the database after the save.
- Messages come from JS (`$.toaster`, `alert`): copy the exact text from the view's JS file, including spaces before
  the full stop (eTail texts often look like `Warning! Enter Category Name .`).
- Weights show 3 decimals, amounts 2 decimals with Indian grouping (1,23,456.78). `NaN` / `undefined` on screen is a bug.

## Database (read-only checks)
- Lot: `ret_lot_inwards` (lot_no, created_on), lines `ret_lot_inwards_detail` (no_of_piece, gross_wt, wastage_percentage, making_charge).
- Tag: `ret_taging` (tag_lot_id, gross_wt, tag_status). **tag_status 0 = available, 1 = sold** (other values exist:
  check the model before asserting anything else).
- Bill types: 1 Sales, 2 Sales+Purchase, 3 Sales+Purchase+Return, 4 Purchase, 5 Order Advance, 7 Sales Return,
  8 Credit Collection, 9 Order Delivery, 10 Chit Pre-Close, 11 Repair Delivery, 13/14 Sales Transfer out/return, 15 Suspense.
- Find the newest record of this test with a time window: `... WHERE created_on >= NOW() - INTERVAL 10 MINUTE`.
- Chained tests: lot add remembers `last_lot`; tagging uses `Select '{{last_lot}}' from the Lot No dropdown (#tag_lot_received_id)`.

## Data safety
- Lot, tag, bill, transfer and cancel tests write real rows. Run them on Local / QA only, never on a client's live
  site unless the user asked for that exact run.
