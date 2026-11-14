// Renders docs/branding/emoji/*.svg to 128x128 PNGs (what the Developer Portal takes) and a preview
// sheet at the sizes Discord shows them, on its dark and light themes.
// Usage, from the repo root: node docs/branding/render_emoji.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, 'emoji');
const names = fs.readdirSync(dir).filter((f) => f.endsWith('.svg')).map((f) => f.slice(0, -4)).sort();
const order = ['online', 'offline', 'starting', 'stopping', 'licence', 'backup', 'console', 'players'];
names.sort((a, b) => order.indexOf(a.replace('portcullis_', '')) - order.indexOf(b.replace('portcullis_', '')));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 128, height: 128 });
  for (const name of names) {
    const svg = fs.readFileSync(path.join(dir, `${name}.svg`), 'utf8');
    await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`);
    const out = path.join(dir, `${name}.png`);
    await page.screenshot({ path: out, omitBackground: true, clip: { x: 0, y: 0, width: 128, height: 128 } });
    console.log(path.relative(process.cwd(), out));
  }

  const img = (n, s) => `<img src="data:image/svg+xml;base64,${Buffer.from(
    fs.readFileSync(path.join(dir, `${n}.svg`))).toString('base64')}" width="${s}" height="${s}">`;
  const row = (theme, bg, fg) => `<div class="row" style="background:${bg};color:${fg}">
    <div class="theme">${theme}</div>
    ${names.map((n) => `<div class="cell">${img(n, 48)}<div class="inline">${img(n, 22)} <span>${n.replace('portcullis_', '')}</span></div><code>:${n}:</code></div>`).join('')}
  </div>`;
  const html = `<html><head><style>
    body{margin:0;font-family:Inter,DejaVu Sans,sans-serif}
    .row{display:flex;gap:18px;padding:20px 24px;align-items:flex-start}
    .theme{width:56px;font-size:13px;opacity:.8;padding-top:14px}
    .cell{width:118px;display:flex;flex-direction:column;align-items:center;gap:8px;font-size:15px}
    .inline{display:flex;align-items:center;gap:4px}
    code{font-size:10px;opacity:.75}
  </style></head><body>${row('Dark', '#313338', '#DBDEE1')}${row('Light', '#FFFFFF', '#313338')}</body></html>`;
  await page.setViewportSize({ width: 1240, height: 300 });
  await page.setContent(html);
  const height = await page.evaluate(() => document.body.scrollHeight);
  const preview = path.join(dir, 'preview.png');
  await page.screenshot({ path: preview, clip: { x: 0, y: 0, width: 1240, height } });
  console.log(path.relative(process.cwd(), preview));
  await browser.close();
})();
