// Where the 记一笔 form starts: the account and kind used last (remembered in this browser), the date
// (for a trade the latest trading session the backend's calendar has verified for its market; for cash
// today by the backend's clock; no date at all when the backend cannot say), and the currency of cash
// entries. What the dialog was opened with (a holdings row's 卖, an alert, a plan) always wins. Pure
// TypeScript: storage and the calendar (`GET /api/ledger/calendar`) are passed in, so node tests drive them.
import { isEntryKind, type EntryKind } from '../features/ledger/kinds.ts'

export const LAST_ACCOUNT_KEY = 'pdt.entry.account'
export const LAST_KIND_KEY = 'pdt.entry.kind'
export const LAST_CURRENCY_KEY = 'pdt.entry.currency'

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/** The browser's localStorage, or null where it is unavailable (private mode, node). */
export function browserStorage(): StorageLike | null {
  try {
    return globalThis.localStorage ?? null
  } catch {
    return null
  }
}

function read(storage: StorageLike | null, key: string): string | null {
  try {
    return storage?.getItem(key) ?? null
  } catch {
    return null
  }
}

/** Remember what was just booked, for the next entry. Storage errors are ignored. */
export function rememberEntry(storage: StorageLike | null, entry: { account?: string; kind?: string; currency?: string }): void {
  const pairs: [string, string | undefined][] = [
    [LAST_ACCOUNT_KEY, entry.account], [LAST_KIND_KEY, entry.kind], [LAST_CURRENCY_KEY, entry.currency],
  ]
  for (const [key, value] of pairs) {
    if (!value) continue
    try {
      storage?.setItem(key, value)
    } catch {
      // Full or blocked storage only loses the convenience.
    }
  }
}

/** The account to start with: the one asked for, else the last used, else the first (often the only). */
export function defaultAccount(accounts: string[], storage: StorageLike | null, requested?: string | null): string {
  if (requested && accounts.includes(requested)) return requested
  const last = read(storage, LAST_ACCOUNT_KEY)
  if (last && accounts.includes(last)) return last
  return accounts[0] ?? ''
}

/** The kind to start with: the one asked for, else the last used, else 买入; 期初余额 when there is no account. */
export function defaultKind(hasAccounts: boolean, storage: StorageLike | null, requested?: string | null): EntryKind {
  if (!hasAccounts) return 'opening'
  if (isEntryKind(requested)) return requested
  const last = read(storage, LAST_KIND_KEY)
  // A new account is a one-off: never start there just because it was the last entry.
  if (isEntryKind(last) && last !== 'opening') return last
  return 'buy'
}

/** The currency of a cash entry to start with: the last used, else CNY. */
export function defaultCurrency(storage: StorageLike | null): 'CNY' | 'HKD' | 'USD' {
  const last = read(storage, LAST_CURRENCY_KEY)
  return last === 'HKD' || last === 'USD' ? last : 'CNY'
}

/** Today's date in a time zone (YYYY-MM-DD); Beijing time by default, as the ledger counts days. */
export function zonedToday(now: Date = new Date(), timeZone = 'Asia/Shanghai'): string {
  const parts: Record<string, string> = {}
  for (const part of new Intl.DateTimeFormat('en-GB', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now)) {
    parts[part.type] = part.value
  }
  return `${parts.year}-${parts.month}-${parts.day}`
}

export type CalendarMarket = 'CN' | 'HK' | 'US'

/** `GET /api/ledger/calendar`: today in Beijing time and each market's latest verified session (null when unverified). */
export interface LedgerCalendar {
  today: string | null
  markets: Partial<Record<CalendarMarket, { session: string | null; verified: boolean }>>
}

const EXCHANGE_MARKET: Record<string, CalendarMarket> = { SHA: 'CN', SHE: 'CN', HKG: 'HK', NASDAQ: 'US', NYSE: 'US' }

/** The calendar a ledger ticker trades by: SHA/SHE → CN, HKG → HK, NASDAQ/NYSE → US; null when unknown. */
export function marketOfTicker(ticker: string | null | undefined): CalendarMarket | null {
  return EXCHANGE_MARKET[(ticker ?? '').split(':', 1)[0].trim().toUpperCase()] ?? null
}

export interface DefaultDate {
  /** '' when there is no honest default: the date must be picked. */
  date: string
  /** Why it is not today, or why there is no date. */
  reason: string | null
}

const DATE = /^\d{4}-\d{2}-\d{2}$/

/**
 * The date to start with: the one asked for; else, for a trade, the latest session of its market (the
 * mainland's before a ticker is chosen) that the backend's calendar has verified; for anything else the
 * backend's today. Without that, no date: nothing is guessed from the browser's clock.
 */
export function defaultDate(
  calendar: LedgerCalendar | null | undefined, options: { trade: boolean; market?: CalendarMarket | null; requested?: string | null },
): DefaultDate {
  if (options.requested && DATE.test(options.requested)) return { date: options.requested, reason: null }
  const today = calendar?.today && DATE.test(calendar.today) ? calendar.today : null
  if (options.trade) {
    const session = today ? calendar?.markets[options.market ?? 'CN']?.session : null
    if (!session || !DATE.test(session) || session > today!) return { date: '', reason: '交易日历暂不可用，请选择成交日期' }
    return { date: session, reason: session === today ? null : `默认最近一个交易日 ${session}` }
  }
  return today ? { date: today, reason: null } : { date: '', reason: '暂时无法确认今天的日期，请选择日期' }
}

/** "今天" only for the backend's today; null otherwise (and while today is not known). */
export function dateLabel(date: string, calendar: LedgerCalendar | null | undefined): string | null {
  return calendar?.today && date === calendar.today ? '今天' : null
}
