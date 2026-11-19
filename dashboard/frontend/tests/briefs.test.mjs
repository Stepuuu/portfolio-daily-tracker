// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The 今日简报 page's decisions: which state a column is in, how a fact's value, chip and source line
// read, where references lead, when to ask again. Payloads follow backend/api/briefs.py.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  KINDS, POLL_BUSY_MS, POLL_TODAY_MS, calendarMark, columnState, dateLabel, demoJump, factChips, factDetail, factMeta,
  factValue, itemText, pollInterval, qualityNote, refTarget, timeLabel,
} from '../src/features/briefs/view.ts';

const DAY = '2026-11-17';
const GENERATED = '2026-11-17T15:45:00+08:00';

function fact(overrides = {}) {
  return {
    label: '示例', value: '1.00', unit: 'pct', text: '示例文字', source: 'fake', as_of: GENERATED, status: null,
    ref: null, metric: 'distance_pct', ...overrides,
  };
}

function item(state, overrides = {}) {
  return {
    kind: 'premarket', title: '盘前简报', trading_date: DAY, enabled: true, time: '08:45',
    due_at: `${DAY}T08:45:00+08:00`, state, state_text: state, reason: null, run: null, brief: null, can_run: false,
    run_refusal: null, ...overrides,
  };
}

function brief(overrides = {}) {
  return {
    kind: 'premarket', title: '盘前简报', trading_date: DAY, generated_at: '2026-11-17T08:45:12+08:00', sections: [],
    quality: { level: 'complete', estimated: 0, unavailable: 0, text: '所有数字都有来源且已核实' }, notices: [],
    commentary: null, version: 1, ...overrides,
  };
}

function day(pre, post, overrides = {}) {
  return {
    date: DAY, current_trading_date: DAY, is_trading_day: true, previous_date: '2026-11-16', next_date: null,
    latest_date: null, now: `${DAY}T16:00:00+08:00`, mode: 'normal', scheduler_alive: true,
    items: { premarket: pre, postmarket: post }, ...overrides,
  };
}

test('dates and times read in Beijing time', () => {
  assert.equal(dateLabel('2026-11-17'), '2026-11-17 周二');
  assert.equal(dateLabel('2026-11-22'), '2026-11-22 周日');
  assert.equal(dateLabel('bad'), 'bad');
  assert.equal(timeLabel('2026-11-17T08:45:12+08:00', DAY), '08:45');
  assert.equal(timeLabel('2026-11-17T00:45:12Z', DAY, true), '08:45:12');
  assert.equal(timeLabel('2026-11-16T15:00:00+08:00', DAY), '11-16 15:00');
  assert.equal(timeLabel(null, DAY), '');
  assert.deepEqual(KINDS, ['premarket', 'postmarket']);
});

test('each state of a column says what is going on and offers the right action', () => {
  const generated = columnState(item('ok', { brief: brief() }), false);
  assert.deepEqual(generated, { status: '08:45 生成', tone: 'neutral', reason: null, showBrief: true, action: null, actionBlocked: null });
  assert.equal(columnState(item('scheduled'), false).status, '尚未生成 · 08:45 自动生成');
  assert.equal(columnState(item('running'), false).tone, 'busy');
  assert.equal(columnState(item('pending'), false).status, '等待生成');
  const failed = columnState(item('failed', { reason: '行情全部不可用', can_run: true, brief: brief() }), false);
  assert.deepEqual([failed.status, failed.tone, failed.reason, failed.action, failed.actionBlocked, failed.showBrief],
    ['失败', 'warn', '行情全部不可用', 'rerun', null, true]);
  const blocked = columnState(item('failed', { reason: 'x', can_run: false, run_refusal: '后台调度器未运行，不能生成。' }), false);
  assert.deepEqual([blocked.action, blocked.actionBlocked], [null, '后台调度器未运行，不能生成。'], 'offered only when can_run');
  const due = columnState(item('due', { reason: '已到 08:45，等待后台调度器生成。', can_run: true }), false);
  assert.deepEqual([due.status, due.action], ['尚未生成', 'run']);
  const demo = columnState(item('demo', { brief: brief() }), true);
  assert.deepEqual([demo.status, demo.action], ['演示数据 · 08:45 生成', null]);
  assert.equal(columnState(item('failed', { reason: 'x', can_run: false }), true).action, null, 'the demo offers no run');
  assert.equal(columnState(item('not_trading_day', { state_text: '非交易日' }), false).status, '非交易日');
  const off = columnState(item('skipped', { state_text: '已跳过', reason: '已关闭（TRACKER_PREMARKET=0）', can_run: false,
    run_refusal: '盘前简报已关闭（TRACKER_PREMARKET=0），不能生成。' }), false);
  assert.deepEqual([off.action, off.actionBlocked], [null, '盘前简报已关闭（TRACKER_PREMARKET=0），不能生成。']);
  assert.equal(columnState(item('skipped', { state_text: '已跳过', can_run: true }), false).action, 'rerun');
});

test('values: percentages, money and counts are formatted, never computed or rounded; null is 不可用', () => {
  assert.deepEqual(factValue(fact({ value: '0.52', signed: true })), { text: '+0.52%', tone: 'up', missing: false });
  assert.deepEqual(factValue(fact({ value: '-2.22' })), { text: '-2.22%', tone: null, missing: false });
  assert.deepEqual(factValue(fact({ value: '-55.00', unit: 'CNY', signed: true })), { text: '-¥55.00', tone: 'down', missing: false });
  assert.equal(factValue(fact({ value: '16500.00', unit: 'CNY' })).text, '¥16,500.00');
  assert.equal(factValue(fact({ value: '10.00', unit: 'USD', signed: true })).text, '+$10.00');
  assert.equal(factValue(fact({ value: 4, unit: 'count', text: '4 笔（买入 3，卖出 1）' })).text, '4');
  assert.equal(factValue(fact({ value: 2, unit: 'count', text: '成交 4 笔：偏离计划 2 笔，按计划 1 笔，未核对 1 笔' })).text, '2');
  assert.equal(factValue(fact({ value: 1, unit: 'count', text: '买入价 62 高于计划入场区间 58–61。' })).text, '1');
  assert.equal(factValue(fact({ value: 1, unit: 'count', text: '示例研究标的' })).text, '1');
  assert.equal(factValue(fact({ value: 12, unit: 'count', text: '1 笔' })).text, '12', 'the phrase must be the count');
  assert.equal(factValue(fact({ value: 0, unit: 'count', text: '没有到期的复查' })).text, '0', 'a zero stays 0');
  assert.equal(factValue(fact({ value: 0, unit: 'count', text: '今日 3 项' })).text, '0');
  assert.equal(factValue(fact({ value: '2026-11-10', unit: 'date' })).text, '2026-11-10');
  assert.deepEqual(factValue(fact({ value: null, text: '不可用：缺少 USD 汇率' })), { text: '不可用', tone: null, missing: true });
  const notAsked = fact({ value: null, unit: 'count', text: '未查询：只支持 A 股', status: { code: 'not_queried', reason: '只支持 A 股', estimated: false } });
  assert.equal(factValue(notAsked).text, '不可用', 'the value position never holds a sentence');
  assert.equal(factValue(fact({ value: '10.005', unit: 'HKD' })).text, 'HK$10.005', 'the backend digits, not re-rounded');
  assert.equal(factValue(fact({ value: '54.004', unit: 'HKD', signed: true })).text, '+HK$54.004');
  assert.equal(factValue(fact({ value: '-2.226', unit: 'pct' })).text, '-2.226%');
  assert.equal(factValue(fact({ value: '0.00', signed: true })).tone, 'flat');
  // Past 8 places (a ratio that repeats): ≈ rounded to 8, the full value kept for the title and screen readers.
  assert.deepEqual(factValue(fact({ value: '33.3333333333333', unit: 'pct' })),
    { text: '≈33.33333333%', full: '33.3333333333333%', label: '约 33.33333333%，完整值 33.3333333333333%', tone: null, missing: false });
});

test('the detail line is the backend sentence; for a null value it is the reason', () => {
  assert.equal(factDetail(fact({ text: '11-16 收盘价 10.50，距止损还有 6.67%' })), '11-16 收盘价 10.50，距止损还有 6.67%');
  assert.equal(factDetail(fact({ value: null, text: '不可用：fake：fake_quotes.json 中没有该代码' })), 'fake：fake_quotes.json 中没有该代码');
  assert.equal(factDetail(fact({ value: null, text: '未查询：公告与新闻数据源只支持 A 股' })), '未查询：公告与新闻数据源只支持 A 股');
  assert.equal(factDetail(fact({ value: 3, unit: 'count', text: '3 项' })), null, 'the value already says it');
  assert.equal(factDetail(fact({ value: 0, unit: 'count', text: '没有到期的复查' })), '没有到期的复查', 'the sentence moves here');
  assert.equal(factDetail(fact({ value: 4, unit: 'count', text: '4 笔（买入 3，卖出 1）' })), '4 笔（买入 3，卖出 1）');
  assert.equal(factDetail(fact({ value: 2, unit: 'count', text: '成交 4 笔：偏离计划 2 笔' })), '成交 4 笔：偏离计划 2 笔');
  const group = fact({ value: 1, unit: 'count', text: '买入价 62 高于计划入场区间 58–61。',
    items: [{ text: '买入 示例宽基 100 股 @ 62.00：买入价 62 高于计划入场区间 58–61。' }] });
  assert.equal(factDetail(group), null, 'the trade row says it');
  const names = fact({ value: 2, unit: 'count', text: '示例一号、示例二号',
    items: [{ text: '示例一号（SHA:600001）' }, { text: '示例二号（SHA:600002）' }] });
  assert.equal(factDetail(names), null);
  const unchecked = fact({ value: 1, unit: 'count', text: '确认这些成交时还没有任何交易计划', items: [{ text: '卖出 示例港股 50 股 @ 51.50' }] });
  assert.equal(factDetail(unchecked), '确认这些成交时还没有任何交易计划');
});

test('chips mark what is not a verified figure, each mark on its own, with the reason for hover and focus', () => {
  const labels = (status, more = {}) => factChips(fact({ status, ...more }), DAY).map((chip) => chip.label);
  assert.deepEqual(labels({ code: 'closed', reason: null, estimated: false }), []);
  assert.deepEqual(labels({ code: 'live', reason: null, estimated: false }), []);
  const [stale, staleEstimate] = factChips(fact({ as_of: '2026-11-17T14:10:00+08:00', status: { code: 'stale', reason: '行情停在 14:10', estimated: true } }), DAY);
  assert.deepEqual(stale, { label: '已过期 14:10', tone: 'warn', title: '行情停在 14:10' });
  assert.deepEqual(staleEstimate, { label: '估算', tone: 'warn', title: '行情停在 14:10' });
  const [close] = factChips(fact({ as_of: '2026-11-16T16:08:00+08:00', status: { code: 'closed', reason: '未核实', estimated: true } }), DAY);
  assert.deepEqual(close, { label: '收盘价 11-16 16:08', tone: 'warn', title: '未核实' });
  assert.deepEqual(factChips(fact({ status: { code: 'estimated', reason: '汇率 USD：已过期', estimated: true } }), DAY),
    [{ label: '估算', tone: 'warn', title: '汇率 USD：已过期' }]);
  assert.deepEqual(labels({ code: 'delayed', reason: '约 3 分钟', estimated: false }), ['延迟']);
  // A live Hong Kong price converted at a stale rate: the quote needs no chip, the estimate still shows.
  assert.deepEqual(factChips(fact({ unit: 'CNY', status: { code: 'live', reason: '汇率未经核实为最新', estimated: true } }), DAY),
    [{ label: '估算', tone: 'warn', title: '汇率未经核实为最新' }]);
  // An intraday price of a session still trading: its own amber mark, estimated or not.
  assert.deepEqual(labels({ code: 'live', reason: '香港 11-17 盘中价 15:44（非收盘价，16:00 收盘）', estimated: false, provisional: true }), ['盘中价']);
  assert.deepEqual(labels({ code: 'estimated', reason: '含港股盘中价', estimated: true, provisional: true }), ['估算', '盘中价']);
  assert.deepEqual(labels({ code: 'provisional', reason: '含港股盘中价', estimated: false, provisional: true }), ['盘中价']);
  assert.deepEqual(factChips(fact({ value: null, text: '未查询：x', status: { code: 'not_queried', reason: 'x', estimated: false } }), DAY),
    [{ label: '未查询', tone: 'warn', title: 'x' }]);
});

test('the quiet source line: quote source and its own time; the brief time is not repeated', () => {
  assert.equal(factMeta(fact({ as_of: '2026-11-17T15:00:00+08:00' }), GENERATED, DAY), '模拟行情 15:00');
  assert.equal(factMeta(fact({ source: 'eastmoney', as_of: '2026-11-16T15:00:00+08:00' }), GENERATED, DAY), '东财 11-16 15:00');
  assert.equal(factMeta(fact({ source: '账本（版本 7）' }), GENERATED, DAY), '账本（版本 7）');
  assert.equal(factMeta(fact({ source: null, as_of: GENERATED }), GENERATED, DAY), '');
  const converted = fact({ source: 'fake', as_of: '2026-11-17T05:00:00+08:00', unit: 'CNY',
    fx: { currency: 'USD', rate: '7.1', source: 'fake', as_of: '2026-11-17T15:44:55+08:00', status: 'live', reason: null, estimated: false } });
  assert.equal(factMeta(converted, GENERATED, DAY), '模拟行情 05:00 · 汇率 USD 7.1 模拟行情 15:44');
});

test('references become links only to routes that exist', () => {
  const routes = new Set(['/', '/market', '/plans', '/ledger']);
  assert.deepEqual(refTarget({ plan_id: 2, ticker: 'SHA:DEMO' }, routes), { href: '/plans?plan=2', label: '查看计划 #2' });
  assert.equal(refTarget({ plan_id: 2 }, new Set(['/'])), null);
  assert.equal(refTarget({ alert_event_id: 7, alert_rule_id: 3 }, routes), null, 'no /alerts page in this build');
  assert.deepEqual(refTarget({ alert_event_id: 7 }, new Set([...routes, '/alerts'])), { href: '/alerts', label: '查看提醒' });
  assert.deepEqual(refTarget({ ticker: 'SHA:DEMO' }, routes, 'day_pnl_cny'), { href: '/', label: '在今日页查看' });
  assert.deepEqual(refTarget({ ticker: 'IDX:000001' }, routes, 'index_change_pct'), { href: '/market', label: '在行情页查看' });
  assert.equal(refTarget({ journal_id: 'j1' }, routes), null);
  assert.equal(refTarget(null, routes), null);
});

test('headlines are third-party text shown as such; other sub-rows as written', () => {
  assert.deepEqual(itemText({ title: { untrusted_text: '示例公告' }, published: '2026-11-16' }),
    { text: '示例公告', thirdParty: true, when: '2026-11-16' });
  assert.deepEqual(itemText({ text: '买入 示例二号：买入价 11 高于计划入场区间', level: 'warning' }),
    { text: '买入 示例二号：买入价 11 高于计划入场区间', thirdParty: false, when: null });
});

test('ask again every 2 s while generating, once a minute while today is still to come, else never', () => {
  assert.equal(pollInterval(undefined), false);
  assert.equal(pollInterval(day(item('running'), item('scheduled'))), POLL_BUSY_MS);
  assert.equal(pollInterval(day(item('ok'), item('scheduled'))), POLL_TODAY_MS);
  assert.equal(pollInterval(day(item('ok'), item('ok'))), false);
  assert.equal(pollInterval(day(item('missing'), item('scheduled'), { date: '2026-11-16' })), false);
  // Pre-market done, post-market failed with a retry at 16:00: keep asking, or the page shows the old failure.
  const retrying = item('failed', { kind: 'postmarket', run: { status: 'failed', next_attempt_at: `${DAY}T16:00:00+08:00` } });
  assert.equal(pollInterval(day(item('ok'), retrying)), POLL_TODAY_MS);
  assert.equal(pollInterval(day(item('ok'), retrying, { date: '2026-11-16', current_trading_date: DAY })), POLL_TODAY_MS);
  const final = item('failed', { kind: 'postmarket', run: { status: 'failed', next_attempt_at: null } });
  assert.equal(pollInterval(day(item('ok'), final)), false);
});

test('a day whose calendar is only the weekday fallback is marked', () => {
  const unknown = day(item('scheduled'), item('scheduled'), { calendar: { '2027-01-04': 'weekday_only', '2026-12-31': 'calendar' } });
  assert.equal(calendarMark(unknown, '2027-01-04'), '交易日历未核实（按工作日推算）');
  assert.equal(calendarMark(unknown, '2026-12-31'), null);
  assert.equal(calendarMark(day(item('ok'), item('ok')), DAY), null, 'no calendar map: nothing to say');
});

test('the demo opens on its newest brief when today has none; elsewhere the page stays put', () => {
  const empty = day(item('demo'), item('demo'), { mode: 'demo', latest_date: '2026-11-16' });
  assert.equal(demoJump(empty, null), '2026-11-16');
  assert.equal(demoJump(empty, '2026-11-17'), null, 'a date the person chose');
  assert.equal(demoJump({ ...empty, mode: 'normal' }, null), null);
  assert.equal(demoJump(day(item('demo', { brief: brief() }), item('demo'), { mode: 'demo', latest_date: DAY }), null), null);
});

test('the quality note names what is unknown or estimated', () => {
  assert.equal(qualityNote(brief()), null);
  assert.equal(qualityNote(brief({ quality: { level: 'incomplete', estimated: 1, unavailable: 3, text: '3 项不可用；1 项为估算' } })),
    '3 项不可用；1 项为估算');
});
