// The import page's steps without React: what the URL holds, which step a session resumes at, what must be
// settled before the preview (2 核对列), whether a batch can be proposed now, and the mapping and account
// choices as the backend describes them. Pure TypeScript; tests/imports.test.mjs.
import type {
  ColumnConflict, ImportAccounts, ImportColumn, ImportFormat, ImportMapping, ImportRow, ImportSession, RowsView, Statement,
} from './types.ts'

export type StepId = 1 | 2 | 3 | 4

export const STEPS: { id: StepId; label: string }[] = [
  { id: 1, label: '选择文件' },
  { id: 2, label: '核对列' },
  { id: 3, label: '预览与去重' },
  { id: 4, label: '确认' },
]

/** The upload limit the API states (backend/api/imports.py, sniff.MAX_BYTES). */
export const MAX_IMPORT_BYTES = 5 * 1024 * 1024
export const TOO_LARGE = '文件超过 5 MB。请缩短导出的日期范围后再试。'
/** A hint for the file picker; the backend reads the content, not the name, and says what it cannot read. */
export const ACCEPTED_FILES = '.csv,.txt,.xls,.xlsx,text/csv,text/plain,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** The statement kinds the API takes, with the labels of its `statement_label`. */
export const STATEMENT_LABELS: Record<Statement, string> = {
  transactions: '交割单 / 对账单',
  holdings: '持仓',
  template: '本系统模板',
}

// ------------------------------------------------------------------------------------------------
// URL: ?session=<32 hex>&step=2&status=new&page=2

export interface ImportUrl {
  session: string | null
  step: StepId | null
  status: string | null
  page: number
}

const SESSION_ID = /^[0-9a-f]{32}$/

export function readImportUrl(search: string | URLSearchParams): ImportUrl {
  const params = typeof search === 'string' ? new URLSearchParams(search) : search
  const session = params.get('session')
  const step = Number(params.get('step'))
  const page = Number(params.get('page'))
  const status = params.get('status')
  return {
    session: session && SESSION_ID.test(session) ? session : null,
    step: step === 1 || step === 2 || step === 3 || step === 4 ? step : null,
    status: status && /^[a-z_]{1,32}$/.test(status) ? status : null,
    page: Number.isInteger(page) && page > 1 ? page : 1,
  }
}

/** The query string for a state; page 1, no filter and no session are left out. */
export function writeImportUrl(state: Partial<ImportUrl>): string {
  const params = new URLSearchParams()
  if (state.session) params.set('session', state.session)
  if (state.session && state.step && state.step > 1) params.set('step', String(state.step))
  if (state.session && state.status) params.set('status', state.status)
  if (state.session && state.page && state.page > 1) params.set('page', String(state.page))
  const text = params.toString()
  return text ? `?${text}` : ''
}

/**
 * The query of parse, GET and PATCH alike: one status (or every row) and one page. The answer's counts,
 * reconciliation and accounts always describe the whole file, so the page asks for those plus the rows it shows.
 */
export function windowParams(view: RowsView): Record<string, string | number> {
  const params: Record<string, string | number> = { offset: view.offset, limit: view.limit }
  if (view.status) params.status = view.status
  return params
}

// ------------------------------------------------------------------------------------------------
// Step 2: what must be settled before the preview

export interface MappingProblem {
  key: string
  kind: 'conflict' | 'sheet' | 'account' | 'required'
  /** One sentence in Chinese. */
  text: string
  field?: string
}

/** Mirrors the backend's sheet blocker: more than one visible data sheet, or only hidden ones, until chosen. */
export function needsSheetChoice(format: Pick<ImportFormat, 'sheet_options' | 'sheet_chosen'>): boolean {
  if (format.sheet_chosen) return false
  const options = format.sheet_options ?? []
  const visible = options.filter((option) => !option.hidden)
  return visible.length > 1 || (options.length > 0 && visible.length === 0)
}

/** "第 6 列“成交价格”". */
export function columnName(columns: ImportColumn[], index: number | null | undefined): string {
  if (index === null || index === undefined) return '未选择'
  const header = columns.find((column) => column.index === index)?.header
  return header ? `第 ${index + 1} 列“${header}”` : `第 ${index + 1} 列`
}

export function conflictText(conflict: ColumnConflict, columns: ImportColumn[]): string {
  const names = conflict.columns.map((index) => columnName(columns, index)).join('、')
  if (conflict.chosen === null) return `${names} 名称相同，作为费用合计会重复计算。请选择要计入的费用列。`
  const rows = conflict.rows.length ? `，例如第 ${conflict.rows.slice(0, 3).join('、')} 行` : ''
  return `${names} 都可以作为“${conflict.label}”，数值不同${rows}。请确认用哪一列。`
}

export function isMapped(mapping: ImportMapping, field: string): boolean {
  const value = mapping[field]
  return Array.isArray(value) ? value.length > 0 : typeof value === 'number'
}

/** A file of several identities whose choice is still the preview's guess ("dominant"). */
export function needsAccountChoice(accounts: ImportAccounts | null | undefined): boolean {
  return Boolean(accounts && accounts.mixed && accounts.source === 'dominant')
}

/**
 * What blocks 继续 on step 2: unresolved column conflicts (action, market and 股/手 included, as the
 * backend lists them), the sheet of a workbook, the account identities of a mixed file, rows kept out by
 * the account's remembered identities until the person chooses (the backend's `accounts.rows_kept_out`,
 * counted over the whole file), and required fields without a column unless the person said the file has
 * none (`acknowledged` fields).
 */
export function mappingProblems(
  session: Pick<ImportSession, 'column_conflicts' | 'format' | 'accounts' | 'fields' | 'mapping' | 'columns'>,
  acknowledged: string[] = [],
): MappingProblem[] {
  const accountReview = session.accounts?.rows_kept_out ?? 0
  const problems: MappingProblem[] = []
  for (const conflict of session.column_conflicts ?? []) {
    if (!conflict.resolved) {
      problems.push({ key: `conflict:${conflict.field}`, kind: 'conflict', field: conflict.field, text: conflictText(conflict, session.columns) })
    }
  }
  if (needsSheetChoice(session.format)) {
    problems.push({ key: 'sheet', kind: 'sheet', text: '工作簿里有不止一个可能的数据表，请确认要导入的工作表。' })
  }
  if (needsAccountChoice(session.accounts)) {
    problems.push({ key: 'account', kind: 'account', text: '文件里有多个资金账号或股东代码，请确认哪些属于这个账户。' })
  } else if (session.accounts && session.accounts.source !== 'chosen' && accountReview > 0) {
    problems.push({
      key: 'account', kind: 'account',
      text: `${accountReview} 行的资金账号或股东代码不是这个账户已确认的（以前没有导入过、被排除过，或不是所选的）：请在账户识别里确认。`,
    })
  }
  for (const field of session.fields ?? []) {
    if (field.required && !isMapped(session.mapping, field.field) && !acknowledged.includes(field.field)) {
      problems.push({ key: `required:${field.field}`, kind: 'required', field: field.field, text: `缺少必需的列：${field.label}。` })
    }
  }
  return problems
}

/** Problems the person resolves through the backend (not acknowledgements): they send a reload back to step 2. */
export function structuralProblems(session: Parameters<typeof mappingProblems>[0]): MappingProblem[] {
  return mappingProblems(session, (session.fields ?? []).map((field) => field.field))
}

/**
 * Where a loaded session continues. A pending, still valid proposal resumes its review (step 4); a
 * session that already booked batches can show its summary there. Otherwise the URL's step, but never
 * past step 2 while something structural must be settled there.
 */
export function resumeStep(session: ImportSession, requested: StepId | null): StepId {
  const pending = session.batches?.pending
  if (pending && !pending.stale) return 4
  if (requested === 4 && (session.batches?.confirmed ?? 0) > 0) return 4
  if (structuralProblems(session).length > 0) return 2
  if (requested === 3 || requested === 4) return 3
  return 2
}

// ------------------------------------------------------------------------------------------------
// Proposing a batch

export interface ProposeState {
  allowed: boolean
  /** Why not, in Chinese (the backend's blocker when there is one). */
  reason: string | null
  /** The button: "生成入账预览（5 笔 · 第 1/1 批）". */
  label: string
  /** "共 2,300 笔，分 3 批…" when more than one batch remains. */
  detail: string | null
}

const thousands = (value: number) => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',')

/** Rows the file still has outside the ledger: 需处理, 不支持 and 与已入账记录不同 (each with its count). */
function leftOut(counts: Record<string, number>): string[] {
  return ([['review', '需处理'], ['unsupported', '不支持'], ['changed', '与已入账记录不同']] as const)
    .filter(([status]) => (counts[status] ?? 0) > 0).map(([status, label]) => `${counts[status]} 行${label}`)
}

/** 全部已导入: something is imported and nothing else is left (only skipped or merged rows besides). */
export function allImported(session: ImportSession): boolean {
  const counts = session.counts ?? {}
  return (counts.imported ?? 0) > 0 && (counts.new ?? 0) + (counts.possible_duplicate ?? 0) === 0 && leftOut(counts).length === 0
}

/** Nothing bookable is left: every row imported, excluded, skipped or waiting for review. */
export function nothingToBook(session: ImportSession): string | null {
  if (session.statement === 'holdings') return null
  if (session.batches?.next !== null && session.batches?.next !== undefined) return null
  const counts = session.counts ?? {}
  const bookable = (counts.new ?? 0) + (counts.possible_duplicate ?? 0)
  if (bookable === 0 && allImported(session)) return '全部已导入，没有需要入账的新记录。'
  if (bookable === 0 && leftOut(counts).length > 0) return `没有可以入账的行：${leftOut(counts).join('、')}。`
  return '没有需要入账的新记录：全部已导入、未勾选或需处理。'
}

export function proposeState(session: ImportSession): ProposeState {
  const batches = session.batches
  if (session.statement === 'holdings') {
    const opening = session.holdings?.mode === 'opening'
    const reason = session.blockers[0] ?? (opening ? null : '持仓文件只用于对账，不会生成交易。')
    return { allowed: !reason, reason, label: '生成期初入账预览', detail: null }
  }
  const nothing = nothingToBook(session)
  const next = batches?.next ?? null
  const total = batches?.total ?? 0
  const single = (batches?.remaining_batches ?? 0) <= 1
  const count = single ? `${thousands(batches?.remaining_events ?? 0)} 笔 · ` : ''
  const label = next ? `生成入账预览（${count}第 ${next}/${total} 批）` : '生成入账预览'
  const detail = !single
    ? `还有 ${thousands(batches.remaining_events)} 笔，分 ${batches.remaining_batches} 批，每批最多 ${thousands(batches.limit)} 笔；确认一批后再准备下一批。`
    : null
  const reason = session.blockers[0] ?? nothing
  return { allowed: !reason, reason, label, detail }
}

/** Events booked by this session's confirmed batches. */
export function confirmedEvents(session: ImportSession): number {
  return (session.batches?.history ?? []).filter((record) => record.state === 'confirmed').reduce((sum, record) => sum + (record.events ?? 0), 0)
}

// ------------------------------------------------------------------------------------------------
// Mapping

export interface ColumnOption {
  value: string
  label: string
}

/** Columns for a field's select: "第 3 列 · 证券代码（600519）". */
export function columnOptions(columns: ImportColumn[], sample?: ImportRow): ColumnOption[] {
  return columns.map((column) => {
    const value = sample?.cells?.[column.index]?.trim()
    const example = value ? `（${value.length > 16 ? `${value.slice(0, 16)}…` : value}）` : ''
    return { value: String(column.index), label: `第 ${column.index + 1} 列 · ${column.header || '（无表头）'}${example}` }
  })
}

/** A column select's value ('' for none) as a column index. */
export function columnIndex(value: string): number | null {
  return /^\d{1,4}$/.test(value) ? Number.parseInt(value, 10) : null
}

/** The mapping as "表头 → 字段" pairs, fee columns joined: "手续费 + 印花税 → 费用". */
export function mappedPairs(session: Pick<ImportSession, 'columns' | 'mapping' | 'fields'>): { key: string; from: string; to: string }[] {
  const labels = new Map(session.fields.map((field) => [field.field, field.label]))
  const header = (index: number) => session.columns.find((column) => column.index === index)?.header || `第 ${index + 1} 列`
  const pairs: { key: string; from: string; to: string }[] = []
  for (const field of session.fields) {
    const value = session.mapping[field.field]
    if (field.field === 'fees' || Array.isArray(value)) {
      const list = Array.isArray(value) ? value : []
      if (list.length) pairs.push({ key: field.field, from: list.map(header).join(' + '), to: '费用' })
    } else if (typeof value === 'number') {
      pairs.push({ key: field.field, from: header(value), to: labels.get(field.field) ?? field.field })
    }
  }
  return pairs
}

/**
 * The PATCH `mapping` for putting `column` (or nothing) on `field`. A column is used once: a field or the
 * fee list that held it gives it up in the same request (the backend refuses a column mapped twice).
 */
export function mappingChange(mapping: ImportMapping, field: string, column: number | null): Record<string, number | number[] | null> {
  const change: Record<string, number | number[] | null> = { [field]: column }
  if (column === null) return change
  for (const [other, value] of Object.entries(mapping)) {
    if (other === field) continue
    if (Array.isArray(value)) {
      if (value.includes(column)) change[other] = value.filter((index) => index !== column)
    } else if (value === column) {
      change[other] = null
    }
  }
  return change
}

/** The PATCH `mapping` for the fee columns. */
export function feesChange(mapping: ImportMapping, fees: number[]): Record<string, number | number[] | null> {
  const change: Record<string, number | number[] | null> = { fees: [...new Set(fees)].sort((a, b) => a - b) }
  for (const [other, value] of Object.entries(mapping)) {
    if (other !== 'fees' && typeof value === 'number' && fees.includes(value)) change[other] = null
  }
  return change
}

export const MAPPING_SOURCE_LABEL: Record<string, string> = {
  saved: '已套用上次为这个账户保存的列对应',
  preset: '按预设识别',
  synonyms: '按列名识别',
  edited: '已修改，会为这个账户和表头记住',
}

// ------------------------------------------------------------------------------------------------
// Account identities (资金账号 / 股东代码)

/** The values currently accepted, plus "" when rows without an account value are accepted. */
export function acceptedValues(accounts: ImportAccounts): string[] {
  const values = new Set<string>()
  for (const column of accounts.columns) for (const value of column.values) if (value.accepted) values.add(value.value)
  if (accounts.accept_rows_without_account) values.add('')
  return [...values]
}

/** The PATCH `account_filter` for a set of chosen values, in the file's order ("" last). */
export function accountFilter(accounts: ImportAccounts, chosen: Iterable<string>): string[] {
  const picked = new Set(chosen)
  const out: string[] = []
  for (const column of accounts.columns) for (const value of column.values) if (picked.has(value.value) && !out.includes(value.value)) out.push(value.value)
  if (picked.has('')) out.push('')
  return out
}

export const ACCOUNT_SOURCE_LABEL: Record<string, string> = {
  single: '文件里只有一个账户',
  dominant: '还没有确认：现在只按行数最多的账户预览',
  chosen: '你已确认',
  saved: '已套用上次的选择',
}

/**
 * The rows the account choice keeps in 需处理: how many (the backend's count over the whole file) and the
 * backend's reasons, grouped, from the loaded page of 需处理 rows. `complete`: the page held every one of them,
 * so the per-reason counts add up; otherwise the reasons are examples.
 */
export function accountReviewRows(
  accounts: Pick<ImportAccounts, 'rows_kept_out'>, rows: ImportRow[] | undefined,
): { count: number; reasons: { text: string; count: number }[]; complete: boolean } {
  const affected = (rows ?? []).filter((row) => row.status === 'review' && row.needs.includes('account'))
  const reasons = new Map<string, number>()
  for (const row of affected) {
    const text = row.messages[0] ?? '账户不在所选范围内'
    reasons.set(text, (reasons.get(text) ?? 0) + 1)
  }
  const count = accounts.rows_kept_out ?? 0
  return { count, reasons: [...reasons].map(([text, n]) => ({ text, count: n })), complete: affected.length >= count }
}
