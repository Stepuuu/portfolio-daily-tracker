import { useCallback, useRef, useState } from 'react'
import { isCancelledError, useQuery, useQueryClient } from '@tanstack/react-query'
import { TODAY_KEY, refreshToday, todayQuery, todayReceivedAt, type TodayPayload } from '@/services/today'
import { pollIntervalMs } from '@/features/today/session'
import { getApiErrorMessage } from '@/services/api'
import { useAppMode } from './useAppMode'

/**
 * `GET /api/today`, polled every 5 s while a held market trades and every 60 s otherwise (never with
 * `refresh`). One query for the whole app: the header's session chip and totals, the connection banner
 * and the Today page all read it. Not used in research-only mode.
 *
 * Order: a poll joins a request in flight; 刷新 replaces it (features/today/feed.ts). The answer to the
 * latest request is shown, and its age (`todayReceivedAt`) is the age of what is shown.
 */
export function useTodayFeed() {
  const { mode } = useAppMode()
  return useQuery<TodayPayload>({
    ...todayQuery,
    enabled: mode !== undefined && mode !== 'lab',
    refetchInterval: (query) => pollIntervalMs(query.state.data),
    refetchOnWindowFocus: true,
    retry: 1,
    staleTime: 0,
  })
}

/** When the Today snapshot on screen arrived (browser ms); null before the first one. */
export function snapshotReceivedAt(data: TodayPayload | undefined): number | null {
  return todayReceivedAt(data)
}

/**
 * The 刷新 button: a new request with `?refresh=1` (skips the backend's quote cache), replacing any
 * request in flight, the first load included. Its error is returned until a newer snapshot arrives, by
 * this button or by the regular poll. A refresh replaced by a newer one is not a failure.
 */
export function useRefreshToday() {
  const client = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [failure, setFailure] = useState<{ message: string; at: number } | null>(null)
  const latest = useRef(0)
  const refresh = useCallback(async () => {
    const id = ++latest.current
    setRefreshing(true)
    setFailure(null)
    try {
      await refreshToday(client)
    } catch (exc) {
      if (id === latest.current && !isCancelledError(exc)) setFailure({ message: getApiErrorMessage(exc), at: Date.now() })
    } finally {
      if (id === latest.current) setRefreshing(false)
    }
  }, [client])
  const shownAt = todayReceivedAt(client.getQueryData<TodayPayload>(TODAY_KEY)) ?? 0
  return { refresh, refreshing, error: failure && failure.at > shownAt ? failure.message : null }
}
