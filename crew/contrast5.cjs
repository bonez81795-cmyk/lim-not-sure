const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path'); const stub = require('./stub4.js');
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  const p = await (await b.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  await p.addInitScript(stub, 5); await p.route(/fonts\.|cdnjs|unpkg/, (r) => r.abort());
  await p.goto('file://' + path.resolve('crew-control.html')); await p.waitForSelector('.chip'); await p.waitForTimeout(800);
  for (const tab of ['stage', 'knowledge', 'messages', 'ideas']) {
    await p.click('.tab[data-tab="' + tab + '"]'); await p.waitForTimeout(250);
    const bad = await p.evaluate(() => {
      const parse = (s) => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const a = m[1].split(',').map((x) => parseFloat(x)); return { r: a[0], g: a[1], b: a[2], a: a.length > 3 ? a[3] : 1 }; };
      const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
      const L = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
      const over = (top, bot) => ({ r: top.r * top.a + bot.r * (1 - top.a), g: top.g * top.a + bot.g * (1 - top.a), b: top.b * top.a + bot.b * (1 - top.a), a: 1 });
      const bgOf = (el) => { const chain = []; for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) chain.push(c); if (c && c.a >= 1) break; } let base = { r: 1, g: 0, b: 7, a: 1 }; for (const c of chain.reverse()) base = over(c, base); return base; };
      const out = [], seen = new Set();
      const w = document.createTreeWalker(document.querySelector('.app'), NodeFilter.SHOW_TEXT);
      for (let n; (n = w.nextNode());) {
        const t = n.textContent.trim(); if (!t) continue; const el = n.parentElement; const r = el.getBoundingClientRect();
        if (!r.width || !r.height || el.closest('[hidden],canvas,svg,[aria-hidden="true"]')) continue;
        const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || +cs.opacity === 0) continue;
        const fg = parse(cs.color), bg = bgOf(el), f = over(fg, bg), l1 = L(f), l2 = L(bg), cr = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        const size = parseFloat(cs.fontSize), bold = +cs.fontWeight >= 700, large = size >= 24 || (size >= 18.66 && bold);
        if (cr < (large ? 3 : 4.5)) { const key = el.className + '|' + cs.color; if (!seen.has(key)) { seen.add(key); out.push(cr.toFixed(2) + ' ' + (el.className || el.tagName) + ' "' + t.slice(0, 30) + '" ' + cs.color + ' ' + size + 'px'); } }
      }
      return out;
    });
    console.log(tab, bad.length ? bad : 'all text >= 4.5:1');
  }
  await b.close();
})();
