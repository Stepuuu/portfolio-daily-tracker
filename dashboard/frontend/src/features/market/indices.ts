// The index line at the top of 行情: five indexes from `GET /api/quotes` (the same quote objects and
// status contract as Today), each with its price, signed change, its own source and exchange time and,
// when the price is not live, a short state word: amber whenever the backend does not count the price as
// verified (stale, previous close, time unknown, or a close the calendar cannot confirm: `estimated`).
// Pure TypeScript so node tests can load it.
import { quoteLine } from '../today/quoteStatus.ts'
import type { QuoteObject, QuoteStatus } from '../today/types.ts'

export const INDEX_STRIP: { ticker: string; label: string }[] = [
  { ticker: 'IDX:000001', label: '上证' },
  { ticker: 'IDX:399001', label: '深成' },
  { ticker: 'IDX:399006', label: '创业板' },
  { ticker: 'IDX:HSI', label: '恒生' },
  { ticker: 'IDX:IXIC', label: '纳指' },
]

const STATE: Partial<Record<QuoteStatus, string>> = {
  delayed: '延迟', closed: '收盘', stale: '已过期', prev_close: '昨收', time_unknown: '时间未知', unavailable: '不可用',
}

export interface StripItem {
  ticker: string
  label: string
  /** null: unavailable (shown as 不可用, never 0). */
  price: string | null
  changePct: string | null
  /** A short word for anything but a live price: 收盘, 已过期 …; null when live. */
  state: string | null
  /** Amber: the price is not verified (stale, previous close, time unknown, unavailable). */
  warn: boolean
  /** The backend's reason, for the tooltip. */
  title: string | undefined
  /** "东财 10:32:02": this index's own source and exchange time; undefined when unavailable. */
  meta: string | undefined
}

/** One item per strip index, in strip order; an index missing from the answer is unavailable. */
export function stripItems(quotes: QuoteObject[] | undefined, today: string | null): StripItem[] {
  const byTicker = new Map((quotes ?? []).map((quote) => [quote.ticker.toUpperCase(), quote]))
  return INDEX_STRIP.map(({ ticker, label }) => {
    const quote = byTicker.get(ticker)
    if (!quote || quote.price === null || quote.quote.status === 'unavailable') {
      return { ticker, label, price: null, changePct: null, state: null, warn: true, title: quote?.quote.reason ?? '没有可用行情', meta: undefined }
    }
    const line = quoteLine(quote.quote, today, quote.estimated, quote.estimated_reason)
    const chip = line.chip ?? line.estimate
    const when = quote.quote.status === 'live' || quote.quote.status === 'delayed' ? '' : chip?.label ?? ''
    return {
      ticker,
      label,
      price: quote.price,
      changePct: quote.change_pct,
      state: STATE[quote.quote.status] ?? (line.estimate ? '估算' : null),
      warn: chip?.tone === 'warn',
      title: chip?.title ?? chip?.label,
      meta: [line.meta, when].filter(Boolean).join(' ') || undefined,
    }
  })
}
