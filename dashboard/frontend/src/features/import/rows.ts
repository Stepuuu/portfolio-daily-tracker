// The preview table without React: the status filters with the backend's counts, and each row as it will be
// booked (its ledger events) or, for a row that books nothing, as the file states it, with the backend's
// reasons. Numbers are formatted from the backend's decimal strings, never recomputed except the display-only
// cash effect of a trade (features/ledger/events.ts); more than 8 places (a price worked out as 成交金额 ÷ 数量)
// is ≈ rounded to 8 with its full value kept, and such a price says so. Pure TypeScript.
import { fmtLong, fmtMoney, fmtNumber, fmtPrice, fmtQty, longLine, type NumericInput, type Shown } from '../../lib/format.ts'
import { eventCash, sourceLabel } from '../ledger/events.ts'
import { kindLabel } from '../ledger/kinds.ts'
import { eventLine } from '../ledger/review.ts'
import type { LedgerEvent } from '../ledger/types.ts'
import { canEdit, canReset } from './edits.ts'
import type { ImportRow, ImportSession, RowsView, Statement } from './types.ts'

/** Rows per page of the table: thousands of rows are paged by the backend, never all read or rendered. */
export const PAGE_SIZE = 50

/** What an upload asks for: the counts plus the first page of every row. */
export const FIRST_PAGE: RowsView = { status: null, offset: 0, limit: PAGE_SIZE }

/** The first page of 需处理 rows: on 核对列, the backend's reasons for the rows the account choice keeps out. */
export const REVIEW_SAMPLE: RowsView = { status: 'review', offset: 0, limit: PAGE_SIZE }

/** The filters shown even at zero, in this order; any other status the backend reports follows when present. */
const FILTER_ORDER = ['new', 'imported', 'possible_duplicate', 'changed', 'review', 'skipped']
/** Shorter tab names where the backend's label is a sentence. */
const SHORT_LABEL: Record<string, string> = { changed: '有变化' }

export interface StatusTab {
  /** 'all', or a row status. */
  id: string
  label: string
  count: number
}

export function statusLabel(status: string, labels: Record<string, string> | undefined): string {
  return SHORT_LABEL[status] ?? labels?.[status] ?? status
}

export function statusTabs(session: Pick<ImportSession, 'counts' | 'status_labels'>): StatusTab[] {
  const counts = session.counts ?? {}
  const labels = session.status_labels ?? {}
  const tabs: StatusTab[] = [{ id: 'all', label: '全部', count: counts.total ?? 0 }]
  const known = Object.keys(labels)
  for (const status of FILTER_ORDER) {
    if (known.includes(status) || status in counts) tabs.push({ id: status, label: statusLabel(status, labels), count: counts[status] ?? 0 })
  }
  for (const status of known) {
    if (!FILTER_ORDER.includes(status) && (counts[status] ?? 0) > 0) {
      tabs.push({ id: status, label: statusLabel(status, labels), count: counts[status] })
    }
  }
  return tabs
}

export type StatusTone = 'accent' | 'warn' | 'neutral'

/** 新 in accent; what needs a look (可能重复, 有变化, 需处理) in amber; the rest neutral. */
export function statusTone(status: string): StatusTone {
  if (status === 'new') return 'accent'
  if (status === 'possible_duplicate' || status === 'changed' || status === 'review') return 'warn'
  return 'neutral'
}

// ------------------------------------------------------------------------------------------------
// One row as cells

const NONE: Shown = { text: '' }

export interface RowLine {
  key: string
  date: string
  kind: string
  /** Name of the security, or a description (現金, "CNY → HKD"). */
  name: string
  /** Ledger ticker, or the file's code while none is chosen. */
  code: string
  /**
   * The figures, `text` '' where the line has none. Every digit up to 8 places; a longer one (the importer's
   * derived price) is ≈ rounded to 8 with its full value in `full` (lib/format.ts `fmtLong`).
   */
  quantity: Shown
  price: Shown
  /** "成交金额 ÷ 数量" under a price the backend worked out because the file's was rounded or missing. */
  priceNote: string | null
  fee: Shown
  /** "|发生金额| − 成交金额" under a fee the backend worked out from the cash amount. */
  feeNote: string | null
  /** Signed cash effect in its currency ("-168,043.68", "-HK$76,096.03"); text '' when the row moves no cash. */
  amount: Shown
}

export const DERIVED_PRICE = '成交金额 ÷ 数量'
export const DERIVED_FEE = '|发生金额| − 成交金额'

/**
 * CNY amounts plain, other currencies with their symbol (a column mixes currencies). A backend figure keeps
 * every digit (`exact`, through `fmtLong`); a trade's cash effect, worked out here for display, is shown to the cent.
 */
function cash(amount: string | null | undefined, currency: string | null | undefined, signed = false, exact = true): Shown {
  if (amount === null || amount === undefined || amount === '') return NONE
  const format = (value: NumericInput) => (!currency || currency === 'CNY'
    ? fmtNumber(value, { signed, exact }) : fmtMoney(value, currency, { signed, exact }))
  return exact ? fmtLong(amount, format) : { text: format(amount) }
}

function price(value: string | null | undefined, currency: string | null | undefined): Shown {
  if (value === null || value === undefined || value === '') return NONE
  const unit = currency ?? 'CNY'
  return fmtLong(value, (v) => fmtPrice(v, unit, 'stock', { symbol: unit !== 'CNY', exact: true }))
}

function quantity(value: string | null | undefined): Shown {
  return value === null || value === undefined || value === '' ? NONE : fmtLong(value, fmtQty)
}

function eventCells(event: LedgerEvent, row: ImportRow, index: number): RowLine {
  const effect = eventCash(event)
  const trade = event.kind === 'buy' || event.kind === 'sell'
  const main = cash(effect.amount, effect.currency, true, !trade)
  const received = effect.received ? cash(effect.received.amount, effect.received.currency, true) : null
  const amount = received ? longLine((pick) => `${pick(main)} → ${pick(received)}`) : main
  let name = event.name ?? (event.ticker && event.ticker === row.ticker ? row.name : '')
  if (event.kind === 'fx') name = `${event.currency ?? ''} → ${event.to_currency ?? ''}`
  else if (!event.ticker && !name) name = '现金'
  const ratio = quantity(event.ratio)
  return {
    key: `${row.row}:${event.id ?? index}`,
    date: event.date ?? '',
    kind: kindLabel(event.kind),
    name,
    code: event.ticker ?? '',
    quantity: event.kind === 'split' ? longLine((pick) => `×${pick(ratio)}`) : quantity(event.quantity),
    price: trade ? price(event.price, event.currency) : NONE,
    priceNote: trade && row.price_source === 'derived' ? DERIVED_PRICE : null,
    fee: trade && event.fee !== undefined ? cash(event.fee, event.currency) : NONE,
    feeNote: trade && event.fee !== undefined && row.fee_source === 'derived' ? DERIVED_FEE : null,
    amount,
  }
}

/**
 * The kind a row states when it books nothing: 持仓 for a holdings line, the template's ledger kind, the
 * file's own words for an unclassified row, else the business type (with the file's words when they differ).
 */
export function statedKind(row: ImportRow, statement: Statement): string {
  if (statement === 'holdings') return '持仓'
  if (statement === 'template') return row.action ? kindLabel(row.action) : row.category_label
  // Not classified (unknown business, or a row set aside before it was read, e.g. another account): the file's words.
  if (row.category === 'unknown' && row.action) return row.action
  const label = row.category_label || row.action
  if (row.action && label && !row.action.includes(label) && !label.includes(row.action)) return `${label}（${row.action}）`
  return label
}

/**
 * The row's cells: one line per ledger event it will book (a 港股通 trade is 换汇 + the HKD trade),
 * otherwise one line of what the file states.
 */
export function rowLines(row: ImportRow, statement: Statement): RowLine[] {
  if (row.events?.length) return row.events.map((event, index) => eventCells(event, row, index))
  const holdings = statement === 'holdings'
  return [{
    key: `${row.row}:file`,
    date: row.date ?? '',
    kind: statedKind(row, statement),
    name: row.name,
    code: row.ticker ?? row.code ?? '',
    quantity: quantity(row.quantity),
    price: price(row.price, row.currency),
    priceNote: row.price && row.price_source === 'derived' ? DERIVED_PRICE : null,
    fee: holdings ? NONE : cash(row.fee, row.currency),
    feeNote: !holdings && row.fee && row.fee_source === 'derived' ? DERIVED_FEE : null,
    amount: holdings ? NONE : cash(row.cash_change, row.currency, true),
  }]
}

/** The figure columns of a line. */
export type FigureField = 'quantity' | 'price' | 'fee' | 'amount'

/** Table columns a need points at, so the cell can say 待填写. */
export const NEED_COLUMN: Record<string, keyof RowLine> = {
  date: 'date', kind: 'kind', ticker: 'code', quantity: 'quantity', price: 'price', fee: 'fee', amount: 'amount',
}

/** Columns of the row the backend needs filled in (only for rows that book nothing yet). */
export function neededColumns(row: ImportRow): Set<string> {
  if (row.events?.length) return new Set()
  return new Set(row.needs.map((need) => NEED_COLUMN[need]).filter(Boolean) as string[])
}

export interface RowExplanation {
  /** Why the row has its status (the backend's words). */
  reasons: string[]
  /** What was inferred. */
  notes: string[]
  /**
   * The ledger event it may repeat or differs from, as booked, with where it came from:
   * "已有记录（手工）：2026-11-13 买入 招商银行 SHA:600036 500 股 @ 32.10 · 费用 5.00 · 示例账户".
   */
  reference: Shown | null
  /** A 需处理 row the page cannot fix (`needs` is empty): the backend's sentence saying so and what to do instead. */
  unfixable: string | null
}

/** The backend's words for a row that cannot be fixed on this page (core/importers/transactions.py NOT_HERE). */
export const NOT_HERE = '这一行无法在这里修改'
const NOT_HERE_FALLBACK = `${NOT_HERE}：请核对文件后重新导入，或把它设为跳过。`

export function rowExplanation(row: ImportRow): RowExplanation {
  const duplicate = row.duplicate_of
  const other = duplicate ?? row.existing
  let reference: Shown | null = null
  if (other && other.kind && other.date) {
    const source = duplicate?.source_label || sourceLabel(other.source)
    const added = duplicate?.added_quantity ? fmtLong(duplicate.added_quantity, fmtQty) : null
    reference = longLine((pick) => `${duplicate ? '已有记录' : '已入账记录'}（${source}）：`
      + `${eventLine(other as LedgerEvent, undefined, undefined, pick)}${added ? ` · 到账 ${pick(added)} 股` : ''}`)
  }
  const reasons = row.messages ?? []
  const unfixable = row.status === 'review' && !(row.needs ?? []).length
    ? reasons.find((reason) => reason.includes(NOT_HERE)) ?? NOT_HERE_FALLBACK
    : null
  return { reasons: unfixable ? reasons.filter((reason) => reason !== unfixable) : reasons, notes: row.notes ?? [], reference, unfixable }
}

/**
 * What a row offers: 处理 (the form) only for a 需处理 row whose `needs` the page can show; 跳过 for every 需处理
 * row (the only way out for one that cannot be fixed here); 撤销修改 while an edit still decides something.
 */
export function rowActions(row: Pick<ImportRow, 'status' | 'needs' | 'edits'>): { edit: boolean; skip: boolean; reset: boolean } {
  const review = canEdit(row)
  return { edit: review && (row.needs ?? []).length > 0, skip: review, reset: canReset(row) }
}

/** "第 1–50 行，共 214 行" for the pager; rows are the filtered set. */
export function pageLabel(offset: number, shown: number, total: number): string {
  if (total === 0) return '没有行'
  return `第 ${offset + 1}–${offset + shown} 行，共 ${total} 行`
}

export function pageCount(total: number, size = PAGE_SIZE): number {
  return Math.max(1, Math.ceil(total / size))
}

/** The skipped-row summary: "撤单 / 废单 1 · 北交所证券…（不支持）1 · 逆回购 2 行合并为利息 4.89". */
export function skippedLine(session: Pick<ImportSession, 'skipped' | 'status_labels'>): { key: string; text: string }[] {
  return (session.skipped ?? []).map((group, index) => {
    const status = group.status === 'skipped' || group.status === 'merged' ? '' : `（${statusLabel(group.status, session.status_labels)}）`
    const count = group.status === 'merged' ? '' : ` ${group.count} 行`
    return { key: `${group.status}:${index}`, text: `${group.label}${status}${count}` }
  })
}
