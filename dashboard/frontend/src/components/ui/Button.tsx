import { forwardRef, useId, type ButtonHTMLAttributes, type MouseEvent, type ReactNode } from 'react'
import { Loader2, type LucideIcon } from 'lucide-react'
import { cx, focusRing, joinIds } from './cx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'warn-outline'
export type ButtonSize = 'sm' | 'md'

// Hover and press tint the accent towards --text (lighter in the dark theme, darker in the light one),
// away from the label's colour, so its contrast only grows. Fading the accent (accent/80) let the surface
// show through and took the label below 4.5:1; tests/tokens.test.mjs composites these exact classes.
const VARIANT: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-fg font-medium hover:bg-[linear-gradient(rgb(var(--text)/0.08),rgb(var(--text)/0.08))] active:bg-[linear-gradient(rgb(var(--text)/0.16),rgb(var(--text)/0.16))]',
  secondary: 'border border-line-input/40 bg-surface text-fg hover:border-line-input/70 hover:bg-surface-2 active:bg-surface-2',
  ghost: 'text-fg-2 hover:bg-fg/[.06] hover:text-fg active:bg-fg/10',
  'warn-outline': 'border border-warn/50 text-warn hover:bg-warn/10 active:bg-warn/15',
}

const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 gap-1 px-2 text-table coarse:h-9',
  md: 'h-8 gap-2 px-3 text-body coarse:h-10',
}

const ICON_SIZE: Record<ButtonSize, number> = { sm: 14, md: 16 }

const BASE =
  'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-md transition-colors duration-[120ms] ' +
  'disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 ' +
  focusRing

/**
 * Small text bubble shown above (or below) its trigger on hover and keyboard focus. The trigger and
 * the bubble must share a parent with `group/tip`.
 */
export function TipBubble({
  id, children, side = 'top', align = 'center', hidden = false,
}: { id?: string; children: ReactNode; side?: 'top' | 'bottom'; align?: 'center' | 'start' | 'end'; hidden?: boolean }) {
  return (
    <span
      id={id}
      role={id ? 'tooltip' : undefined}
      aria-hidden={hidden || undefined}
      className={cx(
        'pointer-events-none absolute z-50 hidden w-max max-w-[16rem] whitespace-normal rounded-md bg-fg px-2 py-1 text-left text-caption font-normal text-bg shadow-overlay',
        'group-hover/tip:block group-has-[:focus-visible]/tip:block',
        side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2',
        align === 'center' && 'left-1/2 -translate-x-1/2',
        align === 'start' && 'left-0',
        align === 'end' && 'right-0',
      )}
    >
      {children}
    </span>
  )
}

/** A button's classes, for a link that must look like one (e.g. a primary link to another page). */
export function buttonClass({ variant = 'secondary', size = 'md' }: { variant?: ButtonVariant; size?: ButtonSize } = {}): string {
  return cx(BASE, VARIANT[variant], SIZE[size])
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: LucideIcon
  /** Shows a spinner and blocks clicks. */
  loading?: boolean
  /**
   * Why the button cannot be used (e.g. "演示模式只读"). Disables it and shows the reason on hover and
   * keyboard focus; the button stays focusable so everyone can read why.
   */
  disabledReason?: string
  /** Where the disabled reason appears. */
  tipAlign?: 'center' | 'start' | 'end'
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary', size = 'md', icon: Icon, loading = false, disabledReason, tipAlign = 'center',
    disabled, type = 'button', className, children, onClick, 'aria-describedby': describedBy, ...rest
  },
  ref,
) {
  const tipId = useId()
  const blocked = Boolean(disabledReason) || loading
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (blocked) {
      event.preventDefault()
      return
    }
    onClick?.(event)
  }
  const button = (
    <button
      ref={ref}
      type={type}
      disabled={disabled && !disabledReason}
      aria-disabled={blocked || undefined}
      aria-busy={loading || undefined}
      aria-describedby={joinIds(describedBy, disabledReason && tipId)}
      onClick={handleClick}
      className={cx(BASE, VARIANT[variant], SIZE[size], loading && 'aria-disabled:cursor-progress aria-disabled:opacity-80', className)}
      {...rest}
    >
      {loading ? (
        <Loader2 size={ICON_SIZE[size]} className="shrink-0 animate-spin motion-reduce:animate-none" aria-hidden />
      ) : Icon ? (
        <Icon size={ICON_SIZE[size]} className="shrink-0" aria-hidden />
      ) : null}
      {children}
    </button>
  )
  if (!disabledReason) return button
  return (
    <span className="group/tip relative inline-flex">
      {button}
      <TipBubble id={tipId} align={tipAlign}>{disabledReason}</TipBubble>
    </span>
  )
})

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: LucideIcon
  /** Accessible name, also shown as a tooltip. Required: an icon alone has no name. */
  label: string
  variant?: Exclude<ButtonVariant, 'warn-outline'>
  size?: ButtonSize
  disabledReason?: string
  tipSide?: 'top' | 'bottom'
  tipAlign?: 'center' | 'start' | 'end'
}

const SQUARE: Record<ButtonSize, string> = { sm: 'h-7 w-7 coarse:h-9 coarse:w-9', md: 'h-8 w-8 coarse:h-10 coarse:w-10' }

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  {
    icon: Icon, label, variant = 'ghost', size = 'md', disabledReason, tipSide = 'top', tipAlign = 'center',
    disabled, type = 'button', className, onClick, 'aria-describedby': describedBy, ...rest
  },
  ref,
) {
  const tipId = useId()
  return (
    <span className="group/tip relative inline-flex">
      <button
        ref={ref}
        type={type}
        aria-label={label}
        disabled={disabled && !disabledReason}
        aria-disabled={disabledReason ? true : undefined}
        aria-describedby={joinIds(describedBy, disabledReason && tipId)}
        onClick={(event) => {
          if (disabledReason) event.preventDefault()
          else onClick?.(event)
        }}
        className={cx(BASE, VARIANT[variant], SQUARE[size], 'px-0', className)}
        {...rest}
      >
        <Icon size={ICON_SIZE[size]} aria-hidden />
      </button>
      <TipBubble id={disabledReason ? tipId : undefined} side={tipSide} align={tipAlign} hidden={!disabledReason}>
        {disabledReason ? `${label}：${disabledReason}` : label}
      </TipBubble>
    </span>
  )
})
