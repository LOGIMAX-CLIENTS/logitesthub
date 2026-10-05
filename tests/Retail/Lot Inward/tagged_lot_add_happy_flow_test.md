---
tags: [lot-inward, tagged, positive, happy-flow]
---

# Add a tagged lot with one line item (happy flow)

## Open the add-lot form
Navigate to {{base_url}}index.php/admin_ret_lot/lot_inward/add.
Assert the page title contains 'Lot'.

## Lot header (Stock Type stays Tagged)
Select the first available option in the Lot Received At dropdown (#lt_rcvd_branch_sel).
Select the first available option in the Gold Smith dropdown (#lt_gold_smith).
Select the first available option in the Category dropdown (#category).
Wait 2 seconds.
Select the first available option in the Purity dropdown (#purity).

## Line item
Select the first available option in the Product dropdown (#select_product).
Wait 2 seconds.
Select the first available option in the Design dropdown (#select_design).
Wait 2 seconds.
Select the first available option in the Sub Design dropdown (#select_sub_design).
Wait 1 seconds.
Type 1 into the Pcs field (#lot_pcs).
Type 10.5 into the GWT field (#lot_gross_wt).
Type 8 into the Purchase Wastage field (#lot_wastage).
Type 300 into the Purchase Mc field (#mc_value).
Type 11500 into the Purchase Rate field (#rate_per_gram).
Click the Add Item button (#add_lot_items).
Wait 2 seconds.

## Save the lot
Click the Save All button (#save_all).
Wait 3 seconds.

## The lot is saved in the database
Remember SQL "SELECT MAX(lot_no) FROM ret_lot_inwards WHERE created_on >= NOW() - INTERVAL 10 MINUTE" as last_lot.
Assert SQL "SELECT no_of_piece FROM ret_lot_inwards_detail WHERE lot_no = {{last_lot}}" returns '1'.
Assert SQL "SELECT gross_wt FROM ret_lot_inwards_detail WHERE lot_no = {{last_lot}}" returns '10.500'.
Assert SQL "SELECT wastage_percentage FROM ret_lot_inwards_detail WHERE lot_no = {{last_lot}}" returns '8.00'.
Assert SQL "SELECT making_charge FROM ret_lot_inwards_detail WHERE lot_no = {{last_lot}}" returns '300'.
