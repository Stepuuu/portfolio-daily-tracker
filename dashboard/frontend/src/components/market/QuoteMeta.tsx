import type { ReactNode } from 'react'
import { Badge, cx, numeric } from '@/components/ui'
import { quoteLine } from '@/features/today/quoteStatus'
import type { QuoteObject } from '@/features/today/types'

/** "暂无行情" for a quote object the backend did not return. */
const MISSING = { source: null, quote_time: null, fetched_at: null, status: 'unavailable' as const, reason: '没有取得行情', session_date: null }

/**
 * The line under a name: ticker, source and exchange time, a chip for any quote that is not live (amber
 * when not verified), then extra chips. The same words as Today's holdings.
 */
export function QuoteMeta({
  ticker, quote, today, extra, className, showTicker = true,
}: { ticker: string; quote: QuoteObject | null | undefined; today: string | null; extra?: ReactNode; className?: string; showTicker?: boolean }) {
  const line = quoteLine(quote?.quote ?? MISSING, today, quote?.estimated ?? false, quote?.estimated_reason ?? null)
  const meta = [showTicker ? ticker : '', line.meta].filter(Boolean)
  return (
    <span className={cx('flex flex-wrap items-center gap-x-1.5 gap-y-1 text-caption text-fg-3', className)}>
      {meta.length > 0 && (
        <span className={cx('inline-flex items-center gap-x-1.5 whitespace-nowrap', numeric)}>
          {meta.map((part, index) => (
            <span key={index} className="inline-flex items-center gap-x-1.5">
              {index > 0 && <span aria-hidden>·</span>}
              {part}
            </span>
          ))}
        </span>
      )}
      {line.chip && <Badge tone={line.chip.tone} title={line.chip.title}>{line.chip.label}</Badge>}
      {line.estimate && <Badge tone="warn" title={line.estimate.title}>{line.estimate.label}</Badge>}
      {extra}
    </span>
  )
}
