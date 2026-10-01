#!/usr/bin/env node
// Server-side AI bridge for the LogiTestHub API: reads one JSON request on stdin, prints one JSON reply.
//   {"op":"step","stepText":"...","url":"...","elements":[...],"shotB64":"..."}
// Reply: {"ok":true,"data":{actions,verdict,reason},"usage":{...},"model":"..."} or {"ok":false,"error":"..."}
const { decideStep, explain, MODEL } = require('./lib/ai');

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', async () => {
  let req;
  try { req = JSON.parse(input); } catch (_) { return reply({ ok: false, error: 'Bad JSON request' }); }
  try {
    if (req.op !== 'step') return reply({ ok: false, error: 'Unknown op' });
    const { data, usage } = await decideStep(req);
    reply({ ok: true, data, usage, model: MODEL });
  } catch (e) {
    reply({ ok: false, error: explain(e) });
  }
});

function reply(obj) {
  process.stdout.write(JSON.stringify(obj));
  process.exitCode = obj.ok ? 0 : 1;   // no process.exit(): let the SDK close its handles
}
