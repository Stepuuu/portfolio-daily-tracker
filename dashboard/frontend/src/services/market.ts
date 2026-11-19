// Market data for 行情 and the stock drawer: quotes for any tickers (`GET /api/quotes`, the same objects
// as Today's rows) and daily bars (`GET /api/market/bars`, provider, adjustment and a provisional last
// bar labelled).
import api from './api'
import type { BarsPayload } from '@/features/market/chart'
import type { QuotesPayload } from '@/features/today/types'

export type { BarsPayload, QuotesPayload }

export const QUOTES_KEY = ['quotes'] as const

/** Quote objects for up to 50 tickers, in request order. */
export async function fetchQuotes(tickers: string[], signal?: AbortSignal): Promise<QuotesPayload> {
  return (await api.get<QuotesPayload>('/quotes', { params: { tickers: tickers.join(',') }, signal })).data
}

export async function fetchBars(ticker: string, days: number, signal?: AbortSignal): Promise<BarsPayload> {
  // The first request of a day may wait for the data source; give it longer than the default 10 s.
  return (await api.get<BarsPayload>('/market/bars', { params: { ticker, days }, signal, timeout: 40_000 })).data
}
