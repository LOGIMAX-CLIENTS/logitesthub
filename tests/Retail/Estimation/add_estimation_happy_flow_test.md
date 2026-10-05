---
tags: [estimation, positive, happy-flow]
# Data (src_v3): tag GBT-00016 is available (tag_status 0) at HEAD OFFICE; customer VIVEK / 9629104630 has a full
# address (save is blocked by alert() if pincode/country/state is missing). Estimation does not change tag status,
# so this case can be re-run with the same tag.
---

# Add an estimation with one tagged item (happy flow)

## Open the add-estimation form
Navigate to {{base_url}}index.php/admin_ret_estimation/estimation/add.
Wait 2 seconds.

## Header details
Select 'HEAD OFFICE' from the Branch dropdown (#branch_select).
Wait 2 seconds.
Select the first available option in the Sales Employee dropdown (#emp_select).

## Pick the customer
Type 9629104630 into the Customer field (#est_cus_name).
Press End in the Customer field (#est_cus_name).
Wait 3 seconds.
Press ArrowDown in the Customer field (#est_cus_name).
Wait 3 seconds.
Press ArrowDown in the Customer field (#est_cus_name).
Press Enter in the Customer field (#est_cus_name).
Wait 2 seconds.

## Close the Customer Details popup (opens after picking a customer with purchase history)
Click the Close button.
Wait 1 second.

## Add the tag
Click the Tag checkbox (#select_tag_details).
Wait 1 second.
Type GBT-00016 into the New Tag Scan field (#est_tag_scan).
Click the tag search button (#tag_search).
Wait 3 seconds.
Assert the text 'GOLD BANGLES' is visible.

## Save
Click the Save and Print button (#est_print).
Wait 3 seconds.

## Skip the offer check (Save opens "Available Offers"; none apply to this tag)
Click the Skip Offers button (#offerSkipBtn).
Wait 4 seconds.

## Lands on the estimation list with a success message
Assert the current URL contains 'admin_ret_estimation/estimation/list'.
Assert the text 'Estimation added successfully' is visible.
