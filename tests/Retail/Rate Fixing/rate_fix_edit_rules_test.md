---
tags: [rate-fixing, edit]
---

# Rate Fixing edit: an open entry loads for editing, an approved one is refused

## Open entry 198 (yet to approve)
Navigate to {{base_url}}index.php/admin_ret_purchase/rate_fixing/edit/198.
Wait 3 seconds.
Assert the Save button (#rate_fix_submit) shows 'Update'.
Assert the Fix Wt field (#item_details .fix_weight) shows '5.000'.
Assert the Rate field (#item_details .rate_fix_rate) shows '11800.00'.
Assert the text '10.322' is visible.

## Approved entry 195 cannot be edited
Navigate to {{base_url}}index.php/admin_ret_purchase/rate_fixing/edit/195.
Wait 2 seconds.
Assert the current URL contains 'rate_fixing/list'.
Assert the text 'Only rate fixing entries that are not approved or cancelled can be edited.' is visible.
