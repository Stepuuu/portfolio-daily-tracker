// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { NO_VALUE, fmt, fmtPct, fmtPlainPct, fmtSignedWan, fmtWan } from '../src/lib/trackerFormat.ts';

const MISSING = [null, undefined, NaN, Infinity, -Infinity];

test('a missing value is a dash in every formatter, never a zero', () => {
  assert.equal(NO_VALUE, '—');
  for (const value of MISSING) {
    for (const format of [fmt, fmtWan, fmtPct, fmtPlainPct, fmtSignedWan]) {
      const text = format(value);
      assert.equal(text, '—', `${format.name}(${value})`);
      assert.doesNotMatch(text, /0/);
    }
  }
});

test('a real zero is still shown as zero', () => {
  assert.equal(fmt(0), '0.00');
  assert.equal(fmtPct(0), '+0.00%');
  assert.equal(fmtWan(0), '0.00万');
  assert.equal(fmtPlainPct(0), '0.00%');
});

test('values keep their sign and rounding', () => {
  assert.equal(fmtPct(12.345), '+12.35%');
  assert.equal(fmtPct(-3.2), '-3.20%');
  assert.equal(fmtWan(123456), '12.35万');
  assert.equal(fmtSignedWan(25000), '+2.50万');
  assert.equal(fmtSignedWan(-25000), '-2.50万');
  assert.equal(fmtPlainPct(-7.5, 1), '-7.5%');
  assert.equal(fmt(1234.5, 1), '1,234.5');
});

test('the tracker page has no formatter of its own', () => {
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'pages', 'PortfolioTracker.tsx'), 'utf8');
  assert.doesNotMatch(source, /const fmt(Pct|Wan)?\s*=/);
  assert.match(source, /@\/lib\/trackerFormat/);
});
