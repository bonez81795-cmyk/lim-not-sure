const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path'); const stub = require('./stub4.js');
let failed = 0; const ok = (c, m) => { if (!c) failed++; console.log((c ? 'ok   ' : 'FAIL ') + m); };
async function open(browser, o = {}) {
  const ctx = await browser.newContext({ viewport: { width: o.w || 1440, height: o.h || 900 }, reducedMotion: o.reduced ? 'reduce' : 'no-preference' });
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/fonts|ERR_|Failed to load resource/.test(m.text())) errors.push('console: ' + m.text()); });
  await page.addInitScript(stub, o.n || 5);
  await page.route(/fonts\.|cdnjs|unpkg/, (r) => r.abort());
  await page.goto('file://' + path.resolve('crew-control.html'));
  await page.waitForSelector('.chip'); await page.waitForTimeout(300);
  return { page, ctx, errors };
}
const lights = (page) => page.evaluate(() => window.__V.flights.concat(window.__V.queue).map((f) => f.label));
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  let { page, ctx, errors } = await open(browser);
  // ---- replay of real recent events on load
  await page.waitForTimeout(2200);
  let l = await lights(page);
  ok(l.length >= 1 && l.length <= 3, 'on load the latest real events are replayed (max 3): ' + l.join(' | '));
  ok(await page.evaluate(() => /^(Welcome email for new signups|Homepage hero and layout notes|Pricing ladder good better best|Write the weekly update|List supplier questions)$/.test(window.__V.flights.concat(window.__V.queue)[0].label)), 'replayed lights carry the task title');
  await page.waitForTimeout(9000);
  ok((await lights(page)).length === 0, 'replayed lights finish and clear');
  // ---- live changes make lights
  await page.evaluate(() => window.__db.doc('tasks/t9').update({ status: 'review', finishedAt: new Date().toISOString(), updatedAt: new Date().toISOString() }));
  await page.waitForTimeout(150);
  ok((await lights(page)).includes('Draft the onboarding checklist'), 'a finished draft sends a light');
  let f = await page.evaluate(() => window.__V.flights.concat(window.__V.queue).find((q) => q.label === 'Draft the onboarding checklist'));
  ok(f.from.id === 'a4' && f.kind === 'draft' && f.exceptTo === 'a4' && JSON.stringify(f.to).includes('Manager'), 'it leaves the author and heads for the Manager');
  await page.evaluate(() => window.__db.collection('tasks').add({ agentId: 'a2', title: 'Plan the launch video', brief: 'x', status: 'planned', plannedBy: 'a0', createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }));
  await page.waitForTimeout(150);
  f = await page.evaluate(() => window.__V.flights.concat(window.__V.queue).find((q) => q.label === 'Plan the launch video'));
  ok(f && f.kind === 'brief' && f.from.id === 'a0' && f.to.id === 'a2', 'a new task flies from the manager to its agent');
  await page.evaluate(() => window.__db.doc('tasks/t6').update({ status: 'returned', note: 'x' }));
  await page.evaluate(() => window.__db.doc('tasks/t7').update({ status: 'approved' }));
  await page.waitForTimeout(150);
  l = await lights(page);
  ok(l.some((x) => x.startsWith('Pricing ladder')) && l.includes('Homepage hero and layout notes'), 'sent back and approved both send lights (long titles are shortened)');
  f = await page.evaluate(() => window.__V.flights.concat(window.__V.queue).find((q) => q.label === 'Homepage hero and layout notes'));
  ok(f.to === 'out' && f.kind === 'ship' && !!f.kc, 'an approved draft leaves the stage in green');
  await page.evaluate(() => window.__db.collection('messages').add({ fromId: 'a1', toId: 'a2', text: 'Try a flat monthly fee', createdAt: new Date().toISOString() }));
  await page.waitForTimeout(150);
  f = await page.evaluate(() => window.__V.flights.concat(window.__V.queue).find((q) => q.label === 'Try a flat monthly fee'));
  ok(f && f.from.id === 'a1' && f.to.id === 'a2', 'a message flies from sender to recipient');
  await page.evaluate(() => window.__db.collection('ideas').add({ title: 'Pay per job pricing', stage: 'Proposed', fromAgent: 'Kira', createdAt: new Date().toISOString() }));
  await page.waitForTimeout(150);
  f = await page.evaluate(() => window.__V.flights.concat(window.__V.queue).find((q) => q.label === 'Pay per job pricing'));
  ok(f && f.from.name === 'Kira' && JSON.stringify(f.to).includes('Manager'), 'a new idea flies from its agent to the Manager');
  const idea = await page.evaluate(async () => (await window.__db.collection('ideas').get()).docs[0].id);
  await page.evaluate((id) => window.__db.doc('ideas/' + id).update({ stage: 'Design' }), idea);
  await page.waitForTimeout(150);
  ok(await page.evaluate(() => window.__V.flights.concat(window.__V.queue).some((q) => q.label === 'Pay per job pricing' && JSON.stringify(q.to).includes('Designer'))), 'moving an idea to Design sends it to the Designer');
  // ---- one flight end to end, frame by frame
  await page.waitForTimeout(100);
  const r = await page.evaluate(() => {
    const V = window.__V; V.paused = true; V.flights.length = 0; V.queue.length = 0; V.beads.length = 0;
    window.__queueFlight({ from: { id: 'a4' }, to: { roles: ['Designer'] }, label: 'Idea: flat fee', kind: 'idea' });
    const seen = new Set(), out = {};
    for (let i = 0; i < 260; i++) {
      window.__draw(false, 1);
      for (const q of V.flights) seen.add(q.p < 62 ? 'in' : q.p < 90 ? 'hole' : 'out');
      if (!out.hit && V.nodes[1].hit > 0.5) out.hit = i;
      if (!V.flights.length && V.qGap < 0 && i > 5) { out.done = i; break; }
    }
    out.phases = [...seen].join(','); out.beads = V.beads.length; out.burstFired = V.ping;
    return out;
  });
  ok(r.phases === 'in,hole,out' && r.done > 130 && r.done < 200, 'a light goes in, through the hole, and out: ' + r.phases + ' in ' + r.done + ' frames');
  ok(r.hit > 100, 'the designated agent lights up when the idea arrives (frame ' + r.hit + ')');
  ok(r.beads > 8, 'a burst of beads leaves the vortex with it: ' + r.beads);
  await ctx.close();

  // ---- beads and spin
  ({ page, ctx, errors } = await open(browser));
  await page.waitForTimeout(3500);
  const b = await page.evaluate(() => ({ n: window.__V.beads.length, purple: window.__V.beads.filter((q) => q.rgb[2] > 200 && q.rgb[0] > 140 && q.rgb[1] < 200).length }));
  ok(b.n >= 3 && b.purple >= 2, 'the vortex has thrown out beads, mostly purple: ' + b.n + ' (' + b.purple + ' purple)');
  const sp = await page.evaluate(async () => { const V = window.__V, a = V.spin; await new Promise((r) => setTimeout(r, 1000)); return V.spin - a; });
  ok(sp > 150, 'the spin runs well above the old speed (old was 60 units/s idle, 100 busy): ' + sp.toFixed(0) + ' units/s');
  // spin speed is not constant
  const rates = await page.evaluate(async () => { const V = window.__V, r = []; for (let i = 0; i < 8; i++) { const a = V.spin; await new Promise((q) => setTimeout(q, 500)); r.push(V.spin - a); } return r; });
  ok(Math.max(...rates) / Math.min(...rates) > 1.1, 'the spin speed drifts at random: ' + rates.map((x) => x.toFixed(0)).join(' '));
  ok((await page.evaluate(() => getComputedStyle(document.body).backgroundColor)) === 'rgb(1, 0, 7)', 'page background is the space black');
  ok((await page.evaluate(() => getComputedStyle(document.querySelector('.vt b')).color)) === 'rgb(155, 109, 255)', 'the title accent is purple');
  const sz = await page.evaluate(() => { const st = document.querySelector('.stage').getBoundingClientRect(), vz = document.querySelector('#vortex').getBoundingClientRect(); return { stageH: st.height, vortexH: vz.height, vortexW: vz.width, title: parseFloat(getComputedStyle(document.querySelector('.vt')).fontSize) }; });
  ok(sz.vortexH > 480 && sz.vortexH / 900 > 0.55, 'the vortex fills most of the screen: ' + sz.vortexW.toFixed(0) + 'x' + sz.vortexH.toFixed(0) + ' in a 900px window');
  ok(sz.title <= 36, 'the title is small: ' + sz.title.toFixed(0) + 'px');
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors');
  if (errors.length) failed++;
  await ctx.close();

  // ---- phone: vortex is most of the first screen
  ({ page, ctx, errors } = await open(browser, { w: 390, h: 844 }));
  const ph = await page.evaluate(() => { const v = document.querySelector('#vortex').getBoundingClientRect(); return { h: v.height, w: v.width, sx: document.documentElement.scrollWidth }; });
  ok(ph.h >= 420 && ph.sx <= 390, 'phone: vortex ' + ph.w.toFixed(0) + 'x' + ph.h.toFixed(0) + ', no sideways scroll (' + ph.sx + ')');
  ok(errors.length === 0, 'phone: no page errors ' + errors.join(';'));
  await ctx.close();

  // ---- reduced motion: calm, still animated, lights slower
  ({ page, ctx, errors } = await open(browser, { reduced: true }));
  const calm = await page.evaluate(async () => { const V = window.__V, a = V.spin; await new Promise((r) => setTimeout(r, 1000)); return V.spin - a; });
  ok(calm < 140 && calm > 2, 'reduced motion: the vortex still turns, at a third of the speed: ' + calm.toFixed(0) + ' units/s');
  ok(errors.length === 0, 'reduced motion: no page errors');
  await ctx.close();
  await browser.close();
  console.log(failed ? failed + ' check(s) FAILED' : 'ALL CHECKS PASSED');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
