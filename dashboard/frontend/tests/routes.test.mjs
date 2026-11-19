// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The real route table (src/routes.tsx): every navigation path resolves to a page file, each mode shows
// the sections the plan gives it, and old paths lead somewhere that exists.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { register } from 'node:module';
import { dirname, join, relative } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  MODES, allowedIn, documentTitle, findPage, homePath, mobileSplit, normalizePath, resolveAppMode, resolveRedirect, visibleSections,
} from '../src/lib/navigation.ts';
import { SETTINGS_SECTIONS } from '../src/features/settings/sections.ts';

register('./tsxHooks.mjs', import.meta.url);
const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const { sections, redirects, hiddenRoutes, navPages } = await import('../src/routes.tsx');
const SOURCE = readFileSync(join(ROOT, 'src', 'routes.tsx'), 'utf8');

/** `path: '/x' ... import('./pages/X')` on one line: the page file behind each path. */
function pageFiles() {
  const files = new Map();
  for (const line of SOURCE.split('\n')) {
    const path = /path: '([^']+)'/.exec(line)?.[1];
    const module = /import\('(\.\/[^']+)'\)/.exec(line)?.[1];
    if (path && module) files.set(path, module);
  }
  return files;
}

const SECTION_IDS = ['today', 'market', 'ledger', 'review', 'research', 'settings'];

test('six sections in the plan order, each with at least one page', () => {
  assert.deepEqual(sections.map((section) => section.id), SECTION_IDS);
  assert.deepEqual(sections.map((section) => section.label), ['今日', '行情', '账本', '计划与复盘', '研究', '设置']);
  for (const section of sections) {
    assert.ok(section.pages.length > 0, section.id);
    assert.ok(section.icon, `${section.id} has an icon`);
    assert.ok(['primary', 'more'].includes(section.mobile), section.id);
  }
});

test('every navigation path resolves to its own page, backed by a file that exists', () => {
  const files = pageFiles();
  const paths = navPages.map((page) => page.path);
  assert.equal(new Set(paths).size, paths.length, 'paths are unique');
  for (const page of [...navPages, ...hiddenRoutes]) {
    assert.match(page.path, /^\//, page.path);
    assert.ok(page.label, page.path);
    assert.equal(page.element?.$$typeof?.toString(), 'Symbol(react.lazy)', `${page.path} is a lazy page`);
    const module = files.get(page.path);
    assert.ok(module, `${page.path} imports its page on the same line`);
    const file = join(ROOT, 'src', module.slice(2));
    assert.ok(existsSync(`${file}.tsx`) || existsSync(`${file}.ts`), `${page.path} → ${module} exists`);
  }
  for (const mode of MODES) {
    const visible = visibleSections(sections, mode);
    for (const section of visible) {
      for (const page of section.pages) {
        const match = findPage(visible, page.path);
        assert.equal(match?.page.path, page.path, `${mode}: ${page.path} resolves to itself`);
        assert.equal(match?.section.id, section.id);
      }
    }
  }
});

test('today is the home page; trailing slashes and sub-paths find their section', () => {
  assert.equal(findPage(sections, '/')?.section.id, 'today');
  assert.equal(findPage(sections, '/ledger/')?.page.path, '/ledger');
  assert.equal(findPage(sections, '/ledger/import')?.section.id, 'ledger');
  assert.equal(findPage(sections, '/backtest?x=1')?.section.id, 'research');
  assert.equal(findPage(sections, '/nowhere'), null);
  assert.equal(findPage(sections, '/ledgerx'), null);
  assert.equal(normalizePath('/market/'), '/market');
  assert.equal(documentTitle(findPage(sections, '/backtest')), '回测 · 研究 · 交易助手');
  assert.equal(documentTitle(findPage(sections, '/')), '今日 · 交易助手');
});

test('modes: lab shows only 研究 and 设置; full and demo show all six', () => {
  assert.deepEqual(visibleSections(sections, 'lab').map((section) => section.id), ['research', 'settings']);
  assert.deepEqual(visibleSections(sections, 'full').map((section) => section.id), SECTION_IDS);
  assert.deepEqual(visibleSections(sections, 'demo').map((section) => section.id), SECTION_IDS);
  assert.equal(homePath(sections, 'full'), '/');
  assert.equal(homePath(sections, 'demo'), '/');
  assert.equal(homePath(sections, 'lab'), '/lab');
  // The research smoke and `make lab` rely on these paths.
  const lab = visibleSections(sections, 'lab').flatMap((section) => section.pages.map((page) => page.path));
  for (const path of ['/lab', '/backtest', '/settings']) assert.ok(lab.includes(path), path);
  assert.ok(!allowedIn(findPage(sections, '/').page, 'lab'));
});

test('phones: 今日 and 行情 in the bottom bar, the rest under 更多; lab mode puts both of its in the bar', () => {
  const full = mobileSplit(visibleSections(sections, 'full'));
  assert.deepEqual(full.primary.map((section) => section.id), ['today', 'market']);
  assert.deepEqual(full.more.map((section) => section.id), ['ledger', 'review', 'research', 'settings']);
  const lab = mobileSplit(visibleSections(sections, 'lab'));
  assert.deepEqual(lab.primary.map((section) => section.id), ['research', 'settings']);
  assert.deepEqual(lab.more, []);
});

/** A path the router shows: a navigation page, or a section of 设置 (`/settings/:section`). */
function reachable(path) {
  const pagePaths = new Set(navPages.map((page) => page.path));
  if (pagePaths.has(path)) return true;
  const section = /^\/settings\/([a-z]+)$/.exec(path)?.[1];
  return Boolean(section && SETTINGS_SECTIONS.some((item) => item.id === section)
    && hiddenRoutes.some((route) => route.path === '/settings/:section'));
}

test('redirects: every removed v3.2 path leads to its new home, with its query and hash; none chains', () => {
  const expected = {
    '/portfolio': '/ledger',
    '/dashboard': '/',
    '/memory': '/settings/preferences',
    '/help': '/settings/about',
    '/settings/cash': '/ledger',
    '/settings/help': '/settings/about',
    '/settings/memory': '/settings/preferences',
  };
  for (const [from, to] of Object.entries(expected)) {
    assert.equal(resolveRedirect(redirects, from), to, from);
    assert.equal(resolveRedirect(redirects, `${from}/`), to, `${from}/`);
    assert.equal(resolveRedirect(redirects, `${from}?a=1#b`), `${to}?a=1#b`, `${from} keeps its query and hash`);
  }
  assert.deepEqual(redirects.map((redirect) => redirect.from).sort(), Object.keys(expected).sort());
  // The query and the hash travel with it: an old link to a proposal still opens that proposal.
  assert.equal(resolveRedirect(redirects, '/portfolio?proposal=abc'), '/ledger?proposal=abc');
  assert.equal(resolveRedirect(redirects, '/portfolio/?proposal=abc#events'), '/ledger?proposal=abc#events');
  assert.equal(resolveRedirect(redirects, '/portfolio#events'), '/ledger#events');
  assert.equal(resolveRedirect(redirects, '/portfolio', '?proposal=a%20b', '#x'), '/ledger?proposal=a%20b#x');
  assert.equal(resolveRedirect(redirects, '/memory', '', '#lessons'), '/settings/preferences#lessons');
  const pagePaths = new Set(navPages.map((page) => page.path));
  for (const { from, to } of redirects) {
    assert.ok(reachable(to), `${from} → ${to}: the target is a page`);
    assert.ok(!pagePaths.has(from), `${from} is not also a page`);
    assert.equal(resolveRedirect(redirects, to), null, `${to} does not redirect again`);
  }
  // 组合走势 keeps its old path until it moves to /history (UI-4).
  assert.ok(pagePaths.has('/tracker'));
  // The removed pages are gone from the table.
  for (const removed of ['/memory', '/help', '/dashboard', '/portfolio']) {
    assert.ok(!pagePaths.has(removed), removed);
    assert.ok(!SOURCE.includes(`path: '${removed}'`), `${removed} has no page line`);
  }
  assert.ok(!/pages\/(Memory|Help|Dashboard|Portfolio|Settings)'/.test(SOURCE), 'no route imports a removed page');
});

test('设置: /settings is the navigation entry, its sections are one hidden route', () => {
  assert.equal(findPage(sections, '/settings/preferences')?.page.path, '/settings');
  assert.equal(findPage(sections, '/settings/about')?.section.id, 'settings');
  assert.equal(documentTitle(findPage(sections, '/settings/backup')), '设置 · 交易助手');
  const route = hiddenRoutes.find((item) => item.path === '/settings/:section');
  assert.ok(route, 'the section route exists');
  assert.ok(!route.bare, 'sections render inside the shell');
  assert.ok(!route.modes, 'every mode reaches its own sections');
});

test('hidden routes stay out of the navigation; the gallery renders without the shell', () => {
  const pagePaths = new Set(navPages.map((page) => page.path));
  for (const route of hiddenRoutes) assert.ok(!pagePaths.has(route.path), route.path);
  const gallery = hiddenRoutes.find((route) => route.path.startsWith('/__ui'));
  assert.ok(gallery, 'the UI gallery is a hidden route in development');
  assert.equal(gallery.bare, true);
});

test('app mode: an unreadable status is an explicit unknown (read-only, neutral), full only for a confirmed missing endpoint', () => {
  assert.deepEqual(resolveAppMode({ mode: 'full', failed: false }), { mode: 'full', unknown: false, readOnly: false, readOnlyReason: undefined });
  const demo = resolveAppMode({ mode: 'demo', failed: false });
  assert.deepEqual([demo.mode, demo.unknown, demo.readOnly, demo.readOnlyReason], ['demo', false, true, '演示模式只读']);
  assert.equal(resolveAppMode({ mode: 'lab', failed: false }).readOnly, false);
  // An older backend of this app, confirmed to have no status endpoint: that, and only that, means full.
  assert.deepEqual(resolveAppMode({ failed: true, status: 404, absent: true }), { mode: 'full', unknown: false, readOnly: false, readOnlyReason: undefined });
  // Timeouts, 503s, network errors, and a 404 nobody confirmed (a proxy, a dev server, a static host):
  // the mode is unknown. A demo server must not lose its read-only state.
  for (const status of [404, 503, 500, 502, 429, 200, undefined]) {
    const unknown = resolveAppMode({ failed: true, status });
    assert.equal(unknown.unknown, true, String(status));
    assert.equal(unknown.readOnly, true, String(status));
    assert.match(unknown.readOnlyReason, /运行模式/);
    assert.equal(unknown.mode, 'full', 'navigation shows every page, read-only');
  }
  // Before the first answer nothing is writable either.
  const pending = resolveAppMode({ failed: false });
  assert.deepEqual([pending.mode, pending.unknown, pending.readOnly], [undefined, false, true]);
});

// Write controls take useAppMode().readOnlyReason, which covers the demo, an unknown mode and the moment
// before the backend answers. A control disabled with `demo ? DEMO_REASON : undefined` (or DEMO_REASON
// alone) stays writable while the mode is unknown, so DEMO_REASON stays inside the app mode's own code.
test('write controls are disabled with readOnlyReason: no page or component uses DEMO_REASON', () => {
  const allowed = new Map([
    ['lib/navigation.ts', 'defines it and turns the demo into readOnlyReason'],
    ['components/shell/useAppMode.ts', 're-exports it with the hook'],
    ['components/market/hooks.ts', "errorText: the demo backend's 403 said in the shell's words, not a disabled control"],
  ]);
  const src = join(ROOT, 'src');
  const offenders = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      const name = relative(src, path).split('\\').join('/');
      if (entry.isDirectory()) walk(path);
      else if (/\.tsx?$/.test(entry.name) && !allowed.has(name)) {
        readFileSync(path, 'utf8').split('\n').forEach((line, index) => {
          if (/\bDEMO_REASON\b/.test(line)) offenders.push(`${name}:${index + 1}: ${line.trim()}`);
        });
      }
    }
  };
  walk(src);
  assert.deepEqual(offenders, []);
});

test('app mode: a status answer is trusted only with a mode the shell knows; a 404 is a missing route only in FastAPI\'s words', async () => {
  const { isMissingRoute, statusMode } = await import('../src/lib/navigation.ts');
  for (const mode of MODES) assert.equal(statusMode({ mode, ledger: { active: true, revision: 3 }, version: '1' }), mode);
  // `{}`, an unknown or malformed mode, or not an object at all: none of them is a mode.
  for (const body of [{}, { mode: 'superuser' }, { mode: '' }, { mode: ['full'] }, { mode: 'FULL' }, null, undefined, 'full', [], 7]) {
    assert.equal(statusMode(body), null, JSON.stringify(body));
  }
  // A route this backend does not have answers FastAPI's own 404; a proxy's or a static host's does not.
  assert.equal(isMissingRoute(404, { detail: 'Not Found' }), true);
  for (const [status, body] of [[404, '<html>Not Found</html>'], [404, ''], [404, { detail: '通知不存在。' }], [404, null], [503, { detail: 'Not Found' }], [undefined, undefined]]) {
    assert.equal(isMissingRoute(status, body), false, `${status} ${JSON.stringify(body)}`);
  }
});

// Controls that write, or send the person's text or images to the model, take readOnlyReason: the demo
// refuses them, and while the mode is unknown (it may be a demo) nothing is sent or saved. Each handler
// returns at once when it is set, and each control shows the reason.
const GATED = {
  'components/ChatPanel.tsx': {
    handlers: ['const handleSubmit', 'const handleNewConversation'],
    controls: ['type="submit"', 'onClick={handleNewConversation}', 'fileInputRef.current?.click()'],
  },
  'components/ConversationHistory.tsx': { handlers: ['const handleResumeConversation'], controls: ['onClick={handleResumeConversation}'] },
  'pages/settings/ModelSection.tsx': { handlers: ['async function save'], controls: ['type="submit" variant="primary"'] },
  'components/lab/LabConnections.tsx': { handlers: ['async function saveDraft'], controls: ['保存连接'] },
  'pages/settings/PreferencesSection.tsx': {
    handlers: ['async function save', 'async function submit'],
    controls: ['type="submit" variant="primary"', 'icon={Plus}', 'label={`删除「${trigger}」`}', 'label={`删除教训', '>确认</Button>', '>放弃</Button>', '>删除</Button>', '清空助手记忆</Button>'],
  },
  'pages/settings/AutomationSection.tsx': { handlers: [], controls: ['icon={Play}'] },
  'components/today/HoldingsTable.tsx': { handlers: [], controls: ['问助手'] },
};

test('every control that writes or sends to the model is disabled with readOnlyReason', () => {
  const offenders = [];
  for (const [file, { handlers, controls }] of Object.entries(GATED)) {
    const lines = readFileSync(join(ROOT, 'src', file), 'utf8').split('\n');
    if (!lines.some((line) => /useAppMode\(\)/.test(line))) offenders.push(`${file}: does not read useAppMode()`);
    for (const handler of handlers) {
      const at = lines.findIndex((line) => line.includes(handler));
      if (at === -1) offenders.push(`${file}: ${handler} not found`);
      else if (!lines.slice(at, at + 4).some((line) => /if \(readOnlyReason\) return/.test(line))) {
        offenders.push(`${file}: ${handler} does not return at once when readOnlyReason is set`);
      }
    }
    for (const control of controls) {
      const at = lines.findIndex((line) => line.includes(control));
      if (at === -1) offenders.push(`${file}: ${control} not found`);
      else if (!lines.slice(Math.max(0, at - 10), at + 10).some((line) => /readOnlyReason/.test(line))) {
        offenders.push(`${file}: the control at "${control}" is not disabled with readOnlyReason`);
      }
    }
  }
  assert.deepEqual(offenders, []);
});
