// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// 记一笔 and the 账本 page without a browser: exact decimal arithmetic, the kinds the ledger accepts, the
// form's checks and events, the cash line, the review's changes ("无", never 0), warnings, the trade-plan
// check, the events table and the fee profile form.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { decAdd, decCmp, decMul, decNeg, decRanks, decShift, decSub, decSum, isDecimal } from '../src/features/ledger/decimal.ts';
import { ENTRY_KINDS, LEDGER_KINDS, LEDGER_KIND_LABEL, MORE_KINDS, PRIMARY_KINDS, kindLabel } from '../src/features/ledger/kinds.ts';
import {
  B_SHARE_REFUSAL, KIND_FIELDS, TICKER_HINT, bShareRefusal, buildEvents, cashEffect, emptyValues, lotStep, parseTicker, resolveTicker,
  tickerCurrency, validateEntry, withCurrency, withTicker,
} from '../src/features/ledger/entry.ts';
import {
  changeFigure, changeRows, changeValue, confirmFailure, contributionsAsked, eventLine, eventLineShown, isOutdated, ledgerErrorText, missingContributions,
  planCheckLines,
  planWarningCount,
  proposalStatus, receiptMessage, shownEvents, warningText,
} from '../src/features/ledger/review.ts';
import {
  cashList, closedAccounts, eventCash, filterEvents, heldPosition, heldQuantity, noteMarks, noteText, rangeBounds, rangeStart, reversedIds,
  sortEvents, sourceLabel, tickerNames,
} from '../src/features/ledger/events.ts';
import { feeHint, formToProfile, fromWan, profileSummary, profileToForm, toWan } from '../src/features/ledger/fees.ts';
import { LEDGER_KIND_LABEL as PENDING_LABEL, describeLedgerEvent } from '../src/features/pending/proposals.ts';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

// -- decimals --------------------------------------------------------------------------------------

test('decimal strings add, subtract and multiply exactly', () => {
  assert.equal(decAdd('0.1', '0.2'), '0.3');
  assert.equal(decSub('120000', '168043.68'), '-48043.68');
  assert.equal(decMul('100', '1680'), '168000');
  assert.equal(decMul('0.5', '100'), '50');
  assert.equal(decMul('1.005', '3'), '3.015');
  assert.equal(decNeg('5'), '-5');
  assert.equal(decNeg('0'), '0');
  assert.equal(decSub('5', '5.00'), '0');
  assert.equal(decCmp('10.50', '10.5'), 0);
  assert.equal(decCmp('-1', '0'), -1);
  assert.equal(decSum(['1', '2.5', '-0.5']), '3');
  assert.equal(decShift('2.5', -4), '0.00025');
  assert.equal(decShift('0.00025', 4), '2.5');
  assert.equal(decShift('0', 4), '0');
  for (const bad of ['', 'abc', '1,000', '1e3', null, undefined, '.', '-']) {
    assert.equal(isDecimal(bad), false, String(bad));
    assert.equal(decAdd(bad, '1'), null, String(bad));
  }
  assert.equal(decAdd('3.', '1'), '4');
});

// -- kinds -----------------------------------------------------------------------------------------

test('the kinds are exactly those core/ledger.py accepts; every one has a Chinese name', () => {
  const source = readFileSync(join(ROOT, 'core', 'ledger.py'), 'utf8');
  const block = /^KINDS = \{([^}]*)\}/m.exec(source)?.[1];
  assert.ok(block, 'KINDS found in core/ledger.py');
  const backend = [...block.matchAll(/"([a-z_]+)"/g)].map((match) => match[1]).sort();
  assert.deepEqual([...LEDGER_KINDS].sort(), backend);
  for (const kind of LEDGER_KINDS) assert.match(LEDGER_KIND_LABEL[kind], /^[一-鿿/]+$/, kind);
  assert.equal(kindLabel('interest'), '利息');
  assert.equal(kindLabel('something_new'), 'something_new');
  // The entry form offers every kind but the reversal, once each.
  assert.deepEqual([...ENTRY_KINDS].sort(), backend.filter((kind) => kind !== 'reverse').sort());
  assert.equal(new Set(ENTRY_KINDS).size, ENTRY_KINDS.length);
  assert.deepEqual(PRIMARY_KINDS, ['buy', 'sell', 'dividend', 'deposit', 'withdrawal']);
  assert.ok(MORE_KINDS.some((group) => group.kinds.includes('interest')));
  for (const kind of ENTRY_KINDS) assert.ok(KIND_FIELDS[kind], `${kind} has its fields`);
});

test('the tray and Today label kinds from the same map (利息 included)', () => {
  assert.equal(PENDING_LABEL, LEDGER_KIND_LABEL);
  assert.equal(describeLedgerEvent({ kind: 'interest', date: '2026-11-17', amount: '12.34', currency: 'CNY' }), '11-17 利息 ¥12.34');
});

// -- tickers ---------------------------------------------------------------------------------------

test('tickers: exchange prefix, market currency, A-share lot stepper, holdings by name or code', () => {
  assert.equal(parseTicker(' sha:600519 '), 'SHA:600519');
  assert.equal(parseTicker('600519'), null);
  assert.equal(parseTicker('IDX:000001'), null);
  assert.equal(tickerCurrency('HKG:00700'), 'HKD');
  assert.equal(tickerCurrency('NYSE:DEMOX'), 'USD');
  assert.equal(tickerCurrency('XYZ:1'), null);
  assert.equal(lotStep('SHE:300750'), 100);
  assert.equal(lotStep('HKG:00700'), null);
  assert.equal(lotStep('NASDAQ:DEMO'), null);
  const held = [{ ticker: 'SHA:DEMO', name: '示例宽基' }, { ticker: 'NASDAQ:DEMO', name: '示例研究标的' }];
  assert.equal(resolveTicker('示例宽基', held), 'SHA:DEMO');
  assert.equal(resolveTicker('nasdaq:demo', held), 'NASDAQ:DEMO');
  assert.equal(resolveTicker('DEMO', held), null, 'a code held on two exchanges is ambiguous');
  assert.equal(resolveTicker('示例', held), null);
});

// -- the form --------------------------------------------------------------------------------------

const CONTEXT = { accounts: ['长期账户', '研究账户'], ticker: 'SHA:600519', held: null, feeEstimate: '43.68' };

function buy(overrides = {}) {
  return emptyValues({ kind: 'buy', account: '长期账户', date: '2026-11-17', ticker: 'SHA:600519', quantity: '100', price: '1680', ...overrides });
}

test('a buy with the fee left empty books the estimate; a typed fee wins', () => {
  assert.deepEqual(validateEntry(buy(), CONTEXT), {});
  assert.deepEqual(buildEvents(buy({ name: '示例蓝筹', note: '  分批建仓 ' }), CONTEXT), [{
    kind: 'buy', date: '2026-11-17', account: '长期账户', ticker: 'SHA:600519', name: '示例蓝筹', currency: 'CNY',
    quantity: '100', price: '1680', fee: '43.68', note: '分批建仓 〔费用为估算：按费率估算〕',
  }]);
  assert.equal(buildEvents(buy({ fee: '40', allowMargin: true }), CONTEXT)[0].fee, '40');
  assert.equal(buildEvents(buy({ allowMargin: true }), CONTEXT)[0].allow_margin, true);
  assert.equal(buildEvents(buy({ kind: 'sell', allowMargin: true }), { ...CONTEXT, held: '300' })[0].allow_margin, undefined);
});

test('without an estimate (no HK/US profile) the fee is required, and never booked as 0 silently', () => {
  const context = { ...CONTEXT, ticker: 'HKG:00700', feeEstimate: null };
  assert.equal(validateEntry(buy({ ticker: 'HKG:00700' }), context).fee, '请填写成交单上的费用');
  assert.deepEqual(validateEntry(buy({ ticker: 'HKG:00700', fee: '0' }), context), {});
  assert.equal(buildEvents(buy({ ticker: 'HKG:00700', fee: '18.5' }), context)[0].currency, 'HKD');
});

test('the form names what is missing or wrong, field by field', () => {
  const errors = validateEntry(emptyValues({ kind: 'buy', account: '', date: '2026-02-30' }), { ...CONTEXT, ticker: null });
  assert.equal(errors.account, '请选择账户');
  assert.equal(errors.date, '日期格式应为 YYYY-MM-DD');
  assert.equal(errors.ticker, '请输入标的');
  assert.equal(errors.quantity, '请输入数量');
  assert.equal(errors.price, '请输入成交价格');
  // The 标的 field searches by name or code: typed text that is not a picked listing asks for a pick.
  assert.equal(validateEntry(buy({ ticker: '茅台' }), { ...CONTEXT, ticker: null }).ticker, '请从列表中选择标的（可输入代码或名称搜索）。');
  assert.doesNotMatch(TICKER_HINT, /交易所前缀|暂需/, 'the hint describes the search, not the old exchange-prefix rule');
  assert.equal(validateEntry(buy({ quantity: '0' }), CONTEXT).quantity, '数量必须大于 0');
  assert.equal(validateEntry(buy({ fee: '-1' }), CONTEXT).fee, '费用不能为负数');
  assert.equal(validateEntry(buy({ fee: '40', note: 'x'.repeat(241) }), CONTEXT).note, '备注最多 240 个字');
});

test('a sale is limited to what the account holds', () => {
  const sell = buy({ kind: 'sell', quantity: '300' });
  assert.deepEqual(validateEntry(sell, { ...CONTEXT, held: '300' }), {});
  assert.equal(validateEntry(buy({ kind: 'sell', quantity: '301' }), { ...CONTEXT, held: '300' }).quantity, '超过可卖数量 300');
  assert.equal(validateEntry(sell, { ...CONTEXT, held: null }).ticker, '该账户没有这只标的的持仓');
});

test('cash movements: amount, currency, a required CNY rate for foreign deposits and transfers', () => {
  const deposit = emptyValues({ kind: 'deposit', account: '长期账户', date: '2026-11-17', currency: 'USD', amount: '1000' });
  assert.match(validateEntry(deposit, CONTEXT).fxRate, /1 USD = \? CNY/);
  assert.deepEqual(buildEvents({ ...deposit, fxRate: '7.05' }, CONTEXT), [{
    kind: 'deposit', date: '2026-11-17', account: '长期账户', currency: 'USD', amount: '1000', fx_rate: '7.05',
  }]);
  const cny = buildEvents({ ...deposit, currency: 'CNY', fxRate: '7.05' }, CONTEXT)[0];
  assert.equal(cny.fx_rate, undefined, 'CNY needs no rate');
  const transfer = emptyValues({ kind: 'transfer', account: '长期账户', date: '2026-11-17', currency: 'CNY', amount: '50', toAccount: '长期账户' });
  assert.equal(validateEntry(transfer, CONTEXT).toAccount, '转入账户不能是转出账户');
  assert.equal(buildEvents({ ...transfer, toAccount: '研究账户' }, CONTEXT)[0].to_account, '研究账户');
  const fx = emptyValues({ kind: 'fx', account: '长期账户', date: '2026-11-17', currency: 'CNY', amount: '7050', toCurrency: 'CNY' });
  assert.equal(validateEntry(fx, CONTEXT).toCurrency, '换入币种不能与换出币种相同');
  assert.deepEqual(buildEvents({ ...fx, toCurrency: 'USD', receivedAmount: '999.5' }, CONTEXT)[0], {
    kind: 'fx', date: '2026-11-17', account: '长期账户', currency: 'CNY', amount: '7050', to_currency: 'USD', received_amount: '999.5',
  });
  const interest = emptyValues({ kind: 'interest', account: '长期账户', date: '2026-11-17', currency: 'CNY', amount: '12.34' });
  assert.deepEqual(buildEvents(interest, CONTEXT)[0], { kind: 'interest', date: '2026-11-17', account: '长期账户', currency: 'CNY', amount: '12.34' });
});

test('a dividend may name its stock; a split needs a holding and a ratio', () => {
  const dividend = emptyValues({ kind: 'dividend', account: '长期账户', date: '2026-11-17', currency: 'CNY', amount: '360' });
  assert.deepEqual(validateEntry(dividend, { ...CONTEXT, ticker: null }), {});
  assert.equal(buildEvents(dividend, { ...CONTEXT, ticker: null })[0].ticker, undefined);
  assert.equal(buildEvents({ ...dividend, ticker: 'SHA:600519' }, CONTEXT)[0].ticker, 'SHA:600519');
  const split = emptyValues({ kind: 'split', account: '长期账户', date: '2026-11-17', ticker: 'SHA:600519', ratio: '1.5' });
  assert.equal(validateEntry(split, CONTEXT).ticker, '该账户没有这只标的的持仓');
  assert.deepEqual(buildEvents(split, { ...CONTEXT, held: '100' })[0],
    { kind: 'split', date: '2026-11-17', account: '长期账户', ticker: 'SHA:600519', ratio: '1.5' });
});

test('funds are CNY; an opening balance creates a new account with its positions', () => {
  const fund = emptyValues({ kind: 'fund_buy', account: '长期账户', date: '2026-11-17', currency: 'USD', amount: '1000' });
  assert.deepEqual(buildEvents(fund, CONTEXT)[0], { kind: 'fund_buy', date: '2026-11-17', account: '长期账户', currency: 'CNY', amount: '1000', fee: '0' });
  assert.equal(validateEntry(emptyValues({ kind: 'fund_value', account: '长期账户', date: '2026-11-17', amount: '0' }), CONTEXT).amount, undefined);
  const opening = emptyValues({
    kind: 'opening', account: '新账户', date: '2026-11-17', contributed: '100000', openingCash: { CNY: '20000', HKD: '', USD: '1000' },
    positions: [{ ticker: 'sha:600519', quantity: '100', cost: '1500' }, { ticker: '', quantity: '', cost: '' }],
  });
  assert.deepEqual(validateEntry(opening, CONTEXT), {});
  assert.deepEqual(buildEvents(opening, CONTEXT)[0], {
    kind: 'opening', date: '2026-11-17', account: '新账户', cash_balances: { CNY: '20000', HKD: '0', USD: '1000' },
    contributed_cny: '100000', fund_cny: '0', positions: [{ ticker: 'SHA:600519', quantity: '100', cost_price: '1500' }],
  });
  assert.equal(validateEntry({ ...opening, account: '长期账户' }, CONTEXT).account, '已有同名账户，请换一个名称');
  const twice = { ...opening, positions: [{ ticker: 'SHA:600519', quantity: '1', cost: '1' }, { ticker: 'sha:600519', quantity: '1', cost: '1' }] };
  assert.equal(validateEntry(twice, CONTEXT)['positions.1.ticker'], '同一标的只能填写一行');
});

test('the cash line: turnover and cash before → after in exact decimals, with 现金不足', () => {
  const effect = cashEffect(buy(), 'SHA:600519', { CNY: '120000' }, '43.68');
  assert.deepEqual(effect, {
    currency: 'CNY', turnover: '168000', delta: '-168043.68', before: '120000', after: '-48043.68', short: true, feeUnknown: false,
  });
  const sale = cashEffect(buy({ kind: 'sell', quantity: '0.5', price: '100.1' }), 'SHA:600519', { CNY: '10' }, '1');
  assert.equal(sale.after, '59.05');
  assert.equal(sale.short, false);
  const unknownFee = cashEffect(buy(), 'SHA:600519', { CNY: '200000' }, null);
  assert.equal(unknownFee.feeUnknown, true);
  assert.equal(unknownFee.after, '32000');
  assert.equal(cashEffect(buy({ quantity: '' }), 'SHA:600519', { CNY: '1' }, '5').after, null);
  // A currency the account has never held starts at 0, as in the ledger.
  const deposit = emptyValues({ kind: 'deposit', currency: 'HKD', amount: '5000' });
  assert.equal(cashEffect(deposit, null, { CNY: '1' }, null).after, '5000');
  const fx = emptyValues({ kind: 'fx', currency: 'CNY', amount: '7050', toCurrency: 'USD', receivedAmount: '1000' });
  assert.deepEqual(cashEffect(fx, null, { CNY: '10000', USD: '1' }, null).received, { currency: 'USD', before: '1', after: '1001' });
  assert.equal(cashEffect(emptyValues({ kind: 'split' }), 'SHA:600519', {}, null), null);
});

// -- the review ------------------------------------------------------------------------------------

const BEFORE = {
  date: '2026-09-01',
  accounts: {
    长期账户: {
      cash_balances: { CNY: '20000', USD: '1000' }, contributed_cny: '81000', fund_cny: '0', realized: {},
      positions: { 'SHA:DEMO': { ticker: 'SHA:DEMO', name: '示例宽基', quantity: '1000', cost_price: '50', currency: 'CNY' } },
    },
  },
};

test('changes: only what moved; 无 where the item did not exist before or after, never 0', () => {
  const after = structuredClone(BEFORE);
  after.accounts.长期账户.cash_balances.USD = '949';
  after.accounts.长期账户.positions['NASDAQ:EXAMPLE'] = { ticker: 'NASDAQ:EXAMPLE', name: 'NASDAQ:EXAMPLE', quantity: '0.5', cost_price: '102', currency: 'USD' };
  after.accounts.新账户 = { cash_balances: { CNY: '100' }, positions: {}, contributed_cny: '100', fund_cny: '0', realized: {} };
  const groups = changeRows({ before: BEFORE, after });
  assert.deepEqual(groups.map((group) => [group.account, group.created]), [['长期账户', false], ['新账户', true]]);
  const rows = groups[0].rows;
  assert.deepEqual(rows.map((row) => row.label), ['USD 现金', 'NASDAQ:EXAMPLE 数量', 'NASDAQ:EXAMPLE 单位成本']);
  assert.equal(changeValue(rows[0], 'before'), '$1,000.00');
  assert.equal(changeValue(rows[0], 'after'), '$949.00');
  assert.equal(changeValue(rows[1], 'before'), '无');
  assert.equal(changeValue(rows[1], 'after'), '0.5');
  assert.equal(changeValue(rows[2], 'after'), '$102.00');
  const created = groups[1].rows;
  assert.ok(created.every((row) => changeValue(row, 'before') === '无'), 'a new account has nothing before');
  // A position sold out: its quantity is 无 afterwards.
  const sold = structuredClone(BEFORE);
  delete sold.accounts.长期账户.positions['SHA:DEMO'];
  sold.accounts.长期账户.cash_balances.CNY = '81000';
  sold.accounts.长期账户.realized = { CNY: '10995' };
  const soldRows = changeRows({ before: BEFORE, after: sold })[0].rows;
  assert.equal(changeValue(soldRows.find((row) => row.key === 'qty:SHA:DEMO'), 'after'), '无');
  assert.equal(soldRows.find((row) => row.key === 'qty:SHA:DEMO').label, '示例宽基 数量');
  assert.equal(soldRows.find((row) => row.key === 'realized:CNY').before, null);
});

test('event lines, status lines and the receipt toast', () => {
  const event = { id: 'a', kind: 'buy', date: '2026-09-02', account: '长期账户', ticker: 'NASDAQ:EXAMPLE', currency: 'USD', quantity: '0.5', price: '100', fee: '1', note: '虚构交易' };
  assert.equal(eventLine(event), '2026-09-02 买入 NASDAQ:EXAMPLE 0.5 股 @ $100.00 · 费用 1.00 · 长期账户 · 虚构交易');
  assert.equal(eventLine({ id: 'r', kind: 'reverse', date: '2026-09-03', target_id: 'a' }, (id) => (id === 'a' ? event : undefined)),
    '2026-09-03 冲销：2026-09-02 买入 NASDAQ:EXAMPLE 0.5 股 @ $100.00 · 费用 1.00 · 长期账户 · 虚构交易');
  assert.equal(eventLine({ id: 'd', kind: 'deposit', date: '2026-09-03', account: '长期账户', currency: 'USD', amount: '100', fx_rate: '7.05' }),
    '2026-09-03 入金 $100.00 · 汇率 7.05 · 长期账户');
  assert.equal(eventLine({ id: 't', kind: 'transfer', date: '2026-09-03', account: '甲', to_account: '乙', currency: 'CNY', amount: '50' }),
    '2026-09-03 转账 ¥50.00 · 甲 → 乙');
  assert.equal(proposalStatus({ events: [event, event], duplicates: 0 }), '2 笔待入账');
  assert.equal(proposalStatus({ events: [], duplicates: 1 }), '0 笔待入账 · 跳过 1 笔重复记录');
  assert.equal(proposalStatus({ events: [event], duplicates: 0, receipt: { applied: 1, revision: 12, duplicates: 0 } }), '已入账 1 笔 · 版本 12');
  assert.equal(receiptMessage({ applied: 2, revision: 3, duplicates: 0 }), '已入账 2 笔 · 版本 3');
});

test('warnings read in Chinese', () => {
  assert.equal(warningText('长期账户 USD: negative cash -51'), '长期账户 的 USD 现金将为负数：-$51.00');
  assert.equal(warningText('长期账户 CNY: negative cash -0.004'), '长期账户 的 CNY 现金将为负数：-¥0.004', 'never -¥0.00');
  assert.equal(warningText('基金按人民币合计估值记账'), '基金按人民币合计估值记账');
});

test('the plan check: relation per trade, warnings and info; silent trades left out', () => {
  const items = [
    { event_ref: 'e1', index: 0, kind: 'buy', date: '2026-11-17', ticker: 'SHA:DEMO', account: '长期账户', quantity: '100', price: '62',
      plan_id: 3, relation: 'add', deviations: [
        { code: 'outside_entry_range', level: 'warning', message: '买入价 62 高于计划区间上沿 61。' },
        { code: 'weight_unknown', level: 'info', message: '无法计算仓位占比。' }] },
    { event_ref: 'e2', index: 1, kind: 'sell', date: '2026-11-17', ticker: 'NASDAQ:DEMO', account: '研究账户', quantity: '5', price: '1',
      plan_id: null, relation: 'none', deviations: [] },
    { event_ref: 'e3', index: 2, kind: 'buy', date: '2026-11-17', ticker: 'SHE:DEMOZ', account: '长期账户', quantity: '100', price: '1',
      plan_id: null, relation: 'none', deviations: [{ code: 'no_plan', level: 'warning', message: '买入 SHE:DEMOZ 时没有启用中的交易计划。' }] },
  ];
  const lines = planCheckLines(items);
  assert.deepEqual(lines.map((line) => [line.title, line.relation]), [['买入 SHA:DEMO 100 股', '计划 #3 · 加仓'], ['买入 SHE:DEMOZ 100 股', '无计划']]);
  assert.deepEqual(lines[0].deviations.map((d) => d.level), ['warning', 'info']);
  assert.equal(planWarningCount(items), 2);
  assert.deepEqual(planCheckLines(undefined), []);
});

// -- the events table ------------------------------------------------------------------------------

const EVENTS = [
  { id: 'o', kind: 'opening', date: '2026-09-01', account: '长期账户', source: 'legacy-opening', positions: [{ ticker: 'SHA:DEMO', name: '示例宽基', quantity: '1000', cost_price: '50' }] },
  { id: 'b', kind: 'buy', date: '2026-11-03', account: '长期账户', ticker: 'NASDAQ:EXAMPLE', currency: 'USD', quantity: '0.5', price: '100', fee: '1', source: 'manual' },
  { id: 'd', kind: 'deposit', date: '2026-11-16', account: '研究账户', currency: 'CNY', amount: '50', source: 'broker-csv' },
  { id: 't', kind: 'transfer', date: '2026-11-17', account: '研究账户', to_account: '长期账户', currency: 'CNY', amount: '20', source: 'import:长期账户' },
  { id: 'r', kind: 'reverse', date: '2026-11-17', target_id: 'b', source: 'manual' },
];

test('each event’s cash effect, signed, in its own currency', () => {
  assert.deepEqual(eventCash(EVENTS[1]), { amount: '-51', currency: 'USD' });
  assert.deepEqual(eventCash({ ...EVENTS[1], kind: 'sell' }), { amount: '49', currency: 'USD' });
  assert.deepEqual(eventCash(EVENTS[2]), { amount: '50', currency: 'CNY' });
  assert.deepEqual(eventCash(EVENTS[3]), { amount: '-20', currency: 'CNY' });
  assert.deepEqual(eventCash({ id: 'f', kind: 'fx', date: '2026-11-17', currency: 'CNY', amount: '705', to_currency: 'USD', received_amount: '100' }),
    { amount: '-705', currency: 'CNY', received: { amount: '100', currency: 'USD' } });
  assert.deepEqual(eventCash(EVENTS[0]), { amount: null, currency: null });
  assert.deepEqual([...reversedIds(EVENTS)], ['b']);
});

test('sources, filters and names', () => {
  assert.deepEqual(EVENTS.map((event) => sourceLabel(event.source)), ['旧持仓迁移', '手工', 'CSV（broker-csv）', '交割单导入', '手工']);
  assert.equal(sourceLabel(undefined), '手工');
  assert.equal(noteText('Opening balance from holdings dated 2026-08-31'), '期初余额取自 2026-08-31 的持仓文件');
  assert.equal(noteText('自己写的备注'), '自己写的备注');
  const all = { account: '', kind: '', query: '', range: 'all' };
  assert.equal(filterEvents(EVENTS, all, '2026-11-17').length, 5);
  assert.deepEqual(filterEvents(EVENTS, { ...all, account: '长期账户' }, '2026-11-17').map((e) => e.id), ['o', 'b', 't', 'r'],
    'a transfer in counts, and so does the reversal of an event of the account');
  assert.deepEqual(filterEvents(EVENTS, { ...all, kind: 'deposit' }, '2026-11-17').map((e) => e.id), ['d']);
  assert.deepEqual(filterEvents(EVENTS, { ...all, query: 'example' }, '2026-11-17').map((e) => e.id), ['b', 'r']);
  assert.deepEqual(filterEvents(EVENTS, { ...all, range: 'week' }, '2026-11-17').map((e) => e.id), ['d', 't', 'r']);
  assert.deepEqual(filterEvents(EVENTS, { ...all, range: 'month' }, '2026-11-17').map((e) => e.id), ['b', 'd', 't', 'r']);
  assert.equal(rangeStart('week', '2026-11-17'), '2026-11-16', 'weeks start on Monday');
  assert.equal(rangeStart('week', '2026-11-22'), '2026-11-16', 'Sunday belongs to the week before');
  assert.equal(rangeStart('year', '2026-11-17'), '2026-01-01');
  assert.equal(rangeStart('all', '2026-11-17'), null);
  const names = tickerNames(EVENTS, BEFORE.accounts);
  assert.equal(names['SHA:DEMO'], '示例宽基');
  assert.deepEqual(filterEvents([{ id: 'x', kind: 'sell', date: '2026-11-17', ticker: 'SHA:DEMO' }], { ...all, query: '宽基' }, '2026-11-17', names).length, 1);
});

test('account lines list CNY and non-zero foreign cash; held quantity by ticker', () => {
  const account = { ...BEFORE.accounts.长期账户, cash_balances: { USD: '1000', HKD: '0', CNY: '-5' } };
  assert.deepEqual(cashList(account), [['CNY', '-5'], ['USD', '1000']]);
  assert.equal(heldQuantity(account, 'SHA:DEMO'), '1000');
  assert.equal(heldQuantity(account, 'SHA:OTHER'), null);
  assert.equal(heldQuantity(undefined, 'SHA:DEMO'), null);
});

test('a holding is found whatever spelling of its listing was typed', () => {
  const account = { ...BEFORE.accounts.长期账户, positions: {
    'HKG:700': { ticker: 'HKG:700', name: '示例港股', currency: 'HKD', quantity: '100', cost_price: '300' },
    'NYSE:BRK-B': { ticker: 'NYSE:BRK-B', name: '示例美股', currency: 'USD', quantity: '2', cost_price: '400' } } };
  assert.equal(heldQuantity(account, 'HKG:00700'), '100');
  assert.equal(heldQuantity(account, 'HKG:0700'), '100');
  assert.equal(heldQuantity(account, 'NYSE:BRK.B'), '2');
  assert.equal(heldPosition(account, 'HKG:00700')?.name, '示例港股');
  assert.equal(heldPosition(account, 'HKG:07000'), null);
  assert.equal(heldPosition(undefined, 'HKG:700'), null);
});

test('of two spellings held (an old ledger), the one typed, else the larger position, as the ledger books it', () => {
  const row = (ticker, quantity) => ({ ticker, name: ticker, currency: 'HKD', quantity, cost_price: '300' });
  const account = { ...BEFORE.accounts.长期账户, positions: { 'HKG:700': row('HKG:700', '100'), 'HKG:00700': row('HKG:00700', '200') } };
  assert.equal(heldQuantity(account, 'HKG:700'), '100');
  assert.equal(heldQuantity(account, 'HKG:00700'), '200');
  assert.equal(heldQuantity(account, 'HKG:0700'), '200');
  const smaller = { ...account, positions: { 'HKG:700': row('HKG:700', '1000'), 'HKG:00700': row('HKG:00700', '200.5') } };
  assert.equal(heldQuantity(smaller, 'HKG:0700'), '1000');
});

test('a contribution asked for one date is not asked for another', () => {
  const asked = { date: '2026-11-13', accounts: ['长期账户'] };
  assert.deepEqual(contributionsAsked(asked, '2026-11-13'), ['长期账户']);
  assert.deepEqual(contributionsAsked(asked, '2026-11-20'), [], 'another date can select another holdings file');
  assert.deepEqual(contributionsAsked(null, '2026-11-13'), []);
});

test('the opening preview of the old holdings file names the accounts whose contribution it asks for', () => {
  const refusal = (status, data) => ({ response: { status, data } });
  assert.deepEqual(missingContributions(refusal(422, { detail: '…', missing_contributions: ['长期账户', 7] })), ['长期账户']);
  assert.deepEqual(missingContributions(refusal(422, { detail: '旧持仓文件 2026-11-13.json 不是有效的 JSON，请修正后再预览。' })), []);
  assert.deepEqual(missingContributions(refusal(500, { missing_contributions: ['长期账户'] })), []);
  assert.deepEqual(missingContributions(new Error('network')), []);
  assert.deepEqual(missingContributions(null), []);
});

// -- fees ------------------------------------------------------------------------------------------

test('the fee hint shows the basis and breakdown, or why there is no estimate', () => {
  const estimate = { total: '43.68', parts: [{ label: '佣金', amount: '42' }, { label: '过户费', amount: '1.68' }], basis: '按账户费率估算', reason: null };
  assert.equal(feeHint(estimate), '按账户费率估算：佣金 42.00 · 过户费 1.68');
  assert.equal(feeHint({ ...estimate, total: null, parts: [], reason: '未设置该市场费率' }), '未设置该市场费率');
  assert.equal(feeHint(null), null);
});

test('the fee profile is edited in 万分之 and saved as exact fractions', () => {
  assert.equal(toWan('0.00025'), '2.5');
  assert.equal(fromWan('1'), '0.0001');
  const preset = { broker: '', a_share: { commission_rate: '0.00025', commission_min: '5', waive_minimum: false }, hk: null, us: null, is_default: true };
  const form = profileToForm(preset);
  assert.deepEqual(form, {
    broker: '', commissionRate: '2.5', commissionMin: '5', waiveMinimum: false,
    hk: { enabled: false, buy: { rate: '', min: '', fixed: '' }, sell: { rate: '', min: '', fixed: '' } },
    us: { enabled: false, buy: { rate: '', min: '', fixed: '' }, sell: { rate: '', min: '', fixed: '' } },
  });
  assert.deepEqual(formToProfile(form).profile, { broker: '', a_share: { commission_rate: '0.00025', commission_min: '5', waive_minimum: false }, hk: null, us: null });
  const hk = { ...form, waiveMinimum: true, commissionMin: '', hk: { enabled: true, buy: { rate: '3', min: '50', fixed: '0' }, sell: { rate: '', min: '', fixed: '' } } };
  assert.deepEqual(formToProfile(hk).profile.hk, { buy: { rate: '0.0003', min: '50', fixed: '0' }, sell: null });
  assert.equal(formToProfile(hk).profile.a_share.commission_min, '0');
  const partial = { ...form, us: { enabled: true, buy: { rate: '1', min: '', fixed: '0' }, sell: { rate: '', min: '', fixed: '' } } };
  assert.equal(formToProfile(partial).errors['us.buy.min'], '请填写（不收的项填 0）');
  const empty = { ...form, us: { enabled: true, buy: { rate: '', min: '', fixed: '' }, sell: { rate: '', min: '', fixed: '' } } };
  assert.match(formToProfile(empty).errors['us.buy.rate'], /至少填写/);
  assert.equal(formToProfile({ ...form, commissionRate: '' }).errors.commissionRate, '请填写佣金率');
  assert.equal(profileSummary(preset), '万2.5 · 最低 ¥5.00');
  assert.equal(profileSummary({ ...preset, a_share: { ...preset.a_share, waive_minimum: true }, hk: { buy: null, sell: null } }), '万2.5 · 免五 · 港股已设置');
});

// -- review round 3 -------------------------------------------------------------------------------

test('a malformed decimal is refused, not repaired', () => {
  for (const bad of ['1.2.', '1.25.', '1..2', '.', '+', '1.2.3']) {
    assert.equal(isDecimal(bad), false, bad);
    assert.equal(decAdd(bad, '0.01'), null, bad);
  }
  assert.equal(decAdd('3.', '1'), '4');
  assert.equal(decAdd('.5', '1'), '1.5');
});

test('decimal ranks order exactly, beyond binary floats', () => {
  const values = ['9007199254740993', '9007199254740992', '1.5', '1.25', '-2', null];
  const ranks = decRanks(values);
  assert.ok(ranks[1] < ranks[0], '…992 before …993');
  assert.ok(ranks[3] < ranks[2], '1.25 before 1.5');
  assert.ok(ranks[4] < ranks[3], 'negative first');
  assert.equal(ranks[5], null, 'unknown has no rank');
});

test('B shares cannot be entered: the backend’s own words, as soon as the ticker is known', () => {
  const source = readFileSync(join(ROOT, 'core', 'ledger.py'), 'utf8');
  assert.equal(B_SHARE_REFUSAL, /^B_SHARE_REFUSAL = '(.*)'$/m.exec(source)?.[1]);
  const instruments = readFileSync(join(ROOT, 'core', 'market', 'instruments.py'), 'utf8');
  assert.match(instruments, /^B_SHARE_PREFIXES = \{'SHA': \('900',\), 'SHE': \('200', '201'\)\}$/m);
  assert.equal(bShareRefusal('SHA:900901'), 'SHA:900901 是 B 股，以 USD 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。');
  assert.match(bShareRefusal('SHE:200002'), /以 HKD 交易/);
  assert.match(bShareRefusal('SHE:201872'), /以 HKD 交易/);
  for (const ticker of ['SHA:600901', 'SHE:000002', 'HKG:00900', 'SHA:DEMO', null]) assert.equal(bShareRefusal(ticker), null, ticker);
  assert.equal(validateEntry(buy({ ticker: 'SHA:900901', price: '0.498' }), { ...CONTEXT, ticker: 'SHA:900901' }).ticker, bShareRefusal('SHA:900901'));
  const dividend = emptyValues({ kind: 'dividend', account: '长期账户', date: '2026-11-17', ticker: 'SHE:200002', amount: '5' });
  assert.equal(validateEntry(dividend, { ...CONTEXT, ticker: 'SHE:200002' }).ticker, bShareRefusal('SHE:200002'));
  const opening = emptyValues({ kind: 'opening', account: '新账户', date: '2026-11-17', contributed: '0',
    positions: [{ ticker: 'SHA:900901', quantity: '100', cost: '1' }] });
  assert.equal(validateEntry(opening, CONTEXT)['positions.0.ticker'], bShareRefusal('SHA:900901'));
});

test('an estimated fee is booked as the estimate and says so in the note; a typed one does not', () => {
  const context = { ...CONTEXT, feeEstimate: '5.01', feeBasis: '按账户费率估算' };
  assert.equal(buildEvents(buy(), context)[0].note, '〔费用为估算：按账户费率估算〕');
  assert.equal(buildEvents(buy({ note: '分批建仓' }), context)[0].note, '分批建仓 〔费用为估算：按账户费率估算〕');
  const typed = buildEvents(buy({ fee: '0' }), context)[0];
  assert.equal(typed.fee, '0', 'a typed 0 is kept');
  assert.equal(typed.note, undefined);
  assert.equal(validateEntry(buy({ note: 'x'.repeat(230) }), context).note, '备注最多 224 个字（另有 16 个字的记账依据）');
});

test('a quote-filled price carries its origin until it is edited, and goes with its ticker', () => {
  const fill = { ticker: 'SHA:600519', basis: 'prev_close', value: '1680', source: '模拟行情', time: '2026-11-16T15:00:00+08:00', estimated: true };
  const values = buy({ fee: '5', priceFill: fill });
  assert.equal(buildEvents(values, CONTEXT)[0].note, '〔价格取自昨收：模拟行情 2026-11-16 15:00，估算〕');
  assert.equal(buildEvents({ ...values, price: '1681' }, CONTEXT)[0].note, undefined, 'edited: no longer the quote');
  const fresh = { ...fill, basis: 'price', estimated: false, time: '2026-11-17T10:32:02+08:00' };
  assert.equal(buildEvents(buy({ fee: '5', priceFill: fresh }), CONTEXT)[0].note, '〔价格取自现价：模拟行情 2026-11-17 10:32〕');
  const moved = withTicker(values, 'HKG:00700', 'HKG:00700');
  assert.deepEqual([moved.ticker, moved.price, moved.priceFill], ['HKG:00700', '', null]);
  const typed = withTicker(buy({ price: '20' }), 'SHA:603999', 'SHA:603999');
  assert.equal(typed.price, '20', 'a typed price stays');
  assert.equal(withTicker(values, 'sha:600519', 'SHA:600519').price, '1680', 'the same ticker keeps its fill');
});

test('an FX rate is evidence: changing the currency clears it; a reference rate is only used when accepted, and says so', () => {
  const deposit = emptyValues({ kind: 'deposit', account: '长期账户', date: '2026-11-17', currency: 'USD', amount: '100', fxRate: '7.20' });
  const hkd = withCurrency(deposit, 'HKD');
  assert.deepEqual([hkd.currency, hkd.fxRate, hkd.rateFill], ['HKD', '', null]);
  assert.match(validateEntry(hkd, CONTEXT).fxRate, /1 HKD = \? CNY/);
  assert.equal(withCurrency(deposit, 'USD'), deposit, 'the same currency changes nothing');
  const accepted = { ...deposit, fxRate: '7.05', rateFill: { value: '7.05', source: '模拟行情', time: '2026-11-19T10:32:00+08:00' } };
  assert.equal(buildEvents(accepted, CONTEXT)[0].note, '〔汇率取自参考汇率：模拟行情 2026-11-19 10:32〕');
  assert.equal(buildEvents({ ...accepted, fxRate: '7.2' }, CONTEXT)[0].note, undefined);
});

test('the holding is checked only once the ledger at the entry’s date is known', () => {
  assert.deepEqual(validateEntry(buy({ kind: 'sell', quantity: '20' }), { ...CONTEXT, held: undefined }), {});
  assert.equal(validateEntry(buy({ kind: 'sell', quantity: '20' }), { ...CONTEXT, held: '10' }).quantity, '超过可卖数量 10');
  assert.equal(cashEffect(buy(), 'SHA:600519', undefined, '5').after, null, 'unknown cash is never 0');
  assert.equal(cashEffect(buy(), 'SHA:600519', undefined, '5').short, false);
});

test('backend digits are shown as they are: review lines and changes', () => {
  const etf = { id: 'e', kind: 'buy', date: '2026-11-17', account: '长期账户', ticker: 'SHA:510300', currency: 'CNY', quantity: '100', price: '1.234', fee: '0.004' };
  assert.equal(eventLine(etf), '2026-11-17 买入 SHA:510300 100 股 @ 1.234 · 费用 0.004 · 长期账户');
  const estimated = { ...etf, fee: '5.01', note: '分批 〔费用为估算：按账户费率估算〕' };
  assert.equal(eventLine(estimated), '2026-11-17 买入 SHA:510300 100 股 @ 1.234 · 费用 5.01（估算） · 长期账户 · 分批');
  const before = { date: null, accounts: { 甲: { cash_balances: { CNY: '100' }, positions: { 'SHA:1': { ticker: 'SHA:1', name: 'SHA:1', quantity: '1', cost_price: '12.345679', currency: 'CNY' } }, contributed_cny: '10', fund_cny: '0', realized: {} } } };
  const after = structuredClone(before);
  after.accounts.甲.cash_balances.CNY = '100.004';
  after.accounts.甲.contributed_cny = '10.0001';
  after.accounts.甲.positions['SHA:1'].cost_price = '12.3456789';
  const rows = changeRows({ before, after })[0].rows;
  assert.deepEqual(rows.map((row) => [changeValue(row, 'before'), changeValue(row, 'after')]), [
    ['¥100.00', '¥100.004'], ['12.345679', '12.3456789'], ['¥10.00', '¥10.0001'],
  ]);
  // Past 8 places (an average cost that repeats, a price worked out as 成交金额 ÷ 数量): ≈ rounded to 8, the full
  // value kept for the title and screen readers.
  after.accounts.甲.positions['SHA:1'].cost_price = '61.2166666666666666666666667';
  const cost = changeRows({ before, after })[0].rows.find((row) => row.key === 'cost:SHA:1');
  assert.deepEqual(changeFigure(cost, 'after'), { text: '≈61.21666667', full: '61.2166666666666666666666667',
    label: '约 61.21666667，完整值 61.2166666666666666666666667' });
  assert.deepEqual(changeFigure(cost, 'before'), { text: '12.345679' });
  const derived = { ...etf, ticker: 'SHA:600036', quantity: '300', price: '32.016666666666666666666666667', fee: '5.1' };
  assert.equal(eventLine(derived), '2026-11-17 买入 SHA:600036 300 股 @ ≈32.01666667 · 费用 5.10 · 长期账户');
  assert.deepEqual(eventLineShown(derived), {
    text: '2026-11-17 买入 SHA:600036 300 股 @ ≈32.01666667 · 费用 5.10 · 长期账户',
    full: '2026-11-17 买入 SHA:600036 300 股 @ 32.016666666666666666666666667 · 费用 5.10 · 长期账户',
    label: '2026-11-17 买入 SHA:600036 300 股 @ 32.016666666666666666666666667 · 费用 5.10 · 长期账户',
  });
  assert.deepEqual(eventLineShown(etf), { text: '2026-11-17 买入 SHA:510300 100 股 @ 1.234 · 费用 0.004 · 长期账户' });
});

test('every event of a large proposal can be shown', () => {
  const events = Array.from({ length: 51 }, (_, index) => ({ id: String(index) }));
  assert.equal(shownEvents(events, false).length, 50);
  assert.equal(shownEvents(events, true).length, 51);
});

test('a plan-weight warning carries the time of the quotes it used', () => {
  const [line] = planCheckLines([{ event_ref: 'e', index: 0, kind: 'buy', date: '2026-11-17', ticker: 'SHA:DEMO', account: null, quantity: '100',
    price: '1', plan_id: 3, relation: 'add', deviations: [{ code: 'over_planned_weight', level: 'warning', message: '按当前行情，买入后该持仓约占组合 30.00%。',
      data: { quotes_as_of: '2026-11-17T10:32:05+08:00', price_basis: 'current_quotes' } }] }]);
  assert.equal(line.deviations[0].asOf, '行情时间 2026-11-17 10:32');
});

test('outdated means below the ledger revision, in the list and in the review alike', () => {
  assert.equal(isOutdated(4, 5), true);
  assert.equal(isOutdated(5, 5), false);
  assert.equal(isOutdated(5, 4), false, 'a newer preview than the cached ledger is not outdated');
  assert.equal(isOutdated(5, undefined), false);
});

test('a confirmation that got no answer is not a refusal', () => {
  assert.equal(confirmFailure(409), 'refused');
  assert.equal(confirmFailure(422), 'refused');
  assert.equal(confirmFailure(404), 'refused');
  for (const status of [undefined, 500, 502, 503, 504, 408]) assert.equal(confirmFailure(status), 'unknown', String(status));
});

test('流水: a transfer from the filtered account’s side, reversals of its events, bounded date ranges, sorting before paging', () => {
  assert.deepEqual(eventCash(EVENTS[3], '长期账户'), { amount: '20', currency: 'CNY' }, 'the receiving side gains');
  assert.deepEqual(eventCash(EVENTS[3], '研究账户'), { amount: '-20', currency: 'CNY' });
  const later = [...EVENTS, { id: 'future', kind: 'deposit', date: '2026-11-20', account: '研究账户', currency: 'CNY', amount: '1' },
    { id: 'dec', kind: 'deposit', date: '2026-12-01', account: '研究账户', currency: 'CNY', amount: '1' }];
  const all = { account: '', kind: '', query: '', range: 'all' };
  assert.deepEqual(filterEvents(later, { ...all, range: 'week' }, '2026-11-17').map((e) => e.id), ['d', 't', 'r']);
  assert.deepEqual(filterEvents(later, { ...all, range: 'month' }, '2026-11-17').map((e) => e.id), ['b', 'd', 't', 'r']);
  assert.deepEqual(rangeBounds('week', '2026-11-17'), { start: '2026-11-16', end: '2026-11-17' });
  assert.equal(rangeBounds('all', '2026-11-17'), null);
  assert.equal(filterEvents(later, { ...all, range: 'week' }, null).length, later.length, 'no date presets without the backend’s today');
  const many = Array.from({ length: 201 }, (_, index) => ({ id: String(index), kind: 'deposit', date: `2026-${String(1 + (index % 12)).padStart(2, '0')}-${String(1 + (index % 28)).padStart(2, '0')}`, _sequence: index }));
  const oldest = sortEvents(many, 'asc').slice(0, 200);
  assert.equal(oldest[0].date, '2026-01-01');
  assert.equal(sortEvents(many, 'desc')[0].date, '2026-12-28');
});

test('notes keep their words; the booking marks are shown apart', () => {
  assert.equal(noteText('分批 〔费用为估算：按账户费率估算〕〔价格取自昨收：模拟行情 2026-11-16 15:00，估算〕'), '分批');
  assert.deepEqual(noteMarks('分批 〔费用为估算：按账户费率估算〕〔价格取自昨收：模拟行情 2026-11-16 15:00，估算〕'),
    ['费用为估算：按账户费率估算', '价格取自昨收：模拟行情 2026-11-16 15:00，估算']);
  assert.deepEqual(noteMarks('自己写的〔括号〕'), []);
  assert.equal(noteText('自己写的〔括号〕'), '自己写的〔括号〕');
});

test('an account sold out keeps its realised profit in view', () => {
  const accounts = {
    甲: { cash_balances: { CNY: '20' }, positions: {}, contributed_cny: '10', fund_cny: '0', realized: { CNY: '10' } },
    乙: { cash_balances: {}, positions: {}, contributed_cny: '0', fund_cny: '0', realized: { CNY: '0' } },
    丙: BEFORE.accounts.长期账户,
  };
  assert.deepEqual(closedAccounts(accounts), [['甲', [['CNY', '10']]]]);
});

test('every message the ledger and its API can return reads in Chinese', () => {
  const sources = [join(ROOT, 'core', 'ledger.py'), join(ROOT, 'backend', 'api', 'ledger.py')].map((path) => readFileSync(path, 'utf8'));
  const messages = new Set();
  for (const source of sources) {
    for (const match of source.matchAll(/(?:LedgerError|Conflict|StaleProposal)\((f?)("|')((?:(?!\2).)*)\2/g)) messages.add(match[3]);
    for (const match of source.matchAll(/HTTPException\(\d+, (f?)("|')((?:(?!\2).)*)\2/g)) messages.add(match[3]);
  }
  assert.ok(messages.size >= 40, `found ${messages.size} messages`);
  const english = [...messages].filter((message) => !/[一-鿿]/.test(message));
  const missing = [];
  for (const raw of english) {
    const sample = raw.replace(/\{[^}]*\}/g, 'X1');
    const text = ledgerErrorText(sample);
    if (!/[一-鿿]/.test(text) || /[A-Za-z]{4,}/.test(text.replace(/YYYY-MM-DD|NASDAQ|AAPL|CSV|UTF|external_id|allow_margin|cash_balances|JSON|CNY|HKD|USD|X1/g, ''))) missing.push(raw);
  }
  assert.deepEqual(missing, []);
  assert.match(ledgerErrorText('Demo mode is read-only'), /演示模式/);
  assert.match(ledgerErrorText('events: List should have at least 1 item after validation, not 0'), /格式/);
});

test('fee profiles: the server’s bounds and digits, shown exactly', () => {
  const form = profileToForm({ broker: '', a_share: { commission_rate: '0.00025', commission_min: '5', waive_minimum: false }, hk: null, us: null });
  assert.equal(formToProfile({ ...form, commissionRate: '101' }).errors.commissionRate, '佣金率最高 100‱（成交额的 1%）');
  assert.equal(formToProfile({ ...form, commissionRate: '100' }).profile.a_share.commission_rate, '0.01');
  assert.equal(formToProfile({ ...form, commissionRate: '2.1234567' }).errors.commissionRate, '佣金率最多 6 位小数（万分之）');
  assert.equal(formToProfile({ ...form, commissionMin: '5.00001' }).errors.commissionMin, '最多 4 位小数');
  assert.equal(formToProfile({ ...form, commissionMin: '100001' }).errors.commissionMin, '最多 100,000');
  const hk = { ...form, hk: { enabled: true, buy: { rate: '101', min: '0', fixed: '100000.00001' }, sell: { rate: '', min: '', fixed: '' } } };
  assert.equal(formToProfile(hk).errors['hk.buy.rate'], '费率最高 100‱（成交额的 1%）');
  assert.equal(formToProfile(hk).errors['hk.buy.fixed'], '最多 4 位小数');
  const exact = { broker: '', a_share: { commission_rate: '0.00025', commission_min: '5.0049', waive_minimum: false }, hk: null, us: null };
  assert.equal(profileSummary(exact), '万2.5 · 最低 ¥5.0049');
  assert.equal(feeHint({ total: '5.015', parts: [{ label: '佣金', amount: '5.004' }, { label: '过户费', amount: '0.011' }], basis: '按账户费率估算', reason: null }),
    '按账户费率估算：佣金 5.004 · 过户费 0.011');
});

test('the phone event card keeps the full value of a shortened quantity in its line', async () => {
  const { cardLine } = await import('../src/features/ledger/events.ts');
  const long = cardLine({ date: '2026-11-17', account: '长期账户', quantity: '300.1234567891', source: 'manual' });
  assert.equal(long.text, '2026-11-17 · 长期账户 · ≈300.12345679 股 · 手工');
  assert.equal(long.full, '2026-11-17 · 长期账户 · 300.1234567891 股 · 手工');
  // A quantity that fits is the line as it was, with nothing to hover.
  const plain = cardLine({ date: '2026-11-17', account: '长期账户', quantity: '300', source: 'manual' });
  assert.deepEqual(plain, { text: '2026-11-17 · 长期账户 · 300 股 · 手工' });
  assert.deepEqual(cardLine({ date: '2026-11-17', account: '长期账户', source: 'manual' }), { text: '2026-11-17 · 长期账户 · 手工' });
});
