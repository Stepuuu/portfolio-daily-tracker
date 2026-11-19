// The amber qualifier next to an estimated total, with the list of non-verified inputs behind it.
import { Info } from 'lucide-react'
import { Badge, Popover, cx, focusRing } from '@/components/ui'
import type { EstimateItem } from '@/features/today/totals'

export function EstimateNote({ items, what = '总资产和盈亏' }: { items: EstimateItem[]; what?: string }) {
  return (
    <Popover
      align="start"
      label="估算依据"
      className="w-[min(360px,calc(100vw-16px))]"
      trigger={(
        <button
          type="button"
          className={cx('inline-flex items-center gap-1 rounded-sm text-table font-normal text-warn underline-offset-4 hover:underline', focusRing)}
        >
          估算
          <Info size={12} aria-hidden />
        </button>
      )}
    >
      <p className="text-table text-fg-2">以下行情或汇率未经核实，{what}按它们估算：</p>
      <ul className="mt-2 flex flex-col gap-2">
        {items.map((item) => (
          <li key={item.key} className="text-table">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-fg">{item.subject}</span>
              <Badge tone="warn">{item.status}</Badge>
            </span>
            <span className="mt-0.5 block text-caption text-fg-3">{item.reason}</span>
          </li>
        ))}
      </ul>
    </Popover>
  )
}
