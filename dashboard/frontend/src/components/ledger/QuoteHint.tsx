// The quote under 标的 once the ticker is known: 现价 … · 昨收 … · 来源 时间, with [按现价] / [按昨收] to fill
// the price. Only a fresh price can be used; a stale, unknown-time or missing quote is labelled as such and
// offers nothing to fill. A previous close that is itself not verified fills as 估算 and says so. What fills
// the field carries its origin (`PriceFill`) until the price is edited. The fill price on the broker's
// confirmation is what counts.
import { Badge, Button, cx, numeric } from '@/components/ui'
import { fmtPrice } from '@/lib/format'
import { quoteLine } from '@/features/today/quoteStatus'
import { sourceLabel } from '@/features/today/session'
import type { QuoteObject } from '@/features/today/types'
import { instrumentKind } from '@/features/today/rows'
import type { PriceFill } from '@/features/ledger/entry'

/** Quote statuses whose price can fill the field. */
const FRESH = new Set(['live', 'delayed'])
/** Statuses whose previous close can fill the field. */
const CLOSE_OK = new Set(['live', 'delayed', 'closed', 'prev_close'])
/** Statuses under which the previous close is verified (anything else fills it as 估算). */
const CLOSE_VERIFIED = new Set(['live', 'delayed', 'closed'])

export interface QuoteHintProps {
  ticker: string
  currency: string
  quote: QuoteObject | undefined
  loading?: boolean
  failed?: boolean
  today: string | null
  /** Fill the price; `fill` says where it came from. */
  onPrice?: (price: string, fill: PriceFill) => void
  className?: string
}

export function QuoteHint({ ticker, currency, quote, loading = false, failed = false, today, onPrice, className }: QuoteHintProps) {
  const kind = instrumentKind(ticker)
  if (loading && !quote) return <p className={cx('text-table text-fg-3', className)}>正在读取行情…</p>
  if (failed || !quote) return <p className={cx('text-table text-fg-3', className)}>行情暂时不可用，请按成交回报填写价格</p>
  const status = quote.quote.status
  const line = quoteLine(quote.quote, today, quote.estimated, quote.estimated_reason)
  const price = quote.price && status !== 'unavailable' ? quote.price : null
  const fresh = Boolean(price && FRESH.has(status) && !quote.estimated)
  const close = quote.prev_close && CLOSE_OK.has(status) ? quote.prev_close : null
  const closeEstimated = Boolean(close) && (quote.estimated || !CLOSE_VERIFIED.has(status))
  const fill = (basis: PriceFill['basis'], value: string, estimated: boolean): PriceFill => ({
    ticker, basis, value, source: sourceLabel(quote.quote.source) || null, time: quote.quote.quote_time, estimated,
  })
  return (
    <div className={cx('flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-2', className)}>
      {price || close ? (
        <span className={numeric}>
          {price && <>现价 {fmtPrice(price, currency, kind, { exact: true })}</>}
          {price && close && ' · '}
          {close && <>昨收 {fmtPrice(close, currency, kind, { exact: true })}</>}
          {line.meta && <span className="text-fg-3"> · {line.meta}</span>}
        </span>
      ) : (
        <span>行情不可用{quote.quote.reason ? `（${quote.quote.reason}）` : ''}</span>
      )}
      {line.chip && status !== 'unavailable' && <Badge tone={line.chip.tone} title={line.chip.title}>{line.chip.label}</Badge>}
      {onPrice && fresh && (
        <Button size="sm" variant="ghost" className="-my-1 text-accent hover:text-accent" onClick={() => onPrice(price!, fill('price', price!, false))}>按现价</Button>
      )}
      {onPrice && close && (
        <Button
          size="sm"
          variant="ghost"
          className="-my-1 text-accent hover:text-accent"
          title={closeEstimated ? '昨收价未经核实，填入后标为估算' : undefined}
          onClick={() => onPrice(close, fill('prev_close', close, closeEstimated))}
        >
          按昨收{closeEstimated ? '（估算）' : ''}
        </Button>
      )}
      <span className="text-fg-3">成交价以成交回报为准</span>
    </div>
  )
}
