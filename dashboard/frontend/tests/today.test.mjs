// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The Today page's decisions: poll rhythm, staleness, quote chips, totals qualifiers, rows, alerts and
// the snapshot chip. Payload shapes follow the contract in dashboard/core/market/today.py.
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  MARKETS, POLL_IN_SESSION_MS, POLL_OUT_OF_SESSION_MS, heldMarkets, isMoving, marketOf, marketStates, phaseLabel,
  pollIntervalMs, sessionChip, sourceLabel,
} from '../src/features/today/session.ts';
import { quoteLine, shortTime, statusChip } from '../src/features/today/quoteStatus.ts';
import { cashBalances, estimateItems, isZeroAmount, missingSummary, totalsView } from '../src/features/today/totals.ts';
import {
  accountSummaries, accountWeightsEstimated, costFigure, costText, holdingRows, instrumentKind, priceMoves, priceSnapshot,
  weightEstimate, weightText,
} from '../src/features/today/rows.ts';
import { connectionState } from '../src/features/today/connection.ts';
import { snapshotChip } from '../src/features/today/snapshot.ts';
import { alertLine, alertsListState, monitorState, todaysEvents } from '../src/features/today/alerts.ts';

// -- fixtures -------------------------------------------------------------------------------------

const TODAY = '2026-11-17';

function market(phase, overrides = {}) {
  return { phase, trading_day: TODAY, is_trading_day: true, calendar_confidence: 'calendar', next_open: null, ...overrides };
}

function session(cn, hk = market('closed'), us = market('closed', { trading_day: '2026-11-16' })) {
  return { market: 'CN', ...cn, markets: { CN: cn, HK: hk, US: us } };
}

function quote(status, overrides = {}) {
  return {
    source: 'eastmoney', quote_time: `${TODAY}T10:32:05+08:00`, fetched_at: `${TODAY}T10:32:06+08:00`, status,
    reason: null, session_date: TODAY, ...overrides,
  };
}

function row(ticker, overrides = {}) {
  return {
    ticker, name: `名称${ticker}`, currency: 'CNY', quantity: '100', cost_price: '10', price: '12', prev_close: '11.5',
    change_pct: '4.35', market_value: '1200.00', market_value_cny: '1200.00', day_pnl: '50.00', day_pnl_cny: '50.00',
    total_pnl: '200.00', total_pnl_cny: '200.00', total_pnl_pct: '20.00', weight_pct: '50.00', quote: quote('live'),
    estimated: false, estimated_reason: null, ...overrides,
  };
}

function account(name, positions, overrides = {}) {
  return {
    name, positions, closed_today: [], cash: { CNY: '1000.00' }, cash_cny: '1000.00', fund_cny: '0.00',
    market_value_cny: '1200.00', total_cny: '2200.00', day_pnl_cny: '50.00', total_pnl_cny: '200.00', quality: 'complete',
    estimated: [], ...overrides,
  };
}

function totals(overrides = {}) {
  return {
    by_currency: { CNY: { market_value: '1200.00', cash: '1000.00' } }, total_cny: '2200.00', day_pnl_cny: '50.00',
    day_pnl_pct: '2.33', total_pnl_cny: '200.00', total_pnl_pct: '20.00', total_pnl_kind: 'unrealized',
    contributed_cny: '2000.00', cumulative_pnl_cny: '200.00', cumulative_pnl_pct: '10.00', day_pnl_excludes: ['dividend', 'fee'],
    unavailable: [], quality: 'complete', estimated: [], ...overrides,
  };
}

function payload(accounts, overrides = {}) {
  return {
    as_of: `${TODAY}T10:32:07+08:00`, source: 'eastmoney', holdings_source: 'confirmed_ledger', ledger_revision: 3,
    session: session(market('morning')), fx: {}, accounts, totals: totals(), notices: [], ...overrides,
  };
}

// -- sessions and polling -------------------------------------------------------------------------

test('a ticker belongs to its exchange market; indexes and junk to none', () => {
  assert.equal(marketOf('SHA:600519'), 'CN');
  assert.equal(marketOf('she:000001'), 'CN');
  assert.equal(marketOf('HKG:00700'), 'HK');
  assert.equal(marketOf('NASDAQ:DEMO'), 'US');
  assert.equal(marketOf('NYSE:X'), 'US');
  assert.equal(marketOf('IDX:000300'), null);
  assert.equal(marketOf(''), null);
  assert.equal(marketOf(undefined), null);
});

test('polls every 5 s while a held market trades (auction included), every 60 s otherwise', () => {
  const cn = payload([account('A', [row('SHA:600519')])]);
  for (const phase of ['morning', 'afternoon', 'call_auction']) {
    assert.equal(pollIntervalMs({ ...cn, session: session(market(phase)) }), POLL_IN_SESSION_MS, phase);
  }
  for (const phase of ['pre_open', 'lunch_break', 'closed']) {
    assert.equal(pollIntervalMs({ ...cn, session: session(market(phase)) }), POLL_OUT_OF_SESSION_MS, phase);
  }
  // A holiday is never "in session", whatever the phase says.
  assert.equal(pollIntervalMs({ ...cn, session: session(market('morning', { is_trading_day: false })) }), POLL_OUT_OF_SESSION_MS);
  // US open at night: only a portfolio holding US shares polls fast.
  const night = session(market('closed'), market('closed'), market('open'));
  assert.equal(pollIntervalMs({ ...cn, session: night }), POLL_OUT_OF_SESSION_MS);
  assert.equal(pollIntervalMs({ ...payload([account('A', [row('SHA:600519'), row('NASDAQ:DEMO', { currency: 'USD' })])]), session: night }), POLL_IN_SESSION_MS);
  // Nothing held: the A-share session decides. No data yet: the slow rhythm.
  assert.equal(pollIntervalMs(payload([])), POLL_IN_SESSION_MS);
  assert.equal(pollIntervalMs(undefined), POLL_OUT_OF_SESSION_MS);
  assert.equal(POLL_IN_SESSION_MS, 5000);
  assert.equal(POLL_OUT_OF_SESSION_MS, 60000);
});

test('held markets include positions sold out today', () => {
  const data = payload([account('A', [row('SHA:1')], { closed_today: [row('HKG:00700', { currency: 'HKD', quantity: '0' })] })]);
  assert.deepEqual(heldMarkets(data), ['CN', 'HK']);
  assert.deepEqual(MARKETS, ['CN', 'HK', 'US']);
});

test('phase labels', () => {
  assert.equal(phaseLabel(market('morning'), 'CN'), '盘中');
  assert.equal(phaseLabel(market('call_auction'), 'CN'), '集合竞价');
  assert.equal(phaseLabel(market('lunch_break'), 'CN'), '午间休市');
  assert.equal(phaseLabel(market('pre_open'), 'CN'), '未开盘');
  assert.equal(phaseLabel(market('closed'), 'CN'), '已收盘');
  assert.equal(phaseLabel(market('closed', { is_trading_day: false }), 'CN'), '休市');
  assert.equal(phaseLabel(market('open'), 'US'), '盘中');
  // HK/US say only "closed": before the open when the next open is on the same trading day.
  assert.equal(phaseLabel(market('closed', { trading_day: TODAY, next_open: `${TODAY}T09:30:00+08:00` }), 'HK'), '未开盘');
  assert.equal(phaseLabel(market('closed', { trading_day: '2026-11-16', next_open: `${TODAY}T22:30:00+08:00` }), 'US'), '已收盘');
  assert.equal(phaseLabel(market('closed', { trading_day: TODAY, next_open: `${TODAY}T22:30:00+08:00` }), 'US'), '未开盘');
  assert.ok(isMoving(market('open')) && !isMoving(market('lunch_break')) && !isMoving(undefined));
});

test('session chip names the markets trading now, else the first relevant one', () => {
  const both = payload([account('A', [row('SHA:1'), row('HKG:00700', { currency: 'HKD' })])], { session: session(market('morning'), market('open')) });
  assert.deepEqual(sessionChip(both), { text: 'A股·港股 盘中', moving: true, uncertain: false, qualifier: null, title: null });
  const closed = payload([account('A', [row('SHA:1')])], { session: session(market('closed')) });
  assert.deepEqual(sessionChip(closed), { text: 'A股 已收盘', moving: false, uncertain: false, qualifier: null, title: null });
  const auction = payload([account('A', [row('SHA:1'), row('HKG:00700', { currency: 'HKD' })])], { session: session(market('call_auction'), market('open')) });
  assert.deepEqual(sessionChip(auction), { text: 'A股 集合竞价 · 港股 盘中', moving: true, uncertain: false, qualifier: null, title: null });
  assert.equal(sessionChip(undefined), null);
  assert.deepEqual(marketStates(closed).map((state) => state.label), ['A股']);
});

test('a session decided by weekday alone keeps its uncertainty: amber qualifier and a reason', () => {
  // 2027-01-01 in a year without a holiday table: the calendar says a morning session by weekday only.
  const guessed = market('morning', { trading_day: '2027-01-01', calendar_confidence: 'weekday_only' });
  const data = payload([], { session: session(guessed) });
  const [cn] = marketStates(data);
  assert.equal(cn.uncertain, true);
  assert.match(cn.reason, /假期|休市/);
  const chip = sessionChip(data);
  assert.equal(chip.text, 'A股 盘中');
  assert.equal(chip.uncertain, true, 'an unqualified 盘中 would claim the market is open');
  assert.equal(chip.qualifier, '按工作日推算');
  assert.match(chip.title, /假期表/);
  // A known calendar has no qualifier.
  const known = sessionChip(payload([], { session: session(market('morning')) }));
  assert.deepEqual([known.uncertain, known.qualifier, known.title], [false, null, null]);
  // Several markets: uncertain when any market the chip names is.
  const both = payload([account('A', [row('SHA:1'), row('HKG:00700', { currency: 'HKD' })])], {
    session: session(market('morning'), market('open', { calendar_confidence: 'weekday_only' })),
  });
  assert.equal(sessionChip(both).uncertain, true);
  assert.match(sessionChip(both).title, /港股/);
});

test('source names', () => {
  assert.equal(sourceLabel('eastmoney'), '东财');
  assert.equal(sourceLabel('eastmoney+yahoo'), '东财+雅虎');
  assert.equal(sourceLabel('tencent'), '腾讯');
  assert.equal(sourceLabel('eastmoney+tencent+yahoo'), '东财+腾讯+雅虎');
  assert.equal(sourceLabel('fake'), '模拟行情');
  assert.equal(sourceLabel('none'), '无行情源');
  assert.equal(sourceLabel(null), '');
  assert.equal(sourceLabel('other'), 'other');
});

// -- connection -----------------------------------------------------------------------------------

test('the connection banner: failed request, or data older than twice the poll interval', () => {
  const base = { receivedAt: 1_000_000, intervalMs: 5000, failed: false, fetching: false };
  assert.equal(connectionState({ ...base, now: 1_000_000 + 10_000 }), 'ok');
  assert.equal(connectionState({ ...base, now: 1_000_000 + 10_001 }), 'stale');
  assert.equal(connectionState({ ...base, intervalMs: 60_000, now: 1_000_000 + 60_000 }), 'ok');
  // A request on its way does not make old data current: a refresh that hangs (or a tab coming back
  // from the background and fetching) still shows the age until a response arrives.
  assert.equal(connectionState({ ...base, fetching: true, now: 1_000_000 + 10_001 }), 'stale');
  assert.equal(connectionState({ ...base, fetching: true, now: 1_000_000 + 30_000 }), 'stale');
  assert.equal(connectionState({ ...base, fetching: true, now: 1_000_000 + 9_000 }), 'ok');
  assert.equal(connectionState({ ...base, failed: true, now: 1_000_000 }), 'failed');
  assert.equal(connectionState({ ...base, receivedAt: null, now: 9e12 }), 'ok');
  assert.equal(connectionState({ ...base, receivedAt: null, failed: true, now: 9e12 }), 'failed');
});

// Which answer is shown (by request order, never by the payload's own time) is in todayFeed.test.mjs.

// -- quote chips ----------------------------------------------------------------------------------

test('quote line: live shows source and time, no chip', () => {
  const line = quoteLine(quote('live'), TODAY);
  assert.equal(line.meta, '东财 10:32:05');
  assert.equal(line.chip, null);
  assert.equal(line.estimate, null);
});

test('every quote status maps to its chip; unverified prices are amber', () => {
  assert.deepEqual(statusChip(quote('delayed'), TODAY), { label: '延迟', tone: 'neutral', title: '行情延迟，非实时价格' });
  assert.equal(statusChip(quote('closed', { quote_time: `${TODAY}T15:00:00+08:00` }), TODAY).label, '收盘价 15:00');
  assert.equal(statusChip(quote('closed'), TODAY).tone, 'neutral');
  // A close from a weekday-only calendar makes the row estimated: amber.
  assert.equal(statusChip(quote('closed', { quote_time: '2026-11-17T05:00:00+08:00' }), TODAY, true, '未核实').tone, 'warn');
  assert.equal(statusChip(quote('closed', { quote_time: '2026-11-16T05:00:00+08:00' }), TODAY).label, '收盘价 11-16 05:00');
  const stale = statusChip(quote('stale', { quote_time: `${TODAY}T10:20:00+08:00` }), TODAY);
  assert.deepEqual([stale.label, stale.tone], ['已过期 10:20', 'warn']);
  assert.deepEqual([statusChip(quote('prev_close'), TODAY).label, statusChip(quote('prev_close'), TODAY).tone], ['昨收价', 'warn']);
  assert.deepEqual([statusChip(quote('time_unknown'), TODAY).label, statusChip(quote('time_unknown'), TODAY).tone], ['时间未知', 'warn']);
  const missing = statusChip(quote('unavailable', { reason: '数据源维护', quote_time: null }), TODAY);
  assert.deepEqual(missing, { label: '不可用', tone: 'warn', title: '数据源维护' });
  // Only fresh prices show a time in the line; the chip carries the others'.
  assert.equal(quoteLine(quote('stale'), TODAY).meta, '东财');
  assert.equal(quoteLine(quote('unavailable', { source: null }), TODAY).meta, '');
});

test('a verified quote on an estimated row (stale currency rate) gets an amber 估算 chip', () => {
  const line = quoteLine(quote('live'), TODAY, true, '汇率已过期');
  assert.deepEqual(line.estimate, { label: '估算', tone: 'warn', title: '汇率已过期' });
  // The status chip already says it for the quote's own reasons.
  assert.equal(quoteLine(quote('stale'), TODAY, true, 'x').estimate, null);
});

test('short times are Beijing time; another day adds the date', () => {
  assert.equal(shortTime('2026-11-17T02:32:05Z', TODAY), '10:32');
  assert.equal(shortTime('2026-11-17T02:32:05Z', TODAY, true), '10:32:05');
  assert.equal(shortTime('2026-11-16T21:00:00Z', TODAY), '05:00');
  assert.equal(shortTime('2026-11-15T21:00:00Z', TODAY), '11-16 05:00');
  assert.equal(shortTime(null, TODAY), '');
  assert.equal(shortTime('garbage', TODAY), '');
});

// -- totals ---------------------------------------------------------------------------------------

test('a null total says how many inputs are missing; day-only gaps count for day profit only', () => {
  const unavailable = [
    { ticker: 'HKG:00700', reason: '数据源维护' },
    { ticker: 'HKG:00700', reason: '另一个原因' },
    { ticker: 'SHA:600519', reason: '数据源维护' },
    { ticker: 'NASDAQ:X', reason: '行情尚未更新到最新交易日', field: 'day_pnl' },
    { currency: 'USD', reason: '汇率不可用' },
  ];
  assert.equal(missingSummary(unavailable, 'value'), '2 项缺价、1 种货币缺汇率');
  assert.equal(missingSummary(unavailable, 'day_pnl'), '2 项缺价、1 项无法计算当日盈亏、1 种货币缺汇率');
  assert.equal(missingSummary([], 'value'), null);
  assert.equal(missingSummary(undefined), null);
  // A ticker missing its price is not counted twice.
  assert.equal(missingSummary([{ ticker: 'A', reason: 'x' }, { ticker: 'A', reason: 'y', field: 'day_pnl' }], 'day_pnl'), '1 项缺价');
});

test('totals view takes every figure from the backend and flags estimates', () => {
  const data = payload([account('A', [row('SHA:1'), row('HKG:00700', { name: '示例控股', currency: 'HKD' })])], {
    totals: totals({
      total_cny: null, total_pnl_cny: null, cumulative_pnl_cny: null, total_pnl_pct: '5.00',
      unavailable: [{ ticker: 'HKG:00700', reason: '数据源维护' }],
      quality: 'estimated', estimated: [{ ticker: 'HKG:00700', status: 'stale', reason: '行情未经核实为最新价格' }, { currency: 'USD', status: 'time_unknown', reason: '汇率未经核实为最新' }],
      by_currency: { USD: { market_value: '0', cash: '949.00' }, CNY: { market_value: '1200.00', cash: '1000.00' }, HKD: { market_value: null, cash: '0.00' } },
    }),
  });
  const view = totalsView(data);
  assert.deepEqual(view.total, { value: null, pct: null, pctUnavailable: false, missing: '1 项缺价', estimated: false });
  assert.deepEqual(view.day, { value: '50.00', pct: '2.33', pctUnavailable: false, missing: null, estimated: true });
  // No percentage next to a figure that is unavailable.
  assert.deepEqual(view.holding, { value: null, pct: null, pctUnavailable: false, missing: '1 项缺价', estimated: false });
  assert.equal(view.estimated, true);
  assert.deepEqual(view.estimates.map((item) => [item.subject, item.status]), [['示例控股 HKG:00700', '已过期'], ['美元汇率', '时间未知']]);
  assert.deepEqual(view.cash, [['CNY', '1000.00'], ['USD', '949.00']]);
});

test('with total assets unknown, each figure that survives carries its own estimate mark', () => {
  // The reviewer's backend case: a stale CNY holding and USD cash without a rate.
  const data = payload([account('A', [row('SHA:1', { estimated: true, estimated_reason: '行情未经核实为最新价格', quote: quote('stale') })])], {
    totals: totals({
      total_cny: null, day_pnl_cny: '100.00', day_pnl_pct: null, total_pnl_cny: '200.00', total_pnl_pct: '25.00',
      cumulative_pnl_cny: null, cumulative_pnl_pct: null, quality: 'estimated',
      unavailable: [{ currency: 'USD', reason: '汇率不可用' }],
      estimated: [{ ticker: 'SHA:1', status: 'stale', reason: '行情未经核实为最新价格' }],
    }),
  });
  const view = totalsView(data);
  assert.equal(view.total.value, null);
  assert.equal(view.total.estimated, false, 'an unknown figure is 不可用, not estimated');
  assert.deepEqual([view.day.value, view.day.estimated], ['100.00', true]);
  assert.deepEqual([view.holding.value, view.holding.estimated], ['200.00', true]);
  assert.deepEqual([view.cumulative.value, view.cumulative.estimated], [null, false]);
  // A known figure whose percentage the backend could not compute says so, instead of dropping it.
  assert.equal(view.day.pctUnavailable, true);
  assert.equal(view.holding.pctUnavailable, false);
  assert.equal(view.cumulative.pctUnavailable, false, 'the figure itself is 不可用');
  assert.equal(view.total.pctUnavailable, false, 'total assets have no percentage');
  // Complete totals carry no mark at all.
  const complete = totalsView(payload([account('A', [row('SHA:1')])]));
  assert.ok(!complete.total.estimated && !complete.day.estimated && !complete.holding.estimated && !complete.cumulative.estimated);
});

test('cash balances: CNY first, zero foreign balances left out, nothing summed', () => {
  assert.deepEqual(cashBalances({ by_currency: { USD: { cash: '1.00' }, HKD: { cash: '5000.00' }, CNY: { cash: '0.00' } } }), [['CNY', '0.00'], ['HKD', '5000.00'], ['USD', '1.00']]);
  assert.deepEqual(cashBalances(undefined), []);
  assert.ok(isZeroAmount('0') && isZeroAmount('-0.00') && isZeroAmount('0.') && !isZeroAmount('0.01') && !isZeroAmount('') && !isZeroAmount(null));
  assert.deepEqual(estimateItems([{ currency: 'EUR', status: 'stale', reason: 'r' }])[0].subject, 'EUR汇率');
});

// -- rows -----------------------------------------------------------------------------------------

test('rows: positions, then today\'s sold-out rows, then cash per currency and the fund total, per account', () => {
  const data = payload([
    account('长期', [row('SHA:1'), row('SHA:2')], {
      closed_today: [row('SHA:3', { quantity: '0' })], cash: { USD: '949.00', CNY: '1000.00', HKD: '0.00' }, fund_cny: '5000.00',
    }),
    account('研究', [row('NASDAQ:DEMO', { currency: 'USD' })], { cash: { CNY: '0.00' } }),
  ]);
  const rows = holdingRows(data);
  assert.deepEqual(rows.map((item) => [item.account, item.kind, item.ticker ?? item.name]), [
    ['长期', 'position', 'SHA:1'], ['长期', 'position', 'SHA:2'], ['长期', 'closed', 'SHA:3'],
    ['长期', 'cash', '现金 CNY'], ['长期', 'cash', '现金 USD'], ['长期', 'fund', '基金（合计）'],
    ['研究', 'position', 'NASDAQ:DEMO'], ['研究', 'cash', '现金 CNY'],
  ]);
  assert.equal(new Set(rows.map((item) => item.key)).size, rows.length, 'keys are unique');
  assert.equal(rows.find((item) => item.name === '现金 USD').amount, '949.00');
  assert.equal(rows.find((item) => item.kind === 'fund').amount, '5000.00');
  assert.deepEqual(holdingRows(undefined), []);
});

test('account summaries come from the backend, with the missing count when unknown', () => {
  const data = payload([
    account('A', [row('SHA:1'), row('HKG:00700', { currency: 'HKD', price: null })], { total_cny: null, day_pnl_cny: null, quality: 'complete', closed_today: [row('SHA:9')] }),
    account('B', [row('SHA:2')]),
  ], { totals: totals({ unavailable: [{ ticker: 'HKG:00700', reason: 'x' }] }) });
  const summaries = accountSummaries(data);
  assert.deepEqual(summaries.get('A'), {
    name: 'A', holdings: 2, closedToday: 1, totalCny: null, dayPnlCny: null, totalPnlCny: '200.00', quality: 'complete', missing: '1 项缺价',
  });
  assert.equal(summaries.get('B').missing, null);
  assert.equal(summaries.get('B').totalCny, '2200.00');
});

test('price moves flash only rows whose price changed', () => {
  const before = holdingRows(payload([account('A', [row('SHA:1', { price: '12' }), row('SHA:2', { price: '5' }), row('SHA:3', { price: null })])]));
  const after = holdingRows(payload([account('A', [row('SHA:1', { price: '12.01' }), row('SHA:2', { price: '4.99' }), row('SHA:3', { price: '1' }), row('SHA:4')])]));
  const moves = priceMoves(priceSnapshot(before), after);
  assert.deepEqual([...moves.entries()].map(([key, move]) => [key.split('\u0000')[2], move]), [['SHA:1', 'up'], ['SHA:2', 'down']]);
  assert.equal(priceMoves(priceSnapshot(after), after).size, 0);
});

test('a weight computed from an estimated total carries the estimate mark, on its row and its account', () => {
  // Review round 4: at 05:10 a mainland position showed an unqualified 50.00% against a total resting on a
  // New York close that had not settled; its account showed complete. (Weights are of the whole portfolio.)
  const plain = row('SHA:1', { weight_pct: '50.00' });
  assert.equal(weightEstimate(plain, 'complete'), null);
  assert.equal(weightEstimate(plain, 'estimated', '收盘价尚未最终确定'), '估算：收盘价尚未最终确定');
  assert.equal(weightEstimate(plain, 'estimated'), '估算：组合中有未经核实的价格或汇率');
  const own = row('SHA:1', { weight_pct: '50.00', estimated: true, estimated_reason: '行情已过期' });
  assert.equal(weightEstimate(own, 'estimated', '收盘价尚未最终确定'), '估算：行情已过期');
  assert.equal(weightEstimate(row('SHA:1', { weight_pct: null }), 'estimated'), null, '不可用 needs no mark');
  const data = payload([account('A', [plain])], { totals: totals({ quality: 'estimated' }) });
  const summary = accountSummaries(data).get('A');
  assert.equal(summary.quality, 'complete');
  assert.equal(accountWeightsEstimated(summary, data.totals.quality), true);
  assert.equal(accountWeightsEstimated(summary, 'complete'), false);
  assert.equal(accountWeightsEstimated({ ...summary, quality: 'estimated' }, 'estimated'), false, 'its own 估算 says it');
});

test('a weight is the backend\'s own digits, never rounded again', () => {
  assert.equal(weightText(row('SHA:1', { weight_pct: '12.34' })), '12.34%');
  assert.equal(weightText(row('SHA:1', { weight_pct: '57.90' })), '57.90%');
  assert.equal(weightText(row('SHA:1', { weight_pct: '0.05' })), '0.05%');
  assert.equal(weightText(row('SHA:1', { weight_pct: null })), '不可用');
});

test('the cost column names each currency the way the price column does, and says 不可用 when unknown', () => {
  const rows = holdingRows(payload([account('A', [
    row('SHA:1', { cost_price: '100' }),
    row('NASDAQ:X', { currency: 'USD', cost_price: '100' }),
    row('HKG:00700', { currency: 'HKD', cost_price: '382.4' }),
    row('SHA:2', { cost_price: null }),
    row('SHA:510300', { cost_price: '3.4567' }),
    row('SHA:3', { cost_price: '12.345679' }),
  ], { closed_today: [row('SHA:9', { quantity: '0', cost_price: null })] })]));
  const byTicker = Object.fromEntries(rows.filter((item) => item.ticker).map((item) => [item.ticker, costText(item)]));
  // Every currency carries its symbol, CNY included: the column mixes currencies.
  assert.equal(byTicker['SHA:1'], '¥100.00');
  assert.equal(byTicker['NASDAQ:X'], '$100.00');
  assert.equal(byTicker['HKG:00700'], 'HK$382.400');
  assert.notEqual(byTicker['SHA:1'], byTicker['NASDAQ:X'], 'a CNY and a USD cost of 100 read differently');
  assert.equal(byTicker['SHA:2'], '不可用');
  // The backend's digits, at least the market's.
  assert.equal(byTicker['SHA:510300'], '¥3.4567');
  assert.equal(byTicker['SHA:3'], '¥12.345679');
  // An average cost that repeats ((cost + fee) ÷ quantity): ≈ rounded to 8 places, the full value kept for the title.
  const [repeating] = holdingRows(payload([account('A', [row('SHA:4', { cost_price: '61.2166666666666666666666667' })])])).filter((item) => item.ticker);
  assert.deepEqual(costFigure(repeating), { text: '≈¥61.21666667', full: '¥61.2166666666666666666666667',
    label: '约 ¥61.21666667，完整值 ¥61.2166666666666666666666667' });
  assert.equal(costText(repeating), '≈¥61.21666667');
  // Rows without a cost of their own: sold out today, cash, funds.
  assert.equal(byTicker['SHA:9'], null);
  assert.ok(rows.filter((item) => item.kind === 'cash').every((item) => costText(item) === null));
});

// Numbers on the Today and plans pages are the backend's: no float arithmetic or rounding of money or
// percentages in their components and rules. A line that converts a figure for another purpose (sorting, a
// price-move direction) says so with an `exact-ok:` comment on it or just above it. The one rounding allowed
// is lib/format.ts `fmtLong` (more than 8 places: ≈ and 8 places, the full value kept for the title and screen
// readers): a page never writes its own "≈" or rounds to some number of places itself.
test('the Today and plans components and rules never round or recompute a backend figure', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
  const offenders = [];
  const dirs = [['components', 'today'], ['features', 'today'], ['components', 'plans'], ['features', 'plans']];
  const files = [...dirs.flatMap((dir) => readdirSync(join(root, ...dir)).map((name) => [...dir, name])), ['pages', 'review', 'Plans.tsx']];
  for (const parts of files) {
    const name = parts.join('/');
    const lines = readFileSync(join(root, ...parts), 'utf8').split('\n');
    lines.forEach((line, index) => {
      if (/^\s*(\/\/|\*)/.test(line)) return;
      const risky = /\bparseFloat\(|\.toFixed\(|\.toPrecision\(|\bNumber\((?!\.)|\bMath\.(round|floor|ceil|trunc)\(|\bdigits:\s*\d|≈|SHOWN_PLACES/.test(line);
      const justified = /exact-ok:/.test(line) || /exact-ok:/.test(lines[index - 1] ?? '');
      if (risky && !justified) offenders.push(`${name}:${index + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(offenders, []);
  // ... and the long figures on these pages go through it: Today's cost, the plan prices, distances and trades.
  const source = (...parts) => readFileSync(join(root, ...parts), 'utf8');
  const body = (text, name) => text.slice(text.indexOf(`function ${name}(`), text.indexOf('\n}\n', text.indexOf(`function ${name}(`)));
  assert.match(body(source('features', 'today', 'rows.ts'), 'costFigure'), /return fmtLong\(cost, /);
  const plans = source('features', 'plans', 'view.ts');
  assert.match(plans, /const figure = \(value: string, pick: ShownPick\) => pick\(fmtLong\(value, fmtQty\)\)/);
  for (const name of ['planPrice', 'tradeText', 'entryRange', 'planSummary', 'alertRuleText', 'distance']) {
    assert.match(body(plans, name), /fmtLong\(|figure\(/, name);
  }
});

test('mainland ETFs price with three decimals', () => {
  assert.equal(instrumentKind('SHA:510300'), 'etf');
  assert.equal(instrumentKind('SHE:159915'), 'etf');
  assert.equal(instrumentKind('SHA:600519'), 'stock');
  assert.equal(instrumentKind('HKG:02800'), 'stock');
  assert.equal(instrumentKind(null), 'stock');
});

// -- snapshot chip --------------------------------------------------------------------------------

test('the 今日快照 chip follows the close-snapshot routine', () => {
  const due = `${TODAY}T15:40:00+08:00`;
  const at = (iso) => Date.parse(iso);
  const status = (routine, alive = true) => ({ scheduler: { alive, note: '后台调度器未运行' }, routines: [{ kind: 'close_snapshot', trading_date: TODAY, due_at: due, ...routine }] });
  assert.deepEqual(snapshotChip(status({ today: null }), at(`${TODAY}T10:00:00+08:00`)), { label: '今日快照 15:40 自动生成', tone: 'neutral' });
  assert.deepEqual(snapshotChip(status({ today: null }), at(`${TODAY}T15:41:00+08:00`)), { label: '今日快照 即将生成', tone: 'neutral' });
  assert.equal(snapshotChip(status({ today: null }, false), at(`${TODAY}T10:00:00+08:00`)).tone, 'warn');
  assert.equal(snapshotChip(status({ today: { status: 'ok', finished_at: `${TODAY}T15:41:30+08:00`, summary: 'ok' } })).label, '今日快照 已完成 15:41');
  assert.equal(snapshotChip(status({ today: { status: 'running' } })).label, '今日快照 生成中');
  assert.deepEqual([snapshotChip(status({ today: { status: 'failed', summary: '取价失败' } })).tone, snapshotChip(status({ today: { status: 'failed', summary: '取价失败' } })).title], ['warn', '取价失败']);
  assert.equal(snapshotChip(status({ today: { status: 'pending', next_attempt_at: `${TODAY}T16:10:00+08:00` } })).label, '今日快照 待重试 16:10');
  // Waiting for a close that is not final yet (Hong Kong at 15:40) is not a failure: neutral, with the reason.
  assert.deepEqual(snapshotChip(status({ today: { status: 'pending', error_category: 'waiting', summary: '等待港股收盘价确定，16:10 后记入',
    next_attempt_at: `${TODAY}T16:10:00+08:00` } })), { label: '今日快照 16:10 后记入', tone: 'neutral', title: '等待港股收盘价确定，16:10 后记入' });
  assert.equal(snapshotChip(status({ today: { status: 'skipped' } })).tone, 'neutral');
  assert.equal(snapshotChip(status({ today: { status: 'unavailable' } })).tone, 'warn');
  assert.deepEqual(snapshotChip(status({ trading_date: null, due_at: null, today: null })), { label: '今日休市 · 无快照', tone: 'neutral' });
  assert.equal(snapshotChip(status({ error: '无法读取状态' })).tone, 'warn');
  assert.equal(snapshotChip({ routines: [] }), null);
  assert.equal(snapshotChip(undefined), null);
});

// -- alerts ---------------------------------------------------------------------------------------

test('today\'s fired alerts in one line each, Beijing time', () => {
  const event = {
    id: 1, ticker: 'SHA:600036', name: '招商银行', kind: 'stop_loss', threshold: '32.00', value: '31.95', price: '31.95',
    quote_source: 'eastmoney', quote_time: '2026-11-17T02:05:10Z', fired_at: '2026-11-17T02:05:12Z',
  };
  assert.deepEqual(alertLine(event), { time: '10:05', text: '招商银行 跌破止损 32，现价 31.95', source: '东财 10:05:10', sell: true });
  assert.equal(alertLine({ ...event, kind: 'target_price', threshold: '1800' }).text, '招商银行 达到目标价 1,800，现价 31.95');
  assert.equal(alertLine({ ...event, kind: 'day_change_pct', threshold: '-5', value: '-5.2' }).text, '招商银行 今日涨跌 -5.20%，达到设定的 -5.00%');
  assert.equal(alertLine({ ...event, kind: 'position_weight_max', threshold: '20', value: '23.456', name: null }).text, 'SHA:600036 仓位 23.46%，达到上限 20.00%');
  assert.equal(alertLine({ ...event, kind: 'target_price' }).sell, false);
  // 2026-11-16T17:00Z is 01:00 on the 17th in Beijing.
  const events = [event, { ...event, id: 2, fired_at: '2026-11-16T17:00:00Z' }, { ...event, id: 3, fired_at: '2026-11-16T15:59:59Z' }];
  assert.deepEqual(todaysEvents(events, TODAY).map((item) => item.id), [1, 2]);
  assert.deepEqual(todaysEvents(undefined, TODAY), []);
  assert.deepEqual(todaysEvents(events, null), []);
});

test('the monitor state in a few words, as the 提醒 page says it (monitorLine); trouble is amber', () => {
  const here = { running: true, in_process: true, demo: false, status: 'ok', lease_holder: 'this_process', role: 'active',
    message: '提醒正在运行。', last_cycle_at: null, rules_enabled: 2 };
  assert.deepEqual(monitorState(here), { text: '提醒监控 运行中', tone: 'neutral', title: '提醒正在运行。' });
  assert.equal(monitorState({ ...here, status: 'waiting' }).text, '提醒监控 运行中 · 等待开盘');
  assert.deepEqual(monitorState({ ...here, rules_skipped: 2 }), {
    text: '提醒监控 运行中 · 2 条规则暂停检查', tone: 'warn', title: '提醒正在运行。' });
  for (const status of ['paused', 'degraded', 'error']) {
    assert.equal(monitorState({ ...here, status }).tone, 'warn', status);
  }
  for (const status of ['not_started', 'stopped', 'unresponsive']) {
    assert.equal(monitorState({ ...here, status, running: false, lease_holder: null }).tone, 'warn', status);
  }
  // The demo runs no monitor: said in words, never the raw status "demo".
  const demo = monitorState({ ...here, status: 'demo', demo: true, running: false, lease_holder: null,
    message: '演示模式不运行提醒监控。' });
  assert.deepEqual(demo, { text: '提醒监控 演示模式不检查提醒', tone: 'neutral', title: '演示模式不运行提醒监控。' });
  // Another process checks: this page cannot vouch for it, as the 提醒 page says.
  assert.equal(monitorState({ ...here, lease_holder: 'another_process', in_process: false }).text, '提醒监控 另一个进程在检查');
  assert.equal(monitorState(undefined), null);
});

test('the alert list tells loading, unavailable and genuinely none apart; a failed refresh marks it outdated', () => {
  const status = { status: 'ok', rules_enabled: 2 };
  const fired = [{ id: 1, ticker: 'SHA:1', name: null, kind: 'stop_loss', threshold: '10', value: '9.9', price: '9.9', quote_source: 'fake', quote_time: null, fired_at: `${TODAY}T10:05:00+08:00` }];
  const at = Date.parse(`${TODAY}T10:30:00+08:00`);
  // The history has not answered yet: not "nothing fired".
  const pending = alertsListState({ status, events: undefined, failed: false, error: null, updatedAt: 0, today: TODAY });
  assert.equal(pending.kind, 'loading');
  assert.doesNotMatch(pending.text, /还没有触发/);
  // It failed before ever answering: 不可用, with the reason, never a claim of zero events.
  const failed = alertsListState({ status, events: undefined, failed: true, error: '上游服务错误 (503)', updatedAt: 0, today: TODAY });
  assert.equal(failed.kind, 'unavailable');
  assert.match(failed.text, /不可用/);
  assert.match(failed.text, /503/);
  assert.doesNotMatch(failed.text, /还没有触发/);
  // A real empty history.
  const none = alertsListState({ status, events: [], failed: false, error: null, updatedAt: at, today: TODAY });
  assert.deepEqual([none.kind, none.stale, none.text], ['empty', false, '今天还没有触发提醒（已启用 2 条规则）。']);
  assert.equal(alertsListState({ status: { status: 'no_rules', rules_enabled: 0 }, events: [], failed: false, error: null, updatedAt: at, today: TODAY }).text,
    '设置止损、目标价等规则后，当天触发的提醒会列在这里。');
  // A later refresh failed: what is shown may be outdated, and says as of when.
  const outdatedEmpty = alertsListState({ status, events: [], failed: true, error: '请求超时，请稍后重试', updatedAt: at, today: TODAY });
  assert.equal(outdatedEmpty.kind, 'empty');
  assert.equal(outdatedEmpty.stale, true);
  assert.match(outdatedEmpty.text, /截至 10:30/);
  assert.match(outdatedEmpty.staleText, /可能不是最新/);
  const outdatedList = alertsListState({ status, events: fired, failed: true, error: 'x', updatedAt: at, today: TODAY });
  assert.deepEqual([outdatedList.kind, outdatedList.stale, outdatedList.fired.length], ['list', true, 1]);
  const list = alertsListState({ status, events: fired, failed: false, error: null, updatedAt: at, today: TODAY });
  assert.deepEqual([list.kind, list.stale, list.staleText], ['list', false, null]);
});

test('until today\'s date is known from /today, the alert list is loading, never "nothing fired"', async () => {
  const status = { status: 'ok', rules_enabled: 2 };
  const at = Date.parse(`${TODAY}T10:30:00+08:00`);
  const fired = [{ id: 1, ticker: 'SHA:1', name: null, kind: 'stop_loss', threshold: '10', value: '9.9', price: '9.9', quote_source: 'fake', quote_time: null, fired_at: `${TODAY}T10:05:00+08:00` }];
  // /today is slow while the history already holds today's event.
  const pending = alertsListState({ status, events: fired, failed: false, error: null, updatedAt: at, today: null });
  assert.equal(pending.kind, 'loading');
  assert.doesNotMatch(pending.text, /还没有触发/);
  // The same for a snapshot whose time cannot be read: its date is not a date.
  const { snapshotDate } = await import('../src/features/today/session.ts');
  for (const today of [null, snapshotDate(undefined), snapshotDate(payload([], { as_of: 'not a time' })), '不可用']) {
    for (const events of [fired, []]) {
      const state = alertsListState({ status, events, failed: false, error: null, updatedAt: at, today });
      assert.equal(state.kind, 'loading', String(today));
      assert.doesNotMatch(state.text, /还没有触发/);
    }
  }
  // Once the date is known the event is listed.
  const date = snapshotDate(payload([]));
  assert.equal(date, TODAY);
  assert.equal(alertsListState({ status, events: fired, failed: false, error: null, updatedAt: at, today: date }).kind, 'list');
  // A failed history with an unknown date is still 不可用.
  assert.equal(alertsListState({ status, events: undefined, failed: true, error: 'x', updatedAt: 0, today: null }).kind, 'unavailable');
});
