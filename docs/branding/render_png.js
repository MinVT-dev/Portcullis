// Renders the brand SVGs to PNG with headless Chromium (Playwright).
// Usage, from the repo root: node docs/branding/render_png.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const here = __dirname;
const png = path.join(here, 'png');
const fallback = path.join(here, '..', '..', 'portcullis', 'assets', 'fallback');

// [svg, out, width, height]
const jobs = [
  ['portcullis-icon.svg', path.join(png, 'portcullis-icon.png'), 1024, 1024],
  ['portcullis-icon.svg', path.join(png, 'portcullis-avatar-512.png'), 512, 512],
  ['portcullis-status-online.svg', path.join(png, 'portcullis-status-online.png'), 256, 256],
  ['portcullis-status-offline.svg', path.join(png, 'portcullis-status-offline.png'), 256, 256],
  ['portcullis-banner.svg', path.join(png, 'portcullis-banner.png'), 1280, 640],
  ['portcullis-discord-banner.svg', path.join(png, 'portcullis-discord-banner.png'), 1360, 480],
  ['portcullis-mark.svg', path.join(png, 'portcullis-mark.png'), 512, 512],
  ['portcullis-mark-dark.svg', path.join(png, 'portcullis-mark-dark.png'), 512, 512],
  ['portcullis-logo.svg', path.join(png, 'portcullis-logo.png'), null, 264],
  ['portcullis-logo-dark.svg', path.join(png, 'portcullis-logo-dark.png'), null, 264],
  ['portcullis-game-banner.svg', path.join(fallback, 'game-banner.png'), 920, 430],
  ['portcullis-game-logo.svg', path.join(fallback, 'game-logo.png'), 512, 512],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const [src, out, w0, h] of jobs) {
    const svg = fs.readFileSync(path.join(here, src), 'utf8');
    const m = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
    const w = w0 ?? Math.round((h * +m[1]) / +m[2]);
    await page.setViewportSize({ width: w, height: h });
    const sized = svg.replace('<svg ', `<svg style="display:block;width:${w}px;height:${h}px" `);
    await page.setContent(`<html><body style="margin:0;background:transparent">${sized}</body></html>`);
    await page.screenshot({ path: out, omitBackground: true, clip: { x: 0, y: 0, width: w, height: h } });
    console.log(`${path.relative(process.cwd(), out)} ${w}x${h}`);
  }
  await browser.close();
})();
