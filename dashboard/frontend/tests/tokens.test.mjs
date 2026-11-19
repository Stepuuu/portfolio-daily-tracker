// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// WCAG contrast of the theme tokens in src/styles/tokens.css. Change token values until this passes.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { TOKEN_NAMES } from '../src/lib/theme.ts';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const css = readFileSync(join(SRC, 'styles', 'tokens.css'), 'utf8');

function block(selector) {
  const start = css.indexOf(selector);
  assert.notEqual(start, -1, `tokens.css has a ${selector} block`);
  const open = css.indexOf('{', start);
  return { head: css.slice(start, open), body: css.slice(open + 1, css.indexOf('}', open)) };
}

function parse(body) {
  const tokens = {};
  for (const match of body.matchAll(/--([\w-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;(?:\s*\/\*\s*(#[0-9a-f]{6}))?/gi)) {
    tokens[match[1]] = { rgb: [match[2], match[3], match[4]].map(Number), hex: match[5]?.toLowerCase() };
  }
  return tokens;
}

const THEMES = {
  dark: parse(block("[data-theme='dark']").body),
  light: parse(block("[data-theme='light']").body),
};

const channel = (value) => {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/** `top` painted at `alpha` over `bottom`, as the browser composites `bg-up/10`. */
const blend = (top, bottom, alpha) => top.map((c, i) => Math.round(c * alpha + bottom[i] * (1 - alpha)));

const TEXT_ROLES = ['text', 'text-2', 'text-3', 'up', 'down', 'warn', 'accent'];
const SURFACES = ['bg', 'surface', 'surface-2'];
// surface-3 is the selected segment of a SegmentedControl; its label is any ReactNode, so every role.
const ALL_SURFACES = [...SURFACES, 'surface-3'];
const AA = 4.5;
const NON_TEXT = 3;

test('dark is the fallback without a data-theme and both themes define every token', () => {
  // The page follows the system by default (index.html sets data-theme before the first paint); :root is dark only
  // for a document without that bootstrap.
  assert.match(css, /:root,\s*\[data-theme='dark'\]\s*\{/, 'the dark block also applies to :root');
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const name of TOKEN_NAMES) assert.ok(tokens[name], `${theme} defines --${name}`);
  }
  assert.match(block("[data-theme='light']").head, /\.theme-light/);
});

test('the hex comments match the values', () => {
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const [name, { rgb, hex }] of Object.entries(tokens)) {
      if (!hex) continue;
      assert.equal(`#${rgb.map((n) => n.toString(16).padStart(2, '0')).join('')}`, hex, `${theme} --${name}`);
    }
  }
});

test('every text role reaches 4.5:1 on bg, surface, surface-2 and surface-3 in both themes', () => {
  const failures = [];
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const role of TEXT_ROLES) {
      for (const surface of ALL_SURFACES) {
        const ratio = contrast(tokens[role].rgb, tokens[surface].rgb);
        if (ratio < AA) failures.push(`${theme} ${role} on ${surface}: ${ratio.toFixed(2)}`);
      }
    }
    // The tooltip bubble is inverted: bg-coloured text on a text-coloured fill.
    const tip = contrast(tokens.bg.rgb, tokens.text.rgb);
    if (tip < AA) failures.push(`${theme} tooltip: ${tip.toFixed(2)}`);
  }
  assert.deepEqual(failures, []);
});

// Every translucent fill the token UI draws, with the text roles that sit on it. Each is composited over
// bg, surface, surface-2 (a hovered or header row) and surface-3 (a selected segment, whose label may be a
// Badge or a count), as the browser does. The scan below fails when a migrated file uses a fill that is
// not listed, so a new one gets its contrast computed before it ships.
const FILLS = {
  'fg/.05': ['text', 'text-2', 'text-3', 'accent'],         // InlineAlert (info), PendingProposals and its link
  'fg/.06': ['text', 'text-2'],                             // hover: ghost button, nav item, bell, toast close
  'fg/.08': ['text-2'],                                     // neutral Badge, Tabs count, skeleton
  'fg/10': ['text', 'text-2'],                              // ghost button pressed
  'fg/.12': ['text'],                                       // AlertChip hover
  'accent/10': ['accent'],                                  // active nav, accent Badge, PlanChip, toast action
  'accent/15': ['accent'],                                  // PlanChip hover
  'accent/90': ['accent-fg'],                               // CSV import file picker (a label styled as a primary button) hover
  'warn/10': ['warn', 'text', 'text-2', 'text-3'],          // InlineAlert (warn), ConnectionBanner, warn Badge
  'warn/15': ['warn'],                                      // warn-outline pressed, PlanChip (warn) hover
  'up/10': ['up'],                                          // up Badge, P&L panel, buy badge
  'down/10': ['down'],
  'surface-2/50': TEXT_ROLES,                               // DataTable group header over surface
};
// Fills with no text on them: marks that need 3:1 against what is around them.
const MARKS = { 'up/70': 'up', 'down/70': 'down' };
const ROLE_TOKEN = { fg: 'text', 'fg-2': 'text-2', 'fg-3': 'text-3', accent: 'accent', warn: 'warn', up: 'up', down: 'down', 'surface-2': 'surface-2' };
const alphaOf = (text) => Number(text.startsWith('.') ? `0${text}` : text) / (text.startsWith('.') ? 1 : 100);

test('text on every translucent fill reaches 4.5:1, composited over bg, surface, surface-2 and surface-3', () => {
  const failures = [];
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const [fill, roles] of Object.entries(FILLS)) {
      const [role, alpha] = fill.split('/');
      for (const surface of ALL_SURFACES) {
        const under = tokens[surface].rgb;
        const composite = blend(tokens[ROLE_TOKEN[role]].rgb, under, alphaOf(alpha));
        for (const text of roles) {
          const ratio = contrast(tokens[text].rgb, composite);
          if (ratio < AA) failures.push(`${theme} ${text} on ${fill} over ${surface}: ${ratio.toFixed(2)}`);
        }
      }
    }
    for (const [fill, role] of Object.entries(MARKS)) {
      for (const surface of SURFACES) {
        const mark = blend(tokens[role].rgb, tokens[surface].rgb, alphaOf(fill.split('/')[1]));
        const ratio = contrast(mark, tokens[surface].rgb);
        if (ratio < NON_TEXT) failures.push(`${theme} ${fill} bar on ${surface}: ${ratio.toFixed(2)}`);
      }
    }
  }
  assert.deepEqual(failures, []);
});

const MIGRATED = JSON.parse(readFileSync(join(SRC, '..', 'tests', 'migrated.json'), 'utf8')).paths;
function migratedFiles() {
  const files = [];
  const walk = (path) => {
    const full = join(SRC, '..', path);
    if (statSync(full).isDirectory()) for (const name of readdirSync(full)) walk(join(path, name));
    else if (/\.tsx?$/.test(path)) files.push(full);
  };
  for (const path of MIGRATED) walk(path);
  return [...files, join(SRC, 'lib', 'pnlColor.ts')];
}

test('every translucent fill in the token UI is in the table above', () => {
  const unknown = new Set();
  for (const file of migratedFiles()) {
    for (const [, role, alpha] of readFileSync(file, 'utf8').matchAll(/\bbg-(fg(?:-[23])?|accent|warn|up|down|surface-2)\/(\[\.\d+\]|\d+)/g)) {
      const fill = `${role}/${alpha.replace(/[[\]]/g, '')}`;
      if (!(fill in FILLS) && !(fill in MARKS)) unknown.add(`${fill} in ${file.slice(SRC.length + 1)}`);
    }
  }
  assert.deepEqual([...unknown], []);
});

// The primary button: its label (accent-fg) on the accent fill at rest, on hover and while pressed, the
// states composited exactly as Button.tsx paints them, on every surface a button can sit on.
const BUTTON = readFileSync(join(SRC, 'components', 'ui', 'Button.tsx'), 'utf8');
function primaryStates(tokens, under) {
  const variant = /primary:\s*'([^']+)'/.exec(BUTTON)[1];
  const states = { rest: tokens.accent.rgb };
  for (const state of ['hover', 'active']) {
    const fade = new RegExp(`${state}:bg-accent\\/(\\d+)`).exec(variant);
    const tint = new RegExp(`${state}:bg-\\[linear-gradient\\(rgb\\(var\\(--([\\w-]+)\\)\\/([\\d.]+)\\)`).exec(variant);
    if (fade) states[state] = blend(tokens.accent.rgb, under, Number(fade[1]) / 100);
    else if (tint) states[state] = blend(tokens[tint[1]].rgb, tokens.accent.rgb, Number(tint[2]));
    else states[state] = tokens.accent.rgb;
  }
  return states;
}

test('the primary button label reaches 4.5:1 at rest, hovered and pressed, on every surface', () => {
  const failures = [];
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const surface of ALL_SURFACES) {
      for (const [state, fill] of Object.entries(primaryStates(tokens, tokens[surface].rgb))) {
        const ratio = contrast(tokens['accent-fg'].rgb, fill);
        if (ratio < AA) failures.push(`${theme} primary ${state} on ${surface}: ${ratio.toFixed(2)}`);
      }
    }
  }
  assert.deepEqual(failures, []);
});

test('the focus ring (accent) is visible against every surface: 3:1', () => {
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const surface of ALL_SURFACES) {
      assert.ok(contrast(tokens.accent.rgb, tokens[surface].rgb) >= NON_TEXT, `${theme} accent on ${surface}`);
    }
  }
});

test('control outlines (border-input) reach 3:1 against every surface', () => {
  const failures = [];
  for (const [theme, tokens] of Object.entries(THEMES)) {
    for (const surface of SURFACES) {
      const ratio = contrast(tokens['border-input'].rgb, tokens[surface].rgb);
      if (ratio < 3) failures.push(`${theme} border-input on ${surface}: ${ratio.toFixed(2)}`);
    }
  }
  assert.deepEqual(failures, []);
});

test('warn (amber) is a clearly different hue from up (red), so an error never reads as a gain', () => {
  const hue = ([r, g, b]) => {
    const [x, y, z] = [r, g, b].map((c) => c / 255);
    const max = Math.max(x, y, z);
    const min = Math.min(x, y, z);
    if (max === min) return 0;
    const d = max - min;
    const h = max === x ? ((y - z) / d) % 6 : max === y ? (z - x) / d + 2 : (x - y) / d + 4;
    return (h * 60 + 360) % 360;
  };
  for (const [theme, tokens] of Object.entries(THEMES)) {
    assert.ok(hue(tokens.warn.rgb) - hue(tokens.up.rgb) >= 20, `${theme}: warn hue ${hue(tokens.warn.rgb).toFixed(0)} vs up ${hue(tokens.up.rgb).toFixed(0)}`);
  }
});
