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
- Radio buttons come in three kinds - open the view and check which one before writing the step:
  | In the view | Example | Step |
  |---|---|---|
  | Bare radios sharing one id, no `<label>` | Lot Stock Type Tagged / Non-Tagged | `Click the Non-Tagged radio button.` (no id) |
  | Each radio has its own id | Branch Transfer type `#type1`..`#type5` | `Click the Purchase Items radio button (#type3).` |
  | `<label class="btn">` in a `data-toggle="buttons"` group | Cart List Order Place `#approve` | `Click the Order Place button (#approve).` |
- Inputs built by JS inside a table row (billing Packaging Box: Item Name, No of Pcs) have no label or id: use the
  class from the JS, `(.id_other_item)`, `(.no_of_pcs)`. Their lists stay empty until the Cost Centre (`#id_branch`)
  is chosen.
- Screens with a `form_secret` (Branch Transfer add / approval) answer `Form Already Submitted` when the first
  Navigate was slow: `Navigate`, `Wait 3 seconds.`, `Navigate` again, then enter data.
- The header has no 'Logout' text: the user menu (Profile / Sign out) is hidden until clicked; do not assert it.
- **Every autocomplete / suggestion field** (customer, karigar, product, account, employee search ...). Two kinds:
  - List made at page load (e.g. payment/add customer): it opens by itself after typing.
  - List made inside the AJAX search reply (e.g. account/add customer, scheme_account.js:259-300): it opens only on
    the NEXT key press. After fast typing there is none, so the list never opens and no wait helps (run 121 waited
    10 s). Press Backspace once after the reply - the tester's fix.
  - If the field searches on EVERY keyup (account/add), ArrowDown also re-runs the search: the box shows the id and
    Enter picks nothing (run 126). Click the suggestion instead.
  Use this for every such field (the Backspace is harmless on the first kind):
  ```
  Type the keys '...' into the X field (#id).
  Wait 2 seconds.
  Press Backspace in the X field (#id).
  Wait 2 seconds.                                   let that search reply land: a late reply undoes the pick
  Assert the suggestion list (.ui-autocomplete li) is visible.
  Click the suggestion link (.ui-autocomplete .ui-menu-item).
  Assert the <hidden id> field (#id_...) shows '{{the id}}'.     the pick really took
  ```
  Never click a suggestion by its visible text (the step cannot find it); use the CSS hint above.
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
- Dropdown options come from the table the controller's AJAX method reads, not from a setting: e.g. the reorder
  report's Sub Design list is `ret_sub_design_mapping` for the chosen product + design. Check the option exists there.
- Other inventory (packaging / gift items): items `ret_other_inventory_item` (`issue_to`), branch transfer lines
  `ret_branch_transfer_other_inventory` (`branch_transfer_id`, `id_other_inv_item`, `no_of_pcs`); header
  `ret_branch_transfer` (`transfer_item_type` 4 = packaging, `created_time`). Saving a transfer moves stock: read the
  available pieces with `Remember SQL` instead of hard-coding them.
- Chained tests: lot add remembers `last_lot`; tagging uses `Select '{{last_lot}}' from the Lot No dropdown (#tag_lot_received_id)`.

## Data safety
- Lot, tag, bill, transfer and cancel tests write real rows. Run them on Local / QA only, never on a client's live
  site unless the user asked for that exact run.
