// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The plans page's decisions: status chips, filters, the one-line summary, distance texts from the
// market block, deviation tones, and the form's request body and loss estimate. Shapes follow
// dashboard/backend/api/plans.py and dashboard/core/plans.py.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  UNFILLED, alertRuleText, buyDraft, changedFields, defaultFilter, deviationTone, duePlans, entryRange, isEnded, listingKey, marketState,
  matchesFilter, maxLossView, planChipView, planName, planPrice, planSummary, plannedSize, plansOfListing, priceView, quantityLine,
  relationLabel, sameListing, statusChip, stopDistance, targetDistance, tradeText, weightLine,
} from '../src/features/plans/view.ts';
import {
  addDays, currencyOf, editRequest, emptyForm, estimateMaxLoss, formFromPlan, formToBody, missingFields,
} from '../src/features/plans/form.ts';
import { longLine } from '../src/lib/format.ts';

function plan(overrides = {}) {
  return {
    id: 7, ticker: 'SHA:DEMO', account: null, direction: 'buy', thesis: '估值低', invalidation: '跌破 54',
    entry_low: '58', entry_high: '61', stop_price: '54', target_price: '66', planned_weight_pct: null,
    planned_quantity: '1200', review_on: '2026-12-17', status: 'active', status_label: '启用中', close_reason: null,
    source: 'manual', created_at: '2026-11-17T02:00:00Z', updated_at: '2026-11-17T02:00:00Z',
    activated_at: '2026-11-17T02:01:00Z', ended_at: null, currency: 'CNY',
    max_loss: { amount: '8400.00', currency: 'CNY', amount_cny: '8400.00', fx_rate: null, fx_status: null,
      fx_source: null, fx_quote_time: null, estimated: false, estimated_reason: null,
      entry_basis: 'entry_high', formula: '(入场价上限 61 − 止损价 54) × 计划股数 1200 = 8,400.00 CNY。不含手续费、滑点和跳空低开。', reason: null },
    max_loss_cny: '8400.00', conflicts: [], version: '0123456789abcdef',
    ...overrides,
  };
}

function market(overrides = {}) {
  return {
    name: '示例宽基', price: '61.20', prev_close: '60.80', change_pct: '0.66', price_reason: null,
    quote: { status: 'live', reason: null, source: 'fake', quote_time: '2026-11-17T10:32:02+08:00', fetched_at: null, session_date: null },
    position_quantity: '1000', planned_quantity: '1200', quantity_vs_plan: '-200', weight_pct: '8.20', weight_estimated: false,
    weight_reason: null, planned_weight_pct: null, weight_vs_plan_pct: null, over_planned_weight: null,
    stop_price: '54', distance_to_stop: '7.20', distance_to_stop_pct: '11.76', stop_reached: false,
    target_price: '66', distance_to_target: '4.80', distance_to_target_pct: '7.84', target_reached: false, as_of: null,
    ...overrides,
  };
}

// -- status and filters -----------------------------------------------------------------------------

test('a status chip uses the API label and a tone; only active is accented', () => {
  assert.deepEqual(statusChip(plan()), { label: '启用中', tone: 'accent' });
  assert.deepEqual(statusChip(plan({ status: 'draft', status_label: '草稿' })), { label: '草稿', tone: 'neutral' });
  assert.deepEqual(statusChip(plan({ status: 'closed', status_label: '已关闭' })), { label: '已关闭', tone: 'neutral' });
  assert.equal(statusChip({ status: 'cancelled', status_label: '' }).label, '已取消');
  assert.ok(isEnded('closed') && isEnded('cancelled') && !isEnded('active') && !isEnded('draft'));
});

test('filters: 已结束 holds closed and cancelled; the default is the first one with plans', () => {
  const rows = [plan(), plan({ status: 'draft' }), plan({ status: 'closed' }), plan({ status: 'cancelled' })];
  assert.deepEqual(['active', 'draft', 'ended', 'all'].map((filter) => rows.filter((row) => matchesFilter(row, filter)).length), [1, 1, 2, 4]);
  assert.equal(defaultFilter(rows), 'active');
  assert.equal(defaultFilter([plan({ status: 'draft' }), plan({ status: 'closed' })]), 'draft');
  assert.equal(defaultFilter([plan({ status: 'closed' })]), 'all');
  assert.equal(defaultFilter([]), 'all');
});

// -- summary --------------------------------------------------------------------------------------------

test('the one-line summary', () => {
  assert.equal(planSummary(plan()), '入场 58–61 · 止损 54 · 目标 66 · 计划 1,200 股');
  assert.equal(planSummary(plan({ planned_quantity: null, planned_weight_pct: '15.50' })), '入场 58–61 · 止损 54 · 目标 66 · 计划 15.5%');
  assert.equal(planSummary(plan({ entry_low: null, target_price: null })), '入场 ≤61 · 止损 54 · 计划 1,200 股');
  assert.ok(planSummary(plan({ entry_low: '60', entry_high: '60.00' })).startsWith('入场 60 · 止损'));
  assert.equal(planSummary(plan({ entry_low: null, entry_high: null, stop_price: null, target_price: null, planned_quantity: null })), '尚未填写价格和仓位');
  assert.equal(entryRange(plan({ entry_high: null })), '≥58');
  assert.equal(entryRange(plan({ entry_high: null, entry_low: null })), null);
  assert.equal(plannedSize(plan({ planned_quantity: null })), null);
});

// -- distances ----------------------------------------------------------------------------------------------

test('distance texts come from the market block: the move needed, the reason when unknown, never 0', () => {
  // The backend's digits (11.76), not rounded again to one decimal.
  assert.deepEqual(stopDistance(plan(), market()), { text: '-11.76%', reason: null, tone: 'neutral' });
  assert.deepEqual(targetDistance(plan(), market()), { text: '+7.84%', reason: null, tone: 'neutral' });
  // Close to the stop: amber.
  assert.equal(stopDistance(plan(), market({ distance_to_stop_pct: '3.20' })).tone, 'warn');
  // Reached.
  assert.deepEqual(stopDistance(plan(), market({ stop_reached: true, distance_to_stop_pct: '-1.00' })), { text: '已触及止损', reason: null, tone: 'warn' });
  assert.equal(targetDistance(plan(), market({ target_reached: true })).text, '已到目标');
  // No price: the reason, not 0.
  const none = market({ price: null, distance_to_stop_pct: null, distance_to_target_pct: null, price_reason: '停牌' });
  assert.deepEqual(stopDistance(plan(), none), { text: '不可用', reason: '停牌', tone: 'neutral' });
  assert.equal(targetDistance(plan(), none).reason, '停牌');
  assert.equal(stopDistance(plan(), market({ price: null, distance_to_stop_pct: null, price_reason: null })).reason, '行情不可用');
  // No stop set; no market yet.
  assert.equal(stopDistance(plan({ stop_price: null }), market()).text, '未设止损');
  assert.equal(targetDistance(plan({ target_price: null }), market()).text, '未设目标');
  assert.deepEqual(stopDistance(plan(), undefined), { text: '不可用', reason: '行情尚未取得', tone: 'neutral' });
  for (const text of [stopDistance(plan(), none).text, stopDistance(plan(), undefined).text]) assert.ok(!/(^|[^\d])0(\.0+)?%/.test(text));
});

test('position against the plan says what is unknown', () => {
  assert.deepEqual(quantityLine(market(), plan()), { text: '持有 1,000 股 / 计划 1,200 股', reason: null });
  assert.deepEqual(quantityLine(market({ position_quantity: null, weight_reason: '账本没有已确认的持仓' }), plan()), { text: '计划 1,200 股', reason: '账本没有已确认的持仓' });
  assert.equal(weightLine(market(), plan({ planned_weight_pct: '15' })).text, '占组合 8.20% / 计划 15%');
  assert.equal(weightLine(market({ weight_pct: '57.94' }), plan()).text, '占组合 57.94%');
  assert.equal(weightLine(market({ weight_pct: null, weight_reason: '组合总值不可用' }), plan()).text, '不可用');
  const estimated = weightLine(market({ weight_estimated: true, weight_reason: '汇率未核实' }), plan());
  assert.deepEqual([estimated.estimated, estimated.reason], [true, '汇率未核实']);
});

test('plan prices and the max loss keep the backend\'s digits', () => {
  assert.equal(planPrice('54', 'CNY'), '54.00');
  assert.equal(planPrice('3.456', 'CNY'), '3.456');
  assert.equal(planPrice('382.4', 'HKD'), 'HK$382.400');
  assert.equal(planPrice('31.555', 'USD'), '$31.555');
  assert.equal(planPrice(null, 'CNY'), null);
  assert.equal(maxLossView(plan({ max_loss: { ...plan().max_loss, amount: '8400.125', amount_cny: '8400.125' } })).text, '¥8,400.125');
});

test('an open plan\'s market block: pending while loading, 不可用 with the reason when the request failed', () => {
  // Drafts and active plans are compared with the market; ended plans are not.
  assert.deepEqual(marketState(plan({ status: 'closed' }), undefined, undefined), { kind: 'none', reason: null });
  assert.deepEqual(marketState(plan({ status: 'cancelled' }), undefined, undefined), { kind: 'none', reason: null });
  assert.deepEqual(marketState(plan({ status: 'draft' }), undefined, undefined), { kind: 'pending', reason: null });
  assert.deepEqual(marketState(plan(), undefined, undefined), { kind: 'pending', reason: null });
  // A failed detail request is not "still loading" forever.
  assert.deepEqual(marketState(plan(), undefined, '上游服务错误 (503)'), { kind: 'failed', reason: '行情读取失败：上游服务错误 (503)' });
  const detail = { ...plan(), market: market() };
  assert.deepEqual(marketState(plan(), detail, undefined), { kind: 'ready', reason: null });
  // A refresh that failed after an answer keeps the answer.
  assert.deepEqual(marketState(plan(), detail, 'x'), { kind: 'ready', reason: null });
});

// -- trades --------------------------------------------------------------------------------------------------------

test('relations, deviation tones, trade and alert lines', () => {
  assert.deepEqual(['entry', 'add', 'reduce', 'exit', 'none'].map(relationLabel), ['建仓', '加仓', '减仓', '清仓', '无计划']);
  assert.equal(deviationTone({ level: 'warning' }), 'warn');
  assert.equal(deviationTone({ level: 'info' }), 'neutral');
  assert.equal(tradeText({ kind: 'buy', quantity: '100', price: '60.50' }), '买入 100 股 @ 60.5');
  assert.equal(tradeText({ kind: 'sell', quantity: null, price: null }), '卖出');
  // An imported trade's price worked out as 成交金额 ÷ 数量: ≈ to 8 places, the full line for the title.
  const derived = { kind: 'buy', quantity: '300', price: '32.016666666666666666666666667' };
  assert.equal(tradeText(derived), '买入 300 股 @ ≈32.01666667');
  assert.equal(longLine((pick) => tradeText(derived, pick)).full, '买入 300 股 @ 32.016666666666666666666666667');
  // A plan's own prices have at most 8 places (the API's rule): every digit, nothing to round.
  assert.equal(planPrice('3.45678901', 'CNY'), '3.45678901');
  assert.equal(alertRuleText({ kind: 'stop_loss', threshold: '54', enabled: true }), '止损提醒：价格跌到 54 或以下');
  assert.equal(alertRuleText({ kind: 'target_price', threshold: '66', enabled: false }), '目标价提醒：价格涨到 66 或以上（已停用）');
  assert.equal(alertRuleText({ kind: 'position_weight_max', threshold: '15', enabled: true }), '仓位上限提醒：占组合超过 15%');
});

// -- decimals ------------------------------------------------------------------------------------------------------

test('review dates a month ahead', () => {
  assert.equal(addDays('2026-11-17', 30), '2026-12-17');
  assert.equal(addDays('2026-12-20', 30), '2027-01-19');
  assert.equal(addDays('nope', 1), null);
});

// -- the form ------------------------------------------------------------------------------------------------------------

test('a new form starts a month ahead and asks only for what is required', () => {
  const form = emptyForm('2026-11-17');
  assert.equal(form.reviewOn, '2026-12-17');
  assert.equal(form.sizeMode, 'weight');
  assert.deepEqual(Object.keys(missingFields(form)).sort(), ['invalidation', 'thesis', 'ticker']);
  assert.deepEqual(missingFields({ ...form, ticker: 'SHA:DEMO', thesis: '估值低', invalidation: '跌破 54' }), {});
  assert.deepEqual(Object.keys(missingFields({ ...form, ticker: ' ', thesis: '  ', invalidation: 'x' })).sort(), ['thesis', 'ticker']);
  assert.equal(emptyForm(null).reviewOn, '');
  assert.equal(emptyForm('2026-11-17', { ticker: 'SHA:DEMO', account: '长期账户' }).account, '长期账户');
});

test('the request body: strings, empty becomes null, weight XOR quantity, no derived max loss', () => {
  const form = { ...emptyForm('2026-11-17'), ticker: ' SHA:DEMO ', account: '', thesis: ' 估值低 ', invalidation: '跌破 54',
    entryLow: '58', entryHigh: '61.50', stop: '54', target: '', sizeMode: 'quantity', size: '1200' };
  const body = formToBody(form);
  assert.deepEqual(body, {
    ticker: 'SHA:DEMO', account: null, direction: 'buy', thesis: '估值低', invalidation: '跌破 54', entry_low: '58', entry_high: '61.50',
    stop_price: '54', target_price: null, planned_weight_pct: null, planned_quantity: '1200', review_on: '2026-12-17',
  });
  assert.ok(!('max_loss_cny' in body) && !('max_loss' in body));
  const weight = formToBody({ ...form, sizeMode: 'weight', size: '15' });
  assert.deepEqual([weight.planned_weight_pct, weight.planned_quantity], ['15', null]);
  const none = formToBody({ ...form, size: ' ' });
  assert.deepEqual([none.planned_weight_pct, none.planned_quantity], [null, null]);
  for (const value of Object.values(body)) assert.notEqual(value, '');
  // Switching the mode never sends both, even with text left in the field.
  assert.equal([formToBody({ ...form, sizeMode: 'weight' }), formToBody({ ...form, sizeMode: 'quantity' })].filter((b) => b.planned_weight_pct && b.planned_quantity).length, 0);
});

test('editing: the form is built from the plan; an edit sends only what changed, with the version it was made against', () => {
  const active = plan();
  const form = formFromPlan(active);
  assert.deepEqual([form.sizeMode, form.size, form.account], ['quantity', '1200', '']);
  assert.equal(formFromPlan(plan({ planned_quantity: null, planned_weight_pct: '15' })).sizeMode, 'weight');
  // The round trip keeps every number: nothing changed, nothing to send.
  assert.equal(editRequest(active, form), null);
  // A thesis edit does not send the stop back (another tab may have moved it).
  assert.deepEqual(editRequest(active, { ...form, thesis: '新理由' }), { thesis: '新理由', if_version: '0123456789abcdef' });
  // Switching the size mode sends both sizes: the new one and the cleared one.
  assert.deepEqual(editRequest(active, { ...form, sizeMode: 'weight', size: '15' }),
    { planned_weight_pct: '15', planned_quantity: null, if_version: '0123456789abcdef' });
  // An active plan keeps ticker, account and direction; a draft may change them.
  assert.deepEqual(editRequest(active, { ...form, ticker: 'SHA:OTHER', account: '长期账户', stop: '' }), { stop_price: null, if_version: '0123456789abcdef' });
  assert.deepEqual(editRequest(plan({ status: 'draft' }), { ...form, ticker: 'SHA:OTHER' }), { ticker: 'SHA:OTHER', if_version: '0123456789abcdef' });
  // Whitespace alone is no change.
  assert.equal(editRequest(active, { ...form, thesis: ` ${form.thesis} ` }), null);
});

test('a refused stale edit says what changed meanwhile', () => {
  const before = plan();
  const after = plan({ stop_price: '55', target_price: null, planned_quantity: null, planned_weight_pct: '10', thesis: '另一个理由' });
  assert.deepEqual(changedFields(before, after), ['买入理由', '止损 54 → 55', '目标 66 → 未填写', '计划仓位 1,200 股 → 10%']);
  assert.deepEqual(changedFields(before, plan({ status: 'closed', status_label: '已关闭' })), ['状态 启用中 → 已关闭']);
  assert.deepEqual(changedFields(before, before), []);
});

test('a stop at or above the entry shows no loss and the gain it locks in, never a negative loss', () => {
  const locked = { ...plan().max_loss, amount: '0.00', amount_cny: '0.00', locked_gain: '2400.00',
    formula: '止损价 63 不低于入场价上限 61：触及止损时不亏损，按计划股数 1200 锁定盈利 (63 − 61) × 1200 = 2400.00 CNY。不含手续费、滑点和跳空低开。' };
  const view = maxLossView(plan({ stop_price: '63', max_loss: locked }));
  assert.deepEqual([view.text, view.estimated, view.muted], ['¥0.00', false, false]);
  assert.equal(view.note, '止损价不低于入场价：触及止损时不亏损，按计划股数锁定盈利 ¥2,400.00');
  // In another currency zero needs no rate: no conversion sentence, the gain in the plan's currency.
  const usd = maxLossView(plan({ currency: 'USD', stop_price: '63', max_loss: { ...locked, currency: 'USD', locked_gain: '20.00' } }));
  assert.deepEqual([usd.text, usd.note], ['¥0.00', '止损价不低于入场价：触及止损时不亏损，按计划股数锁定盈利 $20.00']);
});

test('the loss estimate uses the backend formula, only with a quantity, in the ticker currency', () => {
  const form = { ...emptyForm('2026-11-17'), ticker: 'SHA:600519', entryLow: '58', entryHigh: '61', stop: '54', sizeMode: 'quantity', size: '1200' };
  const estimate = estimateMaxLoss(form);
  assert.equal(estimate.amount, '8400');
  assert.equal(estimate.currency, 'CNY');
  assert.equal(estimate.formula, '(入场价上限 61.00 − 止损价 54.00) × 计划股数 1200 = 8,400.00 CNY');
  // The formula shows the prices as typed (at least two decimals), the same numbers the amount uses.
  const fine = estimateMaxLoss({ ...form, entryHigh: '12.345', stop: '11', size: '100' });
  assert.equal(fine.formula, '(入场价上限 12.345 − 止损价 11.00) × 计划股数 100 = 134.50 CNY');
  // The lower bound when only that is given.
  assert.equal(estimateMaxLoss({ ...form, entryHigh: '' }).amount, '4800');
  assert.match(estimateMaxLoss({ ...form, entryHigh: '' }).formula, /入场价下限/);
  // A stop at or above the entry (an active plan's stop moved to breakeven or into profit): no loss, never a
  // negative one, and the gain the stop locks in.
  const raised = estimateMaxLoss({ ...form, stop: '63' });
  assert.deepEqual([raised.amount, raised.lockedGain], ['0', '2400']);
  assert.equal(raised.formula, '止损价 63.00 不低于入场价上限 61.00：触及止损时不亏损，按计划股数 1200 锁定盈利 (63.00 − 61.00) × 1200 = 2,400.00 CNY');
  assert.deepEqual([estimateMaxLoss({ ...form, stop: '61' }).amount, estimateMaxLoss({ ...form, stop: '61' }).lockedGain], ['0', '0']);
  // Not computable: weight mode, no stop, unknown ticker.
  assert.equal(estimateMaxLoss({ ...form, sizeMode: 'weight' }), null);
  assert.equal(estimateMaxLoss({ ...form, stop: '' }), null);
  assert.equal(estimateMaxLoss({ ...form, ticker: 'abc' }), null);
  assert.equal(estimateMaxLoss({ ...form, entryHigh: '', entryLow: '' }), null);
  assert.equal(estimateMaxLoss({ ...form, ticker: 'NASDAQ:AAPL' }).currency, 'USD');
  assert.deepEqual(['SHA:1', 'SHE:1', 'HKG:00700', 'NASDAQ:A', 'NYSE:B', 'XXX:1'].map(currencyOf), ['CNY', 'CNY', 'HKD', 'USD', 'USD', null]);
});

test('B shares: the form estimates the loss in the currency the share trades in, with exact decimals', () => {
  // As core.market.instruments: SHA 900xxx trade in USD, SHE 200xxx and 201xxx in HKD (six digits).
  assert.deepEqual(['SHA:900901', 'SHE:200012', 'SHE:201001', 'sha:900901 ', 'SHA:600519', 'SHE:000001', 'SHA:90090', 'SHE:2000123'].map(currencyOf),
    ['USD', 'HKD', 'HKD', 'USD', 'CNY', 'CNY', 'CNY', 'CNY']);
  const form = { ...emptyForm('2026-11-17'), entryHigh: '10', stop: '9', sizeMode: 'quantity', size: '100' };
  for (const [ticker, currency] of [['SHA:900901', 'USD'], ['SHE:200012', 'HKD']]) {
    const estimate = estimateMaxLoss({ ...form, ticker });
    assert.deepEqual([estimate.amount, estimate.currency], ['100', currency]);
    assert.equal(estimate.formula, `(入场价上限 10.00 − 止损价 9.00) × 计划股数 100 = 100.00 ${currency}`);
  }
  // Exact: no binary floats on the way (a float product would be 99999999999999983616 and 0.6000000000000001).
  const cn = { ...form, ticker: 'SHA:600519' };
  assert.equal(estimateMaxLoss({ ...cn, entryHigh: '100000000', stop: '0.00000001', size: '1000000000000' }).amount, '99999999999999990000');
  assert.equal(estimateMaxLoss({ ...cn, entryHigh: '0.3', stop: '0.1', size: '3' }).amount, '0.6');
  assert.equal(estimateMaxLoss({ ...cn, entryHigh: '61.005', stop: '54.1', size: '100' }).formula, '(入场价上限 61.005 − 止损价 54.10) × 计划股数 100 = 690.50 CNY');
  assert.equal(estimateMaxLoss({ ...cn, entryHigh: '1e1' }), null);
  assert.equal(estimateMaxLoss({ ...cn, size: '0' }), null);
});

function hkPlan(loss = {}) {
  return plan({
    ticker: 'HKG:00700', currency: 'HKD', max_loss_cny: '3680.00',
    max_loss: { amount: '4000.00', currency: 'HKD', amount_cny: '3680.00', fx_rate: '0.92', fx_status: 'stale', fx_source: 'eastmoney',
      fx_quote_time: '2026-11-17T07:00:00+08:00', estimated: true, estimated_reason: '汇率已超过 180 分钟未更新', entry_basis: 'entry_high',
      formula: '(入场价上限 40 − 止损价 36) × 计划股数 1000 = 4000.00 HKD；× 汇率 0.92 = 3680.00 CNY（估算：汇率已超过 180 分钟未更新）。', reason: null, ...loss },
  });
}

test('a converted max loss names its rate, and an unverified rate makes it 估算', () => {
  assert.deepEqual(maxLossView(hkPlan()), {
    text: '¥3,680.00', estimated: true, muted: false,
    note: '按 eastmoney HKD 汇率 0.92（2026-11-17 07:00）折算 HK$4,000.00；汇率已超过 180 分钟未更新',
  });
  const live = maxLossView(hkPlan({ fx_status: 'live', estimated: false, estimated_reason: null }));
  assert.deepEqual([live.text, live.estimated, live.note], ['¥3,680.00', false, '按 eastmoney HKD 汇率 0.92（2026-11-17 07:00）折算 HK$4,000.00']);
  const unknown = maxLossView(hkPlan({ fx_source: null, fx_quote_time: null }));
  assert.equal(unknown.note, '按 来源未知 HKD 汇率 0.92（时间未知）折算 HK$4,000.00；汇率已超过 180 分钟未更新');
  // A payload from before the rate fields: no mark is invented.
  const { fx_source: _s, fx_quote_time: _t, estimated: _e, estimated_reason: _r, ...old } = hkPlan().max_loss;
  assert.equal(maxLossView(plan({ currency: 'HKD', max_loss: old })).estimated, false);
  // CNY: nothing to add. Without a rate: the loss in its own currency, with the reason.
  assert.deepEqual(maxLossView(plan()), { text: '¥8,400.00', estimated: false, note: null, muted: false });
  assert.deepEqual(maxLossView(hkPlan({ amount_cny: null, fx_rate: null, fx_status: null, fx_source: null, fx_quote_time: null, estimated: false,
    estimated_reason: null, reason: '缺少 HKD 兑人民币汇率，人民币金额不可用' })),
  { text: 'HK$4,000.00', estimated: false, note: '缺少 HKD 兑人民币汇率，人民币金额不可用', muted: false });
});

test('a draft without stop, entry or quantity says 未填写 for its loss, never blank or 0', () => {
  const empty = { amount: null, currency: 'CNY', amount_cny: null, fx_rate: null, fx_status: null, fx_source: null, fx_quote_time: null,
    estimated: false, estimated_reason: null, entry_basis: null, formula: null };
  const noStop = plan({ status: 'draft', stop_price: null, max_loss: { ...empty, reason: '需要止损价才能计算最大亏损' }, max_loss_cny: null });
  assert.equal(UNFILLED, '未填写');
  assert.deepEqual(maxLossView(noStop), { text: '未填写', estimated: false, note: '需要止损价才能计算最大亏损', muted: true });
  const byWeight = plan({ planned_quantity: null, planned_weight_pct: '10', max_loss: { ...empty, reason: '需要计划股数才能计算最大亏损（计划按仓位比例填写时不计算）' } });
  assert.equal(maxLossView(byWeight).text, '未填写');
  assert.equal(maxLossView(plan({ entry_low: null, entry_high: null, max_loss: { ...empty, reason: '需要入场价才能计算最大亏损' } })).text, '未填写');
  // Everything filled in but no figure: unknown, not unfilled.
  assert.deepEqual(maxLossView(plan({ max_loss: { ...empty, reason: '暂时无法计算' } })), { text: '不可用', estimated: false, note: '暂时无法计算', muted: true });
});

test('one listing in any spelling: Hong Kong zero padding and US class shares', () => {
  assert.ok(sameListing('HKG:700', 'HKG:00700') && sameListing('hkg:0700', 'HKG:700'));
  assert.ok(sameListing('NYSE:BRK.B', 'NYSE:BRK-B') && sameListing('nasdaq:abc-d', 'NASDAQ:ABC.D'));
  assert.ok(!sameListing('NYSE:BRK.B', 'NYSE:BRK.A') && !sameListing('HKG:700', 'HKG:701') && !sameListing('SHA:600000', 'SHE:600000'));
  assert.ok(!sameListing('NYSE:BRK.B', 'NASDAQ:BRK.B'));
  assert.equal(listingKey(' HKG:700 '), 'HKG:00700');
});

test('the plan name: the ledger\'s for a holding, else the market block\'s, else none', () => {
  const names = new Map([[listingKey('HKG:00700'), '示例港股']]);
  assert.equal(planName(plan({ ticker: 'HKG:700' }), names, undefined), '示例港股');
  assert.equal(planName(plan(), new Map(), market()), '示例宽基');
  assert.equal(planName(plan(), new Map(), market({ name: null })), undefined);
  assert.equal(planName(plan(), new Map(), undefined), undefined);
});

test('buying from a plan opens the entry with the plan id, so the preview names the plan', () => {
  assert.deepEqual(buyDraft(plan({ account: '长期账户' }), '示例宽基'),
    { kind: 'buy', ticker: 'SHA:DEMO', name: '示例宽基', account: '长期账户', currency: 'CNY', planId: 7, source: 'plan' });
  assert.deepEqual(buyDraft(plan(), undefined),
    { kind: 'buy', ticker: 'SHA:DEMO', name: undefined, account: undefined, currency: 'CNY', planId: 7, source: 'plan' });
});

// -- the second review ------------------------------------------------------------------------------------

test('the Today chip: the plan of the row\'s listing and account; reached and distances are the backend\'s', () => {
  const near = market({ distance_to_stop_pct: '18.30' });
  const a = plan({ id: 1, ticker: 'HKG:700', account: 'A', market: market() });
  const b = plan({ id: 2, ticker: 'HKG:00700', account: 'B', stop_price: '50', market: near });
  assert.equal(planChipView([a, b], null, 'HKG:00700', 'A').planId, 1);
  assert.equal(planChipView([a, b], null, 'HKG:00700', 'B').planId, 2);
  assert.equal(planChipView([a, b], null, 'HKG:00700', 'C'), null, 'a plan for another account is not this row\'s');
  assert.equal(planChipView([plan({ id: 3, account: null, market: market() })], null, 'SHA:DEMO', 'C').planId, 3, 'a plan for every account');
  assert.equal(planChipView([plan({ status: 'draft', market: market() })], null, 'SHA:DEMO', 'A'), null, 'only active plans have a chip');
  const chip = planChipView([plan({ market: market() })], null, 'SHA:DEMO', 'A');
  assert.deepEqual([chip.text, chip.warn, chip.planId], ['计划 · 距止损 -11.76%', false, 7]);
  assert.match(chip.title, /^查看交易计划/);
  assert.match(chip.title, /10:32/);
  // Price 100 and stop 99.999 are 0.00 % apart but the stop is not reached: the backend's flag decides.
  const close = market({ price: '100', distance_to_stop: '0.001', distance_to_stop_pct: '0.00', stop_reached: false });
  assert.deepEqual([planChipView([plan({ stop_price: '99.999', market: close })], null, 'SHA:DEMO', 'A').text,
    planChipView([plan({ stop_price: '99.999', market: close })], null, 'SHA:DEMO', 'A').warn], ['计划 · 距止损 0.00%', true]);
  assert.equal(planChipView([plan({ market: market({ stop_reached: true, distance_to_stop_pct: '-1.00' }) })], null, 'SHA:DEMO', 'A').text, '计划 · 已触及止损');
  assert.equal(planChipView([plan({ stop_price: null, market: market() })], null, 'SHA:DEMO', 'A').text, '计划 · 距目标 +7.84%');
  assert.equal(planChipView([plan({ stop_price: null, market: market({ target_reached: true }) })], null, 'SHA:DEMO', 'A').text, '计划 · 已到目标');
  assert.equal(planChipView([plan({ stop_price: null, target_price: null, market: market() })], null, 'SHA:DEMO', 'A').text, '计划');
  // No price: says so, never a distance.
  const none = planChipView([plan({ market: market({ price: null, distance_to_stop_pct: null, stop_reached: null, price_reason: '停牌' }) })], null, 'SHA:DEMO', 'A');
  assert.deepEqual([none.text, none.warn], ['计划 · 距止损 不可用', false]);
  assert.match(none.title, /停牌/);
  // A stale quote: amber, with its source and time.
  const stale = planChipView([plan({ market: market({ quote: { status: 'stale', reason: '行情已超过 20 分钟未更新', source: 'fake', quote_time: '2026-11-17T09:40:00+08:00' } }) })], null, 'SHA:DEMO', 'A');
  assert.equal(stale.warn, true);
  assert.match(stale.title, /已过期 09:40/);
  // The plans cannot be read: the chip says so instead of disappearing; a failed refresh keeps the answer.
  assert.deepEqual(planChipView(undefined, '上游服务错误 (503)', 'SHA:DEMO', 'A'),
    { text: '计划 不可用', warn: true, planId: null, title: '交易计划读取失败：上游服务错误 (503)。点击重试' });
  assert.equal(planChipView(undefined, null, 'SHA:DEMO', 'A'), null, 'still loading');
  assert.equal(planChipView([plan({ market: market() })], 'x', 'SHA:DEMO', 'A').text, '计划 · 距止损 -11.76%');
});

test('an open plan\'s price: the backend\'s digits with source and time; amber when stale or when the refresh failed', () => {
  const today = '2026-11-17';
  const live = priceView(market(), 'CNY', null, today);
  assert.deepEqual([live.text, live.tone, live.label], ['61.20', 'neutral', null]);
  assert.match(live.title, /10:32/);
  const stale = priceView(market({ price: '3.456', quote: { status: 'stale', reason: null, source: 'fake', quote_time: '2026-11-17T09:40:00+08:00' } }), 'CNY', null, today);
  assert.deepEqual([stale.text, stale.tone, stale.label], ['3.456', 'warn', '已过期 09:40']);
  const failed = priceView(market(), 'CNY', { at: Date.parse('2026-11-17T10:30:00+08:00'), error: '上游服务错误 (503)' }, today);
  assert.deepEqual([failed.text, failed.tone, failed.label], ['61.20', 'warn', '可能不是最新']);
  assert.match(failed.title, /刷新失败：上游服务错误 \(503\)/);
  assert.match(failed.title, /10:30/);
  const none = priceView(market({ price: null, price_reason: '停牌' }), 'CNY', null, today);
  assert.deepEqual([none.text, none.tone, none.title], ['不可用', 'neutral', '停牌']);
  assert.equal(priceView(undefined, 'CNY', null, today).text, '不可用');
  assert.equal(priceView(market({ price: '382.4' }), 'HKD', null, today).text, 'HK$382.400');
});

test('a provisional US close is amber with its qualifier on the plan, as on Today', () => {
  // 16:12 New York: the 16:00 close (05:00 Beijing) is not final for 30 minutes; the backend marks the price estimated.
  const today = '2026-11-18';
  const quote = { status: 'closed', reason: null, source: 'fake', quote_time: '2026-11-17T21:00:00+00:00', fetched_at: null, session_date: '2026-11-17' };
  const reason = '收盘价尚未最终确定：交易所的官方收盘价在收盘后数分钟才发布，当地 16:30 之前看到的价格按估算处理';
  const pending = market({ price: '201.70', quote: { ...quote, reason }, price_estimated: true, price_estimated_reason: reason });
  const view = priceView(pending, 'USD', null, today);
  assert.deepEqual([view.text, view.tone, view.label], ['$201.70', 'warn', '收盘价 05:00']);
  assert.match(view.title, /收盘价尚未最终确定/);
  const settled = priceView(market({ price: '201.70', quote, price_estimated: false, price_estimated_reason: null }), 'USD', null, today);
  assert.deepEqual([settled.tone, settled.label], ['neutral', null]);
  const chip = planChipView([plan({ ticker: 'NASDAQ:EXAMPLE', market: pending })], null, 'NASDAQ:EXAMPLE', 'A');
  assert.equal(chip.warn, true);
  assert.match(chip.title, /收盘价尚未最终确定/);
});

test('plans due for review are listed on Today: active plans whose review date has come, oldest first', () => {
  const items = [plan({ id: 1, review_on: '2026-11-17' }), plan({ id: 2, review_on: '2026-11-18' }), plan({ id: 3, review_on: '2026-11-10', status: 'draft' }),
    plan({ id: 4, review_on: null }), plan({ id: 5, review_on: '2026-11-01' }), plan({ id: 6, review_on: '2026-11-02', status: 'closed' })];
  assert.deepEqual(duePlans(items, '2026-11-17').map((item) => item.id), [5, 1]);
  assert.deepEqual(duePlans(undefined, '2026-11-17'), []);
  assert.deepEqual(duePlans(items, null), []);
});

test('the phone summary says the stop is not filled in', () => {
  assert.equal(planSummary(plan({ stop_price: null })), '入场 58–61 · 止损未填写 · 目标 66 · 计划 1,200 股');
  assert.equal(planSummary(plan({ entry_low: null, entry_high: null, stop_price: null, target_price: null, planned_quantity: null })), '尚未填写价格和仓位');
});

test('/plans?ticker= shows the plans of that listing, and of an account when one is given', () => {
  const items = [plan({ id: 1, ticker: 'HKG:700', account: 'A' }), plan({ id: 2, ticker: 'HKG:00700', account: null }), plan({ id: 3, ticker: 'HKG:701' })];
  assert.deepEqual(plansOfListing(items, 'HKG:00700').map((item) => item.id), [1, 2]);
  assert.deepEqual(plansOfListing(items, 'hkg:0700', 'B').map((item) => item.id), [2]);
  assert.deepEqual(plansOfListing(items, null).map((item) => item.id), [1, 2, 3]);
});
