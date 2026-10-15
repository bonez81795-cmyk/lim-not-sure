const { chromium } = require('/opt/npm-tools/node_modules/playwright');
const path = require('path');

const stubInit = () => {
  const store = { agents: {}, tasks: {}, goals: {} };
  const subs = [];
  let n = 0;
  const newId = () => 'id' + (++n);
  const snap = (col) => ({ docs: Object.entries(store[col]).map(([id, d]) => ({ id, data: () => JSON.parse(JSON.stringify(d)) })) });
  const notify = (col) => subs.filter((s) => s[0] === col).forEach((s) => s[1](snap(col)));
  const ref = (col, id) => ({
    id,
    set: async (d) => { store[col][id] = { ...d }; notify(col); },
    update: async (d) => { store[col][id] = { ...(store[col][id] || {}), ...d }; notify(col); },
    delete: async () => { delete store[col][id]; notify(col); },
    get: async () => ({ exists: !!store[col][id], data: () => store[col][id] }),
  });
  const col = (c) => ({
    orderBy() { return this; }, limit() { return this; }, where() { return this; },
    onSnapshot(cb) { subs.push([c, cb]); cb(snap(c)); return () => {}; },
    add: async (d) => { const id = newId(); store[c][id] = { ...d }; notify(c); return { id }; },
    doc: (id) => ref(c, id || newId()),
  });
  const db = { collection: col, doc: (p) => { const [c, id] = p.split('/'); return ref(c, id); } };
  const now = new Date().toISOString();
  store.agents.nova = { name: 'Nova', role: 'Manager', engine: 'chatgpt', instructions: 'Plan well.', color: '#ff8a3d', createdAt: now };
  store.agents.dev = { name: 'Dev', role: 'Developer', engine: 'gemini', instructions: '', color: '#3ddc97', createdAt: now };
  store.agents.mia = { name: 'Mia', role: 'Marketer', engine: 'grok', instructions: 'Write punchy copy.', color: '#ff5fa2', createdAt: now };
  store.agents.cla = { name: 'Cleo', role: 'Researcher', engine: 'claude', instructions: 'Check facts.', color: '#5aa9ff', createdAt: now };
  store.tasks.t1 = { agentId: 'dev', title: 'Write a debounce helper', brief: 'JS, 10 lines.', status: 'planned', createdAt: now, updatedAt: now };
  store.tasks.t2 = { agentId: 'mia', title: 'Three taglines', brief: 'For a bakery.', status: 'planned', createdAt: now, updatedAt: now };
  store.tasks.t3 = { agentId: 'nova', title: 'Outline a plan', brief: 'Short.', status: 'planned', createdAt: now, updatedAt: now };
  window.__store = store; window.__db = db;
  window.__calls = [];
  window.__mode = 'ok';
  const user = { id: async () => 'u1', can: async () => true, isOwner: () => true, canEdit: () => true, profiles: async () => ({}) };
  const sample = Object.assign(async () => ({ text: 'Claude draft', truncated: false }), { json: async () => [{ agentId: 'dev', title: 'T', brief: 'B' }] });
  const room = { onPeers() {}, presence: async () => {} };
  const mcp = {
    callTool: async (server, tool, input, opts) => {
      window.__calls.push({ server, tool, input, cache: opts && opts.cache });
      if (window.__mode === 'quota') throw { code: 'tool_error', message: '{"error":"429 You exceeded your current quota"}', result: { content: [{ type: 'text', text: '{"error":"429 You exceeded your current quota"}' }] } };
      if (window.__mode === 'down') throw { code: 'server_not_connected', message: 'nope' };
      if (window.__mode === 'plan' && input.message && /JSON array/.test(input.message)) return { payload: { payload: { response: 'Here you go:\n```json\n[{"agentId":"mia","title":"Plan task","brief":"Do it."}]\n```', model: 'gpt-test' } } };
      await new Promise((r) => setTimeout(r, 150));
      return { payload: { payload: { response: 'Reply from ' + server + '\nCheck before using: nothing', model: 'model-of-' + server } } };
    },
  };
  const caps = { db, user, sample, room, mcp };
  window.claude = { use: async (nm) => caps[nm] || null };
};

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

  const chips = await page.$$eval('.chip .engine-badge', (els) => els.map((e) => e.textContent));
  ok(chips.join(',') === 'ChatGPT,Gemini,Grok', 'engine badges shown: ' + chips.join(','));

  // run Gemini developer task (blank instructions -> falls back to the role text)
  await page.click('[data-act="run"][data-id="t1"]');
  await page.waitForFunction(() => window.__store.tasks.t1.status === 'review', null, { timeout: 5000 });
  let t1 = await page.evaluate(() => window.__store.tasks.t1);
  let call = await page.evaluate(() => window.__calls[0]);
  ok(call.server === 'host:gemini-mcp' && call.tool === 'generate_content', 'gemini dispatched to host:gemini-mcp/generate_content');
  ok(call.input.temperature === 0.2 && call.input.max_tokens === 6000, 'developer tuning applied (' + call.input.temperature + ', ' + call.input.max_tokens + ')');
  ok(/You write and review code/.test(call.input.prompt), 'blank instructions fall back to the Developer job text');
  ok(/plain text, so do not use markdown/.test(call.input.prompt), 'plain-text note added for non-Claude engines');
  ok(call.cache === false, 'cache disabled');
  ok(t1.output.startsWith('Reply from host:gemini-mcp') && t1.modelUsed === 'model-of-host:gemini-mcp', 'draft + model saved: ' + t1.modelUsed);

  // grok marketer and chatgpt manager
  await page.click('[data-act="run"][data-id="t2"]');
  await page.waitForFunction(() => window.__store.tasks.t2.status === 'review', null, { timeout: 5000 });
  call = await page.evaluate(() => window.__calls[1]);
  ok(call.server === 'host:grok-mcp' && call.input.temperature === 0.85 && 'message' in call.input, 'grok marketer -> grok-mcp, temp 0.85, message arg');
  await page.click('[data-act="run"][data-id="t3"]');
  await page.waitForFunction(() => window.__store.tasks.t3.status === 'review', null, { timeout: 5000 });
  call = await page.evaluate(() => window.__calls[2]);
  ok(call.server === 'host:openai-mcp' && call.input.temperature === 0.3 && call.input.max_tokens === 3500, 'chatgpt manager -> openai-mcp, temp 0.3, 3500 tokens');

  // review dialog shows who wrote it
  await page.click('[data-act="review"][data-id="t1"]');
  const dd = await page.$$eval('#reviewBody dd', (els) => els.map((e) => e.textContent));
  ok(dd.some((t) => /Gemini · model-of-host:gemini-mcp/.test(t)), 'review dialog shows "Gemini · model"');
  await page.keyboard.press('Escape');

  // error path: no credit
  await page.evaluate(() => { window.__mode = 'quota'; return window.__db.doc('tasks/t2').update({ status: 'planned', error: null }); });
  await page.waitForSelector('[data-act="run"][data-id="t2"]');
  await page.click('[data-act="run"][data-id="t2"]');
  await page.waitForFunction(() => window.__store.tasks.t2.error, null, { timeout: 5000 });
  const e2 = await page.evaluate(() => window.__store.tasks.t2);
  ok(e2.status === 'planned' && /no credit or quota/.test(e2.error), 'quota error is friendly: ' + e2.error);

  // error path: server down
  await page.evaluate(() => { window.__mode = 'down'; });
  await page.waitForSelector('[data-act="run"][data-id="t2"]');
  await page.click('[data-act="run"][data-id="t2"]');
  await page.waitForFunction(() => /isn.t reachable/.test(window.__store.tasks.t2.error || ''), null, { timeout: 5000 });
  ok(true, 'server-down error is friendly: ' + (await page.evaluate(() => window.__store.tasks.t2.error)).slice(0, 80) + '…');

  // Claude agent still uses sample
  await page.evaluate(() => { window.__mode = 'ok'; const at = new Date().toISOString(); return window.__db.doc('tasks/t4').set({ agentId: 'cla', title: 'Check a fact', brief: 'x', status: 'planned', createdAt: at, updatedAt: at }); });
  await page.waitForSelector('[data-act="run"][data-id="t4"]');
  await page.click('[data-act="run"][data-id="t4"]');
  await page.waitForFunction(() => window.__store.tasks.t4.status === 'review', null, { timeout: 5000 });
  const t4 = await page.evaluate(() => window.__store.tasks.t4);
  ok(t4.output === 'Claude draft' && t4.modelUsed === null, 'Claude agent still runs through Claude sampling');

  // agent form: hint + test button
  await page.click('[data-act="add-agent"]');
  await page.selectOption('#aEngine', 'chatgpt');
  const hint = await page.textContent('#aEngineHint');
  ok(/openai-mcp/.test(hint) && /Tuned for the Marketer job|Tuned for the Manager job/.test(hint), 'agent form hint: ' + hint);
  ok(await page.isVisible('#aTestBtn'), 'test button visible for ChatGPT');
  await page.selectOption('#aRole', 'Developer');
  ok(/Developer job: precise/.test(await page.textContent('#aEngineHint')), 'hint follows role change');
  await page.click('#aTestBtn');
  await page.waitForFunction(() => /It.s ready to work/.test(document.querySelector('#aTestOut').textContent), null, { timeout: 5000 });
  ok(true, 'test connection: ' + (await page.textContent('#aTestOut')));
  const testCall = await page.evaluate(() => window.__calls[window.__calls.length - 1]);
  ok(testCall.input.max_tokens === 64, 'test uses a tiny reply limit');
  await page.selectOption('#aEngine', 'claude');
  ok(!(await page.isVisible('#aTestBtn')), 'test button hidden for Claude');
  await page.keyboard.press('Escape');

  // manager planning through ChatGPT
  await page.evaluate(() => { window.__mode = 'plan'; });
  await page.fill('#goal', 'Launch the bakery');
  await page.click('#briefBtn');
  await page.waitForFunction(() => Object.values(window.__store.tasks).some((t) => t.title === 'Plan task'), null, { timeout: 5000 });
  const plan = await page.evaluate(() => window.__calls.filter((c) => c.server === 'host:openai-mcp').pop());
  ok(plan.input.temperature === 0.3, 'manager plans through ChatGPT and the fenced JSON was parsed');

  await page.screenshot({ path: 'shot.png' });
  console.log(errors.length ? 'PAGE ERRORS:\n' + errors.join('\n') : 'no page errors');
  await browser.close();
})().catch((e) => { console.error('HARNESS FAIL', e); process.exit(1); });
