// Exact arithmetic on the backend's decimal strings ("61.20", "-0.5"), for the few figures the page
// derives itself: a price change (price − previous close), the distance from a price to an alert
// threshold, a sum of weights, a chart's return. Scaled BigInt, never binary floating point; results are
// decimal strings for lib/format.ts. null in, null out: an unknown value never becomes 0. Only plain
// decimal text is read: an exponent form ("1e21"), a number that prints as one, or anything longer than
// MAX_LENGTH characters is not a value here (null), never converted through Number.

interface Scaled {
  value: bigint
  scale: number
}

const DECIMAL = /^([+-])?(\d+)(?:\.(\d+))?$|^([+-])?\.(\d+)$/
/** Longer text is refused before it is read (the backend's thresholds are at most 32 characters). */
export const MAX_LENGTH = 40

function parse(text: string | number | null | undefined): Scaled | null {
  if (text === null || text === undefined) return null
  // A JavaScript number is read through its shortest decimal form, which is exact when it has no exponent.
  const raw = typeof text === 'number' ? (Number.isFinite(text) ? String(text) : '') : text.trim()
  if (raw.length > MAX_LENGTH) return null
  const match = DECIMAL.exec(raw)
  if (!match) return null
  const sign = match[1] ?? match[4] ?? ''
  const int = match[2] ?? '0'
  const frac = match[3] ?? match[5] ?? ''
  return { value: BigInt(`${sign === '-' ? '-' : ''}${int}${frac}` || '0'), scale: frac.length }
}

function align(a: Scaled, b: Scaled): [bigint, bigint, number] {
  const scale = Math.max(a.scale, b.scale)
  return [a.value * 10n ** BigInt(scale - a.scale), b.value * 10n ** BigInt(scale - b.scale), scale]
}

function format(value: bigint, scale: number): string {
  const negative = value < 0n
  const digits = (negative ? -value : value).toString().padStart(scale + 1, '0')
  const int = digits.slice(0, digits.length - scale)
  const frac = scale ? digits.slice(digits.length - scale).replace(/0+$/, '') : ''
  const text = frac ? `${int}.${frac}` : int
  return negative && /[1-9]/.test(text) ? `-${text}` : text
}

/** Divides and rounds half away from zero to `places` fraction digits. */
function divide(numerator: bigint, denominator: bigint, places: number): bigint {
  const scaled = numerator * 10n ** BigInt(places)
  const quotient = scaled / denominator
  const remainder = scaled % denominator
  const twice = (remainder < 0n ? -remainder : remainder) * 2n
  const absDenominator = denominator < 0n ? -denominator : denominator
  if (twice >= absDenominator) return quotient + ((scaled < 0n) !== (denominator < 0n) ? -1n : 1n)
  return quotient
}

/** Whether `text` is a decimal this module can read. */
export function isDecimal(text: string | null | undefined): boolean {
  return parse(text) !== null
}

/** a − b, exactly. */
export function subtract(a: string | null | undefined, b: string | null | undefined): string | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  const [p, q, scale] = align(x, y)
  return format(p - q, scale)
}

/** The sum of the values; null when any is unknown (or there are none). */
export function sum(values: (string | null | undefined)[]): string | null {
  if (values.length === 0) return null
  let total: Scaled = { value: 0n, scale: 0 }
  for (const value of values) {
    const next = parse(value)
    if (!next) return null
    const [p, q, scale] = align(total, next)
    total = { value: p + q, scale }
  }
  return format(total.value, total.scale)
}

/** −1, 0 or 1; null when either is unknown. */
export function compare(a: string | null | undefined, b: string | null | undefined): -1 | 0 | 1 | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  const [p, q] = align(x, y)
  return p < q ? -1 : p > q ? 1 : 0
}

/** The sign of `value`: −1, 0 or 1; null when unknown. */
export function sign(value: string | null | undefined): -1 | 0 | 1 | null {
  return compare(value, '0')
}

/**
 * (to − from) / from × 100, rounded to `places` (default 2): how far `to` is from `from`, in percent of
 * `from`. null when either is unknown or `from` is 0.
 */
export function percentChange(from: string | null | undefined, to: string | null | undefined, places = 2): string | null {
  const x = parse(from)
  const y = parse(to)
  if (!x || !y || x.value === 0n) return null
  const [base, target, scale] = align(x, y)
  void scale
  return format(divide((target - base) * 100n, base, places), places)
}

/** `value` × (1 + percent / 100), rounded to `places`: a price `percent` away from `value`. */
export function applyPercent(value: string | null | undefined, percent: string | number, places = 2): string | null {
  const x = parse(value)
  const p = parse(typeof percent === 'number' ? String(percent) : percent)
  if (!x || !p) return null
  // x × (100 + p) / 100, with every scale brought to a common denominator.
  const hundred = { value: 100n, scale: 0 }
  const [h, q, pScale] = align(hundred, p)
  const factor = h + q
  const numerator = x.value * factor
  const denominator = 100n * 10n ** BigInt(x.scale + pScale)
  return format(divide(numerator, denominator, places), places)
}
