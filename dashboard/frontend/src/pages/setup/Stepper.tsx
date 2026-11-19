// The wizard's steps, each with its state from the checklist (done gets a check, to-do is said in words).
// Any step can be opened directly: none blocks another. On phones only the current step's name is shown.
import { Check } from 'lucide-react'
import { cx, focusRing } from '@/components/ui'
import { STEP_LABELS, stepState, type StepId } from '@/features/setup/steps'
import type { SetupItem } from '@/features/setup/types'

const SPOKEN = { done: '已完成', todo: '待处理', optional: '可选', unknown: '' } as const

export function Stepper({ steps, current, items, onSelect }: {
  steps: StepId[]; current: StepId | null; items: SetupItem[] | undefined; onSelect: (step: StepId) => void
}) {
  return (
    <nav aria-label="设置步骤">
      <ol className="flex flex-wrap items-center gap-1">
        {steps.map((step, index) => {
          const state = stepState(step, items)
          const active = step === current
          return (
            <li key={step} className="flex items-center gap-1">
              {index > 0 && <span aria-hidden className="h-px w-3 bg-line max-sm:hidden" />}
              <button
                type="button"
                aria-current={active ? 'step' : undefined}
                onClick={() => onSelect(step)}
                className={cx(
                  'inline-flex h-8 items-center gap-2 rounded-md px-2 text-table transition-colors duration-[120ms] coarse:h-10',
                  active ? 'bg-accent/10 font-medium text-fg' : 'text-fg-2 hover:bg-fg/[.06] hover:text-fg',
                  focusRing,
                )}
              >
                <span
                  aria-hidden
                  className={cx(
                    'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-caption',
                    state === 'done' ? 'bg-accent text-accent-fg' : active ? 'ring-1 ring-inset ring-accent text-accent' : 'ring-1 ring-inset ring-line-input text-fg-3',
                  )}
                >
                  {state === 'done' ? <Check size={12} /> : index + 1}
                </span>
                <span className={cx(!active && 'max-sm:sr-only')}>{STEP_LABELS[step]}</span>
                {SPOKEN[state] && <span className="sr-only">（{SPOKEN[state]}）</span>}
                {state === 'todo' && !active && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-warn" />}
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
