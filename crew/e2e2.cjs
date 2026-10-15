const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path');
const stubInit = require('./stub2.js');
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
  const ok = (c, m) => console.log((c ? 'ok   ' : 'FAIL ') + m);
  const run = async (id) => { await page.click(`[data-act="run"][data-id="${id}"]`); await page.waitForFunction((i) => ['review'].includes(window.__store.tasks[i].status) || window.__store.tasks[i].error, id, { timeout: 5000 }); return page.evaluate((i) => window.__store.tasks[i], id); };

  const badges = await page.$$eval('.chip .engine-badge', (els) => els.map((e) => e.textContent));
  ok(badges.join(',') === 'Mistral,Gemini,Groq,grok', 'badges: ' + badges.join(','));

  let t = await run('t1'); let c = await page.evaluate(() => window.__calls[0]);
  ok(c.server === 'host:mistral-mcp' && c.tool === 'create_chat_completion' && c.input.prefer === 'code' && c.input.temperature === 0.2 && c.input.max_tokens === 6000 && 'message' in c.input, 'dev -> mistral-mcp with code models, temp 0.2, 6000 tokens');
  ok(t.status === 'review' && t.modelUsed === 'model-of-host:mistral-mcp', 'draft saved with model');
  t = await run('t2'); c = await page.evaluate(() => window.__calls[1]);
  ok(c.server === 'host:gemini-mcp' && c.tool === 'generate_content' && 'prompt' in c.input && !('prefer' in c.input) && c.input.temperature === 0.85, 'marketer -> gemini-mcp (prompt arg, temp 0.85)');
  ok(/Marketer|market/i.test(c.input.prompt) && /marketing: positioning/.test(c.input.prompt), 'empty instructions fall back to the Marketer job text');
  t = await run('t3'); c = await page.evaluate(() => window.__calls[2]);
  ok(c.server === 'host:groq-mcp' && c.input.temperature === 0.7 && c.input.max_tokens === 3500, 'sales -> groq-mcp, temp 0.7, 3500 tokens');

  // removed engine
  await page.click('[data-act="run"][data-id="t4"]');
  await page.waitForFunction(() => window.__store.tasks.t4.error, null, { timeout: 5000 });
  const t4 = await page.evaluate(() => window.__store.tasks.t4);
  ok(t4.status === 'planned' && /isn.t available in Crew Control any more.*Mistral or Groq/.test(t4.error), 'removed engine explains itself: ' + t4.error);

  // groq daily limit
  await page.evaluate(() => { window.__mode = 'day'; return window.__db.doc('tasks/t3').update({ status: 'planned', error: null }); });
  await page.waitForSelector('[data-act="run"][data-id="t3"]');
  await page.click('[data-act="run"][data-id="t3"]');
  await page.waitForFunction(() => window.__store.tasks.t3.error, null, { timeout: 5000 });
  ok(/Groq has hit today.s free limit/.test(await page.evaluate(() => window.__store.tasks.t3.error)), 'groq daily limit message');
  await page.evaluate(() => { window.__mode = 'ok'; });

  // agent form
  await page.click('[data-act="add-agent"]');
  const opts = await page.$$eval('#aEngine option', (o) => o.map((x) => x.value));
  ok(opts.join(',') === 'claude,gemini,mistral,groq', 'engine choices: ' + opts.join(','));
  ok(await page.inputValue('#aEngine') === 'gemini', 'new agent starts on the best engine for its default role (Marketer -> gemini)');
  await page.selectOption('#aRole', 'Developer');
  ok(await page.inputValue('#aEngine') === 'mistral', 'changing role to Developer switches engine to mistral');
  let hint = await page.textContent('#aEngineHint');
  ok(/Good match for the Developer job: Mistral, because Mistral.s Codestral/.test(hint) && !(await page.isVisible('#aBestBtn')), 'hint says good match, no switch button');
  await page.selectOption('#aEngine', 'groq');
  hint = await page.textContent('#aEngineHint');
  ok(/Suggested for the Developer job: Mistral/.test(hint) && await page.isVisible('#aBestBtn'), 'hand-picked Groq -> suggestion + "Use Mistral" button');
  await page.selectOption('#aRole', 'Sales');
  ok(await page.inputValue('#aEngine') === 'groq', 'hand-picked engine is not overridden by a role change');
  await page.selectOption('#aEngine', 'claude');
  await page.click('#aBestBtn');
  ok(await page.inputValue('#aEngine') === 'groq', '"Use Groq" applies the suggestion for Sales');
  await page.click('#aTestBtn');
  await page.waitForFunction(() => /ready to work/.test(document.querySelector('#aTestOut').textContent), null, { timeout: 5000 });
  ok(true, 'test connection: ' + await page.textContent('#aTestOut'));
  await page.fill('#aName', 'closer');
  await page.click('#agentSave');
  await page.waitForFunction(() => Object.values(window.__store.agents).some((a) => a.name === 'closer' && a.engine === 'groq' && a.role === 'Sales'), null, { timeout: 5000 });
  ok(true, 'new Sales agent saved on groq');
  await page.screenshot({ path: 'form2.png' });

  // manager on groq plans with fenced JSON
  await page.evaluate(() => window.__db.doc('agents/mgr').update({ engine: 'groq' }));
  await page.evaluate(() => { window.__mode = 'plan'; });
  await page.fill('#goal', 'Launch the bakery');
  await page.click('#briefBtn');
  await page.waitForFunction(() => Object.values(window.__store.tasks).some((t) => t.title === 'Plan task'), null, { timeout: 5000 });
  const pc = await page.evaluate(() => window.__calls.filter((c) => c.server === 'host:groq-mcp').pop());
  ok(pc.input.temperature === 0.3, 'manager on groq plans through groq (fenced JSON parsed)');
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors');
  await browser.close();
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
