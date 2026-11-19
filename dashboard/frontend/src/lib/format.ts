// Number, money, percentage and time formatting for every page. Pure TypeScript (no `@/` imports, no JSX)
// so node tests can load it.
//
// Rules:
// - A missing or unreadable value is shown as "不可用", never as 0.
// - Decimal strings from the backend are formatted digit by digit (no binary rounding) and rounded
//   half away from zero, like the ledger; with `exact` they keep every digit they have (backend figures
//   on the desk pages). Formatting is for display only: never add up the results.
// - A backend figure with more than 8 fraction digits (a derived price, an average cost) goes through
//   `fmtLong`: shown rounded to 8 places with "≈", the full value kept for the title and screen readers.
// - Signs are explicit where asked (`signed`), zero has no sign, and "-0.001" rounded to 0.00 shows 0.00.
import { pnlDirection, type PnlDirection } from './pnlColor.ts'

export const UNAVAILABLE = '不可用'

export type Currency = 'CNY' | 'HKD' | 'USD'
export type NumericInput = string | number | null | undefined
export type InstrumentKind = 'stock' | 'etf' | 'fund' | 'bond' | 'index'

const SYMBOLS: Record<string, string> = { CNY: '¥', HKD: 'HK$', USD: '$' }

/** "¥", "HK$" or "$"; an unknown code is returned as it is. */
export function currencySymbol(currency: string): string {
  return SYMBOLS[currency] ?? currency
}

// ---------------------------------------------------------------------------------------------
// Decimal strings

interface Decimal {
  negative: boolean
  /** Integer digits without leading zeros ("0" for none). */
  int: string
  /** Fraction digits, as given. */
  frac: string
}

const DECIMAL = /^([+-])?(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d{1,3}))?$/

function parseDecimal(value: NumericInput): Decimal | null {
  let text: string
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    text = Object.is(value, -0) ? '-0' : String(value)
  } else if (typeof value === 'string') {
    text = value.trim()
  } else {
    return null
  }
  const match = DECIMAL.exec(text)
  if (!match) return null
  const [, sign, intDigits = '', fracDigits = '', exponent] = match
  if (!intDigits && !fracDigits) return null
  let digits = intDigits + fracDigits
  let point = intDigits.length + (exponent ? Number(exponent) : 0)
  if (point < 0) {
    digits = '0'.repeat(-point) + digits
    point = 0
  }
  if (point > digits.length) digits += '0'.repeat(point - digits.length)
  return { negative: sign === '-', int: digits.slice(0, point).replace(/^0+/, '') || '0', frac: digits.slice(point) }
}

function isZero(decimal: Decimal): boolean {
  return /^0*$/.test(decimal.int + decimal.frac)
}

/** Moves the decimal point: shift(d, 2) multiplies by 100, shift(d, -4) divides by 10,000. */
function shift(decimal: Decimal, places: number): Decimal {
  const sign = decimal.negative ? '-' : ''
  return parseDecimal(`${sign}${decimal.int}.${decimal.frac || '0'}e${places}`)!
}

function increment(digits: string): string {
  const chars = digits.split('')
  for (let i = chars.length - 1; i >= 0; i -= 1) {
    if (chars[i] !== '9') {
      chars[i] = String(Number(chars[i]) + 1)
      return chars.join('')
    }
    chars[i] = '0'
  }
  return `1${chars.join('')}`
}

/** Rounds half away from zero to exactly `places` fraction digits. */
function round(decimal: Decimal, places: number): Decimal {
  if (decimal.frac.length <= places) return { ...decimal, frac: decimal.frac.padEnd(places, '0') }
  let digits = decimal.int + decimal.frac.slice(0, places)
  if (decimal.frac.charCodeAt(places) >= 53 /* '5' */) digits = increment(digits)
  const intLength = digits.length - places
  return { negative: decimal.negative, int: digits.slice(0, intLength).replace(/^0+/, '') || '0', frac: digits.slice(intLength) }
}

function group(int: string): string {
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

function signOf(decimal: Decimal, signed: boolean): string {
  if (isZero(decimal)) return ''
  if (decimal.negative) return '-'
  return signed ? '+' : ''
}

function body(decimal: Decimal): string {
  return decimal.frac ? `${group(decimal.int)}.${decimal.frac}` : group(decimal.int)
}

// ---------------------------------------------------------------------------------------------
// Numbers

export interface NumberOptions {
  /** Show "+" before positive values. */
  signed?: boolean
  /** Fraction digits (default 2). */
  digits?: number
  /**
   * Keep every fraction digit the value has, padding to `digits` but never rounding: a backend figure shown
   * exactly as it was computed ("12.345" stays "12.345", "12.3" becomes "12.30").
   */
  exact?: boolean
}

/** Rounds to `digits`, or with `exact` only pads to them. */
function fit(decimal: Decimal, digits: number, exact = false): Decimal {
  return exact && decimal.frac.length >= digits ? decimal : round(decimal, digits)
}

/** "1,234.50"; a plain number with thousands separators. */
export function fmtNumber(value: NumericInput, options: NumberOptions = {}): string {
  const decimal = parseDecimal(value)
  if (!decimal) return UNAVAILABLE
  const rounded = fit(decimal, options.digits ?? 2, options.exact)
  return signOf(rounded, Boolean(options.signed)) + body(rounded)
}

/** "¥1,234.50", "-HK$20.00", "+$12.00" (signed). */
export function fmtMoney(value: NumericInput, currency: Currency | string, options: NumberOptions = {}): string {
  const decimal = parseDecimal(value)
  if (!decimal) return UNAVAILABLE
  const rounded = fit(decimal, options.digits ?? 2, options.exact)
  const symbol = currencySymbol(currency)
  const sign = signOf(rounded, Boolean(options.signed))
  return SYMBOLS[currency] ? `${sign}${symbol}${body(rounded)}` : `${sign}${symbol} ${body(rounded)}`
}

/**
 * Short form for large headline figures: "12.31万", "¥1.20亿" (with a currency). Below 10,000 the
 * value is shown in full. Use only for Stat size="lg" and put the exact value in its `exactTitle`.
 */
export function fmtCompact(value: NumericInput, currency?: Currency | string, options: { signed?: boolean } = {}): string {
  const decimal = parseDecimal(value)
  if (!decimal) return UNAVAILABLE
  const prefix = currency ? currencySymbol(currency) + (SYMBOLS[currency] ? '' : ' ') : ''
  let rounded = round(decimal, 2)
  let unit = ''
  if (rounded.int.length >= 5) {
    rounded = round(shift(decimal, -4), 2)
    unit = '万'
    if (rounded.int.length >= 5) {
      rounded = round(shift(decimal, -8), 2)
      unit = '亿'
    }
  }
  return `${signOf(rounded, Boolean(options.signed))}${prefix}${body(rounded)}${unit}`
}

/** Fraction digits for a price: A-share stocks 2, A-share ETFs and funds 3, Hong Kong 3, US 2. */
export function priceDigits(currency: Currency | string, kind: InstrumentKind = 'stock'): number {
  if (currency === 'HKD') return 3
  if (currency === 'CNY' && (kind === 'etf' || kind === 'fund')) return 3
  return 2
}

/**
 * "1,680.00"; with `symbol: true` "HK$382.400" (use when a column mixes currencies). With `exact` the
 * market's digits are a minimum: an average cost "12.345679" keeps all six.
 */
export function fmtPrice(
  value: NumericInput, currency: Currency | string, kind: InstrumentKind = 'stock', options: { symbol?: boolean; exact?: boolean } = {},
): string {
  const digits = priceDigits(currency, kind)
  return options.symbol ? fmtMoney(value, currency, { digits, exact: options.exact }) : fmtNumber(value, { digits, exact: options.exact })
}

/** "1,000", "1,000.5", "0.0001": thousands separators, all given digits, no trailing zeros. */
export function fmtQty(value: NumericInput): string {
  const decimal = parseDecimal(value)
  if (!decimal) return UNAVAILABLE
  const trimmed = { ...decimal, frac: decimal.frac.replace(/0+$/, '') }
  return signOf(trimmed, false) + body(trimmed)
}

export interface PercentOptions extends NumberOptions {
  /** The value is a ratio (0.0123 → 1.23%) rather than percent units (1.23 → 1.23%). */
  ratio?: boolean
}

/** "+1.23%" (signed). Values are in percent units, as the backend's `*_pct` fields; see `ratio`. */
export function fmtPct(value: NumericInput, options: PercentOptions = {}): string {
  const parsed = parseDecimal(value)
  if (!parsed) return UNAVAILABLE
  const rounded = fit(options.ratio ? shift(parsed, 2) : parsed, options.digits ?? 2, options.exact)
  return `${signOf(rounded, Boolean(options.signed))}${body(rounded)}%`
}

// ---------------------------------------------------------------------------------------------
// Long decimals

/** Fraction digits shown at most. A value with more (a derived price, an average cost) is shown rounded. */
export const SHOWN_PLACES = 8

/**
 * A figure as the page shows it. `text` is what is printed. When the value has more than 8 fraction digits,
 * `text` is "≈" and the value rounded half up to 8 places, `full` is the value with every digit (the element's
 * title) and `label` its accessible name ("约 32.01666667，完整值 32.016666666666666666666666667").
 */
export interface Shown {
  text: string
  full?: string
  label?: string
}

const allDigits = (value: NumericInput) => fmtNumber(value, { digits: 0, exact: true })

/**
 * The one place a backend figure may be rounded for display. A decimal with at most 8 fraction digits is
 * `format`ted with every digit, as before; one with more is rounded half up (away from zero) to 8 places,
 * digit by digit, marked "≈", and keeps its full value in `full` / `label`. The rounded value loses its
 * trailing zeros before `format` pads it to its own minimum (a cost basis 61764.99999999999999999999996
 * reads "≈¥61,765.00", not "≈¥61,765.00000000"). Zeros past the 8th place lose nothing, so such a value is
 * shown without "≈". `format` turns a decimal string into the shown text and must keep the digits it is given
 * (an `exact` formatter); by default the digits with thousands separators. Money the backend sends at
 * currency precision has at most 4 places and is never affected.
 */
export function fmtLong(value: NumericInput, format: (value: NumericInput) => string = allDigits): Shown {
  const decimal = parseDecimal(value)
  if (!decimal || decimal.frac.length <= SHOWN_PLACES) return { text: format(value) }
  const rounded = round(decimal, SHOWN_PLACES)
  const places = rounded.frac.replace(/0+$/, '')
  const short = format(`${rounded.negative ? '-' : ''}${rounded.int}${places ? `.${places}` : ''}`)
  if (/^0*$/.test(decimal.frac.slice(SHOWN_PLACES))) return { text: short }
  const full = format(value)
  return { text: `≈${short}`, full, label: `约 ${short}，完整值 ${full}` }
}

/** A ratio as a plain decimal string in percent units, exactly ("0.0123" → "1.23"): for `fmtLong` of a ratio. */
export function ratioPercent(value: NumericInput): string | null {
  const decimal = parseDecimal(value)
  if (!decimal) return null
  const percent = shift(decimal, 2)
  return `${percent.negative ? '-' : ''}${percent.int}${percent.frac ? `.${percent.frac}` : ''}`
}

/** Picks a figure's text for a line: as shown (`shownText`) or with every digit (`fullText`). */
export type ShownPick = (figure: Shown) => string
export const shownText: ShownPick = (figure) => figure.text
export const fullText: ShownPick = (figure) => figure.full ?? figure.text

/**
 * A line with figures in it ("买入 300 股 @ ≈32.01666667 · 费用 5.00"), built once as shown and once with every
 * digit: `full` and `label` (the full line) are set only when a figure in it was rounded.
 */
export function longLine(build: (pick: ShownPick) => string): Shown {
  const text = build(shownText)
  const full = build(fullText)
  return full === text ? { text } : { text, full, label: full }
}

/** `longLine` for a builder that may have nothing to show (null). */
export function longLineOrNull(build: (pick: ShownPick) => string | null): Shown | null {
  return build(shownText) === null ? null : longLine((pick) => build(pick) ?? '')
}

// ---------------------------------------------------------------------------------------------
// Direction

/**
 * Direction of a raw or formatted value, for colour: up (red), down (green) or flat. Accepts numbers,
 * decimal strings and formatted text such as "-HK$20.00" or "+1.23%". Zero and unknown are flat.
 */
export function toneOf(value: NumericInput): PnlDirection {
  if (typeof value === 'number' || value === null || value === undefined) return pnlDirection(value)
  const decimal = parseDecimal(value) ?? parseDecimal(value.replace(/[−–]/g, '-').replace(/[^\d.+-]/g, ''))
  if (!decimal || isZero(decimal)) return 'flat'
  return pnlDirection(decimal.negative ? -1 : 1)
}

// ---------------------------------------------------------------------------------------------
// Time

export interface TimeOptions {
  /** IANA zone; the browser's own zone by default. */
  timeZone?: string
}

type TimeInput = string | number | Date | null | undefined

function toDate(value: TimeInput): Date | null {
  if (value === null || value === undefined || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function parts(date: Date, options: Intl.DateTimeFormatOptions, timeZone?: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of new Intl.DateTimeFormat('en-GB', { ...options, hourCycle: 'h23', timeZone }).formatToParts(date)) {
    out[part.type] = part.value
  }
  return out
}

/** "10:32:05"; `seconds: false` gives "10:32". */
export function fmtTime(value: TimeInput, options: TimeOptions & { seconds?: boolean } = {}): string {
  const date = toDate(value)
  if (!date) return UNAVAILABLE
  const withSeconds = options.seconds ?? true
  const p = parts(date, { hour: '2-digit', minute: '2-digit', second: withSeconds ? '2-digit' : undefined }, options.timeZone)
  return withSeconds ? `${p.hour}:${p.minute}:${p.second}` : `${p.hour}:${p.minute}`
}

/** "2026-11-17". A date-only string is returned unchanged (no time-zone shift). */
export function fmtDate(value: TimeInput, options: TimeOptions = {}): string {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return value.trim()
  const date = toDate(value)
  if (!date) return UNAVAILABLE
  const p = parts(date, { year: 'numeric', month: '2-digit', day: '2-digit' }, options.timeZone)
  return `${p.year}-${p.month}-${p.day}`
}

/** "2026-11-17 10:32". */
export function fmtDateTime(value: TimeInput, options: TimeOptions = {}): string {
  const date = toDate(value)
  if (!date) return UNAVAILABLE
  return `${fmtDate(date, options)} ${fmtTime(date, { ...options, seconds: false })}`
}

/**
 * "刚刚", "3 秒前", "5 分钟前", "2 小时前", "3 天前"; older than a week gives the date. For chips only:
 * tables show dates. `now` is injectable for tests.
 */
export function fmtRelative(value: TimeInput, now: number | Date = Date.now(), options: TimeOptions = {}): string {
  const date = toDate(value)
  if (!date) return UNAVAILABLE
  const reference = now instanceof Date ? now.getTime() : now
  const seconds = Math.round((reference - date.getTime()) / 1000)
  const suffix = seconds < 0 ? '后' : '前'
  const elapsed = Math.abs(seconds)
  if (elapsed < 5) return '刚刚'
  if (elapsed < 60) return `${elapsed} 秒${suffix}`
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} 分钟${suffix}`
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} 小时${suffix}`
  if (elapsed < 7 * 86400) return `${Math.floor(elapsed / 86400)} 天${suffix}`
  return fmtDate(date, options)
}

// ---------------------------------------------------------------------------------------------
// Number inputs

export type NumberInputResult = { ok: true; value: string } | { ok: false; error: string }

const THOUSANDS_REASON = '逗号和空格只能作千位分隔符（如 1,234.56），小数点请用“.”'

function refuse(raw: string, reason: string): NumberInputResult {
  const shown = raw.trim()
  return { ok: false, error: `未接受“${shown.length > 16 ? `${shown.slice(0, 16)}…` : shown}”：${reason}` }
}

/**
 * Reads what was typed or pasted into a number field, by one convention:
 * - digits are ASCII or full-width (Chinese input methods); the decimal point is "." (or "．", "。");
 * - "," (or "，") and spaces are thousands separators only, between groups of exactly three digits after a
 *   first group of one to three that does not start with 0 ("1,234.5", "1 000"); one kind per number;
 * - a leading "-" (or "－", "−", "—") only with `allowNegative`; no "+", exponents, symbols or units.
 * Everything else, in particular a decimal comma ("1,23", "1.234,56"), is refused with a reason for the
 * field to show; the field keeps its previous value, so nothing is silently read as another number.
 * Accepted text loses only its separators and becomes ASCII; typing states ("12.", ".", "-") pass.
 */
export function parseNumberInput(raw: string, options: { allowNegative?: boolean } = {}): NumberInputResult {
  const text = raw
    .replace(/[０-９]/g, (digit) => String.fromCharCode(digit.charCodeAt(0) - 0xfee0))
    .replace(/[．。]/g, '.')
    .replace(/，/g, ',')
    .replace(/[－−—]/g, '-')
    .replace(/[\s 　]+/g, ' ')
    .trim()
  if (text === '') return { ok: true, value: '' }
  if (/^[+-]?[\d.,\s]*\d[eE][+-]?\d*$/.test(text)) return refuse(raw, '不支持科学计数法，请写出完整的数字')
  if (/[¥￥$€£%元角分股份手]|HK|US|CN|RMB/i.test(text)) return refuse(raw, '只填数字，不要带货币符号、单位或百分号')
  if (text.startsWith('+')) return refuse(raw, '正数不需要加“+”')
  if (text.includes('-')) {
    if (!options.allowNegative) return refuse(raw, '不能为负数')
    if (!/^-[^-]*$/.test(text)) return refuse(raw, '负数只在最前面写一个负号')
  }
  const body = text.startsWith('-') ? text.slice(1) : text
  if (/[^\d., ]/.test(body)) return refuse(raw, '只能输入数字和小数点')
  const [int, frac, ...more] = body.split('.')
  if (more.length) return refuse(raw, '只能有一个小数点')
  if (frac !== undefined && /[, ]/.test(frac)) return refuse(raw, THOUSANDS_REASON)
  let digits = int
  if (/[, ]/.test(int)) {
    const separators = new Set(int.replace(/\d/g, ''))
    if (separators.size > 1 || !/^[1-9]\d{0,2}(?:[, ]\d{3})+$/.test(int)) return refuse(raw, THOUSANDS_REASON)
    digits = int.replace(/[, ]/g, '')
  }
  return { ok: true, value: `${text.startsWith('-') ? '-' : ''}${digits}${frac === undefined ? '' : `.${frac}`}` }
}

/** `parseNumberInput`'s value, or null when the input is refused (the field keeps its previous value). */
export function normalizeNumberInput(raw: string, options: { allowNegative?: boolean } = {}): string | null {
  const result = parseNumberInput(raw, options)
  return result.ok ? result.value : null
}

/**
 * `value` plus or minus `step`, exactly (no floating point), for the arrow keys and stepper buttons of
 * quantity fields. An empty or unreadable value counts as 0. Results below `min` are clamped to it.
 */
export function stepNumberInput(value: string, step: string | number, direction: 1 | -1, min?: number): string {
  const a = parseDecimal(value) ?? { negative: false, int: '0', frac: '' }
  const b = parseDecimal(step)
  if (!b) return value
  const floor = min === undefined ? null : parseDecimal(min)
  // The minimum's digits count too: a step of 1 from 0 with a minimum of 0.05 stops at 0.05.
  const places = Math.max(a.frac.length, b.frac.length, floor?.frac.length ?? 0)
  const scaled = (d: Decimal) => BigInt((d.negative ? '-' : '') + d.int + d.frac.padEnd(places, '0'))
  let total = scaled(a) + BigInt(direction) * scaled(b)
  if (floor && total < scaled(floor)) total = scaled(floor)
  const negative = total < 0n
  const digits = (negative ? -total : total).toString().padStart(places + 1, '0')
  const int = digits.slice(0, digits.length - places)
  // Zeros that only the minimum's precision added are dropped ("2" minus 1 is "1", not "1.00").
  const ownPlaces = Math.max(a.frac.length, b.frac.length)
  const frac = digits.slice(digits.length - places).replace(new RegExp(`(?<=.{${ownPlaces}})0+$`), '')
  return `${negative ? '-' : ''}${int}${frac ? `.${frac}` : ''}`
}
