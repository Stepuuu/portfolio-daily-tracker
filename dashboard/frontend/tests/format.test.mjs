// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  SHOWN_PLACES, UNAVAILABLE, currencySymbol, fmtCompact, fmtDate, fmtDateTime, fmtLong, fmtMoney, fmtNumber, fmtPct, fmtPrice, fmtQty,
  fmtRelative, fmtTime, longLine, longLineOrNull, normalizeNumberInput, ratioPercent, parseNumberInput, priceDigits, stepNumberInput, toneOf,
} from '../src/lib/format.ts';

const MISSING = [null, undefined, '', '   ', 'abc', '1,234', NaN, Infinity, -Infinity, '1e', '.', '-'];

test('a missing or unreadable value is 不可用 in every formatter, never 0', () => {
  assert.equal(UNAVAILABLE, '不可用');
  for (const value of MISSING) {
    const label = String(value);
    assert.equal(fmtNumber(value), '不可用', label);
    assert.equal(fmtMoney(value, 'CNY'), '不可用', label);
    assert.equal(fmtCompact(value, 'CNY'), '不可用', label);
    assert.equal(fmtPrice(value, 'HKD'), '不可用', label);
    assert.equal(fmtQty(value), '不可用', label);
    assert.equal(fmtPct(value, { signed: true }), '不可用', label);
  }
  for (const value of [null, undefined, '', 'not a date']) {
    assert.equal(fmtTime(value), '不可用');
    assert.equal(fmtDate(value), '不可用');
    assert.equal(fmtDateTime(value), '不可用');
    assert.equal(fmtRelative(value), '不可用');
  }
});

test('money carries its own currency symbol', () => {
  assert.equal(fmtMoney('1234.5', 'CNY'), '¥1,234.50');
  assert.equal(fmtMoney(1234.5, 'CNY'), '¥1,234.50');
  assert.equal(fmtMoney('1234567.891', 'HKD'), 'HK$1,234,567.89');
  assert.equal(fmtMoney('12', 'USD'), '$12.00');
  assert.equal(fmtMoney('-20', 'HKD'), '-HK$20.00');
  assert.equal(fmtMoney('0', 'CNY'), '¥0.00');
  assert.equal(fmtMoney('5', 'EUR'), 'EUR 5.00');
  assert.equal(fmtMoney('1234.5678', 'CNY', { digits: 4 }), '¥1,234.5678');
  assert.equal(fmtMoney('1234.5', 'CNY', { digits: 0 }), '¥1,235');
  assert.deepEqual(['CNY', 'HKD', 'USD'].map(currencySymbol), ['¥', 'HK$', '$']);
});

test('signed values: + for gains, - for losses, no sign for zero (and -0 is zero)', () => {
  assert.equal(fmtMoney('12345', 'CNY', { signed: true }), '+¥12,345.00');
  assert.equal(fmtMoney('-12345', 'CNY', { signed: true }), '-¥12,345.00');
  assert.equal(fmtMoney('0', 'CNY', { signed: true }), '¥0.00');
  assert.equal(fmtMoney(-0, 'CNY', { signed: true }), '¥0.00');
  assert.equal(fmtMoney('-0.00', 'CNY', { signed: true }), '¥0.00');
  assert.equal(fmtMoney('-0.004', 'CNY', { signed: true }), '¥0.00');
  assert.equal(fmtPct('1.234', { signed: true }), '+1.23%');
  assert.equal(fmtPct('-0.5', { signed: true }), '-0.50%');
  assert.equal(fmtPct(0, { signed: true }), '0.00%');
  assert.equal(fmtPct(-0, { signed: true }), '0.00%');
  assert.equal(fmtPct('1.234'), '1.23%');
  assert.equal(fmtNumber('4.59', { signed: true }), '+4.59');
});

test('percentages are in percent units unless marked as a ratio', () => {
  assert.equal(fmtPct('12.3456'), '12.35%');
  assert.equal(fmtPct('0.0123', { ratio: true, signed: true }), '+1.23%');
  assert.equal(fmtPct(-0.048, { ratio: true }), '-4.80%');
  assert.equal(fmtPct('8.7', { digits: 1 }), '8.7%');
});

test('decimal strings round half away from zero, digit by digit', () => {
  assert.equal(fmtMoney('1.005', 'CNY'), '¥1.01');
  assert.equal(fmtMoney('-1.005', 'CNY'), '-¥1.01');
  assert.equal(fmtMoney('2.675', 'CNY'), '¥2.68');
  assert.equal(fmtMoney('999.995', 'USD'), '$1,000.00');
  assert.equal(fmtMoney('0.1', 'CNY'), '¥0.10');
  assert.equal(fmtMoney('123456789012345678.125', 'CNY'), '¥123,456,789,012,345,678.13');
  assert.equal(fmtNumber('1e3'), '1,000.00');
  assert.equal(fmtNumber(1e-7, { digits: 8 }), '0.00000010');
  assert.equal(fmtNumber(' +42 '), '42.00');
});

test('exact: a backend figure keeps every digit it has, padded but never rounded', () => {
  assert.equal(fmtPct('12.34', { exact: true }), '12.34%');
  assert.equal(fmtPct('12.345', { exact: true }), '12.345%');
  assert.equal(fmtPct('12.3', { exact: true }), '12.30%');
  assert.equal(fmtPct('12.34', { digits: 1, exact: true }), '12.34%');
  assert.equal(fmtPct('-0.004', { signed: true, exact: true }), '-0.004%');
  assert.equal(fmtMoney('1234.5678', 'CNY', { exact: true }), '¥1,234.5678');
  assert.equal(fmtMoney('-20', 'HKD', { signed: true, exact: true }), '-HK$20.00');
  assert.equal(fmtNumber('0.10', { exact: true }), '0.10');
  assert.equal(fmtPrice('12.345679', 'CNY', 'stock', { exact: true }), '12.345679');
  assert.equal(fmtPrice('61.2', 'CNY', 'stock', { exact: true }), '61.20');
  assert.equal(fmtPrice('382.4', 'HKD', 'stock', { symbol: true, exact: true }), 'HK$382.400');
  assert.equal(fmtPrice('189.987', 'USD', 'stock', { symbol: true, exact: true }), '$189.987');
  assert.equal(fmtPct(null, { exact: true }), '不可用');
  // Without it, display rounding is unchanged.
  assert.equal(fmtPct('12.345'), '12.35%');
});

test('compact 万 and 亿 for headline figures', () => {
  assert.equal(fmtCompact('123100'), '12.31万');
  assert.equal(fmtCompact('123100', 'CNY'), '¥12.31万');
  assert.equal(fmtCompact('120000000', 'CNY'), '¥1.20亿');
  assert.equal(fmtCompact('-13100', 'CNY', { signed: true }), '-¥1.31万');
  assert.equal(fmtCompact('13100', 'CNY', { signed: true }), '+¥1.31万');
  assert.equal(fmtCompact('9999.5', 'CNY'), '¥9,999.50');
  assert.equal(fmtCompact('9999.999', 'CNY'), '¥1.00万');
  assert.equal(fmtCompact('99999999', 'CNY'), '¥1.00亿');
  assert.equal(fmtCompact('1234567890123', 'HKD'), 'HK$12,345.68亿');
});

test('price digits follow the market: A-share 2, A-share ETF and fund 3, Hong Kong 3, US 2', () => {
  assert.equal(priceDigits('CNY'), 2);
  assert.equal(priceDigits('CNY', 'etf'), 3);
  assert.equal(priceDigits('CNY', 'fund'), 3);
  assert.equal(priceDigits('HKD'), 3);
  assert.equal(priceDigits('USD'), 2);
  assert.equal(fmtPrice('1680', 'CNY'), '1,680.00');
  assert.equal(fmtPrice('3.4567', 'CNY', 'etf'), '3.457');
  assert.equal(fmtPrice('382.4', 'HKD'), '382.400');
  assert.equal(fmtPrice('382.4', 'HKD', 'stock', { symbol: true }), 'HK$382.400');
  assert.equal(fmtPrice('189.987', 'USD'), '189.99');
});

test('quantities keep their digits without trailing zeros', () => {
  assert.equal(fmtQty('1000'), '1,000');
  assert.equal(fmtQty('1000.5000'), '1,000.5');
  assert.equal(fmtQty('0.00010000'), '0.0001');
  assert.equal(fmtQty('-300'), '-300');
  assert.equal(fmtQty('0.000'), '0');
  assert.equal(fmtQty(2500), '2,500');
});

test('long decimals: at most 8 places keep every digit; more are ≈ rounded half up to 8, the full value kept', () => {
  assert.equal(SHOWN_PLACES, 8);
  // Exactly 8 places: every digit, nothing else.
  assert.deepEqual(fmtLong('12.34567891'), { text: '12.34567891' });
  assert.deepEqual(fmtLong('0.00000001'), { text: '0.00000001' });
  // 9 places: rounded, marked, with the title and the accessible name.
  assert.deepEqual(fmtLong('12.345678915'), { text: '≈12.34567892', full: '12.345678915', label: '约 12.34567892，完整值 12.345678915' });
  assert.deepEqual(fmtLong('12.345678914').text, '≈12.34567891');
  // The importer's derived price (30 significant places) and a repeating average cost.
  const price = fmtLong('32.016666666666666666666666667', (v) => fmtPrice(v, 'CNY', 'stock', { exact: true }));
  assert.deepEqual(price, { text: '≈32.01666667', full: '32.016666666666666666666666667', label: '约 32.01666667，完整值 32.016666666666666666666666667' });
  assert.equal(fmtLong('0.333333333333333333333333333333').text, '≈0.33333333');
  // Negative: half away from zero, the sign kept; a value that rounds to zero has no sign.
  assert.equal(fmtLong('-1.234567895').text, '≈-1.2345679', 'the rounded value without trailing zeros');
  assert.equal(fmtLong('-1.234567894').text, '≈-1.23456789');
  assert.equal(fmtLong('-0.000000004').text, '≈0');
  assert.equal(fmtLong('-12.0000000049', (v) => fmtMoney(v, 'HKD', { signed: true, exact: true })).text, '≈-HK$12.00');
  // A cost basis worked out from a repeating average (1,200 × 51.4708333…): the formatter pads to the cent.
  assert.deepEqual(fmtLong('61764.9999999999999999999999996', (v) => fmtMoney(v, 'CNY', { exact: true })),
    { text: '≈¥61,765.00', full: '¥61,764.9999999999999999999999996', label: '约 ¥61,765.00，完整值 ¥61,764.9999999999999999999999996' });
  // Trailing zeros past the 8th place lose nothing: no ≈, no title (and the formatter pads as it likes); within
  // 8 places they stay as sent.
  assert.deepEqual(fmtLong('1.2300000000000'), { text: '1.23' });
  assert.deepEqual(fmtLong('1.2300000000000', (v) => fmtPrice(v, 'HKD', 'stock', { exact: true })), { text: '1.230' });
  assert.deepEqual(fmtLong('1.230'), { text: '1.230' });
  assert.deepEqual(fmtLong('1.2345678900001').text, '≈1.23456789');
  // Half up exactly at the boundary, with a carry through every digit and into the integer part.
  assert.equal(fmtLong('0.123456785').text, '≈0.12345679');
  assert.equal(fmtLong('0.1234567849999').text, '≈0.12345678');
  assert.equal(fmtLong('0.999999995').text, '≈1');
  assert.equal(fmtLong('0.999999995', (v) => fmtPrice(v, 'CNY', 'stock', { exact: true })).text, '≈1.00');
  assert.equal(fmtLong('0.999999994999').text, '≈0.99999999');
  assert.equal(fmtLong('99999.999999999').text, '≈100,000');
  assert.equal(fmtLong('99999.999999999', fmtQty).text, '≈100,000');
  // A huge integer part: string arithmetic, every integer digit kept and grouped.
  const huge = fmtLong('123456789012345678901234567890.1234567891');
  assert.equal(huge.text, '≈123,456,789,012,345,678,901,234,567,890.12345679');
  assert.equal(huge.full, '123,456,789,012,345,678,901,234,567,890.1234567891');
  assert.equal(fmtLong('123456789012345678901234567890').text, '123,456,789,012,345,678,901,234,567,890');
  // Through the other formatters; a percentage keeps its unit; a float from a service is read as written.
  assert.equal(fmtLong('12.3456789012', (v) => fmtPct(v, { signed: true, exact: true })).text, '≈+12.3456789%');
  assert.equal(fmtLong(0.1 + 0.2).text, '≈0.3');
  assert.equal(fmtLong(0.1 + 0.2).full, '0.30000000000000004');
  // Unknown stays 不可用 (whatever the formatter says for it), never 0, never ≈.
  for (const value of [null, undefined, '', 'abc']) assert.deepEqual(fmtLong(value, (v) => fmtPrice(v, 'CNY')), { text: '不可用' }, String(value));
});

test('a line with a long figure in it is shown rounded, its full form the title and accessible name', () => {
  const price = fmtLong('32.016666666666666666666666667');
  const line = longLine((pick) => `买入 300 股 @ ${pick(price)} · 费用 5.00`);
  assert.deepEqual(line, { text: '买入 300 股 @ ≈32.01666667 · 费用 5.00', full: '买入 300 股 @ 32.016666666666666666666666667 · 费用 5.00',
    label: '买入 300 股 @ 32.016666666666666666666666667 · 费用 5.00' });
  assert.deepEqual(longLine((pick) => `买入 100 股 @ ${pick(fmtLong('61.2'))}`), { text: '买入 100 股 @ 61.2' });
  assert.equal(longLineOrNull(() => null), null);
  assert.deepEqual(longLineOrNull((pick) => pick(price)), { text: '≈32.01666667', full: '32.016666666666666666666666667',
    label: '32.016666666666666666666666667' });
});

test('a ratio in percent units, exactly, so its places are counted as shown', () => {
  assert.equal(ratioPercent('0.0123'), '1.23');
  assert.equal(ratioPercent(0.123456789012), '12.3456789012');
  assert.equal(ratioPercent('-0.5'), '-50');
  assert.equal(ratioPercent(1e-9), '0.0000001');
  assert.equal(ratioPercent(null), null);
  assert.equal(fmtLong(ratioPercent(0.123456789012), (v) => fmtPct(v, { signed: true, exact: true })).text, '≈+12.3456789%');
});

test('toneOf: sign of raw or formatted values; zero and unknown are flat', () => {
  assert.equal(toneOf('12.5'), 'up');
  assert.equal(toneOf(-3), 'down');
  assert.equal(toneOf('-0.01'), 'down');
  assert.equal(toneOf('+¥1,234.50'), 'up');
  assert.equal(toneOf('-HK$20.00'), 'down');
  assert.equal(toneOf('−1.20%'), 'down');
  assert.equal(toneOf('12.31万'), 'up');
  for (const value of [0, -0, '0', '-0', '0.00', '¥0.00', '不可用', '', null, undefined, NaN]) {
    assert.equal(toneOf(value), 'flat', String(value));
  }
});

test('times and dates', () => {
  const iso = '2026-11-17T02:32:05Z';
  assert.equal(fmtTime(iso, { timeZone: 'Asia/Shanghai' }), '10:32:05');
  assert.equal(fmtTime(iso, { timeZone: 'Asia/Shanghai', seconds: false }), '10:32');
  assert.equal(fmtTime('2026-11-17T16:00:00Z', { timeZone: 'Asia/Shanghai' }), '00:00:00');
  assert.equal(fmtDate(iso, { timeZone: 'Asia/Shanghai' }), '2026-11-17');
  assert.equal(fmtDate('2026-11-17T20:00:00Z', { timeZone: 'Asia/Shanghai' }), '2026-11-18');
  assert.equal(fmtDate('2026-11-17'), '2026-11-17');
  assert.equal(fmtDateTime(iso, { timeZone: 'Asia/Shanghai' }), '2026-11-17 10:32');
});

test('relative time for chips', () => {
  const now = Date.parse('2026-11-17T02:32:05Z');
  const ago = (seconds) => new Date(now - seconds * 1000).toISOString();
  assert.equal(fmtRelative(ago(2), now), '刚刚');
  assert.equal(fmtRelative(ago(3.4), now), '刚刚');
  assert.equal(fmtRelative(ago(12), now), '12 秒前');
  assert.equal(fmtRelative(ago(5 * 60 + 10), now), '5 分钟前');
  assert.equal(fmtRelative(ago(2 * 3600), now), '2 小时前');
  assert.equal(fmtRelative(ago(3 * 86400), now), '3 天前');
  assert.equal(fmtRelative(ago(-90), now), '1 分钟后');
  assert.equal(fmtRelative(ago(30 * 86400), now, { timeZone: 'Asia/Shanghai' }), '2026-10-18');
  assert.equal(fmtRelative(new Date(now - 3000), new Date(now)), '刚刚');
});

// The one convention every number field follows (NumberInput, MoneyInput): ASCII or full-width digits,
// "." (or the full-width "．" / "。") as the decimal point, "," (or "，") and spaces only as thousands
// separators between groups of exactly three digits. Anything else is refused with a reason and the
// field keeps its previous value: a refused paste never turns into a different number.
const ACCEPTED = [
  // [typed or pasted, value, allowNegative]
  ['1234.5', '1234.5'],
  ['1,234.5', '1234.5'],
  ['1,234,567', '1234567'],
  ['1,680.00', '1680.00'],
  ['12,345.678', '12345.678'],
  ['１２３．５', '123.5'],
  ['１，０００', '1000'],
  ['１２３。５', '123.5'],
  ['1，234', '1234'],
  [' 1 000 ', '1000'],
  ['1\u00a0234.5', '1234.5'],
  ['\u3000１２\u3000', '12'],
  ['', ''],
  ['   ', ''],
  ['12.', '12.'],
  ['.5', '.5'],
  ['.', '.'],
  ['0', '0'],
  ['0.05', '0.05'],
  ['007', '007'],
  ['-5', '-5', true],
  ['－5', '-5', true],
  ['−1,234.5', '-1234.5', true],
  ['-', '-', true],
  ['-0.5', '-0.5', true],
];
const REFUSED = [
  // [typed or pasted, part of the reason, allowNegative]
  ['1,23', '千位分隔符'],
  ['1.234,56', '千位分隔符'],
  ['1,2345', '千位分隔符'],
  ['12,34,567', '千位分隔符'],
  ['0,123', '千位分隔符'],
  [',123', '千位分隔符'],
  ['1,', '千位分隔符'],
  ['1,0', '千位分隔符'],
  ['1,234.567,8', '千位分隔符'],
  ['1 2', '千位分隔符'],
  ['1,234 567', '千位分隔符'],
  ['1.2.3', '小数点'],
  ['1e5', '科学计数法'],
  ['1E-3', '科学计数法'],
  ['2.5e+3', '科学计数法'],
  ['¥100', '货币符号'],
  ['$1,234.50', '货币符号'],
  ['HK$5', '货币符号'],
  ['100元', '货币符号'],
  ['5%', '货币符号'],
  ['+3', '+'],
  ['-5', '负数'],
  ['－5', '负数'],
  ['--5', '负数', true],
  ['5-', '负数', true],
  ['abc', '数字'],
  ['1_000', '数字'],
  ["1'000", '数字'],
  ['0x1F', '数字'],
];

test('number fields: one declared convention, every accepted input and the value it gives', () => {
  for (const [raw, value, allowNegative = false] of ACCEPTED) {
    assert.deepEqual(parseNumberInput(raw, { allowNegative }), { ok: true, value }, JSON.stringify(raw));
    assert.equal(normalizeNumberInput(raw, { allowNegative }), value, JSON.stringify(raw));
  }
});

test('number fields: anything ambiguous is refused with a reason, never silently changed', () => {
  for (const [raw, reason, allowNegative = false] of REFUSED) {
    const result = parseNumberInput(raw, { allowNegative });
    assert.equal(result.ok, false, `${JSON.stringify(raw)} is refused, not read as ${result.value}`);
    assert.ok(result.error.includes(reason), `${JSON.stringify(raw)}: ${result.error}`);
    assert.ok(result.error.includes(raw.trim().slice(0, 12)), `the reason quotes the input: ${result.error}`);
    assert.equal(normalizeNumberInput(raw, { allowNegative }), null, JSON.stringify(raw));
  }
  // The reviewer's reproductions: a decimal comma never becomes a hundredfold amount or a thousandth.
  assert.equal(normalizeNumberInput('1,23'), null);
  assert.equal(normalizeNumberInput('1.234,56'), null);
  // A long paste is quoted shortened.
  assert.ok(parseNumberInput('1,23'.repeat(20)).error.length < 80);
});

test('stepping a number field is exact and never goes below the minimum', () => {
  assert.equal(stepNumberInput('100', 100, 1), '200');
  assert.equal(stepNumberInput('100', 100, -1, 0), '0');
  assert.equal(stepNumberInput('50', 100, -1, 0), '0');
  assert.equal(stepNumberInput('', 100, 1), '100');
  assert.equal(stepNumberInput('0.1', '0.2', 1), '0.3');
  assert.equal(stepNumberInput('1.005', '0.01', 1), '1.015');
  assert.equal(stepNumberInput('0.5', 1, -1), '-0.5');
  assert.equal(stepNumberInput('12345678901234567890', 1, 1), '12345678901234567891');
  // The minimum's own digits count: a step down from 0 stops at 0.05, not at 0 below the minimum.
  assert.equal(stepNumberInput('0', '1', -1, 0.05), '0.05');
  assert.equal(stepNumberInput('1', '1', -1, 0.05), '0.05');
  assert.equal(stepNumberInput('0.5', '0.1', -1, 0.45), '0.45');
  assert.equal(stepNumberInput('2', '1', -1, 0.05), '1');
  assert.equal(stepNumberInput('', 100, -1, 0.01), '0.01');
});
