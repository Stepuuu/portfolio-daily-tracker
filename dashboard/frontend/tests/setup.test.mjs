// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The setup wizard's decisions: the steps per mode, a step's state from the checklist, where a visit
// resumes, when Today sends a first visitor to /setup (once, never in demo or lab, never after a dismissal),
// the 累计净投入 suggestion, the legacy migration's answers and the model form. Payloads follow
// backend/api/setup.py; values are fictional.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';
import {
  REDIRECT_KEY, accountStepView, actionHref, alreadyRedirected, groupItems, neighbours, parseStart, parseStep, rememberRedirect,
  resetRedirectMemory, resumeStep, shouldRedirect, stepState, stepsFor,
} from '../src/features/setup/steps.ts';
import {
  ESTIMATE_LABEL, accountEstimate, answerErrors, costEstimate, draftSummary, draftValueText, emptyAnswers, legacyControls,
  legacySources, openingEstimate, openingHasForeign, proposalBody, skippableRows,
} from '../src/features/setup/opening.ts';
import {
  NEW_ADDRESS_KEY, baseUrlProblem, canTest, formErrors, initialForm, keyStatus, saveBody, switchPreset, testText, visionOf,
} from '../src/features/setup/llm.ts';
import { emptyValues } from '../src/features/ledger/entry.ts';

const IDS = ['opening', 'legacy', 'fees', 'quotes', 'alerts', 'routines', 'notifications', 'llm'];

function items(states = {}) {
  const defaults = { opening: 'todo', legacy: 'done', fees: 'todo', quotes: 'done', alerts: 'todo', routines: 'todo',
    notifications: 'optional', llm: 'optional' };
  return IDS.map((id) => ({ id, label: id, state: states[id] ?? defaults[id], detail: '示例', action: null }));
}

function status(overrides = {}) {
  return { mode: 'full', items: items(), setup_complete: false, completed_at: null, dismissed: false, dismissed_at: null,
    ledger_ready: false, ...overrides };
}

class Memory {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
}

test('steps: six in full and demo mode, three in research-only mode; unknown names are refused', () => {
  assert.deepEqual(stepsFor('full'), ['welcome', 'account', 'fees', 'market', 'ai', 'done']);
  assert.deepEqual(stepsFor('demo'), stepsFor('full'));
  assert.deepEqual(stepsFor(undefined), stepsFor('full'));
  assert.deepEqual(stepsFor('lab'), ['welcome', 'ai', 'done']);
  assert.equal(parseStep('fees', stepsFor('full')), 'fees');
  assert.equal(parseStep('fees', stepsFor('lab')), null);
  assert.equal(parseStep('nowhere', stepsFor('full')), null);
  assert.equal(parseStart('legacy'), 'legacy');
  assert.equal(parseStart('<script>'), null);
  assert.deepEqual(neighbours(stepsFor('full'), 'welcome'), { previous: null, next: 'account' });
  assert.deepEqual(neighbours(stepsFor('full'), 'done'), { previous: 'ai', next: null });
});

test('a step is todo while an item is, done when what applies is done, optional when all it has is optional', () => {
  assert.equal(stepState('account', items()), 'todo');
  // The ledger has an opening; an old file that was not migrated is only optional now.
  assert.equal(stepState('account', items({ opening: 'done', legacy: 'optional' })), 'done');
  assert.equal(stepState('account', items({ opening: 'done', legacy: 'todo' })), 'todo');
  assert.equal(stepState('market', items({ alerts: 'done', routines: 'done' })), 'done');
  // The demo: no monitor or scheduler there (unavailable); the scripted quotes are fine.
  assert.equal(stepState('market', items({ alerts: 'unavailable', routines: 'unavailable' })), 'done');
  assert.equal(stepState('ai', items()), 'optional');
  assert.equal(stepState('ai', items({ llm: 'done' })), 'done');
  assert.equal(stepState('welcome', items()), 'unknown');
  assert.equal(stepState('fees', undefined), 'unknown');
  assert.equal(stepState('market', items({ quotes: 'unavailable', alerts: 'unavailable', routines: 'unavailable', notifications: 'unavailable' })), 'unknown');
});

test('a visit resumes at welcome before anything is set up, then at the first step still to do', () => {
  const steps = stepsFor('full');
  assert.equal(resumeStep(steps, undefined), 'welcome');
  assert.equal(resumeStep(steps, items()), 'welcome');
  assert.equal(resumeStep(steps, items({ opening: 'done' })), 'fees');
  assert.equal(resumeStep(steps, items({ opening: 'done', fees: 'done' })), 'market');
  assert.equal(resumeStep(steps, items({ opening: 'done', fees: 'done', alerts: 'done', routines: 'done' })), 'done');
  assert.equal(resumeStep(stepsFor('lab'), items({ opening: 'unavailable' })), 'welcome');
});

test('the checklist groups by state, to do first; actions lead only inside the app', () => {
  assert.deepEqual(groupItems(items()).map((group) => group.state), ['todo', 'optional', 'done']);
  assert.deepEqual(groupItems(items()).find((group) => group.state === 'todo').items.map((item) => item.id),
    ['opening', 'fees', 'alerts', 'routines']);
  assert.equal(actionHref('/setup?step=fees'), '/setup?step=fees');
  for (const bad of ['//evil.example/x', 'https://evil.example', 'javascript:alert(1)', '', undefined]) {
    assert.equal(actionHref(bad), null, String(bad));
  }
});

test('Today sends a first visitor to /setup once; never in demo or lab, after completing or dismissing, or unknown', () => {
  assert.equal(shouldRedirect({ mode: 'full', status: status(), alreadyRedirected: false }), true);
  for (const mode of ['demo', 'lab', undefined]) {
    assert.equal(shouldRedirect({ mode, status: status(), alreadyRedirected: false }), false, String(mode));
  }
  assert.equal(shouldRedirect({ mode: 'full', unknown: true, status: status(), alreadyRedirected: false }), false);
  assert.equal(shouldRedirect({ mode: 'full', status: status(), alreadyRedirected: true }), false);
  assert.equal(shouldRedirect({ mode: 'full', status: status({ dismissed: true }), alreadyRedirected: false }), false);
  assert.equal(shouldRedirect({ mode: 'full', status: status({ setup_complete: true }), alreadyRedirected: false }), false);
  assert.equal(shouldRedirect({ mode: 'full', status: status({ ledger_ready: true }), alreadyRedirected: false }), false);
  // A status that could not be read in part keeps Today: null is not false.
  for (const field of ['ledger_ready', 'dismissed', 'setup_complete']) {
    assert.equal(shouldRedirect({ mode: 'full', status: status({ [field]: null }), alreadyRedirected: false }), false, field);
  }
  assert.equal(shouldRedirect({ mode: 'full', status: undefined, alreadyRedirected: false }), false);
});

test('the redirect is remembered for the tab, also without storage', () => {
  resetRedirectMemory();
  const storage = new Memory();
  assert.equal(alreadyRedirected(storage), false);
  rememberRedirect(storage);
  assert.equal(storage.getItem(REDIRECT_KEY), '1');
  assert.equal(alreadyRedirected(storage), true);
  resetRedirectMemory();
  assert.equal(alreadyRedirected(storage), true, 'a reload of the tab still remembers');
  resetRedirectMemory();
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(alreadyRedirected(broken), false);
  rememberRedirect(broken);
  assert.equal(alreadyRedirected(broken), true, 'the page load remembers when storage throws');
  resetRedirectMemory();
  assert.equal(alreadyRedirected(null), false);
});

test('the 累计净投入 suggestion is cash plus cost, exact, and only when everything is in CNY', () => {
  const values = emptyValues({ kind: 'opening', openingCash: { CNY: '20000', HKD: '', USD: '' },
    positions: [{ ticker: 'SHA:600519', quantity: '100', cost: '1500.01' }, { ticker: '', quantity: '', cost: '' }] });
  assert.equal(openingEstimate(values), '170001');
  assert.equal(openingEstimate({ ...values, positions: [{ ticker: 'SHA:510300', quantity: '3', cost: '0.1' }, { ticker: 'SHE:000001', quantity: '3', cost: '0.2' }] }), '20000.9');
  assert.equal(openingEstimate({ ...values, openingCash: { CNY: '1', HKD: '5', USD: '' } }), null, 'HKD would need a rate');
  assert.equal(openingEstimate({ ...values, openingCash: { CNY: '1', HKD: '0', USD: '' } }), '150002');
  assert.equal(openingEstimate({ ...values, positions: [{ ticker: 'HKG:00700', quantity: '100', cost: '380' }] }), null);
  assert.equal(openingEstimate({ ...values, positions: [{ ticker: 'SHA:600519', quantity: '100', cost: '' }] }), null);
  assert.equal(openingEstimate({ ...values, openingCash: { CNY: 'abc', HKD: '', USD: '' } }), null);
  assert.equal(costEstimate([], []), '0');
  assert.equal(openingHasForeign(values), false);
  assert.equal(openingHasForeign({ ...values, openingCash: { CNY: '1', HKD: '0', USD: '' } }), false);
  assert.equal(openingHasForeign({ ...values, openingCash: { CNY: '1', HKD: '', USD: '3' } }), true);
  assert.equal(openingHasForeign({ ...values, positions: [{ ticker: 'NASDAQ:EXAMPLE', quantity: '', cost: '' }] }), true);
  assert.equal(openingHasForeign({ ...values, positions: [{ ticker: 'HKG', quantity: '', cost: '' }] }), false, 'not a ticker yet');
});

function draft(overrides = {}) {
  const value = (key, v, problem = null, extra = {}) => ({ key, value: v, problem, note: null, ...extra });
  return {
    source: 'holdings', file: 'holdings/2026-11-14.json', file_date: '2026-11-14', date: '2026-11-14', token: 'f'.repeat(64),
    accounts: [{
      key: 'a0', account: '进攻', account_editable: false, exists: false,
      cash: [value('a0.cash.CNY', '50000', null, { currency: 'CNY' })],
      contributed_cny: value('a0.contributed_cny', '500000'), fund_cny: value('a0.fund_cny', '0'),
      positions: [
        { key: 'a0.p0', index: 0, name: '示例A股', ticker: value('a0.p0.ticker', 'SHA:600000'), quantity: value('a0.p0.quantity', '1000'), cost_price: value('a0.p0.cost_price', '10.5') },
        { key: 'a0.p1', index: 1, name: '示例B股', ticker: value('a0.p1.ticker', 'SHA:600001'), quantity: value('a0.p1.quantity', '100'), cost_price: value('a0.p1.cost_price', null, '成本价无法读取：“三百五”') },
      ],
    }],
    needs_input: [{ key: 'a0.p1.cost_price', kind: 'cost', account: '进攻', label: '进攻 · 示例B股 成本价', problem: '成本价无法读取：“三百五”', choices: [], row: 'a0.p1' }],
    skipped: [], sources: [{ source: 'holdings', file: 'holdings/2026-11-14.json', date: '2026-11-14', count: 1 }],
    ...overrides,
  };
}

test('legacy answers: every value that needed input is answered or its row left out; nothing else is sent', () => {
  const d = draft();
  const answers = emptyAnswers(d);
  assert.deepEqual(answerErrors(d, answers), { 'a0.p1.cost_price': '请填写' });
  assert.deepEqual(answerErrors(d, { ...answers, fills: { 'a0.p1.cost_price': '-1' } }), { 'a0.p1.cost_price': '成本价不能为负数' });
  assert.deepEqual(answerErrors(d, { ...answers, fills: { 'a0.p1.cost_price': '三百五' } }), { 'a0.p1.cost_price': '请输入数字' });
  const filled = { ...answers, fills: { 'a0.p1.cost_price': ' 350 ', 'a0.p0.cost_price': '1' } };
  assert.deepEqual(answerErrors(d, filled), {});
  assert.deepEqual(proposalBody(d, filled), {
    source: 'holdings', date: '2026-11-14', token: 'f'.repeat(64), fills: { 'a0.p1.cost_price': '350' }, skip: [] });
  const skipped = { ...answers, skip: ['a0.p1'] };
  assert.deepEqual(answerErrors(d, skipped), {});
  assert.deepEqual(proposalBody(d, skipped).fills, {});
  assert.deepEqual([...skippableRows(d)], ['a0.p1']);
  assert.equal(accountEstimate(d.accounts[0], filled), '95500');
  assert.equal(accountEstimate(d.accounts[0], answers), null, 'the unknown cost is not guessed');
  assert.equal(accountEstimate(d.accounts[0], skipped), '60500');
  assert.equal(draftSummary(d), '1 个账户 · 2 只持仓 · 1 项需要填写');
});

test('legacy answers: a typed ticker is checked and normalised; the editable account name is required and new', () => {
  const d = draft({
    source: 'portfolio',
    accounts: [{ ...draft().accounts[0], account: '主账户', account_editable: true }],
    needs_input: [{ key: 'a0.p1.ticker', kind: 'ticker', account: '主账户', label: '主账户 · 示例 代码', problem: '需要选择交易所', choices: ['NASDAQ:X', 'NYSE:X'], row: 'a0.p1' }],
  });
  const answers = emptyAnswers(d);
  assert.equal(answers.accountName, '主账户');
  assert.equal(answerErrors(d, { ...answers, fills: { 'a0.p1.ticker': '600519' } })['a0.p1.ticker'], '代码需带交易所前缀，如 SHA:600519');
  assert.match(answerErrors(d, { ...answers, fills: { 'a0.p1.ticker': 'sha:900901' } })['a0.p1.ticker'], /^SHA:900901 是 B 股/);
  const ok ={ ...answers, fills: { 'a0.p1.ticker': 'nyse:x' } };
  assert.deepEqual(answerErrors(d, ok), {});
  assert.deepEqual(answerErrors(d, ok, ['主账户']), { account: '账本中已有同名账户，请换一个名称' });
  assert.deepEqual(answerErrors(d, { ...ok, accountName: ' ' }), { account: '请填写账户名称' });
  const body = proposalBody(d, { ...ok, accountName: ' 老账户 ' });
  assert.deepEqual(body.fills, { 'a0.p1.ticker': 'NYSE:X' });
  assert.equal(body.account_name, '老账户');
});

const SETTINGS = {
  presets: [
    { id: 'deepseek', label: 'DeepSeek', group: 'deepseek', name: 'DeepSeek', description: '', provider_type: 'openai',
      base_url: 'https://api.deepseek.com/v1', base_url_editable: false,
      models: [{ id: 'deepseek-chat', name: 'DeepSeek Chat', supports_vision: false }] },
    { id: 'openai', label: 'OpenAI', group: 'openai_official', name: 'OpenAI', description: '', provider_type: 'openai',
      base_url: 'https://api.openai.com/v1', base_url_editable: false,
      models: [{ id: 'gpt-4o', name: 'GPT-4o', supports_vision: true }] },
    { id: 'compatible', label: '兼容接口', group: 'openai_compatible', name: 'Compatible', description: '', provider_type: 'openai',
      base_url: 'http://localhost:11434/v1', base_url_editable: true, models: [] },
  ],
  current: null,
};

function current(overrides = {}) {
  return { preset: 'deepseek', group: 'deepseek', group_name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat', supports_vision: false, key: { configured: true, source: 'config', env: null }, local: false, ...overrides };
}

test('the model form starts from the saved choice, never with a key; the key is required only when none is in place', () => {
  const fresh = initialForm(SETTINGS);
  assert.deepEqual(fresh, { preset: 'deepseek', model: 'deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', apiKey: '', supportsVision: false });
  assert.deepEqual(formErrors(fresh, SETTINGS), { apiKey: '请填写 API 密钥' });
  const saved = { ...SETTINGS, current: current() };
  assert.equal(initialForm(saved).apiKey, '');
  assert.deepEqual(formErrors(initialForm(saved), saved), {}, 'the saved key stays when the field is empty');
  assert.equal(keyStatus(saved.current, 'deepseek'), '已保存（不显示）；留空则继续使用');
  assert.equal(keyStatus(saved.current, 'openai'), '还没有保存这个服务商的密钥');
  const env = current({ preset: 'openai', key: { configured: true, source: 'env', env: 'OPENAI_API_KEY' } });
  assert.match(keyStatus(env, 'openai'), /OPENAI_API_KEY/);
  assert.equal(canTest(saved.current, 'deepseek'), true);
  assert.equal(canTest(saved.current, 'openai'), false);
  assert.equal(canTest(null, 'deepseek'), false);
  const local = switchPreset({ ...fresh, apiKey: 'typed' }, SETTINGS.presets[2]);
  assert.deepEqual(local, { preset: 'compatible', model: '', baseUrl: 'http://localhost:11434/v1', apiKey: 'typed', supportsVision: false });
  assert.deepEqual(formErrors({ ...local, apiKey: '', model: 'llama3' }, SETTINGS), {}, 'a local server needs no key');
  assert.deepEqual(Object.keys(formErrors({ ...local, apiKey: '', model: 'llama3', baseUrl: 'https://remote.example/v1' }, SETTINGS)), ['apiKey']);
  assert.match(formErrors({ ...fresh, apiKey: 'sk-with space' }, SETTINGS).apiKey, /空格/);
  assert.match(formErrors({ ...fresh, apiKey: 'x'.repeat(401) }, SETTINGS).apiKey, /400/);
  assert.match(formErrors({ ...fresh, model: 'bad model', apiKey: 'k' }, SETTINGS).model, /模型名称/);
});

test('the save body carries the key only when typed, the address only where it can be chosen', () => {
  const [deepseek, , compatible] = SETTINGS.presets;
  assert.deepEqual(saveBody({ preset: 'deepseek', model: 'deepseek-chat', baseUrl: 'ignored', apiKey: ' sk-1 ', supportsVision: true }, deepseek),
    { preset: 'deepseek', model: 'deepseek-chat', api_key: 'sk-1' });
  assert.deepEqual(saveBody({ preset: 'deepseek', model: 'deepseek-chat', baseUrl: '', apiKey: '', supportsVision: false }, deepseek),
    { preset: 'deepseek', model: 'deepseek-chat' });
  assert.deepEqual(saveBody({ preset: 'compatible', model: 'llama3', baseUrl: ' http://127.0.0.1:8000/v1 ', apiKey: '', supportsVision: true }, compatible),
    { preset: 'compatible', model: 'llama3', base_url: 'http://127.0.0.1:8000/v1', supports_vision: true });
  assert.equal(visionOf(SETTINGS.presets[1], { model: 'gpt-4o', supportsVision: false }), true);
  assert.equal(visionOf(compatible, { model: 'llama3', supportsVision: true }), true);
});

test('API addresses follow the backend rule', () => {
  for (const ok of ['https://api.example.test/v1', 'http://127.0.0.1:8000/v1', 'http://localhost:11434/v1', 'http://[::1]:8000/v1']) {
    assert.equal(baseUrlProblem(ok), null, ok);
  }
  for (const bad of ['', 'ftp://x/v1', 'file:///etc/passwd', 'http://u:p@x/v1', 'http://x/v1?key=1', 'http://x/v1?', 'http://x/v1#a',
    'javascript:alert(1)', '/v1', 'http://a b/v1']) {
    assert.ok(baseUrlProblem(bad), bad);
  }
});

test('the test result reads in fixed words; success adds the model and the time', () => {
  assert.equal(testText({ ok: true, code: 'ok', message: '连接成功：模型已回应。', model: 'deepseek-chat', latency_ms: 812 }),
    '连接成功：模型已回应。（deepseek-chat，0.8 秒）');
  assert.equal(testText({ ok: false, code: 'auth', message: '密钥无效或已失效（服务返回 401）。请检查 API 密钥。', status: 401 }),
    '密钥无效或已失效（服务返回 401）。请检查 API 密钥。');
});

test('a saved key stays with its address: another address needs a typed key, and 测试连接 waits for the save', () => {
  const compatible = SETTINGS.presets[2];
  const saved = { ...SETTINGS, current: current({ preset: 'compatible', group: 'openai_compatible', base_url: 'https://api.deepseek.com/v1' }) };
  const form = { preset: 'compatible', model: 'deepseek-chat', baseUrl: 'https://api.deepseek.com/v1/', apiKey: '', supportsVision: false };
  assert.deepEqual(formErrors(form, saved), {}, 'the same address keeps the saved key');
  assert.equal(canTest(saved.current, 'compatible', form), true);
  const moved = { ...form, baseUrl: 'https://openrouter.example.test/api/v1' };
  assert.deepEqual(formErrors(moved, saved), { apiKey: NEW_ADDRESS_KEY });
  assert.deepEqual(formErrors({ ...moved, baseUrl: 'https://api.deepseek.com:8443/v1' }, saved), { apiKey: NEW_ADDRESS_KEY }, 'another port');
  assert.deepEqual(formErrors({ ...moved, baseUrl: 'http://api.deepseek.com/v1' }, saved), { apiKey: NEW_ADDRESS_KEY }, 'another scheme');
  assert.deepEqual(formErrors({ ...moved, apiKey: 'sk-new' }, saved), {});
  assert.deepEqual(formErrors({ ...moved, baseUrl: 'http://127.0.0.1:8000/v1' }, saved), {}, 'a local server needs no key');
  assert.equal(keyStatus(saved.current, 'compatible', compatible, moved), NEW_ADDRESS_KEY);
  assert.equal(keyStatus(saved.current, 'compatible', compatible, form), '已保存（不显示）；留空则继续使用');
  assert.equal(canTest(saved.current, 'compatible', moved), false, 'the test would reach the saved address, not this one');
});

test('a key in the server environment counts for its provider before anything is saved', () => {
  const anthropic = { id: 'anthropic', label: 'Anthropic', group: 'anthropic_official', name: 'Anthropic', description: '',
    provider_type: 'claude', base_url: 'https://api.anthropic.com/v1', base_url_editable: false,
    models: [{ id: 'claude-x', name: 'Claude', supports_vision: true }], env_key: { name: 'ANTHROPIC_API_KEY', available: true } };
  const settings = { presets: [...SETTINGS.presets, anthropic],
    current: current({ preset: 'openai', group: 'openai_official', key: { configured: false, source: null, env: 'OPENAI_API_KEY' } }) };
  const form = switchPreset(initialForm(settings), anthropic);
  assert.deepEqual(formErrors(form, settings), {});
  assert.equal(keyStatus(settings.current, 'anthropic', anthropic, form), '使用环境变量 ANTHROPIC_API_KEY 中的密钥；在这里填写的不会覆盖它');
  const absent = { ...anthropic, env_key: { name: 'ANTHROPIC_API_KEY', available: false } };
  assert.deepEqual(formErrors(form, { ...settings, presets: [...SETTINGS.presets, absent] }), { apiKey: '请填写 API 密钥' });
});

test('the account step offers legacy accounts not migrated yet, also on the direct link, and keeps a fully migrated file closed', () => {
  const opened = { id: 'opening', label: '', state: 'done', detail: '已有期初余额：进攻', action: null };
  const offer = { id: 'legacy', label: '', state: 'optional', detail: '还没有迁移：稳健', action: { label: '迁移旧版持仓', href: '/setup?step=account&start=legacy' } };
  assert.deepEqual(accountStepView(opened, offer, null), { ways: false, migrate: false, offer: true, notice: true });
  assert.deepEqual(accountStepView(opened, offer, 'legacy'), { ways: false, migrate: true, offer: false, notice: true });
  // Everything migrated: the migration stays closed, also on its direct link (as before round 4: no second copy).
  assert.deepEqual(accountStepView(opened, { ...offer, state: 'done' }, 'legacy'), { ways: false, migrate: false, offer: false, notice: false });
  assert.deepEqual(accountStepView(opened, undefined, 'legacy'), { ways: false, migrate: false, offer: false, notice: false });
  assert.deepEqual(accountStepView(opened, { ...offer, state: 'done' }, null), { ways: false, migrate: false, offer: false, notice: false });
  const empty = { ...opened, state: 'todo' };
  assert.deepEqual(accountStepView(empty, { ...offer, state: 'todo' }, null), { ways: true, migrate: false, offer: false, notice: true });
  assert.deepEqual(accountStepView(undefined, undefined, 'legacy'), { ways: true, migrate: false, offer: false, notice: false });
});

test('the 累计净投入 suggestion counts everything the opening books, the fund too, and says it must be replaced', () => {
  const value = (key, v, problem = null, extra = {}) => ({ key, value: v, problem, note: null, ...extra });
  const account = { ...draft().accounts[0], cash: [value('a0.cash.CNY', '10000', null, { currency: 'CNY' })],
    contributed_cny: value('a0.contributed_cny', null, '原文件没有累计净投入（cost_basis）'), fund_cny: value('a0.fund_cny', '40000'), positions: [] };
  const d = draft({ accounts: [account], needs_input: [] });
  assert.equal(accountEstimate(account, emptyAnswers(d)), '50000');
  const unknownFund = { ...account, fund_cny: value('a0.fund_cny', null, '基金估值无法读取') };
  assert.equal(accountEstimate(unknownFund, emptyAnswers(d)), null, 'an unknown fund value is not guessed');
  assert.equal(accountEstimate(unknownFund, { ...emptyAnswers(d), fills: { 'a0.fund_cny': '40000' } }), '50000');
  const values = emptyValues({ kind: 'opening', openingCash: { CNY: '10000', HKD: '', USD: '' }, fundValue: '40000',
    positions: [{ ticker: 'SHA:600519', quantity: '10', cost: '100' }] });
  assert.equal(openingEstimate(values), '51000');
  assert.equal(openingEstimate({ ...values, fundValue: 'abc' }), null);
  assert.match(ESTIMATE_LABEL, /基金/);
  assert.match(ESTIMATE_LABEL, /实际/);
});

test('the legacy date can be chosen when the file has none; long file values are shown rounded with every digit kept', () => {
  assert.deepEqual(legacyControls({ date: null, loaded: undefined, last: null, failed: true, missing: false }), { show: true, date: '' });
  assert.deepEqual(legacyControls({ date: null, loaded: undefined, last: null, failed: true, missing: true }), { show: false, date: '' });
  assert.deepEqual(legacyControls({ date: '2026-11-01', loaded: undefined, last: null, failed: true, missing: false }), { show: true, date: '2026-11-01' });
  const d = draft();
  assert.deepEqual(legacyControls({ date: null, loaded: d, last: d, failed: false, missing: false }), { show: true, date: '2026-11-14' });
  assert.deepEqual(legacyControls({ date: null, loaded: undefined, last: d, failed: true, missing: false }), { show: true, date: '' },
    'another source that needs a date does not show the last file\'s date as if it applied');
  const cost = draftValueText('price', '32.016666666666666666666666667', 'CNY');
  assert.ok(cost.text.startsWith('≈') && cost.text.length < 16, cost.text);
  assert.match(cost.full, /32\.016666666666666666666666667/);
  assert.deepEqual(draftValueText('quantity', '1000'), { text: '1,000' });
  assert.deepEqual(draftValueText('ticker', 'SHA:600000'), { text: 'SHA:600000' });
});

test('the legacy source list offers only what is not migrated yet', () => {
  const d = draft({ sources: [
    { source: 'holdings', file: 'holdings/2026-11-14.json', date: '2026-11-14', count: 1, pending: false },
    { source: 'portfolio', file: 'data/portfolio.json', date: null, count: 1, pending: true },
  ] });
  assert.deepEqual(legacySources(d).map((item) => item.source), ['portfolio']);
  assert.deepEqual(legacySources(draft()).map((item) => item.source), ['holdings'], 'an older payload without the flag');
});

// The API's answer for each address (tests/test_setup_api.py KEYLESS_TABLE): one key saved for 兼容接口 at
// DeepSeek's address, OPENAI_API_KEY in the server environment. The form needs a typed key exactly where the API does.
const KEYLESS_TABLE = [
  ['https://api.deepseek.com/v1', null],
  ['https://API.DeepSeek.com:443/v2', null],
  ['https://api.deepseek.com:8443/v1', 'new_address'],
  ['http://api.deepseek.com/v1', 'new_address'],
  ['https://openrouter.example.test/api/v1', 'new_address'],
  ['https://api.openai.com/v1', null],
  ['http://api.openai.com:8080/v1', null],
  ['http://127.0.0.1:8000/v1', null],
  ['http://localhost:11434/v1', null],
];

test('the form needs a typed key exactly where the API does, from what the API says per preset', () => {
  const env = [{ host: 'api.openai.com', name: 'OPENAI_API_KEY' }];
  const compatible = { ...SETTINGS.presets[2], keyless: { origins: ['https://api.deepseek.com:443'], env, loopback: true, saved_key: true } };
  const settings = { presets: [SETTINGS.presets[0], SETTINGS.presets[1], compatible],
    current: current({ preset: 'compatible', group: 'openai_compatible', base_url: 'https://api.deepseek.com/v1' }) };
  for (const [url, needed] of KEYLESS_TABLE) {
    const form = { preset: 'compatible', model: 'm', baseUrl: url, apiKey: '', supportsVision: false };
    assert.deepEqual(formErrors(form, settings), needed ? { apiKey: NEW_ADDRESS_KEY } : {}, url);
  }
  // The regression: the official OpenAI address under 兼容接口 takes the environment's key, as the API does.
  const official = { preset: 'compatible', model: 'gpt-4o', baseUrl: 'https://api.openai.com/v1', apiKey: '', supportsVision: false };
  assert.equal(keyStatus(settings.current, 'compatible', compatible, official), '使用环境变量 OPENAI_API_KEY 中的密钥；在这里填写的不会覆盖它');
  // Nothing saved yet for this preset: a remote address needs a key, without the "new address" wording.
  const fresh = { ...compatible, keyless: { origins: [], env: [], loopback: true, saved_key: false } };
  const empty = { presets: [fresh], current: null };
  assert.deepEqual(formErrors({ ...official, baseUrl: 'https://openrouter.example.test/api/v1' }, empty), { apiKey: '请填写 API 密钥' });
  assert.deepEqual(formErrors({ ...official, baseUrl: 'http://127.0.0.1:8000/v1' }, empty), {});
});

test('the wizard is a hidden route at /setup, inside the app shell, in every mode and every build', async () => {
  register('./tsxHooks.mjs', import.meta.url);
  const { hiddenRoutes, navPages } = await import('../src/routes.tsx');
  const setup = hiddenRoutes.find((route) => route.path === '/setup');
  assert.ok(setup, '/setup is a hidden route');
  assert.equal(setup.bare, undefined, 'rendered with the shell');
  assert.equal(setup.modes, undefined, 'every mode (the page itself adapts to lab and demo)');
  assert.ok(!navPages.some((page) => page.path === '/setup'), 'not in the navigation');
});
