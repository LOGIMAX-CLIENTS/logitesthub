---
tags: [tagging, positive, happy-flow]
# Run after TC-3 (Add a tagged lot): tags the lot TC-3 created ({{last_lot}}), 1 pc of 10.5 g.
---

# Tag the full weight of a tagged lot (happy flow)

## Open the add-tag form
Navigate to {{base_url}}index.php/admin_ret_tagging/tagging/add.
Wait 2 seconds.

## Pick the lot
Select '{{last_lot}}' from the Lot No dropdown (#tag_lot_received_id).
Wait 3 seconds.
Select the first available option in the Product dropdown (#tag_lt_prod).
Wait 2 seconds.
Select the first available option in the Section dropdown (#section_select).

## Tag details (one piece, the lot's full gross weight)
Select the first available option in the Design dropdown (#des_select).
Wait 2 seconds.
Select the first available option in the Sub Design dropdown (#sub_des_select).
Wait 2 seconds.
Select the first available option in the Quality Code dropdown (#quality_code).
Type 1 into the Pieces field (#tag_pcs).
Type 10.5 into the Gross Wt field (#tag_gwt).
Select the first available option in the Size dropdown (#tag_size).
Select the first available option in the Calc Type dropdown (#tag_calculation_based_on).
Click the Add button (#addTagToPreview).

## The tag is saved and the lot is fully tagged
Assert the text 'Tagging added successfully' is visible.

## The tag is in the database, available for sale
Assert SQL "SELECT COUNT(*) FROM ret_taging WHERE tag_lot_id = {{last_lot}}" returns '1'.
Assert SQL "SELECT gross_wt FROM ret_taging WHERE tag_lot_id = {{last_lot}}" returns '10.500'.
Assert SQL "SELECT tag_status FROM ret_taging WHERE tag_lot_id = {{last_lot}}" returns '0'.
