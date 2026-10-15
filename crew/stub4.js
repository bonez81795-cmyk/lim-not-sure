// Richer fake crew for screenshots and the new-look checks: argument = number of agents (5 or 12).
module.exports = (n) => {
  window.__TEST = true;
  const store = new Proxy({ agents: {}, tasks: {}, goals: {} }, { get: (t, k) => (k in t ? t[k] : (typeof k === 'string' ? (t[k] = {}) : undefined)) });
  const subs = [];
  let seq = 0;
  const newId = () => 'id' + (++seq);
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
    get: async () => snap(c),
    add: async (d) => { const id = newId(); store[c][id] = { ...d }; notify(c); return { id }; },
    doc: (id) => ref(c, id || newId()),
  });
  const db = { collection: col, doc: (p) => { const [c, id] = p.split('/'); return ref(c, id); } };
  const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
  const ROLES = ['Manager', 'Strategist', 'Researcher', 'Marketer', 'Designer', 'Sales', 'Finance', 'Operations', 'Developer', 'Website Builder', 'App Builder', 'Tester'];
  const NAMES = ['Nova', 'Kira', 'Vesk', 'Luma', 'Dash', 'Orin', 'Mako', 'Pike', 'Remy', 'Site runner', 'Appy', 'Tess'];
  const COL = ['#ff6b6b', '#ffd23f', '#5aa9ff', '#ff5fa2', '#a78bfa', '#ff8a3d', '#3ddc97', '#22d3ee'];
  const roles = n >= 12 ? ROLES : ['Manager', 'Designer', 'Developer', 'Marketer', 'Researcher'];
  const names = n >= 12 ? NAMES : ['Nova', 'Site runner', 'Dash', 'Luma', 'Kira'];
  const ids = [];
  roles.forEach((r, i) => { const id = 'a' + i; ids.push(id); store.agents[id] = { name: names[i], role: r, engine: 'claude', instructions: '', color: COL[i % COL.length], createdAt: ago(3000 - i) }; });
  const T = (i, agent, title, brief, status, extra) => { store.tasks['t' + i] = { agentId: ids[agent % ids.length], title, brief, status, createdAt: ago(900 - i * 40), updatedAt: ago(30), ...extra }; };
  const fin = (m, run) => ({ startedAt: ago(m + run / 60), finishedAt: ago(m), output: 'Draft text for this task. ' + 'Line of copy. '.repeat(8) });
  T(1, 1, 'Tagline options for the launch', 'Write ten taglines for the bakery.', 'approved', { ...fin(600, 42), decision: 'approved', decidedAt: ago(580), decidedBy: 'u1' });
  T(2, 2, 'Competitor price map', 'Map direct competitors and prices.', 'approved', { ...fin(500, 95), decision: 'approved', decidedAt: ago(470), decidedBy: 'u1' });
  T(3, 3, 'Landing page copy', 'Hero, three benefits and one button.', 'approved', { ...fin(420, 61), decision: 'approved', decidedAt: ago(400), decidedBy: 'u1' });
  T(4, 4, 'Cold email sequence', 'Three emails for plumbers.', 'returned', { ...fin(300, 50), decision: 'returned', decidedAt: ago(280), decidedBy: 'u1', note: 'Shorter, and drop the discount line.' });
  T(5, 0, 'Break the goal into tasks', 'Plan the first week.', 'approved', { ...fin(250, 12), decision: 'approved', decidedAt: ago(240), decidedBy: 'u1' });
  T(6, 2, 'Pricing ladder good better best', 'Three tiers with margins.', 'review', fin(55, 70));
  T(7, 1, 'Homepage hero and layout notes', 'Section by section layout.', 'review', fin(35, 80));
  T(8, 3, 'Welcome email for new signups', 'Under 100 words.', 'review', fin(12, 33));
  T(9, 4, 'Draft the onboarding checklist', 'Seven steps.', 'working', { startedAt: ago(1) });
  T(10, 0, 'Write the weekly update', 'Short summary for the owner.', 'planned', {});
  T(11, 2, 'List supplier questions', 'Ten questions to ask.', 'planned', {});
  window.__store = store; window.__db = db; window.__calls = []; window.__samples = [];
  const user = { id: async () => 'u1', can: async () => true, isOwner: () => true, canEdit: () => true, profiles: async () => ({}) };
  const sample = Object.assign(async () => ({ text: 'Claude draft', truncated: false }), { json: async () => [] });
  const room = { onPeers() {}, presence: async () => {} };
  const mcp = { callTool: async () => ({ payload: { payload: { response: 'x', model: 'm' } } }) };
  const caps = { db, user, sample, room, mcp };
  window.claude = { use: async (nm) => caps[nm] || null };
};
