import { cx } from '@/components/ui'

/** "示例宽基 SHA:DEMO" when the name is known (a ticker the ledger holds), else the ticker. */
export function PlanName({ ticker, name, className }: { ticker: string; name?: string; className?: string }) {
  return (
    <span className={cx('min-w-0 truncate', className)}>
      {name && <span className="font-medium text-fg">{name} </span>}
      <span className={cx('tabular-nums', name ? 'text-caption text-fg-3' : 'font-medium text-fg')}>{ticker}</span>
    </span>
  )
}
