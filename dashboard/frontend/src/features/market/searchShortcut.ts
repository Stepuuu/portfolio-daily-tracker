// ⌘K / Ctrl+K and "/" focus the global search (components/shell/AppShell.tsx). Neither may take the focus
// from a field someone is typing in: leaving it would end an input method's composition or run the
// field's save-on-blur (a watchlist note). Pure TypeScript so node tests can load it.

export interface ShortcutKey {
  key: string
  ctrlKey: boolean
  metaKey: boolean
  altKey: boolean
  isComposing: boolean
  keyCode: number
  target: EventTarget | { tagName?: string; isContentEditable?: boolean; type?: string } | null
}

/** Input types that are clicked, not typed in. */
const NOT_TYPED = ['button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit']

/** Whether `target` is a field that takes typed text (text inputs, textareas, selects, contenteditable). */
export function typingIn(target: ShortcutKey['target']): boolean {
  if (!target || typeof target !== 'object') return false
  const element = target as { tagName?: string; isContentEditable?: boolean; type?: string }
  if (element.isContentEditable) return true
  const tag = (element.tagName ?? '').toUpperCase()
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  return tag === 'INPUT' && !NOT_TYPED.includes((element.type ?? 'text').toLowerCase())
}

/**
 * What a key press means for the search: `focus` it; `block` (⌘K in a field being typed in: swallow it,
 * so neither the page nor the browser moves the focus away, and leave the field as it is); `ignore`
 * (not the shortcut, an input method composing, or "/" typed into a field).
 */
export function searchShortcut(event: ShortcutKey): 'focus' | 'block' | 'ignore' {
  if (event.isComposing || event.keyCode === 229) return 'ignore'
  const command = event.metaKey || event.ctrlKey
  if (command && !event.altKey && event.key.toLowerCase() === 'k') return typingIn(event.target) ? 'block' : 'focus'
  if (event.key === '/' && !command && !event.altKey && !typingIn(event.target)) return 'focus'
  return 'ignore'
}
