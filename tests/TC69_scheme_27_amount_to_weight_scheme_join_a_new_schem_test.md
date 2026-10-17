---
case_id: 70
mode: testing
tags: [assistant]
---

# Scheme 27 (Amount To Weight Scheme): join a new scheme account for a customer and pay all 3 instalments by Cash, Credit card and Cheque until the scheme is complete

## Pick the test data
Remember SQL "SELECT id_customer FROM customer WHERE active = 1 AND mobile IS NOT NULL AND mobile <> '' AND id_customer NOT IN (SELECT id_customer FROM scheme_account WHERE active = 1 OR is_closed = 1) ORDER BY id_customer DESC LIMIT 1" as cus_id.
Remember SQL "SELECT mobile FROM customer WHERE id_customer = {{cus_id}}" as cus_mobile.
Remember SQL "SELECT CONCAT(firstname,'-',mobile) FROM customer WHERE id_customer = {{cus_id}}" as cus_label.
Remember SQL "SELECT amount FROM scheme WHERE id_scheme = 27" as sch_amt.
Remember SQL "SELECT DATE_FORMAT(CURDATE(), '%d-%m-%Y')" as chq_date.

## Join the customer to scheme 27
Navigate to {{base_url}}index.php/account/add.
Select the first available option in the Select Branch dropdown (#branch_select).
Wait 2 seconds.
Type the keys '{{cus_mobile}}' into the Customer Name field (#mobile_number).
Wait 2 seconds.
Press ArrowDown in the Customer Name field (#mobile_number).
Press Enter in the Customer Name field (#mobile_number).
Wait 3 seconds.
Select the first available option in the Select Employee dropdown (#employee_select).
Select 'Amount To Weight Scheme' from the Scheme dropdown (#scheme).
Wait 2 seconds.
Assert the Scheme field (#scheme) shows '27'.
Click the Save button (#submit).
Wait 3 seconds.
Remember SQL "SELECT MAX(id_scheme_account) FROM scheme_account WHERE id_scheme = 27 AND id_customer = {{cus_id}}" as acc.
Assert SQL "SELECT COUNT(*) FROM scheme_account WHERE id_scheme_account = {{acc}} AND id_customer = {{cus_id}} AND is_closed = 0" returns '1'.

## Instalment 1 - Cash
Navigate to {{base_url}}index.php/payment/add.
Type the keys '{{cus_mobile}}' into the Customer Mobile field (#mobile_number).
Wait 2 seconds.
Press ArrowDown in the Customer Mobile field (#mobile_number).
Press Enter in the Customer Mobile field (#mobile_number).
Wait 3 seconds.
Select the first available option in the Select Branch dropdown (#branch_select).
Wait 2 seconds.
Select the first available option in the Scheme A/c No dropdown (#scheme_account).
Select the first available option in the Select employee dropdown (#employee_select).
Wait 3 seconds.
Assert the 'Can pay?' value in the Scheme A/c Details panel shows 'Yes'.
Assert the Received Amount field (#total_amt) shows {{sch_amt}}.
Click the Proceed button (#proced).
Wait 2 seconds.
Type {{sch_amt}} into the Cash amount field (#make_pay_cash).
Press Tab in the Cash amount field (#make_pay_cash).
Press Space in the Save checkbox box (#pay_save).
Wait 5 seconds.
Assert SQL "SELECT COUNT(*) FROM payment WHERE id_scheme_account = {{acc}} AND payment_status = 1" returns '1'.

## Instalment 2 - Credit card
Set up SQL "UPDATE payment SET date_payment = DATE_SUB(date_payment, INTERVAL 1 MONTH), due_date = DATE_SUB(due_date, INTERVAL 1 MONTH), due_date_to = DATE_SUB(due_date_to, INTERVAL 1 MONTH) WHERE id_scheme_account = {{acc}}".
Set up SQL "UPDATE scheme_account SET start_date = DATE_SUB(start_date, INTERVAL 1 MONTH) WHERE id_scheme_account = {{acc}}".
Navigate to {{base_url}}index.php/payment/add.
Type the keys '{{cus_mobile}}' into the Customer Mobile field (#mobile_number).
Wait 2 seconds.
Press ArrowDown in the Customer Mobile field (#mobile_number).
Press Enter in the Customer Mobile field (#mobile_number).
Wait 3 seconds.
Select the first available option in the Select Branch dropdown (#branch_select).
Wait 2 seconds.
Select the first available option in the Scheme A/c No dropdown (#scheme_account).
Select the first available option in the Select employee dropdown (#employee_select).
Wait 3 seconds.
Assert the 'Can pay?' value in the Scheme A/c Details panel shows 'Yes'.
Assert the Received Amount field (#total_amt) shows {{sch_amt}}.
Click the Proceed button (#proced).
Wait 2 seconds.
Click the + button in the Credit card row of the payment modes table (#card_detail_modal).
Wait 2 seconds.
Click the ADD button (#new_card).
Select 'VISA' from the Card Name dropdown (.card_name).
Select 'CC' from the Type dropdown (.card_type).
Select the first available option in the Device dropdown (.id_device).
Type 411111111111 into the Card No field (.card_no).
Type {{sch_amt}} into the Amount field (.card_amt).
Type {{acc}}02 into the Approval No field (.ref_no).
Click the Save button (#add_newcc).
Wait 2 seconds.
Press Space in the Save checkbox box (#pay_save).
Wait 5 seconds.
Assert SQL "SELECT COUNT(*) FROM payment WHERE id_scheme_account = {{acc}} AND payment_status = 1" returns '2'.

## Instalment 3 - Cheque
Set up SQL "UPDATE payment SET date_payment = DATE_SUB(date_payment, INTERVAL 1 MONTH), due_date = DATE_SUB(due_date, INTERVAL 1 MONTH), due_date_to = DATE_SUB(due_date_to, INTERVAL 1 MONTH) WHERE id_scheme_account = {{acc}}".
Navigate to {{base_url}}index.php/payment/add.
Type the keys '{{cus_mobile}}' into the Customer Mobile field (#mobile_number).
Wait 2 seconds.
Press ArrowDown in the Customer Mobile field (#mobile_number).
Press Enter in the Customer Mobile field (#mobile_number).
Wait 3 seconds.
Select the first available option in the Select Branch dropdown (#branch_select).
Wait 2 seconds.
Select the first available option in the Scheme A/c No dropdown (#scheme_account).
Select the first available option in the Select employee dropdown (#employee_select).
Wait 3 seconds.
Assert the Received Amount field (#total_amt) shows {{sch_amt}}.
Click the Proceed button (#proced).
Wait 2 seconds.
Click the + button in the Cheque row of the payment modes table (#cheque_modal).
Wait 2 seconds.
Type {{chq_date}} into the Cheque Date field (.cheque_date).
Select the first available option in the Bank dropdown (.bank_name).
Type MAIN into the Branch field (.bank_branch).
Type {{acc}}03 into the Cheque No field (.cheque_no).
Type HDFC0000001 into the IFSC Code field (.bank_IFSC).
Type the keys '{{sch_amt}}' into the Amount field (.payment_amount).
Click the Save button (#add_newchq).
Wait 2 seconds.
Press Space in the Save checkbox box (#pay_save).
Wait 5 seconds.
Assert SQL "SELECT COUNT(*) FROM payment WHERE id_scheme_account = {{acc}} AND payment_status = 1" returns '3'.

## Check the scheme is complete
Navigate to {{base_url}}index.php/payment/add.
Type the keys '{{cus_mobile}}' into the Customer Mobile field (#mobile_number).
Wait 2 seconds.
Press ArrowDown in the Customer Mobile field (#mobile_number).
Press Enter in the Customer Mobile field (#mobile_number).
Wait 3 seconds.
Select the first available option in the Select Branch dropdown (#branch_select).
Wait 2 seconds.
Select the first available option in the Scheme A/c No dropdown (#scheme_account).
Wait 3 seconds.
Assert the 'Can pay?' value in the Scheme A/c Details panel shows 'No'.
Assert SQL "SELECT total_paid_ins FROM scheme_account WHERE id_scheme_account = {{acc}}" returns '3'.
Assert SQL "SELECT COUNT(*) FROM scheme_account WHERE id_scheme_account = {{acc}} AND is_closed = 0" returns '1'.
