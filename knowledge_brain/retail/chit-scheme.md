# Chit scheme: join a scheme, pay instalments, complete and close
keywords: chit, scheme, schemes, savings scheme, instalment, installment, installments, monthly payment, due, join, scheme account, account opening, payment, pay, closing, close, maturity, amount to weight, aws, advance, pending due, thavanai

Source: eTail admin (CodeIgniter 2). Line numbers are from `C:\xampp\htdocs\wt-prod\admin\` (Local). The Dev branch
`Retail_1.1.1.0001` has the same logic, but its lines are shifted. All paths below go after `{{base_url}}` (`.../admin/`).

## Screens
| Step | Page | What it does |
|---|---|---|
| Scheme master | `index.php/scheme` (list), `index.php/scheme/add`, `index.php/scheme/edit/<id>` | Scheme settings. List columns: ID, Name, Code, Type, Amount, Installments, Advance, Advance Months, Status, Action |
| Join (open account) | `index.php/account/new` (list), `index.php/account/add` (form) | Creates a `scheme_account` for a customer |
| Pay an instalment | `index.php/payment/add` | Choose customer and account, then **Proceed**, payment mode, **Save** / **Save and Print** |
| Payment list | `index.php/payment/list` | Where the page goes after "Payment Success" |
| Close | `index.php/account/close/scheme/<id_scheme_account>` (from `account/new`, Action → Close) | Closing form (needs the customer OTP) |

**Join form** (`views/scheme/opening/form.php`):
- Fields: "Select Branch *" (only when branch settings are on), "Customer Name *" (search by mobile), "Scheme *",
  "Scheme A/c Name *", "Select Employee *". "Payment Amount" appears for flexible schemes only.
- **Save** sends AJAX to `account/save`.
- Success: "Scheme account <NAME> created successfully". The page then goes to `payment/add` (when it returns type 1)
  or back to `account/new`.
- Errors: "Unable to proceed your request, Please fill the required fields." and "Mandatory KYC documents are required
  for this scheme threshold."

**Payment form** (`views/payment/form.php`):
- Fields: "Customer Mobile *", "Scheme A/c No *" (lists only accounts with `active=1 AND is_closed=0`),
  "Select employee *", "Metal Rate *", "Received Amount *", then the **Proceed** button.
- After the account loads, the **"Can pay?"** cell shows **Yes** or **No**. On No the Save buttons are hidden.
- Success toast: "Payment Success". Mismatch error: "Please click proced. Received amount and Payment amount is not tallied."

## Form fill order
**Join, `account/add`** (run 112 will confirm):
1. Select Branch `#branch_select`. This reloads Scheme.
2. Customer Name `#mobile_number`: type the keys, ArrowDown, Enter. This reloads Scheme.
3. Wait 3 seconds.
4. Select Employee `#employee_select`.
5. Scheme `#scheme`, **last**. Then assert it shows the scheme id.
6. Scheme A/c Name (filled from the customer), then Save `#submit`.

**Payment, `payment/add`**:
1. Customer Mobile `#mobile_number`: type the keys, ArrowDown, Enter.
2. Wait 3 seconds.
3. Scheme A/c No `#scheme_account`. Wait for the "Can pay?" panel.
4. Select employee `#employee_select`.
5. Received Amount `#total_amt`, then Proceed `#proced`.
6. Payment mode row (Cash amount, or the + modal for card / cheque / net banking), then Save `#pay_save`.

## Rules
- **One normal instalment per calendar month** (monthly schemes, single payment chance). This is in
  `payment_model.php` `get_paymentContent()` around :2496:
  - When the account's last successful `payment.date_payment` is in the current month, the counter
    `currentmonthpaycount > 0` makes `allowed_due = 0`, so "Can pay?" = **No**.
  - The rule is calendar-month based, not a day count: a payment on 31 Jan allows the next one on 1 Feb.
  - No alert is shown for Amount / Amount-to-weight schemes; the Save buttons are simply hidden.
- **The check is in the browser page only.** `payment/SaveAll` does not check again (`admin_payment.php:1317-2210`).
- **`payment.date_payment` is always the server's current time** (`admin_payment.php:1645`). The Payment Date on
  the screen is ignored, so back-dated payments cannot be made from the screen.
- **Instalments paid:** `scheme_account.total_paid_ins`. For monthly schemes it counts the distinct months of
  `date_payment` (`payment_model.php:9381`). Once paid = total, "Can pay?" = **No**.
- **Completion does not close the account automatically.** `is_closed` stays 0 until staff close it on the Close page.
  - The Close link shows only when paid instalments ≥ 1 and the user has the closing permission.
  - The form needs the customer **OTP** ("Send OTP to Customer").
  - Save sets `is_closed=1, active=0, closing_date`.
  - Message: "Scheme account <no> closed successfully".
- **Advance / pending dues** work on the admin screen only when the scheme's "Allow Advance In" / "Allow Unpaid In"
  includes **Admin**.
- **Payment is also blocked** when maturity is passed (maturity type 2), payment is disabled, KYC is pending, or the
  CASH limit is reached ("Scheme Account No - X Reached CASH mode payment limit").

## Branches the tester must choose
- **Payment mode** (table `payment_mode WHERE show_in_pay=1`; Local values):
  - **Cash**: amount box.
  - **Credit card** / **Debit Card**: card modal with Card Name (RuPay / VISA / Mastro / Master), Type (CC / DC),
    Device, Card No, Amount, Approval No.
  - **Cheque**: Cheque Date, Bank, Branch, Cheque No, IFSC Code, Amount.
  - **Net Banking**: Type RTGS / IMPS / **UPI** / NEFT, then Bank or Device, Payment Date, Ref No, Amount.
    UPI is entered here; there is no separate UPI row.
  - **Voucher**: Voucher Code, Voucher Value.
  - Also: **Advance Adj** and mixed modes.
- **Scheme settings** that change the flow: type (Amount / Weight / Amount to weight / Flexible); cycle (Monthly /
  Daily / Days-duration with grace / One time); single or multiple payments per month; advance; pending dues;
  maturity type 1-4; pre-close; discount (first / one instalment / all); gift / voucher at joining; free first payment.

## Data (tables)
- **`scheme`**: `id_scheme, scheme_name, code, scheme_type` (0 Amount, 1 Weight, 2 Amount to weight, 3 Flexible),
  `amount, total_installments, installment_cycle` (0 Monthly, 1 Daily, 2 Days duration, 3 One time, 4 Custom),
  `payment_chances` (0 single, 1 multiple), `min_chance, max_chance, allow_advance, advance_months, allow_unpaid,
  unpaid_months, maturity_type, maturity_days, free_payment, active`.
- **`scheme_account`**: `id_scheme_account, id_scheme, id_customer, account_name, start_date, maturity_date, active`
  (1 open, 0 closed), `is_closed, total_paid_ins, scheme_acc_number, ref_no, id_branch, id_employee, closing_date,
  closing_weight, closing_amount`.
  - `scheme_acc_number` is NULL until the first payment, which also resets `start_date` to the first payment date.
- **`payment`**: `id_payment, id_scheme_account, id_scheme, date_payment, due_date, due_date_to, installment,
  due_type` (ND normal, PD pending, AD advance), `payment_mode` (CSH / CC / DC / CHQ / NB / VCH ...),
  `payment_amount, metal_rate, metal_weight, payment_status`.
  - `payment_status`: 1 Success, 2 Awaiting, 3 Failed, 4 Canceled, 5 Returned, 6 Refund, 7 Pending. Only 1 and 2
    count as paid.
- **`payment_mode_details`**: `id_payment, payment_mode, payment_amount, card_no, payment_ref_number, cheque_no, ...`
- **Amount to weight:** `payment.metal_weight = (amount − gst) / metal_rate`.

## Scheme 27 on Local (`src_v3`), as checked
"Amount To Weight Scheme" (code AWS): type 2, ₹10,000, 3 instalments, Monthly (cycle 0), single payment, no advance,
no pending, maturity type 1, maturity days 90, free payment 0, active.
- Payment 2 on the same day as payment 1 → "Can pay?" = **No**.
- After 3 payments → "Can pay?" = **No**, `total_paid_ins = 3`, account still open until it is closed.

## Clean up (test data a join + payments test leaves)
A joined account and its payments live in 4 tables (checked on account 846: scheme_account 1, payment 3,
payment_mode_details 3, payment_status 3). Delete children first, by the account the test remembered (`acc`):
```
## Clean up
Set up SQL "DELETE FROM payment_status WHERE id_payment IN (SELECT id_payment FROM payment WHERE id_scheme_account = {{acc}})".
Set up SQL "DELETE FROM payment_mode_details WHERE id_payment IN (SELECT id_payment FROM payment WHERE id_scheme_account = {{acc}})".
Set up SQL "DELETE FROM payment WHERE id_scheme_account = {{acc}}".
Set up SQL "DELETE FROM scheme_account WHERE id_scheme_account = {{acc}}".
```
Accounts with a gift / prize / KYC / voucher at joining also have rows in gift_issued, kyc, gift_card: add those
(WHERE id_scheme_account = {{acc}}) when the scheme has them.

## Test notes
- **Join form order matters (`account/add`).** Changing **Select Branch** (`scheme_account.js:3056-3061`) and picking
  the **customer** (`get_customer_detail` → `get_schemes`, :1409) both empty and refill the **Scheme** dropdown by
  AJAX, so a scheme chosen earlier is lost. Save then shows "Required Field Missing: Please fill out Scheme* to
  continue." (run 111).
  - Order to use: Branch → Wait → Customer → Wait 3 seconds → Employee → **Scheme last** → Wait.
  - Then `Assert the Scheme field (#scheme) shows '27'.` before Save.
- **Customer search (`#mobile_number`) on `account/add` and `payment/add` is keyup-driven.**
  - It searches by AJAX on keyup once 2 or more characters are typed (`scheme_account.js:259`, `payment.js:617`),
    then shows a jQuery UI autocomplete list.
  - A plain `Type ... into` step shows no list (run 110 failed this way). Pick the customer with keys.
  - **account/add:** the list is built inside the search reply and opens only on the next key, so after typing the
    whole number it never opens (run 121 waited 10 s, runs 119-120 picked nothing and Save said "Please fill out
    Scheme A/c Name"). The tester's fix, a Backspace after the reply, opens it:
    ```
    Type the keys '{{cus_mobile}}' into the Customer Name field (#mobile_number).
    Wait 2 seconds.
    Press Backspace in the Customer Name field (#mobile_number).
    Wait 2 seconds.
    Assert the suggestion list (.ui-autocomplete li) is visible.
    Click the customer suggestion link (.ui-autocomplete .ui-menu-item).
    Assert the customer id (#id_customer) shows '{{cus_id}}'.
    ```
    Not ArrowDown + Enter here: the page searches again on every keyup, so ArrowDown puts the id (e.g. 15009) in
    the box, rebuilds the list and Enter picks nothing (run 126). A keyboard-only user hits the same: minor app bug.
  - **payment/add:** the list is made at page load and opens while typing: Type the keys, Assert the suggestion
    list is visible, ArrowDown, Enter.
  - Selecting with the keyboard runs the page's `select` handler, which fills the hidden `#id_customer`.
    Never click the suggestion by its text.
- **Pick a customer:** use an active customer, `Remember SQL "SELECT id_customer FROM customer WHERE ... LIMIT 1"`.
  Take the column names from `customer_model` (*unconfirmed*: check the model before using them).
- **Remember the new account:**
  `Remember SQL "SELECT MAX(id_scheme_account) FROM scheme_account WHERE id_scheme = 27 AND id_customer = {{cust}}" as acc`.
- **Prove each payment:**
  `Assert SQL "SELECT COUNT(*) FROM payment WHERE id_scheme_account = {{acc}} AND payment_status = 1" returns '1'`
  (then '2', '3').
- **Time travel to pay the next instalment today** (Set up SQL, Local only). Before each next instalment, move all of
  this account's payments back one month, so the months stay distinct:
  ```
  Set up SQL "UPDATE payment SET date_payment = DATE_SUB(date_payment, INTERVAL 1 MONTH), due_date = DATE_SUB(due_date, INTERVAL 1 MONTH), due_date_to = DATE_SUB(due_date_to, INTERVAL 1 MONTH) WHERE id_scheme_account = {{acc}}".
  ```
  - `due_date` / `due_date_to` must move too: paid due ranges are skipped by `due_date` (`get_due_date()`,
    `payment_model.php:8899-9075`), so leaving them would keep this month "paid".
  - *Unconfirmed until the first run:* whether `scheme_account.start_date` must also move back. Check "Can pay?" =
    Yes after the update.
- **Reload `payment/add`** after a Set up SQL, and choose the account again: "Can pay?" is computed when the account loads.
- **Closing** needs an OTP, so a test can check completion (`total_paid_ins`, "Can pay?" = No) but cannot finish
  closing unless the OTP can be read from the database.
- **Known issues:**
  - The same-month block is client-side only.
  - The cheque IFSC is overwritten by a `$$` typo at `admin_payment.php:1846`.
