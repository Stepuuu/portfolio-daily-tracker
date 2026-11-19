// What a row says about its quote: the source and time, and a chip for anything that is not a fresh
// price. Wording rules (plan 2.7): 实时 / 延迟 / 已过期 hh:mm / 收盘价 / 时间未知 / 不可用. Amber (`warn`)
// marks every price the backend does not count as verified (the row is then estimated or unavailable).
import { fmtDate, fmtTime } from '../../lib/format.ts'
import { DISPLAY_ZONE, sourceLabel } from './session.ts'
import type { Quote, QuoteStatus } from './types.ts'

export type ChipTone = 'neutral' | 'warn'

export interface Chip {
  label: string
  tone: ChipTone
  /** Longer explanation (the backend's reason), for the tooltip. */
  title?: string
}

export interface QuoteLine {
  /** "东财 10:32:05": source and exchange time when the price is fresh, else the source only. */
  meta: string
  /** The quote state, when it is anything but live. */
  chip: Chip | null
  /** An extra amber chip when the row is estimated for another reason (a currency rate). */
  estimate: Chip | null
}

/** Statuses whose price the backend counts as verified (unless a weekday-only calendar says closed). */
const VERIFIED: QuoteStatus[] = ['live', 'delayed', 'closed']

/** "10:32", or "11-16 05:00" when the time is not on `today` (YYYY-MM-DD, Beijing). */
export function shortTime(iso: string | null | undefined, today: string | null, seconds = false): string {
  if (!iso) return ''
  const day = fmtDate(iso, { timeZone: DISPLAY_ZONE })
  const time = fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds })
  if (day === '不可用' || time === '不可用') return ''
  return today && day !== today ? `${day.slice(5)} ${fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds: false })}` : time
}

/** The chip for a quote status. `estimated` comes from the row (or quote object). */
export function statusChip(quote: Quote, today: string | null, estimated = false, estimatedReason: string | null = null): Chip | null {
  const reason = quote.reason ?? undefined
  switch (quote.status) {
    case 'live':
      return null
    case 'delayed':
      return { label: '延迟', tone: 'neutral', title: reason ?? '行情延迟，非实时价格' }
    case 'closed': {
      const when = shortTime(quote.quote_time, today)
      // A close from a market without a holiday table is not verified: the row is estimated.
      return { label: when ? `收盘价 ${when}` : '收盘价', tone: estimated ? 'warn' : 'neutral', title: (estimated ? estimatedReason : null) ?? reason }
    }
    case 'stale': {
      const when = shortTime(quote.quote_time, today)
      return { label: when ? `已过期 ${when}` : '已过期', tone: 'warn', title: estimatedReason ?? reason ?? '行情未及时更新，按最后一次价格估算' }
    }
    case 'prev_close':
      return { label: '昨收价', tone: 'warn', title: estimatedReason ?? reason ?? '今日尚无成交，按昨收价估算' }
    case 'time_unknown':
      return { label: '时间未知', tone: 'warn', title: estimatedReason ?? reason ?? '行情缺少交易所时间，按此价格估算' }
    case 'unavailable':
    default:
      return { label: '不可用', tone: 'warn', title: reason ?? '没有可用行情' }
  }
}

/** Everything the quote line under a holding's name shows. */
export function quoteLine(
  quote: Quote, today: string | null, estimated = false, estimatedReason: string | null = null,
): QuoteLine {
  const source = sourceLabel(quote.source)
  const fresh = quote.status === 'live' || quote.status === 'delayed'
  const time = fresh ? shortTime(quote.quote_time, today, true) : ''
  const chip = statusChip(quote, today, estimated, estimatedReason)
  // The status chip already says "estimated" for the quote's own reasons; a verified quote on an
  // estimated row means another input (the currency rate) is not verified.
  const estimate = estimated && VERIFIED.includes(quote.status) && chip?.tone !== 'warn'
    ? { label: '估算', tone: 'warn' as const, title: estimatedReason ?? '汇率未经核实，按最近汇率估算' }
    : null
  return { meta: [source, time].filter(Boolean).join(' '), chip, estimate }
}

/** Whether a figure computed from this quote is missing (the row's cells then say 不可用). */
export function priceMissing(quote: Quote): boolean {
  return quote.status === 'unavailable'
}

const ESTIMATE_STATUS: Record<string, string> = {
  stale: '已过期', prev_close: '昨收价', time_unknown: '时间未知', closed: '收盘价未核实', delayed: '延迟',
}

/** "已过期" for an `estimated[]` entry's status. */
export function estimateStatusLabel(status: string): string {
  return ESTIMATE_STATUS[status] ?? status
}
