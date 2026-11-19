// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The 提醒 page's decisions: how a rule's condition, distance and source read, which rules a plan owns,
// the new-alert dialog's live line and one-click thresholds, which field an API error belongs to, and
// the monitor line. Payloads follow backend/api/alerts.py and core/alerts/view.py.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  KIND_OPTIONS, accountAfterTickerChange, conditionText, currencyOf, distanceView, fieldForError, kindLabel, previewLine, priceBasis, quickPicks,
  holdingRules, ruleState, sourceInfo, splitThreshold, thresholdDigits, thresholdValue, tickerError, weightContext,
} from '../src/features/alerts/rules.ts';
import { MONITOR_CAVEAT, dayCounts, deliveryLabel, firesToday, monitorLine, splitDay, staleDayNote, suggestsSale } from '../src/features/alerts/monitor.ts';

const TODAY = '2026-11-17';

function rule(kind, threshold, market = {}, overrides = {}) {
  return {
    id: 1, ticker: 'SHA:DEMO', account: null, kind, kind_label: kindLabel(kind), threshold, condition: '', enabled: true,
    cooldown_seconds: 300, note: null, source: 'manual', created_at: '', updated_at: '',
    state: { armed: true, last_fired_at: null, last_trading_date: null, fire_count_today: 0 },
    market: {
      price: '61.2', prev_close: '60.8', change_pct: '0.66', weight_pct: null, value: '61.2', distance: '7.2', distance_pct: '11.76',
      condition_met: false, unit: 'price', checked_now: true, skip_reason: null, quote: null, ...market,
    },
    ...overrides,
  };
}

test('kinds and conditions read like the wireframe', () => {
  assert.deepEqual(KIND_OPTIONS.map((option) => option.label), ['止损', '目标价', '涨跌幅', '仓位上限']);
  assert.equal(conditionText({ kind: 'stop_loss', threshold: '54', ticker: 'SHA:DEMO' }), '≤ 54.00');
  assert.equal(conditionText({ kind: 'target_price', threshold: '1800', ticker: 'SHA:600519' }), '≥ 1,800.00');
  assert.equal(conditionText({ kind: 'target_price', threshold: '3.2', ticker: 'SHA:510300' }), '≥ 3.200', 'ETF prices have three decimals');
  assert.equal(conditionText({ kind: 'stop_loss', threshold: '380', ticker: 'HKG:00700' }), '≤ 380.000');
  assert.equal(conditionText({ kind: 'day_change_pct', threshold: '-4', ticker: 'NASDAQ:DEMO' }), '跌幅 ≥ 4%');
  assert.equal(conditionText({ kind: 'day_change_pct', threshold: '5.5', ticker: 'SHA:DEMO' }), '涨幅 ≥ 5.5%');
  assert.equal(conditionText({ kind: 'position_weight_max', threshold: '50', ticker: 'SHA:DEMO' }), '仓位 ≥ 50%');
  assert.equal(currencyOf('HKG:00700'), 'HKD');
  assert.equal(currencyOf('IDX:IXIC'), 'USD');
  assert.equal(thresholdDigits('IDX:HSI', 'HKD'), 2);
});

test('sources: manual, the assistant, and plans, whose rules are read-only here', () => {
  assert.deepEqual(sourceInfo('manual'), { label: '手动', planId: null, readOnly: false });
  assert.deepEqual(sourceInfo('agent:chat'), { label: '助手', planId: null, readOnly: false });
  assert.deepEqual(sourceInfo('plan:12'), { label: '计划 #12', planId: '12', readOnly: true });
  assert.equal(sourceInfo(null).label, '手动');
});

test('distance: how far the price must move, 已达到 when the condition holds, unknown without a trusted quote', () => {
  assert.deepEqual(distanceView(rule('stop_loss', '54')), { now: '61.20', gap: '差 11.8%', tone: 'plain', title: undefined });
  const reached = distanceView(rule('stop_loss', '62', { distance: '-0.8', distance_pct: '-1.31', condition_met: true }));
  assert.equal(reached.gap, '已达到');
  assert.equal(reached.tone, 'warn');
  const stale = distanceView(rule('stop_loss', '54', { distance: null, distance_pct: null, skip_reason: '行情已过期' }));
  assert.equal(stale.now, '61.20');
  assert.equal(stale.gap, null);
  assert.equal(stale.title, '行情已过期');
  const none = distanceView(rule('stop_loss', '54', { price: null, distance: null, distance_pct: null, skip_reason: '没有行情' }));
  assert.equal(none.now, null);
  assert.equal(none.tone, 'muted');
  const day = distanceView(rule('day_change_pct', '-4', { unit: 'pct_points', value: '0.66', distance: '4.66', distance_pct: '4.66' }));
  assert.equal(day.now, '+0.66%');
  assert.equal(day.gap, '差 4.66 个百分点');
  const weight = distanceView(rule('position_weight_max', '50', { unit: 'pct_points', weight_pct: '57.9', distance: '-7.9', distance_pct: null, condition_met: true }));
  assert.equal(weight.now, '57.90%');
  assert.equal(weight.gap, '已达到');
  assert.equal(distanceView({ ...rule('stop_loss', '54'), market: null }).title, '未请求行情');
});

test('已达到 is the backend\'s own comparison, never a distance rounded to 0, and a small gap keeps its digits', () => {
  // The gate review's case: 20.01 -> 21.01 is +4.9975 %, short of a 5 % rule (core/alerts/view.py condition_met).
  const short = distanceView(rule('day_change_pct', '5', { unit: 'pct_points', change_pct: '5.00', value: '5.00', distance: '0.0025', distance_pct: '0.0024' }));
  assert.equal(short.gap, '差 0.0025 个百分点');
  assert.equal(short.tone, 'plain');
  const rounded = distanceView(rule('day_change_pct', '5', { unit: 'pct_points', distance: '0.00', distance_pct: '0.00', condition_met: false }));
  assert.notEqual(rounded.gap, '已达到');
  const price = distanceView(rule('stop_loss', '21.01049999', { price: '21.0105', distance: '0.00000001', distance_pct: '0.000000048' }));
  assert.equal(price.gap, '差 0.000000048%');
  assert.equal(distanceView(rule('stop_loss', '54', { distance_pct: '7.04' })).gap, '差 7.0%', 'an ordinary gap reads as before');
  const exact = distanceView(rule('day_change_pct', '5', { unit: 'pct_points', distance: '0.00', distance_pct: '0.00', condition_met: true }));
  assert.equal(exact.gap, '已达到');
  assert.equal(exact.tone, 'warn');
});

test('the dialog promises a fire only on the comparison the monitor makes, not on a rounded change or weight', () => {
  // +4.9975 % shows as +5.00 %: the monitor waits, so saving does not fire.
  const rise = { ticker: 'SHA:DEMO', price: '21.01', prevClose: '20.01', changePct: '5.00' };
  assert.equal(previewLine('day_change_pct', '5', rise).text, '今日涨跌 +5.00%');
  assert.equal(previewLine('day_change_pct', '5', rise).warn, null);
  assert.match(previewLine('day_change_pct', '5', { ...rise, price: '21.0105' }).warn, /已达到/, 'exactly +5 %');
  assert.match(previewLine('day_change_pct', '4.99', rise).warn, /已达到/);
  const fall = { ticker: 'SHA:DEMO', price: '19.01', prevClose: '20.01', changePct: '-5.00' };   // -4.9975 %
  assert.equal(previewLine('day_change_pct', '-5', fall).warn, null);
  assert.match(previewLine('day_change_pct', '-4.99', fall).warn, /已达到/);
  // B3 (round 5): the monitor compares the exact weight (/api/today `weight_pct_exact`). An exact, verified
  // 50.00 % meets a 50 % cap and is promised, as 40868ac did; 49.996 % shown as 50.00 % is not.
  const held = (pct, exact) => ({ ticker: 'SHA:DEMO', price: '1', changePct: '0',
    weight: { state: 'value', pct, estimated: false, reason: null, ...(exact === undefined ? {} : { exact }) } });
  assert.match(previewLine('position_weight_max', '50', held('50.00', '50')).warn, /上限/);
  assert.equal(previewLine('position_weight_max', '50', held('50.00', '49.996')).warn, null);
  assert.equal(previewLine('position_weight_max', '50', held('50.00', '49.996')).text, '当前仓位 50.00%', 'the figure shown is as before');
  assert.match(previewLine('position_weight_max', '49.996', held('50.00', '49.996')).warn, /上限/);
  assert.match(previewLine('position_weight_max', '50', held('50.00')).warn, /上限/, 'without the exact figure (an older backend) as 40868ac');
  const row = (name, pct, exact) => ({ name, positions: [{ ticker: 'SHA:DEMO', weight_pct: pct, weight_pct_exact: exact, estimated: false, estimated_reason: null, quote: { reason: null } }] });
  const totals = { total_cny: '600', quality: 'complete' };
  // Round 6 (cr_v6b N4, cr_v6bs B3): one stock in several accounts. The figures are /api/today's for these
  // portfolios (tests/test_alerts_api.py); the API's condition_met is false and the monitor sends nothing, so no
  // promise, as at 40868ac. The sum of the exact figures decides, nothing added to it.
  const several = (rows) => ({ ...held('0'), weight: weightContext({ accounts: rows.map(([name, pct, exact]) => row(name, pct, exact)), totals }, 'SHA:DEMO', null, false) });
  const thirds = several([['A', '3.33', '3.33333333333333333333'], ['B', '3.33', '3.33333333333333333333'], ['C', '3.33', '3.33333333333333333333']]);
  assert.deepEqual(previewLine('position_weight_max', '10', thirds), { text: '当前仓位 9.99%', warn: null });
  const sevenths = several([['A', '3.57', '3.57142857142857142857'], ['B', '10.71', '10.71428571428571428571'], ['C', '35.71', '35.71428571428571428571']]);
  assert.deepEqual(previewLine('position_weight_max', '50', sevenths), { text: '当前仓位 49.99%', warn: null });
  // Both ways: two accounts at exactly 25 % each meet a 50 % cap (the API says true, the monitor fires).
  assert.match(previewLine('position_weight_max', '50', several([['A', '25.00', '25'], ['B', '25.00', '25']])).warn, /上限/);
  assert.match(previewLine('position_weight_max', '50', several([['A', '30.00', '30'], ['B', '25.00', '25']])).warn, /上限/);
  const short = { accounts: [row('A', '25.00', '24.998'), row('B', '25.00', '25.001')], totals };
  assert.equal(previewLine('position_weight_max', '50', { ...held('50'), weight: weightContext(short, 'SHA:DEMO', null, false) }).warn, null, '49.999 % in all');
  assert.deepEqual(weightContext(short, 'SHA:DEMO', 'B', false), { state: 'value', pct: '25.00', estimated: false, reason: null, exact: '25.001' });
});

test('the dialog line shows the price and the distance as the threshold is typed', () => {
  const context = { ticker: 'SHA:DEMO', price: '61.20', changePct: '0.66', weight: { state: 'value', pct: '57.9', estimated: false, reason: null } };
  assert.deepEqual(previewLine('stop_loss', '54', context), { text: '现价 61.20 · 距止损 -11.8%', warn: null });
  assert.deepEqual(previewLine('target_price', '66', context), { text: '现价 61.20 · 距目标 +7.8%', warn: null });
  assert.equal(previewLine('stop_loss', '', context).text, '现价 61.20');
  assert.equal(previewLine('stop_loss', 'abc', context).text, '现价 61.20');
  assert.match(previewLine('stop_loss', '62', context).warn, /止损价或以下/);
  assert.match(previewLine('target_price', '60', context).warn, /达到目标价/);
  assert.deepEqual(previewLine('stop_loss', '54', { ...context, price: null, priceReason: '停牌' }), { text: '现价不可用（停牌）', warn: null });
  assert.equal(previewLine('day_change_pct', '-4', context).text, '今日涨跌 +0.66%');
  assert.equal(previewLine('day_change_pct', '-4', context).warn, null);
  assert.match(previewLine('day_change_pct', '0.5', context).warn, /已达到/);
  assert.equal(previewLine('position_weight_max', '50', context).text, '当前仓位 57.90%');
  assert.match(previewLine('position_weight_max', '50', context).warn, /上限/);
  assert.equal(previewLine('position_weight_max', '50', { ...context, weight: { state: 'not_held', estimated: false, reason: null } }).text, '没有这只的持仓，当前仓位 0%');
});

test('one-click thresholds sit a fixed percentage from the price, in the instrument’s price digits', () => {
  assert.deepEqual(quickPicks('stop_loss', '61.20', 'SHA:DEMO'), [
    { label: '-5%', value: '58.14' }, { label: '-8%', value: '56.3' }, { label: '-10%', value: '55.08' },
  ]);
  assert.deepEqual(quickPicks('target_price', '61.20', 'SHA:DEMO').map((pick) => pick.label), ['+5%', '+10%', '+20%']);
  assert.deepEqual(quickPicks('stop_loss', null, 'SHA:DEMO'), []);
  assert.equal(quickPicks('stop_loss', '382.4', 'HKG:00700')[0].value, '363.28');
  assert.deepEqual(quickPicks('day_change_pct', null, 'SHA:DEMO').map((pick) => pick.value), ['-3', '-5', '5']);
  assert.deepEqual(quickPicks('position_weight_max', null, 'SHA:DEMO').map((pick) => pick.value), ['20', '30', '50']);
});

test('day moves keep their direction as a sign', () => {
  assert.equal(thresholdValue('day_change_pct', '5', 'down'), '-5');
  assert.equal(thresholdValue('day_change_pct', '-5', 'up'), '5');
  assert.equal(thresholdValue('stop_loss', ' 54 ', 'down'), '54');
  assert.deepEqual(splitThreshold('day_change_pct', '-4'), { magnitude: '4', direction: 'down' });
  assert.deepEqual(splitThreshold('day_change_pct', '3'), { magnitude: '3', direction: 'up' });
  assert.deepEqual(splitThreshold('stop_loss', '54'), { magnitude: '54', direction: 'down' });
});

test('API messages land on the field they are about', () => {
  assert.equal(fieldForError('止损价和目标价必须大于 0。'), 'threshold');
  assert.equal(fieldForError('阈值必须是数字，例如 "9.5"。'), 'threshold');
  assert.equal(fieldForError('涨跌幅阈值是带符号的百分数：负数表示下跌'), 'threshold');
  assert.equal(fieldForError('已有相同的提醒规则（#3）。'), 'threshold');
  assert.equal(fieldForError('代码格式不正确。请使用“交易所:代码”'), 'ticker');
  assert.equal(fieldForError('指数不是持仓，不能设置仓位上限提醒。'), 'kind');
  assert.equal(fieldForError('账户名称最多 240 个字符。'), 'account');
  assert.equal(fieldForError('备注最多 200 个字符。'), 'note');
  assert.equal(fieldForError('提醒规则已达上限 500 条'), null);
});

test('the monitor line: running or not, the last check, amber when alerts are not checked', () => {
  const status = { running: true, status: 'ok', message: '提醒正在运行。', last_cycle_at: `${TODAY}T10:32:05+08:00`, rules_enabled: 3 };
  assert.deepEqual(monitorLine(status, TODAY), { label: '运行中', warn: false, checked: '上次检查 10:32:05', message: '提醒正在运行。', notes: [] });
  assert.equal(monitorLine({ ...status, last_cycle_at: '2026-11-16T15:00:00+08:00' }, TODAY).checked, '上次检查 11-16 15:00:00');
  assert.deepEqual(monitorLine({ ...status, running: false, status: 'not_started', last_cycle_at: null }, TODAY).checked, null);
  assert.equal(monitorLine({ ...status, running: false, status: 'not_started' }).label, '未运行');
  assert.equal(monitorLine({ ...status, running: false, status: 'not_started' }).warn, true);
  assert.equal(monitorLine({ ...status, status: 'waiting' }).label, '运行中 · 等待开盘');
  assert.equal(monitorLine({ ...status, status: 'rule_errors', rules_errored: 2 }).label, '运行中 · 2 条规则无法检查');
  assert.equal(monitorLine({ ...status, status: 'rule_errors', rules_errored: 2 }).warn, true);
  assert.equal(monitorLine({ ...status, status: 'standby' }).warn, false);
  assert.equal(monitorLine({ ...status, running: false, status: 'something_new' }).label, '未运行');
  assert.equal(monitorLine(undefined), null);
  assert.match(MONITOR_CAVEAT, /后端关闭时不会提醒/);
});

test('fires: which suggest a sale, how many per rule today, how they were delivered', () => {
  assert.ok(suggestsSale({ kind: 'stop_loss' }));
  assert.ok(suggestsSale({ kind: 'position_weight_max' }));
  assert.ok(!suggestsSale({ kind: 'day_change_pct' }));
  const events = [
    { rule_id: 1, fired_at: `${TODAY}T10:05:12+08:00` }, { rule_id: 1, fired_at: `${TODAY}T13:05:12+08:00` },
    { rule_id: 2, fired_at: '2026-11-16T10:05:12+08:00' },
  ];
  const counts = firesToday(events, TODAY);
  assert.equal(counts.get(1), 2);
  assert.equal(counts.get(2), undefined);
  assert.equal(firesToday(events, null).size, 0);
  assert.equal(deliveryLabel('suppressed', true), '已汇总');
});

test('today\'s counts are 不可用 once a refresh fails, not the earlier counts shown as current', () => {
  const events = [{ rule_id: 1, fired_at: `${TODAY}T10:05:12+08:00` }];
  assert.deepEqual(dayCounts(undefined, null, TODAY), { state: 'loading' });
  assert.deepEqual(dayCounts(events, null, TODAY), { state: 'ok', counts: new Map([[1, 1]]) });
  // The gate review's case: the earlier read is still cached, the refetch failed; a later fire may be missing.
  const failed = dayCounts(events, '网络连接失败', TODAY);
  assert.equal(failed.state, 'unavailable');
  assert.match(failed.reason, /今天的触发记录暂时无法读取：网络连接失败/);
  assert.deepEqual(dayCounts(undefined, '网络连接失败', TODAY).state, 'unavailable');
  assert.match(staleDayNote('网络连接失败'), /刷新失败（网络连接失败）.*之前读到的记录/);
});

// -- review r3 ----------------------------------------------------------------------------------------

test('a condition shows the stored threshold digit for digit', () => {
  assert.equal(conditionText({ kind: 'target_price', threshold: '11.004', ticker: 'SHA:DEMO' }), '≥ 11.004', '11.00 would not fire it');
  assert.equal(conditionText({ kind: 'stop_loss', threshold: '0.12345678', ticker: 'NASDAQ:DEMO' }), '≤ 0.12345678');
  assert.equal(conditionText({ kind: 'day_change_pct', threshold: '0.001', ticker: 'SHA:DEMO' }), '涨幅 ≥ 0.001%');
  assert.equal(conditionText({ kind: 'day_change_pct', threshold: '-0.001', ticker: 'SHA:DEMO' }), '跌幅 ≥ 0.001%');
  assert.equal(conditionText({ kind: 'position_weight_max', threshold: '12.345', ticker: 'SHA:DEMO' }), '仓位 ≥ 12.345%');
  assert.equal(conditionText({ kind: 'stop_loss', threshold: '1e21', ticker: 'SHA:DEMO' }), '阈值无效', 'not a crash, not a guess');
  assert.equal(conditionText({ kind: 'day_change_pct', threshold: '1e-13', ticker: 'SHA:DEMO' }), '阈值无效');
});

test('a rule the monitor skips says so, with the backend\'s reason and since when, instead of 启用 or 已达到', () => {
  const skipped = rule('stop_loss', '62', {
    distance: '-0.8', distance_pct: '-1.31', condition_met: true, checked_now: false, skip_code: 'stale_observation',
    skip_reason: '行情的交易所时间早于上次变化所依据的行情，本次不检查', skipped_since: `${TODAY}T10:05:00+08:00`,
  });
  const state = ruleState(skipped, false);
  assert.equal(state.label, '暂停检查');
  assert.equal(state.tone, 'warn');
  assert.match(state.detail, /交易所时间早于/);
  assert.match(state.detail, /10:05 起/);
  assert.match(ruleState(skipped, false, TODAY).detail, / · 自 10:05 起$/, 'the page\'s own day is not repeated');
  assert.match(ruleState(skipped, false, '2026-11-18').detail, /自 11-17 10:05 起/);
  assert.notEqual(distanceView(skipped).gap, '已达到');
  assert.equal(distanceView(skipped).tone, 'warn');
  const unknown = ruleState(rule('stop_loss', '54', { checked_now: false, skip_code: 'brand_new_code', skip_reason: null }), false);
  assert.equal(unknown.tone, 'warn');
  assert.doesNotMatch(`${unknown.label}${unknown.detail ?? ''}`, /brand_new_code|正常/);
  const closed = ruleState(rule('stop_loss', '54', { checked_now: false, skip_code: 'session_closed', skip_reason: '不在交易时段，不检查提醒' }), false);
  assert.equal(closed.tone, 'muted');
  assert.equal(ruleState(rule('stop_loss', '54'), false).label, '启用');
  assert.equal(ruleState(rule('stop_loss', '54'), true).label, '无法检查');
  assert.equal(ruleState({ ...rule('stop_loss', '54'), enabled: false }, false).label, '已停用');
  const duplicate = ruleState({ ...rule('stop_loss', '54'), enabled: false, disabled_reason: '与规则 #3 重复，已停用' }, false);
  assert.equal(duplicate.label, '已停用');
  assert.match(duplicate.detail, /与规则 #3 重复/);
  assert.equal(ruleState({ ...rule('stop_loss', '1e21') }, false).label, '无法检查', 'an unreadable threshold cannot be checked');
});

const todayFeed = (overrides = {}) => ({
  as_of: `${TODAY}T10:32:05+08:00`,
  accounts: [
    { name: 'A', positions: [{ ticker: 'SHA:DEMO', identity: 'SHA:DEMO', weight_pct: '40.00', estimated: false, estimated_reason: null, quote: { reason: null } }] },
    { name: 'B', positions: [{ ticker: 'NYSE:BRK-B', identity: 'NYSE:BRK.B', weight_pct: null, estimated: false, estimated_reason: null, quote: { reason: '没有行情' } }] },
  ],
  totals: { total_cny: '100000', quality: 'complete', estimated: [], unavailable: [] },
  ...overrides,
});

test('the dialog\'s weight: unavailable is 不可用, estimated is marked and promises nothing, not held weighs 0 only when the total is verified', () => {
  assert.deepEqual(weightContext(undefined, 'SHA:DEMO', null, false), { state: 'loading' });
  assert.equal(weightContext(undefined, 'SHA:DEMO', null, true).state, 'unavailable');
  assert.deepEqual(weightContext(todayFeed(), 'SHA:DEMO', null, false), { state: 'value', pct: '40.00', estimated: false, reason: null });
  const missing = weightContext(todayFeed(), 'NYSE:BRK.B', null, false);
  assert.equal(missing.state, 'unavailable');
  assert.match(missing.reason, /没有行情/);
  assert.equal(weightContext(todayFeed(), 'SHA:DEMO', 'B', false).state, 'not_held');
  assert.equal(weightContext(todayFeed(), 'SHA:DEMO', 'C', false).state, 'unavailable', 'no such account');
  const estimated = weightContext(todayFeed({ totals: { total_cny: '100000', quality: 'estimated', estimated: [{ currency: 'USD', status: 'stale', reason: '汇率已过期' }], unavailable: [] } }), 'SHA:DEMO', null, false);
  assert.equal(estimated.estimated, true);
  assert.match(estimated.reason, /汇率已过期/);
  const noTotal = weightContext(todayFeed({ totals: { total_cny: null, quality: null, estimated: [], unavailable: [{ ticker: 'X', reason: '行情不可用' }] } }), 'SHA:DEMO', null, false);
  assert.equal(noTotal.state, 'unavailable');

  const base = { ticker: 'SHA:DEMO', price: '61.20', changePct: '0.66' };
  const unavailable = previewLine('position_weight_max', '50', { ...base, weight: missing });
  assert.match(unavailable.text, /不可用/);
  assert.doesNotMatch(unavailable.text, /为 0|0%/);
  const marked = previewLine('position_weight_max', '30', { ...base, weight: estimated });
  assert.match(marked.text, /估算/);
  assert.equal(marked.warn, null, 'no promise that saving fires at the next check');
  assert.match(marked.note, /核实/);
  assert.match(previewLine('position_weight_max', '50', { ...base, weight: { state: 'not_held', estimated: true, reason: '汇率已过期' } }).text, /未经核实|估算/);
  assert.match(previewLine('position_weight_max', '50', { ...base, weight: { state: 'loading' } }).text, /读取/);
});

test('one-click thresholds name their price basis, and an unverified price says so in amber', () => {
  const quote = (status, extra = {}) => ({
    ticker: 'SHA:DEMO', name: null, price: '100', prev_close: '99', change_pct: '1.01', currency: 'CNY', estimated: status !== 'live', estimated_reason: status === 'live' ? null : '行情已过期',
    quote: { source: 'fake', quote_time: `${TODAY}T09:40:00+08:00`, fetched_at: null, status, reason: status === 'live' ? null : '超过 2 分钟未更新', session_date: TODAY },
    ...extra,
  });
  const live = priceBasis(quote('live'), 'SHA:DEMO', TODAY);
  assert.match(live.text, /100\.00/);
  assert.match(live.text, /09:40/);
  assert.equal(live.warn, false);
  const stale = priceBasis(quote('stale'), 'SHA:DEMO', TODAY);
  assert.equal(stale.warn, true);
  assert.match(stale.text, /已过期/);
  assert.equal(priceBasis(null, 'SHA:DEMO', TODAY), null);
  assert.equal(priceBasis(quote('unavailable', { price: null }), 'SHA:DEMO', TODAY), null);
});

test('the ticker must be a pick of a usable listing; an unsupported code says why', () => {
  assert.equal(tickerError('SHA:600519', { ticker: 'SHA:600519', supported: true }), null);
  assert.match(tickerError('SHA:000001', null), /指数编号段/);
  assert.match(tickerError('SHA:600519', null), /从列表中选择/);
  assert.match(tickerError('600519', { ticker: 'SHA:600519', supported: true }), /从列表中选择/, 'the field no longer holds the pick');
  assert.match(tickerError('', null), /请选择标的/);
});

test('the account is shown and changes with the instrument, never kept hidden from the opener', () => {
  assert.equal(accountAfterTickerChange('A', 'SHA:DEMO', 'SHA:DEMO'), 'A');
  assert.equal(accountAfterTickerChange('A', 'SHA:DEMO', 'SHA:600519'), '', 'another instrument: no account until chosen');
  assert.equal(accountAfterTickerChange('A', 'HKG:0700', 'HKG:00700'), 'A', 'one listing in two spellings');
});

test('the monitor line says who checks: this process, another one (liveness unverified), nobody, or the demo', () => {
  // GET /api/alerts/status after the alerts review: lease_holder, lease.live, running (live lease, not demo), demo.
  const base = {
    running: true, status: 'ok', active_status: 'ok', message: '提醒正在运行。', last_cycle_at: `${TODAY}T10:32:05+08:00`, rules_enabled: 3,
    demo: false, in_process: true, role: 'active', lease_holder: 'this_process', lease_holder_pid: 10,
  };
  const here = monitorLine(base, TODAY);
  assert.equal(here.label, '运行中');
  assert.equal(here.warn, false);
  assert.equal(here.checked, '上次检查 10:32:05');
  const other = monitorLine({ ...base, in_process: false, role: null, lease_holder: 'another_process', lease_holder_pid: 77 }, TODAY);
  assert.notEqual(other.label, '运行中');
  assert.match(other.label, /另一个进程/);
  assert.equal(other.warn, true, 'this backend cannot vouch for it');
  assert.match(other.checked, /10:32:05/);
  assert.match(other.message, /无法确认/);
  assert.match(other.message, /77/);
  const gone = monitorLine({ ...base, running: false, status: 'unresponsive', active_status: 'unresponsive', in_process: false, role: null, lease_holder: null, lease_holder_pid: null }, TODAY);
  assert.equal(gone.warn, true);
  assert.doesNotMatch(gone.label, /运行中|在检查/);
  const noLease = monitorLine({ ...base, running: false, in_process: false, role: null, lease_holder: null, lease_holder_pid: null }, TODAY);
  assert.equal(noLease.warn, true, 'a fresh heartbeat without a live lease: nobody checks now');
  assert.doesNotMatch(noLease.label, /运行中|在检查/);
  const standbyDead = monitorLine({ ...base, running: false, status: 'standby', active_status: 'unresponsive', role: 'standby', lease_holder: null }, TODAY);
  assert.equal(standbyDead.warn, true);
  assert.doesNotMatch(standbyDead.label, /在检查/);
  const standbyOk = monitorLine({ ...base, status: 'standby', role: 'standby', lease_holder: 'another_process', lease_holder_pid: 77 }, TODAY);
  assert.equal(standbyOk.warn, false);
  assert.match(standbyOk.label, /另一个后端进程在检查/);
  const skipped = monitorLine({ ...base, rules_skipped: 2, skipped_rules: [{ rule_id: 1, code: 'weight_estimated', message: '组合估值含未核实的价格', since: `${TODAY}T09:30:00+08:00`, held_since: `${TODAY}T09:30:00+08:00` }] }, TODAY);
  assert.equal(skipped.warn, true);
  assert.match(skipped.label, /2 条规则暂停检查/);
  const clock = monitorLine({ ...base, clock_warning: '提醒监控记录的时间晚于本机当前时间' }, TODAY);
  assert.equal(clock.warn, true);
  assert.deepEqual(clock.notes, ['提醒监控记录的时间晚于本机当前时间']);
  const demo = monitorLine({ ...base, running: false, status: 'demo', demo: true, in_process: false, role: null, lease_holder: null }, TODAY);
  assert.equal(demo.label, '演示模式不检查提醒');
  assert.equal(demo.warn, false);
  assert.equal(monitorLine({ ...base, in_process: false }, TODAY, { demo: true }).label, '演示模式不检查提醒', 'an older backend in the demo');
  assert.equal(monitorLine({ ...base, running: false, status: 'disabled', active_status: 'disabled', lease_holder: null }, TODAY).label, '已关闭');
});

test('a held-back fire is 已抑制 until a published digest covers it', () => {
  assert.equal(deliveryLabel('suppressed', false), '已抑制（等待下一次汇总）');
  assert.equal(deliveryLabel('suppressed', null), '已抑制（等待下一次汇总）');
  assert.equal(deliveryLabel('suppressed'), '已抑制（等待下一次汇总）');
  assert.equal(deliveryLabel('notified', null), '已通知');
});

test('today\'s fires are read to the end of the day, page by page', () => {
  const at = (date, id) => ({ id, rule_id: id, fired_at: `${date}T10:00:00+08:00` });
  assert.deepEqual(splitDay([at(TODAY, 3), at(TODAY, 2)], TODAY), { today: [at(TODAY, 3), at(TODAY, 2)], done: false });
  assert.deepEqual(splitDay([at(TODAY, 3), at('2026-11-16', 2)], TODAY), { today: [at(TODAY, 3)], done: true });
  assert.deepEqual(splitDay([], TODAY), { today: [], done: true });
});

test('a crossing seen on an unverified price promises nothing: the monitor will not fire on it', () => {
  const stale = { ticker: 'SHA:DEMO', price: '100', changePct: '-6', priceVerified: false, priceReason: '行情已过期' };
  const stop = previewLine('stop_loss', '101', stale);
  assert.equal(stop.warn, null);
  assert.match(stop.note, /未经核实/);
  assert.equal(previewLine('day_change_pct', '-5', stale).warn, null);
  assert.match(previewLine('day_change_pct', '-5', stale).note, /未经核实/);
  assert.match(previewLine('stop_loss', '101', { ...stale, priceVerified: true }).warn, /下一次检查/);
});

test('a holding\'s alert chip counts the enabled rules of its listing that cover its account', () => {
  const rules = [
    { id: 1, ticker: 'NYSE:BRK.B', identity: 'NYSE:BRK.B', account: null, enabled: true },
    { id: 2, ticker: 'NYSE:BRK-B', account: 'A', enabled: true },
    { id: 3, ticker: 'NYSE:BRK.B', identity: 'NYSE:BRK.B', account: 'B', enabled: true },
    { id: 4, ticker: 'NYSE:BRK.B', identity: 'NYSE:BRK.B', account: null, enabled: false },
    { id: 5, ticker: 'SHA:DEMO', identity: 'SHA:DEMO', account: null, enabled: true },
  ];
  assert.deepEqual(holdingRules(rules, 'NYSE:BRK-B', 'A').map((rule) => rule.id), [1, 2]);
  assert.deepEqual(holdingRules(rules, 'NYSE:BRK-B', 'B').map((rule) => rule.id), [1, 3]);
  assert.deepEqual(holdingRules(rules, 'NYSE:BRK-B').map((rule) => rule.id), [1, 2, 3], 'no account given: every account');
});
