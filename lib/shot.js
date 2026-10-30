// Step screenshots as 256-colour PNG: about 40% of a full-colour PNG, text stays as sharp
// (app screens use few colours). sharp is optional: without it the full-colour PNG stays.
const fs = require('fs');

let sharp = null;
try { sharp = require('sharp'); } catch { /* not installed: keep full-colour PNG */ }

const pending = [];

// Saves the screenshot now; the smaller copy replaces it in the background so a step is not slowed.
async function saveShot(page, file) {
  const buf = await page.screenshot();
  fs.writeFileSync(file, buf);
  if (!sharp) return;
  pending.push(sharp(buf).png({ palette: true, colours: 256, dither: 0, effort: 1, compressionLevel: 9 }).toBuffer()
    .then(small => {
      if (small.length >= buf.length) return;
      fs.writeFileSync(file + '.tmp', small);
      fs.renameSync(file + '.tmp', file);   // a viewer never reads a half-written file
    })
    .catch(() => {}));
}

// Waits for the smaller copies before the run ends.
function flushShots() {
  return Promise.all(pending.splice(0));
}

module.exports = { saveShot, flushShots };
