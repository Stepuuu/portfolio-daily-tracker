import type { CSSProperties, ReactNode } from 'react'
import { AlertTriangle, Info, type LucideIcon } from 'lucide-react'
import { cx } from './cx'

export interface EmptyStateProps {
  title: ReactNode
  description?: ReactNode
  /** One or two buttons: what to do next. */
  action?: ReactNode
  icon?: LucideIcon
  className?: string
}

/** What to show when there is nothing yet: a short title, one sentence, and the next step. */
export function EmptyState({ title, description, action, icon: Icon, className }: EmptyStateProps) {
  return (
    <div className={cx('flex flex-col items-center justify-center px-6 py-8 text-center', className)}>
      {Icon && <Icon size={20} className="mb-2 text-fg-3" aria-hidden />}
      <p className="text-body font-medium text-fg">{title}</p>
      {description && <p className="mt-1 max-w-sm text-table text-fg-3">{description}</p>}
      {action && <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  )
}

const size = (value: number | string | undefined) => (typeof value === 'number' ? `${value}px` : value)

/**
 * Placeholder blocks while loading. They fade in only after 200 ms so fast loads do not flicker.
 * Decorative: put the loading text for screen readers on the container (DataTable does this).
 */
export function Skeleton({
  w, h = 12, lines, className,
}: { w?: number | string; h?: number | string; lines?: number; className?: string }) {
  const block = 'block animate-delayed-in rounded bg-fg/[.08]'
  if (lines && lines > 1) {
    return (
      <span aria-hidden className={cx('flex flex-col gap-2', className)} style={{ width: size(w) }}>
        {Array.from({ length: lines }, (_, index) => (
          <span key={index} className={block} style={{ height: size(h), width: index === lines - 1 ? '60%' : '100%' }} />
        ))}
      </span>
    )
  }
  const style: CSSProperties = { width: size(w) ?? '100%', height: size(h) }
  return <span aria-hidden className={cx(block, className)} style={style} />
}

/** A table-shaped placeholder for when even the columns are not known yet. */
export function TableSkeleton({ rows = 5, cols = 4, label = '正在加载' }: { rows?: number; cols?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="divide-y divide-line">
      {Array.from({ length: rows }, (_, row) => (
        <div key={row} className="flex h-9 items-center gap-6 px-4">
          {Array.from({ length: cols }, (_, col) => (
            <Skeleton key={col} className="flex-1" w={col === 0 ? '28%' : undefined} />
          ))}
        </div>
      ))}
    </div>
  )
}

export interface InlineAlertProps {
  /** 'warn' for errors, warnings and stale data (amber; red never means error). 'info' is neutral. */
  tone?: 'warn' | 'info'
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
}

/** A message inside the page flow, e.g. "行情已过期" or a failed request with a retry button. */
export function InlineAlert({ tone = 'info', title, children, action, className }: InlineAlertProps) {
  const Icon = tone === 'warn' ? AlertTriangle : Info
  return (
    <div
      role={tone === 'warn' ? 'alert' : 'status'}
      className={cx('flex items-start gap-3 rounded-md px-3 py-2 text-body', tone === 'warn' ? 'bg-warn/10' : 'bg-fg/[.05]', className)}
    >
      <Icon size={16} aria-hidden className={cx('mt-[3px] shrink-0', tone === 'warn' ? 'text-warn' : 'text-fg-3')} />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium text-fg">{title}</p>}
        {children && <div className={title ? 'text-fg-2' : 'text-fg'}>{children}</div>}
      </div>
      {action && <div className="-my-0.5 flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  )
}
