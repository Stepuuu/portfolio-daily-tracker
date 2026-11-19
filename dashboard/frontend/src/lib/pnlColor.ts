// Gain/loss colours for the whole app. One convention, defined only here:
// red = up / profit, green = down / loss (the A-share convention), neutral for zero or no value.
// Pages must not hard-code red/green for price moves or profit; import these helpers instead.
// (Colours that mean something else — errors, risk levels, delete buttons — are not gain/loss.)
import { tokenHex } from './theme.ts'

export type PnlDirection = 'up' | 'down' | 'flat'
export type PnlTone = 'dark' | 'light'

/** Direction of a signed amount or percentage. Zero, NaN and missing values are flat. */
export function pnlDirection(value: number | null | undefined): PnlDirection {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) return 'flat'
  return value > 0 ? 'up' : 'down'
}

function resolve(value: number | PnlDirection | null | undefined): PnlDirection {
  return value === 'up' || value === 'down' || value === 'flat' ? value : pnlDirection(value)
}

// Class names are written out in full so Tailwind keeps them in the build. They are the theme
// tokens (src/styles/tokens.css): `up` is red and `down` is green in both themes. The 'light' tone
// adds `theme-light`, which gives that element the light-theme values (legacy light pages).
const TEXT_CLASS: Record<PnlTone, Record<PnlDirection, string>> = {
  dark: { up: 'text-up', down: 'text-down', flat: 'text-fg-2' },
  light: { up: 'text-up theme-light', down: 'text-down theme-light', flat: 'text-fg-2 theme-light' },
}

const PANEL_CLASS: Record<PnlDirection, string> = {
  up: 'bg-up/10 border-up/20',
  down: 'bg-down/10 border-down/20',
  flat: 'bg-surface-2 border-line',
}

// Bars are marks, 3:1 against the surface (tests/tokens.test.mjs); text-3 at 70 % fell short in light.
const BAR_CLASS: Record<PnlDirection, string> = {
  up: 'bg-up/70',
  down: 'bg-down/70',
  flat: 'bg-fg-3',
}

// Read from the page at call time; the fallbacks are the dark-theme tokens (for non-browser use).
const HEX: Record<PnlDirection, string> = {
  get up() { return tokenHex('up', '#f97066') },
  get down() { return tokenHex('down', '#3fbf7f') },
  get flat() { return tokenHex('text-3', '#999fab') },
}

/** Text colour. `tone` is the page background: 'dark' (default) or 'light' (the backtest page). */
export function pnlTextClass(value: number | PnlDirection | null | undefined, tone: PnlTone = 'dark'): string {
  return TEXT_CLASS[tone][resolve(value)]
}

/** Tinted background plus border for a highlighted box. */
export function pnlPanelClass(value: number | PnlDirection | null | undefined): string {
  return PANEL_CLASS[resolve(value)]
}

/** Solid fill for progress bars. */
export function pnlBarClass(value: number | PnlDirection | null | undefined): string {
  return BAR_CLASS[resolve(value)]
}

/** Hex colour for SVG and chart strokes. */
export function pnlHex(value: number | PnlDirection | null | undefined): string {
  return HEX[resolve(value)]
}

export type TradeAction = 'buy' | 'add' | 'sell' | 'reduce' | 'hold'

/**
 * Direction a buy or sell recommendation points in, for colouring: buy/add = up, sell/reduce =
 * down, hold and anything unknown = flat. The same red-up / green-down convention as prices.
 */
export function actionDirection(action: string | null | undefined): PnlDirection {
  if (action === 'buy' || action === 'add') return 'up'
  if (action === 'sell' || action === 'reduce') return 'down'
  return 'flat'
}

/** Text colour for a buy/sell arrow or label. Hold (and unknown) keeps its amber. */
export function actionColor(action: string | null | undefined, tone: PnlTone = 'dark'): string {
  const direction = actionDirection(action)
  return direction === 'flat' ? 'text-warn' : pnlTextClass(direction, tone)
}

/** Tinted badge (background and text) for a buy/sell label. */
export function actionBadgeClass(action: string | null | undefined): string {
  const direction = actionDirection(action)
  return direction === 'up' ? 'bg-up/10 text-up'
    : direction === 'down' ? 'bg-down/10 text-down'
    : 'bg-warn/10 text-warn'
}
