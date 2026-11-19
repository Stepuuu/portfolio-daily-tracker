// 对照交易计划: for each buy or sell in a preview, the plan in force and what the trade is to it (建仓, 加仓,
// 减仓, 清仓), with the departures the backend found: warnings in amber, notes quieter, a weight with the
// time of the quotes it was valued at. It informs and never blocks; a failed check is one quiet line.
import { AlertTriangle, Info } from 'lucide-react'
import { cx, numeric } from '@/components/ui'
import { planCheckLines } from '@/features/ledger/review'
import type { PlanCheckItem } from '@/features/ledger/types'

export function PlanCheck({ items, error, className }: { items?: PlanCheckItem[]; error?: string; className?: string }) {
  const lines = planCheckLines(items)
  if (!lines.length && !error) return null
  return (
    <section aria-label="对照交易计划" className={cx('flex flex-col gap-2', className)}>
      <h3 className="text-table font-medium text-fg-2">对照交易计划</h3>
      {error && <p className="text-table text-fg-3">{error}</p>}
      <ul className="flex flex-col gap-2">
        {lines.map((line) => (
          <li key={line.key} className="text-table">
            <p className={cx('text-fg', numeric)}>
              {line.title}
              <span className={cx('ml-2', line.hasPlan ? 'text-fg-2' : 'text-fg-3')}>{line.relation}</span>
            </p>
            {line.deviations.length > 0 && (
              <ul className="mt-1 flex flex-col gap-1">
                {line.deviations.map((deviation) => (
                  <li
                    key={deviation.key}
                    className={cx('flex items-start gap-1.5', deviation.level === 'warning' ? 'text-warn' : 'text-fg-3')}
                  >
                    {deviation.level === 'warning'
                      ? <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0" />
                      : <Info size={14} aria-hidden className="mt-[3px] shrink-0" />}
                    <span className="sr-only">{deviation.level === 'warning' ? '提示：' : '说明：'}</span>
                    <span className="min-w-0 break-words">
                      {deviation.message}
                      {deviation.asOf && <span className={cx('ml-1 text-fg-3', numeric)}>（{deviation.asOf}）</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
