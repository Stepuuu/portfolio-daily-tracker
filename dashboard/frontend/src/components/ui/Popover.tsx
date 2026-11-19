import {
  cloneElement, useCallback, useEffect, useId, useLayoutEffect, useRef, useState,
  type MouseEvent, type ReactElement, type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { cx } from './cx'
import { firstFocusable } from './hooks'

export interface PopoverProps {
  /** The button that opens it; it gets aria-expanded and aria-controls. */
  trigger: ReactElement<{ onClick?: (event: MouseEvent<HTMLElement>) => void }>
  /** Content, or a function that receives `close` (for menus and small forms). */
  children: ReactNode | ((close: () => void) => ReactNode)
  /** Horizontal alignment with the trigger. */
  align?: 'start' | 'center' | 'end'
  /** Accessible name of the panel when it has no heading. */
  label?: string
  /** Open on first render (previews, tests). */
  defaultOpen?: boolean
  /** Called when the panel opens or closes (not on the first render). */
  onOpenChange?: (open: boolean) => void
  className?: string
}

const GAP = 4
const MARGIN = 8

/**
 * A small non-modal panel anchored to its trigger (filters, a column menu, the notification list).
 * Closes on Esc (focus back to the trigger), an outside click, or when focus leaves it. Rendered in a
 * portal so table and panel edges never clip it; flips above the trigger near the bottom of the screen.
 */
export function Popover({ trigger, children, align = 'start', label, defaultOpen = false, onOpenChange, className }: PopoverProps) {
  const [open, setOpen] = useState(defaultOpen)
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null)
  const anchor = useRef<HTMLSpanElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const id = useId()
  const notify = useRef(onOpenChange)
  notify.current = onOpenChange
  const reported = useRef(open)

  useEffect(() => {
    if (reported.current === open) return
    reported.current = open
    notify.current?.(open)
  }, [open])

  const close = useCallback((refocus = false) => {
    setOpen(false)
    setPosition(null)
    if (refocus) firstFocusable(anchor.current)?.focus()
  }, [])

  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      if (!anchor.current || !panel.current) return
      const a = anchor.current.getBoundingClientRect()
      const p = panel.current.getBoundingClientRect()
      let top = a.bottom + GAP
      if (top + p.height > window.innerHeight - MARGIN && a.top - GAP - p.height >= MARGIN) top = a.top - GAP - p.height
      let left = align === 'start' ? a.left : align === 'end' ? a.right - p.width : a.left + (a.width - p.width) / 2
      left = Math.max(MARGIN, Math.min(left, window.innerWidth - MARGIN - p.width))
      setPosition({ top, left })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [open, align])

  useEffect(() => {
    if (!open) return
    ;(firstFocusable(panel.current) ?? panel.current)?.focus({ preventScroll: true })
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!panel.current?.contains(target) && !anchor.current?.contains(target)) close()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // Handled here, so an enclosing dialog stays open.
      event.preventDefault()
      event.stopPropagation()
      close(true)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown, true)
    }
  }, [open, close])

  const triggerElement = cloneElement(trigger, {
    'aria-expanded': open,
    'aria-haspopup': 'dialog',
    'aria-controls': open ? id : undefined,
    onClick: (event: MouseEvent<HTMLElement>) => {
      trigger.props.onClick?.(event)
      if (!event.defaultPrevented) setOpen((value) => !value)
    },
  } as Record<string, unknown>)

  // Inside a modal dialog the rest of the page is inert, so the panel must live in the dialog.
  const host = anchor.current?.closest('dialog') ?? (typeof document === 'undefined' ? null : document.body)

  return (
    <>
      <span ref={anchor} className="inline-flex">{triggerElement}</span>
      {open && host && createPortal(
        <div
          ref={panel}
          id={id}
          role="dialog"
          aria-label={label}
          tabIndex={-1}
          onBlur={(event) => {
            const next = event.relatedTarget as Node | null
            if (next && !panel.current?.contains(next) && !anchor.current?.contains(next)) close()
          }}
          style={{ position: 'fixed', top: position?.top ?? 0, left: position?.left ?? 0, opacity: position ? 1 : 0 }}
          className={cx(
            'z-50 min-w-[12rem] max-w-[calc(100vw-16px)] animate-pop-in rounded-lg border border-line bg-surface p-3 text-body text-fg shadow-overlay outline-none motion-reduce:animate-none',
            className,
          )}
        >
          {typeof children === 'function' ? children(() => close(true)) : children}
        </div>,
        host,
      )}
    </>
  )
}
