// The stock drawer's own figures: a holding as Today has it (the backend's digits, its estimate mark and
// the reason). The 问助手 prefill is the shell's (shellStore `assistantPrefillFor`). Pure TypeScript so
// node tests can load it.
import { UNAVAILABLE, fmtLong, fmtPct, fmtQty, type Shown } from '../../lib/format.ts'
import type { Quality, TodayRow } from '../today/types.ts'
import { fmtTickerPrice } from './format.ts'

export interface PositionView {
  quantity: string
  /**
   * The average cost with every digit the ledger has ("12.345679"), padded to the market's digits; one that
   * repeats past 8 places is ≈ rounded to 8 with the full value in `full` (lib/format.ts `fmtLong`).
   */
  cost: Shown
  /** Of the whole portfolio, as the backend computed it ("57.93%"). */
  weight: string
  /**
   * The weight rests on a price or rate that is not verified: the row's own (stale quote, stale rate) or
   * any other holding's (the portfolio total behind every weight).
   */
  weightEstimated: boolean
  weightTitle: string | undefined
}

/**
 * A Today row for the drawer. `totalsQuality` is `/api/today`'s `totals.quality`: anything but
 * `complete` makes every weight an estimate (the denominator is the whole portfolio), exactly when the
 * alert monitor also refuses to use it.
 */
export function positionView(row: TodayRow, totalsQuality: Quality | undefined, totalsReason: string | null = null): PositionView {
  const ownEstimate = row.estimated
  const weightEstimated = row.weight_pct !== null && (ownEstimate || totalsQuality !== 'complete')
  return {
    quantity: fmtQty(row.quantity),
    cost: row.cost_price === null ? { text: UNAVAILABLE } : fmtLong(row.cost_price, (value) => fmtTickerPrice(String(value), row.ticker, row.currency, { exact: true })),
    weight: fmtPct(row.weight_pct, { exact: true }),
    weightEstimated,
    weightTitle: weightEstimated
      ? `估算：${(ownEstimate ? row.estimated_reason : null) ?? totalsReason ?? '组合中有未经核实的价格或汇率'}`
      : undefined,
  }
}
