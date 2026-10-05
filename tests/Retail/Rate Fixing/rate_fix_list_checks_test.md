---
tags: [rate-fixing, list, ui-standards]
---

# Rate Fixing list: date range, number format, export buttons and actions

## Open the list
Navigate to {{base_url}}index.php/admin_ret_purchase/rate_fixing/list.
Assert the page title contains 'Rate'.
Wait 2 seconds.

## Date range shown under the button
Click the date range button (#rf-dt-btn).
Click the Last 30 Days link.
Wait 2 seconds.
Assert the text 'Date Range:' is visible.

## Export buttons and page length (10 / 50 / 100 / All)
Assert the Excel button (.buttons-excel) is visible.
Assert the Print button (.buttons-print) is visible.
Assert the Column visibility button (.buttons-colvis) is visible.
Assert the page length dropdown (#payment_list_length select) contains '100'.
Assert the page length dropdown (#payment_list_length select) contains 'All'.

## Add page Cancel goes back to the Rate Fixing list
Navigate to {{base_url}}index.php/admin_ret_purchase/rate_fixing/add.
Wait 2 seconds.
Click the Cancel button.
Wait 2 seconds.
Assert the current URL contains 'rate_fixing/list'.
