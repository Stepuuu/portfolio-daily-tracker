// Ticker field with code / name search (plan EN-2): type 茅台, 600519 or 00700 and pick; the value
// becomes the ledger ticker (SHA:600519) and `onSelect` receives the whole result (name, exchange,
// currency, kind). Behaviour:
// - the list opens on focus when the field is empty (holdings first, then the watchlist) and as you type;
//   ↑ ↓ move, Enter picks, Esc closes the list (not the dialog around it), Tab leaves;
// - nothing is searched while an input method is composing;
// - codes the ledger cannot hold are listed disabled with their reason; a letter symbol on both NASDAQ
//   and NYSE is picked once and the choice remembered;
// - Enter takes the highlighted row, or the code typed when it is exactly one listing on screen;
// - a complete `exch:code` (`sha:600519`) that is exactly one usable listing among the field's rows is
//   taken when the field loses focus, as if picked (`onSelect` is called), unless `resolveOnBlur` is off
//   (a field whose pick writes at once, like the watchlist's add box); one the search does not know is
//   only put in upper case. Anything else typed (a name, a bare code such as 600519 or 60) stays text and
//   nothing is selected: the form decides what to say.
import { forwardRef, useState } from 'react'
import type { InstrumentResult } from '@/services/instruments'
import { InstrumentCombobox } from './InstrumentCombobox'

export interface TickerInputProps {
  /** The text in the field: a ledger ticker (SHA:600519) once chosen, otherwise what the user typed. */
  value: string
  onChange: (value: string) => void
  /** Called when the user picks a search result; `value` is then that result's ticker. */
  onSelect?: (choice: InstrumentResult) => void
  /** Limit the choices, e.g. only what an account holds when selling. */
  filter?: (choice: InstrumentResult) => boolean
  /** Take a complete typed EXCH:CODE when the field loses focus (default true). Off: only a pick or Enter selects. */
  resolveOnBlur?: boolean
  placeholder?: string
  id?: string
  disabled?: boolean
  invalid?: boolean
  autoFocus?: boolean
  /** Show the picked instrument's name inside the field, after the ticker. Default false. */
  showName?: boolean
  /** Accessible name when the field has no visible label (outside a Field). */
  'aria-label'?: string
  size?: 'sm' | 'md'
  className?: string
}

export const TickerInput = forwardRef<HTMLInputElement, TickerInputProps>(function TickerInput(
  {
    value, onChange, onSelect, filter, resolveOnBlur = true, placeholder = '代码或名称，如 600519 / 茅台', id, disabled, invalid,
    autoFocus, showName = false, 'aria-label': ariaLabel, size, className,
  },
  ref,
) {
  const [chosen, setChosen] = useState<{ ticker: string; name: string | null } | null>(null)
  const name = showName && chosen && chosen.ticker === value && chosen.name && chosen.name !== value ? chosen.name : null
  return (
    <InstrumentCombobox
      inputRef={ref}
      value={value}
      onValueChange={onChange}
      onPick={(choice) => {
        setChosen({ ticker: choice.ticker!, name: choice.name })
        onChange(choice.ticker!)
        onSelect?.(choice)
      }}
      filter={filter}
      resolveOnBlur={resolveOnBlur}
      id={id}
      placeholder={placeholder}
      disabled={disabled}
      invalid={invalid}
      autoFocus={autoFocus}
      ariaLabel={ariaLabel}
      size={size}
      className={className}
      listMinWidth={320}
      // Always a suffix element when names are shown, so the field is not remounted (and blurred) when one appears.
      suffix={showName ? <span className="max-w-[10rem] truncate text-table">{name}</span> : undefined}
    />
  )
})
