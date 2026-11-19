// Exact arithmetic on decimal strings, for figures the entry form shows before the backend has answered
// (turnover, cash before → after). Never binary floats: "0.1" + "0.2" is "0.3". Results are plain decimal
// strings with every digit kept; round only when formatting (lib/format.ts). Display only: the amounts that
// are booked come from the backend's preview. Pure TypeScript so node tests can load it.

interface Fixed {
  /** The value times 10^scale. */
  units: bigint
  scale: number
}

const PLAIN = /^([+-])?(\d+)?(?:\.(\d*))?$/

/** A plain decimal string ("-12.50", ".5", "3.") as a fixed-point value; null when it is not one ("1.2."). */
function parse(value: string | null | undefined): Fixed | null {
  if (typeof value !== 'string') return null
  const match = PLAIN.exec(value.trim())
  if (!match) return null
  const [, sign, int = '', frac = ''] = match
  if (!int && !frac) return null
  const units = BigInt(`${int || '0'}${frac}`)
  return { units: sign === '-' ? -units : units, scale: frac.length }
}

function align(a: Fixed, b: Fixed): [bigint, bigint, number] {
  const scale = Math.max(a.scale, b.scale)
  const up = (x: Fixed) => x.units * 10n ** BigInt(scale - x.scale)
  return [up(a), up(b), scale]
}

function format({ units, scale }: Fixed): string {
  const negative = units < 0n
  const digits = (negative ? -units : units).toString().padStart(scale + 1, '0')
  const int = digits.slice(0, digits.length - scale)
  const frac = scale ? digits.slice(digits.length - scale).replace(/0+$/, '') : ''
  const body = frac ? `${int}.${frac}` : int
  return negative && body !== '0' ? `-${body}` : body
}

/** True for a plain decimal string ("12", "-0.5"). */
export function isDecimal(value: string | null | undefined): boolean {
  return parse(value) !== null
}

/** a + b; null when either is not a decimal. */
export function decAdd(a: string | null | undefined, b: string | null | undefined): string | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  const [p, q, scale] = align(x, y)
  return format({ units: p + q, scale })
}

/** a − b. */
export function decSub(a: string | null | undefined, b: string | null | undefined): string | null {
  const y = parse(b)
  return y ? decAdd(a, format({ units: -y.units, scale: y.scale })) : null
}

/** a × b, exactly. */
export function decMul(a: string | null | undefined, b: string | null | undefined): string | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  return format({ units: x.units * y.units, scale: x.scale + y.scale })
}

/** −a. */
export function decNeg(a: string | null | undefined): string | null {
  const x = parse(a)
  return x ? format({ units: -x.units, scale: x.scale }) : null
}

/** -1, 0 or 1 as a < b, a = b, a > b; null when either is not a decimal. */
export function decCmp(a: string | null | undefined, b: string | null | undefined): -1 | 0 | 1 | null {
  const x = parse(a)
  const y = parse(b)
  if (!x || !y) return null
  const [p, q] = align(x, y)
  return p < q ? -1 : p > q ? 1 : 0
}

/** -1, 0 or 1 by sign; null when not a decimal. */
export function decSign(a: string | null | undefined): -1 | 0 | 1 | null {
  return decCmp(a, '0')
}

/** True for a decimal above zero. */
export function decPositive(a: string | null | undefined): boolean {
  return decSign(a) === 1
}

/** a × 10^places, exactly: decShift("2.5", -4) is "0.00025" (万2.5 as a fraction). */
export function decShift(a: string | null | undefined, places: number): string | null {
  const x = parse(a)
  if (!x) return null
  if (places >= 0) return format({ units: x.units * 10n ** BigInt(places), scale: x.scale })
  return format({ units: x.units, scale: x.scale - places })
}

/** The sum of the given decimals; null when any of them is not a decimal. */
export function decSum(values: (string | null | undefined)[]): string | null {
  let total: string | null = '0'
  for (const value of values) total = decAdd(total, value)
  return total
}

/**
 * Ranks for sorting decimal strings exactly (binary floats tie 9007199254740992 and …993): equal values share
 * a rank; a value that is not a decimal gets null (it sorts last).
 */
export function decRanks(values: (string | null | undefined)[]): (number | null)[] {
  const known = values
    .map((value, index) => ({ value, index }))
    .filter((item): item is { value: string; index: number } => isDecimal(item.value))
    .sort((a, b) => decCmp(a.value, b.value)!)
  const ranks: (number | null)[] = values.map(() => null)
  let rank = 0
  known.forEach((item, position) => {
    if (position > 0 && decCmp(known[position - 1].value, item.value) !== 0) rank += 1
    ranks[item.index] = rank
  })
  return ranks
}
