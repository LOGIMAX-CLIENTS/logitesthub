// Claude-powered pieces: running a free-text step on the live page, and generating test cases.
// Model is overridable with ETAIL_AI_MODEL; defaults to Claude Opus 5.5.
const Anthropic = require('@anthropic-ai/sdk');

const MODEL = process.env.ETAIL_AI_MODEL || 'claude-opus-5-5';
let client = null;
const getClient = () => (client ||= new Anthropic());

// Opus 5.5 can decline a request on safety grounds; "default" fallbacks re-run it on
// another model inside the same call instead of failing the test run.
// ---------- provider: Anthropic API (credits) or the Claude subscription through Claude Code (testing) ----------
// LTH_AI_PROVIDER=claude-code (Settings -> System -> AI provider): text-only requests (Test Assistant, Generate with AI)
// run through the Claude Code CLI on this PC, signed in with a Claude subscription. Requests that carry a screenshot
// (free-text steps during a run) always use the API. Set LTH_CLAUDE_CLI to the claude executable if it is elsewhere.
function claudeCli() {
  if (process.env.LTH_CLAUDE_CLI) return process.env.LTH_CLAUDE_CLI;
  const path = require('path'), fs = require('fs');
  const guess = [
    process.env.APPDATA && path.join(process.env.APPDATA, 'npm', 'node_modules', '@anthropic-ai', 'claude-code', 'bin', 'claude.exe'),
    process.env.APPDATA && path.join(process.env.APPDATA, 'npm', 'node_modules', '@anthropic-ai', 'claude-code', 'bin', 'claude'),
    '/usr/local/bin/claude', '/usr/bin/claude',
  ].filter(Boolean);
  return guess.find(f => fs.existsSync(f)) || 'claude';
}

// codeDir: a read-only copy of the application's source (Code repository). Claude may then search and read files
// there - only Read / Grep / Glob, no shell, no edits - to take exact page URLs, field ids and messages from the code.
function askViaClaudeCode({ system, content, schema, codeDir }) {
  const { spawn } = require('child_process');
  const prompt = content.filter(b => b.type === 'text').map(b => b.text).join('\n\n');
  const tools = codeDir ? ['Read', 'Grep', 'Glob'] : [];
  const args = ['-p', '--output-format', 'json', '--json-schema', JSON.stringify(schema), '--system-prompt', system,
    '--tools', tools.join(','), '--no-session-persistence', '--setting-sources', ''];
  if (tools.length) args.push('--allowedTools', ...tools);
  if (process.env.LTH_CLAUDE_MODEL) args.push('--model', process.env.LTH_CLAUDE_MODEL);
  const limitMs = codeDir ? 420000 : 240000;   // reading code takes longer
  return new Promise((resolve, reject) => {
    // Claude Code prefers ANTHROPIC_API_KEY over the subscription sign-in: hide it, or "subscription" calls bill the API
    const env = { ...process.env };
    delete env.ANTHROPIC_API_KEY;
    delete env.ANTHROPIC_AUTH_TOKEN;
    const child = spawn(claudeCli(), args, { windowsHide: true, env, ...(codeDir ? { cwd: codeDir } : {}) });
    let out = '', err = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Claude Code took longer than ${limitMs / 60000} minutes`)); }, limitMs);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', d => { out += d; }); child.stderr.on('data', d => { err += d; });
    child.on('error', e => { clearTimeout(timer); reject(new Error(`Claude Code CLI not found (${e.code || e.message}). Install it or set LTH_CLAUDE_CLI.`)); });
    child.on('close', () => {
      clearTimeout(timer);
      let j;
      try { j = JSON.parse(out); } catch (_) { return reject(new Error('Claude Code: ' + ((err || out).trim().split('\n').pop() || 'no answer').slice(0, 200))); }
      if (j.is_error || !j.structured_output) {
        const msg = String(j.result || j.subtype || 'no structured answer');
        return reject(new Error(/log ?in|auth|credential|token/i.test(msg) ? 'Claude Code is not signed in on the LogiTestHub PC (run: claude, then /login)' : 'Claude Code: ' + msg.slice(0, 200)));
      }
      const u = j.usage || {};
      const model = Object.keys(j.modelUsage || {}).filter(k => !/haiku/i.test(k)).pop() || 'claude-code';
      resolve({ data: j.structured_output, usage: {
        input_tokens: u.input_tokens || 0, output_tokens: u.output_tokens || 0,
        cache_read_input_tokens: u.cache_read_input_tokens || 0, cache_creation_input_tokens: u.cache_creation_input_tokens || 0,
        via: 'claude-code', model: model.replace(/\[.*\]$/, '') } });
    });
    child.stdin.end(prompt, 'utf8');
  });
}

async function ask({ system, content, schema, effort, maxTokens, codeDir }) {
  if (process.env.LTH_AI_PROVIDER === 'claude-code' && !content.some(b => b.type === 'image')) {
    return askViaClaudeCode({ system, content, schema, codeDir });
  }
  // Streamed so long generations (large max_tokens) don't hit the SDK's request timeout.
  const res = await getClient().beta.messages.stream({
    model: MODEL,
    max_tokens: maxTokens,
    betas: ['server-side-fallback-2026-07-01'],
    fallbacks: 'default',
    system,
    output_config: { effort, format: { type: 'json_schema', schema } },
    messages: [{ role: 'user', content }],
  }).finalMessage();
  if (res.stop_reason === 'refusal') throw new Error('AI declined this request');
  if (res.stop_reason === 'max_tokens') throw new Error('AI response was cut off (max_tokens)');
  const text = res.content.filter(b => b.type === 'text').map(b => b.text).join('');
  return { data: JSON.parse(text), usage: res.usage };
}

// Friendly one-line reason for the report, without leaking request internals.
function explain(e) {
  if (e instanceof Anthropic.AuthenticationError) return 'AI: API key invalid';
  if (e instanceof Anthropic.RateLimitError) return 'AI: rate limited, retry later';
  if (e instanceof Anthropic.BadRequestError) return /credit balance/i.test(e.message) ? 'AI: no API credits' : 'AI: bad request';
  if (e instanceof Anthropic.APIConnectionError) return 'AI: cannot reach API';
  if (e instanceof Anthropic.APIError) return `AI: API error ${e.status}`;
  return `AI: ${e.message}`;
}

// ---------- 1. Free-text step on the live page ----------

const STEP_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['actions', 'verdict', 'reason'],
  properties: {
    actions: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['kind', 'index', 'value'],
        properties: {
          kind: { type: 'string', enum: ['click', 'type', 'select'] },
          index: { type: 'integer' },
          value: { type: 'string' },
        },
      },
    },
    verdict: { type: 'string', enum: ['pass', 'fail'] },
    reason: { type: 'string' },
  },
};

const STEP_SYSTEM = `You execute one step of a UI test on the eTail jewellery ERP (CodeIgniter, jQuery, Select2, DataTables).
You get the step text, a screenshot, and a numbered list of the visible interactive elements.
- For an action step, return the actions (click / type / select) using element indexes from the list, in order. Use at most 3 actions.
- For a check step ("verify", "assert", "should"), return no actions and judge from the screenshot and element list.
- verdict "fail" when the step cannot be done or the check is not true; say why in one short sentence in reason.
Never invent an index that is not in the list.`;

async function listElements(page) {
  return page.evaluate(() => {
    document.querySelectorAll('[data-ai-idx]').forEach(e => e.removeAttribute('data-ai-idx'));
    const sel = 'input:not([type=hidden]), select, textarea, button, a[href], [role=button], .select2-selection, .select2-choice';
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
      const isHiddenSelect = el.tagName === 'SELECT' && el.classList.contains('select2-hidden-accessible');
      if (!visible && !isHiddenSelect) continue;
      const id = out.length;
      el.setAttribute('data-ai-idx', String(id));
      const label = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
      out.push({
        index: id,
        tag: el.tagName.toLowerCase(),
        type: el.type || '',
        id: el.id || '',
        name: el.name || '',
        label: (label?.innerText || el.getAttribute('aria-label') || el.placeholder || '').trim().slice(0, 60),
        text: (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 60),
        options: el.tagName === 'SELECT' ? [...el.options].slice(0, 25).map(o => o.text.trim()) : undefined,
        select2: isHiddenSelect || undefined,
      });
      if (out.length >= 150) break;
    }
    return out;
  });
}

async function doAction(page, a) {
  const el = page.locator(`[data-ai-idx="${a.index}"]`).first();
  if (!(await el.count())) throw new Error(`AI picked element ${a.index}, which is not on the page`);
  if (a.kind === 'click') {
    await el.click();
    await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
  } else if (a.kind === 'type') {
    await el.fill(a.value);
  } else if (a.kind === 'select') {
    // Works for plain and Select2 selects: set the value, then fire jQuery change so eTail handlers run.
    await el.selectOption({ label: a.value }, { force: true });
    await el.evaluate(n => { if (window.jQuery) window.jQuery(n).trigger('change'); });
  }
}

// Asks Claude what to do for one step, given what the browser shows. No browser access here,
// so the LogiTestHub server can run it for a CLI whose browser is on another PC.
async function decideStep({ stepText, url, elements, shotB64 }) {
  if (remote()) return remoteStep({ stepText, url, elements, shotB64 });
  return ask({
    system: STEP_SYSTEM,
    effort: 'low',
    maxTokens: 4000,
    schema: STEP_SCHEMA,
    content: [
      { type: 'image', source: { type: 'base64', media_type: 'image/jpeg', data: shotB64 } },
      { type: 'text', text: `Step: ${stepText}\n\nPage URL: ${url}\n\nElements:\n${JSON.stringify(elements)}` },
    ],
  });
}

// `lth` CLI mode: the Anthropic key stays on the LogiTestHub server. The CLI sets LTH_SERVER / LTH_USER /
// LTH_KEY and the step decision is asked from the server (it applies the user's AI limit and records usage).
const remote = () => !!(process.env.LTH_SERVER && process.env.LTH_USER && process.env.LTH_KEY);

async function remoteStep(body) {
  const caseId = Number(process.env.LTH_CASE_ID) || undefined;
  const res = await fetch(process.env.LTH_SERVER.replace(/\/+$/, '') + '/api/v1/ai/step', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Basic ' + Buffer.from(`${process.env.LTH_USER}:${process.env.LTH_KEY}`).toString('base64'),
    },
    body: JSON.stringify({ ...body, case_id: caseId }),
    signal: AbortSignal.timeout(180000),
  });
  let j = {};
  try { j = await res.json(); } catch { /* not JSON: use the status below */ }
  if (!res.ok || !j.ok) throw new Error(String(j.message || j.error || `LogiTestHub server HTTP ${res.status}`).replace(/^AI:\s*/, ''));   // explain() adds 'AI:'
  // usage is recorded on the server for remote steps, so nothing is counted again here
  return { data: { actions: j.actions || [], verdict: j.verdict, reason: j.reason }, usage: {} };
}

async function aiStep(page, stepText) {
  const elements = await listElements(page);
  const shot = (await page.screenshot({ type: 'jpeg', quality: 70 })).toString('base64');
  const { data, usage } = await decideStep({ stepText, url: page.url(), elements, shotB64: shot });
  for (const a of data.actions) await doAction(page, a);
  if (data.actions.length) await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
  return { verdict: data.verdict, reason: data.reason, actions: data.actions, usage };
}

// ---------- 2. Test case generation ----------

const GEN_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['cases'],
  properties: {
    cases: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['slug', 'title', 'kind', 'why', 'sections'],
        properties: {
          slug: { type: 'string' },
          title: { type: 'string' },
          kind: { type: 'string', enum: ['positive', 'negative', 'edge'] },
          why: { type: 'string' },
          sections: {
            type: 'array',
            items: {
              type: 'object',
              additionalProperties: false,
              required: ['heading', 'steps'],
              properties: { heading: { type: 'string' }, steps: { type: 'array', items: { type: 'string' } } },
            },
          },
        },
      },
    },
  },
};

const GEN_SYSTEM = `You write UI test cases for eTail, a jewellery retail ERP (PHP CodeIgniter admin panel, jQuery, Select2 dropdowns, DataTables).
Write steps a browser runner can execute. Prefer these exact sentence shapes, which run without AI:
- Navigate to {{base_url}}index.php/<controller>/<method>.
- Type <value> into the <label> field.
- Select '<option>' from the <label> dropdown.
- Click the <text> button.
- Assert the page title is '<title>'. / Assert the page title contains '<text>'.
- Assert the current URL contains '<text>'.
- Assert the text '<text>' is visible.
Other plain-English checks are allowed when needed; an AI will judge them from a screenshot.
Rules:
- Sign-in: if login steps are given to you, every case starts with a "Sign in" section using exactly those steps.
  If none are given, the runner signs in by itself before every case: write NO login steps and start on the page under test.
- Use {{variable}} placeholders from the provided variable names instead of real data; never invent secrets.
- Cover the change: positive, negative (validation, wrong input) and edge cases, plus a regression check of the existing flow it touches.
- Keep each case independent and 5-20 steps. slug = short kebab-case.
- "why" = one sentence naming what in the change this case protects.`;

async function generateCases({ requirement, diff, variableNames, loginSteps, count, lessons }) {
  const parts = [
    `Requirement / bug / task:\n${requirement}`,
    loginSteps ? `Login steps to start every case with:\n${loginSteps}` : 'No login steps: the runner signs in before every case (project login helper).',
    `Available variables: ${variableNames.join(', ')}`,
    `Write about ${count} test cases.`,
  ];
  if (lessons) parts.push(lessons);   // "Lessons learned in this project ..." from LogiTestHub
  if (diff) parts.push(`Code change (git diff) the tests must cover:\n${diff}`);
  const { data, usage } = await ask({
    system: GEN_SYSTEM,
    effort: 'medium',
    maxTokens: 32000,
    schema: GEN_SCHEMA,
    content: [{ type: 'text', text: parts.join('\n\n') }],
  });
  return { cases: data.cases, usage };
}

// ---------- 3. Test Assistant chat (LogiTestHub web): scenario in plain words -> understanding + draft test case ----------

const CHAT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    reply: { type: 'string' },
    understanding: {
      type: 'object',
      additionalProperties: false,
      properties: {
        screen: { type: 'string' }, module: { type: 'string' }, sub_module: { type: 'string' },
        goal: { type: 'string' }, expected: { type: 'string' }, test_data: { type: 'string' },
      },
      required: ['screen', 'module', 'sub_module', 'goal', 'expected', 'test_data'],
    },
    questions: { type: 'array', items: { type: 'string' } },
    // choices the user picks with a click (payment mode per instalment ...): options come from the code / data
    choices: { type: 'array', items: {
      type: 'object', additionalProperties: false,
      properties: { question: { type: 'string' }, options: { type: 'array', items: { type: 'string' } } },
      required: ['question', 'options'] } },
    draft: {
      anyOf: [
        { type: 'null' },
        {
          type: 'object',
          additionalProperties: false,
          properties: {
            title: { type: 'string' },
            steps: { type: 'array', items: {
              type: 'object', additionalProperties: false,
              properties: { section: { type: 'string' }, text: { type: 'string' } }, required: ['section', 'text'] } },
          },
          required: ['title', 'steps'],
        },
      ],
    },
    lesson: {   // after explaining a failed run: what the next test must do differently (saved by the user's click)
      anyOf: [
        { type: 'null' },
        {
          type: 'object', additionalProperties: false,
          properties: {
            kind: { type: 'string', enum: ['app_bug', 'test_outdated', 'environment', 'test_data', 'agent_mistake'] },
            title: { type: 'string' }, symptom: { type: 'string' }, fix: { type: 'string' }, avoid: { type: 'string' },
          },
          required: ['kind', 'title', 'symptom', 'fix', 'avoid'],
        },
      ],
    },
  },
  required: ['reply', 'understanding', 'questions', 'choices', 'draft', 'lesson'],
};

const CHAT_SYSTEM = `You are the Tester Assistant inside LogiTestHub, the team's test manager. A tester describes a scenario in their own
words (often Tamil, Tanglish or English); you turn it into ONE runnable UI test case for the project's web application.

Always fill "understanding" with what you understood, in plain words (screen = the page; module / sub_module = names from the
project's module list when one matches, else ""; goal; expected result; test_data = the values the test will use).
"reply" is read by a manual TESTER who does not know the code. Write it like a friendly chat (ChatGPT style), in the user's
own language and style (Tanglish if they wrote Tanglish, else plain English), with a few emojis, short lines, never a paragraph:
- Talk only about what happens ON THE SCREEN, in everyday words: "customer name type pannumbodhu list varala",
  "Branch maathinaa Scheme clear aagudhu".
- NEVER put in "reply": file names, line numbers, function names, SQL, table / column names, element ids (#...),
  CSS classes, or words like keyup, AJAX, widget, select2, minLength, DOM. (Those belong only in the draft steps.)
- No list of files you read ("Paathen: ...", "Files: ...") - never.
- A simple request: 1-3 short sentences.
- Explaining a failed run: at most 4 lines, nothing else:
    🏷️ <type>  - exactly one of: 🐞 Development bug (the app does the wrong thing - a developer must fix it) /
       🧪 Test issue (our steps were wrong - you fixed the draft) / 📘 Flow doc wrong or missing (the knowledge_brain doc
       misled us - say what to correct in it) / 🌐 Environment (wrong "Run on", site down, missing test data)
    ❌ <the problem in one line, on the screen>
    ✅ <the fix in one line>  (for a Development bug: what the developer must fix)
    ⚠️ <only if the tester must act, e.g. "Run on → Local">
  Several problems: one 🏷️/❌ line pair each, then one ✅ line; still short.
- Asking something: 🤔 one short question per line, at most 2.
- Never shrink the scenario to fit the "Run on" environment, and never ask the user to type values the database would give
  (customer mobile, amounts, ids). If the chosen "Run on" cannot run the database / Set up SQL steps the scenario needs,
  keep the full draft with those steps and write exactly one line: ⚠️ Run on → <environment that allows it, e.g. Local>.
- Never show {{variables}} or step syntax in "reply".
Never claim a test passed: you only prepare it; the user runs it.

Write "draft" when you know enough to write a useful test; otherwise draft = null and ask 1-3 precise "questions"
(which screen, which values, what should happen). When the user asks for a change, return the complete updated draft.

Steps run in a browser. Prefer these exact shapes (they run without AI):
- Navigate to {{base_url}}<path>.   (take <path> from the module list links when one matches the screen)
- Type <value> into the <label> field.        or  ... field (#id) when the element id is known from the code context
- Select '<option>' from the <label> dropdown.
- Click the <text> button.
- Assert the text '<text>' is visible. / Assert the page title contains '<text>'. / Assert the current URL contains '<text>'.
- Select the first available option in the <label> dropdown (#id).  /  Click the <text> radio button.  /  Wait 2 seconds.
Database steps - ONLY when the project context says the environment has them:
- Remember SQL "<one SELECT>" as <name>.   then use {{name}} in later steps (e.g. the id of the record this test created)
- Assert SQL "<one SELECT>" returns '<value>'.  /  ... returns a row.  /  ... returns no rows.
- Set up SQL "<one INSERT / UPDATE / DELETE ... WHERE ...>".   test-data setup, e.g. move the last payment date back a month
  so the next instalment can be paid today. UPDATE / DELETE always with a WHERE on this test's own rows ({{name}}).
  Table / column names must come from the model code, never guessed; if you cannot see them, ask.
Other plain-English checks are allowed when needed (an AI judges them from a screenshot) - keep them few.
Rules:
- Sign-in: if the context says the project has a login helper, write NO login steps (it runs first). Otherwise start with a
  "Sign in" section using {{username}} / {{password}} placeholders (never real passwords).
- Use {{variable}} placeholders for environment values; never invent credentials or customer data.
- Group steps in sections ("Open the screen", "Enter data", "Check the result"). 5-25 steps. Title = one clear sentence.
  A scenario that repeats an action (pay every instalment of a scheme, approve several levels) may have up to 70 steps:
  one section per repetition ("Instalment 1", "Instalment 2", ...).
- Users speak briefly ("scheme 27 la oru customer choose panni 3 month complete pannu"). Fill in the rest yourself from
  the code and the database instead of asking: pick existing test data with Remember SQL (e.g. an active customer:
  SELECT ... LIMIT 1), remember the ids the test creates, and prove each save with Assert SQL. Ask a question only when
  the answer cannot be found in the code or the data (e.g. which of two different flows they mean).
- Test-data choices that change what the test does (payment mode of each instalment, cash / card / UPI, which scheme
  type, with or without discount ...): do not guess. Put each one in "choices" with its options taken from the code or the
  data (e.g. the payment modes the payment screen offers), one choice per repetition when each may differ ("Instalment 1
  payment mode", "Instalment 2 payment mode" ...), keep draft = null, and in "reply" ask the user to pick. After they
  answer, write the draft with their picks. Never ask about technical details (ids, tables, element ids) - find those.
  "choices" = [] when nothing needs picking; "questions" stays for open questions without fixed options.
- Form fill ORDER: fill every form in the order a user does on screen (top to bottom, left to right; parent before
  child, e.g. Branch -> Customer -> dependent dropdowns), because choosing a parent often reloads and clears the
  fields after it. Find the order in THIS sequence:
  1. The flow doc's "Form fill order" for that form.
  2. Not there: the code - the field order in the view, and the JS change / keyup / select handlers that reload or empty
     other fields (e.g. a Branch change that calls get_schemes and empties #scheme). Mention in "reply" what you found.
  3. Only when neither tells you: draft = null and ask the user in "questions" in which order they fill that form.
  After the last dependent dropdown, assert the earlier picks still hold before Save.
- Clean up: when the test creates records and Set up SQL is allowed, end the draft with a "Clean up" section of
  Set up SQL DELETE steps (children first, WHERE <id> = {{remembered id}}), from the flow doc's "Clean up" when it
  has one. That section runs even if the test fails, so the test can run again on the same data.
- "lesson": when the user's message reports a FAILED run and you found the cause, fill it so the next test (anyone's)
  avoids it; the user saves it with one click. title = what went wrong, one line, general (not one customer / run).
  If the project's lessons already hold the same mistake, use that lesson's title exactly (it is counted again).
  symptom = the error as seen; fix = what you changed; avoid = the rule for next time, written for EVERY similar field
  or screen (e.g. "every autocomplete field: assert the suggestion list is visible before ArrowDown + Enter").
  kind: app_bug when the application is wrong (avoid = what the test should keep checking), test_outdated,
  environment, test_data or agent_mistake (how the test was written). lesson = null otherwise (no failure, a one-off typo).
- Time rules (next instalment only after N days, due dates, maturity): never wait. When Set up SQL is allowed, move this
  test's own last date back with Set up SQL (WHERE on the remembered id) and continue; take the gap from the code.
  When it is not allowed, say in "reply" that the scenario needs Set up SQL on an environment that allows it.
- If the scenario saves or deletes data, say so in "reply" so the user knows the run changes the test database.`;

const CODE_RULES = `
The application's source code is in your current folder (read-only copy of the repository). Before writing the draft,
look up the screen in the code: find its controller method (from the page path), the view it loads and the JS it uses
(Glob / Grep / Read only). Take from the code: the exact page path, field ids for "(#id)" steps, dropdown option texts,
button texts and validation / success messages. Read only what you need (a few files); never quote secrets or config
values from the code. Mention in "reply" which files you used (short paths).`;

async function chatTest({ messages, context, codeDir }) {
  const content = [
    { type: 'text', text: `Project context:\n${context}` },
    { type: 'text', text: 'Conversation so far (latest last):\n' + messages.map(m => `${m.role === 'user' ? 'USER' : 'ASSISTANT'}: ${m.text}`).join('\n\n') },
  ];
  const useCode = codeDir && process.env.LTH_AI_PROVIDER === 'claude-code';   // code browsing needs the Claude Code provider
  const { data, usage } = await ask({ system: CHAT_SYSTEM + (useCode ? CODE_RULES : ''), effort: 'medium', maxTokens: 8000,
                                      schema: CHAT_SCHEMA, content, codeDir: useCode ? codeDir : undefined });
  return { data, usage };
}

module.exports = { aiStep, decideStep, listElements, doAction, generateCases, chatTest, explain, MODEL };
