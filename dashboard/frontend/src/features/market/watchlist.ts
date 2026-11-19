// The 自选 page's rules: the watchlist payload (backend/api/watchlist.py), the polling rhythm (Today's
// rule applied to the markets of the watched tickers), the change amount, reordering and the alert-rule
// count per listing (by the backend's `identity`). Pure TypeScript (relative `.ts` imports only) so node
// tests can load it.
import { identityOf, type Identified } from '../../lib/instrument.ts'
import { POLL_IN_SESSION_MS, POLL_OUT_OF_SESSION_MS, isMoving, marketOf } from '../today/session.ts'
import type { Market, QuoteObject, TodaySession } from '../today/types.ts'
import { subtract } from './decimal.ts'

export interface WatchlistItem {
  ticker: string
  /** The listing identity (core/market/search.py `identity_key`); absent from an older backend. */
  identity?: string | null
  name: string | null
  note: string | null
  /** 0-based position. */
  order: number
  source: string
  added_at: string
  updated_at: string
  in_holdings: boolean
  /** One element of `GET /api/quotes`; null when quotes were not asked for. */
  quote: QuoteObject | null
}

export interface WatchlistPayload {
  items: WatchlistItem[]
  count: number
  max_items: number
  as_of: string | null
  source: string | null
}

const INDEX_MARKET: Record<string, Market> = { HSI: 'HK', HSTECH: 'HK', IXIC: 'US', DJIA: 'US', SPX: 'US' }

/** The market whose session moves this ticker's price; indexes included (IDX:HSI → HK). */
export function tickerMarket(ticker: string | null | undefined): Market | null {
  const text = (ticker ?? '').trim().toUpperCase()
  if (text.startsWith('IDX:')) return INDEX_MARKET[text.slice(4)] ?? 'CN'
  return marketOf(text)
}

/**
 * How long to wait before the next watchlist poll: Today's rhythm (5 s while a market moves, 60 s
 * otherwise), applied to the markets of the watched tickers. `session` is `/api/today`'s, which
 * describes every market.
 */
export function watchPollMs(tickers: string[], session: Pick<TodaySession, 'markets'> | undefined): number {
  if (!session?.markets) return POLL_OUT_OF_SESSION_MS
  const markets = new Set(tickers.map(tickerMarket).filter((market): market is Market => market !== null))
  return [...markets].some((market) => isMoving(session.markets[market])) ? POLL_IN_SESSION_MS : POLL_OUT_OF_SESSION_MS
}

/** Whether the quote carries a price the page may show (anything but unavailable). */
export function hasPrice(quote: QuoteObject | null | undefined): quote is QuoteObject & { price: string } {
  return Boolean(quote && quote.price !== null && quote.quote.status !== 'unavailable')
}

/**
 * price − previous close, exactly; null when either is unknown. Both are the backend's decimal strings
 * (`plain(value, 6)`, finer than any provider quotes), so the difference is not built on rounded prices.
 */
export function changeAmount(quote: QuoteObject | null | undefined): string | null {
  if (!hasPrice(quote) || quote.prev_close === null) return null
  return subtract(quote.price, quote.prev_close)
}

/** The display name: the stored name, the quote's, else the ticker. */
export function displayName(item: Pick<WatchlistItem, 'ticker' | 'name' | 'quote'>): string {
  return item.name || item.quote?.name || item.ticker
}

/**
 * The list after moving `ticker` one place up (-1) or down (1), with every item's `order` set to its new
 * position (so a later undo puts an item back where it was on screen), and the moved item's new 0-based
 * position; null when it cannot move that way.
 */
export function moveItem<T extends { ticker: string; order?: number }>(items: T[], ticker: string, delta: -1 | 1): { items: T[]; order: number } | null {
  const from = items.findIndex((item) => item.ticker === ticker)
  const to = from + delta
  if (from < 0 || to < 0 || to >= items.length) return null
  const next = items.slice()
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return { items: renumber(next), order: to }
}

/** The list with `item` put back at position `order` (undo of a removal), orders renumbered. */
export function restoreItem<T extends { ticker: string; order?: number }>(items: T[], item: T, order: number): T[] {
  const next = items.filter((entry) => entry.ticker !== item.ticker)
  next.splice(Math.max(0, Math.min(order, next.length)), 0, item)
  return renumber(next)
}

function renumber<T extends { order?: number }>(items: T[]): T[] {
  return items.map((item, index) => ('order' in item && item.order !== index ? { ...item, order: index } : item))
}

export interface RuleLike extends Identified {
  ticker: string
  enabled: boolean
}

/** Alert rules per listing identity (the rule's `identity` from the backend): how many, how many enabled. */
export function ruleCounts(rules: RuleLike[] | undefined): Map<string, { total: number; enabled: number }> {
  const counts = new Map<string, { total: number; enabled: number }>()
  for (const rule of rules ?? []) {
    const key = identityOf(rule) ?? rule.ticker
    const entry = counts.get(key) ?? { total: 0, enabled: 0 }
    entry.total += 1
    if (rule.enabled) entry.enabled += 1
    counts.set(key, entry)
  }
  return counts
}

/** Enabled rules of one listing; null when the rules could not be read (shown 不可用, never 0). */
export function ruleCountFor(counts: Map<string, { total: number; enabled: number }> | null, item: Identified): number | null {
  if (!counts) return null
  return counts.get(identityOf(item) ?? '')?.enabled ?? 0
}
