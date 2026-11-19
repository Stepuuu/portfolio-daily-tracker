import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { UNAVAILABLE, toneOf } from '@/lib/format'
import { cx, numeric } from './cx'

export type StatTone = 'auto' | 'up' | 'down' | 'flat'

export interface StatProps {
  label: ReactNode
  /** Already formatted; null shows "不可用" (never 0). */
  value: string | null
  /** Shown after the value on the same line, e.g. a signed percentage. */
  sub?: ReactNode
  /** 'auto' colours by the sign of `value` (red up, green down). Default 'flat': plain text. */
  tone?: StatTone
  size?: 'md' | 'lg'
  /** Why the value is unavailable, e.g. "1 项缺价". */
  unavailableReason?: string
  /** The exact value when `value` is shortened (万/亿); shown on hover and read by screen readers. */
  exactTitle?: string
  className?: string
}

const TONE = { up: 'text-up', down: 'text-down', flat: 'text-fg' } as const

/** A labelled figure. Plain text on the page, never a tinted card. Lay several out with StatGroup. */
export function Stat({ label, value, sub, tone = 'flat', size = 'md', unavailableReason, exactTitle, className }: StatProps) {
  const resolved = tone === 'auto' ? toneOf(value) : tone
  return (
    <dl className={cx('min-w-0', className)}>
      <dt className="text-table text-fg-2">{label}</dt>
      <dd className="flex flex-wrap items-baseline gap-x-2">
        {value === null ? (
          <>
            <span className={cx(size === 'lg' ? 'text-page' : 'text-title', 'font-normal text-fg-3')}>{UNAVAILABLE}</span>
            {unavailableReason && <span className="text-table text-fg-3">（{unavailableReason}）</span>}
          </>
        ) : (
          <span title={exactTitle} className={cx(size === 'lg' ? 'text-figure' : 'text-title', numeric, TONE[resolved])}>
            {value}
            {exactTitle && <span className="sr-only">（精确值 {exactTitle}）</span>}
          </span>
        )}
        {sub && value !== null && <span className={cx('text-body', numeric)}>{sub}</span>}
      </dd>
    </dl>
  )
}

/** A row of Stats with even spacing that wraps on small screens. */
export function StatGroup({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('flex flex-wrap gap-x-8 gap-y-4', className)}>{children}</div>
}

export type BadgeTone = 'neutral' | 'accent' | 'warn' | 'up' | 'down'

const BADGE_TONE: Record<BadgeTone, string> = {
  neutral: 'bg-fg/[.08] text-fg-2',
  accent: 'bg-accent/10 text-accent',
  warn: 'bg-warn/10 text-warn',
  up: 'bg-up/10 text-up',
  down: 'bg-down/10 text-down',
}

/** A short status chip ("已过期 10:20", "演示", "2"). Colour only when the status means something. */
export function Badge({
  tone = 'neutral', icon: Icon, children, className, title,
}: { tone?: BadgeTone; icon?: LucideIcon; children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        'inline-flex h-5 max-w-full items-center gap-1 whitespace-nowrap rounded-full px-2 text-caption font-medium',
        numeric,
        BADGE_TONE[tone],
        className,
      )}
    >
      {Icon && <Icon size={12} className="shrink-0" aria-hidden />}
      <span className="truncate">{children}</span>
    </span>
  )
}
