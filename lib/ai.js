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
// What Claude is doing right now, in words a tester reads ("Reading the code: views/grn/add.php"), from a tool call.
function toolStep(b, codeDir) {
  const i = b.input || {};
  const rel = f => String(f || '').replace(codeDir || '', '').replace(/^[\\/]+/, '').replace(/\\/g, '/');
  if (b.name === 'Read') return `Reading the code: ${rel(i.file_path)}`;
  if (b.name === 'Grep') return `Searching the code for "${String(i.pattern || '').slice(0, 60)}"`;
  if (b.name === 'Glob') return `Looking for files: ${String(i.pattern || '').slice(0, 60)}`;
  if (b.name === 'StructuredOutput') return 'Writing the test case';
  return null;
}

// onStep(text): called as Claude works (a file read, a search, writing the answer) for the "what is it doing" line.
function askViaClaudeCode({ system, content, schema, codeDir, onStep }) {
  const { spawn } = require('child_process');
  const prompt = content.filter(b => b.type === 'text').map(b => b.text).join('\n\n');
  const tools = codeDir ? ['Read', 'Grep', 'Glob'] : [];
  const args = ['-p', '--output-format', 'stream-json', '--verbose', '--json-schema', JSON.stringify(schema), '--system-prompt', system,
    '--tools', tools.join(','), '--no-session-persistence', '--setting-sources', ''];
  if (tools.length) args.push('--allowedTools', ...tools);
  // the AI model picked in Settings -> System (ETAIL_AI_MODEL), as for the API; LTH_CLAUDE_MODEL overrides it
  const model = process.env.LTH_CLAUDE_MODEL || process.env.ETAIL_AI_MODEL;
  if (model) args.push('--model', model);
  const limitMs = codeDir ? 600000 : 240000;   // reading code takes longer (multi-screen flows)
  return new Promise((resolve, reject) => {
    // Claude Code prefers ANTHROPIC_API_KEY over the subscription sign-in: hide it, or "subscription" calls bill the API
    const env = { ...process.env };
    delete env.ANTHROPIC_API_KEY;
    delete env.ANTHROPIC_AUTH_TOKEN;
    const child = spawn(claudeCli(), args, { windowsHide: true, env, ...(codeDir ? { cwd: codeDir } : {}) });
    let out = '', err = '', buf = '', result = null;
    const timer = setTimeout(() => { child.kill(); reject(new Error(`Claude Code took longer than ${limitMs / 60000} minutes`)); }, limitMs);
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    const event = l => {   // stream-json: one event per line; tool calls -> onStep, the last event is the result
      let e;
      try { e = JSON.parse(l); } catch (_) { return; }
      if (e.type === 'result') result = e;
      if (e.type === 'assistant' && onStep) {
        for (const b of (e.message && e.message.content) || []) {
          const t = b.type === 'tool_use' && toolStep(b, codeDir);
          if (t) onStep(t);
        }
      }
    };
    child.stdout.on('data', d => {
      out += d; buf += d;
      const lines = buf.split('\n'); buf = lines.pop();
      lines.forEach(event);
    });
    child.stderr.on('data', d => { err += d; });
    child.on('error', e => { clearTimeout(timer); reject(new Error(`Claude Code CLI not found (${e.code || e.message}). Install it or set LTH_CLAUDE_CLI.`)); });
    child.on('close', () => {
      clearTimeout(timer);
      if (buf.trim()) event(buf);
      const j = result;
      if (!j) return reject(new Error('Claude Code: ' + ((err || out).trim().split('\n').pop() || 'no answer').slice(0, 200)));
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

async function ask({ system, content, schema, effort, maxTokens, codeDir, onStep }) {
  if (process.env.LTH_AI_PROVIDER === 'claude-code' && !content.some(b => b.type === 'image')) {
    return askViaClaudeCode({ system, content, schema, codeDir, onStep });
  }
  if (onStep) onStep('Writing the test case');
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

// ---------- 1b. Heal: a step failed on another environment (e.g. the test domain) - is it the app or a changed screen? ----------
// Text only (fields + the page's own words, no screenshot), so it also runs on the Claude subscription.

const HEAL_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['verdict', 'change', 'explain', 'steps_now', 'case_edit'],
  properties: {
    verdict: { type: 'string', enum: ['heal', 'app_bug', 'test_data', 'unknown'] },
    change: {
      type: 'object', additionalProperties: false, required: ['kind', 'field', 'code_name'],
      properties: {
        kind: { type: 'string', enum: ['new_field', 'id_changed', 'label_changed', 'option_changed', 'none'] },
        field: { type: 'string' },       // the field / button as the user sees it, e.g. "GST No"
        code_name: { type: 'string' },   // its id or name in the page, e.g. "gst_no" ('' when none)
      },
    },
    explain: { type: 'string' },
    steps_now: { type: 'array', items: { type: 'string' } },
    case_edit: {
      type: 'object', additionalProperties: false, required: ['op', 'step', 'steps'],
      properties: {
        op: { type: 'string', enum: ['replace', 'insert_before', 'none'] },
        step: { type: 'integer' },
        steps: { type: 'array', items: { type: 'string' } },
      },
    },
  },
};

const HEAL_SYSTEM = `A UI test of the eTail jewellery ERP (CodeIgniter, jQuery, Select2) passed before and now fails on a newer
deployment. Decide whether the SCREEN CHANGED (heal it) or the APP IS WRONG (do not heal). You get the numbered steps
(the failed one marked), the error, the page URL, the fields on the page now (id / name / label / text) and the page's
visible text (validation messages, alerts).

verdict "heal" only for a change in the screen, never in the result:
- new_field: a new field the form now needs (a "required" / "please enter" message, or a new empty field next to the
  ones the test fills). Fill it with a simple valid test value.
- id_changed / label_changed / option_changed: the element the step uses is still there with another id, label or option text.
verdict "app_bug": an error page, a 500, a PHP / database error, a wrong total, a wrong saved value, a success message
missing while no field asks for anything. verdict "test_data": the data the test needs is not in this database
(empty dropdown, "no records"). "unknown" when you cannot tell. For these, steps_now = [] and case_edit.op = "none".

For "heal":
- steps_now: steps to run NOW, in order, to get past the failure. When a new field blocks a Save / Submit that already
  ran, fill the field and click that button again. When an assert failed, the runner checks the same assert again after
  steps_now - never write an assert yourself and never change what an assert expects.
- case_edit: the change to keep in the test case. "replace" step N (an action step whose element changed) with the new
  steps, or "insert_before" step N (the button click the new field must be filled before). Never replace an assert.
- Steps use only these shapes, with the hint from the fields list:
  Type <value> into the <label> field (#id).   Select '<option>' from the <label> dropdown (#id).   Click the <text> button (#id).
  A typed value is one word (TEST29ABCDE1Z5) or in single quotes ('Ravi Traders'). Write the label as the user reads
  it, without a required-star or colon ("GST No", not "GST No *").
- explain: 1-2 short sentences in plain English for a tester: what changed on the screen and what you did.
  e.g. "The supplier form has a new required field, GST No. I filled it with TEST29ABCDE1Z5 and saved again."`;

async function healStep({ steps, failedN, error, url, elements, pageText }) {
  const list = steps.map((t, i) => `${i + 1 === failedN ? '>>' : '  '} ${i + 1}. ${t}`).join('\n');
  const { data, usage } = await ask({
    system: HEAL_SYSTEM, effort: 'medium', maxTokens: 3000, schema: HEAL_SCHEMA,
    content: [{ type: 'text', text: `Steps (>> = failed):\n${list}\n\nError of step ${failedN}: ${error}\n\nPage URL: ${url}\n\n` +
      `Fields on the page now:\n${JSON.stringify(elements)}\n\nVisible text on the page:\n${pageText}` }],
  });
  return { data, usage };
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
- Set the date range (#picker_id) from 'DD-MM-YYYY' to 'DD-MM-YYYY'.   (list / report date filters; then click Search)
- Click the <text> button.
- Assert the page title is '<title>'. / Assert the page title contains '<text>'.
- Assert the current URL contains '<text>'.
- Assert the text '<text>' is visible.   (page text only, not input values)
- Assert the <label> field (#id or .class) shows '<value>'.   (the value inside an input / select; "contains" also works)
- Type the keys '<value>' into the <label> field (#id).   (real key presses, one at a time)
- Press the <Key> key in the <label> field (#id).   (one real key: Backspace, Enter, ArrowDown ...)
An autocomplete that loads its list on keyup: either "Type the keys '<value>' ..." or "Type '<value>' ..." followed by
"Press the Backspace key in ..." - then Wait 2 seconds and assert the suggestion is visible. Plain Type fires no keyup.
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
    // what the test needs, found in the code BEFORE the draft (shown to the user; unknowns block the draft)
    readiness: {
      type: 'object', additionalProperties: false,
      properties: {
        screens: { type: 'array', items: { type: 'string' } },     // page path + its view / JS files
        flow_doc: { type: 'string' },                               // knowledge_brain doc used, or "none"
        filters: { type: 'array', items: { type: 'string' } },     // list / report filters the test must set
        settings: { type: 'array', items: { type: 'string' } },    // permissions / settings the action needs
        test_data: { type: 'array', items: { type: 'string' } },   // records the test needs and where they come from
        unknowns: { type: 'array', items: { type: 'string' } },    // still not known: ask, do not draft
      },
      required: ['screens', 'flow_doc', 'filters', 'settings', 'test_data', 'unknowns'],
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
    // the app's knowledge_brain lacked (or had wrong) something this test needed: LogiTestHub lists it for the brain team
    brain_gap: {
      anyOf: [
        { type: 'null' },
        {
          type: 'object', additionalProperties: false,
          properties: {
            module: { type: 'string' }, doc: { type: 'string' }, screen: { type: 'string' }, missing: { type: 'string' },
            detail: { type: 'string' }, evidence: { type: 'string' }, suggested: { type: 'string' },
            terms: { type: 'array', items: { type: 'string' } },
          },
          required: ['module', 'doc', 'screen', 'missing', 'detail', 'evidence', 'suggested', 'terms'],
        },
      ],
    },
  },
  required: ['reply', 'understanding', 'readiness', 'questions', 'choices', 'draft', 'lesson', 'brain_gap'],
};

const CHAT_SYSTEM = `You are the Tester Assistant inside LogiTestHub, the team's test manager. A tester describes a scenario in their own
words (often Tamil, Tanglish or English); you turn it into ONE runnable UI test case for the project's web application.

Always fill "understanding" with what you understood, in plain words (screen = the page; module / sub_module = names from the
project's module list when one matches, else ""; goal; expected result; test_data = the values the test will use).
"reply" is read by a manual TESTER who does not know the code. Write it like a friendly chat (ChatGPT style), with a few
emojis, short lines, never a paragraph.
- Language: simple English by default. Reply in Tanglish ONLY when the user's LATEST message is itself written in Tanglish
  (Tamil words in English letters, e.g. "panna", "varanum", "irukura"). A message that is only a module / screen name,
  English words, or a mix you are unsure about gets simple English. Tamil script gets simple English too.
- Simple English = short everyday words a non-native reader follows: "The customer list does not show when you type a name",
  "Changing the branch clears the scheme". (Tanglish, only when they wrote Tanglish: "customer name type pannumbodhu list varala".)
- Talk only about what happens ON THE SCREEN, in everyday words.
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
- Set the date range (#picker_id) from 'DD-MM-YYYY' to 'DD-MM-YYYY'.   (list / report date filters; then click Search)
- Click the <text> button.
- Assert the text '<text>' is visible. / Assert the page title contains '<text>'. / Assert the current URL contains '<text>'.
  "Assert the text" reads only the page's visible TEXT - never what is inside an input / select / textarea.
- Assert the <label> field (#id or .class) shows '<value>'.   / ... contains '<value>'.   the VALUE of an input, select
  or textarea (e.g. a row's tag code in input.est_tag_name, a calculated amount box): use this, not "Assert the text".
- Assert the <label> (#id or .class) is visible.   an element (button, row, message box) is shown
- Select the first available option in the <label> dropdown (#id).  /  Click the <text> radio button.  /  Wait 2 seconds.
- Type the keys '<value>' into the <label> field (#id).   real key presses, one at a time (keydown / keyup per character)
- Press the <Key> key in the <label> field (#id).   one real key: Backspace, Enter, ArrowDown, Tab, Escape ...
  Plain "Type ..." fills the box at once and fires NO keyup. Pressing a key any other way (End, "press one more key",
  typing the same value again) is NOT a step the runner has: it goes to the AI, which cannot press keys, and fails.
AUTOCOMPLETE that loads its list on keyup (customer name / mobile search ...): use ONLY one of these two ways, nothing else:
  a) Type the keys '8925532099' into the Customer field (#est_cus_name).   then  Wait 2 seconds.
  b) Type '8925532099' into the Customer field (#est_cus_name).   then  Press the Backspace key in the Customer field
     (#est_cus_name).   (the last character goes, the text changes, the list loads)  then  Wait 2 seconds.
  Then: Assert the text '<suggestion>' is visible.  then  Click the <suggestion> ... (the list item).
  If a) failed in a run, write b); if b) failed, write a). Never invent a third way.
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
- Records taken from the database and then looked for on a list / report screen: also Remember the record's
  date (DATE_FORMAT(<date col>,'%d-%m-%Y')), then set the screen's date filter to it before searching -
  lists default to today / this month / last 7 days, so an older record is not shown otherwise:
  Set the date range (#rpt_date_picker) from '{{rec_date}}' to '{{rec_date}}'.  then the screen's Search button.
- WORK IN THIS ORDER, and stop as soon as a step says the test cannot run:
  1. Flow doc (knowledge_brain) for the screen, if there is one.
  2. Settings / permissions: from LIVE SETTINGS in the context (the login user's profile, employee settings, retail
     and chit settings, business date) decide whether what the user asked is ALLOWED for this user on this
     environment. Find in the code which setting decides it. If it is not allowed: draft = null, and "reply" says in
     1-2 lines exactly which setting to change (screen > field: value now -> needed) and, if there is one, the way
     that needs no change. Settings also decide which options a screen shows - use them before reading the view.
  3. Test data: what the test needs, from the flow doc / code; picked by SQL with branch, date, full display number.
  4. View / JS / controller: the screen as these settings show it - ids, filters, messages.
  5. Draft (it is then checked: database steps and element ids, before the user sees it).
- READINESS (fill "readiness" before any draft, from the flow doc and the code - never guess): screens = every page
  the test opens with its view / JS file; filters = every filter / search on a list or report screen the test uses;
  settings = every permission / setting the actions need (with the value needed); test_data = every record the test
  needs and how it gets it (made by the test, or picked by SQL with branch, date, full display number); unknowns =
  anything you could not settle from the doc or the code. If unknowns is not empty: draft = null and ask about them.
  The draft is then checked automatically before the user sees it (database steps run read-only, every (#id) /
  (.class) looked up in the code); a problem comes back to you to fix, so the user gets a draft that runs.
- Settings and permissions FIRST: before writing the draft, find in the code what the action under test depends on -
  profile / role permissions (e.g. profile.allow_bill_cancel, previous_bill_cancel), retail / company settings, the
  day-close date - and whether the user picked an option that needs them. Put one "Assert SQL ... returns '1'" step
  per such setting at the top of the draft (the draft is checked against the database before it runs). If a setting
  blocks what the user asked, say it in "reply" in one line with the exact place to change it (screen > field) and
  the other way that needs no change (e.g. make a new record in the test). Never let the user find it by a failed run.
- Business date in eTail = the branch's day-close date (ret_day_closing.entry_date), not CURDATE(): use it for
  "today" in SQL, and records the screen lets you change (cancel, edit) are usually only those of that date.
- Before writing the steps that find a database record on a screen, read that screen's view + JS and list EVERY filter
  and search it applies (branch, date range, status, type, the list's own search box) and how it shows the record's
  number. Remember ALL of them from the database first - the record's branch, its date, and its number exactly as the
  screen shows it (e.g. CONCAT(branch short_name,'-',fin_year_code,'-SA-',sales_ref_no) = 'ATM-25-SA-00010', never only
  '00010': numbers repeat per branch) - then set each filter, Search, and check the row's Id.
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
- Element hints, most stable first: the element's own (#id); a class only that element has on the page; a short path
  from the nearest id (#lot_form .category_req). A position path (nth-child, long > chains) only when nothing else
  exists - it breaks when a field is added and may match another row (a "*" found by position passed on the item row
  while the header had none). When you use one, say in "reply" in one line that the check depends on the screen layout
  and a developer can add an id to make it exact. Testers cannot change the code: never ask them to.
- A test for a bug fix or a change must check the change itself: find in the code what it changes on the screen and
  put steps for exactly that. Say in "reply" in one line which steps prove it ("Steps 7-9 check the new * marks").
  Steps that only check what worked before (an old validation message) may stay, never as the only checks.
- Save / Submit / Add item steps: BEFORE writing them, read the click handler and every validate function it calls
  (e.g. validateMetalIssueRow) and fill every field it checks, also inputs inside JS-built rows. After the click, assert
  what only a successful save shows (redirect URL / success message), never only a Wait.
- "brain_gap": the knowledge_brain is written by another team; LogiTestHub lists its gaps for them. Fill it when the test
  needed something the module's brain docs do not say (or say wrongly) and you had to take it from the code: a mandatory
  field or row check, a setting, a filter, a message, a form order, a table. Also when a failure's 🏷️ is 📘 Flow doc.
  One gap per answer (the most important); brain_gap = null when the brain had what you needed or there is no brain.
  module = the knowledge_brain folder name exactly (e.g. Smith_Metal_Issue); doc = the file it belongs in
  (FIELD_DICTIONARY.md, SETTINGS_REGISTER.md, SCREENS.md ...); screen = page path; missing = one line, general
  ("Touch and UOM are mandatory in every Smith Metal Issue item row"); detail = what the code does, plainly;
  evidence = file:line of the code; suggested = the text (a table row or 2-4 lines of Markdown, in that doc's style)
  the brain team can paste; terms = 1-4 exact words or ids from the code that the updated doc must contain
  (e.g. "karigar_touch", "validateMetalIssueRow") - LogiTestHub checks the brain again by looking for them.
  Say it in "reply" in one line: 📘 Brain gap noted for the brain team: <missing>.
- Time rules (next instalment only after N days, due dates, maturity): never wait. When Set up SQL is allowed, move this
  test's own last date back with Set up SQL (WHERE on the remembered id) and continue; take the gap from the code.
  When it is not allowed, say in "reply" that the scenario needs Set up SQL on an environment that allows it.
- If the scenario saves or deletes data, say so in "reply" so the user knows the run changes the test database.`;

const CODE_RULES = `
The application's source code is in your current folder (read-only copy of the repository). Before writing the draft,
look up the screen in the code: find its controller method (from the page path), the view it loads and the JS it uses
(Glob / Grep / Read only). Take from the code: the exact page path, field ids for "(#id)" steps, dropdown option texts,
button texts and validation / success messages. Read only what you need (a few files); never quote secrets or config
values from the code. Mention in "reply" which files you used (short paths).
Time limit: answer within about 5 minutes. Each turn starts without memory of earlier file reads, so:
- What earlier ASSISTANT turns already found (files, ids, rules, readiness) is in the conversation: reuse it, never
  read those files again unless something is missing.
- Large files (JS of 1000+ lines, models): Grep for the id / function / message you need and Read only those line
  ranges (offset + limit); never Read a whole large file.
- A flow of 3+ screens: finish the screens you have found; list the rest in readiness.unknowns and say in "reply"
  that the next message continues with them.`;

async function chatTest({ messages, context, codeDir }, onStep) {
  const content = [
    { type: 'text', text: `Project context:\n${context}` },
    { type: 'text', text: 'Conversation so far (latest last):\n' + messages.map(m => `${m.role === 'user' ? 'USER' : 'ASSISTANT'}: ${m.text}`).join('\n\n') },
  ];
  const useCode = codeDir && process.env.LTH_AI_PROVIDER === 'claude-code';   // code browsing needs the Claude Code provider
  const { data, usage } = await ask({ system: CHAT_SYSTEM + (useCode ? CODE_RULES : ''), effort: 'medium', maxTokens: 8000,
                                      schema: CHAT_SCHEMA, content, codeDir: useCode ? codeDir : undefined, onStep });
  return { data, usage };
}

// Plain English for SQL steps, shown on the case page instead of the query (the query is one click away).
const SQL_SCHEMA = { type: 'object', additionalProperties: false, required: ['texts'],
  properties: { texts: { type: 'array', items: { type: 'string' } } } };
const SQL_SYSTEM = `You explain database steps of an automated UI test to a manual tester who does not read SQL.
For each step write ONE short sentence (max 30 words) in plain English: what it picks / checks / changes and why it
matters for the test, in business words (bill, tag, customer, branch, day-close date, permission), never table or
column names, never SQL words. "Remember ... as x" = picks a value the next steps use: say what it picks and name the
value in {{x}} form. "Assert ... returns 'v'" = say what must be true. "Set up SQL" = say what test data it changes.
Ids and numbers shown as N or 'X' are this run's values: do not mention them. Use the test title and section names to
know what a value means (e.g. the status a tag must have after a cancel); when you cannot tell what a code value
means, say the value instead of guessing a meaning. Return texts in the same order as the steps.`;

async function explainSql({ steps, context }) {
  const list = steps.map((s, i) => `${i + 1}. ${s}`).join('\n');
  const content = [{ type: 'text', text: context ? `${context}\n\nSteps:\n${list}` : list }];
  const { data, usage } = await ask({ system: SQL_SYSTEM, effort: 'low', maxTokens: 2000, schema: SQL_SCHEMA, content });
  return { data, usage };
}

module.exports = { aiStep, decideStep, listElements, doAction, healStep, generateCases, chatTest, explainSql, explain, MODEL };
