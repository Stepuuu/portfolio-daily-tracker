// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// Where 记一笔 starts: the account and kind used last, the date from the backend's trading calendar (none
// when it is unavailable), and what the dialog was opened with always winning.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  LAST_ACCOUNT_KEY, LAST_CURRENCY_KEY, LAST_KIND_KEY, dateLabel, defaultAccount, defaultCurrency, defaultDate, defaultKind,
  marketOfTicker, rememberEntry, zonedToday,
} from '../src/lib/entryDefaults.ts';

function memory(initial = {}) {
  const data = new Map(Object.entries(initial));
  return { getItem: (key) => (data.has(key) ? data.get(key) : null), setItem: (key, value) => data.set(key, String(value)), data };
}

const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };

test('account: requested, else last used, else the first (the only one)', () => {
  const accounts = ['长期账户', '研究账户'];
  assert.equal(defaultAccount(accounts, memory(), '研究账户'), '研究账户');
  assert.equal(defaultAccount(accounts, memory({ [LAST_ACCOUNT_KEY]: '研究账户' })), '研究账户');
  assert.equal(defaultAccount(accounts, memory({ [LAST_ACCOUNT_KEY]: '已删除的账户' })), '长期账户');
  assert.equal(defaultAccount(['唯一账户'], null), '唯一账户');
  assert.equal(defaultAccount(accounts, memory(), '不存在'), '长期账户', 'an unknown requested account falls back');
  assert.equal(defaultAccount([], memory()), '');
  assert.equal(defaultAccount(accounts, broken), '长期账户', 'blocked storage is not an error');
});

test('kind: requested, else last used, else 买入; 期初余额 when there is no account', () => {
  assert.equal(defaultKind(true, memory(), 'sell'), 'sell');
  assert.equal(defaultKind(true, memory({ [LAST_KIND_KEY]: 'dividend' })), 'dividend');
  assert.equal(defaultKind(true, memory({ [LAST_KIND_KEY]: 'interest' })), 'interest');
  assert.equal(defaultKind(true, memory({ [LAST_KIND_KEY]: 'opening' })), 'buy', 'a new account is never the default');
  assert.equal(defaultKind(true, memory({ [LAST_KIND_KEY]: 'reverse' })), 'buy', 'reversals are not entered here');
  assert.equal(defaultKind(true, memory(), 'bogus'), 'buy');
  assert.equal(defaultKind(false, memory({ [LAST_KIND_KEY]: 'sell' }), 'buy'), 'opening');
  assert.equal(defaultKind(true, broken), 'buy');
});

test('currency of cash entries: last used, else CNY', () => {
  assert.equal(defaultCurrency(memory({ [LAST_CURRENCY_KEY]: 'USD' })), 'USD');
  assert.equal(defaultCurrency(memory({ [LAST_CURRENCY_KEY]: 'EUR' })), 'CNY');
  assert.equal(defaultCurrency(null), 'CNY');
});

test('remembering stores account, kind and currency; blocked storage loses only the convenience', () => {
  const storage = memory();
  rememberEntry(storage, { account: '长期账户', kind: 'sell', currency: 'HKD' });
  assert.equal(storage.data.get(LAST_ACCOUNT_KEY), '长期账户');
  assert.equal(storage.data.get(LAST_KIND_KEY), 'sell');
  assert.equal(storage.data.get(LAST_CURRENCY_KEY), 'HKD');
  rememberEntry(storage, { account: '研究账户' });
  assert.equal(storage.data.get(LAST_KIND_KEY), 'sell', 'missing fields keep what was stored');
  assert.doesNotThrow(() => rememberEntry(broken, { account: 'x' }));
  assert.doesNotThrow(() => rememberEntry(null, { account: 'x' }));
});

function calendar(today, sessions = {}) {
  const market = (session) => (session ? { session, verified: true } : { session: null, verified: false });
  return { today, markets: { CN: market(sessions.CN), HK: market(sessions.HK), US: market(sessions.US) } };
}

test('date of a trade: the latest verified session of its market, from the backend', () => {
  // Tuesday 2026-11-17 in the morning session.
  assert.deepEqual(defaultDate(calendar('2026-11-17', { CN: '2026-11-17', US: '2026-11-16' }), { trade: true, market: 'CN' }),
    { date: '2026-11-17', reason: null });
  // New York's last session is Monday's.
  assert.equal(defaultDate(calendar('2026-11-17', { CN: '2026-11-17', US: '2026-11-16' }), { trade: true, market: 'US' }).date, '2026-11-16');
  // Saturday 2026-11-21: Friday, and it says why.
  assert.deepEqual(defaultDate(calendar('2026-11-21', { CN: '2026-11-20' }), { trade: true, market: 'CN' }),
    { date: '2026-11-20', reason: '默认最近一个交易日 2026-11-20' });
  // Before a ticker is chosen: the mainland session.
  assert.equal(defaultDate(calendar('2026-11-21', { CN: '2026-11-20' }), { trade: true }).date, '2026-11-20');
  // No calendar, or one that cannot verify the day: no default, the date must be picked.
  for (const cal of [null, undefined, calendar('2026-11-21'), calendar(null, { CN: '2026-11-20' })]) {
    assert.deepEqual(defaultDate(cal, { trade: true, market: 'CN' }), { date: '', reason: '交易日历暂不可用，请选择成交日期' });
  }
});

test('date of a cash entry: today by the backend, else none', () => {
  assert.deepEqual(defaultDate(calendar('2026-11-21', { CN: '2026-11-20' }), { trade: false }), { date: '2026-11-21', reason: null });
  assert.deepEqual(defaultDate(null, { trade: false }), { date: '', reason: '暂时无法确认今天的日期，请选择日期' });
  // What the dialog was opened with wins.
  assert.deepEqual(defaultDate(null, { trade: true, requested: '2026-11-18' }), { date: '2026-11-18', reason: null });
});

test('only today is labelled 今天', () => {
  assert.equal(dateLabel('2026-11-21', calendar('2026-11-21')), '今天');
  assert.equal(dateLabel('2026-11-20', calendar('2026-11-21')), null);
  assert.equal(dateLabel('2026-11-21', null), null, 'unknown without the backend');
  assert.equal(marketOfTicker('NASDAQ:DEMO'), 'US');
  assert.equal(marketOfTicker('HKG:00700'), 'HK');
  assert.equal(marketOfTicker('SHE:000001'), 'CN');
  assert.equal(marketOfTicker(null), null);
});

test('today is counted in Beijing time', () => {
  // 2026-11-17 17:30 UTC is already the 18th in Beijing.
  assert.equal(zonedToday(new Date('2026-11-17T17:30:00Z')), '2026-11-18');
  assert.equal(zonedToday(new Date('2026-11-17T15:59:00Z')), '2026-11-17');
  assert.equal(zonedToday(new Date('2026-11-17T17:30:00Z'), 'America/New_York'), '2026-11-17');
});
