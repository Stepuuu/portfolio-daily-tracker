// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { actionBadgeClass, actionColor, actionDirection, pnlBarClass, pnlDirection, pnlHex, pnlPanelClass, pnlTextClass } from '../src/lib/pnlColor.ts';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');

test('direction follows the sign; zero and missing values are flat', () => {
  assert.equal(pnlDirection(0.01), 'up');
  assert.equal(pnlDirection(-0.01), 'down');
  for (const value of [0, -0, NaN, Infinity, null, undefined]) {
    assert.equal(pnlDirection(value), 'flat', String(value));
  }
});

// The helpers return the `up` / `down` token classes; the next test checks those tokens are red and green.
test('red is up and green is down in every helper', () => {
  for (const helper of [pnlTextClass, pnlPanelClass, pnlBarClass]) {
    assert.match(helper(5), /-up\b/, helper.name);
    assert.doesNotMatch(helper(5), /down|red|green/, helper.name);
    assert.match(helper(-5), /-down\b/, helper.name);
    assert.doesNotMatch(helper(-5), /-up\b|red|green/, helper.name);
    assert.doesNotMatch(helper(0), /-up\b|down|red|green/, helper.name);
  }
  assert.match(pnlTextClass(5, 'light'), /text-up\b.*theme-light/);
  assert.match(pnlTextClass(-5, 'light'), /text-down\b.*theme-light/);
  assert.doesNotMatch(pnlTextClass(0, 'light'), /-up\b|down/);
  assert.notEqual(pnlHex(0), pnlHex(1));
  assert.notEqual(pnlHex(0), pnlHex(-1));
});

test('the up token is red and the down token green in both themes; pnlHex falls back to the dark tokens', () => {
  const css = readFileSync(join(SRC, 'styles', 'tokens.css'), 'utf8');
  const block = (theme) => css.slice(css.indexOf(`[data-theme='${theme}']`)).split('}')[0];
  const rgb = (theme, name) => block(theme).match(new RegExp(`--${name}:\\s*(\\d+) (\\d+) (\\d+);`)).slice(1).map(Number);
  const hex = (parts) => `#${parts.map((n) => n.toString(16).padStart(2, '0')).join('')}`;
  for (const theme of ['dark', 'light']) {
    const [ur, ug, ub] = rgb(theme, 'up');
    const [dr, dg, db] = rgb(theme, 'down');
    assert.ok(ur > ug && ur > ub, `${theme} --up is red`);
    assert.ok(dg > dr && dg > db, `${theme} --down is green`);
  }
  // Outside a browser there is no stylesheet to read.
  assert.equal(pnlHex(1), hex(rgb('dark', 'up')));
  assert.equal(pnlHex(-1), hex(rgb('dark', 'down')));
  assert.equal(pnlHex(0), hex(rgb('dark', 'text-3')));
});

test('a direction can be passed instead of a number', () => {
  assert.equal(pnlTextClass('up'), pnlTextClass(1));
  assert.equal(pnlTextClass('down', 'light'), pnlTextClass(-1, 'light'));
  assert.equal(pnlPanelClass('flat'), pnlPanelClass(0));
  assert.equal(pnlHex('down'), pnlHex(-3));
});

// Guard against the convention diverging again: a sign test that picks between a red and a
// green class by hand (in either order) must go through the helper instead.
test('no page chooses red or green from a sign comparison by hand', () => {
  const offenders = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(entry.name) && !path.endsWith(join('lib', 'pnlColor.ts'))) {
        readFileSync(path, 'utf8').split('\n').forEach((line, index) => {
          const comparesSign = /[<>]=?\s*-?[01](\.0)?\b/.test(line);
          const colours = line.match(/(?:text|bg|border)-(?:red|green|emerald|rose)-\d+|#ef4444|#22c55e|#10b981/g) || [];
          const hasRed = colours.some((c) => /red|rose|#ef4444/.test(c));
          const hasGreen = colours.some((c) => /green|emerald|#22c55e|#10b981/.test(c));
          if (comparesSign && hasRed && hasGreen) offenders.push(`${path}:${index + 1}: ${line.trim()}`);
        });
      }
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, []);
});

// Indirect mappings: a figure judged good or bad (a `good` flag, a threshold) and painted green or red
// reads as a gain or a loss here, where red is up and green is down. Three shapes, on one line:
//   flag ? green : red/orange/amber  (a good/bad switch, e.g. `m.good ? 'text-emerald-400' : 'text-orange-400'`)
//   good / bad / positive / profit … ? any red or green  (the flag names a judgement)
//   x < -5 ? red …  /  x > 0 ? green …  (a loss painted red, a gain painted green)
// Colours that mean something else (status chips, delete buttons, a categorical palette) never sit in
// such a choice; a request status (`msg.type === 'ok' ? green : red`) is a success/error message, not a
// gain or loss. Gain and loss colours come from pnlTextClass and friends, the `up` / `down` tokens.
const GREEN = /(?:text|bg|border|fill|stroke|ring|from|to|via)-(?:green|emerald|lime|teal)-\d+|#(?:22c55e|10b981|4ade80|34d399|16a34a|059669)\b/i;
const RED = /(?:text|bg|border|fill|stroke|ring|from|to|via)-(?:red|rose)-\d+|#(?:ef4444|f87171|dc2626|f43f5e)\b/i;
const WARM = /(?:text|bg|border|fill|stroke|ring|from|to|via)-(?:red|rose|orange|amber|yellow)-\d+|#(?:ef4444|f87171|dc2626|f43f5e|f97316|f59e0b|eab308)\b/i;
const STATUS = /\b(?:type|status|state|level)\s*[!=]==?\s*['"](?:ok|success|error|failed|done)['"]/;
const JUDGEMENT = /\b(?:good|bad|isGood|isBad|positive|negative|isPositive|isNegative|gain|loss|profit|isProfit|win|isWin|better|worse|healthy)\s*\?/;

function indirectOffences(line) {
  const reasons = [];
  const choice = /\?[^:]*:/.test(line);
  if (choice && GREEN.test(line) && WARM.test(line) && !STATUS.test(line)) reasons.push('green against red/orange/amber');
  if (JUDGEMENT.test(line) && (GREEN.test(line) || RED.test(line))) reasons.push('a judgement flag picks red or green');
  if (/[<>]=?\s*-\d/.test(line) && RED.test(line)) reasons.push('a negative value painted red');
  if (/>=?\s*0(?:\.0+)?\b/.test(line) && GREEN.test(line)) reasons.push('a positive value painted green');
  return reasons;
}

test('the indirect-mapping guard catches the shapes it is meant for', () => {
  for (const line of [
    "<div className={`text-sm ${m.good ? 'text-emerald-400' : 'text-orange-400'}`}>",
    "const tone = good ? 'text-green-400' : 'text-slate-400'",
    "typeof n === 'number' && n < -10 ? 'text-red-400' : neutral",
    "value >= 0 ? 'text-emerald-300' : 'text-slate-300'",
    "fill={isWin ? '#10b981' : '#64748b'}",
  ]) assert.ok(indirectOffences(line).length, line);
  for (const line of [
    "className={`rounded-lg border ${p.available ? 'border-emerald-500/25 text-emerald-300' : 'border-slate-700'}`}",
    "<AlertTriangle className=\"h-5 w-5 text-red-400\" />",
    "className={pnlTextClass(value)}",
    "const PIE_COLORS = ['#ef4444', '#f97316', '#22c55e']",
    "${msg.type === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'}",
  ]) assert.deepEqual(indirectOffences(line), [], line);
});

test('no page maps a good/bad judgement or a gain/loss threshold to red or green by hand', () => {
  const offenders = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(entry.name) && !path.endsWith(join('lib', 'pnlColor.ts'))) {
        readFileSync(path, 'utf8').split('\n').forEach((line, index) => {
          const reasons = indirectOffences(line);
          if (reasons.length) offenders.push(`${path.slice(SRC.length + 1)}:${index + 1}: ${reasons.join('; ')}: ${line.trim()}`);
        });
      }
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, []);
});

test('the old green-is-up CSS classes are gone', () => {
  const css = readFileSync(join(SRC, 'styles', 'index.css'), 'utf8');
  assert.doesNotMatch(css, /profit-positive|profit-negative/);
});

test('buy and add are red, sell and reduce are green, hold is neither', () => {
  // The helpers return the `up` / `down` token classes; the token test above checks that
  // `up` is red and `down` is green in both themes.
  for (const action of ['buy', 'add']) {
    assert.equal(actionDirection(action), 'up');
    assert.match(actionColor(action), /-up\b/);
    assert.match(actionBadgeClass(action), /-up\b/);
    assert.doesNotMatch(actionBadgeClass(action), /-down\b|red|green/);
  }
  for (const action of ['sell', 'reduce']) {
    assert.equal(actionDirection(action), 'down');
    assert.match(actionColor(action), /-down\b/);
    assert.match(actionBadgeClass(action), /-down\b/);
    assert.doesNotMatch(actionBadgeClass(action), /-up\b|red|green/);
  }
  for (const action of ['hold', 'other', '', null, undefined]) {
    assert.equal(actionDirection(action), 'flat');
    assert.doesNotMatch(actionColor(action), /-up\b|-down\b|red|green/);
    assert.doesNotMatch(actionBadgeClass(action), /-up\b|-down\b|red|green/);
  }
  // same colours as a price move in the same direction
  assert.equal(actionColor('buy'), pnlTextClass('up'));
  assert.equal(actionColor('sell', 'light'), pnlTextClass('down', 'light'));
});

// Buy/sell arrows and badges are actions, not risk levels, but they follow the same colours as
// gains and losses: a hand-picked red or green next to them must go through actionColor.
test('buy/sell arrows and badges do not hard-code red or green', () => {
  const offenders = [];
  const colour = /(?:text|bg|border|fill|stroke)-(?:red|green|emerald|rose)-\d+|#ef4444|#22c55e|#10b981/;
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.tsx$/.test(entry.name)) {
        const lines = readFileSync(path, 'utf8').split('\n');
        lines.forEach((line, index) => {
          // an arrow icon, or a line deciding by buy/sell/add/reduce
          const isArrow = /<(TrendingUp|TrendingDown|ArrowUp|ArrowDown)\b/.test(line);
          const isAction = /\b(isBuy|isSell)\b|(?:===|case)\s*['"](buy|sell|add|reduce)['"]/.test(line);
          // the line after a `case 'buy':` / `case 'sell':` chain belongs to it too
          const afterCase = lines.slice(Math.max(0, index - 3), index).some((l) => /case\s*['"](buy|sell|add|reduce)['"]/.test(l)) &&
            /^\s*return\b/.test(line);
          if ((isArrow || isAction || afterCase) && colour.test(line)) offenders.push(`${path}:${index + 1}: ${line.trim()}`);
        });
      }
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, []);
});

// A risk level, a confidence or a status is not a gain or a loss: green for "low risk", "high confidence",
// "completed" or "available" and red for "high risk" read as up and down here. These chips use the
// neutral and amber tokens (warn for what needs attention), never red, green or the up / down tokens.
const GAIN_LOSS = /(?:text|bg|border|ring|fill|stroke)-(?:red|rose|green|emerald|lime|teal)-\d+|\b(?:text|bg|border|ring)-(?:up|down)\b/;

/** The lines of the function or arrow function declared by `declaration`, up to its closing brace. */
function body(source, declaration) {
  const lines = source.split('\n');
  const start = lines.findIndex((line) => line.includes(declaration));
  assert.notEqual(start, -1, `${declaration} exists`);
  const indent = /^\s*/.exec(lines[start])[0];
  const end = lines.findIndex((line, index) => index > start && line === `${indent}}`);
  return lines.slice(start, end + 1);
}

test('risk, confidence and status chips are neutral or amber, never the gain and loss colours', () => {
  const read = (path) => readFileSync(join(SRC, path), 'utf8');
  const chips = {
    'LabUi Status': body(read('components/lab/LabUi.tsx'), 'export function Status'),
    'LabConnections provider chip': read('components/lab/LabConnections.tsx').split('\n').filter((line) => /available/.test(line)),
  };
  const offenders = [];
  for (const [name, lines] of Object.entries(chips)) {
    for (const line of lines) if (GAIN_LOSS.test(line)) offenders.push(`${name}: ${line.trim()}`);
  }
  assert.deepEqual(offenders, []);
});
