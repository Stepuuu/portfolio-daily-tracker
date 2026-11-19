// The holdings table's rows, built from `/api/today` accounts: open positions, positions sold out in
// their session (`closed_today`, which must be shown or the day total would not match the rows), then
// cash per currency and the fund total. Nothing is summed here; group figures come from the backend.
import { UNAVAILABLE, fmtLong, fmtPct, fmtPrice, type Shown } from '../../lib/format.ts'
import { missingSummary, isZeroAmount } from './totals.ts'
import type { Quality, TodayAccount, TodayPayload, TodayRow } from './types.ts'

export type RowKind = 'position' | 'closed' | 'cash' | 'fund'

export interface HoldingRow {
  key: string
  account: string
  kind: RowKind
  /** The ledger ticker for position and closed rows. */
  ticker: string | null
  name: string
  currency: string
  /** The backend row, for position and closed rows. */
  position: TodayRow | null
  /** Native amount of a cash row, CNY amount of the fund row. */
  amount: string | null
}

export interface AccountSummary {
  name: string
  /** Open positions (sold-out rows not counted). */
  holdings: number
  closedToday: number
  totalCny: string | null
  dayPnlCny: string | null
  totalPnlCny: string | null
  quality: Quality
  /** "1 项缺价" when the account's figures are unknown. */
  missing: string | null
}

const CURRENCY_ORDER = ['CNY', 'HKD', 'USD']

/**
 * 'etf' for mainland exchange-traded funds (SHA:5xxxxx, SHE:15xxxx/16xxxx), whose prices have three
 * decimals; 'stock' otherwise.
 */
export function instrumentKind(ticker: string | null | undefined): 'stock' | 'etf' {
  const match = /^(SHA|SHE):(\d{6})$/.exec((ticker ?? '').trim().toUpperCase())
  if (!match) return 'stock'
  const [, exchange, code] = match
  return (exchange === 'SHA' && code.startsWith('5')) || (exchange === 'SHE' && /^1[56]/.test(code)) ? 'etf' : 'stock'
}

function cashRows(account: TodayAccount): HoldingRow[] {
  const rank = (currency: string) => (CURRENCY_ORDER.includes(currency) ? CURRENCY_ORDER.indexOf(currency) : CURRENCY_ORDER.length)
  return Object.entries(account.cash ?? {})
    // A zero foreign balance is noise; the CNY balance is always shown.
    .filter(([currency, amount]) => currency === 'CNY' || !isZeroAmount(amount))
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
    .map(([currency, amount]) => ({
      key: `${account.name}\u0000cash\u0000${currency}`, account: account.name, kind: 'cash' as const, ticker: null,
      name: `现金 ${currency}`, currency, position: null, amount,
    }))
}

/** Every row of every account, in the order the table shows them. */
export function holdingRows(payload: Pick<TodayPayload, 'accounts'> | undefined): HoldingRow[] {
  const rows: HoldingRow[] = []
  for (const account of payload?.accounts ?? []) {
    const position = (row: TodayRow, kind: 'position' | 'closed'): HoldingRow => ({
      key: `${account.name}\u0000${kind}\u0000${row.ticker}`, account: account.name, kind, ticker: row.ticker,
      name: row.name || row.ticker, currency: row.currency, position: row, amount: null,
    })
    rows.push(...account.positions.map((row) => position(row, 'position')))
    rows.push(...(account.closed_today ?? []).map((row) => position(row, 'closed')))
    rows.push(...cashRows(account))
    if (account.fund_cny !== null && !isZeroAmount(account.fund_cny)) {
      rows.push({
        key: `${account.name}\u0000fund`, account: account.name, kind: 'fund', ticker: null, name: '基金（合计）',
        currency: 'CNY', position: null, amount: account.fund_cny,
      })
    }
  }
  return rows
}

/** Per-account figures for the group rows, as the backend sent them. */
export function accountSummaries(payload: Pick<TodayPayload, 'accounts' | 'totals'> | undefined): Map<string, AccountSummary> {
  const summaries = new Map<string, AccountSummary>()
  for (const account of payload?.accounts ?? []) {
    const tickers = new Set([...account.positions, ...account.closed_today].map((row) => row.ticker))
    const unavailable = (payload?.totals.unavailable ?? []).filter((entry) => !entry.ticker || tickers.has(entry.ticker))
    summaries.set(account.name, {
      name: account.name,
      holdings: account.positions.length,
      closedToday: account.closed_today?.length ?? 0,
      totalCny: account.total_cny,
      dayPnlCny: account.day_pnl_cny,
      totalPnlCny: account.total_pnl_cny,
      quality: account.quality,
      missing: account.total_cny === null ? missingSummary(unavailable.filter((entry) => !entry.currency || cashOrHeld(account, entry.currency))) : null,
    })
  }
  return summaries
}

function cashOrHeld(account: TodayAccount, currency: string): boolean {
  if (currency in (account.cash ?? {}) && !isZeroAmount(account.cash[currency])) return true
  return account.positions.some((row) => row.currency === currency)
}

/** Account names in the order the backend lists them. */
export function accountNames(payload: Pick<TodayPayload, 'accounts'> | undefined): string[] {
  return (payload?.accounts ?? []).map((account) => account.name)
}

export type Move = 'up' | 'down'

/** Last seen price per row key, to flash the cells whose price changed. */
export function priceSnapshot(rows: HoldingRow[]): Map<string, string> {
  const prices = new Map<string, string>()
  for (const row of rows) if (row.position?.price) prices.set(row.key, row.position.price)
  return prices
}

function compareDecimal(a: string, b: string): number {
  // exact-ok: only the direction of a price move (for the flash), never shown; prices are short decimals.
  const x = Number(a)
  // exact-ok: as above.
  const y = Number(b)
  return Number.isFinite(x) && Number.isFinite(y) ? Math.sign(x - y) : 0
}

/** Rows whose price went up or down since `previous` (rows new to the table do not flash). */
export function priceMoves(previous: Map<string, string>, rows: HoldingRow[]): Map<string, Move> {
  const moves = new Map<string, Move>()
  for (const row of rows) {
    const before = previous.get(row.key)
    const after = row.position?.price
    if (!before || !after || before === after) continue
    const direction = compareDecimal(after, before)
    if (direction) moves.set(row.key, direction > 0 ? 'up' : 'down')
  }
  return moves
}

/** The 占比 cell: the backend's `weight_pct` with all its digits ("12.34%"), 不可用 when unknown. */
export function weightText(row: Pick<TodayRow, 'weight_pct'>): string {
  return row.weight_pct === null ? UNAVAILABLE : fmtPct(row.weight_pct, { exact: true })
}

/**
 * The title of a weight's 估算 mark, or null when the weight is verified (or unknown). A weight is of the
 * whole portfolio, so it is an estimate when its own row is (a stale quote or rate) and whenever the
 * portfolio total is (`totals.quality` anything but `complete`: any holding's estimate), as the stock
 * drawer has it (features/market/drawer.ts `positionView`).
 */
export function weightEstimate(
  row: Pick<TodayRow, 'weight_pct' | 'estimated' | 'estimated_reason'>, totalsQuality: Quality | undefined,
  totalsReason: string | null = null,
): string | null {
  if (row.weight_pct === null || (!row.estimated && totalsQuality === 'complete')) return null
  return `估算：${(row.estimated ? row.estimated_reason : null) ?? totalsReason ?? '组合中有未经核实的价格或汇率'}`
}

/** The first non-verified input behind estimated totals, as the 估算 title names it ("SHA:600519：…"). */
export function totalsEstimateReason(payload: Pick<TodayPayload, 'totals'> | undefined): string | null {
  const estimate = payload?.totals.estimated?.[0]
  return estimate ? `${estimate.ticker ?? (estimate.currency ? `汇率 ${estimate.currency}` : '部分持仓')}：${estimate.reason}` : null
}

/**
 * Whether an account line says its weights are estimates although its own figures are verified: the
 * portfolio total behind them is not (an account that is itself estimated already says 估算).
 */
export function accountWeightsEstimated(summary: Pick<AccountSummary, 'quality'>, totalsQuality: Quality | undefined): boolean {
  return summary.quality === 'complete' && totalsQuality !== undefined && totalsQuality !== null && totalsQuality !== 'complete'
}

/**
 * The 成本 cell of an open position: the average cost in the instrument's own currency, each with its
 * symbol (¥, HK$, $: the column mixes currencies), with every digit the backend sent and at least the
 * market's (more than 8 places, as an average that repeats: ≈ rounded to 8, the full value in `full`);
 * 不可用 when unknown. Null for rows without a cost of their own (sold out today, cash, funds).
 */
export function costFigure(row: HoldingRow): Shown | null {
  if (row.kind !== 'position' || !row.position) return null
  const cost = row.position.cost_price
  if (cost === null || cost === undefined) return { text: UNAVAILABLE }
  return fmtLong(cost, (value) => fmtPrice(value, row.currency, instrumentKind(row.ticker), { symbol: true, exact: true }))
}

/** `costFigure` as text. */
export function costText(row: HoldingRow): string | null {
  return costFigure(row)?.text ?? null
}
