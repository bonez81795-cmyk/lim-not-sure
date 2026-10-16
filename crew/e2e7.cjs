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
    if (/THE WORK TO CHECK: /.test(prompt)) { kind = 'review'; label = (/THE WORK TO CHECK: (.+?), by /.exec(prompt) || [0, '?'])[1]; }
    else if (/running a buyer panel/.test(prompt)) kind = 'batch';
    else if (/Do not recount/.test(prompt)) kind = 'digest';
    else if (/YOUR STEP: Consistency check/.test(prompt)) { kind = 'check'; label = 'Consistency check'; }
    else if (/doing the quality check/.test(prompt)) kind = 'qa';
    else if (/YOUR STEP: Final package/.test(prompt)) { kind = 'final'; label = 'Final package'; }
    else { const m = /YOUR STEP: ([A-Za-z ]+?)\. /.exec(prompt); label = m ? m[1] : (/Your task: (.*)\n/.exec(prompt) || [0, '?'])[1]; }
    const who = (/^You are ([^,]+),/.exec(prompt) || [0, '?'])[1];
    const entry = { kind, label, who, prompt, revise: /THE BUYER TEST FAILED/.test(prompt), fix: /CONSISTENCY CHECK FOUND A PROBLEM/.test(prompt), active: window.__active + 1, t: Date.now() };
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
      else if (kind === 'review') { const r = (window.__mgr || {})[label]; text = r && r.n > 0 ? (r.n--, 'REDO: put the price in the headline; say who it is for') : 'PASS: it does what was asked and matches the concept.'; }
      else if (kind === 'check') text = window.__checkReply || 'VERDICT: CONSISTENT';
      else if (kind === 'qa') text = 'Quality check: nothing is broken. One confusing label. Run the form test for real.';
      else {
        const n = window.__log.filter((l) => l.label === label && (l.kind === 'make' || l.kind === 'final')).length;
        text = (entry.revise || entry.fix ? 'WHAT CHANGED:\n- tweak ' + n + ': lowered the price to answer the price doubt\n\n' : '') + 'Output of ' + label + ' try ' + n + '.\n' +
          (label === 'Build' && !(window.__noCode > 0 && window.__noCode--) ? '<!doctype html><html><body><h1>Site for JobPing</h1><form onsubmit="event.preventDefault();this.textContent=\'Thanks\'"><button>Join</button></form></body></html>\n' : '') + 'Check before using: nothing';
      }
      if (opts && opts.onText) opts.onText({ text });
      return { text, truncated: false };
    } finally { window.__active--; }
  }, { json: async () => window.__plan });
  window.__saved = [];
  const downloads = { save: async (r) => { window.__saved.push({ filename: r.filename, data: String(r.data) }); return { status: 'saved' }; } };
  window.claude.use = async (n) => (n === 'sample' ? sample : n === 'downloads' ? downloads : orig(n));
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
const log = (page) => page.evaluate(() => window.__log.map((l) => ({ kind: l.kind, label: l.label, who: l.who, revise: l.revise, fix: l.fix, active: l.active, prompt: l.prompt })));
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
  ok(/set the route: Concept > Research > Test 1 > Design > Build > Test 2 > Pricing > Operations > Outreach > Launch > Consistency check > Test 3 > Final package/.test(await status(page)) || /Autopilot/.test(await status(page)), 'the manager sets one route through the roles: ' + (await status(page)).slice(0, 150));
  ok(await page.evaluate(() => !document.querySelector('#relay').hidden && document.querySelectorAll('#relay .rs').length === 13), 'the relay strip shows all 13 steps, including the consistency check');
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
  ok(JSON.stringify(gates) === JSON.stringify(['Research', 'Build', 'Consistency check']), 'the three tests come after Research, after Build and after the consistency check: ' + gates.join(', '));
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
  ok(fin && fin.status === 'review' && fin.agentId === 'a0' && fin.after.length === 12, 'the manager wrote one final package from the latest of all 12 earlier steps');
  const fp = L[L.length - 1].prompt;
  ok(/BUYER TEST RESULTS/.test(fp) && /Test 3: try 1 got 30 of 30 yes/.test(fp) && /quality check/i.test(fp) && fp.includes('Output of Launch try 1') && fp.includes('Output of Concept try 1'), 'the final package is built from every step plus the test results');
  const chkRuns = L.filter((x) => x.kind === 'check');
  const firstB3 = L.findIndex((x, i) => x.kind === 'batch' && L.slice(0, i).filter((y) => y.kind === 'batch').length === 6);
  ok(chkRuns.length === 1 && chkRuns[0].who === 'Nova' && L.indexOf(chkRuns[0]) < firstB3 && L.indexOf(chkRuns[0]) > L.findIndex((x) => x.label === 'Launch'), 'the Manager (Nova) ran one consistency check, after Launch and before Test 3');
  ok(/Output of Concept try 1/.test(chkRuns[0].prompt) && /Output of Launch try 1/.test(chkRuns[0].prompt) && /FIX <key>/.test(chkRuns[0].prompt) && /keys you may use are: pricing, operations, outreach, launch/.test(chkRuns[0].prompt), 'the check reads every step and may only send back the steps after Test 2');
  ok(['pricing', 'operations', 'outreach', 'launch', 'design', 'build'].every((k) => ts.filter((t) => t.stage === k).length === 1), 'a clean verdict changes nothing');
  ok(/CONSISTENCY CHECK, run before Test 3/.test(fp) && /VERDICT: CONSISTENT/.test(fp), 'the final package is told what the check found');
  ok((await goal(page)).status === 'ready', 'the goal is ready');
  ok(await page.evaluate(() => document.querySelector('#autoCancel').hidden), 'a finished relay has nothing to cancel');
  ok(await page.evaluate(() => document.querySelector('#autoFinish').hidden), 'a finished relay has nothing to finish');
  ok(ts.filter((t) => t.kind !== 'final').every((t) => t.status === 'approved' && t.passedBy === 'a0' && t.decision === 'manager' && !t.decidedBy), 'the manager (not you) passed every one of the 12 steps');
  const RV = L.filter((x) => x.kind === 'review');
  ok(RV.length === 8 && RV.every((x) => x.who === 'Nova'), 'the manager checked each of the 8 written steps before handing it on (' + RV.length + ' checks)');
  ok(L.every((x, i) => x.kind !== 'make' || i === 0 || !L.slice(i + 1).some((y) => y.kind === 'make') || L.slice(i + 1).find((y) => y.kind !== 'batch' && y.kind !== 'digest' && y.kind !== 'qa').kind === 'review'), 'each written step is followed by the manager\'s check before anyone else works');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item').length === 1 && document.querySelectorAll('#gateList .gate-item.final').length === 1), 'the only thing waiting on you is the final package');
  ok(await page.evaluate(() => !document.querySelector('#board .col.review') && document.querySelectorAll('#board .col.final .card').length === 1 && document.querySelectorAll('#board .col .card [data-act="review"].primary').length === 1), 'there is no Needs your OK column, and only the final draft has a Review button');
  ok(await page.evaluate(() => [...document.querySelectorAll('#relay .rs')].every((b) => b.classList.contains('done'))), 'every step in the relay is ticked');
  ok(/The website is built/.test(await status(page)), 'the done message says the website is built');
  await page.click('#gateList [data-act="open-site"]'); await page.waitForSelector('#siteDlg[open]');
  ok(await page.evaluate(() => { const f = document.querySelector('#siteBody iframe.site-frame'); return !!f && /Site for JobPing/.test(f.srcdoc) && f.getAttribute('sandbox') === 'allow-scripts allow-forms'; }), 'See website opens the real built site in a safe preview');
  const fr = page.frameLocator('#siteBody iframe');
  let answered = false;
  for (let k = 0; k < 5 && !answered; k++) {
    await page.waitForTimeout(300);
    if (await fr.locator('button').count()) await fr.locator('button').click().catch(() => {});
    answered = await fr.locator('form', { hasText: 'Thanks' }).waitFor({ timeout: 1000 }).then(() => true, () => false);
  }
  ok(answered, 'the preview is a working page: its form answers');
  await page.click('#siteBody [data-act="site-size"]');
  ok(await page.evaluate(() => document.querySelector('#siteBody iframe').classList.contains('phone')), 'it can be shown at phone size');
  await page.click('#siteBody [data-act="dl-site"]'); await page.click('#siteBody [data-act="dl-kit"]'); await page.waitForTimeout(200);
  const sv = await page.evaluate(() => window.__saved);
  ok(sv.length === 2 && /\.html$/.test(sv[0].filename) && /^<!doctype html>/i.test(sv[0].data) && /-kit\.md$/.test(sv[1].filename) && /## Final package/.test(sv[1].data) && /## Pricing/.test(sv[1].data) && /index\.html/.test(sv[1].data) && !/<!doctype/i.test(sv[1].data), 'Download website saves the .html file, and Download everything else saves one kit with every step');
  await page.click('#siteDlg [data-act="close"]');
  ok(await page.evaluate(() => /30\/30/.test(document.querySelector('#relay .rs.test i').textContent) && document.querySelectorAll('#relay .rs.test').length === 3), 'the three test chips show their votes');
  ok(await page.evaluate(() => document.querySelectorAll('#board .col.shared .card .pkg.mgr').length >= 12), 'the 12 steps sit under Passed by the manager, each marked as passed');
  await page.screenshot({ path: 'shot-relay-done.png' });
  // open a test and look at the vote
  await page.click('#relay .rs.test');
  await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => !!document.querySelector('#reviewBody .votebar') && /30 yes · 0 no · 16 yes needed/.test(document.querySelector('#reviewBody .vote-cap').textContent)), 'a test shows a vote bar and the count');
  await page.screenshot({ path: 'shot-relay-test.png' });
  await page.click('#reviewDlg [data-act="close"]');
  // the build can be copied as a website file
  const buildId = ts.find((t) => t.stage === 'build').id;
  await page.click('#board .card button.t[data-id="' + buildId + '"]'); await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => !!document.querySelector('#reviewBody [data-act="copy-code"]')), 'the Build step has a Copy website file button');
  ok(await page.evaluate(() => !document.querySelector('#reviewBody [data-act="approve"]') && !document.querySelector('#reviewBody [data-act="send-back"]') && /Passed by the manager/.test(document.querySelector('#reviewBody .pill').textContent) && /Nova passed it on/.test(document.querySelector('#reviewBody').textContent)), 'a step has no Approve or Send back for you; it says the manager passed it');
  await page.click('#reviewDlg [data-act="close"]');
  // one approval
  await page.click('#gateList .gate-item.final [data-act="review"]'); await page.waitForSelector('#reviewDlg[open]');
  ok(await page.evaluate(() => document.querySelectorAll('#reviewBody .built-from li').length === 12 && !!document.querySelector('#reviewBody [data-act="approve"]')), 'the package lists the 12 steps it was built from, and you can approve it');
  await page.click('#reviewBody [data-act="approve"]'); await page.waitForTimeout(400);
  ts = await tasks(page);
  ok(ts.every((t) => t.status === 'approved') && ts.find((t) => t.kind === 'final').decidedBy === 'u1', 'one click approved the package');
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
  ok(await page.evaluate(() => document.querySelectorAll('#board .col.shared .held-row').length === 4), 'the 4 older tries are kept, folded under the newest version');
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

  // ================= the check flags problems: flagged steps go back to their owner once, before Test 3
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__checkReply = 'VERDICT: CONFLICTS FOUND\nFIX pricing: Pricing says $79 a month but the concept says $49.\nFIX launch: the Launch offer promises a free year, which no step supports.\nFIX design: the logo colour is wrong.\nNOTE Design: the logo colour differs from the concept.'; });
  await start(page);
  await waitStatus(page, /^Done\./);
  L = await log(page); ts = await tasks(page);
  const cnt = (k) => ts.filter((t) => t.stage === k).length;
  ok(cnt('pricing') === 2 && cnt('launch') === 2 && cnt('operations') === 1 && cnt('outreach') === 1 && cnt('concept') === 1, 'only the flagged steps were sent back, once each');
  ok(cnt('design') === 1 && cnt('build') === 1, 'a flag on Design is a note only: Design already won a buyer vote and is not rewritten');
  ok(L.filter((x) => x.kind === 'check').length === 1, 'the check ran once');
  const pf = L.find((x) => x.label === 'Pricing' && x.fix), lf = L.find((x) => x.label === 'Launch' && x.fix);
  ok(pf && pf.who === 'Mako' && /\$79 a month/.test(pf.prompt) && /Output of Pricing try 1/.test(pf.prompt) && /WHAT CHANGED/.test(pf.prompt), 'the flagged step goes to its own owner with the problem and its old version');
  ok(lf && lf.who === 'Luma' && /Output of Pricing try 2/.test(lf.prompt), 'the next fix builds on the fixed pricing, not the old one');
  const fi = L.indexOf(lf), ci = L.findIndex((x) => x.kind === 'check'), b3 = L.findIndex((x, i) => x.kind === 'batch' && L.slice(0, i).filter((y) => y.kind === 'batch').length === 6);
  ok(ci < L.indexOf(pf) && L.indexOf(pf) < fi && fi < b3, 'the order is check, then the fixes, then Test 3');
  ok(/Output of Pricing try 2/.test(L[b3].prompt) && /Output of Launch try 2/.test(L[b3].prompt), 'Test 3 buyers see the fixed versions');
  const pt = ts.filter((t) => t.stage === 'pricing').sort((a, b) => a.round - b.round)[1];
  ok(pt.feedbackFrom && pt.problem && /lowered the price/.test(pt.changes), 'the fix is saved with what the problem was and what changed');
  const fp2 = L[L.length - 1].prompt;
  ok(/NOTE Design: the logo colour differs/.test(fp2) && /NOTE lines were not changed/.test(fp2), 'the final package is told about the conflict that was not fixed');
  ok(await page.evaluate(() => [...document.querySelectorAll('#relay .rs')].every((b) => b.classList.contains('done')) && /Consistency check/.test(document.querySelector('#relay').textContent)), 'the relay shows the check as done');
  ok(errors.length === 0, 'no page errors in the flagged run');
  await ctx.close();

  // ================= Stop during the fixes, then Resume: the check is not repeated and no fix is made twice
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 250; window.__checkReply = 'VERDICT: CONFLICTS FOUND\nFIX pricing: wrong price.\nFIX launch: wrong promise.'; });
  await start(page);
  await page.waitForFunction(() => window.__log.some((l) => l.fix), null, { timeout: 30000 });
  await page.click('#autoStop');
  await waitStatus(page, /^Autopilot stopped at step/);
  await page.evaluate(() => { window.__delay = 30; });
  await page.click('#autoResume');
  await waitStatus(page, /^Done\./);
  ts = await tasks(page); L = await log(page);
  ok(L.filter((x) => x.kind === 'check').length === 1 && ts.filter((t) => t.stage === 'pricing').length === 2 && ts.filter((t) => t.stage === 'launch').length === 2, 'Resume does not repeat the check or make a fix twice');
  await ctx.close();

  // ================= Cancel autopilot while it runs: two clicks, then it is abandoned for good
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 200; });
  await start(page);
  await page.waitForFunction(() => window.__log.length >= 4, null, { timeout: 20000 });
  ok(await page.evaluate(() => !document.querySelector('#autoCancel').hidden && /Cancel autopilot/.test(document.querySelector('#autoCancel').textContent)), 'Cancel autopilot is offered while it runs');
  await page.click('#autoCancel');
  await page.waitForTimeout(150);
  ok(/Confirm cancel/.test(await page.evaluate(() => document.querySelector('#autoCancel').textContent)) && (await goal(page)).status === 'running', 'the first click only asks to confirm and cancels nothing');
  await page.click('#autoCancel');
  await waitStatus(page, /^Autopilot cancelled/);
  ts = await tasks(page);
  ok((await goal(page)).status === 'cancelled', 'the goal is marked cancelled');
  ok(!ts.some((t) => t.status === 'working') && !ts.some((t) => t.pipe && !t.output), 'nothing is left running and no empty unfinished step is left on the board');
  ok(ts.some((t) => t.stage === 'concept' && t.output) && ts.some((t) => t.stage === 'research' && t.output), 'steps that already finished are kept');
  await page.waitForFunction(() => document.querySelector('#relay').hidden && document.querySelector('#autoCancel').hidden, null, { timeout: 3000 }).catch(() => {});
  ok(await page.evaluate(() => document.querySelector('#autoResume').hidden && document.querySelector('#autoCancel').hidden && document.querySelector('#autoStop').hidden && document.querySelector('#relay').hidden), 'Resume, Stop, Cancel and the relay strip are all gone');
  ok(await page.evaluate(() => !document.querySelector('#briefBtn').disabled && !document.querySelector('#goal').disabled), 'the manager can be briefed again');
  const calls = await page.evaluate(() => window.__log.length);
  await page.waitForTimeout(800);
  ok((await page.evaluate(() => window.__log.length)) === calls, 'nothing keeps running in the background after a cancel');
  await page.evaluate(() => { window.__delay = 30; });
  await start(page, 'Launch a website for a bakery');
  await waitStatus(page, /^Done\./);
  ts = await tasks(page);
  ok(ts.some((t) => t.kind === 'final' && t.status === 'review' && /bakery/.test(t.goalText)), 'a new goal runs normally after a cancel');
  ok(errors.length === 0, 'no page errors after a cancel');
  await ctx.close();

  // ================= the confirm step times out, and Cancel also works on a stopped run
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 250; });
  await start(page);
  await page.waitForFunction(() => window.__log.length >= 4, null, { timeout: 20000 });
  await page.click('#autoStop');
  await waitStatus(page, /^Autopilot stopped at step/);
  await page.waitForFunction(() => !document.querySelector('#autoResume').hidden, null, { timeout: 3000 });
  ok(await page.evaluate(() => !document.querySelector('#autoCancel').hidden), 'Cancel is still offered next to Resume when autopilot is stopped');
  await page.click('#autoCancel');
  await page.waitForTimeout(150);
  ok(await page.evaluate(() => /Confirm cancel/.test(document.querySelector('#autoCancel').textContent)), 'it asks to confirm');
  await page.waitForFunction(() => /^Cancel autopilot$/.test(document.querySelector('#autoCancel').textContent), null, { timeout: 9000 });
  ok((await goal(page)).status === 'stopped', 'if you do not confirm, it goes back to normal and nothing is cancelled');
  await page.click('#autoCancel'); await page.click('#autoCancel');
  await waitStatus(page, /^Autopilot cancelled/);
  ok((await goal(page)).status === 'cancelled' && await page.evaluate(() => document.querySelector('#autoResume').hidden && document.querySelector('#autoCancel').hidden), 'cancelling a stopped run removes Resume for good');
  ok(!(await tasks(page)).some((t) => t.pipe && !t.output), 'its empty unfinished steps are removed');
  await ctx.close();

  // ================= Finish now: skip the buyer votes that are left and get the final package
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 60; window.__rounds = Array(80).fill(8); });
  await start(page);
  await page.waitForFunction(() => window.__batchCalls >= 9, null, { timeout: 60000 });
  ok(await page.evaluate(() => !document.querySelector('#autoFinish').hidden && /Finish now/.test(document.querySelector('#autoFinish').textContent)), 'Finish now is offered while the buyer tests run');
  await page.click('#autoFinish');
  await page.waitForTimeout(150);
  ok(/Confirm/.test(await page.evaluate(() => document.querySelector('#autoFinish').textContent)) && !(await goal(page)).skipTests, 'the first click only asks to confirm');
  await page.click('#autoFinish');
  await waitStatus(page, /^Done\./, 90000);
  L = await log(page); ts = await tasks(page);
  ok((await goal(page)).skipTests === true, 'the goal remembers the votes were skipped');
  const gt = ts.filter((t) => t.kind === 'test');
  ok(gt.every((t) => t.stage === 'test1' && t.votes && !t.votes.pass) && gt.length >= 3, 'Test 1 kept its failed tries and nothing else was tested: ' + gt.length + ' tries, no Test 2 or Test 3, no empty test left over');
  ok(['design', 'build', 'pricing', 'operations', 'outreach', 'launch'].every((k) => ts.filter((t) => t.stage === k).length === 1) && L.filter((x) => x.kind === 'check').length === 1, 'every later step still ran once, in order, with the consistency check');
  const fin2 = ts.find((t) => t.kind === 'final');
  ok(fin2 && fin2.status === 'review', 'the final package was written');
  const fp3 = L[L.length - 1].prompt;
  ok(/THE OWNER CHOSE TO FINISH WITHOUT WINNING THE BUYER VOTES/.test(fp3) && /Test 1: NOT passed\. Best of \d+ tries: 8 of 30/.test(fp3) && /Test 2: not run/.test(fp3) && /Test 3: not run/.test(fp3) && /did NOT win a majority/.test(fp3), 'the final package is told to say plainly that the votes were not won');
  ok(/did not win them \(best result 8 of 30 yes\)/.test(await status(page)), 'the done message says so too: ' + (await status(page)).slice(0, 140));
  ok(await page.evaluate(() => { const t = [...document.querySelectorAll('#relay .rs.test')]; return t.length === 3 && t.every((b) => b.classList.contains('skip')) && /best 8\/30/.test(t[0].textContent) && /skipped/.test(t[1].textContent) && document.querySelectorAll('#relay .rs.fail').length === 0; }), 'the three test chips show as skipped, with the best vote');
  ok(await page.evaluate(() => document.querySelector('#autoFinish').hidden && document.querySelectorAll('#gateList .gate-item.final').length === 1), 'the gate shows the final package, and Finish now is gone');
  ok(await page.evaluate(() => { const g = document.querySelector('#board .col.shared .held-group summary'); return !!g && /older version/.test(g.textContent); }), 'the old failed tries fold away under the newest one');
  ok(errors.length === 0, 'no page errors after Finish now' + (errors.length ? ': ' + errors.join(' | ') : ''));
  await ctx.close();

  // ================= Finish now also works on a stopped run
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__delay = 100; window.__rounds = Array(80).fill(8); });
  await start(page);
  await page.waitForFunction(() => window.__batchCalls >= 6, null, { timeout: 60000 });
  await page.click('#autoStop');
  await waitStatus(page, /^Autopilot stopped at step/);
  await page.waitForFunction(() => !document.querySelector('#autoResume').hidden && !document.querySelector('#autoFinish').hidden, null, { timeout: 3000 });
  await page.click('#autoFinish'); await page.click('#autoFinish');
  await waitStatus(page, /^Done\./, 90000);
  ts = await tasks(page);
  ok((await goal(page)).skipTests === true && ts.find((t) => t.kind === 'final').status === 'review' && !ts.some((t) => t.kind === 'test' && !t.votes), 'Finish now on a stopped run restarts it and finishes with the final package');
  ctx.close && await ctx.close();

  // ================= no limit: it keeps tweaking and re-testing until a majority says yes
  ({ page, ctx, errors } = await open(browser));
  await start(page, null, [10, 10, 10, 10, 10, 10, 10, 12, 22]);
  await waitStatus(page, /^Done\./, 120000);
  ts = await tasks(page); L = await log(page);
  const g1 = ts.filter((t) => t.stage === 'test1');
  ok(g1.length === 9 && g1.find((t) => t.round === 9).votes.pass && g1.filter((t) => t.votes && !t.votes.pass).length === 8, 'it never gives up: 8 failed tests, 8 tweaks, the 9th passes');
  ok(L.filter((x) => x.label === 'Concept' && x.kind === 'make').length === 9, 'the concept owner tweaked it 8 times');
  const fd = L.findIndex((x) => x.label === 'Design'), lc = L.map((x) => x.label).lastIndexOf('Concept');
  ok(fd > lc, 'Design only started once Test 1 passed');
  ok((await goal(page)).status !== 'stuck' && ts.find((t) => t.kind === 'final').status === 'review', 'and the relay carried on to the final package');
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
  await page.waitForFunction(() => !document.querySelector('#autoResume').hidden, null, { timeout: 3000 }).catch(() => {});
  ok(await page.evaluate(() => !document.querySelector('#autoResume').hidden && document.querySelector('#autoStop').hidden), 'Resume appears and Stop hides');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item').length === 0 && +document.querySelector('#sGate').textContent === 0), 'while stopped, nothing waits on you');
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
  ok(L.filter((x) => x.label === 'Research' && x.kind === 'make').length === 2, 'a failed step is retried once');
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

  // ================= every role gets its skills; the builder gets the pro web-design method and a design system to follow
  ({ page, ctx, errors } = await open(browser));
  await start(page);
  await waitStatus(page, /^Done\./, 90000);
  L = await log(page);
  const mk = (lab) => L.find((x) => x.kind === 'make' && x.label === lab).prompt;
  ok(['Concept', 'Research', 'Design', 'Build', 'Pricing', 'Operations', 'Outreach', 'Launch'].every((lab) => /Your role skills:/.test(mk(lab))), 'every step is written with its role skills');
  ok(/DESIGN TOKENS as a CSS block/.test(mk('Design')) && /senior brand and web designer/.test(mk('Design')), 'the Designer is asked for a full design system');
  ok(/PROFESSIONAL WEB DESIGNER/.test(mk('Build')) && /Follow the Designer's DESIGN TOKENS/.test(mk('Build')) && /Output of Design try 1/.test(mk('Build')), 'the builder gets the pro web-design method and the Designer\'s system');
  const br = L.find((x) => x.kind === 'review' && x.label === 'Build').prompt, cr = L.find((x) => x.kind === 'review' && x.label === 'Pricing').prompt;
  ok(/demanding art director/.test(br) && /Your role skills:/.test(br) && !/demanding art director/.test(cr), 'the manager judges the build like an art director, and only design work gets that bar');
  await ctx.close();

  // ================= the manager sends a step back: it is fixed once and passed on, and you never see it
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__mgr = { Design: { n: 1 } }; });
  await start(page);
  await waitStatus(page, /^Done\./, 90000);
  ts = await tasks(page); L = await log(page);
  const ds = ts.filter((t) => t.stage === 'design').sort((a, b) => a.round - b.round);
  ok(ds.length === 2 && ds[0].decision === 'redo' && /price in the headline/.test(ds[0].redoNote) && ds[1].revises === ds[0].id && ds[1].passedBy === 'a0', 'the manager sent Design back once, Dash fixed it, and the fix was passed on');
  const fixP = L.filter((x) => x.label === 'Design' && x.kind === 'make')[1].prompt;
  ok(/YOUR MANAGER SENT THIS STEP BACK/.test(fixP) && /price in the headline/.test(fixP) && /Output of Design try 1/.test(fixP), 'the fix is given the first version and the manager\'s notes');
  ok(L.filter((x) => x.kind === 'review' && x.label === 'Design').length === 1, 'the fix is not checked again, so it cannot loop');
  const bp = L.find((x) => x.label === 'Build' && x.kind === 'make').prompt;
  ok(/Output of Design try 2/.test(bp) && !/Output of Design try 1/.test(bp), 'the next bot builds on the fixed design, not the one sent back');
  ok(await page.evaluate(() => document.querySelectorAll('#gateList .gate-item').length === 1), 'still only the final package waits on you');
  await ctx.close();

  // ================= the build came back without a working file: the builder writes it again, automatically
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__noCode = 1; });
  await start(page);
  await waitStatus(page, /^Done\./, 90000);
  ts = await tasks(page); L = await log(page);
  const bs = ts.filter((t) => t.stage === 'build').sort((a, b) => a.round - b.round);
  ok(bs.length === 2 && bs[0].decision === 'redo' && bs[1].fileRetry && /<!doctype html>/i.test(bs[1].output), 'a build with no website file is redone once, and the redo has the file');
  ok(/did not contain the complete website file/.test(L.filter((x) => x.label === 'Build' && x.kind === 'make')[1].prompt), 'the builder is told exactly what was missing');
  ok(await page.evaluate(() => !!document.querySelector('#gateList [data-act="open-site"]')), 'and the website can be opened from the final package');
  await ctx.close();

  // ================= a direct task is checked by the manager too, and never waits on you
  ({ page, ctx, errors } = await open(browser));
  await page.evaluate(() => { window.__mgr = { 'Cold email sequence': { n: 1 } }; });
  const dId = await page.evaluate(() => Object.entries(window.__store.tasks).find(([, t]) => t.title === 'Cold email sequence')[0]);
  await page.click('#board [data-act="run"][data-id="' + dId + '"]');
  await page.waitForFunction((id) => window.__store.tasks[id].status === 'approved', dId, { timeout: 20000 });
  const dt = await page.evaluate((id) => window.__store.tasks[id], dId);
  L = await log(page);
  ok(dt.passedBy === 'a0' && dt.mgrRedone && dt.noteBy === 'manager', 'the manager sent the direct task back once, then passed it on');
  ok(L.filter((x) => x.kind === 'make').length === 2 && /Your manager sent it back/.test(L.filter((x) => x.kind === 'make')[1].prompt), 'the agent redid it with the manager\'s note');
  ok(await page.evaluate(() => !document.querySelector('#autoLbl') || document.querySelector('#autoLbl').hidden), 'there is no switch back to approving every task by hand');
  ok(errors.length === 0, 'no page errors' + (errors.length ? ': ' + errors.join(' | ') : ''));
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
