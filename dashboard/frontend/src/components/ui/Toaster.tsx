import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { useToastStore, type ToastItem } from '@/lib/toast'
import { cx, focusRing } from './cx'

const ICON = {
  success: { Icon: CheckCircle2, className: 'text-accent' },
  info: { Icon: Info, className: 'text-fg-3' },
  warn: { Icon: AlertTriangle, className: 'text-warn' },
}

function ToastView({ toast }: { toast: ToastItem }) {
  const dismiss = useToastStore((state) => state.dismiss)
  const [paused, setPaused] = useState(false)
  const remaining = useRef(toast.duration)
  useEffect(() => {
    if (paused || toast.duration === 0) return
    const started = Date.now()
    const timer = window.setTimeout(() => dismiss(toast.id), remaining.current)
    return () => {
      window.clearTimeout(timer)
      remaining.current -= Date.now() - started
    }
  }, [paused, toast.id, toast.duration, dismiss])
  const { Icon, className } = ICON[toast.tone]
  return (
    <div
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      className="pointer-events-auto flex w-full max-w-[400px] animate-pop-in items-start gap-3 rounded-lg border border-line bg-surface py-3 pl-4 pr-2 text-body text-fg shadow-overlay motion-reduce:animate-none"
    >
      <Icon size={16} aria-hidden className={cx('mt-[3px] shrink-0', className)} />
      <p className="min-w-0 flex-1 break-words">{toast.message}</p>
      {toast.action && (
        <button
          type="button"
          onClick={() => {
            toast.action?.onClick()
            dismiss(toast.id)
          }}
          className={cx('-my-0.5 shrink-0 rounded-md px-2 py-0.5 font-medium text-accent hover:bg-accent/10', focusRing)}
        >
          {toast.action.label}
        </button>
      )}
      <button
        type="button"
        aria-label="关闭通知"
        onClick={() => dismiss(toast.id)}
        className={cx('-my-0.5 shrink-0 rounded-md p-1 text-fg-3 hover:bg-fg/[.06] hover:text-fg', focusRing)}
      >
        <X size={14} aria-hidden />
      </button>
    </div>
  )
}

/**
 * Where toasts appear (bottom right; bottom centre on phones). Render once at the app root; while a
 * modal dialog is open the toasts move into it so they are not hidden behind its backdrop.
 */
export function Toaster() {
  const toasts = useToastStore((state) => state.toasts)
  const host = useToastStore((state) => state.hosts[state.hosts.length - 1])
  const region = (
    <section
      aria-label="通知"
      aria-live="polite"
      aria-relevant="additions text"
      className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex flex-col items-center gap-2 p-4 sm:items-end sm:p-6"
    >
      {toasts.map((item) => <ToastView key={item.id} toast={item} />)}
    </section>
  )
  return host ? createPortal(region, host) : region
}
