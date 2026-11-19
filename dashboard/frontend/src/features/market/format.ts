// Prices of any ticker in its own digits: A-share stocks 2, A-share ETFs and funds 3, Hong Kong 3,
// US 2, indexes 2. Pure TypeScript so node tests can load it.
import { fmtMoney, fmtNumber, priceDigits, type InstrumentKind } from '../../lib/format.ts'
import { instrumentKind } from '../today/rows.ts'

/** The currency of a ledger ticker (SHA/SHE CNY, HKG HKD, NASDAQ/NYSE USD; indexes by market). */
export function currencyOf(ticker: string): string {
  const [exchange, code = ''] = ticker.toUpperCase().split(':')
  if (exchange === 'HKG') return 'HKD'
  if (exchange === 'NASDAQ' || exchange === 'NYSE') return 'USD'
  if (exchange === 'IDX') return ({ HSI: 'HKD', HSTECH: 'HKD', IXIC: 'USD', DJIA: 'USD', SPX: 'USD' } as Record<string, string>)[code] ?? 'CNY'
  return 'CNY'
}

export function tickerPriceDigits(ticker: string, currency: string = currencyOf(ticker)): number {
  if (ticker.toUpperCase().startsWith('IDX:')) return 2
  return priceDigits(currency, instrumentKind(ticker) as InstrumentKind)
}

/**
 * "61.20", "382.400"; with `symbol` "HK$382.400" (a column that mixes currencies). With `exact` the
 * market's digits are a minimum and nothing is rounded: a stored threshold "11.004" or an average cost
 * "12.345679" keeps every digit.
 */
export function fmtTickerPrice(
  value: string | null | undefined, ticker: string, currency: string = currencyOf(ticker),
  options: { symbol?: boolean; signed?: boolean; exact?: boolean } = {},
): string {
  const digits = tickerPriceDigits(ticker, currency)
  const index = ticker.toUpperCase().startsWith('IDX:')
  return options.symbol && !index && currency !== 'CNY'
    ? fmtMoney(value, currency, { digits, signed: options.signed, exact: options.exact })
    : fmtNumber(value, { digits, signed: options.signed, exact: options.exact })
}
