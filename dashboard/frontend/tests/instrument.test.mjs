// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// Instrument search on the client (src/lib/instrument.ts): the exchange inference that mirrors
// dashboard/core/importers/codes.py, how a result reads, the order of the list and what an exact
// typed code resolves to.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import {
  applyChosenExchanges, arrangeResults, codeMatches, codeOf, describeResult, enterAction, exactMatch, identityKey, identityOf,
  inferInstrument, inferredResults, instrumentLabel, isExchangeChoice, moveActive, normalizeQuery, optionKey, optionsOf, qualifiedInput,
  sameTicker, typedChoice,
} from '../src/lib/instrument.ts';

function result(ticker, overrides = {}) {
  const exchange = ticker ? ticker.split(':')[0] : null;
  return {
    ticker, name: null, exchange, currency: { SHA: 'CNY', SHE: 'CNY', HKG: 'HKD', NASDAQ: 'USD', NYSE: 'USD', IDX: 'CNY' }[exchange] ?? null,
    kind: 'stock', supported: true, reason: null, needs_confirmation: false, in_holdings: false, in_watchlist: false,
    matched_by: 'index', ...overrides,
  };
}

test('the prefix table: six-digit codes go to their exchange, never to an index', () => {
  const cases = [
    ['600519', 'SHA:600519', 'stock'], ['688981', 'SHA:688981', 'stock'], ['510300', 'SHA:510300', 'etf'],
    ['500001', 'SHA:500001', 'fund'], ['520500', 'SHA:520500', 'etf'], ['563000', 'SHA:563000', 'etf'],
    ['588000', 'SHA:588000', 'etf'], ['113050', 'SHA:113050', 'bond'],
    ['000001', 'SHE:000001', 'stock'], ['300750', 'SHE:300750', 'stock'], ['159915', 'SHE:159915', 'etf'],
    ['161725', 'SHE:161725', 'fund'], ['184688', 'SHE:184688', 'fund'], ['128136', 'SHE:128136', 'bond'],
  ];
  for (const [code, ticker, kind] of cases) {
    const inferred = inferInstrument(code);
    assert.equal(inferred.ticker, ticker, code);
    assert.equal(inferred.kind, kind, code);
    assert.equal(inferred.supported, true, code);
    assert.equal(inferred.currency, 'CNY', code);
  }
});

test('codes the ledger cannot hold are unsupported with a reason', () => {
  for (const [code, words] of [
    ['430047', '北交所'], ['830799', '北交所'], ['920001', '北交所'], ['900901', 'B 股'], ['200002', 'B 股'],
    ['204001', '逆回购'], ['131810', '逆回购'], ['190001', '无法'], ['730001', '申购'], ['399001', '指数'],
  ]) {
    const inferred = inferInstrument(code);
    assert.equal(inferred.supported, false, code);
    assert.ok(inferred.reason.includes(words), `${code}: ${inferred.reason}`);
  }
  assert.equal(inferInstrument('BJ430047').exchange, 'BSE');
  assert.equal(inferInstrument('SHA:000001').supported, false, 'the 上证指数 range on Shanghai');
  assert.match(inferInstrument('SHA:000001').reason, /IDX:000001/);
  assert.match(inferInstrument('SHA:000001x')?.reason ?? '', /6 位/);
  assert.match(inferInstrument('SHA:000858').reason, /指数编号段/);
  assert.equal(inferInstrument('SHE:000858').ticker, 'SHE:000858');
  assert.match(inferInstrument('SHA:000651').reason, /指数编号段/);
  assert.match(inferInstrument('SHE:600519').reason, /应写作 SHA:600519/);
  assert.equal(inferInstrument('SHE:600519').supported, false);
});

test('Hong Kong: up to five digits, padded, or the form the ledger already uses', () => {
  assert.equal(inferInstrument('700').ticker, 'HKG:00700');
  assert.equal(inferInstrument('0700').ticker, 'HKG:00700');
  assert.equal(inferInstrument('700', ['HKG:0700']).ticker, 'HKG:0700');
  assert.equal(inferInstrument('hkg:00700', ['HKG:0700']).ticker, 'HKG:0700');
  assert.equal(inferInstrument('00700.HK').ticker, 'HKG:00700');
  assert.equal(inferInstrument('700').currency, 'HKD');
  assert.equal(inferInstrument('0').supported, false);
});

test('letters are a US symbol: NASDAQ or NYSE, unless the ledger or an earlier choice says which', () => {
  const open = inferInstrument('aapl');
  assert.equal(open.ticker, null);
  assert.deepEqual(open.choices, ['NASDAQ:AAPL', 'NYSE:AAPL']);
  assert.equal(open.currency, 'USD');
  assert.equal(inferInstrument('AAPL', ['NASDAQ:AAPL']).ticker, 'NASDAQ:AAPL');
  assert.equal(inferInstrument('BRK.B', ['NYSE:BRK.B']).ticker, 'NYSE:BRK.B');
  assert.equal(inferInstrument('nyse:ko').ticker, 'NYSE:KO');
  assert.equal(inferInstrument('IDX:000300').ticker, 'IDX:000300');
  assert.equal(inferInstrument('IDX:HSI').currency, 'HKD');
  assert.equal(inferInstrument('IDX:123456').supported, false);
  assert.equal(inferInstrument('XYZ:1').supported, false);
  assert.equal(inferInstrument('茅台'), null, 'a name is not a code');
  assert.equal(inferInstrument(''), null);
});

test('typed text is cleaned: full-width characters, spaces, case', () => {
  assert.equal(normalizeQuery(' ６００５１９ '), '600519');
  assert.equal(qualifiedInput('sha:600519'), 'SHA:600519');
  assert.equal(qualifiedInput('ＳＨＡ：600519'), 'SHA:600519');
  assert.equal(qualifiedInput('600519'), null);
  assert.equal(codeOf('SHA:600519'), '600519');
  assert.ok(sameTicker('HKG:0700', 'hkg:00700'));
  assert.ok(!sameTicker('HKG:0700', 'HKG:0701'));
  assert.ok(!sameTicker(null, 'SHA:600519'));
});

test('a result reads 名称 代码 · 市场 · 币种 with quiet tags; unusable ones are disabled with their reason', () => {
  const held = describeResult(result('SHA:600519', { name: '贵州茅台', in_holdings: true, in_watchlist: true }));
  assert.equal(held.title, '贵州茅台');
  assert.equal(held.meta, '600519 · 上交所 · CNY');
  assert.deepEqual(held.tags, ['持仓', '自选']);
  assert.equal(held.disabled, false);
  assert.equal(held.note, null);
  const etf = describeResult(result('SHE:159915', { name: '创业板ETF', kind: 'etf' }));
  assert.deepEqual(etf.tags, ['ETF']);
  const bare = describeResult(result('HKG:00700'));
  assert.equal(bare.title, '00700');
  assert.equal(bare.meta, '港股 · HKD');
  const bse = describeResult(result(null, { exchange: 'BSE', currency: 'CNY', supported: false, reason: '北交所证券：账本暂不支持北交所。' }), '430047');
  assert.equal(bse.title, '430047');
  assert.equal(bse.disabled, true);
  assert.match(bse.note, /北交所/);
  const check = describeResult(result('SHA:600999', { needs_confirmation: true, reason: '名称索引中没有这个代码，请核对后再使用。' }));
  assert.equal(check.disabled, false);
  assert.match(check.note, /核对/);
  assert.equal(instrumentLabel('SHA:600519', '贵州茅台'), '贵州茅台 SHA:600519');
  assert.equal(instrumentLabel('SHA:600519', 'SHA:600519'), 'SHA:600519');
});

test('the list keeps the backend order, applies the filter, drops repeats and heads a NASDAQ / NYSE pair', () => {
  const results = [
    result('SHA:600519', { name: '贵州茅台', matched_by: 'holdings', in_holdings: true }),
    result('HKG:0700', { name: '腾讯控股', matched_by: 'watchlist' }),
    result('HKG:00700', { name: '腾讯控股' }),
    result('NASDAQ:ABC', { needs_confirmation: true, reason: '美股代码需要选择交易所' }),
    result('NYSE:ABC', { needs_confirmation: true, reason: '美股代码需要选择交易所' }),
    result(null, { exchange: 'BSE', supported: false, reason: '北交所' }),
  ];
  const items = arrangeResults(results);
  assert.deepEqual(items.map((item) => (item.type === 'heading' ? `# ${item.text}` : item.result.ticker)), [
    'SHA:600519', 'HKG:0700', '# ABC 在哪个交易所？选择后会记住', 'NASDAQ:ABC', 'NYSE:ABC', null,
  ]);
  assert.equal(optionsOf(items).length, 5);
  assert.ok(isExchangeChoice(results[3]));
  assert.ok(!isExchangeChoice(results[0]));
  const held = arrangeResults(results, (item) => item.in_holdings);
  assert.deepEqual(optionsOf(held).map((item) => item.ticker), ['SHA:600519']);
});

test('arrow keys wrap around the options; Home and End jump', () => {
  assert.equal(moveActive(3, -1, 'ArrowDown'), 0);
  assert.equal(moveActive(3, 2, 'ArrowDown'), 0);
  assert.equal(moveActive(3, -1, 'ArrowUp'), 2);
  assert.equal(moveActive(3, 0, 'ArrowUp'), 2);
  assert.equal(moveActive(3, 1, 'Home'), 0);
  assert.equal(moveActive(3, 1, 'End'), 2);
  assert.equal(moveActive(0, -1, 'ArrowDown'), -1);
});

test('an exact typed code resolves only when it is one usable listing', () => {
  const results = [
    result('SHA:600519', { name: '贵州茅台', matched_by: 'input' }),
    result('SHA:600518', { name: '康美药业' }),
  ];
  assert.equal(exactMatch('sha:600519', results)?.name, '贵州茅台');
  assert.equal(exactMatch('600519', results)?.ticker, 'SHA:600519');
  assert.equal(exactMatch('60051', results), null, 'a prefix is not a match');
  assert.equal(exactMatch('茅台', results), null);
  assert.equal(exactMatch('700', [result('HKG:0700', { name: '腾讯控股' })])?.ticker, 'HKG:0700');
  assert.equal(exactMatch('HKG:00700', [result('HKG:0700')])?.ticker, 'HKG:0700');
  const pair = [result('NASDAQ:ABC', { needs_confirmation: true }), result('NYSE:ABC', { needs_confirmation: true })];
  assert.equal(exactMatch('ABC', pair), null, 'the exchange must be picked');
  assert.equal(exactMatch('ABC', [result('NYSE:ABC')])?.ticker, 'NYSE:ABC');
  assert.equal(exactMatch('430047', [result(null, { supported: false })]), null);
  assert.equal(exactMatch('SHA:600999', [result('SHA:600999', { needs_confirmation: true })]), null);
});

test('when the search request fails, an exact code is still offered from the rule alone', () => {
  assert.deepEqual(inferredResults('600519').map((item) => [item.ticker, item.supported, item.matched_by]), [['SHA:600519', true, 'input']]);
  assert.deepEqual(inferredResults('abc').map((item) => [item.ticker, item.needs_confirmation]), [['NASDAQ:ABC', true], ['NYSE:ABC', true]]);
  assert.deepEqual(inferredResults('茅台'), []);
  assert.equal(inferredResults('430047')[0].supported, false);
});

test('an exchange picked on the page collapses the NASDAQ / NYSE pair at once', () => {
  const pair = [
    result('NASDAQ:ABC', { needs_confirmation: true, reason: '美股代码需要选择交易所' }),
    result('NYSE:ABC', { needs_confirmation: true, reason: '美股代码需要选择交易所' }),
    result('SHA:600519', { name: '贵州茅台' }),
  ];
  const applied = applyChosenExchanges(pair, new Map([['ABC', 'NYSE:ABC']]));
  assert.deepEqual(applied.map((item) => [item.ticker, item.needs_confirmation, item.reason]), [
    ['NYSE:ABC', false, null], ['SHA:600519', false, null],
  ]);
  assert.equal(applyChosenExchanges(pair, new Map()), pair);
  assert.equal(applyChosenExchanges(pair, new Map([['XYZ', 'NYSE:XYZ']])).length, 3);
});

// -- one listing, one key (review r3) -------------------------------------------------------------

const VECTORS = JSON.parse(readFileSync(new URL('../../../tests/fixtures/instrument_identity.json', import.meta.url), 'utf8'));

test('the identity fallback gives the backend identity_key for every shared vector', () => {
  assert.ok(VECTORS.vectors.length >= 20);
  for (const [given, expected] of VECTORS.vectors) assert.equal(identityKey(given), expected, JSON.stringify(given));
});

test('a payload identity wins; the rule is only the fallback; BRK.B and BRK-B are one listing', () => {
  assert.equal(identityOf({ ticker: 'NYSE:BRK-B', identity: 'NYSE:BRK.B' }), 'NYSE:BRK.B');
  assert.equal(identityOf({ ticker: 'NYSE:BRK-B' }), 'NYSE:BRK.B');
  assert.equal(identityOf({ ticker: 'NYSE:BRK-B', identity: 'SERVER:SAYS' }), 'SERVER:SAYS', 'the backend decides');
  assert.ok(sameTicker('NYSE:BRK-B', 'nyse:brk.b'));
  assert.ok(!sameTicker('NYSE:BRK.B', 'NASDAQ:BRK.B'));
  assert.equal(exactMatch('BRK-B', [result('NYSE:BRK.B')])?.ticker, 'NYSE:BRK.B');
});

test('Hong Kong codes longer than five digits are refused even with a .HK suffix (the fallback picker)', () => {
  for (const text of ['100000.HK', 'HK100000', '000700.HK']) {
    const inferred = inferInstrument(text);
    assert.equal(inferred.supported, false, text);
    assert.equal(inferred.ticker, null, text);
    assert.match(inferred.reason, /1 到 5 位/, text);
    assert.deepEqual(inferredResults(text).map((item) => item.supported), [false], text);
  }
  assert.equal(inferInstrument('00700.HK').ticker, 'HKG:00700');
});

test('options are keyed by listing, so a highlight can follow its own row', () => {
  assert.equal(optionKey(result('HKG:0700')), optionKey(result('HKG:00700')));
  assert.notEqual(optionKey(result('SHE:000001')), optionKey(result('IDX:000001', { kind: 'index' })));
  assert.equal(typeof optionKey(result(null, { exchange: 'BSE', supported: false, reason: '北交所' })), 'string');
});

test('a bare code on two listings is ambiguous: no exact match, nothing taken on Enter', () => {
  const both = [result('SHE:000001', { name: '平安银行' }), result('IDX:000001', { name: '上证指数', kind: 'index' })];
  assert.equal(codeMatches('000001', both).length, 2);
  assert.equal(exactMatch('000001', both), null);
  assert.deepEqual(enterAction({ value: '000001', active: null, fresh: true, options: both, pickFirst: true }), { kind: 'stay' });
  assert.equal(enterAction({ value: 'SHE:000001', active: null, fresh: true, options: both, pickFirst: true }).result?.ticker, 'SHE:000001');
});

test('Enter takes the highlighted row the user sees, else waits for this text\'s answer, else an unambiguous row', () => {
  const rows = [result('SHA:600519', { name: '贵州茅台' }), result('SHA:600518', { name: '康美药业' })];
  const pick = (input) => enterAction({ value: '', active: null, fresh: true, options: rows, pickFirst: false, ...input });
  assert.equal(pick({ value: '6005', active: rows[1] }).result.ticker, 'SHA:600518', 'the highlighted row');
  assert.deepEqual(pick({ value: '6005', active: result(null, { supported: false }) }), { kind: 'stay' });
  assert.deepEqual(pick({ value: '6005', fresh: false }), { kind: 'wait' }, 'rows of the previous text: wait');
  assert.deepEqual(pick({ value: '6005' }), { kind: 'stay' }, 'two rows, none highlighted, no exact code');
  assert.equal(pick({ value: '6005', pickFirst: true }).result.ticker, 'SHA:600519', 'the global search opens the first row');
  assert.equal(pick({ value: '600519' }).result.ticker, 'SHA:600519', 'the exact code on screen');
  assert.equal(pick({ value: '茅台', options: rows.slice(0, 1) }).result.ticker, 'SHA:600519', 'the only row on screen');
  assert.deepEqual(pick({ value: '  ' }), { kind: 'stay' });
});

test('typed text is taken without a pick only as a complete EXCH:CODE that is one usable listing on screen', () => {
  const rows = [result('SHA:600519', { name: '贵州茅台' }), result('HKG:00060', { name: '示例港股' })];
  assert.equal(typedChoice('sha:600519', rows)?.ticker, 'SHA:600519');
  assert.equal(typedChoice('600519', rows), null, 'a bare code stays text');
  assert.equal(typedChoice('60', rows), null, 'two digits are not a pick of HKG:00060');
  assert.equal(typedChoice('茅台', rows), null);
  assert.equal(typedChoice('SHA:000001', [result('SHA:000001', { supported: false, reason: '指数编号段' })]), null);
  assert.equal(typedChoice('NYSE:ABC', [result('NYSE:ABC', { needs_confirmation: true })]), null);
  // The field's filter applies first: a stock held only in another account is not on screen.
  const sellable = arrangeResults(rows, (item) => item.ticker === 'HKG:00060');
  assert.equal(typedChoice('SHA:600519', optionsOf(sellable)), null);
});
