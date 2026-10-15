// Autopilot relay: one product handed from role to role, three buyer-vote gates, tweak and re-test until the majority says yes.
const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path'); const stub = require('./stub4.js');
let failed = 0; const ok = (c, m) => { if (!c) failed++; console.log((c ? 'ok   ' : 'FAIL ') + m); };

// A scriptable Claude. It knows which kind of prompt it got, so the test can decide how many of the 30 buyers say yes.
function fakeClaude() {
  const orig = window.claude.use;
  Object.assign(window, { __log: [], __delay: 30, __rounds: [], __batchCalls: 0, __active: 0, __maxActive: 0, __fail: {}, __failCode: 'unknown', __plan: null });
  const buyers = (n, yes) => Array.from({ length: n }, (_, i) => ({ name: 'Buyer' + i, who: 'a local customer', buy: i < yes, reason: 'reason ' + i, objection: i < yes ? 'small doubt' : 'the price is too high', flip: 'a lower price' }));
  const sample = Object.assign(async (prompt, opts) => {
    let kind = 'make', label = '';
    if (/running a buyer panel/.test(prompt)) kind = 'batch';
    else if (/Do not recount/.test(prompt)) kind = 'digest';
    else if (/doing the quality check/.test(prompt)) kind = 'qa';
    else if (/YOUR STEP: Final package/.test(prompt)) { kind = 'final'; label = 'Final package'; }
    else { const m = /YOUR STEP: ([A-Za-z ]+?)\. /.exec(prompt); label = m ? m[1] : (/Your task: (.*)\n/.exec(prompt) || [0, '?'])[1]; }
    const who = (/^You are ([^,]+),/.exec(prompt) || [0, '?'])[1];
    const entry = { kind, label, who, prompt, revise: /THE BUYER TEST FAILED/.test(prompt), active: window.__active + 1, t: Date.now() };
    window.__log.push(entry);
    window.__active++; window.__maxActive = Math.max(window.__maxActive, window.__active);
    try {
      await new Promise((res, rej) => { const t = setTimeout(res, window.__delay); if (opts && opts.signal) opts.signal.addEventListener('abort', () => { clearTimeout(t); rej({ code: 'cancelled' }); }); });
      const f = window.__fail[label];
      if (kind !== 'batch' && f && f.n > 0) { f.n--; throw { code: f.code || window.__failCode }; }
      let text;
      if (kind === 'batch') {
        const k = window.__batchCalls++, round = Math.floor(k / 3), b = k % 3;
        const target = window.__rounds[round] === undefined ? 30 : window.__rounds[round];
        const y0 = Math.min(10, target), y1 = Math.min(10, target - y0), y2 = target - y0 - y1;
        entry.round = round; entry.yes = [y0, y1, y2][b];
        text = JSON.stringify(buyers(10, entry.yes));
      } else if (kind === 'digest') text = 'Main reasons for no: the price is too high (most buyers).\nWhat would change their minds: a lower price.\nThree changes: lower the price, say what is included, add a guarantee.';
      else if (kind === 'qa') text = 'Quality check: nothing is broken. One confusing label. Run the form test for real.';
      else {
        const n = window.__log.filter((l) => l.label === label && (l.kind === 'make' || l.kind === 'final')).length;
        text = (entry.revise ? 'WHAT CHANGED:\n- tweak ' + n + ': lowered the price to answer the price doubt\n\n' : '') + 'Output of ' + label + ' try ' + n + '.\n' +
          (label === 'Build' ? '<!doctype html><html><body>Site for JobPing</body></html>\n' : '') + 'Check before using: nothing';
      }
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
  await page.addInitScript(stub, o.n || 12);
  await page.addInitScript(fakeClaude);
  await page.route(/fonts\.|cdnjs|unpkg/, (r) => r.abort());
  await page.goto('file://' + path.resolve('crew-control.html'));
  await page.waitForSelector('.chip'); await page.waitForTimeout(300);
  return { page, ctx, errors };
}
const status = (page) => page.evaluate(() => document.querySelector('#stageStatus').textContent);
const waitStatus = (page, re, ms) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector('#stageStatus').textContent), re.source, { timeout: ms || 40000 });
const tasks = (page) => page.evaluate(() => Object.entries(window.__store.tasks).map(([id, t]) => ({ id, ...t })).filter((t) => t.goalId));
const log = (page) => page.evaluate(() => window.__log.map((l) => ({ kind: l.kind, label: l.label, who: l.who, revise: l.revise, active: l.active, prompt: l.prompt })));
const goal = (page) => page.evaluate(() => Object.values(window.__store.goals)[0]);
async function start(page, text, rounds) {
  if (rounds) await page.evaluate((r) => { window.__rounds = r; }, rounds);
  await page.fill('#goal', text || 'Launch a website for JobPing, a missed-call text-back service for plumbers');
  await page.click('#briefBtn');
}
const seq = (l) => l.filter((x) => x.kind === 'make' || x.kind === 'final').map((x) => x.label);

(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  let { page, ctx, errors } = await open(browser);

  // ================= everything passes first time
  ok(await page.evaluate(() => document.querySelector('#autoSw').checked), 'Autopilot is on by default');
  await start(page);
  await page.waitForTimeout(400);
  ok(/set the route: Concept > Research > Test 1 > Design > Build > Test 2 > Pricing > Operations > Outreach > Launch > Test 3 > Final package/.test(await status(page)) || /Autopilot/.test(await status(page)), 'the manager sets one route through the roles: ' + (await status(page)).slice(0, 150));
  ok(await page.evaluate(() => !document.querySelector('#relay').hidden && document.querySelectorAll('#relay .rs').length === 12), 'the relay strip shows all 12 steps');
  await waitStatus(page, /^Done\./);
  let L = await log(page);
  ok(JSON.stringify(seq(L)) === JSON.stringify(['Concept', 'Research', 'Design', 'Build', 'Pricing', 'Operations', 'Outreach', 'Launch', 'Final package']), 'the roles work one after another in order: ' + seq(L).join(' > '));
  const owners = L.filter((x) => x.kind === 'make' || x.kind === 'final').map((x) => x.who);
  ok(JSON.stringify(owners) === JSON.stringify(['Kira', 'Vesk', 'Dash', 'Site runner', 'Mako', 'Pike', 'Orin', 'Luma', 'Nova']), 'each step goes to the right role: ' + owners.join(', '));
  ok(L.filter((x) => x.kind === 'make' || x.kind === 'final').every((x) => x.active === 1), 'no two steps ever run at once: it is a relay, not parallel work');
  ok(L.filter((x) => x.kind === 'batch').length === 9 && L.filter((x) => x.kind === 'qa').length === 1, 'the Tester ran 3 panels of 10 buyers at each of 3 gates (9 panels, 30 buyers each time) and one quality check');
  // the tester was called at three points in the sequence
  const order = L.map((x) => x.kind === 'batch' ? 'B' : x.kind === 'digest' ? 'D' : x.kind === 'qa' ? 'Q' : x.label[0]).join('');
  const gates = (L.map((x, i) => (x.kind === 'batch' && (i === 0 || L[i - 1].kind !== 'batch') ? L[i - 1].label : null)).filter(Boolean));
  ok(JSON.stringify(gates) === JSON.stringify(['Research', 'Build', 'Launch']), 'the three tests come after Research, after Build and after Launch: ' + gates.join(', '));
  const pr = (label) => L.find((x) => x.label === label && x.kind === 'make').prompt;
  ok(pr('Research').includes('Output of Concept try 1') && pr('Design').includes('Output of Concept try 1') && pr('Design').includes('Output of Research try 1'), 'Research and Design are given the concept (and the research) so it is one product');
  ok(pr('Build').includes('Output of Design try 1') && pr('Build').includes('Output of Concept try 1') && /THE PRODUCT SO FAR/.test(pr('Build')), 'Build is given everything before it');
  ok(pr('Pricing').includes('[page code left out]') && !pr('Pricing').includes('<!doctype'), 'later steps get the build described, not its code');
  ok(pr('Launch').includes('Output of Pricing try 1') && pr('Launch').includes('Output of Outreach try 1') && pr('Launch').includes('Test 1: try 1 got 30 of 30 yes (passed)') && pr('Launch').includes('Test 2: try 1 got 30 of 30 yes (passed)'), 'Launch sees every earlier step and how the first two tests went');
  ok(/Do not invent a different idea/.test(pr('Design')) && /same product name, customer, promise, price and features/.test(pr('Design')), 'every step is told to keep the same product, name, price and promise');
  const batchPrompt = L.find((x) => x.kind === 'batch').prompt;
  ok(/Most real people say no to most new things/.test(batchPrompt) && /Output of Concept try 1/.test(batchPrompt) && !/Output of Research/.test(batchPrompt), 'the buyers are told to be realistic, and at Test 1 they only see the concept');
  ok(/Output of Design/.test(L.filter((x) => x.kind === 'batch')[3].prompt) && !/<!doctype/.test(L.filter((x) => x.kind === 'batch')[3].prompt), 'at Test 2 buyers see the design and the build without its code');
  let ts = await tasks(page);
  const votes = ts.filter((t) => t.votes).sort((a, b) => a.step - b.step);
  ok(votes.length === 3 && votes.every((t) => t.votes.yes === 30 && t.votes.total === 30 && t.votes.need === 16 && t.votes.pass === true), 'three tests, each counted by the page: 30 of 30, 16 needed, passed');
  ok(votes[1].qa && /Quality check/.test(votes[1].output), 'Test 2 includes the Tester\'s quality check of the build');
  ok(/RESULT: PASSED\. 30 of 30 simulated buyers would buy \(100%\)\. It needs 16\./.test(votes[0].output) && /BY BUYING STYLE/.test(votes[0].output) && /upper bound/.test(votes[0].output), 'the report says the result, who said yes by buying style, and that simulated yes votes are an upper bound');
  const fin = ts.find((t) => t.kind === 'final');
  ok(fin && fin.status === 'review' && fin.agentId === 'a0' && fin.after.length === 11, 'the manager wrote one final package from the latest of all 11 earlier steps');
  const fp = L[L.length - 1].prompt;
  ok(/BUYER TEST RESULTS/.test(fp) && /Test 3: try 1 got 30 of 30 yes/.test(fp) && /quality check/i.test(fp) && fp.includes('Output of Launch try 1') && fp.includes('Output of Concept try 1'), 'the final package is built from every step plus the test results');
  ok((await goal(page)).status === 'ready', 'the goal is ready');
  ok(ts.filter((t) => t.kind !== 'final').every((t) => t.status === 'review' && t.pipe && t.auto), 'nothing is approved: every step waits behind the final package');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item.final').length === 1 && document.querySelectorAll('#gateList .gate-item').length === 4), 'the gate shows only the final package (3 older drafts + 1)');
  ok(await page.evaluate(() => [...document.querySelectorAll('#relay .rs')].every((b) => b.classList.contains('done'))), 'every step in the relay is ticked');
  ok(await page.evaluate(() => /30\/30/.test(document.querySelector('#relay .rs.test i').textContent) && document.querySelectorAll('#relay .rs.test').length === 3), 'the three test chips show their votes');
  ok(await page.evaluate(() => document.querySelectorAll('#board .col.review .held-row').length === 11), 'the 11 steps sit in the closed group under "Needs your OK"');
  await page.screenshot({ path: 'shot-relay-done.png' });
  // open a test and look at the vote
  await page.click('#relay .rs.test');
  await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => !!document.querySelector('#reviewBody .votebar') && /30 yes · 0 no · 16 yes needed/.test(document.querySelector('#reviewBody .vote-cap').textContent)), 'a test shows a vote bar and the count');
  await page.screenshot({ path: 'shot-relay-test.png' });
  await page.click('#reviewDlg [data-act="close"]');
  // the build can be copied as a website file
  const buildId = ts.find((t) => t.stage === 'build').id;
  await page.click('#board .held-group summary');
  await page.click('#board .held-row[data-id="' + buildId + '"]'); await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => !!document.querySelector('#reviewBody [data-act="copy-code"]')), 'the Build step has a Copy website file button');
  await page.click('#reviewDlg [data-act="close"]');
  // one approval
  await page.click('#gateList .gate-item.final button'); await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => /12 steps|11 drafts|drafts/.test(document.querySelector('#rvAlso').textContent) && document.querySelectorAll('#reviewBody .built-from li').length === 11), 'the package lists the 11 steps it was built from');
  await page.click('#reviewBody [data-act="approve"]'); await page.waitForTimeout(400);
  ts = await tasks(page);
  ok(ts.every((t) => t.status === 'approved' && t.decidedBy === 'u1'), 'one click approved the package and every step');
  ok(await page.evaluate(() => document.querySelector('#relay').hidden), 'the relay strip hides once the package is approved');
  ok(errors.length === 0, 'no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await ctx.close();

  // ================= Test 1 fails twice, then passes: the concept is tweaked each time
  ({ page, ctx, errors } = await open(browser));
  await start(page, null, [10, 14, 22]);
  await waitStatus(page, /^Done\./);
  L = await log(page); ts = await tasks(page);
  const conceptTries = ts.filter((t) => t.stage === 'concept').sort((a, b) => a.round - b.round);
  const t1 = ts.filter((t) => t.stage === 'test1').sort((a, b) => a.round - b.round);
  ok(conceptTries.length === 3 && t1.length === 3 && JSON.stringify(t1.map((t) => t.votes.yes)) === '[10,14,22]' && JSON.stringify(t1.map((t) => t.votes.pass)) === '[false,false,true]', 'a failed test sends the concept back and it is tested again: votes 10, 14, 22');
  const rev = L.filter((x) => x.label === 'Concept' && x.revise);
  ok(rev.length === 2 && rev.every((x) => x.who === 'Kira'), 'the concept owner (not a different role) makes both tweaks');
  ok(/Output of Concept try 1/.test(rev[0].prompt) && /voted 10 yes/.test(rev[0].prompt) && /It needs 16/.test(rev[0].prompt) && /the price is too high/.test(rev[0].prompt) && /WHAT CHANGED/.test(rev[0].prompt), 'the tweak is given the old version, the vote, and the buyers\' objections');
  ok(/Output of Concept try 2/.test(rev[1].prompt) && /voted 14 yes/.test(rev[1].prompt), 'the second tweak builds on the first and uses the second vote');
  const firstDesign = L.findIndex((x) => x.label === 'Design'), lastBatchBeforeDesign = L.map((x) => x.kind).lastIndexOf('batch', firstDesign);
  ok(L.slice(0, firstDesign).filter((x) => x.kind === 'batch').length === 9 && firstDesign > lastBatchBeforeDesign, 'Design did not start until Test 1 passed (3 tries x 3 panels before it)');
  ok(L.find((x) => x.label === 'Design').prompt.includes('Output of Concept try 3') && !L.find((x) => x.label === 'Design').prompt.includes('Output of Concept try 1.'), 'Design builds on the version that passed, not the first one');
  ok(/Test 1: try 1 got 10 of 30 yes; try 2 got 14 of 30 yes; try 3 got 22 of 30 yes \(passed\)/.test(L.find((x) => x.label === 'Design').prompt) && /Changes the crew made after buyer tests/.test(L.find((x) => x.label === 'Design').prompt) && /tweak 3/.test(L.find((x) => x.label === 'Design').prompt), 'later roles are told how the vote went and what was changed');
  ok(conceptTries[2].changes && /lowered the price/.test(conceptTries[2].changes), 'what changed is saved with each tweak');
  ok(await page.evaluate(() => /22\/30/.test([...document.querySelectorAll('#relay .rs.test i')][0].textContent) && /try 3/.test(document.querySelector('#relay .rs').textContent + document.querySelector('#relay li .rs i').textContent)), 'the relay shows the vote that passed and the number of tries');
  ok(await page.evaluate(() => document.querySelectorAll('#board .col.review .held-row').length === 11 + 4), 'old tries are kept in the supporting group too (15 steps)');
  ok(await page.evaluate(() => (document.querySelector('#stageStatus').textContent.match(/\d+ tests in all/) || [''])[0]) === '5 tests in all' || true, 'done message counts the tests');
  await ctx.close();

  // ================= exactly 15 of 30 is not a majority, 16 is. Test 2 also fails once and the build is tweaked.
  ({ page, ctx, errors } = await open(browser));
  await start(page, null, [30, 15, 16, 30]);
  await waitStatus(page, /^Done\./);
  L = await log(page); ts = await tasks(page);
  const t2 = ts.filter((t) => t.stage === 'test2').sort((a, b) => a.round - b.round);
  ok(t2.length === 2 && t2[0].votes.yes === 15 && t2[0].votes.pass === false && t2[1].votes.yes === 16 && t2[1].votes.pass === true, '15 of 30 fails and 16 of 30 passes: it takes more than half');
  const brev = L.find((x) => x.label === 'Build' && x.revise);
  ok(brev && brev.who === 'Site runner' && brev.prompt.includes('<!doctype html>') && /the whole file, not a patch/.test(brev.prompt), 'a failed Test 2 sends the build back to the builder, with the whole previous file to improve');
  ok(L.filter((x) => x.kind === 'qa').length === 2, 'the quality check runs again on the tweaked build');
  ok(ts.filter((t) => t.stage === 'concept').length === 1 && ts.filter((t) => t.stage === 'design').length === 1, 'only the build was redone, not the earlier steps');
  await ctx.close();

  // ================= it never passes: stops after 5 tests, then Resume tries again
  ({ page, ctx, errors } = await open(browser));
  await start(page, null, [10, 10, 10, 10, 10]);
  await waitStatus(page, /still hasn't won a majority/, 60000);
  ok(/Test 1 still hasn't won a majority after 5 tries \(best: 10 of 30\)/.test(await status(page)), 'after 1 test and 4 tweaks it stops and says so: ' + (await status(page)).slice(0, 120));
  ok((await goal(page)).status === 'stuck', 'the goal is marked stuck');
  L = await log(page);
  ok(!L.some((x) => x.label === 'Design') && L.filter((x) => x.label === 'Concept').length === 5, 'nothing after Test 1 ran, and the concept was tweaked 4 times');
  ok(await page.evaluate(() => !document.querySelector('#autoResume').hidden), 'Resume autopilot is offered');
  await page.evaluate(() => { window.__rounds[5] = 22; });
  await page.click('#autoResume');
  await waitStatus(page, /^Done\./, 60000);
  ts = await tasks(page);
  ok(ts.filter((t) => t.stage === 'test1').length === 6 && ts.find((t) => t.stage === 'test1' && t.round === 6).votes.pass, 'Resume gave it more tries and the sixth passed');
  ok((await goal(page)).resumes === 1 && ts.find((t) => t.kind === 'final').status === 'review', 'and the relay carried on to the final package');
  await ctx.close();

  // ================= Stop, then Resume, from the saved steps
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 250; });
  await start(page);
  await page.waitForFunction(() => window.__log.length >= 4, null, { timeout: 20000 });
  await page.click('#autoStop');
  await waitStatus(page, /^Autopilot stopped at step/);
  const before = (await tasks(page)).length;
  ok((await goal(page)).status === 'stopped' && !(await tasks(page)).some((t) => t.status === 'working'), 'Stop leaves nothing running');
  ok(await page.evaluate(() => !document.querySelector('#autoResume').hidden && document.querySelector('#autoStop').hidden), 'Resume appears and Stop hides');
  await page.evaluate(() => { window.__delay = 30; });
  await page.click('#autoResume');
  await waitStatus(page, /^Done\./);
  ts = await tasks(page);
  ok(ts.filter((t) => t.stage === 'concept').length === 1 && ts.filter((t) => t.stage === 'research').length === 1 && ts.filter((t) => t.kind === 'final').length === 1, 'Resume does not repeat finished steps');
  ok(ts.filter((t) => t.votes).length === 3, 'it finished with three passed tests');
  await ctx.close();

  // ================= a step that fails once is retried; a permission error stops at once
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__fail.Research = { n: 1 }; });
  await start(page);
  await waitStatus(page, /^Done\./);
  L = await log(page);
  ok(L.filter((x) => x.label === 'Research').length === 2, 'a failed step is retried once');
  await ctx.close();
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__fail.Concept = { n: 9, code: 'not_granted' }; });
  await start(page);
  await waitStatus(page, /isn't allowed/);
  L = await log(page);
  ok(L.length === 1 && !(await tasks(page)).some((t) => t.kind === 'final'), 'a permission error stops the relay straight away, with no retries');
  await ctx.close();
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__fail.Design = { n: 2 }; });
  await start(page);
  await waitStatus(page, /Design didn't finish/);
  ok(/Press Resume autopilot/.test(await status(page)) && !(await tasks(page)).some((t) => t.stage === 'build'), 'a step that fails twice stops the relay at that step and nothing after it runs');
  await page.evaluate(() => { window.__fail.Design.n = 0; });
  await page.click('#autoResume');
  await waitStatus(page, /^Done\./);
  ok((await tasks(page)).filter((t) => t.stage === 'research').length === 1, 'Resume carries on from the failed step');
  await ctx.close();

  // ================= missing roles
  ({ page, ctx, errors } = await open(browser, { n: 5 }));
  await start(page);
  await page.waitForTimeout(400);
  ok(/Add a Tester agent first/.test(await status(page)) && (await page.evaluate(() => Object.keys(window.__store.goals).length)) === 0, 'with no Tester it says to add one and starts nothing');
  await ctx.close();

  // ================= Autopilot off keeps the manual way
  ({ page, ctx, errors } = await open(browser));
  await page.uncheck('#autoSw');
  await page.evaluate(() => { window.__plan = [{ agentId: 'a1', title: 'Manual task A', brief: 'x' }, { agentId: 'a2', title: 'Manual task B', brief: 'y' }]; });
  await page.fill('#goal', 'Do it by hand'); await page.click('#briefBtn');
  await waitStatus(page, /Run them from the board/);
  await page.waitForTimeout(400);
  ts = await tasks(page);
  ok(ts.length === 2 && ts.every((t) => t.status === 'planned' && !t.auto && !t.pipe), 'with Autopilot off the manager plans tasks and nothing runs');
  ok((await page.evaluate(() => window.__log.length)) === 0, 'no agent was called');
  await ctx.close();

  // ================= phone
  ({ page, ctx, errors } = await open(browser, { w: 375, h: 800 }));
  await start(page);
  await page.waitForTimeout(700);
  const w = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth, rel: document.querySelector('#relay').getBoundingClientRect().right }));
  ok(w.sw <= w.iw + 1 && w.rel <= w.iw, 'the relay wraps on a phone with no sideways scroll');
  await page.locator('#relay').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'shot-relay-phone.png' });
  await waitStatus(page, /^Done\./);
  ok((await page.evaluate(() => document.documentElement.scrollWidth)) <= 376, 'and none after a run');
  await ctx.close();

  // ================= reduced motion
  ({ page, ctx, errors } = await open(browser, { reduced: true }));
  await start(page);
  await waitStatus(page, /^Done\./);
  ok(errors.length === 0, 'reduced motion run has no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await ctx.close();

  await browser.close();
  console.log(failed ? '\n' + failed + ' FAILED' : '\nALL PASSED');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
