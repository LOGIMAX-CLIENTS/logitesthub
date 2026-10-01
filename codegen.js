#!/usr/bin/env node
// Reads a test case (_test.md text) on stdin, prints JSON { title, steps, vars, files }.
// Usage: node codegen.js [--id TC-23] [--vars <variables.json>] [--base <folder for @import>] < case_test.md
const { generate } = require('./lib/codegen');

const DEFAULT_VARS = 'F:/TestMU-Ai/.testmuai/variables/etail.json';
const arg = name => { const i = process.argv.indexOf(name); return i > -1 ? process.argv[i + 1] : undefined; };
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => { input += d; });
process.stdin.on('end', () => {
  process.stdout.write(JSON.stringify(generate(input, { id: arg('--id'), varsFile: arg('--vars') || DEFAULT_VARS, base: arg('--base') })));
});
