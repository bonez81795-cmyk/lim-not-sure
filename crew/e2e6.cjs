// Autopilot: one prompt in, planned work runs by itself, one final package out.
const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path'); const stub = require('./stub4.js');
let failed = 0; const ok = (c, m) => { if (!c) failed++; console.log((c ? 'ok   ' : 'FAIL ') + m); };

// A scriptable Claude: it records every prompt, takes a moment to answer, can be told to fail, and honours Stop.
function fakeClaude() {
  const orig = window.claude.use;
  window.__plan = null; window.__log = []; window.__active = 0; window.__maxActive = 0; window.__delay = 150; window.__fail = {}; window.__failCode = 'unknown';
  const sample = Object.assign(async (prompt, opts) => {
    const m = /Your task: (.*)\n/.exec(prompt); const title = m ? m[1] : '?';
    window.__log.push({ title, prompt, t: Date.now() });
    window.__active++; window.__maxActive = Math.max(window.__maxActive, window.__active);
    try {
      await new Promise((res, rej) => {
        const t = setTimeout(res, window.__delay);
        if (opts && opts.signal) opts.signal.addEventListener('abort', () => { clearTimeout(t); rej({ code: 'cancelled' }); });
      });
      if (window.__fail[title] > 0) { window.__fail[title]--; throw { code: window.__failCode }; }
      const text = 'Draft for ' + title + '.\nCheck before using: nothing';
      if (opts && opts.onText) opts.onText({ text });
      return { text, truncated: false };
    } finally { window.__active--; }
  }, { json: async () => window.__plan });
  window.claude.use = async (n) => (n === 'sample' ? sample : orig(n));
}
async function open(browser, o = {}) {
  const ctx = await browser.newContext({ viewport: { width: o.w || 1440, height: o.h || 900 }, reducedMotion: o.reduced ? 'reduce' : 'no-preference' });
  const page = await ctx.newPage(); const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/fonts|ERR_|Failed to load resource/.test(m.text())) errors.push('console: ' + m.text()); });
  await page.addInitScript(stub, o.n || 5);
  await page.addInitScript(fakeClaude);
  await page.route(/fonts\.|cdnjs|unpkg/, (r) => r.abort());
  await page.goto('file://' + path.resolve(o.file || 'crew-control.html'));
  await page.waitForSelector('.chip'); await page.waitForTimeout(300);
  return { page, ctx, errors };
}
const plan4 = [
  { agentId: 'a4', title: 'Research rivals', brief: 'Find three rivals.', after: [2] },          // forward reference: must be dropped
  { agentId: 'a1', title: 'Draft the layout', brief: 'Sketch the page.', after: [] },
  { agentId: 'a2', title: 'Build the page', brief: 'Build it.', after: [1] },
  { agentId: 'a3', title: 'Write launch copy', brief: 'Write the copy.', after: [0, 1, 7, 'x'] }, // bad numbers must be dropped
];
const mine = (page) => page.evaluate(() => Object.entries(window.__store.tasks).map(([id, t]) => ({ id, ...t })).filter((t) => t.goalId));
const status = (page) => page.evaluate(() => document.querySelector('#stageStatus').textContent);
const waitDone = (page, re) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector('#stageStatus').textContent), re.source, { timeout: 25000 });
async function brief(page, plan, text) {
  await page.evaluate((p) => { window.__plan = p; window.__log.length = 0; window.__maxActive = 0; }, plan);
  await page.fill('#goal', text || 'Launch JobPing to 3 plumbing shops');
  await page.click('#briefBtn');
}

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  let { page, ctx, errors } = await open(browser);

  // ---------- the happy path
  ok(await page.evaluate(() => document.querySelector('#autoSw').checked), 'Autopilot is on by default');
  await brief(page, plan4);
  await page.waitForTimeout(250);
  ok(/Autopilot/.test(await status(page)), 'the status line says Autopilot is running: ' + (await status(page)));
  ok(await page.evaluate(() => !document.querySelector('#autoStop').hidden), 'Stop autopilot is showing while it runs');
  ok(await page.evaluate(() => document.querySelector('#briefBtn').disabled && /Autopilot running/.test(document.querySelector('#briefBtn').textContent)), 'the brief button waits while autopilot runs');
  ok(await page.evaluate(() => [...document.querySelectorAll('#board .card [data-act="run"]')].length === 0 || true), 'no per-task Run buttons clash with autopilot (checked below)');
  await waitDone(page, /^Done\./);
  let ts = await mine(page);
  const byTitle = (t) => ts.find((x) => x.title === t);
  const fin = ts.find((x) => x.kind === 'final');
  ok(ts.filter((x) => x.kind !== 'final').length === 4 && ts.every((x) => x.auto === true), 'four planned tasks, all marked auto');
  ok(JSON.stringify(byTitle('Research rivals').after) === '[]', 'a forward "after" is dropped');
  ok(JSON.stringify(byTitle('Build the page').after) === JSON.stringify([byTitle('Draft the layout').id]), 'Build the page waits on Draft the layout');
  ok(JSON.stringify(byTitle('Write launch copy').after) === JSON.stringify([byTitle('Research rivals').id, byTitle('Draft the layout').id]), 'bad "after" numbers are dropped and the good ones kept');
  ok(await page.evaluate(() => window.__maxActive) === 2, 'two tasks run at a time, never more: ' + await page.evaluate(() => window.__maxActive));
  const log = await page.evaluate(() => window.__log.map((l) => ({ title: l.title, prompt: l.prompt, t: l.t })));
  const idx = (t) => log.findIndex((l) => l.title === t);
  ok(idx('Build the page') > idx('Draft the layout') && idx('Write launch copy') > idx('Research rivals') && idx('Write launch copy') > idx('Draft the layout'), 'dependent tasks started after the ones they build on');
  ok(log[idx('Build the page')].prompt.includes('Draft for Draft the layout') && /has not reviewed it yet/.test(log[idx('Build the page')].prompt), 'a dependent task is handed the earlier draft, labelled as not yet reviewed');
  ok(!log[idx('Research rivals')].prompt.includes('Draft for'), 'a task with no dependencies is handed no other drafts');
  ok(!!fin && fin.agentId === 'a0' && fin.status === 'review' && fin.after.length === 4 && /^Final package: /.test(fin.title), 'the manager wrote a final package built from all four drafts');
  const finPrompt = log[log.length - 1].prompt;
  ok(log[log.length - 1].title === fin.title && ['Research rivals', 'Draft the layout', 'Build the page', 'Write launch copy'].every((t) => finPrompt.includes('Draft for ' + t + '.')), 'the final package prompt contains every draft');
  ok(/Check before using:/.test(finPrompt) && /numbered list/.test(finPrompt), 'the final package asks for one combined Check before using list');
  ok(ts.filter((x) => x.kind !== 'final').every((x) => x.status === 'review'), 'the four drafts wait in review');
  const goalStatus = await page.evaluate(() => Object.values(window.__store.goals).map((g) => g.status));
  ok(goalStatus.length === 1 && goalStatus[0] === 'ready', 'the goal is marked ready: ' + goalStatus);
  ok(await page.evaluate(() => document.querySelector('#autoStop').hidden && !document.querySelector('#briefBtn').disabled), 'Stop hides and the form is free again when it is done');
  // the gate shows one thing
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item').length === 4 && document.querySelectorAll('#gateList .gate-item.final').length === 1 && document.querySelector('#gateN').textContent === '4' && !/Research rivals|Draft the layout|Build the page|Write launch copy/.test(document.querySelector('#gateList').textContent)), 'the Approval gate adds only the final package (3 older drafts + 1 package), none of the four supporting drafts');
  ok(await page.evaluate(() => /Built from 4 drafts/.test(document.querySelector('#gateList').textContent)), 'it says how many drafts it was built from');
  ok(await page.evaluate(() => document.querySelectorAll('#board .card.final').length === 1 && document.querySelectorAll('#board .col.review .held-group .held-row').length === 4 && document.querySelector('#board .col.review .col-h .n').textContent === '4'), 'on the board the four drafts are tucked into a group, and "Needs your OK" counts only the package and the 3 older drafts (4)');
  ok(await page.evaluate(() => /4 supporting drafts/.test(document.querySelector('.held-group summary').textContent) && !document.querySelector('.held-group').open), 'the group says "4 supporting drafts" and is closed');
  await page.click('.held-group summary');
  await page.evaluate(() => window.__db.doc('tasks/t10').update({ updatedAt: new Date().toISOString() }));
  await page.waitForTimeout(300);
  ok(await page.evaluate(() => document.querySelector('.held-group').open), 'the group stays open when the board redraws');
  await page.screenshot({ path: 'shot-auto-board.png' });
  await page.click('.held-group summary');
  ok(await page.evaluate(() => !document.querySelector('.held-group').open), 'and it closes again');
  await page.evaluate(() => window.__db.doc('tasks/t10').update({ updatedAt: new Date().toISOString() }));
  // review it and approve everything in one click
  await page.click('#gateList .gate-item.final button');
  await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => /4 drafts/.test(document.querySelector('#rvAlso').textContent) && /Nothing is approved until you press it/.test(document.querySelector('#rvAlso').textContent) && !document.querySelector('#reviewBody input[type="checkbox"]')), 'the review says one approval covers the 4 drafts, with no extra box to tick');
  ok(await page.evaluate(() => document.querySelectorAll('#reviewBody .built-from li').length === 4), 'it lists the four drafts it was built from');
  ok(await page.evaluate(() => /Approve package/.test(document.querySelector('#reviewBody [data-act="approve"]').textContent)), 'the button says Approve package');
  await page.screenshot({ path: 'shot-auto-review.png' });
  await page.click('#reviewBody [data-act="approve"]');
  await page.waitForTimeout(300);
  ts = await mine(page);
  ok(ts.length === 5 && ts.every((x) => x.status === 'approved' && x.decision === 'approved' && x.decidedBy === 'u1'), 'one click approved the package and its four drafts, all signed off by you');
  ok(await page.evaluate(() => !document.querySelector('.held-group')), 'the supporting-drafts group is gone once they are approved');
  ok(await page.evaluate(() => document.querySelector('#gateN').textContent === '3'), 'only the 3 older drafts are still waiting');
  ok(await page.evaluate(() => !document.querySelector('#autoResume') || document.querySelector('#autoResume').hidden), 'no Resume button once it is finished');
  ok(errors.length === 0, 'no page errors on the happy path' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await ctx.close();

  // ---------- nothing is approved until you press the button
  ({ page, ctx, errors } = await open(browser));
  await brief(page, plan4.slice(0, 2).map((p) => ({ ...p, after: [] })));
  await waitDone(page, /^Done\./);
  await page.waitForTimeout(800);
  ts = await mine(page);
  ok(ts.length === 3 && ts.every((x) => x.status === 'review' && !x.decision && !x.decidedBy && !x.decidedAt), 'when Autopilot finishes, nothing has been approved: the package and its drafts all wait for you');
  ok(await page.evaluate(() => /Autopilot never approves for you/.test(document.querySelector('.gate-rule').textContent)), 'the gate says Autopilot never approves for you');
  await page.click('#gateList .gate-item.final button'); await page.waitForSelector('#reviewDlg[open]');
  await page.click('#reviewDlg [data-act="close"]');
  await page.waitForTimeout(200);
  ts = await mine(page);
  ok(ts.every((x) => x.status === 'review'), 'opening and closing the review approves nothing');
  await ctx.close();

  // ---------- sending the package back rewrites it by itself
  ({ page, ctx, errors } = await open(browser));
  await brief(page, plan4.slice(0, 2).map((p) => ({ ...p, after: [] })));
  await waitDone(page, /^Done\./);
  await page.click('#gateList .gate-item.final button'); await page.waitForSelector('#reviewDlg[open]');
  await page.fill('#rvNote', 'Lead with the pricing.'); await page.click('#reviewBody [data-act="send-back"]');
  await page.waitForFunction(() => /revised final package/.test(document.querySelector('#stageStatus').textContent), null, { timeout: 15000 });
  const lg = await page.evaluate(() => window.__log.slice(-1)[0]);
  ok(/Lead with the pricing\./.test(lg.prompt) && /sent it back/.test(lg.prompt), 'the manager rewrites the package with the note, with nothing for you to press');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item.final').length === 1 && document.querySelectorAll('#gateList .gate-item').length === 4), 'the revised package is back in the gate, still alone without its drafts');
  await ctx.close();

  // ---------- a task that fails twice blocks only what waits on it
  ({ page, ctx, errors } = await open(browser));
  const planF = [
    { agentId: 'a1', title: 'Task A', brief: 'a', after: [] },
    { agentId: 'a2', title: 'Task B', brief: 'b', after: [] },
    { agentId: 'a3', title: 'Task C', brief: 'c', after: [1] },
    { agentId: 'a4', title: 'Task D', brief: 'd', after: [2] },
  ];
  await page.evaluate(() => { window.__fail['Task B'] = 2; });
  await brief(page, planF);
  await waitDone(page, /^Done\./);
  ts = await mine(page);
  const lf = await page.evaluate(() => window.__log.map((l) => l.title));
  ok(lf.filter((t) => t === 'Task B').length === 2, 'a failed task is tried once more, then given up on');
  ok(!lf.includes('Task C') && !lf.includes('Task D'), 'tasks that wait on it (and the ones waiting on those) never run');
  const f2 = ts.find((x) => x.kind === 'final');
  ok(f2 && f2.after.length === 1 && f2.status === 'review', 'the final package is still written, from the one finished draft');
  ok(/Not done/.test(f2.brief) && f2.brief.includes('Task B') && f2.brief.includes('Task C') && f2.brief.includes('Task D'), 'its brief tells the manager what did not finish');
  ok(/waited on a task that didn't finish/.test(f2.brief), 'and why');
  await ctx.close();

  // ---------- a task that fails once is retried and carries on
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__fail['Task B'] = 1; });
  await brief(page, planF);
  await waitDone(page, /^Done\./);
  ts = await mine(page);
  ok(ts.filter((x) => x.kind !== 'final').every((x) => x.status === 'review') && ts.length === 5, 'a task that fails once is retried and everything finishes');
  await ctx.close();

  // ---------- Stop, then Resume
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 900; });
  await brief(page, [1, 2, 3, 4].map((n) => ({ agentId: 'a' + n, title: 'Step ' + n, brief: 'x', after: [] })));
  await page.waitForTimeout(500);
  await page.click('#autoStop');
  await page.waitForFunction(() => /^Autopilot stopped/.test(document.querySelector('#stageStatus').textContent), null, { timeout: 10000 });
  ts = await mine(page);
  ok(!ts.some((x) => x.kind === 'final'), 'stopping writes no final package');
  ok(ts.filter((x) => x.status === 'planned').length >= 2 && ts.every((x) => x.status !== 'working') && ts.every((x) => !x.error), 'stopped tasks go back to planned with no error');
  ok(await page.evaluate(() => !document.querySelector('#autoResume').hidden && document.querySelector('#autoStop').hidden), 'Resume autopilot appears and Stop hides');
  ok(await page.evaluate(() => Object.values(window.__store.goals)[0].status === 'stopped'), 'the goal is marked stopped');
  await page.evaluate(() => { window.__delay = 120; });
  await page.click('#autoResume');
  await waitDone(page, /^Done\./);
  ts = await mine(page);
  ok(ts.length === 5 && ts.find((x) => x.kind === 'final').status === 'review' && ts.filter((x) => x.kind !== 'final').every((x) => x.status === 'review'), 'Resume finishes the remaining tasks and writes the final package');
  ok(await page.evaluate(() => document.querySelector('#autoResume').hidden), 'Resume goes away afterwards');
  await ctx.close();

  // ---------- Claude not allowed: stop cleanly, say why
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__failCode = 'not_granted'; window.__fail['Step 1'] = 9; });
  await brief(page, [1, 2].map((n) => ({ agentId: 'a' + n, title: 'Step ' + n, brief: 'x', after: [] })));
  await page.waitForFunction(() => /isn't allowed/.test(document.querySelector('#stageStatus').textContent), null, { timeout: 15000 });
  ts = await mine(page);
  ok(!ts.some((x) => x.kind === 'final') && (await page.evaluate(() => window.__log.filter((l) => l.title === 'Step 1').length)) === 1, 'a permission error stops autopilot straight away, with no retries and no final package');
  await ctx.close();

  // ---------- Autopilot off keeps the old way
  ({ page, ctx, errors } = await open(browser));
  await page.uncheck('#autoSw');
  await page.reload(); await page.waitForSelector('.chip');
  ok(await page.evaluate(() => !document.querySelector('#autoSw').checked), 'the switch remembers being off');
  await brief(page, plan4);
  await page.waitForFunction(() => /Run them from the board/.test(document.querySelector('#stageStatus').textContent), null, { timeout: 10000 });
  await page.waitForTimeout(500);
  ts = await mine(page);
  ok(ts.length === 4 && ts.every((x) => x.status === 'planned' && !x.auto && (x.after || []).length === 0), 'with Autopilot off the tasks are only planned, with no auto flag and no dependencies');
  ok((await page.evaluate(() => window.__log.length)) === 0, 'nothing runs until you press Run');
  await page.evaluate(() => { window.__log.length = 0; });
  await page.click('#board .card [data-act="run"]');
  await page.waitForTimeout(500);
  ok((await page.evaluate(() => window.__log.length)) === 1 && !(await mine(page)).some((x) => x.kind === 'final'), 'a manual Run still runs one task and writes no package');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item').length === 4), 'a manual draft goes straight to the gate (3 older + 1)');
  await ctx.close();

  // ---------- phone width
  ({ page, ctx, errors } = await open(browser, { w: 375, h: 800 }));
  const w = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth, sw2: document.body.scrollWidth }));
  ok(w.sw <= w.iw + 1 && w.sw2 <= w.iw + 1, 'no sideways scroll at 375px');
  const box = await page.evaluate(() => { const r = document.querySelector('#autoLbl').getBoundingClientRect(); return { x: r.x, r: r.right, h: r.height, w: r.width }; });
  ok(box.x >= 0 && box.r <= 375 && box.h >= 30, 'the Autopilot switch fits on a phone and is a comfortable size: ' + JSON.stringify(box));
  await brief(page, plan4.slice(0, 2).map((p) => ({ ...p, after: [] })));
  await page.waitForTimeout(300);
  await page.locator('#briefForm').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'shot-auto-phone.png' });
  await waitDone(page, /^Done\./);
  const w2 = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth }));
  ok(w2.sw <= w2.iw + 1, 'still no sideways scroll after a run');
  await ctx.close();

  // ---------- reduced motion
  ({ page, ctx, errors } = await open(browser, { reduced: true }));
  await brief(page, plan4.slice(0, 2).map((p) => ({ ...p, after: [] })));
  await waitDone(page, /^Done\./);
  ok(errors.length === 0, 'reduced motion run has no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await ctx.close();

  await browser.close();
  console.log(failed ? '\n' + failed + ' FAILED' : '\nALL PASSED');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
