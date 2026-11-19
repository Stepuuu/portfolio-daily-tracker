import { useCallback, useRef, useSyncExternalStore } from 'react'
import {
  TOKEN_NAMES, currentTheme, subscribeTheme, tokenHex, type Theme, type TokenName,
} from '@/lib/theme'

/** The theme currently shown; re-renders when it changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, currentTheme, () => 'dark')
}

// Fallbacks for the first render outside a browser; the real values are read from tokens.css.
const DARK_FALLBACK: Record<TokenName, string> = {
  bg: '#0f1217', surface: '#161a21', 'surface-2': '#1d222b', 'surface-3': '#232933', border: '#272d37',
  'border-input': '#687080', text: '#e6e8eb', 'text-2': '#a9b0bb', 'text-3': '#999fab', accent: '#7ea7f4',
  'accent-fg': '#0f1217', up: '#f97066', down: '#3fbf7f', warn: '#e5a23a',
}

const tokenCache = new Map<Theme, Record<TokenName, string>>()

/**
 * Token colours as hex strings, for charts and SVG that cannot use classes. Updates with the theme.
 * Example: `const t = useTokens(); <Line stroke={t.up} />`.
 */
export function useTokens(): Record<TokenName, string> {
  const theme = useTheme()
  let tokens = tokenCache.get(theme)
  if (!tokens) {
    tokens = Object.fromEntries(TOKEN_NAMES.map((name) => [name, tokenHex(name, DARK_FALLBACK[name])])) as Record<TokenName, string>
    tokenCache.set(theme, tokens)
  }
  return tokens
}

/** True while the media query matches, e.g. `useMediaQuery('(max-width: 639px)')`. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof matchMedia !== 'function') return () => {}
    const list = matchMedia(query)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])
  return useSyncExternalStore(
    subscribe,
    () => typeof matchMedia === 'function' && matchMedia(query).matches,
    () => false,
  )
}

/** A ref that always holds the latest value (for event handlers inside effects). */
export function useLatest<T>(value: T) {
  const ref = useRef(value)
  ref.current = value
  return ref
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Every keyboard-focusable element inside `root` that is shown, in document order. */
export function focusables(root: Element | null | undefined): HTMLElement[] {
  if (!root) return []
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => element.getClientRects().length > 0)
}

/** First keyboard-focusable element inside `root`. */
export function firstFocusable(root: Element | null | undefined): HTMLElement | null {
  return focusables(root)[0] ?? null
}
