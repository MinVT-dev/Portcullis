// Screenshots the pages tools/render_embeds.py wrote, at twice the pixel density, as PNGs.
// Usage, from the repo root: NODE_PATH=$(npm root -g) node tools/screenshot_embeds.js <html dir> <png dir>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [src, dest] = process.argv.slice(2);
if (!src || !dest) {
  console.error('usage: node tools/screenshot_embeds.js <html dir> <png dir>');
  process.exit(2);
}

(async () => {
  fs.mkdirSync(dest, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 640, height: 400 }, deviceScaleFactor: 2 });
  for (const file of fs.readdirSync(src).filter((f) => f.endsWith('.html')).sort()) {
    await page.goto('file://' + path.resolve(src, file));
    const out = path.join(dest, file.replace(/\.html$/, '.png'));
    await page.locator('#shot').screenshot({ path: out });
    console.log(out);
  }
  await browser.close();
})();
