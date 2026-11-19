// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The broker-file import page without a browser (src/features/import): the URL a session resumes from, what
// blocks 核对列, when a batch can be proposed, each row as it will be booked, the edits a row's `needs`
// allow, 对账 lines and their acknowledgement, and the next step for every refused request. Payloads are
// shaped like dashboard/backend/api/imports.py answers for the synthetic fixtures in tests/fixtures/broker.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  MAX_IMPORT_BYTES, STEPS, acceptedValues, accountFilter, accountReviewRows, columnName, columnOptions, confirmedEvents,
  feesChange, mappedPairs, mappingChange, mappingProblems, needsAccountChoice, needsSheetChoice, nothingToBook,
  proposeState, readImportUrl, resumeStep, structuralProblems, windowParams, writeImportUrl,
} from '../src/features/import/flow.ts';
import {
  DERIVED_FEE, DERIVED_PRICE, FIRST_PAGE, NEED_COLUMN, NOT_HERE, PAGE_SIZE, REVIEW_SAMPLE, neededColumns, pageCount, pageLabel,
  rowActions, rowExplanation, rowLines, skippedLine, statedKind, statusTabs, statusTone,
} from '../src/features/import/rows.ts';
import {
  EDIT_KINDS, canEdit, canReset, editKinds, editSpecs, hasEdits, includeEdit, initialEditValues, resetEdit, rowEditPayload, skipEdit,
} from '../src/features/import/edits.ts';
import { reconAckKey, reconLines, reconNeedsAck, reconTally } from '../src/features/import/reconcile.ts';
import {
  NEXT_LABEL, confirmFailure, deleteFailure, patchFailure, proposeFailure, readFailure, uploadFailure,
} from '../src/features/import/errors.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');

const LABELS = { new: '新', imported: '已导入', changed: '与已入账记录不同', possible_duplicate: '可能重复', review: '需处理',
  skipped: '跳过', unsupported: '不支持', merged: '已合并' };

const COLUMNS = ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交价格', '成交价格', '手续费']
  .map((header, index) => ({ index, header, field: null, field_label: null }));

function session(overrides = {}) {
  return {
    id: 'a'.repeat(32), account: '示例账户', account_exists: true, statement: 'transactions', statement_label: '交割单 / 对账单',
    format: { type: 'text', label: 'UTF-8 逗号分隔文本', encoding: 'UTF-8', delimiter: 'comma', sheet: null, sheets: [],
      sheet_options: [], sheet_chosen: false, header_row: 1, extension: '.csv', title_lines: [] },
    preset: { id: 'generic', label: '通用表格', verified: false, note: null },
    columns: COLUMNS,
    mapping: { date: 0, code: 1, name: 2, action: 3, quantity: 4, price: 5, fees: [7] },
    mapping_source: 'synonyms',
    fields: [{ field: 'date', label: '日期', required: true }, { field: 'code', label: '代码', required: true },
      { field: 'action', label: '业务', required: true }, { field: 'price', label: '成交价格', required: false },
      { field: 'fees', label: '费用（可多列，合计）', required: false }],
    column_conflicts: [], accounts: null, short_code_market: null, date_range: { from: '2026-11-03', to: '2026-11-12' },
    counts: { new: 5, imported: 0, changed: 0, possible_duplicate: 0, review: 0, skipped: 3, unsupported: 0, merged: 1,
      total: 9, included_rows: 5, included_events: 5 },
    status_labels: LABELS, skipped: [], reconciliation: null, holdings: null,
    batches: { confirmed: 0, pending: null, remaining_events: 5, remaining_batches: 1, total: 1, next: 1, limit: 1000, history: [] },
    blockers: [], warnings: [], ledger_revision: 1,
    ...overrides,
  };
}

function row(overrides = {}) {
  return {
    row: 3, cells: [], order: 0, status: 'new', status_label: '新', include: true, can_include: true, category: 'buy',
    category_label: '买入', action: '证券买入', date: '2026-11-03', time: null, code: '600519', name: '贵州茅台',
    ticker: 'SHA:600519', currency: 'CNY', quantity: '100', price: '1680', turnover: '168000', cash_change: '-168043.68',
    fee: '43.68', fee_source: 'columns', cash_balance: null, share_balance: null, external_id: 't:1', events: [],
    messages: [], notes: [], needs: [], choices: {}, account_identity: [], edits: {}, duplicate_of: null, existing: null,
    merged_into: null, pair_row: null,
    ...overrides,
  };
}

const BUY = { id: 'e1', date: '2026-11-03', kind: 'buy', account: '示例账户', ticker: 'SHA:600519', currency: 'CNY',
  quantity: '100', price: '1680', fee: '43.68', name: '贵州茅台' };

// -- URL and steps ---------------------------------------------------------------------------------

test('four steps in the wireframe order', () => {
  assert.deepEqual(STEPS.map((step) => step.label), ['选择文件', '核对列', '预览与去重', '确认']);
  assert.equal(MAX_IMPORT_BYTES, 5 * 1024 * 1024);
});

test('the URL keeps the session, step, filter and page; anything else is dropped', () => {
  const id = '0123456789abcdef0123456789abcdef';
  assert.deepEqual(readImportUrl(`?session=${id}&step=3&status=review&page=2`), { session: id, step: 3, status: 'review', page: 2 });
  assert.deepEqual(readImportUrl('?session=../etc&step=9&status=<b>&page=-1'), { session: null, step: null, status: null, page: 1 });
  assert.equal(writeImportUrl({ session: id, step: 3, status: 'new', page: 2 }), `?session=${id}&step=3&status=new&page=2`);
  assert.equal(writeImportUrl({ session: id, step: 2, status: null, page: 1 }), `?session=${id}&step=2`);
  assert.equal(writeImportUrl({ session: null, step: 3, status: 'new' }), '', 'no session: the upload step');
});

test('a workbook needs a sheet choice like the backend: two visible data sheets, or only hidden ones', () => {
  const sheet = (name, hidden) => ({ name, hidden, data_rows: 3 });
  assert.equal(needsSheetChoice({ sheet_options: [], sheet_chosen: false }), false);
  assert.equal(needsSheetChoice({ sheet_options: [sheet('old', true), sheet('Current', false)], sheet_chosen: false }), false,
    'a hidden sheet beside a visible one is ignored by default');
  assert.equal(needsSheetChoice({ sheet_options: [sheet('A', false), sheet('B', false)], sheet_chosen: false }), true);
  assert.equal(needsSheetChoice({ sheet_options: [sheet('old', true)], sheet_chosen: false }), true);
  assert.equal(needsSheetChoice({ sheet_options: [sheet('A', false), sheet('B', false)], sheet_chosen: true }), false);
});

test('核对列 is blocked by every unresolved conflict, the sheet, a guessed account and a missing required column', () => {
  const conflict = { field: 'price', label: '成交价格', columns: [5, 6], chosen: 5, rows: [2], resolved: false };
  const action = { field: 'action', label: '业务', columns: [3, 4], chosen: 3, rows: [2, 4], resolved: false };
  const blocked = session({
    column_conflicts: [conflict, action, { ...conflict, field: 'quantity', resolved: true }],
    accounts: { mixed: true, source: 'dominant', columns: [], accepted: [], accept_rows_without_account: false, rows_without_account: 0,
      rows_kept_out: 0, summary: null },
    mapping: { date: 0, action: 3 },
  });
  const problems = mappingProblems(blocked);
  assert.deepEqual(problems.map((p) => p.key), ['conflict:price', 'conflict:action', 'account', 'required:code']);
  assert.match(problems[0].text, /第 6 列“成交价格”、第 7 列“成交价格” 都可以作为“成交价格”，数值不同，例如第 2 行/);
  assert.deepEqual(mappingProblems(blocked, ['code']).map((p) => p.key), ['conflict:price', 'conflict:action', 'account'],
    'a missing column the person says the file does not have');
  assert.deepEqual(structuralProblems(blocked).map((p) => p.kind), ['conflict', 'conflict', 'account']);
  assert.deepEqual(mappingProblems(session()), []);
  const fees = { field: 'fees', label: '费用', columns: [7, 8], chosen: null, rows: [], resolved: false };
  assert.match(mappingProblems(session({ column_conflicts: [fees] }))[0].text, /名称相同，作为费用合计会重复计算/);
  assert.equal(needsAccountChoice({ mixed: true, source: 'chosen' }), false);
  assert.equal(needsAccountChoice(null), false);
});

test('rows kept out by the account\'s remembered identities need a choice until it is explicit', () => {
  // The count is the backend's, over the whole file (accounts.rows_kept_out): the page loads no rows to count.
  const remembered = { mixed: false, source: 'saved', columns: [], accepted: ['222'], accept_rows_without_account: false,
    rows_without_account: 0, rows_kept_out: 1, summary: null,
    remembered: [{ dimension: 'fund_account', label: '资金账号', value: '111', state: 'excluded' }] };
  const problems = mappingProblems(session({ accounts: remembered }));
  assert.deepEqual(problems.map((p) => p.key), ['account']);
  assert.match(problems[0].text, /^1 行的资金账号或股东代码不是这个账户已确认的/);
  assert.deepEqual(mappingProblems(session({ accounts: { ...remembered, rows_kept_out: 0 } })), [], 'nothing kept out');
  assert.deepEqual(mappingProblems(session({ accounts: { ...remembered, source: 'chosen' } })), [], 'chosen explicitly');
  assert.match(mappingProblems(session({ accounts: { ...remembered, source: 'single', rows_kept_out: 2300 } }))[0].text, /^2300 行的资金账号/);
  assert.deepEqual(structuralProblems(session({ accounts: remembered })).map((p) => p.key), ['account'],
    'known without loading rows, so a reload goes back to 核对列 too');
});

test('parse, GET and PATCH ask for the counts plus one page of rows, never every row', () => {
  assert.deepEqual(FIRST_PAGE, { status: null, offset: 0, limit: PAGE_SIZE });
  assert.deepEqual(windowParams(FIRST_PAGE), { offset: 0, limit: 50 });
  assert.deepEqual(windowParams({ status: 'review', offset: 100, limit: 50 }), { offset: 100, limit: 50, status: 'review' });
  assert.deepEqual(REVIEW_SAMPLE, { status: 'review', offset: 0, limit: 50 });
  const page = readFileSync(join(FRONTEND, 'src/pages/ledger/Import.tsx'), 'utf8');
  const service = readFileSync(join(FRONTEND, 'src/services/imports.ts'), 'utf8');
  assert.doesNotMatch(page, /limit: 500|sliceSession/, 'the page no longer reads 500 需处理 rows to count them');
  assert.match(service, /'\/imports\/parse', form, \{[\s\S]*?params: windowParams\(view\)/);
  assert.match(service, /api\.patch<ImportSession>\(`\/imports\/\$\{encodeURIComponent\(id\)\}`, body, \{ params: windowParams\(view\)/);
});

test('a session resumes its pending review, else the requested step unless 核对列 still has a structural problem', () => {
  const pending = { number: 1, proposal_id: 'p1', events: 5, rows: [3], created_at: '', state: 'pending', stale: false };
  assert.equal(resumeStep(session({ batches: { ...session().batches, pending } }), 3), 4);
  assert.equal(resumeStep(session({ batches: { ...session().batches, pending: { ...pending, stale: true } } }), 4), 3,
    'a stale proposal is prepared again from the preview');
  assert.equal(resumeStep(session(), null), 2);
  assert.equal(resumeStep(session(), 3), 3);
  assert.equal(resumeStep(session(), 4), 3, 'nothing proposed or booked yet');
  assert.equal(resumeStep(session({ batches: { ...session().batches, confirmed: 1, next: null } }), 4), 4, 'the summary');
  const conflicted = session({ column_conflicts: [{ field: 'price', label: '成交价格', columns: [5, 6], chosen: 5, rows: [2], resolved: false }] });
  assert.equal(resumeStep(conflicted, 3), 2);
});

test('a batch is proposed only without blockers; the label says the batch and its size', () => {
  assert.deepEqual(proposeState(session()), { allowed: true, reason: null, label: '生成入账预览（5 笔 · 第 1/1 批）', detail: null });
  const big = proposeState(session({ batches: { ...session().batches, remaining_events: 2300, remaining_batches: 3, total: 3, next: 1 } }));
  assert.equal(big.label, '生成入账预览（第 1/3 批）');
  assert.match(big.detail, /还有 2,300 笔，分 3 批，每批最多 1,000 笔/);
  const blocked = proposeState(session({ blockers: ['账户“示例账户”还没有期初余额。'] }));
  assert.equal(blocked.allowed, false);
  assert.equal(blocked.reason, '账户“示例账户”还没有期初余额。');
  const reimport = session({ counts: { ...session().counts, new: 0, imported: 5, included_rows: 0 },
    batches: { ...session().batches, remaining_events: 0, remaining_batches: 0, total: 1, confirmed: 1, next: null } });
  assert.equal(nothingToBook(reimport), '全部已导入，没有需要入账的新记录。');
  assert.equal(proposeState(reimport).allowed, false);
  const onlyReview = session({ counts: { ...session().counts, new: 0, review: 2 }, batches: { ...session().batches, next: null } });
  assert.match(nothingToBook(onlyReview), /2 行需处理/);
  const opening = session({ statement: 'holdings', holdings: { mode: 'opening' }, batches: { ...session().batches, next: null } });
  assert.deepEqual(proposeState(opening), { allowed: true, reason: null, label: '生成期初入账预览', detail: null });
  assert.equal(proposeState({ ...opening, holdings: { mode: 'reconcile' } }).allowed, false);
  const history = [{ state: 'confirmed', events: 1000 }, { state: 'discarded', events: 1000 }, { state: 'confirmed', events: 300 }];
  assert.equal(confirmedEvents(session({ batches: { ...session().batches, history } })), 1300);
});

// -- mapping ---------------------------------------------------------------------------------------

test('mapping: column names, options with a sample value, pairs, and a column used once', () => {
  assert.equal(columnName(COLUMNS, 5), '第 6 列“成交价格”');
  assert.equal(columnName(COLUMNS, null), '未选择');
  const options = columnOptions(COLUMNS.slice(0, 2), row({ cells: ['2026-11-03', '600519'] }));
  assert.deepEqual(options, [{ value: '0', label: '第 1 列 · 成交日期（2026-11-03）' }, { value: '1', label: '第 2 列 · 证券代码（600519）' }]);
  const pairs = mappedPairs(session({ mapping: { date: 0, code: 1, fees: [6, 7] } }));
  assert.deepEqual(pairs.map((p) => `${p.from}→${p.to}`), ['成交日期→日期', '证券代码→代码', '成交价格 + 手续费→费用']);
  assert.deepEqual(mappingChange({ date: 0, price: 5, fees: [7] }, 'price', 6), { price: 6 });
  assert.deepEqual(mappingChange({ date: 0, price: 5, fees: [6, 7] }, 'price', 6), { price: 6, fees: [7] }, 'taken from the fees');
  assert.deepEqual(mappingChange({ date: 0, time: 5 }, 'price', 5), { price: 5, time: null }, 'taken from another field');
  assert.deepEqual(mappingChange({ price: 5 }, 'price', null), { price: null });
  assert.deepEqual(feesChange({ price: 5, fees: [7] }, [7, 5, 7]), { fees: [5, 7], price: null });
});

test('account identities: accepted values, the filter sent, and the rows kept in review with the reasons', () => {
  const accounts = {
    mixed: true, source: 'dominant', accepted: ['111', 'A000000001'], accept_rows_without_account: false, rows_without_account: 0,
    summary: '资金账号 111（2 行）、222（1 行）',
    columns: [
      { dimension: 'fund_account', label: '资金账号', values: [{ value: '111', rows: 2, accepted: true }, { value: '222', rows: 1, accepted: false }] },
      { dimension: 'shareholder:SH', label: '沪市股东代码', values: [{ value: 'A000000001', rows: 2, accepted: true }, { value: 'A000000002', rows: 1, accepted: false }] },
    ],
  };
  assert.deepEqual(acceptedValues(accounts), ['111', 'A000000001']);
  assert.deepEqual(acceptedValues({ ...accounts, accept_rows_without_account: true }), ['111', 'A000000001', '']);
  assert.deepEqual(accountFilter(accounts, new Set(['', 'A000000002', '222'])), ['222', 'A000000002', '']);
  const why = '这一行属于资金账号 222，不是为账户“示例账户”选择的资金账号 111。';
  const rows = [row({ status: 'review', needs: ['account'], messages: [why] }), row({ status: 'review', needs: ['account'], messages: [why] }),
    row({ status: 'review', needs: ['kind'] }), row()];
  assert.deepEqual(accountReviewRows({ rows_kept_out: 2 }, rows), { count: 2, reasons: [{ text: why, count: 2 }], complete: true });
  assert.deepEqual(accountReviewRows({ rows_kept_out: 900 }, rows), { count: 900, reasons: [{ text: why, count: 2 }], complete: false },
    'the count is the backend\'s; the first page of 需处理 rows only gives examples of the reasons');
  assert.deepEqual(accountReviewRows({ rows_kept_out: 3 }, undefined), { count: 3, reasons: [], complete: false });
});

// -- rows ------------------------------------------------------------------------------------------

test('status filters: 全部 and the six in order with the backend counts (有变化 for changed); others only when present', () => {
  const tabs = statusTabs(session({ counts: { ...session().counts, unsupported: 1, merged: 0, later_status: 2 },
    status_labels: { ...LABELS, later_status: '新状态' } }));
  assert.deepEqual(tabs.map((tab) => `${tab.label} ${tab.count}`),
    ['全部 9', '新 5', '已导入 0', '可能重复 0', '有变化 0', '需处理 0', '跳过 3', '不支持 1', '新状态 2']);
  assert.equal(tabs[1].id, 'new');
  assert.equal(statusTone('new'), 'accent');
  assert.equal(statusTone('review'), 'warn');
  assert.equal(statusTone('possible_duplicate'), 'warn');
  assert.equal(statusTone('imported'), 'neutral');
  assert.equal(statusTone('some_new_status'), 'neutral');
});

const text = (line, field) => line[field].text;

test('a bookable row shows its events exactly as booked: price digits kept, cash effect signed', () => {
  const [line] = rowLines(row({ price: '32.01666667', events: [{ ...BUY, price: '32.01666667', quantity: '300', fee: '5.1' }] }), 'transactions');
  assert.equal(line.kind, '买入');
  assert.equal(line.name, '贵州茅台');
  assert.equal(line.code, 'SHA:600519');
  assert.equal(text(line, 'quantity'), '300');
  assert.deepEqual(line.price, { text: '32.01666667' }, 'eight places: every digit, no title');
  assert.equal(text(line, 'fee'), '5.10');
  assert.equal(text(line, 'amount'), '-9,610.10');
  assert.equal(line.priceNote, null);
  assert.equal(line.feeNote, null);
  const [buy] = rowLines(row({ events: [BUY] }), 'transactions');
  assert.equal(text(buy, 'amount'), '-168,043.68');
  assert.equal(text(buy, 'price'), '1,680.00');
});

test('a price worked out as 成交金额 ÷ 数量 is ≈ rounded to 8 places, keeps its digits for the title and says how', () => {
  const price = '32.016666666666666666666666667';
  const derived = row({ price, price_source: 'derived', fee: '5.1', fee_source: 'derived',
    events: [{ ...BUY, price, quantity: '300', fee: '5.1' }] });
  const [line] = rowLines(derived, 'transactions');
  assert.deepEqual(line.price, { text: '≈32.01666667', full: price, label: `约 32.01666667，完整值 ${price}` });
  assert.equal(line.priceNote, DERIVED_PRICE);
  assert.equal(DERIVED_PRICE, '成交金额 ÷ 数量');
  assert.equal(line.feeNote, DERIVED_FEE, 'fee_source "derived": the fee was worked out from the cash amount');
  assert.equal(text(line, 'amount'), '-9,610.10', 'the cash effect worked out here is shown to the cent, never ≈');
  const columns = rowLines(row({ price_source: 'columns', fee_source: 'columns', events: [BUY] }), 'transactions')[0];
  assert.deepEqual([columns.priceNote, columns.feeNote], [null, null]);
  const edited = rowLines(row({ price_source: 'edited', fee_source: 'edited', events: [BUY] }), 'transactions')[0];
  assert.deepEqual([edited.priceNote, edited.feeNote], [null, null]);
  // A row that books nothing yet still shows the file's figures, and a derived price says so.
  const review = rowLines(row({ status: 'review', price, price_source: 'derived', needs: ['ticker'], events: [] }), 'transactions')[0];
  assert.equal(text(review, 'price'), '≈32.01666667');
  assert.equal(review.priceNote, DERIVED_PRICE);
  // The duplicate's line names its price the same way, the full line kept for the title.
  const duplicate = rowExplanation(row({ status: 'possible_duplicate', duplicate_of: { ...BUY, price, quantity: '300', source: 'import:示例账户', source_label: '交割单导入' } }));
  assert.match(duplicate.reference.text, /@ ≈32\.01666667/);
  assert.match(duplicate.reference.full, /@ 32\.016666666666666666666666667/);
});

test('a 港股通 row is two lines: 换汇 and the HKD trade with the currency symbol', () => {
  const lines = rowLines(row({ ticker: 'HKG:00700', name: '腾讯控股', currency: 'HKD', events: [
    { id: 'fx', date: '2026-11-05', kind: 'fx', currency: 'CNY', amount: '70008.35', to_currency: 'HKD', received_amount: '76096.03' },
    { id: 'b', date: '2026-11-05', kind: 'buy', ticker: 'HKG:00700', currency: 'HKD', quantity: '200', price: '380', fee: '96.03', name: '腾讯控股' },
  ] }), 'transactions');
  assert.equal(lines.length, 2);
  assert.equal(lines[0].kind, '换汇');
  assert.equal(lines[0].name, 'CNY → HKD');
  assert.equal(text(lines[0], 'amount'), '-70,008.35 → +HK$76,096.03');
  assert.equal(text(lines[1], 'price'), 'HK$380.000');
  assert.equal(text(lines[1], 'fee'), 'HK$96.03');
  assert.equal(text(lines[1], 'amount'), '-HK$76,096.03');
});

test('cash events, splits and interest have no trade cells', () => {
  const [deposit] = rowLines(row({ code: '', name: '', ticker: null, events: [{ id: 'd', date: '2026-11-02', kind: 'deposit', currency: 'CNY', amount: '50000' }] }), 'transactions');
  assert.deepEqual([deposit.kind, deposit.name, text(deposit, 'quantity'), text(deposit, 'price'), text(deposit, 'fee'), text(deposit, 'amount')],
    ['入金', '现金', '', '', '', '+50,000.00']);
  const [split] = rowLines(row({ events: [{ id: 's', date: '2026-11-05', kind: 'split', ticker: 'SHA:600036', ratio: '1.2', name: '招商银行' }] }), 'transactions');
  assert.deepEqual([split.kind, text(split, 'quantity'), text(split, 'amount')], ['拆股/送转', '×1.2', '']);
});

test('a row that books nothing shows what the file states; needed columns are flagged', () => {
  const ipo = row({ status: 'review', category: 'ipo_payment', category_label: '中签缴款', action: '中签缴款', code: '732999',
    name: '示例新股', ticker: null, currency: null, price: '10', fee: null, cash_change: '-5000', needs: ['kind', 'ticker', 'price', 'fee'] });
  const [line] = rowLines(ipo, 'transactions');
  assert.deepEqual([line.kind, line.code, text(line, 'price'), text(line, 'fee'), text(line, 'amount')], ['中签缴款', '732999', '10.00', '', '-5,000.00']);
  assert.deepEqual([...neededColumns(ipo)].sort(), ['code', 'fee', 'kind', 'price']);
  assert.equal(neededColumns(row({ events: [BUY], needs: ['fee'] })).size, 0, 'a row with events is not flagged');
  assert.equal(NEED_COLUMN.ticker, 'code');
  const unknown = row({ status: 'review', category: 'unknown', category_label: '未知业务', action: '资金调整', code: '', name: '', ticker: null });
  assert.equal(statedKind(unknown, 'transactions'), '资金调整');
  assert.equal(rowLines(unknown, 'transactions')[0].name, '', 'a row that books nothing is not called 现金');
  assert.equal(statedKind(row({ category: 'skip', category_label: '跳过', action: '撤单' }), 'transactions'), '跳过（撤单）');
  assert.equal(statedKind(row(), 'transactions'), '买入', '证券买入 says the same');
  assert.equal(statedKind(row({ action: 'deposit', category_label: '模板行' }), 'template'), '入金');
  const holding = rowLines(row({ category: 'position', category_label: 'position', action: '', price: '1650', cash_change: null }), 'holdings')[0];
  assert.deepEqual([holding.kind, text(holding, 'price'), text(holding, 'fee'), text(holding, 'amount')], ['持仓', '1,650.00', '', '']);
});

test('reasons are the backend words; a duplicate names the existing event, its listing and where it came from', () => {
  const why = '与 2026-11-03 的手工记录相同（贵州茅台 SHA:600519 100 @ 1680），可能已经记过。确认不是同一笔再勾选。';
  const duplicate = row({ status: 'possible_duplicate', messages: [why],
    duplicate_of: { id: 'x', source: 'manual', date: '2026-11-03', kind: 'buy', ticker: 'SHA:600519', quantity: '100', price: '1680', fee: '5',
      name: '贵州茅台', source_label: '手工' } });
  const explained = rowExplanation(duplicate);
  assert.deepEqual(explained.reasons, [why]);
  assert.deepEqual(explained.reference, { text: '已有记录（手工）：2026-11-03 买入 贵州茅台 SHA:600519 100 股 @ 1,680.00 · 费用 5.00' });
  assert.equal(explained.unfixable, null);
  // The backend's source label wins; a split says the shares it added.
  const split = rowExplanation(row({ status: 'possible_duplicate', category: 'split', duplicate_of: { id: 's', source: 'corporate-action', date: '2026-11-05',
    kind: 'split', ticker: 'SHA:600036', ratio: '1.2', name: '招商银行', source_label: '公司行为', added_quantity: '200' } }));
  assert.equal(split.reference.text, '已有记录（公司行为）：2026-11-05 拆股/送转 招商银行 SHA:600036 比例 1.2 · 到账 200 股');
  assert.equal(rowExplanation(row()).reference, null);
  assert.match(rowExplanation(row({ existing: { date: '2026-11-03', kind: 'deposit', amount: '50', currency: 'CNY', source: 'manual' } })).reference.text,
    /^已入账记录（手工）：2026-11-03 入金 ¥50\.00/);
});

test('a 需处理 row the page cannot fix shows the backend\'s sentence and offers only 跳过', () => {
  const sentence = `账本无法接受这一行：卖出数量超过持仓。${NOT_HERE}：请核对文件，或把它设为跳过。`;
  const stuck = row({ status: 'review', needs: [], messages: [sentence], events: [] });
  const explained = rowExplanation(stuck);
  assert.equal(explained.unfixable, sentence);
  assert.deepEqual(explained.reasons, [], 'said once');
  assert.deepEqual(rowActions(stuck), { edit: false, skip: true, reset: false });
  assert.match(rowExplanation(row({ status: 'review', needs: [], messages: ['旧版本的说明'] })).unfixable, /^这一行无法在这里修改：/,
    'without the backend\'s sentence the page still says why nothing can be filled in');
  assert.deepEqual(rowActions(row({ status: 'review', needs: ['ticker'] })), { edit: true, skip: true, reset: false });
  assert.deepEqual(rowActions(row({ status: 'review', needs: [], edits: { kind: 'buy' } })), { edit: false, skip: true, reset: true });
  assert.deepEqual(rowActions(row()), { edit: false, skip: false, reset: false });
  assert.equal(rowExplanation(row({ status: 'review', needs: ['fee'] })).unfixable, null);
});

test('a template row that needs its fee gets the fee field, and only that', () => {
  const template = row({ status: 'review', category: 'buy', action: 'buy', needs: ['fee'], fee: null, fee_source: null,
    messages: ['买入或卖出需要填写费用；确实没有费用填 0。'] });
  assert.deepEqual(editSpecs(template).map((spec) => `${spec.need}:${spec.control}`), ['fee:money']);
  assert.deepEqual(rowActions(template), { edit: true, skip: true, reset: false });
  assert.deepEqual(rowEditPayload(template, { fee: '5' }), { edit: { row: 3, fee: '5' } });
  assert.equal(rowExplanation(template).unfixable, null);
});

test('skipped summary and pager wording', () => {
  const lines = skippedLine({ status_labels: LABELS, skipped: [
    { status: 'skipped', label: '撤单 / 废单', count: 5, rows: [] },
    { status: 'unsupported', label: '北交所证券：账本暂不支持北交所。', count: 1, rows: [] },
    { status: 'merged', label: '逆回购 2 行合并为利息 4.89', count: 1, rows: [], amount: '4.89' },
  ] });
  assert.deepEqual(lines.map((line) => line.text), ['撤单 / 废单 5 行', '北交所证券：账本暂不支持北交所。（不支持） 1 行', '逆回购 2 行合并为利息 4.89']);
  assert.equal(pageLabel(0, 50, 214), '第 1–50 行，共 214 行');
  assert.equal(pageLabel(0, 0, 0), '没有行');
  assert.equal(pageCount(2300), 46);
  assert.equal(pageCount(0), 1);
  assert.equal(PAGE_SIZE, 50);
});

// -- edits -----------------------------------------------------------------------------------------

test('the edit form offers exactly the needs, in their order; an unknown need is shown by name', () => {
  const ipo = row({ status: 'review', needs: ['kind', 'ticker', 'price', 'fee', 'kind'] });
  assert.deepEqual(editSpecs(ipo).map((spec) => `${spec.need}:${spec.control}`), ['kind:kind', 'ticker:ticker', 'price:price', 'fee:money']);
  assert.deepEqual(editSpecs(row({ needs: ['account', 'mapping', 'something_new'] })).map((spec) => `${spec.control}:${spec.label}`),
    ['account:账户识别', 'mapping:列对应', 'other:something_new']);
  assert.match(editSpecs(row({ needs: ['quantity'] }))[0].hint, /不是手/);
  assert.deepEqual(editKinds('transactions'), EDIT_KINDS);
  assert.deepEqual(editKinds('holdings'), []);
  assert.deepEqual(editKinds('template'), []);
  assert.equal(canEdit(ipo), true);
  assert.equal(canEdit(row()), false);
});

test('edit values start from stored edits; the PATCH item carries only filled needs', () => {
  const ipo = row({ row: 9, status: 'review', needs: ['kind', 'ticker', 'price', 'fee', 'allow_margin', 'account'], edits: { price: '10', include: true } });
  assert.deepEqual(initialEditValues(ipo), { kind: '', ticker: '', price: '10', fee: '', allow_margin: false });
  assert.deepEqual(rowEditPayload(ipo, { kind: 'buy', ticker: ' SHA:603999 ', price: '10', fee: '', allow_margin: false }),
    { edit: { row: 9, kind: 'buy', ticker: 'SHA:603999', price: '10' } });
  assert.deepEqual(rowEditPayload(ipo, { allow_margin: true }), { edit: { row: 9, allow_margin: true } });
  assert.match(rowEditPayload(ipo, {}).error, /请至少填写一项/);
  assert.deepEqual(includeEdit(ipo, false), { row: 9, include: false });
  assert.deepEqual(skipEdit(ipo), { row: 9, kind: 'skip' });
  assert.deepEqual(resetEdit(ipo), { row: 9, reset: true });
  assert.equal(hasEdits(ipo), true);
  assert.equal(hasEdits(row()), false);
  assert.equal(canReset(ipo), true);
  assert.equal(canReset({ ...ipo, status: 'imported' }), false);
  assert.equal(canReset(row()), false);
});

// -- reconciliation --------------------------------------------------------------------------------

const RECON = {
  available: true, as_of: '2026-11-13', consistent: false, summary: '导入后 CNY 现金 515,796.38，券商资金余额 499,791.88，差 CNY 16,004.50',
  causes: ['需处理 3 行没有入账，资金变动合计 -14,999.50'],
  cash: [{ currency: 'CNY', file: '499791.88', ledger: '515796.38', difference: '16004.5', status: '差额', label: '差 CNY 16,004.50', row: 13 }],
  positions: [
    { ticker: 'SHA:600036', code: '600036', name: '招商银行', file: '1800', ledger: '1800', difference: '0', status: '一致', label: '一致', row: 6 },
    { ticker: null, code: '787001', name: '示例新股', file: '500', ledger: null, difference: null, status: '未核对', label: '代码未确定，未核对', row: 10 },
  ],
};

test('对账 lines: 不一致 with both figures and the difference, 未核对 with its reason', () => {
  const lines = reconLines(RECON);
  assert.deepEqual(lines.map((line) => [line.label, line.stateText, line.ours, line.theirs, line.detail]), [
    ['CNY 现金', '不一致', '¥515,796.38', '¥499,791.88', '差 CNY 16,004.50'],
    ['招商银行 SHA:600036', '一致', '1,800 股', '1,800 股', null],
    ['示例新股 787001', '未核对', null, '500 股', '代码未确定，未核对'],
  ]);
  assert.equal(reconTally(lines), '一致 1 · 不一致 1 · 未核对 1');
  assert.equal(reconNeedsAck(RECON), true);
  assert.deepEqual(reconLines({ available: false, reason: '文件没有资金余额或股份余额列，无法对账。' }), []);
  assert.equal(reconNeedsAck(null), false);
});

test('a mismatch needs a new acknowledgement when the differences change; an unknown status is a mismatch', () => {
  const consistent = { ...RECON, consistent: true, cash: [{ ...RECON.cash[0], ledger: '499791.88', difference: '0', status: '一致', label: '一致' }] };
  assert.equal(reconNeedsAck(consistent), false, '未核对 alone is shown, not acknowledged');
  const key = reconAckKey(RECON);
  assert.notEqual(key, '');
  assert.equal(reconAckKey({ ...RECON }), key);
  assert.notEqual(reconAckKey({ ...RECON, cash: [{ ...RECON.cash[0], ledger: '515800.00' }] }), key);
  const strange = { ...consistent, cash: [{ ...consistent.cash[0], status: '待定' }] };
  assert.equal(reconNeedsAck(strange), true);
  assert.equal(reconLines(strange)[0].stateText, '不一致');
});

// -- refused requests ------------------------------------------------------------------------------

test('every status code of the contract has its own title and next step', () => {
  assert.deepEqual([413, 415, 422, 503].map((s) => uploadFailure(s, 'x').title), ['文件太大', '无法读取这种文件', '这个文件不能导入', '没有上传成功']);
  assert.equal(uploadFailure(413, '文件超过 5 MB。').message, '文件超过 5 MB。', 'the backend words are the text');
  assert.equal(uploadFailure(503, '').next, 'retry');
  assert.equal(readFailure(404, '').next, 'restart');
  assert.deepEqual([422, 409, 503, 404].map((s) => patchFailure(s, '').next), ['none', 'reload', 'retry', 'restart']);
  assert.equal(patchFailure(503, '').title, '账本正忙，修改没有保存');
  assert.equal(patchFailure(409, '').title, '导入记录在别处被修改了');
  assert.deepEqual([409, 422, 503, 404].map((s) => proposeFailure(s, '').next), ['again', 'none', 'retry', 'restart']);
  assert.equal(deleteFailure(404, '').next, 'restart');
  assert.equal(deleteFailure(409, '').next, 'retry', 'a concurrent edit: discard again');
  assert.equal(NEXT_LABEL.again, '重新生成入账预览');
  assert.equal(NEXT_LABEL.none, null);
});

test('确认入账: a refusal or a preview that is gone sends the page back; a lost answer never claims 没有入账', () => {
  const refused = confirmFailure({ state: 'refused', message: '导入记录已变化' }, true);
  assert.deepEqual(refused, { status: undefined, title: '没有入账：这份入账预览已作废', message: '导入记录已变化', next: 'back' });
  assert.equal(confirmFailure({ state: 'not_booked', message: '这份预览已不存在' }, true).next, 'back');
  assert.equal(confirmFailure({ state: 'not_booked', message: '仍未入账，可以再次确认' }, false), null, 'still pending: confirm again');
  assert.equal(confirmFailure({ state: 'unknown' }, true), null, 'the review says 结果未知 itself');
  assert.equal(confirmFailure({ state: 'checking' }, true), null);
  assert.equal(confirmFailure(null, false), null);
});

// -- the page's sources ------------------------------------------------------------------------------

function sources(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sources(path));
    else if (/\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

// The one rounding allowed is lib/format.ts `fmtLong` (past 8 places: ≈ and 8 places, the full value kept): the
// import page never writes its own "≈" or rounds a figure to some number of places itself.
test('the import page renders file text as text and never turns money into binary numbers', () => {
  const files = [...sources(join(FRONTEND, 'src/components/import')), ...sources(join(FRONTEND, 'src/features/import')),
    join(FRONTEND, 'src/pages/ledger/Import.tsx'), join(FRONTEND, 'src/services/imports.ts')];
  const offenders = [];
  for (const file of files) {
    readFileSync(file, 'utf8').split('\n').forEach((line, index) => {
      if (/^\s*(\/\/|\*)/.test(line)) return;
      if (/dangerouslySetInnerHTML|parseFloat\(|\.toFixed\(|(?<![\w.])Number\((?:row|line|item|value|event|session)\.|≈|SHOWN_PLACES|\bdigits:\s*\d/.test(line)) {
        offenders.push(`${file.slice(FRONTEND.length + 1)}:${index + 1}: ${line.trim()}`);
      }
    });
  }
  assert.deepEqual(offenders, []);
});

test('the legacy CSV dialog is gone and the routes list the import tab', () => {
  const routes = readFileSync(join(FRONTEND, 'src/routes.tsx'), 'utf8');
  assert.match(routes, /path: '\/ledger\/import', label: '导入', element: lazy\(\(\) => import\('\.\/pages\/ledger\/Import'\)\), modes: DESK/);
  const ledger = sources(join(FRONTEND, 'src'));
  assert.ok(!ledger.some((file) => file.endsWith('CsvImportDialog.tsx')));
  const migrated = JSON.parse(readFileSync(join(FRONTEND, 'tests/migrated.json'), 'utf8')).paths;
  // Held to the kit's conventions: listed itself or under a listed folder (since UI-6b the list is `src/`).
  for (const path of ['src/components/import/', 'src/services/imports.ts', 'src/features/', 'src/pages/ledger/']) {
    assert.ok(migrated.some((prefix) => path.startsWith(prefix)), path);
  }
});

test('全部已导入 only when nothing is left: no review, unsupported or changed rows besides the imported ones', async () => {
  const flow = await import('../src/features/import/flow.ts');
  const done = { batches: { ...session().batches, remaining_events: 0, remaining_batches: 0, total: 1, confirmed: 1, next: null } };
  const counts = (extra) => ({ ...session().counts, new: 0, possible_duplicate: 0, review: 0, unsupported: 0, changed: 0,
    imported: 7, included_rows: 0, ...extra });
  assert.equal(flow.allImported(session({ ...done, counts: counts({}) })), true);
  assert.equal(flow.allImported(session({ ...done, counts: counts({ skipped: 3, merged: 1 }) })), true, 'skipped rows book nothing');
  for (const left of [{ review: 4, unsupported: 1 }, { unsupported: 1 }, { changed: 2 }, { possible_duplicate: 1 }]) {
    const mixed = session({ ...done, counts: counts(left) });
    assert.equal(flow.allImported(mixed), false, JSON.stringify(left));
    assert.notEqual(nothingToBook(mixed), '全部已导入，没有需要入账的新记录。', JSON.stringify(left));
  }
  assert.match(nothingToBook(session({ ...done, counts: counts({ unsupported: 1 }) })), /1 行不支持/);
});
