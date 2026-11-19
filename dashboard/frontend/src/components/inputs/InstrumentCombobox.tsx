// The search field behind TickerInput and the header's global search (ARIA combobox with a listbox):
// type a code or a name, pick with the mouse or with ↑ ↓ Enter, Esc closes the list. Nothing is searched
// while an input method is composing. Results read `名称 代码 · 市场 · 币种` with quiet 持仓 / 自选 tags;
// codes the ledger cannot hold are listed disabled with their reason; a letter symbol on two exchanges
// is picked once and the choice remembered (POST /api/instruments/aliases, only where writes are allowed;
// a refused write forgets it again). Under the rows the answer's own notices are shown for every query, the
// empty one included, and for cached answers: sources the search could not read (`omitted`: 持仓暂时读取不到…)
// and the name index's state (`index_reason`).
//
// Only what is on screen is taken (lib/instrument.ts `enterAction`, `typedChoice`): the highlight belongs
// to the rows it was made on (a new answer drops it), Enter on rows of the previous text waits and then
// takes only a complete EXCH:CODE that is one listing, Esc cancels that, and leaving the field never
// guesses: only a complete EXCH:CODE that is one usable listing among the field's own (filtered) rows is
// taken, and only where the caller allows it (`resolveOnBlur`).
import {
  useEffect, useId, useImperativeHandle, useLayoutEffect, useRef, useState,
  type CSSProperties, type KeyboardEvent, type ReactNode, type Ref,
} from 'react'
import { createPortal } from 'react-dom'
import { Info } from 'lucide-react'
import { Badge, Input, cx, numeric } from '@/components/ui'
import {
  applyChosenExchanges, arrangeResults, codeOf, describeResult, enterAction, inferredResults, isExchangeChoice, moveActive,
  normalizeQuery, optionKey, optionsOf, qualifiedInput, typedChoice, type InstrumentResult,
} from '@/lib/instrument'
import { useAppMode } from '@/components/shell/useAppMode'
import { rememberInstrument } from '@/services/instruments'
import { pickerNotices } from './pickerNotices'
import { clearInstrumentCache, searchOnce, useInstrumentSearch } from './useInstrumentSearch'

export interface InstrumentComboboxProps {
  value: string
  onValueChange: (value: string) => void
  /** A usable result was chosen (exchange choices already remembered). */
  onPick: (choice: InstrumentResult) => void
  filter?: (choice: InstrumentResult) => boolean
  /**
   * A complete EXCH:CODE that is exactly one usable listing among the field's rows is taken when the field
   * loses focus (form fields). Anything else typed stays text and nothing is picked.
   */
  resolveOnBlur?: boolean
  /** Enter with nothing highlighted takes the first usable result (the global search). */
  pickFirstOnEnter?: boolean
  /** Esc on a closed list: e.g. clear the global search. */
  onEscape?: () => void
  inputRef?: Ref<HTMLInputElement>
  id?: string
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
  autoFocus?: boolean
  ariaLabel?: string
  size?: 'sm' | 'md'
  prefix?: ReactNode
  suffix?: ReactNode
  className?: string
  /** The list is at least this wide (the header's search field is narrower than its results). */
  listMinWidth?: number
  /** Which edge of the field the list lines up with. */
  align?: 'start' | 'end'
}

/** Exchanges picked for letter symbols during this page's life (symbol → ticker). */
const chosenExchanges = new Map<string, string>()

interface Placement {
  left: number
  width: number
  maxHeight: number
  top?: number
  bottom?: number
}

function OptionRow({
  result, query, id, active, onHover, onChoose,
}: { result: InstrumentResult; query: string; id: string; active: boolean; onHover: () => void; onChoose: () => void }) {
  const display = describeResult(result, query)
  const choice = isExchangeChoice(result)
  return (
    <li
      id={id}
      role="option"
      aria-selected={active}
      aria-disabled={display.disabled || undefined}
      onMouseMove={onHover}
      onClick={onChoose}
      className={cx(
        'flex flex-col gap-0.5 px-3 py-1.5 coarse:py-2.5',
        display.disabled ? 'cursor-not-allowed' : 'cursor-pointer',
        active && 'bg-surface-2',
      )}
    >
      <span className="flex min-w-0 items-baseline gap-2">
        <span className={cx('min-w-0 truncate text-body', display.disabled ? 'text-fg-3' : 'font-medium text-fg')}>{display.title}</span>
        <span className={cx('min-w-0 shrink truncate text-table text-fg-3', numeric)}>{display.meta}</span>
        {display.tags.length > 0 && (
          <span className="ml-auto flex shrink-0 gap-1">
            {display.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}
          </span>
        )}
      </span>
      {display.note && !choice && (
        <span className={cx('text-caption', display.disabled ? 'text-fg-3' : 'text-warn')}>{display.note}</span>
      )}
    </li>
  )
}

export function InstrumentCombobox({
  value, onValueChange, onPick, filter, resolveOnBlur = false, pickFirstOnEnter = false, onEscape, inputRef,
  id, placeholder, disabled, invalid, autoFocus, ariaLabel, size = 'md', prefix, suffix, className, listMinWidth = 0,
  align = 'start',
}: InstrumentComboboxProps) {
  const baseId = useId()
  const listId = `${baseId}-list`
  const anchor = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  useImperativeHandle(inputRef, () => input.current as HTMLInputElement)
  const [focused, setFocused] = useState(false)
  const [composing, setComposing] = useState(false)
  const composingRef = useRef(false)
  const [open, setOpen] = useState(false)
  /** The highlighted row: its listing key and the rows (answer) it was highlighted on. */
  const [highlight, setHighlight] = useState<{ rows: string; key: string } | null>(null)
  const [placement, setPlacement] = useState<Placement | null>(null)
  const picked = useRef<string | null>(null)
  const latestValue = useRef(value)
  latestValue.current = value
  const { readOnlyReason } = useAppMode()

  const query = focused && !composing ? normalizeQuery(value) : null
  const search = useInstrumentSearch(query)
  const results = applyChosenExchanges(search.error ? inferredResults(value) : search.data?.results ?? [], chosenExchanges)
  const items = arrangeResults(results, filter)
  const options = optionsOf(items)
  const shownQuery = search.query ?? ''
  // The list on screen answers what is in the field now (not the previous text, still shown while typing).
  const fresh = search.query !== null && search.query === normalizeQuery(value) && !search.loading
  /** Which answer the rows on screen are: a highlight made on other rows is not carried over. */
  const rowsId = search.error ? `inferred:${normalizeQuery(value)}` : `answer:${search.query ?? ''}`
  const active = highlight && highlight.rows === rowsId ? options.findIndex((option) => optionKey(option) === highlight.key) : -1
  const highlightAt = (index: number) => setHighlight(index >= 0 && options[index] ? { rows: rowsId, key: optionKey(options[index]) } : null)
  /** Enter pressed before the answer for the current text arrived: act on it when it does. */
  const pendingEnter = useRef<string | null>(null)

  useEffect(() => {
    if (open && active >= 0) document.getElementById(`${baseId}-opt-${active}`)?.scrollIntoView({ block: 'nearest' })
  }, [open, active, baseId])

  useLayoutEffect(() => {
    if (!open) {
      setPlacement(null)
      return
    }
    const place = () => {
      const box = anchor.current?.getBoundingClientRect()
      if (!box) return
      const width = Math.min(Math.max(box.width, listMinWidth), window.innerWidth - 16)
      const left = Math.max(8, Math.min(align === 'end' ? box.right - width : box.left, window.innerWidth - 8 - width))
      const below = window.innerHeight - box.bottom - 12
      const above = box.top - 12
      if (below < 220 && above > below) setPlacement({ left, width, maxHeight: Math.min(380, above), bottom: window.innerHeight - box.top + 4 })
      else setPlacement({ left, width, maxHeight: Math.min(380, below), top: box.bottom + 4 })
    }
    // A dialog or drawer that is still animating in moves (and scales) the field, and while it has a
    // transform it holds position: fixed children: place the list once it has settled.
    let live = true
    const entering = anchor.current?.closest('dialog')?.getAnimations().filter((animation) => animation.playState === 'running') ?? []
    if (entering.length) {
      const later = () => { if (live) place() }
      void Promise.all(entering.map((animation) => animation.finished)).then(later, later)
    } else {
      place()
    }
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      live = false
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, align, listMinWidth])

  const choose = (result: InstrumentResult) => {
    if (!result.ticker || !result.supported) return
    let chosen = result
    if (isExchangeChoice(result)) {
      // Remembered on this page at once and by the backend for next time, only where writes are
      // allowed (not in the demo, not while the mode is unknown); a refused write forgets it again, so
      // the page keeps asking as the backend does.
      if (!readOnlyReason) {
        const symbol = codeOf(result.ticker).toUpperCase()
        const ticker = result.ticker
        chosenExchanges.set(symbol, ticker)
        void rememberInstrument({ ticker, symbol: codeOf(ticker) }).then(clearInstrumentCache, () => {
          if (chosenExchanges.get(symbol) === ticker) chosenExchanges.delete(symbol)
          clearInstrumentCache()
        })
      }
      chosen = { ...result, needs_confirmation: false, reason: null }
    }
    pendingEnter.current = null
    picked.current = chosen.ticker
    setOpen(false)
    setHighlight(null)
    onPick(chosen)
  }

  /** The rows the field would show for an answer: chosen exchanges and the field's filter applied. */
  const rowsOf = (answer: InstrumentResult[]) => optionsOf(arrangeResults(applyChosenExchanges(answer, chosenExchanges), filter))

  /**
   * The field lost focus with typed text: take it only when it is a complete EXCH:CODE that is one usable
   * listing among the field's rows; otherwise it stays as typed (a complete code in upper case).
   */
  const resolveTyped = (text: string) => {
    const qualified = qualifiedInput(text)
    if (!qualified || text === picked.current) return
    const normalise = () => {
      if (latestValue.current === text && qualified !== text) onValueChange(qualified)
    }
    const shown = search.query === normalizeQuery(text) && search.data ? search.data.results : null
    if (shown) {
      const match = typedChoice(text, rowsOf(shown))
      if (match) choose(match)
      else normalise()
      return
    }
    searchOnce(normalizeQuery(text)).then((data) => {
      if (latestValue.current !== text) return
      const found = typedChoice(text, rowsOf(data.results))
      if (found) choose(found)
      else normalise()
    }, normalise)
  }

  useEffect(() => {
    if (pendingEnter.current === null) return
    if (pendingEnter.current !== value || !focused) {
      pendingEnter.current = null
      return
    }
    if (!fresh) return
    pendingEnter.current = null
    // The answer came after Enter: the user saw none of these rows, so only the complete code typed.
    const choice = typedChoice(value, options)
    if (choice) choose(choice)
    // `choose` reads this render's values; only the answer arriving matters here.
  }, [fresh, value, focused])

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    // Focus given back by a closing dialog can arrive without a focus event React sees: a key press
    // in the field means it has focus.
    if (!focused) setFocused(true)
    if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault()
        if (!open) setOpen(true)
        highlightAt(moveActive(options.length, open ? active : -1, event.key))
        break
      case 'Enter': {
        if (!open && value === picked.current) break
        const action = enterAction({
          value, active: open && active >= 0 ? options[active] ?? null : null, fresh, options, pickFirst: pickFirstOnEnter,
        })
        if (action.kind === 'choose') {
          event.preventDefault()
          choose(action.result)
        } else if (action.kind === 'wait') {
          // The rows on screen are for the previous text: wait for this text's answer.
          event.preventDefault()
          pendingEnter.current = value
        } else if (open) {
          event.preventDefault()
        }
        break
      }
      case 'Escape':
        // A dismissed search takes nothing later, not even an Enter that was waiting for its answer.
        pendingEnter.current = null
        if (open) {
          // Handled here, so an enclosing dialog stays open.
          event.preventDefault()
          event.stopPropagation()
          setOpen(false)
          setHighlight(null)
        } else if (onEscape) {
          event.preventDefault()
          event.stopPropagation()
          onEscape()
        }
        break
      case 'Tab':
        setOpen(false)
        break
      default:
    }
  }

  const host = open && placement ? anchor.current?.closest('dialog') ?? document.body : null
  const loadingFirst = search.loading && !search.data && !search.error
  const notices = pickerNotices(search.data, shownQuery)
  const reason = notices.indexReason
  const style: CSSProperties | undefined = placement
    ? { position: 'fixed', left: placement.left, width: placement.width, maxHeight: placement.maxHeight, top: placement.top, bottom: placement.bottom }
    : undefined

  let optionIndex = -1
  const listbox = host && createPortal(
    <div
      ref={list}
      style={style}
      // Clicks inside the list keep the focus in the field.
      onMouseDown={(event) => event.preventDefault()}
      className="z-50 flex animate-pop-in flex-col overflow-hidden rounded-lg border border-line bg-surface text-fg shadow-overlay motion-reduce:animate-none"
    >
      {!shownQuery && options.length > 0 && <p className="px-3 pb-1 pt-2 text-caption text-fg-3">持仓和自选</p>}
      {search.error && (
        <p className="px-3 pt-2 text-caption text-warn">搜索暂时不可用：{search.error}{results.length ? '；按代码规则列出' : ''}</p>
      )}
      <ul id={listId} role="listbox" aria-label="代码搜索结果" className="min-h-0 flex-1 overflow-y-auto py-1">
        {items.map((item) => {
          if (item.type === 'heading') {
            return <li key={item.key} role="presentation" className="px-3 pb-0.5 pt-2 text-caption text-fg-3">{item.text}</li>
          }
          optionIndex += 1
          const index = optionIndex
          return (
            <OptionRow
              key={item.key}
              result={item.result}
              query={value}
              id={`${baseId}-opt-${index}`}
              active={index === active}
              onHover={() => highlightAt(index)}
              onChoose={() => choose(item.result)}
            />
          )
        })}
        {loadingFirst && <li role="presentation" className="px-3 py-2 text-table text-fg-3">正在搜索…</li>}
        {!loadingFirst && !search.loading && options.length === 0 && !search.error && (
          <li role="presentation" className="px-3 py-2 text-table text-fg-3">
            {notices.emptyText}
          </li>
        )}
      </ul>
      {notices.omitted.length > 0 && (
        <div role="note" aria-label="未读到的来源" className="border-t border-line px-3 py-2 text-caption text-warn">
          {notices.omitted.map((text) => <p key={text}>{text}</p>)}
        </div>
      )}
      {reason && (
        <p className="flex items-start gap-1.5 border-t border-line px-3 py-2 text-caption text-fg-3">
          <Info size={12} aria-hidden className="mt-0.5 shrink-0" />
          <span>{reason}；按代码仍可查找。</span>
        </p>
      )}
    </div>,
    host,
  )

  return (
    <div ref={anchor} className={cx('relative min-w-0', className)}>
      <Input
        ref={input}
        id={id}
        size={size}
        prefix={prefix}
        suffix={suffix}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && active >= 0 ? `${baseId}-opt-${active}` : undefined}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="search"
        onChange={(event) => {
          onValueChange(event.target.value)
          if (!focused) setFocused(true)
          if (!composingRef.current) {
            setOpen(true)
            setHighlight(null)
          }
        }}
        onCompositionStart={() => {
          composingRef.current = true
          setComposing(true)
        }}
        onCompositionEnd={() => {
          composingRef.current = false
          setComposing(false)
          setOpen(true)
          setHighlight(null)
        }}
        onFocus={() => {
          setFocused(true)
          if (!value.trim()) setOpen(true)
        }}
        onClick={() => {
          if (!focused) setFocused(true)
          if (!open && value !== picked.current) setOpen(true)
        }}
        onBlur={(event) => {
          if (list.current?.contains(event.relatedTarget as Node | null)) return
          setFocused(false)
          setOpen(false)
          setHighlight(null)
          pendingEnter.current = null
          if (resolveOnBlur) resolveTyped(value)
        }}
        onKeyDown={onKeyDown}
      />
      {/* A polite live region without role="status": it stays mounted (so the count is announced) and is
          not mistaken for the page's own status messages. */}
      <span aria-live="polite" aria-atomic="true" className="sr-only">
        {open && !search.loading && query !== null ? (options.length ? `${options.length} 个结果` : '没有结果') : ''}
      </span>
      {listbox}
    </div>
  )
}
