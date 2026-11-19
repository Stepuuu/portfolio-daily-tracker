import type { ReactNode } from 'react'
import { Badge } from '@/components/ui'
import { quoteLine } from '@/features/today/quoteStatus'
import type { TodayRow } from '@/features/today/types'
import { Dot } from './cells'

/**
 * The line under a holding's name: ticker, source and exchange time, a chip for any quote that is not
 * live (amber when the price is not verified), then the row's extra chips (plans, alert rules).
 */
export function QuoteLine({ row, today, extra }: { row: TodayRow; today: string | null; extra?: ReactNode }) {
  const line = quoteLine(row.quote, today, row.estimated, row.estimated_reason)
  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-caption text-fg-3">
      <span className="inline-flex items-center gap-x-1.5 whitespace-nowrap tabular-nums">
        {row.ticker}
        {line.meta && <><Dot />{line.meta}</>}
      </span>
      {line.chip && <Badge tone={line.chip.tone} title={line.chip.title} className="ml-0.5">{line.chip.label}</Badge>}
      {line.estimate && <Badge tone="warn" title={line.estimate.title}>{line.estimate.label}</Badge>}
      {extra}
    </div>
  )
}
