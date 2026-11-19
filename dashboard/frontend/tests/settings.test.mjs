// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// 设置 without React: its sections and where a URL leads in each mode, what a 交易偏好 save sends, the routine
// rows of 自动化与通知, the bar cache and calendar lines of 数据与行情, the backup statement, and that the v3.2
// client code is gone for good.
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { SETTINGS_SECTIONS, resolveSection, sectionsFor, settingsPath } from '../src/features/settings/sections.ts';
import {
  EXPERIENCE_CHOICES, RISK_CHOICES, SECTORS, STYLE_CHOICES, choiceLabel, itemProblem, lessonText, profileChanges, profileForm,
  sectorChoices, toggleSector,
} from '../src/features/settings/memory.ts';
import { UNAVAILABLE, routineRows } from '../src/features/settings/routines.ts';
import { cacheFacts, calendarLine } from '../src/features/settings/data.ts';
import { BACKUP_CONTENTS, NOT_IN_BACKUPS } from '../src/features/settings/backup.ts';
import { ABOUT_TIPS, ASSISTANT_KEYS, DOC_LINKS, MODE_LABEL } from '../src/features/settings/about.ts';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');

// -- sections ------------------------------------------------------------------------------------

test('seven sections in the plan order, each with a short description', () => {
  assert.deepEqual(SETTINGS_SECTIONS.map((section) => section.label),
    ['账户与费率', '模型', '数据与行情', '自动化与通知', '交易偏好', '备份与导出', '关于与帮助']);
  assert.deepEqual(SETTINGS_SECTIONS.map((section) => section.id), ['accounts', 'models', 'data', 'automation', 'preferences', 'backup', 'about']);
  for (const section of SETTINGS_SECTIONS) {
    assert.ok(section.description.length <= 40 && /。$/.test(section.description), section.id);
    assert.equal(settingsPath(section.id), `/settings/${section.id}`);
  }
});

test('research-only mode shows the sections without the ledger, the memory or the routines', () => {
  assert.deepEqual(sectionsFor('lab').map((section) => section.id), ['models', 'data', 'about']);
  assert.equal(sectionsFor('full').length, 7);
  assert.equal(sectionsFor('demo').length, 7);
  assert.equal(sectionsFor(undefined).length, 7);
});

test('/settings and a section the mode does not show go to the mode\'s first section; an unknown mode waits', () => {
  assert.deepEqual(resolveSection(undefined, 'full'), { section: null, redirect: '/settings/accounts' });
  assert.deepEqual(resolveSection(undefined, 'demo'), { section: null, redirect: '/settings/accounts' });
  assert.deepEqual(resolveSection(undefined, 'lab'), { section: null, redirect: '/settings/models' });
  assert.deepEqual(resolveSection('accounts', 'lab'), { section: null, redirect: '/settings/models' });
  assert.deepEqual(resolveSection('nowhere', 'full'), { section: null, redirect: '/settings/accounts' });
  assert.equal(resolveSection('backup', 'full').section?.label, '备份与导出');
  assert.equal(resolveSection('about', 'lab').section?.id, 'about');
  // Before the backend answered: a known section shows, /settings does not move yet.
  assert.equal(resolveSection('preferences', undefined).section?.id, 'preferences');
  assert.deepEqual(resolveSection(undefined, undefined), { section: null, redirect: null });
});

// -- 交易偏好 --------------------------------------------------------------------------------------

const MEMORY = {
  profile: { experience_level: 'intermediate', trading_style: 'swing', risk_tolerance: 'moderate', preferred_sectors: ['医药', '旧板块'], notes: '单只不超过两成' },
  preferences: { emotional_triggers: ['看到涨停就想追'] },
  history: { lessons: [{ date: '2026-11-16', description: '追涨停买入后亏损', type: 'failure', symbol: 'SHA:600519', lesson: '不追涨停' }] },
};

test('the choices are the backend\'s enums with its own labels, and the sectors its closed list', () => {
  assert.deepEqual(EXPERIENCE_CHOICES.map((choice) => choice.value), ['beginner', 'intermediate', 'expert']);
  assert.deepEqual(STYLE_CHOICES.map((choice) => choice.value), ['day', 'swing', 'position', 'value']);
  assert.deepEqual(RISK_CHOICES.map((choice) => choice.value), ['conservative', 'moderate', 'aggressive']);
  assert.equal(SECTORS.length, new Set(SECTORS).size);
  assert.equal(choiceLabel(STYLE_CHOICES, 'position'), '中线持股');
  assert.equal(choiceLabel(STYLE_CHOICES, undefined), '未设置');
});

test('a saved sector outside the list stays visible so it can be unticked', () => {
  const choices = sectorChoices(['医药', '旧板块']);
  assert.equal(choices.length, SECTORS.length + 1);
  assert.deepEqual(choices.at(-1), { value: '旧板块', extra: true });
  assert.ok(!choices.find((choice) => choice.value === '医药').extra);
  assert.deepEqual(toggleSector(['医药'], '医药', true), ['医药']);
  assert.deepEqual(toggleSector(['医药'], '芯片', true), ['医药', '芯片']);
  assert.deepEqual(toggleSector(['医药', '芯片'], '医药', false), ['芯片']);
});

test('a save sends only what changed: choices, trimmed notes, sectors to add and to remove', () => {
  const saved = profileForm(MEMORY);
  assert.deepEqual(saved.sectors, ['医药', '旧板块']);
  assert.equal(profileChanges(saved, { ...saved }).empty, true);
  assert.equal(profileChanges(saved, { ...saved, notes: ' 单只不超过两成 ' }).empty, true);
  const changes = profileChanges(saved, { ...saved, risk_tolerance: 'aggressive', sectors: ['医药', '芯片'], notes: '' });
  assert.deepEqual(changes, { profile: { risk_tolerance: 'aggressive', notes: '' }, add: ['芯片'], remove: ['旧板块'], empty: false });
  // An empty choice (nothing picked) is never sent as a change.
  assert.deepEqual(profileChanges(saved, { ...saved, trading_style: '' }).profile, {});
  // No memory read: an empty form, nothing to send.
  assert.deepEqual(profileForm(undefined), { experience_level: '', trading_style: '', risk_tolerance: '', sectors: [], notes: '' });
});

test('lessons read as the lesson with its event, and a typed item is checked before it is sent', () => {
  assert.deepEqual(lessonText(MEMORY.history.lessons[0]), { text: '不追涨停', event: '追涨停买入后亏损' });
  assert.deepEqual(lessonText({ ...MEMORY.history.lessons[0], lesson: '' }), { text: '追涨停买入后亏损', event: null });
  assert.equal(itemProblem('  ', []), '请先填写内容');
  assert.equal(itemProblem('看到涨停就想追', ['看到涨停就想追']), '已经记录过了');
  assert.match(itemProblem('长'.repeat(201), []), /最多 200/);
  assert.equal(itemProblem('亏损后加仓', ['看到涨停就想追']), null);
});

// -- 自动化与通知 -----------------------------------------------------------------------------------

const ROUTINES = {
  now: '2026-11-17T10:32:05+08:00',
  scheduler: { alive: true, note: null },
  settings: {
    premarket: { enabled: true, time: '08:45', problem: null, env: { enabled: 'TRACKER_PREMARKET', time: 'TRACKER_PREMARKET_TIME' } },
    postmarket: { enabled: false, time: '15:45', problem: 'TRACKER_POSTMARKET_TIME 不是 HH:MM，按 15:45', env: { enabled: 'TRACKER_POSTMARKET', time: 'TRACKER_POSTMARKET_TIME' } },
    commentary: { enabled: false, env: { enabled: 'TRACKER_BRIEF_COMMENTARY' } },
    close_snapshot: { enabled: true, time: '15:40', env: { enabled: 'TRACKER_CLOSE_SNAPSHOT' } },
  },
  routines: [
    { kind: 'postmarket', title: '盘后简报', trading_date: '2026-11-17', due_at: '2026-11-17T15:45:00+08:00', next_due_at: null, today: null, last_success: null },
    { kind: 'close_snapshot', title: '收盘快照', trading_date: '2026-11-17', due_at: '2026-11-17T15:40:00+08:00', next_due_at: '2026-11-17T15:40:00+08:00',
      today: { status: 'failed', status_text: '失败', summary: '行情取不到', next_attempt_at: '2026-11-17T15:55:00+08:00' },
      last_success: { trading_date: '2026-11-16', finished_at: '2026-11-16T07:41:00+00:00', summary: 'ok' } },
    { kind: 'premarket', title: '盘前简报', trading_date: '2026-11-17', due_at: '2026-11-17T08:45:00+08:00', next_due_at: '2026-11-18T08:45:00+08:00',
      today: { status: 'ok', status_text: '已完成', finished_at: '2026-11-17T00:46:00+00:00' }, last_success: { trading_date: '2026-11-17', finished_at: '2026-11-17T00:46:00+00:00' } },
    { kind: 'later_kind', title: '其他任务', error: '无法读取状态' },
  ],
};

test('routines in the order of the day, with switch, time, today, last success and next run', () => {
  const rows = routineRows(ROUTINES);
  assert.deepEqual(rows.map((row) => row.kind), ['premarket', 'close_snapshot', 'postmarket', 'later_kind']);
  const [pre, snapshot, post, other] = rows;
  assert.deepEqual([pre.switch, pre.time, pre.today, pre.last, pre.next], ['开', '08:45', '已完成 08:46', '2026-11-17 08:46', '2026-11-18 08:45']);
  assert.deepEqual(pre.lines, ['TRACKER_PREMARKET=1', 'TRACKER_PREMARKET_TIME=08:45']);
  assert.deepEqual([snapshot.today, snapshot.todayWarn, snapshot.detail, snapshot.last], ['失败', true, '行情取不到', '2026-11-16 15:41']);
  assert.deepEqual(snapshot.lines, ['TRACKER_CLOSE_SNAPSHOT=1']);
  assert.deepEqual([post.switch, post.next, post.problem, post.today, post.last], ['关', '已关闭', 'TRACKER_POSTMARKET_TIME 不是 HH:MM，按 15:45', '还没运行', '还没有']);
  // A kind the status could not read: 不可用, never blank.
  assert.deepEqual([other.switch, other.time, other.today, other.todayWarn, other.next], [UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, true, UNAVAILABLE]);
  assert.deepEqual(routineRows(undefined), []);
});

test('a run waiting for a close that is not final yet is not shown as a problem', () => {
  const [row] = routineRows({ routines: [{ kind: 'close_snapshot', title: '收盘快照', trading_date: '2026-11-17',
    due_at: '2026-11-17T15:40:00+08:00', next_due_at: '2026-11-17T16:10:00+08:00', today: { status: 'pending',
      status_text: '等待中', error_category: 'waiting', summary: '等待港股收盘价确定，16:10 后记入',
      next_attempt_at: '2026-11-17T16:10:00+08:00' } }] });
  assert.deepEqual([row.today, row.todayWarn, row.detail, row.next], ['等待中', false, '等待港股收盘价确定，16:10 后记入', '2026-11-17 16:10']);
});

test('a day without a session says so', () => {
  const [row] = routineRows({ routines: [{ kind: 'premarket', title: '盘前简报', trading_date: null, due_at: null, next_due_at: '2026-11-18T08:45:00+08:00', today: null }] });
  assert.equal(row.today, '今日休市');
  assert.equal(row.switch, UNAVAILABLE);
});

// -- 数据与行情 ------------------------------------------------------------------------------------

test('the bar cache in figures; the demo keeps none; unknown figures are 不可用', () => {
  const facts = cacheFacts({ symbols: 12, total_rows: 30125, earliest_date: '2016-01-04', latest_date: '2026-11-16', db_path: '/data/bars.db' });
  assert.deepEqual(facts.rows, [['标的', '12'], ['日线', '30,125'], ['日期范围', '2016-01-04 至 2026-11-16'], ['文件', '/data/bars.db']]);
  assert.deepEqual(cacheFacts({ symbols: 0, total_rows: 0, earliest_date: null, latest_date: null }).rows[2], ['日期范围', '还没有数据']);
  assert.deepEqual(cacheFacts({}).rows.slice(0, 3).map(([, value]) => value), [UNAVAILABLE, UNAVAILABLE, UNAVAILABLE]);
  const demo = cacheFacts({ kept: false, symbols: 0 });
  assert.equal(demo.kept, false);
  assert.match(demo.note, /演示模式/);
});

test('the calendar line names the covered years and warns when this year is a guess', () => {
  assert.deepEqual(calendarLine([2026], '2026-11-17'), { text: '2026 年已收录 · 2027 年起按工作日推算', warn: false });
  assert.deepEqual(calendarLine([2027, 2026], '2026-11-17'), { text: '2026–2027 年已收录 · 2028 年起按工作日推算', warn: false });
  assert.deepEqual(calendarLine([2026], '2027-03-02'), { text: '2026 年已收录 · 今年（2027）按工作日推算', warn: true });
  assert.deepEqual(calendarLine([2024, 2026], null), { text: '2024、2026 年已收录 · 2027 年起按工作日推算', warn: false });
  assert.equal(calendarLine([], '2026-11-17').warn, true);
});

// -- 备份与导出, 关于与帮助 ----------------------------------------------------------------------------

test('the backup statement names both files and what neither holds, each line once', () => {
  assert.equal(BACKUP_CONTENTS.ledger.file, 'ledger.sqlite3');
  assert.equal(BACKUP_CONTENTS.desk.file, 'desk.sqlite3');
  const lines = [...BACKUP_CONTENTS.ledger.items, ...BACKUP_CONTENTS.desk.items, ...NOT_IN_BACKUPS];
  assert.equal(new Set(lines).size, lines.length);
  assert.ok(BACKUP_CONTENTS.desk.items.some((line) => line.includes('放弃')), 'rejected proposals travel with the desk file');
  assert.ok(NOT_IN_BACKUPS.some((line) => line.includes('memory.json')), 'the assistant memory is named as outside');
  assert.ok(NOT_IN_BACKUPS.some((line) => line.includes('密钥')), 'keys are named as outside');
});

test('the help is current: no hand-edited files, Chinese only, links to the repository\'s docs', () => {
  for (const tip of ABOUT_TIPS) {
    assert.doesNotMatch(tip, /json|JSON|编辑.*文件|手动修改/, tip);
    assert.ok(tip.endsWith('。'), tip);
  }
  for (const doc of DOC_LINKS) assert.match(doc.href, /^https:\/\/github\.com\/Stepuuu\/portfolio-daily-tracker\/blob\/main\/[\w/]+\.md$/);
  const repo = join(ROOT, '..', '..');
  for (const doc of DOC_LINKS) {
    const file = doc.href.split('/blob/main/')[1];
    assert.ok(existsSync(join(repo, file)), `${file} exists in the repository`);
  }
  assert.deepEqual(Object.keys(MODE_LABEL).sort(), ['demo', 'full', 'lab']);
  assert.ok(ASSISTANT_KEYS.every(([keys, action]) => keys && action));
});

// -- the v3.2 client is gone -----------------------------------------------------------------------

test('no source file brings back the v3.2 portfolio, suggestions or legacy styles', () => {
  for (const removed of [
    'pages/Dashboard.tsx', 'pages/Portfolio.tsx', 'pages/Help.tsx', 'pages/Memory.tsx', 'pages/Settings.tsx',
    'components/PortfolioPanel.tsx', 'components/SuggestionsPanel.tsx', 'components/LedgerProposals.tsx',
    'components/PortfolioLoadNotice.tsx', 'components/Layout.tsx', 'components/Sidebar.tsx', 'components/Header.tsx',
    'services/portfolio.ts', 'services/suggestions.ts', 'lib/currency.ts', 'lib/portfolioLoad.ts', 'lib/positionInput.ts', 'index.css',
  ]) assert.ok(!existsSync(join(SRC, removed)), `${removed} is deleted`);
  const offenders = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(tsx?|css)$/.test(entry.name)) {
        readFileSync(path, 'utf8').split('\n').forEach((line, index) => {
          const name = `${relative(SRC, path)}:${index + 1}`;
          // An API call (axios `api` has the /api base; fetch and links spell it out) to a v3.2 write or read.
          if (/(?:api\.\w+|fetch)\(\s*[`'"](?:\/api)?\/(?:portfolio|suggestions|settings\/cash)\b|['"`]\/api\/(?:portfolio|suggestions|settings\/cash)\b/.test(line)) offenders.push(`${name}: calls a v3.2 route: ${line.trim()}`);
          if (/(?:^|[\s"'`])(?:card|btn-primary|btn-secondary|input-field)(?=[\s"'`])/.test(line) && /className|class=|@apply|^\s*\./.test(line)) offenders.push(`${name}: legacy CSS class: ${line.trim()}`);
          if (/--(?:bg-primary|bg-secondary|bg-tertiary|text-primary|text-secondary|border-color)\b/.test(line)) offenders.push(`${name}: legacy CSS variable: ${line.trim()}`);
        });
      }
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, []);
  const store = readFileSync(join(SRC, 'store', 'index.ts'), 'utf8');
  for (const field of ['suggestions', 'risks', 'portfolio', 'memory']) assert.doesNotMatch(store, new RegExp(`^\\s+${field}:`, 'm'), `store field ${field}`);
});
