const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path');
const stubInit = require('./stub3.js');
let failed = 0;
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && !/fonts|ERR_|Failed to load resource/.test(m.text())) errors.push('console: ' + m.text()); });
  await page.addInitScript(stubInit);
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());
  await page.goto('file://' + path.resolve('crew-control.html'));
  await page.waitForSelector('.chip');
  const ok = (c, m) => { if (!c) failed++; console.log((c ? 'ok   ' : 'FAIL ') + m); };
  // A direct task now goes to the manager when it is finished, so the task's own call is the first one after Run.
  let mark = 0;
  const run = async (id) => { mark = await page.evaluate(() => window.__samples.length); await page.click(`[data-act="run"][data-id="${id}"]`); await page.waitForFunction((i) => window.__store.tasks[i].status === 'approved' || window.__store.tasks[i].error, id, { timeout: 5000 }); return page.evaluate((i) => window.__store.tasks[i], id); };
  const lastSample = () => page.evaluate((m) => window.__samples[m], mark);

  const badges = await page.$$eval('.chip .engine-badge', (els) => els.map((e) => e.textContent));
  ok(badges.join(',') === 'Gemini', 'only the Gemini agent shows an engine badge (removed engines count as Claude): ' + badges.join(','));

  // Claude runs, tier per job
  let t = await run('t1'); let s = await lastSample();
  ok(t.status === 'approved' && s.modelTier === 'complex' && s.cache === false, 'Developer runs on Claude with the complex tier');
  ok(/careful senior engineer/.test(s.prompt) && /do not use markdown/.test(s.prompt), 'blank instructions fall back to the Developer playbook, and the plain-text note is on');
  t = await run('t2'); s = await lastSample();
  ok(s.modelTier === 'default' && /Write punchy copy/.test(s.prompt), 'Marketer runs on the default tier with its own instructions');
  t = await run('t3'); s = await lastSample();
  ok(s.modelTier === 'quick', 'Sales runs on the quick tier');
  t = await run('t4'); s = await lastSample();
  ok(t.status === 'approved' && s.modelTier === 'complex' && !t.error && /Your role skills:/.test(s.prompt) && /senior brand and web designer/.test(s.prompt), 'agent saved with a removed engine (mistral) now runs on Claude, and a Designer gets the strongest setting and its role skills');
  t = await run('t6'); s = await lastSample();
  ok(t.status === 'approved' && s.modelTier === 'default', 'custom role runs on Claude with the default tier');
  t = await run('t7'); s = await lastSample();
  ok(t.status === 'approved' && s.modelTier === 'complex' && /self-contained HTML file/.test(s.prompt) && /PROFESSIONAL WEB DESIGNER/.test(s.prompt) && /art director/.test(s.prompt), 'Website Builder runs on the complex tier with its playbook and the pro web-design skills');
  t = await run('t8'); s = await lastSample();
  ok(t.status === 'approved' && s.modelTier === 'default' && /buyer panel/.test(s.prompt), 'Tester runs on the default tier with its playbook');
  ok((await page.evaluate(() => window.__calls.length)) === 0, 'no connector calls were made for Claude agents');

  // Gemini still works as an optional extra
  t = await run('t5'); let c = await page.evaluate(() => window.__calls[0]);
  ok(c.server === 'host:gemini-mcp' && c.tool === 'generate_content' && !('prefer' in c.input) && c.input.temperature === 0.3 && c.input.max_tokens === 5000, 'Gemini Researcher call: temp 0.3, 5000 tokens, no prefer');
  ok(t.status === 'approved' && t.modelUsed === 'model-of-host:gemini-mcp', 'Gemini draft saved with its model');
  await page.evaluate(() => { window.__mode = 'day'; return window.__db.doc('tasks/t5').update({ status: 'planned', error: null }); });
  await page.waitForSelector('[data-act="run"][data-id="t5"]');
  await page.click('[data-act="run"][data-id="t5"]');
  await page.waitForFunction(() => window.__store.tasks.t5.error, null, { timeout: 5000 });
  ok(/Gemini has hit today.s free limit/.test(await page.evaluate(() => window.__store.tasks.t5.error)), 'Gemini daily limit message');
  await page.evaluate(() => { window.__mode = 'ok'; });

  // agent form
  await page.click('[data-act="add-agent"]');
  const opts = await page.$$eval('#aEngine option', (o) => o.map((x) => x.value));
  ok(opts.join(',') === 'claude,gemini', 'engine choices: ' + opts.join(','));
  ok(await page.inputValue('#aEngine') === 'claude', 'new agent starts on Claude');
  const roles = await page.$$eval('#aRole option', (o) => o.map((x) => x.value).filter((v) => v !== '__custom'));
  let lens = [];
  for (const r of roles) { await page.selectOption('#aRole', r); lens.push(r + ':' + (await page.inputValue('#aInstr')).length); }
  ok(lens.every((x) => { const n = +x.split(':')[1]; return n > 400 && n <= 1500; }), 'every playbook fits the 1500-character limit: ' + lens.join(' '));
  ok(roles.join(',') === 'Manager,Strategist,Researcher,Marketer,Designer,Sales,Finance,Operations,Developer,Website Builder,App Builder,Tester', 'twelve roles in order: ' + roles.join(','));
  await page.selectOption('#aRole', '__custom');
  const guess = async (name, re, label) => { await page.fill('#aCustom', name); const hh = await page.textContent('#aEngineHint'); ok(re.test(hh), label + ': ' + hh.slice(0, 90)); };
  await guess('Web designer', /Treated as the Website Builder job and runs on its strongest/, 'custom "Web designer"');
  await guess('Mobile developer', /Treated as the App Builder job and runs on its strongest/, 'custom "Mobile developer"');
  await guess('QA lead', /Treated as the Tester job and runs on its standard/, 'custom "QA lead"');
  await guess('Finance manager', /Treated as the Finance job/, 'custom "Finance manager"');
  await guess('Account executive', /Treated as the Sales job and runs on its fastest/, 'custom "Account executive"');
  await guess('Chef', /^Suggested for this job|^Good match for this job/, 'custom "Chef" has no job guess');
  await page.selectOption('#aRole', 'Developer');
  ok(/careful senior engineer/.test(await page.inputValue('#aInstr')), 'picking a role loads its playbook');
  let hint = await page.textContent('#aEngineHint');
  ok(/Follows the Developer playbook/.test(hint) && /strongest setting/.test(hint) && /Good match for the Developer job: Claude/.test(hint) && !(await page.isVisible('#aBestBtn')), 'Developer hint: playbook + strongest setting, good match');
  await page.selectOption('#aRole', 'Sales');
  hint = await page.textContent('#aEngineHint');
  ok(/fastest setting/.test(hint) && /^.*Sales playbook/.test(hint), 'Sales hint: fastest setting');
  ok(/earns replies/.test(await page.inputValue('#aInstr')), 'changing role swaps the untouched playbook');
  await page.selectOption('#aEngine', 'gemini');
  hint = await page.textContent('#aEngineHint');
  ok(/Suggested for the Sales job: Claude/.test(hint) && await page.isVisible('#aBestBtn') && await page.isVisible('#aTestBtn'), 'Gemini picked by hand -> suggestion, Use Claude button, test button');
  await page.selectOption('#aRole', 'Marketer');
  ok(await page.inputValue('#aEngine') === 'gemini', 'hand-picked engine survives a role change');
  await page.click('#aTestBtn');
  await page.waitForFunction(() => /ready to work/.test(document.querySelector('#aTestOut').textContent), null, { timeout: 5000 });
  ok(true, 'Gemini test connection: ' + await page.textContent('#aTestOut'));
  await page.click('#aBestBtn');
  ok(await page.inputValue('#aEngine') === 'claude' && !(await page.isVisible('#aTestBtn')), '"Use Claude" switches back and hides the test button');
  await page.selectOption('#aRole', 'Sales');
  await page.fill('#aName', 'closer');
  await page.screenshot({ path: 'form3.png' });
  await page.click('#agentSave');
  await page.waitForFunction(() => Object.values(window.__store.agents).some((a) => a.name === 'closer' && a.engine === 'claude' && a.role === 'Sales' && /earns replies/.test(a.instructions)), null, { timeout: 5000 });
  ok(true, 'new Sales agent saved on Claude with the playbook');

  // the manager checks every direct task and passes it on, on the manager's own engine
  t = await page.evaluate(() => window.__store.tasks.t1);
  let rv = await page.evaluate(() => window.__samples.filter((x) => /THE WORK TO CHECK/.test(x.prompt)));
  ok(t.passedBy === 'mgr' && t.decision === 'manager' && rv.length >= 6 && /You stand in for the owner/.test(rv[0].prompt), 'a Claude manager checks each finished task on Claude and passes it on (' + rv.length + ' checks)');
  await page.evaluate(() => window.__db.doc('agents/mgr').update({ engine: 'gemini' }));
  await page.evaluate(() => { window.__mode = 'ok'; window.__calls.length = 0; return window.__db.doc('tasks/t3').update({ status: 'planned', passedBy: null, decision: null }); });
  await page.waitForSelector('[data-act="run"][data-id="t3"]');
  t = await run('t3');
  c = await page.evaluate(() => window.__calls.find((x) => /THE WORK TO CHECK/.test(x.input.prompt || '')));
  ok(t.passedBy === 'mgr' && c && c.server === 'host:gemini-mcp', 'a Gemini manager checks through Gemini');

  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors');
  if (errors.length) failed++;
  await browser.close();
  console.log(failed ? failed + ' check(s) FAILED' : 'ALL CHECKS PASSED');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
