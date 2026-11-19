// Number formatters for the daily tracker page. A missing value (null, undefined, NaN, Infinity)
// is shown as "—": it must never turn into "0.00" or "+0.00%", which would be a false number.

export const NO_VALUE = '—'

type Num = number | null | undefined

function isValue(n: Num): n is number {
  return typeof n === 'number' && Number.isFinite(n)
}

const formatters = new Map<number, Intl.NumberFormat>()
function formatter(digits: number): Intl.NumberFormat {
  let f = formatters.get(digits)
  if (!f) {
    f = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    formatters.set(digits, f)
  }
  return f
}

export const fmt = (n: Num, digits = 2): string => (isValue(n) ? formatter(digits).format(n) : NO_VALUE)

/** Amount in units of 10,000 (万). */
export const fmtWan = (n: Num): string => (isValue(n) ? `${fmt(n / 10000, 2)}万` : NO_VALUE)

/** Percentage with an explicit "+" for gains. */
export const fmtPct = (n: Num): string => (isValue(n) ? `${n >= 0 ? '+' : ''}${fmt(n, 2)}%` : NO_VALUE)

/** Percentage without a sign prefix (weights, drawdown). */
export const fmtPlainPct = (n: Num, digits = 2): string => (isValue(n) ? `${fmt(n, digits)}%` : NO_VALUE)

/** Signed amount in 万 ("+1.20万"); "—" when missing. */
export const fmtSignedWan = (n: Num): string => (isValue(n) ? `${n >= 0 ? '+' : ''}${fmtWan(n)}` : NO_VALUE)
