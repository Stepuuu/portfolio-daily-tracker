// "1 选择文件 > 2 核对列 > 3 预览与去重 > 4 确认": where the import is; a reachable step is a button.
import { Check, ChevronRight } from 'lucide-react'
import { cx, focusRing, numeric } from '@/components/ui'
import { STEPS, type StepId } from '@/features/import/flow'

export interface StepIndicatorProps {
  current: StepId
  /** Steps that can be opened now. */
  reachable: (step: StepId) => boolean
  onSelect: (step: StepId) => void
  className?: string
}

export function StepIndicator({ current, reachable, onSelect, className }: StepIndicatorProps) {
  return (
    <nav aria-label="导入步骤" className={cx('min-w-0', className)}>
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-1 text-body">
        {STEPS.map((step, index) => {
          const active = step.id === current
          const done = step.id < current
          const open = !active && reachable(step.id)
          const badge = (
            <span
              aria-hidden
              className={cx(
                'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-caption',
                numeric,
                active ? 'bg-accent text-accent-fg' : done ? 'bg-fg/[.08] text-fg-2' : 'border border-line-input/60 text-fg-3',
              )}
            >
              {done ? <Check size={12} strokeWidth={3} /> : step.id}
            </span>
          )
          const label = <span className={cx('whitespace-nowrap', active ? 'font-medium text-fg' : 'text-fg-2', !active && !open && 'text-fg-3')}>{step.label}</span>
          return (
            <li key={step.id} className="flex items-center gap-1">
              {open ? (
                <button
                  type="button"
                  onClick={() => onSelect(step.id)}
                  className={cx('inline-flex h-8 items-center gap-1.5 rounded-md px-1.5 hover:bg-fg/[.06] coarse:h-10', focusRing)}
                >
                  {badge}
                  {label}
                </button>
              ) : (
                <span aria-current={active ? 'step' : undefined} className="inline-flex h-8 items-center gap-1.5 px-1.5 coarse:h-10">
                  {badge}
                  {label}
                </span>
              )}
              {index < STEPS.length - 1 && <ChevronRight size={14} aria-hidden className="shrink-0 text-fg-3" />}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
