import { useId, useLayoutEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { useToastStore } from '@/lib/toast'
import { IconButton } from './Button'
import { cx } from './cx'
import { firstFocusable, useLatest } from './hooks'

export type DialogSize = 'sm' | 'md' | 'lg' | 'sheet'

export interface DialogProps {
  open: boolean
  /** Called on Esc, the close button and (unless disabled) a click on the backdrop. */
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  /** sm 400 px (confirmations), md 560 px (forms), lg 800 px (reviews), sheet = right drawer. */
  size?: DialogSize
  /** Buttons, primary last; right-aligned and always visible. */
  footer?: ReactNode
  /** Default true. Turn off for forms where a stray click would lose typing. */
  closeOnBackdrop?: boolean
  className?: string
  children: ReactNode
}

const SIZE: Record<DialogSize, string> = {
  sm: 'max-w-[400px]',
  md: 'max-w-[560px] max-sm:m-0 max-sm:h-[100dvh] max-sm:max-h-none max-sm:w-full max-sm:max-w-none max-sm:rounded-none max-sm:border-0',
  lg: 'max-w-[800px] max-sm:m-0 max-sm:h-[100dvh] max-sm:max-h-none max-sm:w-full max-sm:max-w-none max-sm:rounded-none max-sm:border-0',
  sheet:
    'my-0 ml-auto mr-0 h-[100dvh] max-h-none w-full max-w-[480px] animate-slide-in rounded-none border-y-0 border-r-0 max-sm:max-w-none max-sm:border-0',
}

let scrollLocks = 0

function lockScroll() {
  scrollLocks += 1
  if (scrollLocks === 1) document.documentElement.style.overflow = 'hidden'
}

function unlockScroll() {
  scrollLocks = Math.max(0, scrollLocks - 1)
  if (scrollLocks === 0) document.documentElement.style.overflow = ''
}

/**
 * Modal dialog on the native <dialog>: focus is kept inside, Esc closes, and focus returns to the
 * element that opened it. Content mounts on open, so forms start fresh each time. Focus starts on the
 * first field (or the element with `data-autofocus`; give a confirmation's 取消 button that), else on
 * the dialog itself. On phones md/lg/sheet fill the screen.
 */
export function Dialog({
  open, onClose, title, description, size = 'md', footer, closeOnBackdrop = true, className, children,
}: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const returnFocus = useRef<HTMLElement | null>(null)
  const pressedOnBackdrop = useRef(false)
  const titleId = useId()
  const descriptionId = useId()
  const close = useLatest(onClose)

  // Layout effect: open and close before paint, so an empty dialog never flashes.
  useLayoutEffect(() => {
    const dialog = ref.current
    if (!dialog || !open) return
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    if (!dialog.open) dialog.showModal()
    lockScroll()
    const { pushHost, popHost } = useToastStore.getState()
    pushHost(dialog)
    // First field of the body; otherwise the dialog itself, so screen readers start at the title.
    // Confirmations mark their safe button (取消) with data-autofocus.
    const target = dialog.querySelector<HTMLElement>('[data-autofocus]') ?? firstFocusable(bodyRef.current) ?? dialog
    target.focus()
    return () => {
      popHost(dialog)
      unlockScroll()
      if (dialog.open) dialog.close()
      const back = returnFocus.current
      if (back?.isConnected) back.focus()
    }
  }, [open])

  return (
    <dialog
      ref={ref}
      tabIndex={-1}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={(event) => {
        // Esc: let the owner decide (it sets open=false), so state and DOM stay in step.
        event.preventDefault()
        close.current()
      }}
      onMouseDown={(event) => {
        pressedOnBackdrop.current = event.target === event.currentTarget
      }}
      onClick={(event) => {
        if (closeOnBackdrop && pressedOnBackdrop.current && event.target === event.currentTarget) close.current()
        pressedOnBackdrop.current = false
      }}
      className={cx(
        'm-auto max-h-[calc(100dvh-32px)] w-[calc(100%-32px)] flex-col overflow-hidden rounded-lg border border-line bg-surface p-0 text-fg shadow-overlay outline-none open:flex',
        'animate-pop-in backdrop:bg-[var(--backdrop)] motion-reduce:animate-none',
        SIZE[size],
        className,
      )}
    >
      {open && (
        <>
          <header className="flex shrink-0 items-start gap-3 px-4 pb-2 pt-4 sm:px-6 sm:pt-6">
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="text-title text-fg">{title}</h2>
              {description && <p id={descriptionId} className="mt-1 text-table text-fg-2">{description}</p>}
            </div>
            <IconButton icon={X} label="关闭" size="sm" tipSide="bottom" tipAlign="end" onClick={() => close.current()} className="-mr-2 -mt-1" />
          </header>
          <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">{children}</div>
          {footer && (
            <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3 sm:px-6">
              {footer}
            </footer>
          )}
        </>
      )}
    </dialog>
  )
}

/** A panel that slides in from the right (details, the pending tray). Same behaviour as Dialog. */
export function Drawer(props: Omit<DialogProps, 'size'>) {
  return <Dialog {...props} size="sheet" />
}
