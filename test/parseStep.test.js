// Step shapes used by real test cases: each must be read the same way after any change to lib/actions.js.
// Add the step here when a case fails because the runner read it wrongly (TC-75: tr:first-child was not read).
const test = require('node:test');
const assert = require('node:assert');
const { parseStep } = require('../lib/actions');

const CASES = [
  // [step, kind, css]
  ["Click the cancel (X) button in the first bill row (#billing_list tbody tr:first-child .btn-warning).", 'click', '#billing_list tbody tr:first-child .btn-warning'],
  ["Assert the cancel (X) button of the first bill row (#billing_list tbody tr:first-child .btn-warning) is visible.", 'assertVisible', '#billing_list tbody tr:first-child .btn-warning'],
  ["Assert the first bill row's Id cell (#billing_list tbody tr:first-child td:first-child) shows '3036'.", 'assertValue', '#billing_list tbody tr:first-child td:first-child'],
  ["Click the third row (#list tbody tr:nth-child(3) .btn-edit).", 'click', '#list tbody tr:nth-child(3) .btn-edit'],
  ["Click the Save button (.btn.btn-primary).", 'click', '.btn.btn-primary'],
  ["Click the row button (#list > tbody > tr .btn-warning).", 'click', '#list > tbody > tr .btn-warning'],
  ["Type the keys 'ATM-25-SA-00010' into the bill list search box (#billing_list_filter input).", 'typeKeys', '#billing_list_filter input'],
  ["Click the Search button (#bill_search).", 'click', '#bill_search'],
  ["Select 'HEAD OFFICE' from the Branch dropdown (#branch_select).", 'select', '#branch_select'],
  ["Set the date range (#rpt_date_picker) from '10-01-2026' to '10-01-2026'.", 'dateRange', '#rpt_date_picker'],
  ["Assert the suggestion list (.ui-autocomplete li) is visible.", 'assertVisible', '.ui-autocomplete li'],
  ["Press Backspace in the Customer field (#cus_name).", 'press', '#cus_name'],
  ["Type '10.500' into the Gross Weight field (#gross_wt).", 'type', '#gross_wt'],
  ["Wait 2 seconds.", 'wait', null],
  ["Navigate to {{base_url}}index.php/admin_ret_billing/billing/list.", 'navigate', null],
  ["Remember SQL \"SELECT id FROM t WHERE a = '(#x)'\" as bill.", 'sqlRemember', null],
];

for (const [step, kind, css] of CASES) {
  test(step, () => {
    const a = parseStep(step);
    assert.strictEqual(a.kind, kind);
    if (css) assert.strictEqual(a.css, css);
  });
}

test('a hint the runner cannot read fails clearly, never goes to AI', () => {
  const a = parseStep("Click the Save button (#form [name='save']).");
  assert.strictEqual(a.kind, 'badHint');
  assert.ok(!a.needsAI);
});

test('several hints in one sentence are several actions (AI)', () => {
  assert.strictEqual(parseStep('Fill Metal (#metal), Tax Group (#tax_group) and Save.').kind, 'unknown');
});
