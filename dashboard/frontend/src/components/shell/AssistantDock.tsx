// The assistant, docked on the right: the existing ChatPanel, unchanged inside. From 1440 px it takes its
// own column and pushes the page; below that it lies over the page; on phones it fills the screen. It
// stays mounted once opened, so a reply that is streaming keeps going while the dock is closed.
import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { IconButton, Skeleton, cx, useMediaQuery } from '@/components/ui'
import { firstFocusable, focusables } from '@/components/ui/hooks'
import { useShell } from './shellStore'

// Loaded when the dock first opens: the chat panel brings the Markdown renderer with it.
const ChatPanel = lazy(() => import('@/components/ChatPanel'))

/** Puts `text` in the chat input as if typed (ChatPanel keeps its input in React state). */
function prefill(root: HTMLElement | null, text: string) {
  const input = root?.querySelector<HTMLInputElement>('form input[type="text"]')
  if (!input || input.disabled) return false
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, text)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.focus()
  input.setSelectionRange(text.length, text.length)
  return true
}

/** The chat input when it can take text (the panel may still be loading or restoring a conversation). */
function chatInput(root: HTMLElement | null): HTMLInputElement | null {
  const input = root?.querySelector<HTMLInputElement>('form input[type="text"]')
  return input && !input.disabled ? input : null
}

/**
 * Focus: every opening moves it into the dock (the chat input once it can take text, the close button
 * until then); on phones the dock fills the screen, so Tab cycles inside it; closing (Esc, ×, the toggle)
 * gives focus back to where it was when the dock opened.
 */
export function AssistantDock() {
  const open = useShell((state) => state.assistantOpen)
  const text = useShell((state) => state.assistantPrefill)
  const prefillId = useShell((state) => state.prefillId)
  const close = useShell((state) => state.closeAssistant)
  const wide = useMediaQuery('(min-width: 1440px)')
  const phone = useMediaQuery('(max-width: 767px)')
  const [mounted, setMounted] = useState(open)
  const root = useRef<HTMLElement>(null)
  const opener = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (open) {
      const active = document.activeElement
      if (active instanceof HTMLElement && !root.current?.contains(active)) opener.current = active
      setMounted(true)
      return
    }
    // Closed: focus that was in the dock (now hidden, so on <body>) goes back to the opener.
    const active = document.activeElement
    const target = opener.current
    if (target?.isConnected && (!active || active === document.body || root.current?.contains(active))) target.focus()
  }, [open])

  // Into the dock on every opening. A row's 问助手 also puts "关于 名称(代码)：" in the input; the panel may
  // still be loading or restoring the last conversation (its input disabled meanwhile): retry for a while.
  useEffect(() => {
    if (!open || !mounted) return
    let attempts = 0
    let timer = 0
    const start = () => {
      if (root.current?.contains(document.activeElement)) return
      const button = focusables(root.current).find((element) => element.getAttribute('aria-label') === '关闭助手')
      ;(button ?? root.current)?.focus()
    }
    const tryFill = () => {
      if (!root.current) return
      if (text) {
        if (prefill(root.current, text)) return
      } else {
        const input = chatInput(root.current)
        const parked = !root.current.contains(document.activeElement) || document.activeElement?.getAttribute('aria-label') === '关闭助手'
        if (input) {
          if (parked) input.focus()
          return
        }
      }
      if (++attempts > 50) return
      timer = window.setTimeout(tryFill, 100)
    }
    start()
    timer = window.setTimeout(tryFill, 0)
    return () => window.clearTimeout(timer)
  }, [open, mounted, text, prefillId])

  // Esc closes the dock when it lies over the page (focus then goes back, see above).
  useEffect(() => {
    if (!open || wide) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('dialog[open]')) return
      if (!root.current?.contains(document.activeElement)) return
      close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, wide, close])

  // Phones: the dock covers the page, so focus stays inside it.
  useEffect(() => {
    if (!open || !phone) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || document.querySelector('dialog[open]')) return
      const items = focusables(root.current)
      if (!items.length) {
        event.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (!root.current?.contains(active)) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }
    const onFocusIn = (event: FocusEvent) => {
      if (document.querySelector('dialog[open]')) return
      if (event.target instanceof Node && !root.current?.contains(event.target)) firstFocusable(root.current)?.focus()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('focusin', onFocusIn)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('focusin', onFocusIn)
    }
  }, [open, phone])

  if (!mounted) return null
  return (
    <aside
      ref={root}
      aria-label="助手"
      // A target for focus while the panel loads.
      tabIndex={-1}
      hidden={!open}
      className={cx(
        'flex flex-col bg-surface text-fg outline-none',
        // Phones: a full-screen sheet. Tablets and laptops: over the page. From 1440 px: its own column.
        'max-md:fixed max-md:inset-0 max-md:z-50',
        wide ? 'w-[400px] shrink-0 border-l border-line' : 'md:fixed md:bottom-0 md:right-0 md:top-12 md:z-40 md:w-[400px] md:border-l md:border-line md:shadow-overlay',
        !open && 'hidden',
      )}
    >
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line pl-4 pr-2 md:hidden">
        <h2 className="text-title">助手</h2>
        <IconButton icon={X} label="关闭助手" onClick={close} tipAlign="end" tipSide="bottom" />
      </div>
      <div className="hidden h-10 shrink-0 items-center justify-between border-b border-line pl-4 pr-2 md:flex">
        <h2 className="text-table font-medium text-fg-2">助手</h2>
        <IconButton icon={X} label="关闭助手" size="sm" onClick={close} tipAlign="end" tipSide="bottom" />
      </div>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<div className="p-4" aria-busy="true"><Skeleton lines={3} /></div>}>
          <ChatPanel />
        </Suspense>
      </div>
    </aside>
  )
}
