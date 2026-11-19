// Short confirmations and warnings ("已入账 1 笔 · 版本 12"). Render <Toaster /> once at the app root.
// Errors that need attention belong in the page (InlineAlert); a toast is for news the user can miss.
import { create } from 'zustand'

export type ToastTone = 'success' | 'warn' | 'info'

export interface ToastAction {
  label: string
  onClick: () => void
}

export interface ToastOptions {
  /** One button, e.g. 再记一笔 or 重试. */
  action?: ToastAction
  /** Milliseconds before it hides itself; 0 keeps it until dismissed. */
  duration?: number
}

export interface ToastItem {
  id: number
  tone: ToastTone
  message: string
  action?: ToastAction
  duration: number
}

interface ToastState {
  toasts: ToastItem[]
  /** Open modal dialogs, innermost last: toasts render inside it so they stay visible and usable. */
  hosts: HTMLElement[]
  add: (toast: ToastItem) => void
  dismiss: (id: number) => void
  pushHost: (host: HTMLElement) => void
  popHost: (host: HTMLElement) => void
}

const MAX_VISIBLE = 3
const DEFAULT_DURATION: Record<ToastTone, number> = { success: 4000, info: 5000, warn: 8000 }

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  hosts: [],
  add: (toast) => set((state) => ({ toasts: [...state.toasts, toast].slice(-MAX_VISIBLE) })),
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) })),
  pushHost: (host) => set((state) => ({ hosts: [...state.hosts.filter((item) => item !== host), host] })),
  popHost: (host) => set((state) => ({ hosts: state.hosts.filter((item) => item !== host) })),
}))

let nextId = 1

function show(tone: ToastTone, message: string, options: ToastOptions = {}): number {
  const id = nextId++
  const duration = options.duration ?? (options.action ? 10000 : DEFAULT_DURATION[tone])
  useToastStore.getState().add({ id, tone, message, action: options.action, duration })
  return id
}

export const toast = {
  success: (message: string, options?: ToastOptions) => show('success', message, options),
  warn: (message: string, options?: ToastOptions) => show('warn', message, options),
  info: (message: string, options?: ToastOptions) => show('info', message, options),
  dismiss: (id: number) => useToastStore.getState().dismiss(id),
}
