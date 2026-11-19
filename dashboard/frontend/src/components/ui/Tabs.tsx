import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { cx, focusRing, focusRingInset, numeric } from './cx'
import { useFieldLabelId } from './Field'

/** Arrow-key movement shared by tabs and segmented controls; returns the index to move to, or null. */
function nextIndex(key: string, current: number, count: number, enabled: (index: number) => boolean): number | null {
  const steps: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  if (key === 'Home' || key === 'End') {
    const order = Array.from({ length: count }, (_, i) => (key === 'Home' ? i : count - 1 - i))
    return order.find(enabled) ?? null
  }
  const step = steps[key]
  if (!step) return null
  for (let offset = 1; offset <= count; offset += 1) {
    const index = (current + step * offset + count * offset) % count
    if (enabled(index)) return index
  }
  return null
}

export interface SegmentOption<T extends string> {
  value: T
  label: ReactNode
  disabled?: boolean
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentOption<T>[]
  value: T
  onChange: (value: T) => void
  /** Needed unless the control sits in a Field, whose label names it. */
  ariaLabel?: string
  size?: 'sm' | 'md'
  className?: string
}

/** A few mutually exclusive choices shown at once (radio group). Arrow keys move and select. */
export function SegmentedControl<T extends string>({
  options, value, onChange, ariaLabel, size = 'md', className,
}: SegmentedControlProps<T>) {
  const labelledBy = useFieldLabelId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const selectedIndex = options.findIndex((option) => option.value === value)
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const next = nextIndex(event.key, index, options.length, (i) => !options[i].disabled)
    if (next === null) return
    event.preventDefault()
    onChange(options[next].value)
    refs.current[next]?.focus()
  }
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-labelledby={ariaLabel ? undefined : labelledBy}
      className={cx('inline-flex w-fit max-w-full overflow-x-auto rounded-md bg-surface-2 p-0.5 ring-1 ring-inset ring-line [scrollbar-width:none]', className)}
    >
      {options.map((option, index) => {
        const selected = option.value === value
        const focusable = selected || (selectedIndex === -1 && index === 0)
        return (
          <button
            key={option.value}
            ref={(element) => { refs.current[index] = element }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={focusable ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cx(
              'inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-[5px] transition-colors duration-[120ms]',
              size === 'sm' ? 'h-6 px-2 text-table' : 'h-7 px-3 text-body coarse:h-9',
              selected
                ? 'bg-surface-3 font-medium text-fg shadow-[0_0_0_1px_rgb(var(--border)),0_1px_2px_rgb(0_0_0/0.06)]'
                : 'text-fg-2 hover:text-fg',
              'disabled:cursor-not-allowed disabled:opacity-50',
              focusRing,
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

export interface TabItem {
  id: string
  label: ReactNode
  /** Route tabs: a link to this path (active when the URL matches). */
  href?: string
  /** A count shown after the label, e.g. pending items. */
  count?: number | string
  /** Route tabs: match the path exactly (default true). */
  end?: boolean
}

export interface TabsProps {
  items: TabItem[]
  /** Selected id, for in-page tabs. */
  value?: string
  onChange?: (id: string) => void
  ariaLabel?: string
  className?: string
}

const TAB =
  'relative inline-flex h-10 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 text-body transition-colors duration-[120ms] rounded-t-sm'
const TAB_ON = 'border-accent font-medium text-fg'
const TAB_OFF = 'border-transparent text-fg-2 hover:border-line-input/60 hover:text-fg'

function Count({ value }: { value: number | string }) {
  return <span className={cx('rounded-full bg-fg/[.08] px-2 text-caption text-fg-2', numeric)}>{value}</span>
}

/**
 * Section tabs. With `href` on the items they are route links (a <nav> with aria-current); otherwise an
 * ARIA tab list controlled by `value` / `onChange` (arrow keys switch tabs).
 */
export function Tabs({ items, value, onChange, ariaLabel, className }: TabsProps) {
  const baseId = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const strip = cx(
    'flex min-w-0 gap-6 overflow-x-auto shadow-[inset_0_-1px_0_rgb(var(--border))] [scrollbar-width:none]',
    className,
  )
  if (items.some((item) => item.href)) {
    return (
      <nav aria-label={ariaLabel} className={strip}>
        {items.map((item) => (
          <NavLink
            key={item.id}
            to={item.href ?? '#'}
            end={item.end ?? true}
            className={({ isActive }) => cx(TAB, isActive ? TAB_ON : TAB_OFF, focusRingInset)}
          >
            {item.label}
            {item.count !== undefined && <Count value={item.count} />}
          </NavLink>
        ))}
      </nav>
    )
  }
  const selected = value ?? items[0]?.id
  return (
    <div role="tablist" aria-label={ariaLabel} className={strip}>
      {items.map((item, index) => {
        const active = item.id === selected
        return (
          <button
            key={item.id}
            ref={(element) => { refs.current[index] = element }}
            type="button"
            role="tab"
            id={`${baseId}-${item.id}`}
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange?.(item.id)}
            onKeyDown={(event) => {
              const next = nextIndex(event.key, index, items.length, () => true)
              if (next === null) return
              event.preventDefault()
              onChange?.(items[next].id)
              refs.current[next]?.focus()
            }}
            className={cx(TAB, active ? TAB_ON : TAB_OFF, focusRingInset)}
          >
            {item.label}
            {item.count !== undefined && <Count value={item.count} />}
          </button>
        )
      })}
    </div>
  )
}
