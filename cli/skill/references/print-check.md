# Check a print copy or PDF

The user gives a printed document - a PDF file, a photo / scan of a print, or a screenshot - from the app under test
(bill, estimation, receipt, chit payment slip, report export ...) and asks whether it is right. Check every value on it
against three things: **what the app saved** (the database), **the arithmetic on the document itself**, and **what
the print code says it should show**. Report each value as OK / MISMATCH / CANNOT READ, with the proof.

No browser and no test case is needed. Nothing is saved or changed: only `SELECT`s.

## 1. Read the document

- Open it with the Read tool (PDF: all pages; a photo or scan: look at it closely, zoomed parts if needed).
- Write down every value, exactly as printed (spacing, commas, decimals): header (company, branch, address, GSTIN),
  document type and number, date and time, customer / supplier (name, mobile, GSTIN), each line (item, HSN, pieces,
  gross / less / net weight, purity, rate, V.A, M.C, discount, taxable value, tax %, tax, line amount), sub total,
  each tax (CGST / SGST / IGST), round off, total, old metal / advance / chit adjustments, payment modes and amounts,
  balance, amount in words, footer (terms, "Original / Duplicate", signature lines).
- A photo or scan: a value you cannot read for sure is **CANNOT READ**. Never guess a digit.
- The PDF has no text layer (a scanned page): read it as an image, same rule.

## 2. Find what the app saved for it

- Ask which environment it came from if it is not clear (Dev / Stagging / ...). Never a live database without asking.
- Find the screen's print code in the application repo: the controller method behind the Print button and the view
  it renders (eTail: `grep -rn "<doc title or a label on the print>" application/views` and the controller that
  loads that view; bills: `billing_invoice()` in `admin_ret_billing.php` and `application/views/billing/print/`;
  receipts / slips made as PDF: `dompdf` / `mpdf` calls in the controller). The print view tells you **which column
  each printed value comes from** and how it is formatted (number_format, rounding, which tax label).
- Take table and column names from that code and the model, never guess. Then read the saved values:
  ```
  tester sql "SELECT bill_id, bill_no, bill_date, tot_bill_amount, ... FROM ret_billing WHERE bill_no = '00001'" --env Dev --json
  tester sql "SELECT ... FROM <detail table> WHERE <parent id column> = <bill_id>" --env Dev --json
  ```
  One statement, read-only, max 20 rows per query: page through lines with `LIMIT 20 OFFSET 20` when there are more.
- Settings that change the print (show HSN, tax inclusive, print format, branch-wise company details) are part of
  the truth too: read them (`tester settings --project <id> --env <name>` or the settings table the view reads).

## 3. Check

For every printed value, one of:

| Check | Example |
|---|---|
| **Saved value** - printed = what the database has (after the view's formatting) | net wt `10.500` = `ret_billing_items.net_wt` 10.500 |
| **Arithmetic** - the document adds up | line amount = taxable + tax; sub total = sum of lines; CGST = SGST = half the GST; total = sub total + tax ± round off; balance = total - payments |
| **Rule** - what the code / settings say must show | GSTIN of the billing branch, "Duplicate" on a reprint, amount in words = the total |

- Recompute with the same rounding the code uses (read it: `round(x, 2)`, `number_format`, round-off to the rupee).
- A difference of a paisa: say which rounding causes it, from the code. Do not call it OK silently.
- Amount in words: spell the total yourself and compare word by word (lakh / crore, "only", paise).

## 4. Report

A table, then the verdict:

```
| # | Field | On the print | Expected | From | Result |
|---|---|---|---|---|---|
| 1 | Bill No | 00001 | 00001 | ret_billing.bill_no | OK |
| 7 | CGST 1.5% | 255.00 | 127.50 | 8,500.00 x 1.5% | MISMATCH |
| 9 | Amount in words | Seventeen Thousand Only | Seventeen Thousand Rupees Only | total 17,000.00 | MISMATCH |
| 12 | Customer mobile | 98xxxx12 | - | photo blurred | CANNOT READ |
```

```json
{
  "result": "PASSED | FAILED | NOT_COMPLETED",
  "side": "none | development | environment",
  "kind": "none | app_bug | environment",
  "summary": "one line: e.g. 2 of 34 values wrong: CGST printed on the full amount, amount in words drops 'Rupees'",
  "document": "file name, type, number, environment",
  "mismatches": [{"field": "CGST 1.5%", "printed": "255.00", "expected": "127.50", "why": "view uses tot_bill_amount, not the taxable value", "code": "application/views/billing/print/x.php:812"}],
  "cannot_read": ["Customer mobile"],
  "fix": "suggested fix for the developer, with the code location"
}
```

- A mismatch is a **development** finding (`app_bug`): the print shows something other than what was saved or what
  adds up. Give the code line that prints it. Do not change application code unless the user asked for a fix.
- Saved data itself is wrong (the bill saved a wrong tax): say that it is the save, not the print, with the query.
- Could not reach the database / no access to that environment: `NOT_COMPLETED`, say what was still checked
  (arithmetic and rules need no database).

## 5. After the check

- Something worth catching every time (a tax label, amount in words, rounding): offer to add it to the screen's test
  case as steps, or record a lesson: `tester lesson add --kind app_bug ...` with the document type in the title.
