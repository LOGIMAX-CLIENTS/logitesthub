---
tags: [rate-fixing, add, validation, negative]
---

# Rate Fixing add: mandatory fields, decimals and balance weight checks

## Open the add page
Navigate to {{base_url}}index.php/admin_ret_purchase/rate_fixing/add.
Wait 2 seconds.

## Save with nothing selected
Click the Save button (#rate_fix_submit).
Assert the text 'Please select the Karigar..' is visible.

## Karigar chosen, PO missing
Select 'KISHOR LOGIMAX - 642' from the Karigar dropdown (#select_karigar).
Wait 2 seconds.
Click the Save button (#rate_fix_submit).
Assert the text 'Please select the PO Ref No..' is visible.

## PO chosen: the row and the mandatory marks
Select 'P-00072' from the PO Ref No dropdown (#select_po_ref_no).
Wait 3 seconds.
Assert the item row (#item_details tbody tr) is visible.
Assert the text '4.486' is visible.

## Decimal limits
Type the keys '1.23456' into the Fix Wt field (#item_details .fix_weight).
Press Tab in the Fix Wt field (#item_details .fix_weight).
Assert the Fix Wt field (#item_details .fix_weight) shows '1.234'.
Type the keys 'abc12.345' into the Rate field (#item_details .rate_fix_rate).
Press Tab in the Rate field (#item_details .rate_fix_rate).
Assert the Rate field (#item_details .rate_fix_rate) shows '12.34'.

## Rate missing
Type the keys '1' into the Fix Wt field (#item_details .fix_weight).
Type the keys '' into the Rate field (#item_details .rate_fix_rate).
Click the Save button (#rate_fix_submit).
Assert the text 'Row 1: Please enter the Rate..' is visible.

## Fix Wt above the balance weight is refused and the box is cleared
Type the keys '50' into the Fix Wt field (#item_details .fix_weight).
Press Tab in the Fix Wt field (#item_details .fix_weight).
Assert the text 'Fix Wt cannot be greater than Bal Wt (4.486)' is visible.
Assert the Fix Wt field (#item_details .fix_weight) shows ''.
