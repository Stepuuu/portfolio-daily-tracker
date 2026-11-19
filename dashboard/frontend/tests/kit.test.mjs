// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// Files listed in tests/migrated.json use only the design tokens and the kit: no legacy palette, no
// decoration. Add a file (or a directory ending in "/") to the list when its page moves to the kit.
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { DEFAULT_THEME_PREFERENCE, THEME_STORAGE_KEY, resolveTheme } from '../src/lib/theme.ts';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { paths } = JSON.parse(readFileSync(join(ROOT, 'tests', 'migrated.json'), 'utf8'));

function expand(path) {
  const full = join(ROOT, path);
  if (!statSync(full).isDirectory()) return [path];
  return readdirSync(full).flatMap((name) => expand(join(path, name)));
}

const FILES = paths.flatMap((path) => {
  assert.ok(existsSync(join(ROOT, path)), `migrated.json lists a missing path: ${path}`);
  return expand(path).filter((file) => /\.(tsx?|css)$/.test(file));
});

// The plan's list: the legacy slate/gray palette, the old radii, gradients, monospace numbers,
// attention-seeking animation and emoji.
const BANNED = [
  [/\bslate-/, 'slate-* (use the bg / surface / line / fg tokens)'],
  [/\bgray-/, 'gray-* (use the tokens)'],
  [/\brounded-(?:xl|2xl)\b/, 'rounded-xl / rounded-2xl (controls rounded-md, panels rounded-lg)'],
  [/\bbg-gradient/, 'gradients'],
  [/\bfont-mono\b/, 'font-mono (numbers use tabular-nums)'],
  [/\banimate-(?:pulse|ping)\b/, 'animate-pulse / animate-ping'],
  [/[\p{Extended_Pictographic}\u{FE0F}]/u, 'emoji'],
];

// Beyond the list: colour and size come only from the tokens.
const PALETTE = /\b(?:bg|text|border|ring|fill|stroke|divide|outline|from|via|to|placeholder|decoration|shadow)-(?:zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|primary)-\d{2,3}\b|\b(?:bg|text|border)-(?:white|black)\b/;
const LEGACY_SIZE = /\btext-(?:xs|sm|base|lg|[2-9]?xl)\b|\btext-\[\d+px\]/;
const LEGACY_SHADOW = /\bshadow-(?:sm|md|lg|xl|2xl|inner)\b/;

test('migrated.json lists the kit and the gallery', () => {
  assert.ok(FILES.some((file) => file.includes(join('components', 'ui'))));
  assert.ok(FILES.some((file) => file.endsWith('UiGallery.tsx')));
});

test('migrated files contain no legacy palette, old radii, gradients, font-mono, pulse/ping or emoji', () => {
  const offenders = [];
  for (const file of FILES) {
    readFileSync(join(ROOT, file), 'utf8').split('\n').forEach((line, index) => {
      for (const [pattern, why] of BANNED) if (pattern.test(line)) offenders.push(`${file}:${index + 1}: ${why}: ${line.trim()}`);
    });
  }
  assert.deepEqual(offenders, []);
});

test('migrated files take colours, font sizes and shadows from the tokens only', () => {
  const offenders = [];
  for (const file of FILES) {
    readFileSync(join(ROOT, file), 'utf8').split('\n').forEach((line, index) => {
      if (PALETTE.test(line)) offenders.push(`${file}:${index + 1}: palette colour: ${line.trim()}`);
      if (LEGACY_SIZE.test(line)) offenders.push(`${file}:${index + 1}: use text-caption/table/body/title/page/figure: ${line.trim()}`);
      if (LEGACY_SHADOW.test(line)) offenders.push(`${file}:${index + 1}: only overlays get a shadow (shadow-overlay): ${line.trim()}`);
    });
  }
  assert.deepEqual(offenders, []);
});

test('every kit module is exported from components/ui/index.ts', () => {
  const dir = join(ROOT, 'src', 'components', 'ui');
  const index = readFileSync(join(dir, 'index.ts'), 'utf8');
  for (const name of readdirSync(dir)) {
    if (name === 'index.ts') continue;
    assert.match(index, new RegExp(`from '\\./${name.replace(/\.tsx?$/, '')}'`), `${name} is exported`);
  }
});

test('the theme bootstrap in index.html matches lib/theme.ts: follow the system unless a choice is saved', () => {
  assert.equal(DEFAULT_THEME_PREFERENCE, 'system', 'every page uses the tokens, so the default is 跟随系统');
  const html = readFileSync(join(ROOT, 'index.html'), 'utf8');
  const head = html.slice(0, html.indexOf('</head>'));
  assert.ok(head.includes(`'${THEME_STORAGE_KEY}'`), 'bootstrap reads the same storage key');
  assert.match(head, new RegExp(`var preference = '${DEFAULT_THEME_PREFERENCE}'`), 'bootstrap has the same default');
  assert.match(head, /stored === 'light' \|\| stored === 'dark' \|\| stored === 'system'/, 'an explicit earlier choice is kept');
  assert.match(head, /matchMedia\('\(prefers-color-scheme: light\)'\)/, 'and resolved like resolveTheme');
  assert.match(head, /setAttribute\('data-theme'/);
  assert.ok(head.includes('<script>\n      // Theme bootstrap'), 'a plain blocking script in <head>: the theme is set before the first paint');
  assert.match(html, /<html[^>]*data-theme="dark"/, 'dark without JavaScript too (as resolveTheme without a media query)');
  // The bootstrap's resolution, run here as the browser would, against lib/theme.ts.
  const script = head.slice(head.indexOf('<script>') + 8, head.indexOf('</script>'));
  for (const [stored, prefersLight, expected] of [[null, true, 'light'], [null, false, 'dark'], ['system', true, 'light'], ['dark', true, 'dark'],
    ['light', false, 'light'], ['junk', true, 'light']]) {
    let theme = null;
    const env = {
      localStorage: { getItem: () => stored },
      matchMedia: () => ({ matches: prefersLight }),
      document: { documentElement: { setAttribute: (_name, value) => { theme = value; } } },
    };
    new Function('localStorage', 'matchMedia', 'document', script)(env.localStorage, env.matchMedia, env.document);
    assert.equal(theme, expected, `${stored} with a ${prefersLight ? 'light' : 'dark'} system`);
    const preference = ['light', 'dark', 'system'].includes(stored) ? stored : DEFAULT_THEME_PREFERENCE;
    assert.equal(resolveTheme(preference, prefersLight), expected);
  }
});
