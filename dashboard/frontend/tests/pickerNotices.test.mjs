// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// What the instrument picker says besides its rows (src/components/inputs/pickerNotices.ts): the sources the
// search could not read (`omitted`) and the name index's state (`index_reason`), for the empty query too and
// for cached answers, and an empty list that never claims there are no holdings when they could not be read.
import assert from 'node:assert/strict';
import test from 'node:test';
import { pickerNotices } from '../src/components/inputs/pickerNotices.ts';

const HOLDINGS_BUSY = '持仓暂时读取不到（正在写入或读取较慢，超过了本次搜索的等待时间），本次结果中没有持仓';

function answer(overrides = {}) {
  return { query: '', results: [], source: 'none', as_of: null, index_available: true, index_reason: null, omitted: [],
           index_stale: false, index_restart_required: false, ...overrides };
}

test('an empty query with the holdings left out says so instead of "no holdings"', () => {
  // The reviewer's reproduction: an exclusively locked ledger, an empty picker.
  const notices = pickerNotices(answer({ omitted: [{ source: 'holdings', reason: HOLDINGS_BUSY }] }), '');
  assert.deepEqual(notices.omitted, [HOLDINGS_BUSY]);
  assert.ok(!notices.emptyText.includes('还没有持仓'), notices.emptyText);
  assert.equal(pickerNotices(answer(), '').emptyText, '还没有持仓或自选。输入代码或名称搜索。');
});

test('the index reason is shown while a stale index still answers, and for the empty query', () => {
  const stale = answer({ index_available: true, index_stale: true, index_reason: '名称索引停留在 2026-11-17 10:00，更新失败：数据源出错' });
  assert.equal(pickerNotices(stale, '600001').indexReason, stale.index_reason);
  assert.equal(pickerNotices(stale, '').indexReason, stale.index_reason);
  assert.equal(pickerNotices(answer(), '600001').indexReason, null);
});

test('a query with nothing found names the query; an older answer without omitted is read as none', () => {
  const notices = pickerNotices({ ...answer(), omitted: undefined }, '示例');
  assert.deepEqual(notices.omitted, []);
  assert.ok(notices.emptyText.includes('没有找到“示例”'));
  assert.deepEqual(pickerNotices(null, '').omitted, []);
});
