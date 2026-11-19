import {
  forwardRef, useEffect, useImperativeHandle, useRef, useState,
  type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes,
} from 'react'
import { Check, ChevronDown, Minus } from 'lucide-react'
import { currencySymbol, parseNumberInput, stepNumberInput, type Currency } from '@/lib/format'
import { Button } from './Button'
import { cx, focusRing, numeric } from './cx'
import { useFieldControl, useFieldReport } from './Field'

// Control outline: `line-input` is at least 3:1 against every surface (tests/tokens.test.mjs).
const FRAME =
  'rounded-md border border-line-input bg-surface text-fg transition-colors duration-[120ms] hover:border-fg-3 [color-scheme:var(--scheme)]'
const CONTROL = cx(
  FRAME,
  'placeholder:text-fg-3 focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent',
  'aria-[invalid=true]:border-warn aria-[invalid=true]:focus:border-warn aria-[invalid=true]:focus:ring-warn',
  'disabled:cursor-not-allowed disabled:border-line disabled:bg-surface-2 disabled:text-fg-3 read-only:bg-surface-2',
)
const HEIGHT = { sm: 'h-7 text-table', md: 'h-8 text-body coarse:h-10' } as const

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'prefix' | 'size'> {
  /** Text or icon inside the field, before the value (e.g. "¥"). */
  prefix?: ReactNode
  /** Text inside the field, after the value (e.g. "股", "%"). */
  suffix?: ReactNode
  size?: 'sm' | 'md'
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { prefix, suffix, size = 'md', className, ...props },
  ref,
) {
  const inner = useRef<HTMLInputElement>(null)
  useImperativeHandle(ref, () => inner.current as HTMLInputElement)
  const wired = useFieldControl(props)
  if (!prefix && !suffix) {
    return <input ref={inner} className={cx(CONTROL, HEIGHT[size], 'w-full min-w-0 px-3', className)} {...wired} />
  }
  const invalid = wired['aria-invalid'] === true || wired['aria-invalid'] === 'true'
  return (
    <div
      data-invalid={invalid || undefined}
      // Clicking the prefix or suffix focuses the field.
      onMouseDown={(event) => {
        if (event.target !== inner.current) {
          event.preventDefault()
          inner.current?.focus()
        }
      }}
      className={cx(
        FRAME, HEIGHT[size], 'flex w-full min-w-0 cursor-text items-center',
        'focus-within:border-accent focus-within:ring-1 focus-within:ring-accent',
        'data-[invalid]:border-warn data-[invalid]:focus-within:ring-warn',
        'has-[:disabled]:cursor-not-allowed has-[:disabled]:border-line has-[:disabled]:bg-surface-2',
        className,
      )}
    >
      {prefix && <span className="shrink-0 select-none pl-3 text-fg-3">{prefix}</span>}
      <input
        ref={inner}
        className={cx(
          'h-full w-full min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-fg-3 disabled:cursor-not-allowed disabled:text-fg-3',
          prefix ? 'pl-1' : 'pl-3',
          suffix ? 'pr-1' : 'pr-3',
        )}
        {...wired}
      />
      {suffix && <span className="shrink-0 select-none pr-3 text-fg-3">{suffix}</span>}
    </div>
  )
})

export interface NumberInputProps
  extends Omit<InputProps, 'type' | 'value' | 'defaultValue' | 'onChange' | 'step' | 'min' | 'max'> {
  /** The decimal string; '' when empty. The field never turns it into a binary number. */
  value: string
  onValueChange: (value: string) => void
  /** Arrow up / down (and the stepper buttons) add or subtract this. */
  step?: number | string
  min?: number
  allowNegative?: boolean
  /** Show "-step" / "+step" buttons after the field, e.g. lots of 100 for A-shares. */
  stepButtons?: boolean
}

/**
 * Numeric text field: keeps the exact decimal string (use instead of type="number"). What is typed or
 * pasted is read by the one convention of `parseNumberInput` (full-width digits, "," only as thousands
 * separators); anything ambiguous, such as a decimal comma, is refused: the value stays as it was and the
 * reason shows as the Field's error (or under the field outside a Field) until the next accepted change.
 */
export const NumberInput = forwardRef<HTMLInputElement, NumberInputProps>(function NumberInput(
  { value, onValueChange, step, min, allowNegative = false, stepButtons = false, className, onKeyDown, ...props },
  ref,
) {
  const report = useFieldReport()
  const [refusal, setRefusal] = useState<string | null>(null)
  // A new value (typed, stepped or set by the page) ends the refusal of an earlier attempt.
  useEffect(() => setRefusal(null), [value])
  useEffect(() => {
    if (!report) return
    report(refusal)
    return () => report(null)
  }, [report, refusal])
  const stepBy = (direction: 1 | -1) => {
    if (step !== undefined) onValueChange(stepNumberInput(value, step, direction, min ?? (allowNegative ? undefined : 0)))
  }
  const field = (
    <Input
      ref={ref}
      inputMode="decimal"
      autoComplete="off"
      spellCheck={false}
      value={value}
      onChange={(event) => {
        const result = parseNumberInput(event.target.value, { allowNegative })
        if (result.ok) {
          setRefusal(null)
          onValueChange(result.value)
        } else {
          setRefusal(result.error)
        }
      }}
      onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
        onKeyDown?.(event)
        if (event.defaultPrevented || step === undefined) return
        if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
          event.preventDefault()
          stepBy(event.key === 'ArrowUp' ? 1 : -1)
        }
      }}
      className={cx(numeric, !stepButtons && className)}
      {...props}
      aria-invalid={refusal ? true : props['aria-invalid']}
    />
  )
  // Inside a Field the Field shows the refusal; on its own the field says it underneath.
  const message = refusal && !report ? <p role="alert" className="mt-1 text-table text-warn">{refusal}</p> : null
  if (!stepButtons || step === undefined) return <>{field}{message}</>
  return (
    <>
      <div className={cx('flex min-w-0 items-center gap-2', className)}>
        {field}
        <Button size="md" className="px-2" onClick={() => stepBy(-1)} disabled={props.disabled}>-{step}</Button>
        <Button size="md" className="px-2" onClick={() => stepBy(1)} disabled={props.disabled}>+{step}</Button>
      </div>
      {message}
    </>
  )
})

export interface MoneyInputProps extends Omit<NumberInputProps, 'prefix'> {
  currency: Currency | string
}

/** An amount in one currency; the symbol sits inside the field. */
export const MoneyInput = forwardRef<HTMLInputElement, MoneyInputProps>(function MoneyInput({ currency, ...props }, ref) {
  return <NumberInput ref={ref} prefix={currencySymbol(currency)} {...props} />
})

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  options?: SelectOption[]
  /** Shown while nothing is chosen (value ''). */
  placeholder?: string
  size?: 'sm' | 'md'
}

/** Native select (keyboard and mobile pickers for free) with the kit's look. */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { options, placeholder, size = 'md', className, children, ...props },
  ref,
) {
  const wired = useFieldControl(props)
  return (
    <div className={cx('relative w-full min-w-0', className)}>
      <select ref={ref} className={cx(CONTROL, HEIGHT[size], 'w-full min-w-0 cursor-pointer appearance-none pl-3 pr-8')} {...wired}>
        {placeholder !== undefined && <option value="" disabled>{placeholder}</option>}
        {options?.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>
        ))}
        {children}
      </select>
      <ChevronDown size={16} aria-hidden className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-fg-3" />
    </div>
  )
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, rows = 3, ...props },
  ref,
) {
  const wired = useFieldControl(props)
  return <textarea ref={ref} rows={rows} className={cx(CONTROL, 'block w-full min-w-0 resize-y px-3 py-2 text-body', className)} {...wired} />
})

/** Native date field (yyyy-mm-dd value) with the theme's picker. */
export const DateInput = forwardRef<HTMLInputElement, Omit<InputProps, 'type' | 'prefix' | 'suffix'>>(function DateInput(
  { className, ...props },
  ref,
) {
  return <Input ref={ref} type="date" className={cx(numeric, className)} {...props} />
})

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'size'> {
  /** Visible label; without one, pass aria-label. */
  label?: ReactNode
  description?: ReactNode
  /** "Some selected" state for a select-all box. */
  indeterminate?: boolean
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
  { label, description, indeterminate = false, className, disabled, ...props },
  ref,
) {
  const inner = useRef<HTMLInputElement>(null)
  useImperativeHandle(ref, () => inner.current as HTMLInputElement)
  useEffect(() => {
    if (inner.current) inner.current.indeterminate = indeterminate
  }, [indeterminate])
  const box = (
    <span className="relative inline-flex h-4 w-4 shrink-0">
      <input
        ref={inner}
        type="checkbox"
        disabled={disabled}
        className={cx(
          'peer h-4 w-4 cursor-pointer appearance-none rounded border border-line-input bg-surface transition-colors duration-[120ms]',
          'checked:border-accent checked:bg-accent indeterminate:border-accent indeterminate:bg-accent',
          'disabled:cursor-not-allowed disabled:opacity-50',
          focusRing,
        )}
        {...props}
      />
      {indeterminate ? (
        <Minus size={12} strokeWidth={3} aria-hidden className="pointer-events-none absolute left-0.5 top-0.5 text-accent-fg" />
      ) : (
        <Check size={12} strokeWidth={3} aria-hidden className="pointer-events-none absolute left-0.5 top-0.5 hidden text-accent-fg peer-checked:block" />
      )}
    </span>
  )
  if (!label) return <span className={cx('inline-flex', className)}>{box}</span>
  return (
    <label className={cx('inline-flex min-w-0 items-start gap-2 text-body text-fg', disabled ? 'cursor-not-allowed text-fg-3' : 'cursor-pointer', className)}>
      <span className="flex h-[22px] items-center">{box}</span>
      <span className="min-w-0">
        {label}
        {description && <span className="block text-table text-fg-3">{description}</span>}
      </span>
    </label>
  )
})
