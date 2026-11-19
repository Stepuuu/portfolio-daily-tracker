// The `GET /api/today` and `GET /api/quotes` payloads. The authoritative contract is the docstring at the
// top of dashboard/core/market/today.py: money and quantities are decimal strings, null means unknown
// (never zero), times are ISO-8601 in Asia/Shanghai with an offset.

export type QuoteStatus = 'live' | 'delayed' | 'closed' | 'stale' | 'prev_close' | 'time_unknown' | 'unavailable'
export type Market = 'CN' | 'HK' | 'US'
/** CN: pre_open, call_auction, morning, lunch_break, afternoon, closed. HK and US: open, closed. */
export type Phase = 'pre_open' | 'call_auction' | 'morning' | 'lunch_break' | 'afternoon' | 'open' | 'closed'
export type Quality = 'complete' | 'estimated' | null

export interface Quote {
  source: string | null
  quote_time: string | null
  fetched_at: string | null
  status: QuoteStatus
  reason: string | null
  session_date: string | null
}

export interface TodayRow {
  ticker: string
  name: string
  currency: string
  quantity: string | null
  cost_price: string | null
  price: string | null
  prev_close: string | null
  change_pct: string | null
  market_value: string | null
  market_value_cny: string | null
  day_pnl: string | null
  day_pnl_cny: string | null
  total_pnl: string | null
  total_pnl_cny: string | null
  total_pnl_pct: string | null
  weight_pct: string | null
  /** The same weight cut at 20 places, what the alert monitor compares with a cap; absent from an older backend. */
  weight_pct_exact?: string | null
  quote: Quote
  estimated: boolean
  estimated_reason: string | null
}

/** One non-verified input behind a figure. */
export interface EstimatedEntry {
  ticker?: string
  currency?: string
  status: string
  reason: string
}

/** One missing input; `field: 'day_pnl'` affects day profit only. */
export interface UnavailableEntry {
  ticker?: string
  currency?: string
  reason: string
  field?: string
}

export interface TodayAccount {
  name: string
  positions: TodayRow[]
  /** Rows sold out in their session (quantity 0); their day profit is in the totals. */
  closed_today: TodayRow[]
  cash: Record<string, string | null>
  cash_cny: string | null
  fund_cny: string | null
  market_value_cny: string | null
  total_cny: string | null
  day_pnl_cny: string | null
  total_pnl_cny: string | null
  quality: Quality
  estimated: EstimatedEntry[]
}

export interface TodayTotals {
  by_currency: Record<string, { market_value: string | null; cash: string | null }>
  total_cny: string | null
  day_pnl_cny: string | null
  day_pnl_pct: string | null
  total_pnl_cny: string | null
  total_pnl_pct: string | null
  total_pnl_kind: string
  contributed_cny: string | null
  cumulative_pnl_cny: string | null
  cumulative_pnl_pct: string | null
  day_pnl_excludes: string[]
  unavailable: UnavailableEntry[]
  quality: Quality
  estimated: EstimatedEntry[]
}

export interface MarketSession {
  phase: Phase
  trading_day: string
  is_trading_day: boolean
  calendar_confidence: string
  next_open: string | null
}

export interface TodaySession extends MarketSession {
  market: Market
  markets: Partial<Record<Market, MarketSession>>
}

export interface FxEntry {
  rate: string | null
  source: string | null
  quote_time: string | null
  status: QuoteStatus
  reason: string | null
}

export interface TodayNotice {
  kind: string
  message: string
  [key: string]: unknown
}

export interface TodayPayload {
  as_of: string
  source: string
  holdings_source: string
  /** 0 without a ledger; null when no consistent read was possible. */
  ledger_revision: number | null
  session: TodaySession
  fx: Record<string, FxEntry>
  accounts: TodayAccount[]
  totals: TodayTotals
  notices: TodayNotice[]
  /** Present only when `accounts` is empty: why there is nothing to show. */
  notice?: string
}

export interface QuoteObject {
  ticker: string
  name: string | null
  price: string | null
  prev_close: string | null
  change_pct: string | null
  currency: string | null
  quote: Quote
  estimated: boolean
  estimated_reason: string | null
}

export interface QuotesPayload {
  as_of: string
  source: string
  quotes: QuoteObject[]
}
