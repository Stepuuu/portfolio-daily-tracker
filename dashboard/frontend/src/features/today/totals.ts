// How the headline totals are presented. The backend computes every figure; this only decides the
// qualifiers: amber "估算" (with the list of non-verified inputs) on each known figure of estimated totals,
// "不可用（N 项缺价）" for a null figure, 不可用 for a known figure's missing percentage.
import { estimateStatusLabel } from './quoteStatus.ts'
import type { EstimatedEntry, TodayPayload, TodayTotals, UnavailableEntry } from './types.ts'

export type FigureField = 'value' | 'day_pnl'

/**
 * Why a null figure is missing, as a short count: "1 项缺价", "2 项缺价、1 种货币缺汇率". `value` counts the
 * inputs that leave every CNY figure unknown; `day_pnl` adds the ones that affect day profit only.
 */
export function missingSummary(unavailable: UnavailableEntry[] | undefined, field: FigureField = 'value'): string | null {
  const prices = new Set<string>()
  const days = new Set<string>()
  const rates = new Set<string>()
  for (const entry of unavailable ?? []) {
    const dayOnly = entry.field === 'day_pnl'
    if (dayOnly && field !== 'day_pnl') continue
    if (entry.currency && !entry.ticker) rates.add(entry.currency)
    else if (entry.ticker) (dayOnly ? days : prices).add(entry.ticker)
  }
  for (const ticker of prices) days.delete(ticker)
  const parts = [
    prices.size ? `${prices.size} 项缺价` : '',
    days.size ? `${days.size} 项无法计算当日盈亏` : '',
    rates.size ? `${rates.size} 种货币缺汇率` : '',
  ].filter(Boolean)
  return parts.length ? parts.join('、') : null
}

export interface EstimateItem {
  key: string
  /** "示例宽基 SHA:DEMO" or "美元汇率". */
  subject: string
  status: string
  reason: string
}

const CURRENCY_NAME: Record<string, string> = { USD: '美元', HKD: '港币', CNY: '人民币' }

/** `estimated[]` as lines for the popover next to an estimated total. */
export function estimateItems(entries: EstimatedEntry[] | undefined, names: Map<string, string> = new Map()): EstimateItem[] {
  return (entries ?? []).map((entry, index) => {
    const subject = entry.ticker
      ? [names.get(entry.ticker), entry.ticker].filter(Boolean).join(' ')
      : `${CURRENCY_NAME[entry.currency ?? ''] ?? entry.currency ?? ''}汇率`
    return { key: `${entry.ticker ?? entry.currency ?? ''}:${entry.status}:${index}`, subject, status: estimateStatusLabel(entry.status), reason: entry.reason }
  })
}

/** Ticker → name, from every row of the payload (for popovers that list tickers). */
export function tickerNames(payload: Pick<TodayPayload, 'accounts'> | undefined): Map<string, string> {
  const names = new Map<string, string>()
  for (const account of payload?.accounts ?? []) {
    for (const row of [...account.positions, ...account.closed_today]) {
      if (row.name && row.name !== row.ticker) names.set(row.ticker, row.name)
    }
  }
  return names
}

export interface Figure {
  /** The backend's decimal string; null when unknown. */
  value: string | null
  pct: string | null
  /** The figure is known and normally has a percentage, but the backend could not compute it: 不可用. */
  pctUnavailable: boolean
  /** Shown after 不可用. */
  missing: string | null
  /**
   * The figure is known and rests on non-verified inputs (`quality: 'estimated'`): it carries its own amber
   * 估算 mark, so the mark never depends on another figure being shown (total assets may be 不可用).
   */
  estimated: boolean
}

export interface TotalsView {
  total: Figure
  day: Figure
  /** Unrealized profit of open positions against average cost (`total_pnl_kind`). */
  holding: Figure
  /** Total value minus contributed capital. */
  cumulative: Figure
  /** Cash per currency, as the backend sends it (never summed here). */
  cash: [string, string][]
  estimated: boolean
  estimates: EstimateItem[]
}

const CURRENCY_ORDER = ['CNY', 'HKD', 'USD']

/** "0", "0.00", "-0.0": a decimal string that is exactly zero (read as text, not as a float). */
export function isZeroAmount(value: string | null | undefined): boolean {
  return typeof value === 'string' && /^[+-]?0*\.?0*$/.test(value.trim()) && /0/.test(value)
}

/** Cash balances per currency, CNY first; zero balances of foreign currencies are left out. */
export function cashBalances(totals: Pick<TodayTotals, 'by_currency'> | undefined): [string, string][] {
  const entries = Object.entries(totals?.by_currency ?? {})
    .filter((entry): entry is [string, { market_value: string | null; cash: string }] => entry[1].cash !== null)
    .filter(([currency, { cash }]) => currency === 'CNY' || !isZeroAmount(cash))
    .map(([currency, { cash }]): [string, string] => [currency, cash])
  const rank = (currency: string) => (CURRENCY_ORDER.includes(currency) ? CURRENCY_ORDER.indexOf(currency) : CURRENCY_ORDER.length)
  return entries.sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))
}

/** The four headline figures with their qualifiers, straight from `totals`. */
export function totalsView(payload: Pick<TodayPayload, 'accounts' | 'totals'>): TotalsView {
  const totals = payload.totals
  const estimated = totals.quality === 'estimated'
  // `pct` undefined: the figure has no percentage (total assets).
  const figure = (value: string | null, pct: string | null | undefined, field: FigureField): Figure => ({
    value,
    pct: value === null ? null : pct ?? null,
    pctUnavailable: value !== null && pct === null,
    missing: value === null ? missingSummary(totals.unavailable, field) : null,
    estimated: estimated && value !== null,
  })
  return {
    total: figure(totals.total_cny, undefined, 'value'),
    day: figure(totals.day_pnl_cny, totals.day_pnl_pct, 'day_pnl'),
    holding: figure(totals.total_pnl_cny, totals.total_pnl_pct, 'value'),
    cumulative: figure(totals.cumulative_pnl_cny, totals.cumulative_pnl_pct, 'value'),
    cash: cashBalances(totals),
    estimated,
    estimates: estimateItems(totals.estimated, tickerNames(payload)),
  }
}
