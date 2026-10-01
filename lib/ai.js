// Claude-powered pieces: running a free-text step on the live page, and generating test cases.
// Model is overridable with ETAIL_AI_MODEL; defaults to Claude Opus 5.5.
const Anthropic = require('@anthropic-ai/sdk');

const MODEL = process.env.ETAIL_AI_MODEL || 'claude-opus-5-5';
let client = null;
const getClient = () => (client ||= new Anthropic());

// Opus 5.5 can decline a request on safety grounds; "default" fallbacks re-run it on
// another model inside the same call instead of failing the test run.
async function ask({ system, content, schema, effort, maxTokens }) {
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
- Every case starts with a "Sign in" section using exactly the login steps given to you.
- Use {{variable}} placeholders from the provided variable names instead of real data; never invent secrets.
- Cover the change: positive, negative (validation, wrong input) and edge cases, plus a regression check of the existing flow it touches.
- Keep each case independent and 5-20 steps. slug = short kebab-case.
- "why" = one sentence naming what in the change this case protects.`;

async function generateCases({ requirement, diff, variableNames, loginSteps, count }) {
  const parts = [
    `Requirement / bug / task:\n${requirement}`,
    `Login steps to start every case with:\n${loginSteps}`,
    `Available variables: ${variableNames.join(', ')}`,
    `Write about ${count} test cases.`,
  ];
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

module.exports = { aiStep, decideStep, listElements, doAction, generateCases, explain, MODEL };
