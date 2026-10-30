#!/usr/bin/env node
// One-time: turns the step screenshots of past runs into 256-colour PNG (about 40% of the size),
// the format new runs already save. Same file names, so nothing else changes.
// Usage: node deploy/shrink-shots.js [runs folder] [--dry-run]   (default folder: runs/ next to run.js)
const fs = require('fs');
const path = require('path');
const sharp = require('sharp');

const args = process.argv.slice(2);
const dry = args.includes('--dry-run');
const root = args.find(a => !a.startsWith('--')) || path.join(__dirname, '..', 'runs');

// PNG colour type (byte 25 of the IHDR chunk): 3 = already a palette image
const isPalette = file => {
  const fd = fs.openSync(file, 'r');
  try { const b = Buffer.alloc(26); fs.readSync(fd, b, 0, 26, 0); return b[25] === 3; } finally { fs.closeSync(fd); }
};

(async () => {
  let files = 0, done = 0, before = 0, after = 0, skipped = 0;
  for (const run of fs.readdirSync(root)) {
    const dir = path.join(root, run);
    let names;
    try { names = fs.readdirSync(dir).filter(n => /^step-\d+\.png$/.test(n)); } catch { continue; }
    for (const n of names) {
      const file = path.join(dir, n);
      files++;
      try {
        if (isPalette(file)) { skipped++; continue; }
        const buf = fs.readFileSync(file);
        const small = await sharp(buf).png({ palette: true, colours: 256, dither: 0, effort: 1, compressionLevel: 9 }).toBuffer();
        if (small.length >= buf.length) { skipped++; continue; }
        before += buf.length; after += small.length; done++;
        if (!dry) { fs.writeFileSync(file + '.tmp', small); fs.renameSync(file + '.tmp', file); }
      } catch (e) { skipped++; console.error(`skip ${file}: ${e.message}`); }
      if (files % 1000 === 0) console.log(`${files} checked ...`);
    }
  }
  const mb = x => (x / 1048576).toFixed(0) + ' MB';
  console.log(`${dry ? 'Would shrink' : 'Shrunk'} ${done} of ${files} screenshots: ${mb(before)} -> ${mb(after)}, ` +
    `${mb(before - after)} ${dry ? 'would be freed' : 'freed'}. ${skipped} skipped (already small).`);
})();
