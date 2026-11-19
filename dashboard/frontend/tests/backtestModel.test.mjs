// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The backtest page's decisions: links into it (the WP6 URL contract), the form's request (untouched fields
// left out, percent typed and fraction sent), period presets from an injected today, the service's refusals
// next to their field, and the result view: every figure as computed or 不可用 with its reason, the run's
// currency, drawdowns as losses, every caveat, comparisons that may be missing, reruns, end marks, coverage.
// Shapes follow dashboard/backtesting/service.py (RunResult) and presets.py (describe()).
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  EMPTY_FORM, backtestHref, buildRunBody, changedSettings, checkNumber, feeDefaultsLine, formFromSpec, fractionToPercentText,
  hasPrefill, holdingLink, parseLink, percentToFraction, periodRange, planLink, prefillFromLink, refusalErrors, watchLink, yearsBefore,
} from '../src/features/backtest/model.ts';
import {
  allMetrics, blockedText, caveatTone, commissionText, comparisons, coverageProblems, coverageRows, drawdownTone, endMarkOf, endMarkPosition,
  adjustFact, cacheProvenance, downloadReport, feeFigure, feeParts, legacyView, LEGACY_RERUN, fillKind, gainTone, historyRows, historyView, keyFigures, nextHistoryOffset, markerCounts, markerLines, metricView, METRICS, monthlyRows, moneyFigure,
  moneyText, orderedCaveats, pctFigure, pctText, plainReading, priceFigure, priceSummary, progressSteps, rerunFindings, rowBlocked, splitRoundTrips, statusChip, triState, unfilledOutcome, unfilledReason,
} from '../src/features/backtest/result.ts';
import { chartData, drawdownPath, equityGeometry, indexAt, markerAt, priceData, priceGeometry } from '../src/features/backtest/chart.ts';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');

// GET /presets as presets.describe() serves it.
const PRESETS = [
  { id: 'buy_and_hold', name: '一直持有（对照）', summary: '第一个可交易日买入，一直拿到最后。', params: [] },
  { id: 'ma_trend', name: '均线趋势', summary: '收盘价站上 N 日均线就买入，跌破就卖出。',
    params: [{ key: 'period', label: '均线天数', unit: '日', default: 20, min: 2, max: 250, step: 1 }] },
  { id: 'rsi_rebound', name: '超跌反弹', summary: 'RSI(14) 低于 30 买入。', params: [
    { key: 'buy_below', label: 'RSI 低于多少时买入', unit: '点', default: 30, min: 5, max: 50, step: 1 },
    { key: 'sell_above', label: 'RSI 高于多少时卖出', unit: '点', default: 70, min: 50, max: 95, step: 1 },
  ] },
  { id: 'stop_target', name: '止损止盈（持仓）', summary: '在指定日期买入，亏 8% 止损。', params: [
    { key: 'entry_date', label: '买入日期', unit: 'date', default: null, min: null, max: null, step: null },
    { key: 'stop_pct', label: '亏损多少止损（0 表示不用）', unit: '%', default: 8, min: 0, max: 50, step: 0.5 },
    { key: 'target_pct', label: '盈利多少止盈（0 表示不用）', unit: '%', default: 20, min: 0, max: 500, step: 1 },
    { key: 'trailing_pct', label: '从高点回落多少离场（0 表示不用）', unit: '%', default: 10, min: 0, max: 50, step: 0.5 },
  ] },
];
const CONTEXT = { presets: PRESETS, strategies: [
  { id: 'dual_ma', name: 'DualMa', class: 'DualMaStrategy', parameters: { fast_period: 5, slow_period: 20, use_atr: false, label: 'x' },
    description: '双均线', builtin: true, file: '' },
] };
const form = (patch = {}) => ({ ...EMPTY_FORM, ticker: 'SHA:DEMO', method: { kind: 'preset', id: 'ma_trend' }, ...patch });

// -- links (WP6) ----------------------------------------------------------------------------------

test('a link is read by the documented keys; unknown keys are parameters, junk is dropped', () => {
  const link = parseLink('?ticker=sha:demo&preset=stop_target&entry=2025-03-03&from=holding&stop_pct=8&capital=50000&from2=&period=5y');
  assert.equal(link.ticker, 'SHA:DEMO');
  assert.equal(link.preset, 'stop_target');
  assert.equal(link.entry, '2025-03-03');
  assert.equal(link.from, 'holding');
  assert.equal(link.capital, '50000');
  assert.equal(link.period, '5y');
  assert.deepEqual(link.params, { stop_pct: '8' });
  assert.equal(parseLink('?from=elsewhere&period=9y&tab=nope').from, null);
  assert.equal(parseLink('?period=9y').period, null);
  assert.equal(parseLink('?tab=nope').tab, null);
  assert.equal(parseLink('?run=abc').run, 'abc');
  assert.ok(hasPrefill(parseLink('?ticker=SHA:DEMO')));
  assert.ok(!hasPrefill(parseLink('?run=abc&tab=history')));
});

test('a link prefills the form: the preset, its date parameter from entry, given parameters, capital; it says where it came from', () => {
  const { form: filled, notes } = prefillFromLink(parseLink('?ticker=SHA:DEMO&preset=stop_target&entry=2025-03-03&from=holding&stop_pct=8&capital=50000'), CONTEXT);
  assert.deepEqual(filled.method, { kind: 'preset', id: 'stop_target' });
  assert.equal(filled.params.entry_date, '2025-03-03');
  assert.equal(filled.params.stop_pct, '8');
  assert.equal(filled.params.target_pct, undefined, 'untouched parameters stay empty (the default)');
  assert.equal(filled.capital, '50000');
  assert.equal(filled.ticker, 'SHA:DEMO');
  assert.deepEqual(notes, ['来自持仓']);
  // An unknown preset falls back to the default method and says so; a parameter of another preset is ignored.
  const other = prefillFromLink(parseLink('?ticker=SHA:DEMO&preset=nope&stop_pct=8'), CONTEXT);
  assert.deepEqual(other.form.method, { kind: 'preset', id: 'ma_trend' });
  assert.deepEqual(other.form.params, {});
  assert.match(other.notes[0], /nope/);
});

test('holding, watch and plan links; a plan’s absolute stop and target become % of its entry price, shown', () => {
  assert.equal(holdingLink('SHA:DEMO', '50000'), '/backtest?ticker=SHA%3ADEMO&preset=ma_trend&from=holding&capital=50000');
  assert.equal(holdingLink('SHA:DEMO', null), '/backtest?ticker=SHA%3ADEMO&preset=ma_trend&from=holding');
  assert.equal(holdingLink('SHA:DEMO', '0'), '/backtest?ticker=SHA%3ADEMO&preset=ma_trend&from=holding');
  assert.equal(watchLink('HKG:00700'), '/backtest?ticker=HKG%3A00700&preset=ma_trend&from=watch');
  assert.equal(backtestHref({ run: 'r1', ticker: '' }), '/backtest?run=r1');
  const href = planLink({ id: 7, ticker: 'SHA:DEMO', entry_low: '58', entry_high: '61', stop_price: '54', target_price: '66', entry_day: '2026-11-17' });
  const link = parseLink(href.split('?')[1]);
  assert.equal(link.entryPrice, '59.5', 'the middle of the entry range, exactly');
  assert.equal(link.plan, '7');
  assert.equal(link.entry, '2026-11-17');
  const { form: filled, conversions, notes } = prefillFromLink(link, CONTEXT);
  assert.equal(filled.params.stop_pct, '9.24');
  assert.equal(filled.params.target_pct, '10.92');
  assert.equal(filled.params.entry_date, '2026-11-17');
  assert.match(conversions.stop_pct, /止损价 54 相对计划买入价 59.5 为 9.24%/);
  assert.match(conversions.target_pct, /10.92%/);
  assert.match(notes[0], /交易计划（计划 #7）/);
  // A stop above the entry is not converted, and the reason is shown.
  const odd = prefillFromLink(parseLink(planLink({ id: 1, ticker: 'SHA:DEMO', entry_low: '50', entry_high: null, stop_price: '55', target_price: null, entry_day: null }).split('?')[1]), CONTEXT);
  assert.equal(odd.form.params.stop_pct, undefined);
  assert.match(odd.conversions.stop_pct, /没有换算/);
});

test('the URL contract is documented at the top of the model', () => {
  const head = readFileSync(join(SRC, 'features', 'backtest', 'model.ts'), 'utf8').slice(0, 2500);
  for (const key of ['ticker=EXCH:CODE', 'preset=<id>', 'entry=<YYYY-MM-DD>', 'from=holding|plan|watch', 'run=<id>', 'never starts a run']) {
    assert.ok(head.includes(key), key);
  }
});

// -- the request ----------------------------------------------------------------------------------

test('untouched fields are left out of the request: the service applies its defaults', () => {
  assert.deepEqual(buildRunBody(form(), CONTEXT), { ok: true, body: { ticker: 'SHA:DEMO', preset: 'ma_trend' } });
  const body = buildRunBody(form({ params: { period: '30' }, period: '1y' }), CONTEXT);
  assert.deepEqual(body, { ok: true, body: { ticker: 'SHA:DEMO', preset: 'ma_trend', params: { period: 30 }, period: '1y' } });
  assert.ok(!('fee_basis' in body.body), 'RunSpec has no fee_basis: the commission fields are commission only');
});

test('percentages are typed in percent and sent as fractions; advanced settings only when set', () => {
  const built = buildRunBody(form({
    capital: '50000', commissionBuy: '0.025', commissionSell: '0.03', minCommission: '5', slippage: '0.1', lotSize: '200',
    adjust: 'hfq', benchmark: 'custom', benchmarkCode: 'idx:000905',
  }), CONTEXT);
  assert.ok(built.ok);
  assert.deepEqual(built.body, {
    ticker: 'SHA:DEMO', preset: 'ma_trend', capital: 50000, commission_buy: 0.00025, commission_sell: 0.0003, min_commission: 5,
    slippage_pct: 0.001, lot_size: 200, adjust: 'hfq', benchmark: 'IDX:000905',
  });
  assert.equal(percentToFraction('0.025'), 0.00025);
  assert.equal(fractionToPercentText(0.00025), '0.025');
  assert.equal(fractionToPercentText(0.0005), '0.05');
  assert.equal(fractionToPercentText(null), '');
  const none = buildRunBody(form({ adjust: 'none', allowUnadjusted: true, benchmark: 'none' }), CONTEXT);
  assert.deepEqual(none.body, { ticker: 'SHA:DEMO', preset: 'ma_trend', adjust: 'none', allow_unadjusted: true, benchmark: 'none' });
});

test('a custom period sends its dates; a strategy sends only the parameters typed, by their own type', () => {
  const custom = buildRunBody(form({ period: 'custom', start: '2024-01-02', end: '2025-01-02' }), CONTEXT);
  assert.deepEqual(custom.body, { ticker: 'SHA:DEMO', preset: 'ma_trend', period: 'custom', start_date: '2024-01-02', end_date: '2025-01-02' });
  const strategy = buildRunBody(form({ method: { kind: 'strategy', id: 'dual_ma' }, params: { fast_period: '8', use_atr: 'true', label: 'y', slow_period: '' } }), CONTEXT);
  assert.deepEqual(strategy.body, { ticker: 'SHA:DEMO', strategy: 'dual_ma', params: { fast_period: 8, use_atr: true, label: 'y' } });
});

test('the form refuses in Chinese, next to the field, before anything is sent', () => {
  const bad = buildRunBody(form({
    ticker: ' ', params: { period: '300' }, period: 'custom', start: '', capital: '0', commissionBuy: '2', slippage: '5', lotSize: '1.5',
    benchmark: 'custom', benchmarkCode: '',
  }), CONTEXT);
  assert.equal(bad.ok, false);
  assert.match(bad.errors.ticker, /请填写要回测的标的/);
  assert.match(bad.errors['param:period'], /均线天数应在 2 日 到 250 日 之间/);
  assert.match(bad.errors.start, /开始日期/);
  assert.match(bad.errors.capital, /本金必须大于 0/);
  assert.match(bad.errors.commissionBuy, /买入佣金率必须小于 1%/);
  assert.match(bad.errors.slippage, /滑点必须小于 5%/);
  assert.match(bad.errors.lotSize, /每手股数应为整数/);
  assert.match(bad.errors.benchmark, /指数代码/);
  const date = buildRunBody(form({ method: { kind: 'preset', id: 'stop_target' }, params: { entry_date: '2025-02-30' } }), CONTEXT);
  assert.match(date.errors['param:entry_date'], /YYYY-MM-DD/);
  const order = buildRunBody(form({ period: 'custom', start: '2025-01-02', end: '2024-01-02' }), CONTEXT);
  assert.match(order.errors.end, /晚于开始日期/);
  assert.equal(checkNumber('abc', { label: 'x' }), 'x应为数字');
  assert.equal(checkNumber('', { label: 'x', min: 1 }), null, 'empty is the default');
});

test('period presets are counted back from an injected today, 29 February as the service counts it', () => {
  assert.deepEqual(periodRange('3y', '2026-11-19'), { start: '2023-11-19', end: '2026-11-19' });
  assert.deepEqual(periodRange('1y', '2026-11-19'), { start: '2025-11-19', end: '2026-11-19' });
  assert.deepEqual(periodRange('5y', '2026-11-19'), { start: '2021-11-19', end: '2026-11-19' });
  assert.equal(yearsBefore('2028-02-29', 1), '2027-02-28');
  assert.equal(yearsBefore('2028-02-29', 4), '2024-02-29');
  assert.deepEqual(periodRange('custom', '2026-11-19', { start: '2024-01-02', end: '' }), { start: '2024-01-02', end: '2026-11-19' });
});

test('the service’s refusals go next to their field; a server error is never a field’s fault', () => {
  const params = PRESETS[1].params;
  assert.deepEqual(refusalErrors({ status: 422, message: '「均线天数」应为整数, 收到 20.5', errors: [] }, params),
    { 'param:period': '「均线天数」应为整数, 收到 20.5' });
  assert.deepEqual(refusalErrors({ status: 422, message: '本金必须大于 0', errors: [{ field: 'capital', message: '本金必须大于 0' }] }, params),
    { capital: '本金必须大于 0' });
  assert.deepEqual(refusalErrors({ status: 403, message: '演示模式只能回测演示持仓里的标的：SHA:DEMO', errors: [] }, params),
    { ticker: '演示模式只能回测演示持仓里的标的：SHA:DEMO' });
  assert.deepEqual(refusalErrors({ status: 422, message: '对比基准: 不认识的代码', errors: [] }, params), { benchmark: '对比基准: 不认识的代码' });
  assert.deepEqual(refusalErrors({ status: 422, message: '开始日期 2026-11-19 必须早于结束日期 2026-11-19', errors: [] }, params),
    { start: '开始日期 2026-11-19 必须早于结束日期 2026-11-19' });
  const entry = PRESETS[3].params;
  assert.deepEqual(Object.keys(refusalErrors({ status: 422, message: '买入日期 2030-01-01 晚于回测结束日期 2026-11-19', errors: [] }, entry)), ['param:entry_date']);
  assert.deepEqual(refusalErrors({ status: 500, message: '预设方法出错，已记录', errors: [] }, params), { form: '预设方法出错，已记录' });
  assert.deepEqual(refusalErrors({ status: undefined, message: '请求超时，请稍后重试', errors: [] }, params), { form: '请求超时，请稍后重试' });
  assert.deepEqual(refusalErrors({ status: 422, message: '说不清楚的问题', errors: [] }, params), { form: '说不清楚的问题' });
});

// Gate review G4d item 2: a saved 不复权 run reopened and changed ran 前复权 — the service stores the raw
// adjustment as an empty string, which the form did not read back.
test('a saved run without adjustment (stored as "") reopens 不复权 and runs 不复权 again', () => {
  const raw = formFromSpec({ ticker: 'SHA:600519', strategy: { kind: 'preset', id: 'ma_trend', params: { period: 20 } },
    period: { preset: '3y' }, adjust: '', allow_unadjusted: true });
  assert.equal(raw.adjust, 'none');
  assert.equal(raw.allowUnadjusted, true);
  const built = buildRunBody({ ...raw, params: { period: '30' } }, CONTEXT);
  assert.equal(built.ok, true);
  assert.equal(built.body.adjust, 'none');
  assert.equal(built.body.allow_unadjusted, true);
  assert.equal(formFromSpec({ ticker: 'X', adjust: 'none' }).adjust, 'none');
  assert.equal(formFromSpec({ ticker: 'X', adjust: 'hfq' }).adjust, 'hfq');
  assert.equal(formFromSpec({ ticker: 'X' }, { ...EMPTY_FORM, adjust: 'hfq' }).adjust, 'hfq', 'a spec without the field keeps the base');
});

// Gate review G4d item 8 (known limitation): equal RSI thresholds were refused at form level; the
// message is about the two threshold fields, so it is shown next to both.
test('a refusal about two parameters together is shown next to each of them', () => {
  const message = '买入的 RSI 线 (30) 应低于卖出的 RSI 线 (30)';
  assert.deepEqual(refusalErrors({ status: 422, message, errors: [] }, PRESETS[2].params),
    { 'param:buy_below': message, 'param:sell_above': message });
  // Another method has no such fields: the message stays at form level.
  assert.deepEqual(refusalErrors({ status: 422, message, errors: [] }, PRESETS[1].params), { form: message });
});

test('the defaults line names the market commission and what is charged on top; edits are listed', () => {
  assert.equal(
    feeDefaultsLine({ currency: 'HKD', commission: { rate: 0.00025, min: 5, source: 'market_default' }, taxes: [{ id: 'stamp_duty', name: '印花税', rate: 0.001, sides: ['buy', 'sell'], side_label: '双边' }] }),
    '留空按市场默认：佣金 0.025%，每笔最低 5 港元；双边印花税按成交日的法定费率另计，不含在佣金里',
  );
  assert.match(feeDefaultsLine({ currency: 'USD', commission: { rate: 0, min: 0, source: 'market_default' }, taxes: [] }), /佣金 0；这个市场没有另计的印花税/);
  assert.match(feeDefaultsLine(undefined), /留空按市场默认佣金/);
  assert.deepEqual(changedSettings(form({ capital: '50000', slippage: '0.1', benchmark: 'none' }), 'USD'), ['本金 50000 美元', '滑点 0.1%', '不对比指数']);
  assert.deepEqual(changedSettings(form(), 'CNY'), []);
  // A run opened from history carries the market's own capital and slippage: not listed as edits.
  const market = { capital: 100000, slippage_pct: 0.0005 };
  assert.deepEqual(changedSettings(form({ capital: '100000', slippage: '0.05' }), 'USD', market), []);
  assert.deepEqual(changedSettings(form({ capital: '100', slippage: '0.05' }), 'HKD', market), ['本金 100 港元']);
});

test('a finished run’s spec fills an empty form to change a parameter and run again', () => {
  const filled = formFromSpec({ ticker: 'SHA:DEMO', strategy: { kind: 'preset', id: 'ma_trend', params: { period: 30 } }, period: { preset: '1y', start: '2025-11-19', end: '2026-11-18' } });
  assert.equal(filled.ticker, 'SHA:DEMO');
  assert.deepEqual(filled.method, { kind: 'preset', id: 'ma_trend' });
  assert.deepEqual(filled.params, { period: '30' });
  assert.equal(filled.period, '1y');
  assert.equal(formFromSpec(null, EMPTY_FORM), EMPTY_FORM);
  // The settings the run used come along, so changing one parameter keeps the rest; defaults left null stay empty.
  const settings = formFromSpec({
    ticker: 'HKG:00700', strategy: { kind: 'preset', id: 'ma_trend', params: {} }, period: { preset: '3y' },
    capital: 100, commission_buy: 0.0003, commission_sell: null, min_commission: null, slippage_pct: 0.0005, lot_size: null,
    adjust: 'qfq', allow_unadjusted: false, benchmark: 'IDX:HSCEI',
  });
  assert.equal(settings.capital, '100');
  assert.equal(settings.commissionBuy, '0.03');
  assert.equal(settings.commissionSell, '');
  assert.equal(settings.minCommission, '');
  assert.equal(settings.slippage, '0.05');
  assert.equal(settings.lotSize, '');
  assert.equal(settings.benchmark, 'custom');
  assert.equal(settings.benchmarkCode, 'IDX:HSCEI');
  assert.equal(formFromSpec({ ticker: 'X', benchmark: 'none' }).benchmark, 'none');
});

// -- the result -----------------------------------------------------------------------------------

function result(overrides = {}) {
  const base = {
    run_id: 'r1',
    facts: {
      ticker: 'NASDAQ:DEMO', name: '示例研究标的', currency: 'USD', kind: 'stock', market_label: '美股',
      strategy: { kind: 'preset', id: 'ma_trend', name: '均线趋势' },
      period: { first_date: '2023-11-20', last_date: '2026-11-18', span_label: '3 年' },
      capital: 2000, final_value: 2364.69, total_return: 0.182345, annualized_return: null, same_stock_return: null,
      same_stock_reason: '按同样的本金 2,000.00 美元、每手股数和费用，一直持有 NASDAQ:DEMO 也买不进，没有“一直持有”的对照收益；这一栏是不可用，不是 0。',
      index_ticker: 'IDX:SPX', index_name: '标普500', index_return: null, index_reason: '标普500 在 2024-07-04 没有行情，没有和指数比较。',
      max_drawdown: -0.141, closed_trades: 9, winning_trades: 4, fees_total: 12.5, lot: { minimum: 1, step: 1, assumed: false, override: null },
      commission: { buy: null, sell: null, min: null, default_rate: 0, default_min: 0, defaulted: true, sell_rate_includes: [] }, slippage_pct: 0.0005,
    },
    headline_zh: '2023-11-20 至 2026-11-18（3 年），这个方法收益 +18.2%。',
    caveats: [
      { code: 'in_sample', level: 'info', text: '参数是在这同一段历史上看的结果' },
      { code: 'few_trades', level: 'warning', text: '只有 9 笔完整交易' },
      { code: 'unverified_splice', level: 'warning', text: '缓存在 2024-01-02 拼接' },
      { code: 'in_sample', level: 'info', text: '第二条同代码的提醒也要显示' },
    ],
    metrics: {
      total_return: 0.182345, annualized_return: null, volatility: null, sharpe_ratio: 1.2345, max_drawdown: -0.141, avg_loss: null,
      avg_profit: 31.25, max_single_loss: -40.5, win_rate: 0.4444, closed_trades: 9, final_value: 2364.69, mystery_ratio: 2.5,
      undefined: { annualized_return: '年化会溢出，没有给出', volatility: '少于 2 个净值点, 没有日收益', avg_loss: '没有亏损的完整交易' },
    },
    metric_notes: { volatility: '日收益的标准差乘以 √252。' },
    equity: [
      { date: '2023-11-20', total_value: 2000, net_value: 1, drawdown: 0 },
      { date: '2023-11-21', total_value: 2100, net_value: 1.05, drawdown: 0 },
      { date: '2023-11-22', total_value: 1900, net_value: 0.95, drawdown: -0.095238 },
    ],
    benchmark: { same_stock: { total_return: null, curve: [], reason: '买不进' }, index: null },
    round_trips: [], fills: [], unfilled: [],
    manifest: { coverage: [
      { ticker: 'NASDAQ:DEMO', adjust: 'qfq', source: 'demo.synthetic', first: '2023-08-01', last: '2026-11-18', rows: 830, complete: true, offline: false, fetched_at: '2026-11-19T09:00:00', sha256: 'abc' },
    ] },
  };
  return { ...base, ...overrides, facts: { ...base.facts, ...(overrides.facts ?? {}) } };
}

test('figures keep the service’s digits; unknown is 不可用, never 0; money in the run’s currency', () => {
  assert.equal(pctText(0.182345), '+18.2345%');
  assert.equal(pctText(-0.064), '-6.40%');
  assert.equal(pctText(0), '0.00%');
  assert.equal(pctText(null), '不可用');
  assert.equal(pctText(undefined), '不可用');
  assert.equal(moneyText(2364.69, 'USD'), '$2,364.69');
  assert.equal(moneyText(118234.5, 'HKD'), 'HK$118,234.50');
  assert.equal(moneyText(-40.5, 'CNY', { signed: true }), '-¥40.50');
  assert.equal(moneyText(null, 'USD'), '不可用');
  // A float past 8 places as shown (a slipped fill price, 0.1 + 0.2): ≈ rounded to 8, the full value kept for the title.
  assert.deepEqual(priceFigure(10.0123456789, { currency: 'CNY', kind: 'stock' }),
    { text: '≈10.01234568', full: '10.0123456789', label: '约 10.01234568，完整值 10.0123456789' });
  assert.equal(pctFigure(0.1 + 0.2).text, '≈+30.00%', 'counted in percent, as shown, padded to two places');
  assert.equal(pctFigure(0.1 + 0.2).full, '+30.000000000000004%');
  assert.equal(pctText(0.1234567891), '+12.34567891%', 'eight places in percent: every digit');
  assert.equal(moneyFigure(0.1 + 0.2, 'CNY').text, '≈¥0.30');
  assert.equal(feeFigure({ fees: { commission: 5, stamp_duty: 0.1 + 0.2 } }, 'CNY').text.endsWith('≈¥0.30'), true);
  assert.match(feeFigure({ fees: { commission: 5, stamp_duty: 0.1 + 0.2 } }, 'CNY').full, /¥0\.30000000000000004$/);
  const figures = Object.fromEntries(keyFigures(result()).map((item) => [item.key, item]));
  assert.equal(figures.total_return.value, '+18.2345%');
  assert.equal(figures.total_return.tone, 'up');
  assert.equal(figures.annualized_return.value, null, 'a null annualised figure is 不可用');
  assert.equal(figures.annualized_return.reason, '年化会溢出，没有给出');
  assert.equal(figures.max_drawdown.value, '-14.10%');
  assert.equal(figures.max_drawdown.tone, 'down', 'a drawdown is a loss: green');
  assert.equal(figures.final_value.value, '$2,364.69', 'a USD run never shows ¥');
  assert.equal(figures.fees_total.value, '$12.50');
  assert.equal(figures.closed_trades.value, '9 笔');
  assert.equal(figures.closed_trades.sub, '盈利 4 笔');
});

test('every metric is its number or 不可用 with the reason; volatility unavailable is not 0.00%', () => {
  const r = result();
  const volatility = metricView(r, METRICS.find((item) => item.key === 'volatility'));
  assert.equal(volatility.value, null);
  assert.equal(volatility.reason, '少于 2 个净值点, 没有日收益');
  assert.equal(volatility.note, '日收益的标准差乘以 √252。');
  const listed = Object.fromEntries(allMetrics(r).map((item) => [item.key, item]));
  assert.equal(listed.avg_loss.value, null, 'a null average loss is 不可用, never ¥undefined');
  assert.equal(listed.avg_profit.value, '+$31.25');
  assert.equal(listed.max_single_loss.tone, 'down');
  assert.equal(listed.sharpe_ratio.value, '1.2345');
  assert.equal(listed.sharpe_ratio.tone, 'flat');
  assert.equal(listed.win_rate.value, '44.44%');
  assert.equal(listed.max_drawdown.tone, 'down');
  assert.equal(listed.mystery_ratio.value, '2.50', 'a metric the page does not know is still shown');
  assert.ok(!('undefined' in listed));
  assert.equal(drawdownTone(0.2), 'down', 'whatever sign a drawdown has, it is a loss');
  assert.equal(drawdownTone(0), 'flat');
  assert.equal(gainTone(null), 'flat');
});

test('comparisons: 一直持有 and the index can be 不可用 with the service’s reason; no index chosen says so', () => {
  const [strategy, same, index] = comparisons(result());
  assert.equal(strategy.value, '+18.2345%');
  assert.match(strategy.label, /均线趋势/);
  assert.equal(same.value, null);
  assert.match(same.reason, /买不进/);
  assert.equal(index.label, '标普500');
  assert.equal(index.value, null);
  assert.match(index.reason, /2024-07-04 没有行情/);
  const none = comparisons(result({ facts: { index_ticker: null, index_name: null, index_reason: null } }))[2];
  assert.equal(none.reason, '这次没有选择对比指数');
  const both = comparisons(result({ facts: { same_stock_return: -0.064, index_return: 0.098 } }));
  assert.equal(both[1].value, '-6.40%');
  assert.equal(both[1].tone, 'down');
  assert.equal(both[2].value, '+9.80%');
});

test('every caveat is kept (warnings first, none dropped), warnings amber, unknown codes shown as sent', () => {
  const list = orderedCaveats(result().caveats);
  assert.deepEqual(list.map((item) => item.code), ['few_trades', 'unverified_splice', 'in_sample', 'in_sample']);
  assert.equal(caveatTone(list[0]), 'warn');
  assert.equal(caveatTone(list[2]), 'info');
  assert.deepEqual(orderedCaveats(null), []);
});

test('a rerun: same data / same strategy as 相同 / 不同 / 无法核对, and its caveats in plain words', () => {
  assert.deepEqual(triState(true), { text: '相同', tone: 'neutral' });
  assert.deepEqual(triState(false), { text: '不同', tone: 'warn' });
  assert.deepEqual(triState(null), { text: '无法核对', tone: 'warn' });
  assert.deepEqual(triState(undefined), { text: '无法核对', tone: 'warn' });
  const findings = rerunFindings({ caveats: [
    { code: 'strategy_changed', level: 'warning', text: '策略「均线趋势」与保存时不同（源码 abcd → efgh）' },
    { code: 'settings_unverified', level: 'warning', text: '对比指数无法与保存时核对' },
    { code: 'runtime_changed', level: 'info', text: '运行环境的版本不同' },
    { code: 'legacy_fees_translated', level: 'info', text: '旧版请求的卖出佣金率 0.13%' },
    { code: 'few_trades', level: 'warning', text: '只有 2 笔' },
  ] });
  assert.deepEqual(findings.map((item) => item.code), ['strategy_changed', 'settings_unverified', 'runtime_changed', 'legacy_fees_translated']);
  assert.equal(findings[0].title, '策略和保存时不一样');
  assert.match(findings[1].title, /无法核对费用和交易规则/);
  assert.equal(findings[0].tone, 'warn');
  for (const code of ['strategy_changed', 'strategy_unverified', 'data_unverified', 'settings_changed', 'runtime_changed', 'legacy_fees_translated']) {
    assert.ok(rerunFindings({ caveats: [{ code, level: 'info', text: 't' }] }).length === 1, code);
  }
});

// Gate review G4d item 3: an HK buy-and-hold had 0 completed trades in the facts but one 完整交易 row
// with a 卖出日 and 卖出均价 — the end valuation of what was still held, shown as a sale.
test('round trips: what was still held at the end is apart from the completed trades, which match the facts', () => {
  const closed = { entry_date: '2024-01-02', exit_date: '2024-02-01', quantity: 100, entry_price: 10, exit_price: 11, pnl: 100,
    return_pct: 0.1, holding_days: 30, exit_reason: 'signal' };
  const held = { entry_date: '2024-03-01', exit_date: '2026-11-18', quantity: 200, entry_price: 300, exit_price: 320, pnl: 4000,
    return_pct: 0.0667, holding_days: 627, exit_reason: 'end_mark', exit_reason_zh: '期末估值', end_mark: true, blocked: null };
  const flaggedByReasonOnly = { ...held, end_mark: undefined };
  assert.deepEqual(splitRoundTrips([held]), { closed: [], held: [held] }, 'buy-and-hold: nothing completed');
  assert.deepEqual(splitRoundTrips([closed, held]), { closed: [closed], held: [held] });
  assert.deepEqual(splitRoundTrips([flaggedByReasonOnly]).held, [flaggedByReasonOnly]);
  assert.deepEqual(splitRoundTrips(undefined), { closed: [], held: [] });
  // The 完整交易 table lists the completed ones only, the end valuation has its own table.
  const tables = readFileSync(join(SRC, 'components', 'backtest', 'ResultTables.tsx'), 'utf8');
  assert.match(tables, /splitRoundTrips\(/);
  assert.match(tables, /期末仍持有/);
});

test('an end mark is a valuation, not a sale; a blocked one shows the service’s sentence', () => {
  const sentence = '这些股票是最后一天买入的（A 股 T+1，当天不能卖出），实际卖不出，不能按这个价格成交';
  const endMark = { date: '2026-11-18', pnl: 5, positions: [{ symbol: 'SHA:DEMO', quantity: 200, blocked: 't_plus_one', blocked_quantity: 100, blocked_zh: sentence }] };
  assert.equal(endMarkOf({ facts: { end_mark: endMark } }), endMark);
  assert.equal(endMarkOf({ end_mark: endMark }), endMark);
  assert.equal(endMarkOf({ facts: { end_mark: null } }), null);
  assert.equal(endMarkOf({}), null);
  assert.equal(blockedText(endMark.positions[0]), sentence, 'blocked_zh is shown as the service wrote it');
  assert.equal(blockedText({ blocked: 't_plus_one', quantity: 200, blocked_quantity: 100 }), '实际卖不出：最后一天买入，A 股 T+1 当天不能卖出（其中 100 股）');
  assert.equal(blockedText({ blocked: 'limit_down', quantity: 200, blocked_quantity: 200 }), '实际卖不出：最后一天一字跌停');
  assert.equal(blockedText({ blocked: null }), null);
  assert.equal(endMarkPosition(endMark, 'SHA:DEMO'), endMark.positions[0]);
  // The row's own fields first (RunResult.fills[] / round_trips[]), the facts' end mark second.
  const own = fillKind({ direction: 'sell', reason: 'end_mark', end_mark: true, symbol: 'SHA:DEMO', quantity: 200, blocked: 'limit_down',
    blocked_zh: '最后一天一字跌停，实际卖不出，不能按这个价格成交' }, endMark);
  assert.deepEqual(own, { text: '期末估值（没有卖出）', mark: true, blocked: '最后一天一字跌停，实际卖不出，不能按这个价格成交' });
  const fromFacts = fillKind({ direction: 'sell', reason: 'end_mark', symbol: 'SHA:DEMO', quantity: 200 }, endMark);
  assert.equal(fromFacts.blocked, sentence);
  assert.equal(fillKind({ direction: 'sell', end_mark: true, blocked: null, symbol: 'X', quantity: 1 }, null).blocked, null);
  assert.equal(rowBlocked({ symbol: 'SHA:DEMO', quantity: 200, blocked: null }, null), null);
  assert.deepEqual(fillKind({ direction: 'sell', reason: 'signal', end_mark: false }, endMark), { text: '卖出', mark: false, blocked: null });
});

test('tables: fees by name in the run’s currency; an unfilled order always says why', () => {
  assert.equal(feeParts({ fees: { commission: 5, stamp_duty: 8.4 } }, 'CNY'), '佣金 ¥5.00 · 印花税 ¥8.40');
  assert.equal(feeParts({ fees: {}, commission: 1.2 }, 'USD'), '佣金 $1.20');
  assert.equal(unfilledReason({ reason: 'limit_up', reason_zh: '涨停买不进' }), '涨停买不进');
  assert.equal(unfilledReason({ reason: 'lot_unaffordable' }), '一手超过可用资金');
  assert.equal(unfilledReason({ reason: 'something_new' }), 'something_new');
  assert.equal(unfilledReason({}), '服务没有给出原因');
  assert.equal(unfilledOutcome({ outcome: 'expired' }), '过期作废');
  assert.equal(unfilledOutcome({ outcome: null }), '不可用');
});

test('data coverage: source in words, completeness, and a splice or dropped bars named', () => {
  const rows = coverageRows(result());
  assert.equal(rows[0].source, '演示数据（程序生成，不是真实行情）');
  assert.equal(rows[0].complete, '完整');
  assert.equal(rows[0].range, '2023-08-01 至 2026-11-18');
  assert.equal(rows[0].fetchedAt, '2026-11-19 09:00:00');
  const problems = coverageProblems({ ticker: 'SHA:600519', splice_seams: ['2024-01-02'], unverified: ['2023-12-29'], dropped_provisional: ['2026-11-19'] });
  assert.equal(problems.length, 3);
  assert.match(problems[0], /2024-01-02/);
  const flagged = coverageRows(result({ manifest: { coverage: [{ ticker: 'X', complete: true, splice_seams: ['2024-01-02'] }] } }));
  assert.equal(flagged[0].completeTone, 'warn');
  // Today's bar left out before the close is a fact about the data, not a problem.
  const today = coverageRows(result({ manifest: { coverage: [{ ticker: 'X', complete: true, provisional_last_bar: true }] } }));
  assert.equal(today[0].completeTone, 'neutral');
  assert.deepEqual(today[0].problems, []);
  assert.match(today[0].notes[0], /今天还没收盘/);
});

// Gate review G4d item 6: 更新到今天 said bars were saved under 前复权 while the 不复权 series the
// index comparison reads still ended on Friday. The update now covers every adjustment and reports each.
test('更新到今天: one line per adjustment, what the cache now holds and whether it reaches the last close', () => {
  const answer = {
    symbol: 'US:AAPL', rows_saved: 6, start_date: '2023-08-07', end_date: '2026-11-19',
    adjustments: [
      { adjust: 'qfq', label: '前复权', ok: true, rows_saved: 6, rows: 858, first: '2023-08-07', last: '2026-11-18', last_session: '2026-11-18', up_to_date: true, error: null },
      { adjust: '', label: '不复权', ok: true, rows_saved: 0, rows: 853, first: '2023-08-07', last: '2026-11-13', last_session: '2026-11-18', up_to_date: false, error: null },
      { adjust: 'hfq', label: '后复权', ok: false, rows_saved: null, rows: 800, first: '2023-08-07', last: '2026-11-10', last_session: '2026-11-18', up_to_date: false, error: '数据下载失败：数据源暂时不可用' },
      { adjust: 'qfq', label: '前复权', ok: true, rows_saved: 1, rows: null, first: null, last: null, last_session: null, up_to_date: null, error: null },
    ],
  };
  const report = downloadReport(answer);
  assert.equal(report.tone, 'warn');
  assert.deepEqual(report.lines, [
    { text: '前复权 到 2026-11-18（最新）', tone: 'neutral' },
    { text: '不复权 到 2026-11-13，比最近收盘日 2026-11-18 早', tone: 'warn' },
    { text: '后复权 没有更新：数据下载失败：数据源暂时不可用；缓存仍到 2026-11-10', tone: 'warn' },
    { text: '前复权 到 不可用（是否最新：不可用）', tone: 'neutral' },
  ]);
  assert.equal(downloadReport({ ...answer, adjustments: answer.adjustments.slice(0, 1) }).tone, 'neutral');
  // An older backend without the per-adjustment answer: the count it gave, nothing more claimed.
  assert.deepEqual(downloadReport({ symbol: 'US:AAPL', rows_saved: 6 }), { tone: 'neutral', lines: [{ text: '保存 6 根 K 线（没有逐项的复权方式）', tone: 'neutral' }] });
  // The run's coverage says when its bars stop before the last settled session.
  const early = coverageProblems({ ticker: 'IDX:SPX', last: '2026-11-13', last_session: '2026-11-18', ends_early: true });
  assert.deepEqual(early, ['数据到 2026-11-13，比最近收盘日 2026-11-18 早']);
  const service = readFileSync(join(SRC, 'services', 'backtest.ts'), 'utf8');
  assert.match(service, /all_adjustments\?: boolean/);
  const tab = readFileSync(join(SRC, 'components', 'backtest', 'DataTab.tsx'), 'utf8');
  assert.match(tab, /all_adjustments: true/);
  assert.match(tab, /downloadReport\(/);
});

// Gate review G4d item 4: a run saved by v3.2.1 was listed, but open and export answered 404. The service
// now answers with a read-only legacy result (fix/backtest-r4): what it holds is shown, the rest is 不可用.
const LEGACY = {
  saved_by: 'v3.2.1 或更早',
  note_zh: '这是旧版本（v3.2.1 或更早）保存的回测，只保留了当时的汇总指标、抽样净值和成交明细。没有的部分：运行清单、完整交易明细。',
  unavailable: [
    { key: 'manifest', label_zh: '运行清单（设置、数据指纹、引擎和费用表版本）：不能按原设置重跑，也不能核对当时用的行情' },
    { key: 'round_trips', label_zh: '每一笔完整交易（买入到卖出）的明细' },
    { key: 'unfilled', label_zh: '没有成交的信号' },
    { key: 'fills_detail', label_zh: '每笔成交的费用明细和成交原因' },
  ],
  saved_metrics: { sharpe_ratio: 0, win_rate: 0 },
};

test('a run saved by an older version: what it holds is shown, what it lacks is 不可用 with its name; it cannot be rerun', () => {
  const view = legacyView({ legacy: LEGACY });
  assert.equal(view.savedBy, 'v3.2.1 或更早');
  assert.deepEqual(view.missing, ['manifest', 'round_trips', 'unfilled', 'fills_detail']);
  assert.equal(view.parts[1].label, '每一笔完整交易（买入到卖出）的明细');
  assert.equal(view.unavailable('round_trips'), '不可用：旧版本没有保存每一笔完整交易（买入到卖出）的明细');
  assert.equal(view.unavailable('prices'), null);
  assert.equal(legacyView({}), null);
  assert.equal(legacyView({ legacy: null }), null);
  // The saved statistics where v3.2.1 wrote 0 for "none" are not shown as figures.
  assert.ok(!('saved_metrics' in view));
  // Its adjustment is not known: 不可用, not the 前复权 default.
  assert.equal(adjustFact(null), '不可用');
  assert.equal(adjustFact(''), '不复权');
  assert.equal(adjustFact('qfq'), '前复权');
  // 历史 knows such a row by its missing engine version: 重跑 is off and says why.
  assert.equal(historyView({ run_id: 'old', symbol: '600519', engine_version: null }).rerunBlocked, LEGACY_RERUN);
  assert.equal(historyView({ run_id: 'new', ticker: 'SHA:600519', engine_version: '2.2.0' }).rerunBlocked, null);
  const tables = readFileSync(join(SRC, 'components', 'backtest', 'ResultTables.tsx'), 'utf8');
  assert.match(tables, /unavailable/);
  const view2 = readFileSync(join(SRC, 'components', 'backtest', 'ResultView.tsx'), 'utf8');
  assert.match(view2, /legacyView\(/);
});

test('the AI reading is shown as plain text: markdown markers are taken out, nothing is rendered', () => {
  assert.equal(plainReading('## 策略分析\n*规则分析 (未使用 LLM)*\n\n\n**整体评级**: 较差 `x`'), '策略分析\n规则分析 (未使用 LLM)\n\n整体评级: 较差 x');
  assert.equal(plainReading('<img src=x onerror=alert(1)>'), '<img src=x onerror=alert(1)>', 'text stays text (React escapes it)');
  assert.equal(plainReading(null), '');
  assert.equal(plainReading('整体评级: ❌ 较差\n✅ 胜率 ⚠️ 回撤 🇺🇸'), '整体评级: 较差\n胜率 回撤');
});

test('commission: market defaults said once, not after every figure; a typed rate is not called a default', () => {
  const defaults = { default_rate: 0, default_min: 0, defaulted: true, buy: null, sell: null, min: null };
  assert.equal(commissionText({ currency: 'USD', commission: defaults }), '买入 0.00%，卖出 0.00%，每笔最低 0 美元（都是市场默认，不是你券商的费率）');
  assert.equal(
    commissionText({ currency: 'CNY', commission: { ...defaults, default_rate: 0.00025, default_min: 5, buy: 0.0001, sell_rate_includes: ['stamp_duty'] } }),
    '买入 0.01%，卖出 0.025%（默认），每笔最低 5 元（默认）；标“默认”的是市场默认，不是你券商的费率；卖出佣金率已含印花税',
  );
  assert.equal(commissionText({ currency: 'HKD' }), '不可用');
});

test('progress: the stages with where the run is; a status is never red or green', () => {
  const steps = progressSteps({ status: 'running', stage: 'simulate', stage_detail: null, progress: 0.45, stages: [
    { id: 'prepare_data', label: '准备数据' }, { id: 'simulate', label: '模拟交易' }, { id: 'stats', label: '统计' }] });
  assert.deepEqual(steps.map((step) => step.state), ['done', 'active', 'pending']);
  assert.equal(steps[1].detail, '45%');
  const download = progressSteps({ status: 'running', stage: 'prepare_data', stage_detail: '正在下载（首次约 5–20 秒）', progress: null, stages: [] });
  assert.equal(download[0].detail, '正在下载（首次约 5–20 秒）');
  const failed = progressSteps({ status: 'failed', stage: 'prepare_data', stage_detail: null, progress: null, stages: [] });
  assert.equal(failed[0].state, 'stopped');
  for (const status of ['queued', 'running', 'completed', 'failed', 'cancelled', 'other']) {
    assert.ok(['neutral', 'accent', 'warn'].includes(statusChip(status).tone), status);
  }
  assert.equal(statusChip('failed').tone, 'warn');
});

test('history rows: headline, period, method, result and date; a missing result is 不可用', () => {
  const row = historyView({ run_id: 'h1', ticker: 'SHA:DEMO', strategy_name: '均线趋势', start_date: '2023-11-19', end_date: '2026-11-18',
    total_return: -0.05, run_date: '2026-11-19T10:01:02', headline_zh: '这个方法收益 −5.0%' });
  assert.equal(row.result, '-5.00%');
  assert.equal(row.resultTone, 'down');
  assert.equal(row.period, '2023-11-19 至 2026-11-18');
  assert.equal(row.date, '2026-11-19 10:01');
  assert.equal(historyView({ run_id: 'h2', total_return: null }).result, null);
});

// Gate review G4d item 7: 数据 said 不可用 for source, download time and completeness although
// GET /data/cache sends them per instrument and per adjustment; history rows carry the dates used.
test('数据: each instrument’s source, download time and completeness as /data/cache sends them; 不可用 only when null', () => {
  const detail = {
    symbol: 'US:AAPL', source: 'yfinance', fetched_at: '2026-11-18T19:47:58', complete: true,
    adjustments: [
      { adjust: 'qfq', rows: 842, first: '2023-08-28', last: '2026-11-17', coverage: [['2023-08-28', '2026-11-17']], sources: ['yfinance'], fetched_at: '2026-11-18T19:47:58', complete: true },
      { adjust: '', rows: 800, first: '2023-08-28', last: '2026-11-13', coverage: [], sources: ['unknown'], fetched_at: null, complete: null },
    ],
  };
  const view = cacheProvenance(detail);
  assert.equal(view.source, 'yfinance');
  assert.equal(view.fetchedAt, '2026-11-18 19:47:58');
  assert.deepEqual(view.complete, { text: '完整', tone: 'neutral' });
  assert.deepEqual(view.adjustments.map((item) => item.text), [
    '前复权 2023-08-28 至 2026-11-17 · 842 根 · 完整 · 下载于 2026-11-18 19:47:58',
    '不复权 2023-08-28 至 2026-11-13 · 800 根 · 完整性不可用 · 下载时间不可用',
  ]);
  const unknown = cacheProvenance({ symbol: 'X', source: null, fetched_at: null, complete: null, adjustments: [] });
  assert.equal(unknown.source, '不可用');
  assert.equal(unknown.fetchedAt, '不可用');
  assert.deepEqual(unknown.complete, { text: '不可用', tone: 'neutral' });
  assert.deepEqual(cacheProvenance({ symbol: 'X', complete: false, adjustments: [] }).complete, { text: '不完整', tone: 'warn' });
  assert.equal(cacheProvenance(undefined).source, '不可用');
  // History: the first and last bar the run used, the requested range only when they are missing.
  assert.equal(historyView({ run_id: 'h', start_date: '2023-11-18', end_date: '2026-11-18', first_date: '2023-11-20', last_date: '2026-11-17' }).period,
    '2023-11-20 至 2026-11-17');
  const tab = readFileSync(join(SRC, 'components', 'backtest', 'DataTab.tsx'), 'utf8');
  assert.doesNotMatch(tab, /来源、下载时间和是否完整：\{UNAVAILABLE\}/);
  assert.match(tab, /cacheProvenance\(/);
});

// Gate review G4d item 5: with 101 saved runs 历史 showed 100 and no way to the rest (the API pages by offset).
test('历史 pages by offset: a full page asks for the next one, a short page is the last; each run once', () => {
  const page = (from, count) => ({ runs: Array.from({ length: count }, (_, i) => ({ run_id: `r${from + i}` })), stats: { total_runs: 101 } });
  assert.equal(nextHistoryOffset([page(0, 100)], 100), 100);
  assert.equal(nextHistoryOffset([page(0, 100), page(100, 1)], 100), undefined);
  assert.equal(nextHistoryOffset([page(0, 7)], 100), undefined);
  // A run deleted between two pages shifts the next one: the run that moved up is listed once.
  const rows = historyRows([page(0, 100), { runs: [{ run_id: 'r99' }, { run_id: 'r100' }], stats: {} }]);
  assert.equal(rows.length, 101);
  assert.deepEqual(rows.slice(-2).map((row) => row.run_id), ['r99', 'r100']);
  assert.deepEqual(historyRows(undefined), []);
  const service = readFileSync(join(SRC, 'services', 'backtest.ts'), 'utf8');
  assert.match(service, /params: \{ limit, offset \}/);
  const tab = readFileSync(join(SRC, 'components', 'backtest', 'HistoryTab.tsx'), 'utf8');
  assert.match(tab, /加载更多/);
});

test('chart: the method always, a comparison only when computed; paths and the cursor index', () => {
  const data = chartData(result());
  assert.deepEqual(data.series.map((series) => series.key), ['strategy']);
  const withAll = chartData(result({
    benchmark: { same_stock: { total_return: 0.1, curve: [{ date: '2023-11-20', value: 1 }, { date: '2023-11-22', value: 1.1 }], reason: null },
      index: { ticker: 'IDX:SPX', name: '标普500', total_return: 0.02, curve: [{ date: '2023-11-21', value: 1.02 }] } },
  }));
  assert.deepEqual(withAll.series.map((series) => series.key), ['strategy', 'same_stock', 'index']);
  assert.deepEqual(withAll.series[1].values, [1, null, 1.1]);
  const geometry = equityGeometry(withAll, 200, 100);
  assert.match(geometry.paths.strategy, /^M0\.0 /);
  assert.equal(geometry.min, 0.95);
  assert.equal(geometry.max, 1.1);
  assert.equal(equityGeometry({ dates: ['a'], series: [], drawdown: [] }, 200, 100), null);
  assert.match(drawdownPath(data.drawdown, 200, 40), /Z$/);
  assert.equal(drawdownPath([0, 0, 0], 200, 40), null);
  assert.equal(indexAt(3, 200, 199), 2);
  assert.equal(indexAt(3, 200, -5), 0);
});

test('price chart: a marker per fill on its price, the end mark apart, unfilled orders on the close; nothing invented', () => {
  const prices = [
    { date: '2024-01-02', open: 10, high: 10.5, low: 9.8, close: 10.2 },
    { date: '2024-01-03', open: 10.2, high: 11, low: 10.1, close: 10.9 },
    { date: '2024-01-04', open: 10.9, high: 11.2, low: 10.4, close: 10.5 },
    { date: '2024-01-05', open: 10.5, high: 10.6, low: 9.9, close: 10.0 },
  ];
  const fills = [
    { date: '2024-01-03', direction: 'buy', quantity: 100, price: 10.2051, commission: 0 },
    { date: '2024-01-04', direction: 'sell', quantity: 100, price: 10.8946, commission: 0, pnl: 68.95, reason: 'signal', reason_zh: '信号' },
    { date: '2024-01-04', direction: 'buy', quantity: 50, price: 10.9, commission: 0 },
    { date: '2024-01-05', direction: 'sell', quantity: 50, price: 10.0, commission: 0, pnl: -45, reason: 'end_mark', end_mark: true,
      blocked: 'suspended', blocked_quantity: 50, blocked_zh: '最后一天停牌，实际卖不出' },
    { date: '2025-01-01', direction: 'sell', quantity: 1, price: 1, commission: 0 },
  ];
  const unfilled = [{ signal_date: '2024-01-02', date: '2024-01-03', direction: 'buy', quantity: 7, reason: 'insufficient_cash', outcome: 'partial' }];
  const data = priceData({ prices, fills, unfilled });
  assert.deepEqual(data.dates, ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05']);
  assert.deepEqual(data.markers.map((m) => `${m.date} ${m.kind} ${m.side} ${m.value}`), [
    '2024-01-03 buy buy 10.2051', '2024-01-03 unfilled buy 10.9', '2024-01-04 buy buy 10.9', '2024-01-04 sell sell 10.8946', '2024-01-05 end_mark sell 10',
  ]);
  assert.equal(data.undrawn, 1, 'a fill on a day without a price row is counted, not placed');
  // Every sell fill on a drawn day is a sell-side marker (the end mark included, drawn apart).
  const sellFills = fills.filter((fill) => fill.direction === 'sell' && fill.date !== '2025-01-01').length;
  assert.equal(data.markers.filter((m) => m.side === 'sell').length, sellFills);
  assert.deepEqual(markerCounts(data), { buy: 2, sell: 1, endMark: 1, unfilled: 1, undrawn: 1 });

  const facts = { currency: 'USD', kind: 'stock', ticker: 'US:DEMO', adjust: 'qfq' };
  const buy = markerLines(data.markers[0], facts, null);
  assert.equal(buy.title, '2024-01-03 · 买入');
  assert.deepEqual(buy.lines, ['成交价 10.2051', '100 股']);
  const sell = markerLines(data.markers[3], facts, null);
  assert.equal(sell.title, '2024-01-04 · 卖出');
  assert.deepEqual(sell.lines, ['成交价 10.8946', '100 股', '原因：信号', '盈亏 +$68.95']);
  const mark = markerLines(data.markers[4], facts, null);
  assert.equal(mark.title, '2024-01-05 · 期末估值（没有卖出）');
  assert.ok(mark.lines.includes('数据结束时仍持有，只是估值，没有真的卖出'));
  assert.ok(mark.lines.includes('最后一天停牌，实际卖不出'), 'the service’s blocked_zh, as sent');
  assert.ok(!mark.lines.some((line) => line.startsWith('盈亏')), 'an end mark is not a sale with a profit');
  const missed = markerLines(data.markers[1], facts, null);
  assert.equal(missed.title, '2024-01-03 · 买入没有成交');
  assert.ok(missed.lines.includes('原因：资金不足'));
  assert.ok(missed.lines.includes('信号日 2024-01-02'));
  assert.ok(missed.lines.includes('画在当天收盘价 10.90'));

  const summary = priceSummary(data, facts);
  assert.match(summary, /^价格走势：US:DEMO 2024-01-02 至 2024-01-05 的收盘价（前复权，和成交价同一口径），从 10\.20 到 10\.00；/);
  assert.match(summary, /买入 2 次，卖出 1 次，期末估值 1 次（没有卖出），没有成交的委托 1 个，1 个日期不在价格数据里，没有画出/);

  const geometry = priceGeometry(data, 300, 100, 10);
  assert.equal(geometry.min, 10, 'the scale is a price the result has (a close or a fill)');
  assert.equal(geometry.max, 10.9);
  assert.match(geometry.path, /^M0\.0 /);
  assert.equal(geometry.y(10.9), 10);
  assert.equal(geometry.y(10), 90);
  // The pointer finds the marker whose shape it is on: the buy hangs below its price, the sale sits above.
  assert.equal(markerAt(data.markers, geometry, geometry.x(1), geometry.y(10.2051) + 7.5), 0);
  assert.equal(markerAt(data.markers, geometry, geometry.x(2), geometry.y(10.8946) - 7.5), 3);
  assert.equal(markerAt(data.markers, geometry, 5, 50), null);
  assert.equal(priceGeometry(priceData({ prices: prices.slice(0, 1) }), 300, 100), null);
  assert.deepEqual(priceData({}).markers, [], 'an older record without prices draws nothing');
});

test('monthly returns: a year × month grid of the service’s figures; months outside the run are not 0', () => {
  const rows = monthlyRows([
    { year: 2023, month: 11, return: 0.012345 },
    { year: 2023, month: 12, return: -0.02 },
    { year: 2024, month: 1, return: null },
    { year: 2024, month: 2, return: 0 },
  ]);
  assert.deepEqual(rows.map((row) => row.year), [2023, 2024]);
  assert.deepEqual(rows[0].months.slice(9), [undefined, 0.012345, -0.02]);
  assert.equal(rows[0].months[0], undefined, 'before the run');
  assert.equal(rows[1].months[0], null, 'in the run without a figure: 不可用');
  assert.equal(rows[1].months[1], 0);
  assert.equal(rows[1].months[2], undefined, 'after the run');
  assert.deepEqual(monthlyRows(undefined), []);
  assert.deepEqual(monthlyRows([{ year: 2024, month: 13, return: 0.1 }]), []);
});

// -- the code ------------------------------------------------------------------------------------

// Figures on the backtest page are the service's: no float arithmetic or rounding of money or percentages.
// A line that converts typed input or pixels says so with `exact-ok:` on it or just above it. The one rounding
// allowed is lib/format.ts `fmtLong` (past 8 places: ≈ and 8 places, the full value kept): the page never writes
// its own "≈" or rounds to some number of places itself, and the result tables use it.
test('the backtest page never rounds or recomputes a figure from the service', () => {
  const offenders = [];
  for (const dir of [join(SRC, 'components', 'backtest'), join(SRC, 'features', 'backtest'), join(SRC, 'pages', 'research')]) {
    for (const name of readdirSync(dir)) {
      const lines = readFileSync(join(dir, name), 'utf8').split('\n');
      lines.forEach((line, index) => {
        if (/^\s*(\/\/|\*)/.test(line)) return;
        const rounding = /\bdigits:\s*\d/.test(line) && !/exact:\s*true/.test(line);
        const risky = rounding || /\bparseFloat\(|\.toFixed\(|\.toPrecision\(|\bNumber\((?!\.)|\bMath\.(round|floor|ceil|trunc)\(|toLocaleString\(|≈|SHOWN_PLACES/.test(line);
        const justified = /exact-ok:/.test(line) || /exact-ok:/.test(lines[index - 1] ?? '');
        if (risky && !justified) offenders.push(`${name}:${index + 1}: ${line.trim()}`);
      });
    }
  }
  assert.deepEqual(offenders, []);
  const result = readFileSync(join(SRC, 'features', 'backtest', 'result.ts'), 'utf8');
  for (const name of ['pctFigure', 'moneyFigure', 'priceFigure']) {
    const body = result.slice(result.indexOf(`function ${name}(`), result.indexOf('\n}\n', result.indexOf(`function ${name}(`)));
    assert.match(body, /fmtLong\(/, name);
  }
  const tables = readFileSync(join(SRC, 'components', 'backtest', 'ResultTables.tsx'), 'utf8');
  assert.doesNotMatch(tables, /\b(priceText|moneyText|pctText|feeParts)\(/, 'the tables show the figures with their full value');
});

test('the backtest page renders text from results as text and colours by token only', () => {
  for (const dir of [join(SRC, 'components', 'backtest'), join(SRC, 'pages', 'research')]) {
    for (const name of readdirSync(dir)) {
      const source = readFileSync(join(dir, name), 'utf8');
      assert.ok(!/dangerouslySetInnerHTML|ReactMarkdown|SafeMarkdown/.test(source), name);
      assert.ok(!/text-(red|green|emerald)-\d/.test(source), name);
    }
  }
});
