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

test('a quoted value with spaces and a hint runs without AI (lesson 199)', () => {
  for (const s of ["Type 'ZT TEST ONE' into the Name field (#name).", 'Type "ZT TEST ONE" into the Name field (#name).']) {
    const a = parseStep(s);
    assert.strictEqual(a.kind, 'type', s);
    assert.strictEqual(a.value, 'ZT TEST ONE');
    assert.strictEqual(a.css, '#name');
  }
  assert.strictEqual(parseStep('Type a numeric HSN code into the HSN field (#hsn).').kind, 'unknown');
});

test('is not visible with a hint runs without AI (lesson 115)', () => {
  const a = parseStep("Assert the dropdown showing 'Select Bank' (#upi_type_0 .id_bank) is not visible.");
  assert.strictEqual(a.kind, 'assertHidden');
  assert.strictEqual(a.css, '#upi_type_0 .id_bank');
});

test('no source file holds a lost regex back-reference (a raw 0x01 byte)', () => {
  const fs = require('node:fs'), path = require('node:path');
  for (const f of ['lib/actions.js', 'run.js']) {
    assert.ok(!fs.readFileSync(path.join(__dirname, '..', f), 'latin1').includes('\x01'), f);
  }
});

test('login: none and a case with its own sign-in skip the helper (lesson 107)', () => {
  const { skipsLogin } = require('../lib/testcase');
  assert.ok(skipsLogin('---\nlogin: none\n---\n\n# T\n\nWait 2 seconds.\n'));
  assert.ok(skipsLogin('# T\n\n## Sign in\nWait 2 seconds.\n'));
  assert.ok(!skipsLogin('---\ncase_id: 7\n---\n\n# T\n\nWait 2 seconds.\n'));
});

test('several hints in one sentence are several actions (AI)', () => {
  assert.strictEqual(parseStep('Fill Metal (#metal), Tax Group (#tax_group) and Save.').kind, 'unknown');
});

test('a CSS selector not starting with # or . fails clearly (run 37: it was looked for as text)', () => {
  for (const s of ["Click the AI Usage link (a.side-item[title=\"AI Usage\"]).", 'Type 5 into the Pcs field (input[name=pcs]).']) {
    assert.strictEqual(parseStep(s).kind, 'badHint', s);
  }
});

test('words in brackets are not a selector', () => {
  assert.strictEqual(parseStep('Click the Save button (e.g. the green one).').kind, 'click');
  assert.strictEqual(parseStep("Assert the text 'v2.0' is visible (v2.0).").kind, 'assertText');
});

test('a BOM at the start of a case does not hide its front matter (TC-8)', () => {
  const { parseCase } = require('../lib/testcase');
  const tc = parseCase('﻿---\ncase_id: 7\n---\n\n# Title\n\n## S\nWait 2 seconds.\n', '.');
  assert.strictEqual(tc.title, 'Title');
  assert.strictEqual(tc.steps.length, 1);
});
