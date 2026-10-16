#!/usr/bin/env node
// Server-side AI bridge for the LogiTestHub API: reads one JSON request on stdin, prints one JSON reply.
//   {"op":"step","stepText":"...","url":"...","elements":[...],"shotB64":"..."}
//   {"op":"chat","messages":[{role,text}],"context":"..."}   (Test Assistant)
//   {"op":"sql","steps":["Remember SQL \"...\" as bill.", ...]}   (plain English for SQL steps)
// Reply: {"ok":true,"data":{actions,verdict,reason},"usage":{...},"model":"..."} or {"ok":false,"error":"..."}
const { decideStep, chatTest, explainSql, explain, MODEL } = require('./lib/ai');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', async () => {
  let req;
  try { req = JSON.parse(input); } catch (_) { return reply({ ok: false, error: 'Bad JSON request' }); }
  try {
    const OPS = { step: decideStep, chat: chatTest, sql: explainSql };   // chat: Test Assistant; sql: plain English for SQL steps
    if (!OPS[req.op]) return reply({ ok: false, error: 'Unknown op' });
    // what the AI is doing (a file read, a search), one "STEP <text>" line on stderr: the Test Assistant shows it live
    const { data, usage } = await OPS[req.op](req, t => process.stderr.write(`STEP ${String(t).replace(/\s+/g, ' ')}\n`));
    reply({ ok: true, data, usage, model: MODEL });
  } catch (e) {
    reply({ ok: false, error: explain(e) });
  }
});

function reply(obj) {
  process.stdout.write(JSON.stringify(obj));
  process.exitCode = obj.ok ? 0 : 1;   // no process.exit(): let the SDK close its handles
}
