const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path');
const stub = require('./stub4.js');
const libs = require('./libstubs.js');
let failed = 0;
const ok = (c, m) => { if (!c) failed++; console.log((c ? 'ok   ' : 'FAIL ') + m); };

async function open(browser, { n = 5, width = 1440, height = 900, reduced = false, lib = 'stub', file = 'crew-control.html' } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: reduced ? 'reduce' : 'no-preference' });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/fonts|ERR_|Failed to load resource/.test(m.text())) errors.push('console: ' + m.text()); });
  await page.addInitScript(stub, n);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await page.route(/cdnjs\.cloudflare\.com\/ajax\/libs\/gsap\/.*\/gsap\.min\.js/, (r) => (lib === 'stub' ? r.fulfill({ contentType: 'application/javascript', body: libs.gsap }) : r.abort()));
  await page.route(/cdnjs\.cloudflare\.com\/ajax\/libs\/gsap\/.*\/ScrollTrigger\.min\.js/, (r) => (lib === 'stub' ? r.fulfill({ contentType: 'application/javascript', body: libs.st }) : r.abort()));
  await page.route(/unpkg\.com\/lenis/, (r) => (lib === 'stub' ? r.fulfill({ contentType: 'application/javascript', body: libs.lenis }) : r.abort()));
  await page.goto('file://' + path.resolve(file));
  await page.waitForSelector('.chip');
  await page.waitForTimeout(400);
  return { page, ctx, errors };
}
const txt = (page, sel) => page.textContent(sel).then((s) => s.trim());
const nodes = (page) => page.evaluate(() => window.__V.nodes.map((x) => ({ id: x.id, name: x.a.name, x: x.x, y: x.y, r: x.r })));

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });

  // ---------- desktop, 5 agents, library stand-ins
  let { page, ctx, errors } = await open(browser, { n: 5 });
  ok((await txt(page, '#sAgents')) === '5' && (await txt(page, '#sMotion')) === '4' && (await txt(page, '#sGate')) === '3' && (await txt(page, '#sOk')) === '4', 'telemetry bar counts: agents 5, in motion 4, waiting 3, approved ' + await txt(page, '#sOk'));
  ok((await txt(page, '#liveTxt')) === 'Live' && !(await page.$eval('#livePill', (e) => e.classList.contains('off'))), 'LIVE pill shows Live');
  ok(/^\d\d:\d\d:\d\d$/.test(await txt(page, '#clock')), 'clock ticks: ' + await txt(page, '#clock'));
  ok((await txt(page, '#gateN')) === '3' && (await page.$$('.gate-item')).length === 3, 'gate shows 3 waiting items');
  ok((await page.$$eval('.gate-stats dd', (e) => e.map((x) => x.textContent))).join(',') === '2,1,4,1', 'gate stat cells planned/working/approved/sent back: ' + (await page.$$eval('.gate-stats dd', (e) => e.map((x) => x.textContent))).join(','));
  ok((await txt(page, '#mDrafts')) === '8' && (await txt(page, '#mRate')) === '80%' && /^\d+s$/.test(await txt(page, '#mTime')), 'stage numbers: drafts ' + await txt(page, '#mDrafts') + ', approval ' + await txt(page, '#mRate') + ', avg ' + await txt(page, '#mTime'));
  ok((await page.$eval('#phases li.on', (e) => e.dataset.ph)) === 'draft', 'phase tracker is on Draft while an agent is working');
  ok((await page.$$('.tk-set')).length === 2 && /Waiting on you 3/.test(await txt(page, '.tk-set')), 'ticker has two sets and the live counts');
  ok((await txt(page, '#pulseN')) === '24' && /24 events/.test(await txt(page, '#sparkCap')), 'timeline counts 24 events: ' + await txt(page, '#sparkCap'));
  ok((await page.$$('#spark path')).length >= 24 + 3, 'timeline draws a marker per event: ' + (await page.$$('#spark path')).length + ' paths');
  ok((await page.$eval('#spark', (s) => s.getAttribute('viewBox'))).split(' ')[2] > 280, 'timeline sizes to its width: ' + await page.$eval('#spark', (s) => s.getAttribute('viewBox')));
  const px = await page.evaluate(() => { const c = document.getElementById('vortex'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++; return n; });
  ok(px > 3000, 'vortex canvas has drawn pixels: ' + px);
  const nd = await nodes(page);
  const box = await page.$eval('#vortex', (c) => { const r = c.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; });
  ok(nd.length === 5 && nd.every((q) => q.x > 0 && q.x < box.w && q.y > 0 && q.y < box.h), 'five agent nodes sit inside the stage');
  // hover card + click
  await page.mouse.move(box.x + nd[2].x, box.y + nd[2].y);
  await page.waitForTimeout(120);
  const cardOn = await page.$eval('#nodeCard', (e) => !e.hidden && e.textContent);
  ok(cardOn && cardOn.includes(nd[2].name) && /Waiting on you/.test(cardOn) && /Click to open/.test(cardOn), 'hovering an agent shows its card: ' + (cardOn || '').slice(0, 70));
  const cb = await page.$eval('#nodeCard', (e) => { const r = e.getBoundingClientRect(), s = document.getElementById('vortex').getBoundingClientRect(); return r.left >= s.left - 1 && r.right <= s.right + 1 && r.top >= s.top - 1 && r.bottom <= s.bottom + 1; });
  ok(cb, 'the hover card stays inside the stage');
  const cur = await page.$eval('#vortex', (e) => e.style.cursor);
  ok(cur === 'pointer', 'cursor is a pointer over an agent');
  const hn = (await nodes(page))[2];
  await page.mouse.click(box.x + hn.x, box.y + hn.y);
  await page.waitForTimeout(150);
  ok(await page.$eval('#drawerDlg', (d) => d.open), 'clicking an agent opens its panel');
  await page.keyboard.press('Escape');
  await page.mouse.move(box.x + 5, box.y + 5);
  await page.waitForTimeout(100);
  ok(await page.$eval('#nodeCard', (e) => e.hidden), 'the card hides when the pointer leaves the agent');
  // the vortex sits in front of the agents: an agent it covers fades, one clear of it does not
  const fadeRows = await page.evaluate(async () => {
    const V = window.__V, out = [], pb = document.getElementById('vPause');
    pb.click(); // freeze
    for (const yaw of [0, 0.9]) { V.yaw = yaw; pb.click(); pb.click(); await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); const n = V.nodes[0]; out.push({ yaw, cover: n.cover, fa: n.fa, x: n.x, y: n.y }); }
    V.yaw = 0; if (V.paused) pb.click();
    return out;
  });
  ok(fadeRows[0].cover > 0.9 && fadeRows[0].fa < 0.25, 'an agent behind the vortex fades out: alpha ' + fadeRows[0].fa.toFixed(2));
  ok(fadeRows[1].cover < 0.1 && fadeRows[1].fa > 0.7, 'an agent clear of the vortex is fully visible: alpha ' + fadeRows[1].fa.toFixed(2));
  await page.mouse.move(box.x + 5, box.y + 5);
  // pause / resume
  await page.click('#vPause');
  ok((await txt(page, '#vPause')) === 'Resume' && (await page.getAttribute('#vPause', 'aria-pressed')) === 'true', 'Pause switches to Resume');
  const p1 = (await nodes(page))[0].x; await page.waitForTimeout(350); const p2 = (await nodes(page))[0].x;
  ok(p1 === p2, 'paused: agents hold still');
  await page.click('#vPause'); await page.waitForTimeout(500);
  const p3 = (await nodes(page))[0].x;
  ok(p3 !== p2 && (await txt(page, '#vPause')) === 'Pause', 'resumed: agents move again');
  // drag rotates, reset restores
  const yaw0 = await page.evaluate(() => window.__V.yaw);
  await page.mouse.move(box.x + 40, box.y + box.h - 20); await page.mouse.down(); await page.mouse.move(box.x + 160, box.y + box.h - 20, { steps: 6 }); await page.mouse.up();
  const yaw1 = await page.evaluate(() => window.__V.yaw);
  ok(Math.abs(yaw1 - yaw0) > 0.5, 'dragging the vortex rotates the ring: yaw ' + yaw1.toFixed(2));
  ok(!(await page.$eval('#drawerDlg', (d) => d.open)), 'a drag is not a click');
  await page.click('#vFocus');
  ok((await page.getAttribute('#vFocus', 'aria-pressed')) === 'true' && (await page.evaluate(() => window.__V.zoomTo)) > 1, 'Focus zooms in');
  await page.click('#vReset');
  ok((await page.evaluate(() => window.__V.yaw)) === 0 && (await page.evaluate(() => window.__V.zoomTo)) === 1 && (await page.getAttribute('#vFocus', 'aria-pressed')) === 'false', 'Reset view restores rotation and zoom');
  // terminal card types the newest event
  await page.waitForTimeout(1800);
  ok((await txt(page, '#termTxt')) === 'Welcome email for new signups' && (await txt(page, '#termWho')) === 'Luma' && (await txt(page, '#termTag')) === 'finished', 'terminal shows the newest event: ' + await txt(page, '#termWho') + ' ' + await txt(page, '#termTag') + ' / ' + await txt(page, '#termTxt'));
  // data change flows through
  await page.evaluate(() => window.__db.doc('tasks/t9').update({ status: 'review', finishedAt: new Date().toISOString() }));
  await page.waitForFunction(() => document.getElementById('gateN').textContent === '4', null, { timeout: 4000 });
  ok((await page.$eval('#phases li.on', (e) => e.dataset.ph)) === 'review', 'phase tracker moves to Review when nothing is working');
  ok((await txt(page, '#sGate')) === '4' && /Waiting on you 4/.test(await txt(page, '.tk-set')), 'counters and ticker follow a change');
  // tabs live inside the page frame now
  await page.click('.tab[data-tab="knowledge"]');
  ok(await page.$eval('#knowledgeTab', (e) => e.classList.contains('active') && !!e.closest('.app')), 'Knowledge tab opens inside the padded page');
  await page.click('.tab[data-tab="stage"]');
  // libraries wired
  const gl = await page.evaluate(() => window.__gsapLog), ll = await page.evaluate(() => window.__lenis);
  ok(gl && gl.tl === 1 && gl.reg === 1 && gl.ticker === 1 && gl.lag === 1, 'GSAP: one intro timeline, plugin registered, Lenis driven from the ticker: ' + JSON.stringify(gl));
  ok(gl.from >= 8, 'GSAP: entrance and scroll-reveal tweens requested: ' + gl.from);
  ok(ll && ll.made === 1 && ll.opts.smoothWheel === true, 'Lenis created once with smooth wheel');
  ok((await page.$$('[data-lenis-prevent]')).length >= 6, 'dialogs and scroll panes keep native scrolling: ' + (await page.$$('[data-lenis-prevent]')).length);
  ok((await page.$$('#vt .ch')).length === 8 && (await page.getAttribute('#vt', 'aria-label')) === 'The Crew', 'title splits into letters but keeps its accessible name');
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors (desktop)');
  if (errors.length) failed++;
  await ctx.close();

  // ---------- libraries blocked: page must still be complete and visible
  ({ page, ctx, errors } = await open(browser, { n: 5, lib: 'none' }));
  const vis = await page.$$eval('.top, .gate, .brief, .vctl, .board-wrap, .pulse, .ticker', (els) => els.map((e) => getComputedStyle(e).opacity));
  ok(vis.every((o) => o === '1'), 'without the libraries every panel is fully visible');
  ok((await txt(page, '#vt')) === 'The Crew' && (await page.$$('#vt .ch')).length === 0, 'title stays plain text');
  ok((await txt(page, '#sGate')) === '3' && px > 0, 'numbers and vortex work without the libraries');
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors (no libraries)');
  if (errors.length) failed++;
  await ctx.close();

  // ---------- reduced motion
  ({ page, ctx, errors } = await open(browser, { n: 5, reduced: true }));
  const gl2 = await page.evaluate(() => window.__gsapLog), ll2 = await page.evaluate(() => window.__lenis);
  ok((!gl2 || gl2.tl === 0) && (!ll2 || ll2.made === 0), 'reduced motion: no GSAP timeline and no smooth scroll');
  ok(!(await page.$eval('#vPause', (e) => e.hidden)), 'reduced motion: pause control is shown');
  const g1 = await page.evaluate(() => document.getElementById('vortex').toDataURL().length + document.getElementById('vortex').toDataURL().slice(8000, 8300)); await page.waitForTimeout(500); const g2 = await page.evaluate(() => document.getElementById('vortex').toDataURL().length + document.getElementById('vortex').toDataURL().slice(8000, 8300));
  ok(g1 !== g2, 'reduced motion: the vortex still turns, calmly');
  const px2 = await page.evaluate(() => { const c = document.getElementById('vortex'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 20) n++; return n; });
  ok(px2 > 3000, 'reduced motion: still vortex frame is drawn');
  ok((await txt(page, '#termTxt')) === 'Welcome email for new signups', 'reduced motion: terminal text appears at once');
  const t0 = (await nodes(page))[0].x; await page.waitForTimeout(300);
  ok((await nodes(page))[0].x === t0, 'reduced motion: agents do not drift');
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors (reduced motion)');
  if (errors.length) failed++;
  await ctx.close();

  // ---------- phone width, 5 and 12 agents, every tab: no sideways scroll
  for (const n of [5, 12]) {
    ({ page, ctx, errors } = await open(browser, { n, width: 375, height: 800 }));
    for (const tab of ['stage', 'knowledge', 'messages', 'ideas']) {
      await page.click('.tab[data-tab="' + tab + '"]');
      await page.waitForTimeout(150);
      const sw = await page.evaluate(() => ({ s: document.documentElement.scrollWidth, w: window.innerWidth }));
      ok(sw.s <= sw.w, 'phone 375px, ' + n + ' agents, ' + tab + ' tab: no sideways scroll (' + sw.s + ' <= ' + sw.w + ')');
    }
    await page.click('.tab[data-tab="stage"]');
    if (n === 12) {
      const nn = await nodes(page);
      ok(nn.length === 12 && nn.every((q) => q.x > 0 && q.y > 0), 'twelve agents are placed on the ring');
    }
    ok(errors.length === 0, 'phone page errors: ' + errors.length + (errors[0] ? ' ' + errors[0] : ''));
    if (errors.length) console.log(errors.join('\n'));
    await ctx.close();
  }

  await browser.close();
  console.log(failed ? failed + ' check(s) FAILED' : 'ALL CHECKS PASSED');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
