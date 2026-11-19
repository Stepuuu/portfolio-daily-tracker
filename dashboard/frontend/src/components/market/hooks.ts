// Queries shared by 行情, 提醒, the stock drawer and Today's alert chips. Polling follows Today's rule
// (features/today/session.ts) applied to the markets of the tickers on screen.
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { AxiosError } from 'axios'
import { useTodayFeed } from '@/components/shell/useTodayFeed'
import { DEMO_REASON, useAppMode } from '@/components/shell/useAppMode'
import { getApiErrorMessage } from '@/services/api'
import { fetchWatchlist, WATCHLIST_KEY } from '@/services/watchlist'
import { RULES_KEY, fetchAlertRules } from '@/services/alerts'
import { QUOTES_KEY, fetchQuotes } from '@/services/market'
import { watchPollMs } from '@/features/market/watchlist'
import { identityKey, identityOf } from '@/lib/instrument'
import { POLL_OUT_OF_SESSION_MS } from '@/features/today/session'

/** An API error in words; the demo's English 403 becomes the shell's own reason. */
export function errorText(error: unknown): string {
  const status = (error as AxiosError)?.response?.status
  if (status === 403) return DEMO_REASON
  return getApiErrorMessage(error)
}

/** `/api/today`'s session (every market), for the poll rhythm. */
export function useSession() {
  return useTodayFeed().data?.session
}

/** The watchlist; with quotes it polls like Today. */
export function useWatchlist(quotes: boolean) {
  const { mode } = useAppMode()
  const session = useSession()
  return useQuery({
    queryKey: [...WATCHLIST_KEY, quotes ? 'quotes' : 'plain'],
    queryFn: ({ signal }) => fetchWatchlist({ quotes, signal }),
    enabled: mode !== undefined && mode !== 'lab',
    refetchInterval: quotes ? (query) => watchPollMs(query.state.data?.items.map((item) => item.ticker) ?? [], session) : 60_000,
    staleTime: quotes ? 0 : 30_000,
    retry: 1,
  })
}

/**
 * Alert rules. Without quotes it is cheap and shared by every chip on Today; with quotes (price and
 * distance) it polls like the quotes, but not more often than every 10 s.
 */
export function useAlertRules(quotes: boolean) {
  const { mode } = useAppMode()
  const session = useSession()
  return useQuery({
    queryKey: [...RULES_KEY, quotes ? 'quotes' : 'plain'],
    queryFn: ({ signal }) => fetchAlertRules({ quotes, signal }),
    enabled: mode !== undefined && mode !== 'lab',
    refetchInterval: quotes
      ? (query) => Math.max(10_000, watchPollMs(query.state.data?.items.map((rule) => rule.ticker) ?? [], session))
      : 30_000,
    staleTime: quotes ? 0 : 20_000,
    retry: false,
  })
}

/** One ticker's quote, polled like Today. */
export function useQuote(ticker: string | null) {
  const session = useSession()
  return useQuery({
    queryKey: [...QUOTES_KEY, ticker],
    queryFn: ({ signal }) => fetchQuotes([ticker!], signal),
    enabled: Boolean(ticker),
    refetchInterval: () => (ticker ? watchPollMs([ticker], session) : POLL_OUT_OF_SESSION_MS),
    select: (payload) => ({ ...payload, quote: payload.quotes[0] ?? null }),
    retry: 1,
  })
}

/**
 * Display names for tickers: holdings (Today), the watchlist, then quote names for the rest (one
 * batched request). Every spelling of one listing shares a name (the backend's `identity`).
 */
export function useNames(tickers: string[]): Map<string, string> {
  const today = useTodayFeed().data
  const watch = useWatchlist(false).data
  const known = useMemo(() => {
    const names = new Map<string, string>()
    for (const account of today?.accounts ?? []) {
      for (const row of [...account.positions, ...account.closed_today]) {
        if (row.name && row.name !== row.ticker) names.set(identityOf(row) ?? row.ticker, row.name)
      }
    }
    for (const item of watch?.items ?? []) if (item.name) names.set(identityOf(item) ?? item.ticker, item.name)
    return names
  }, [today, watch])
  const missing = [...new Set(tickers.filter((ticker) => !known.has(identityKey(ticker) ?? ticker)))].sort().slice(0, 50)
  const quoted = useQuery({
    queryKey: [...QUOTES_KEY, 'names', missing.join(',')],
    queryFn: ({ signal }) => fetchQuotes(missing, signal),
    enabled: missing.length > 0,
    staleTime: 10 * 60_000,
    retry: false,
  })
  return useMemo(() => {
    const names = new Map(known)
    for (const quote of quoted.data?.quotes ?? []) if (quote.name) names.set(identityKey(quote.ticker) ?? quote.ticker, quote.name)
    return names
  }, [known, quoted.data])
}

/** The name of `ticker` from a names map, else null. */
export function nameOf(names: Map<string, string>, ticker: string): string | null {
  return names.get(identityKey(ticker) ?? ticker) ?? null
}
