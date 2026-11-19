// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The 行情 page's decisions: exact decimal arithmetic, the watchlist poll rhythm (Today's rule on the
// watched markets), change amounts that never turn an unknown price into 0, reordering, rule counts,
// the index line and the mini chart's geometry.
import assert from 'node:assert/strict';
import test from 'node:test';
import { applyPercent, compare, isDecimal, percentChange, sign, subtract, sum } from '../src/features/market/decimal.ts';
import {
  changeAmount, displayName, hasPrice, moveItem, restoreItem, ruleCountFor, ruleCounts, tickerMarket, watchPollMs,
} from '../src/features/market/watchlist.ts';
import { INDEX_STRIP, stripItems } from '../src/features/market/indices.ts';
import { RANGES, chartGeometry, nearestPoint } from '../src/features/market/chart.ts';
import { positionView } from '../src/features/market/drawer.ts';
import { searchShortcut } from '../src/features/market/searchShortcut.ts';
import { identityKey } from '../src/lib/instrument.ts';
import { POLL_IN_SESSION_MS, POLL_OUT_OF_SESSION_MS } from '../src/features/today/session.ts';

const TODAY = '2026-11-17';

function quoteObject(ticker, price, prevClose, status = 'live', overrides = {}) {
  return {
    ticker, name: null, price, prev_close: prevClose, change_pct: null, currency: 'CNY', estimated: false, estimated_reason: null,
    quote: { source: 'fake', quote_time: `${TODAY}T10:32:02+08:00`, fetched_at: `${TODAY}T10:32:05+08:00`, status, reason: null, session_date: TODAY },
    ...overrides,
  };
}

test('decimal strings: exact differences, sums, comparisons and percentages', () => {
  assert.equal(subtract('61.20', '60.80'), '0.4');
  assert.equal(subtract('0.1', '0.3'), '-0.2');
  assert.equal(subtract('60.80', '60.80'), '0');
  assert.equal(subtract(null, '1'), null);
  assert.equal(subtract('abc', '1'), null);
  assert.equal(sum(['57.9', '0.1', '2']), '60');
  assert.equal(sum(['1', null]), null);
  assert.equal(sum([]), null);
  assert.equal(compare('10', '9.99'), 1);
  assert.equal(compare('-1', '0'), -1);
  assert.equal(compare('1.50', '1.5'), 0);
  assert.equal(sign('-0.00'), 0);
  assert.equal(isDecimal('1,000'), false);
  assert.equal(isDecimal('.5'), true);
  // 61.20 → 54: -11.764…% → -11.8 (one decimal), half away from zero.
  assert.equal(percentChange('61.20', '54', 1), '-11.8');
  assert.equal(percentChange('61.20', '66', 1), '7.8');
  assert.equal(percentChange('8', '9', 2), '12.5');
  assert.equal(percentChange('0', '9'), null);
  assert.equal(applyPercent('61.20', '-8', 2), '56.3');
  assert.equal(applyPercent('61.20', '10', 2), '67.32');
  assert.equal(applyPercent('100', '-5.5', 3), '94.5');
  assert.equal(applyPercent(null, '5'), null);
});

test('the watchlist polls every 5 s while a watched market moves, every 60 s otherwise', () => {
  const open = { phase: 'morning', trading_day: TODAY, is_trading_day: true, calendar_confidence: 'calendar', next_open: null };
  const closed = { ...open, phase: 'closed' };
  const usOpen = { ...open, phase: 'open' };
  const session = { markets: { CN: open, HK: closed, US: closed } };
  assert.equal(watchPollMs(['SHA:600519'], session), POLL_IN_SESSION_MS);
  assert.equal(watchPollMs(['NASDAQ:DEMOW', 'HKG:00700'], session), POLL_OUT_OF_SESSION_MS);
  assert.equal(watchPollMs(['IDX:000300'], session), POLL_IN_SESSION_MS, 'an A-share index follows the A-share session');
  assert.equal(watchPollMs(['IDX:IXIC'], { markets: { CN: closed, HK: closed, US: usOpen } }), POLL_IN_SESSION_MS);
  assert.equal(watchPollMs([], session), POLL_OUT_OF_SESSION_MS);
  assert.equal(watchPollMs(['SHA:600519'], undefined), POLL_OUT_OF_SESSION_MS);
  assert.equal(watchPollMs(['SHA:600519'], { markets: { CN: { ...open, is_trading_day: false } } }), POLL_OUT_OF_SESSION_MS);
  assert.equal(tickerMarket('IDX:HSI'), 'HK');
  assert.equal(tickerMarket('IDX:000001'), 'CN');
  assert.equal(tickerMarket('NYSE:DEMOX'), 'US');
  assert.equal(tickerMarket('XYZ:1'), null);
});

test('change amount: price − previous close, never computed from a missing price', () => {
  assert.equal(changeAmount(quoteObject('SHA:DEMO', '61.20', '60.80')), '0.4');
  assert.equal(changeAmount(quoteObject('SHA:DEMO', '59.5', '60.80')), '-1.3');
  assert.equal(changeAmount(quoteObject('SHA:DEMO', null, '60.80', 'unavailable')), null);
  assert.equal(changeAmount(quoteObject('SHA:DEMO', '61.20', null)), null);
  assert.equal(changeAmount(null), null);
  assert.equal(hasPrice(quoteObject('SHA:DEMO', '61.2', '60', 'stale')), true, 'a stale price is shown, marked');
  assert.equal(hasPrice(quoteObject('SHA:DEMO', '61.2', '60', 'unavailable')), false);
  assert.equal(displayName({ ticker: 'NYSE:DEMOX', name: null, quote: quoteObject('NYSE:DEMOX', '1', '1', 'live', { name: '示例价值股' }) }), '示例价值股');
  assert.equal(displayName({ ticker: 'NYSE:DEMOX', name: '自选名', quote: null }), '自选名');
  assert.equal(displayName({ ticker: 'NYSE:DEMOX', name: null, quote: null }), 'NYSE:DEMOX');
});

test('reordering moves one place and refuses the ends; undo puts an item back where it was', () => {
  const items = ['A', 'B', 'C'].map((ticker) => ({ ticker }));
  assert.deepEqual(moveItem(items, 'B', -1), { items: [{ ticker: 'B' }, { ticker: 'A' }, { ticker: 'C' }], order: 0 });
  assert.deepEqual(moveItem(items, 'B', 1)?.order, 2);
  assert.equal(moveItem(items, 'A', -1), null);
  assert.equal(moveItem(items, 'C', 1), null);
  assert.equal(moveItem(items, 'Z', 1), null);
  assert.deepEqual(restoreItem([{ ticker: 'A' }, { ticker: 'C' }], { ticker: 'B' }, 1).map((item) => item.ticker), ['A', 'B', 'C']);
  assert.deepEqual(restoreItem([{ ticker: 'A' }], { ticker: 'B' }, 9).map((item) => item.ticker), ['A', 'B']);
});

test('rule counts per ticker merge Hong Kong spellings', () => {
  const counts = ruleCounts([
    { ticker: 'SHA:DEMO', enabled: true }, { ticker: 'SHA:DEMO', enabled: false }, { ticker: 'HKG:0700', enabled: true },
    { ticker: 'HKG:00700', enabled: true },
  ]);
  assert.deepEqual(counts.get('SHA:DEMO'), { total: 2, enabled: 1 });
  assert.deepEqual(counts.get(identityKey('HKG:00700')), { total: 2, enabled: 2 });
  assert.equal(counts.get('SHE:000001'), undefined);
  assert.equal(ruleCounts(undefined).size, 0);
});

test('the index line: strip order, 不可用 for anything without a price, a state word when not live', () => {
  const items = stripItems([
    quoteObject('IDX:000001', '3312.45', '3295.3', 'live', { change_pct: '0.52' }),
    quoteObject('IDX:HSI', '19880', '19820', 'stale', { quote: { source: 'fake', quote_time: `${TODAY}T10:00:00+08:00`, status: 'stale', reason: null } }),
    quoteObject('IDX:IXIC', '18000', '18100', 'closed', { quote: { source: 'fake', quote_time: '2026-11-17T05:00:00+08:00', status: 'closed', reason: null } }),
    quoteObject('IDX:399001', null, null, 'unavailable', { quote: { source: 'fake', status: 'unavailable', reason: '停牌' } }),
  ], TODAY);
  assert.deepEqual(items.map((item) => item.label), INDEX_STRIP.map((entry) => entry.label));
  assert.deepEqual(
    { ...items[0], meta: undefined },
    { ticker: 'IDX:000001', label: '上证', price: '3312.45', changePct: '0.52', state: null, warn: false, title: undefined, meta: undefined },
  );
  assert.equal(items[1].price, null);
  assert.equal(items[1].title, '停牌');
  assert.equal(items[2].price, null, 'missing from the answer: unavailable');
  assert.equal(items[3].state, '已过期');
  assert.equal(items[3].warn, true);
  assert.equal(items[4].state, '收盘');
});

test('the mini chart maps closes into the box, marks the high, low and last bar', () => {
  const bars = [10, 12, 11, 9, 13].map((close, index) => ({ date: `2026-11-${10 + index}`, open: close, high: close, low: close, close, volume: 1 }));
  const geometry = chartGeometry(bars, { width: 400, height: 100, padTop: 10, padBottom: 10 });
  assert.equal(geometry.points.length, 5);
  assert.equal(geometry.first.x, 0);
  assert.equal(geometry.last.x, 400);
  assert.equal(geometry.high.value, 13);
  assert.equal(geometry.high.y, 10);
  assert.equal(geometry.low.value, 9);
  assert.equal(geometry.low.y, 90);
  assert.ok(geometry.line.startsWith('M0 '));
  assert.ok(geometry.area.endsWith('Z'));
  assert.equal(geometry.changePct, '30');
  assert.equal(nearestPoint(geometry, 190).date, '2026-11-12');
  assert.equal(chartGeometry(bars.slice(0, 1), { width: 10, height: 10 }), null);
  const flat = chartGeometry([{ ...bars[0] }, { ...bars[0], date: 'x' }], { width: 10, height: 10 });
  assert.ok(Number.isFinite(flat.first.y), 'a flat line stays inside the box');
  const gaps = chartGeometry([{ ...bars[0] }, { ...bars[1], close: null }, { ...bars[2] }], { width: 10, height: 10 });
  assert.equal(gaps.points.length, 2);
  assert.deepEqual(RANGES.map((range) => range.label), ['1个月', '3个月', '6个月']);
});

// -- review r3 ----------------------------------------------------------------------------------------

test('decimal strings are parsed as written: no exponent forms, no float round trip, bounded length', () => {
  for (const text of ['1e21', '1E-13', '1e-13', '-2.5e3', 'Infinity', 'NaN', '1'.repeat(80)]) {
    assert.equal(isDecimal(text), false, text);
    assert.equal(subtract(text, '0'), null, text);
    assert.equal(compare(text, '0'), null, text);
  }
  assert.equal(isDecimal(1e21), false, 'a number that prints with an exponent is not read');
  assert.equal(subtract('0.0000000000001', '0'), '0.0000000000001');
  assert.equal(percentChange('200', '200.01', 2), '0.01');
});

test('the chart return is exact, and unavailable from a zero first close', () => {
  const bar = (date, close) => ({ date, open: close, high: close, low: close, close, volume: 1 });
  const box = { width: 100, height: 50 };
  assert.equal(chartGeometry([bar('2026-11-16', 200), bar('2026-11-17', 200.01)], box).changePct, '0.01');
  assert.equal(chartGeometry([bar('2026-11-16', 0.1), bar('2026-11-17', 0.3)], box).changePct, '200');
  assert.equal(chartGeometry([bar('2026-11-16', 0), bar('2026-11-17', 5)], box).changePct, null, 'not 0.00%');
});

test('reordering keeps each item\'s order, so undoing a removal after a move restores what was on screen', () => {
  const items = [{ ticker: 'A', order: 0 }, { ticker: 'B', order: 1 }];
  const moved = moveItem(items, 'B', -1);
  assert.deepEqual(moved.items, [{ ticker: 'B', order: 0 }, { ticker: 'A', order: 1 }]);
  const removed = moved.items[0];
  const left = moved.items.filter((item) => item.ticker !== 'B');
  assert.deepEqual(restoreItem(left, removed, removed.order).map((item) => [item.ticker, item.order]), [['B', 0], ['A', 1]]);
});

test('rule counts go by the listing identity (the payload\'s when it has one); unknown counts are null', () => {
  const counts = ruleCounts([
    { ticker: 'NYSE:BRK.B', enabled: true, identity: 'NYSE:BRK.B' }, { ticker: 'NYSE:BRK-B', enabled: true },
    { ticker: 'SHA:DEMO', enabled: false },
  ]);
  assert.deepEqual(ruleCountFor(counts, { ticker: 'NYSE:BRK-B', identity: 'NYSE:BRK.B' }), 2);
  assert.equal(ruleCountFor(counts, { ticker: 'SHA:DEMO' }), 0, 'disabled rules are not counted');
  assert.equal(ruleCountFor(counts, { ticker: 'SHE:000001' }), 0);
  assert.equal(ruleCountFor(null, { ticker: 'SHA:DEMO' }), null, 'the rules request failed: not 0');
});

test('an index close from a weekday-only calendar keeps its amber estimate; each index says its source and time', () => {
  const [first] = stripItems([
    quoteObject('IDX:000001', '3312.45', '3295.3', 'closed', {
      estimated: true, estimated_reason: '交易日历只按工作日判断，收盘价未核实',
      quote: { source: 'eastmoney', quote_time: `${TODAY}T15:00:00+08:00`, fetched_at: `${TODAY}T15:01:00+08:00`, status: 'closed', reason: null, session_date: TODAY },
    }),
  ], TODAY);
  assert.equal(first.warn, true);
  assert.match(first.title, /未核实/);
  assert.match(first.meta, /15:00/);
  const [live] = stripItems([quoteObject('IDX:000001', '3312.45', '3295.3', 'live')], TODAY);
  assert.match(live.meta, /10:32:02/);
});

test('the drawer shows a holding with the backend digits and marks an estimated weight', () => {
  const row = {
    ticker: 'SHA:DEMO', name: '示例宽基', currency: 'CNY', quantity: '1000', cost_price: '12.345679', price: '61.2', prev_close: '60.8',
    change_pct: '0.66', market_value: '61200', market_value_cny: '61200', day_pnl: '400', day_pnl_cny: '400', total_pnl: '48854.32',
    total_pnl_cny: '48854.32', total_pnl_pct: '395.72', weight_pct: '57.93',
    quote: { source: 'fake', quote_time: `${TODAY}T10:32:02+08:00`, fetched_at: null, status: 'live', reason: null, session_date: TODAY },
    estimated: false, estimated_reason: null,
  };
  const plain = positionView(row, 'complete');
  assert.deepEqual(plain.cost, { text: '12.345679' });
  // An average cost that repeats ((cost + fees) ÷ quantity): ≈ rounded to 8 places, the full value on hover.
  assert.deepEqual(positionView({ ...row, cost_price: '61.2166666666666666666666667' }, 'complete').cost,
    { text: '≈61.21666667', full: '61.2166666666666666666666667', label: '约 61.21666667，完整值 61.2166666666666666666666667' });
  assert.equal(plain.weight, '57.93%');
  assert.equal(plain.weightEstimated, false);
  const fx = positionView(row, 'estimated', '汇率 USD：已过期');
  assert.equal(fx.weightEstimated, true, 'the total behind the weight is estimated');
  assert.match(fx.weightTitle, /USD/);
  assert.equal(positionView({ ...row, estimated: true, estimated_reason: '行情已过期' }, 'complete').weightEstimated, true);
  assert.equal(positionView({ ...row, weight_pct: null, cost_price: null }, 'complete').weight, '不可用');
  assert.deepEqual(positionView({ ...row, weight_pct: null, cost_price: null }, 'complete').cost, { text: '不可用' });
});

test('⌘K and / never take the focus from a field being typed in or an input method composing', () => {
  const key = (overrides) => ({ key: 'k', ctrlKey: true, metaKey: false, altKey: false, isComposing: false, keyCode: 75, target: null, ...overrides });
  const field = (tagName, extra = {}) => ({ tagName, isContentEditable: false, type: 'text', ...extra });
  assert.equal(searchShortcut(key({})), 'focus');
  assert.equal(searchShortcut(key({ target: field('INPUT') })), 'block', 'swallowed: no focus change, no browser shortcut, no blur-save');
  assert.equal(searchShortcut(key({ target: field('TEXTAREA') })), 'block');
  assert.equal(searchShortcut(key({ target: field('DIV', { isContentEditable: true }) })), 'block');
  assert.equal(searchShortcut(key({ target: field('INPUT', { type: 'checkbox' }) })), 'focus', 'a checkbox is not typed in');
  assert.equal(searchShortcut(key({ isComposing: true })), 'ignore');
  assert.equal(searchShortcut(key({ keyCode: 229 })), 'ignore');
  assert.equal(searchShortcut(key({ key: '/', ctrlKey: false, keyCode: 191 })), 'focus');
  assert.equal(searchShortcut(key({ key: '/', ctrlKey: false, target: field('INPUT') })), 'ignore');
  assert.equal(searchShortcut(key({ key: '/', ctrlKey: false, isComposing: true })), 'ignore');
  assert.equal(searchShortcut(key({ key: 'j' })), 'ignore');
});

// A quote is shown with the digits the backend sent: a $0.8437 holding against a $0.84 close is "$0.8437" and
// "+0.0037", never "$0.84" and "+0.00" (sub-dollar listings and Hong Kong penny stocks carry 3-4 places). The
// market's digits are a minimum (`exact`), not a rounding; a fixed `digits: 2` on a price is the same defect.
test('market pages and the stock drawer show a quote\'s price, change and close with every digit sent', async () => {
  const { readdirSync, readFileSync } = await import('node:fs');
  const { dirname, join, relative } = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.tsx?$/.test(entry.name)) files.push(path);
    }
  };
  for (const dir of ['components/market', 'pages/market', 'features/market']) walk(join(src, dir));
  const offenders = [];
  for (const file of files) {
    readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
      const where = `${relative(src, file)}:${index + 1}: ${line.trim()}`;
      // The definition itself, and figures the page computes (the chart's axis labels and its range return,
      // `geometry.*`), are not a quote.
      if (/export function fmtTickerPrice|exact-ok:/.test(line)) return;
      if (/fmtPct\(geometry\.changePct/.test(line)) return;
      for (const call of line.matchAll(/fmtTickerPrice\(([^()]|\([^()]*\))*\)/g)) {
        if (!/exact:\s*true/.test(call[0])) offenders.push(`${where} (fmtTickerPrice without exact)`);
      }
      if (/fmtNumber\([^)]*price[^)]*digits:\s*\d/.test(line) && !/exact:\s*true/.test(line)) offenders.push(`${where} (a price at fixed digits)`);
      if (/fmtPct\([^)]*change_?[pP]ct[^)]*\)/.test(line) && !/exact:\s*true/.test(line)) offenders.push(`${where} (a change rounded again)`);
    });
  }
  assert.deepEqual(offenders, []);
});
