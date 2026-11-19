// The 账本 tables without React: each event's cash effect, which events a reversal undid, where an event
// came from, the filters, and the one-line summary of each account. Pure TypeScript.
import { identityKey } from '../../lib/instrument.ts'
import { decAdd, decCmp, decMul, decNeg, decSub } from './decimal.ts'
import { fmtLong, fmtQty, longLine, type Shown } from '../../lib/format.ts'
import type { LedgerAccount, LedgerEvent, LedgerPosition } from './types.ts'

/** The change an event made to its account's cash, in that cash's currency (signed). */
export interface EventCash {
  amount: string | null
  currency: string | null
  /** 换汇: what came in, in the other currency. */
  received?: { amount: string; currency: string }
}

/**
 * The event's cash effect. `side` is the account it is seen from: a transfer takes cash from its account and
 * gives it to `to_account`, so seen from the receiving account it is positive.
 */
export function eventCash(event: LedgerEvent, side?: string | null): EventCash {
  const currency = event.currency ?? null
  const amount = event.amount ?? null
  switch (event.kind) {
    case 'buy':
    case 'sell': {
      if (!event.quantity || !event.price) return { amount: null, currency }
      const turnover = decMul(event.quantity, event.price)
      const fee = event.fee ?? '0'
      return { amount: event.kind === 'buy' ? decNeg(decAdd(turnover, fee)) : decSub(turnover, fee), currency }
    }
    case 'deposit':
    case 'dividend':
    case 'interest':
      return { amount, currency }
    case 'transfer':
      if (side && side === event.to_account && side !== event.account) return { amount, currency }
      return { amount: amount && decNeg(amount), currency }
    case 'withdrawal':
    case 'fee':
      return { amount: amount && decNeg(amount), currency }
    case 'fx':
      return {
        amount: amount && decNeg(amount), currency,
        received: event.received_amount && event.to_currency ? { amount: event.received_amount, currency: event.to_currency } : undefined,
      }
    case 'fund_buy':
      return { amount: amount && decNeg(decAdd(amount, event.fee ?? '0')), currency: 'CNY' }
    case 'fund_sell':
      return { amount: amount && decSub(amount, event.fee ?? '0'), currency: 'CNY' }
    default:
      return { amount: null, currency }
  }
}

/** Ids of the events a confirmed reversal undid. */
export function reversedIds(events: LedgerEvent[]): Set<string> {
  return new Set(events.filter((event) => event.kind === 'reverse' && event.target_id).map((event) => event.target_id!))
}

const SOURCE_LABEL: Record<string, string> = {
  manual: '手工', demo: '演示', 'legacy-opening': '旧持仓迁移', agent: '助手', 'agent:chat': '助手',
  'corporate-action': '公司行为', screenshot: '截图', today: '手工', alert: '手工', plan: '手工', assistant: '助手',
  'feishu-card': '飞书卡片',
}

/** "手工", "交割单导入", "CSV（broker-csv）": where an event came from. */
export function sourceLabel(source: string | undefined): string {
  if (!source) return '手工'
  if (SOURCE_LABEL[source]) return SOURCE_LABEL[source]
  if (source.startsWith('import:')) return '交割单导入'
  if (source.startsWith('agent')) return '助手'
  return `CSV（${source}）`
}

/**
 * The phone card's second line ("2026-11-17 · 长期账户 · 300 股 · 手工"): a quantity with more than 8 places is
 * shown rounded (≈) and the line keeps its full value (`full`, for the title and screen readers).
 */
export function cardLine(event: Pick<LedgerEvent, 'date' | 'account' | 'quantity' | 'source'>): Shown {
  return longLine((pick) => [
    event.date, event.account, event.quantity ? `${pick(fmtLong(event.quantity, fmtQty))} 股` : '', sourceLabel(event.source),
  ].filter(Boolean).join(' · '))
}

// The booking marks entry.ts (`provenanceMarks`) puts at the end of a note: an estimated fee, a price filled
// from a quote, an accepted reference FX rate.
const MARK = '〔(?:费用为估算|价格取自|汇率取自)[^〔〕]*〕'
const TRAILING_MARKS = new RegExp(`\\s*((?:${MARK})+)$`)

/** The note's booking marks, without brackets: ["费用为估算：按账户费率估算"]. */
export function noteMarks(note: string | undefined): string[] {
  const found = note ? TRAILING_MARKS.exec(note) : null
  return found ? [...found[1].matchAll(new RegExp(MARK, 'g'))].map((match) => match[0].slice(1, -1)) : []
}

/** Whether the fee of this event was the estimate, not a statement's figure. */
export function feeEstimated(event: Pick<LedgerEvent, 'note'>): boolean {
  return noteMarks(event.note).some((mark) => mark.startsWith('费用为估算'))
}

/** A note as shown: the person's words (booking marks apart), the legacy migration's English note in Chinese. */
export function noteText(note: string | undefined): string {
  if (!note) return ''
  const text = note.replace(TRAILING_MARKS, '').trim()
  const legacy = /^Opening balance from holdings dated (\d{4}-\d{2}-\d{2})$/.exec(text)
  return legacy ? `期初余额取自 ${legacy[1]} 的持仓文件` : text
}

// ------------------------------------------------------------------------------------------------
// Filters

export type DateRange = 'week' | 'month' | 'year' | 'all'

export interface EventFilter {
  account: string
  kind: string
  /** Text in the ticker or name. */
  query: string
  range: DateRange
}

export const ALL = ''

/** The first day of the range that ends `today` (YYYY-MM-DD); null for 全部. */
export function rangeStart(range: DateRange, today: string): string | null {
  if (range === 'all') return null
  if (range === 'year') return `${today.slice(0, 4)}-01-01`
  if (range === 'month') return `${today.slice(0, 7)}-01`
  const [y, m, d] = today.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  const monday = (date.getUTCDay() + 6) % 7
  date.setUTCDate(date.getUTCDate() - monday)
  return date.toISOString().slice(0, 10)
}

/**
 * The days of a preset (本周, 本月, 今年) up to and including `today` (the backend's, in Beijing time); null for
 * 全部. Events dated after today are only under 全部.
 */
export function rangeBounds(range: DateRange, today: string): { start: string; end: string } | null {
  const start = rangeStart(range, today)
  return start ? { start, end: today } : null
}

/**
 * The events that match. `today` is the backend's date (null while unknown: then no date preset applies). A
 * reversal matches an account or a ticker through the event it undoes (looked up among `events`).
 */
export function filterEvents(
  events: LedgerEvent[], filter: EventFilter, today: string | null, names: Record<string, string> = {},
): LedgerEvent[] {
  const bounds = today ? rangeBounds(filter.range, today) : null
  const query = filter.query.trim().toUpperCase()
  const byId = new Map(events.map((event) => [event.id, event]))
  const subject = (event: LedgerEvent) => (event.kind === 'reverse' && event.target_id ? byId.get(event.target_id) ?? event : event)
  return events.filter((event) => {
    const target = subject(event)
    if (filter.account && ![event, target].some((item) => item.account === filter.account || item.to_account === filter.account)) return false
    if (filter.kind && event.kind !== filter.kind) return false
    if (bounds && (event.date < bounds.start || event.date > bounds.end)) return false
    if (query) {
      const name = target.name ?? (target.ticker ? names[target.ticker] : undefined) ?? ''
      if (!`${target.ticker ?? ''} ${name}`.toUpperCase().includes(query)) return false
    }
    return true
  })
}

/** Newest (desc) or oldest (asc) first, by date then order of confirmation; sort before showing a page. */
export function sortEvents(events: LedgerEvent[], direction: 'asc' | 'desc'): LedgerEvent[] {
  const factor = direction === 'asc' ? 1 : -1
  return [...events].sort((a, b) => factor * (a.date.localeCompare(b.date) || (a._sequence ?? 0) - (b._sequence ?? 0)))
}

/** Names of the tickers in the ledger, from the positions held and the events that carried one. */
export function tickerNames(events: LedgerEvent[], accounts: Record<string, LedgerAccount> = {}): Record<string, string> {
  const names: Record<string, string> = {}
  for (const event of events) {
    if (event.ticker && event.name) names[event.ticker] = event.name
    for (const position of event.positions ?? []) if (position.name) names[position.ticker] = position.name
  }
  for (const account of Object.values(accounts)) {
    for (const position of Object.values(account.positions)) {
      if (position.name && position.name !== position.ticker) names[position.ticker] = position.name
    }
  }
  return names
}

// ------------------------------------------------------------------------------------------------
// Account lines

const CURRENCY_ORDER = ['CNY', 'HKD', 'USD']

/** The account's cash balances in CNY, HKD, USD order; zero foreign balances are left out. */
export function cashList(account: LedgerAccount): [string, string][] {
  const rank = (unit: string) => (CURRENCY_ORDER.includes(unit) ? CURRENCY_ORDER.indexOf(unit) : CURRENCY_ORDER.length)
  return Object.entries(account.cash_balances)
    .filter(([unit, value]) => unit === 'CNY' || !/^-?0*(\.0*)?$/.test(value))
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
}

/** Accounts with no open position whose realised profit is not zero, with it per currency (CNY first). */
export function closedAccounts(accounts: Record<string, LedgerAccount>): [string, [string, string][]][] {
  const rank = (unit: string) => (CURRENCY_ORDER.includes(unit) ? CURRENCY_ORDER.indexOf(unit) : CURRENCY_ORDER.length)
  return Object.entries(accounts)
    .filter(([, account]) => Object.keys(account.positions).length === 0)
    .map(([name, account]): [string, [string, string][]] => [name, Object.entries(account.realized ?? {})
      .filter(([, value]) => !/^-?0*(\.0*)?$/.test(value))
      .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))])
    .filter(([, realized]) => realized.length > 0)
}

/**
 * The account's position in `ticker`'s listing, whatever its spelling (`HKG:00700` finds `HKG:700`, as the
 * ledger books it); null when none.
 */
export function heldPosition(account: LedgerAccount | undefined, ticker: string | null): LedgerPosition | null {
  if (!account || !ticker) return null
  const exact = account.positions[ticker]
  if (exact) return exact
  // Of two spellings held (a ledger booked before 4.0), the larger position: where the ledger books a new entry.
  const identity = identityKey(ticker)
  let found: LedgerPosition | null = null
  for (const [held, position] of Object.entries(account.positions)) {
    if (identityKey(held) === identity && (!found || decCmp(position.quantity, found.quantity) === 1)) found = position
  }
  return found
}

/** Shares held of `ticker` in the account (any spelling of its listing); null when none. */
export function heldQuantity(account: LedgerAccount | undefined, ticker: string | null): string | null {
  return heldPosition(account, ticker)?.quantity ?? null
}
