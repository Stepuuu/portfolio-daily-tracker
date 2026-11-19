// Theme selection and token access. Pure TypeScript (no React, no `@/` imports) so node tests can load it.
// The theme is the `data-theme` attribute on <html>; index.html sets it before the first paint with the
// same storage key and default as here. Colours live in src/styles/tokens.css.

export type Theme = 'light' | 'dark'
export type ThemePreference = Theme | 'system'

export const THEME_STORAGE_KEY = 'pdt.theme'
/**
 * 跟随系统: every page uses the tokens (tests/migrated.json is all of src/), so the page follows the operating
 * system until the person picks 浅色 or 深色 in 设置 › 关于与帮助 (an earlier explicit choice is kept).
 */
export const DEFAULT_THEME_PREFERENCE: ThemePreference = 'system'

/** Token names, as CSS variables without the leading dashes. */
export const TOKEN_NAMES = [
  'bg', 'surface', 'surface-2', 'surface-3', 'border', 'border-input',
  'text', 'text-2', 'text-3', 'accent', 'accent-fg', 'up', 'down', 'warn',
] as const
export type TokenName = (typeof TOKEN_NAMES)[number]

const THEME_EVENT = 'pdt:themechange'

function root(): HTMLElement | null {
  return typeof document === 'undefined' ? null : document.documentElement
}

function isPreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}

export function resolveTheme(preference: ThemePreference, prefersLight: boolean): Theme {
  if (preference === 'system') return prefersLight ? 'light' : 'dark'
  return preference
}

/** The saved preference, or the default when nothing (or something unreadable) is stored. */
export function readThemePreference(): ThemePreference {
  try {
    const stored = globalThis.localStorage?.getItem(THEME_STORAGE_KEY)
    return isPreference(stored) ? stored : DEFAULT_THEME_PREFERENCE
  } catch {
    return DEFAULT_THEME_PREFERENCE
  }
}

function systemPrefersLight(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: light)').matches
}

/** The theme currently shown. */
export function currentTheme(): Theme {
  return root()?.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

/** Show a theme without saving it (previews, the gallery). */
export function applyTheme(theme: Theme): void {
  const element = root()
  if (!element || element.getAttribute('data-theme') === theme) return
  element.setAttribute('data-theme', theme)
  element.dispatchEvent(new Event(THEME_EVENT))
}

let stopFollowingSystem: (() => void) | null = null

/**
 * Show the preference and keep following the operating system while it is 'system'.
 * Call once at start-up (after the bootstrap in index.html) and again whenever the preference changes.
 */
export function followThemePreference(preference: ThemePreference = readThemePreference()): void {
  stopFollowingSystem?.()
  stopFollowingSystem = null
  applyTheme(resolveTheme(preference, systemPrefersLight()))
  if (preference !== 'system' || typeof matchMedia !== 'function') return
  const query = matchMedia('(prefers-color-scheme: light)')
  const onChange = () => applyTheme(query.matches ? 'light' : 'dark')
  query.addEventListener('change', onChange)
  stopFollowingSystem = () => query.removeEventListener('change', onChange)
}

/** Save a preference and show it. 'system' follows the operating system. */
export function setThemePreference(preference: ThemePreference): void {
  try {
    globalThis.localStorage?.setItem(THEME_STORAGE_KEY, preference)
  } catch {
    // Storage can be blocked; the theme still applies for this page.
  }
  followThemePreference(preference)
}

/** Calls `listener` whenever the shown theme changes. Returns the unsubscribe function. */
export function subscribeTheme(listener: () => void): () => void {
  const element = root()
  if (!element) return () => {}
  element.addEventListener(THEME_EVENT, listener)
  return () => element.removeEventListener(THEME_EVENT, listener)
}

/** `"15 18 23"` → `"#0f1217"`; null when the value is not an RGB triplet. */
export function tripletToHex(value: string): string | null {
  const parts = value.trim().split(/\s+/).map(Number)
  if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return null
  return `#${parts.map((part) => part.toString(16).padStart(2, '0')).join('')}`
}

/**
 * A token's colour as a hex string for SVG and canvas (charts), read from the page.
 * Outside a browser, or before the stylesheet loads, returns `fallback`.
 */
export function tokenHex(name: TokenName, fallback: string): string {
  const element = root()
  if (!element || typeof getComputedStyle !== 'function') return fallback
  return tripletToHex(getComputedStyle(element).getPropertyValue(`--${name}`)) ?? fallback
}
