// How the runner reads each step, for the checks before a run (LogiTestHub preflight, tester check): they ask the
// runner's own parseStep, so a selector the checks accept is always one the runner can use.
// stdin: JSON array of step texts. stdout: JSON array of { kind, css, needsAI, hint }.
const { parseStep } = require('./actions');

let input = '';
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  const steps = JSON.parse(input || '[]');
  process.stdout.write(JSON.stringify(steps.map(t => {
    const a = parseStep(String(t || ''));
    return { kind: a.kind, css: a.css || null, needsAI: !!a.needsAI, hint: a.hint || null };
  })));
});
