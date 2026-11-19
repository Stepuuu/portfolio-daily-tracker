// The shared `/api/today` query's order and its 刷新 (services/today.ts builds the query from this).
// Every request is numbered when it leaves; the page shows the answer to the latest request, whatever
// the payload's own `as_of` or ledger revision says: a corrected server clock or a restored backup lowers
// those legitimately, and an unreadable time must not freeze the page. The age the page shows is that of
// the snapshot it shows, from when that answer arrived. Kept apart from React and the HTTP client, so a
// node test drives it with a real query client.
import type { QueryKey } from '@tanstack/react-query'

interface Stamp {
  /** The number of the request this answered. */
  seq: number
  /** Browser time (ms) when the answer arrived. */
  receivedAt: number
}

/** Request numbers, answer stamps and the choice between two answers. */
export function createSnapshotOrder<T extends object>(now: () => number = Date.now) {
  let issued = 0
  const stamps = new WeakMap<T, Stamp>()
  return {
    /** A request leaves: its number. */
    issue(): number {
      issued += 1
      return issued
    },
    /** The answer to request `seq` arrived (stamped now); returns it. */
    received(payload: T, seq: number): T {
      stamps.set(payload, { seq, receivedAt: now() })
      return payload
    },
    /** What to show: the answer to the later request. One without a number (set by hand) counts as newest. */
    pick(current: T | undefined, incoming: T): T {
      const before = current && stamps.get(current)
      const after = stamps.get(incoming)
      return before && after && after.seq < before.seq ? current! : incoming
    },
    /** When the answer shown arrived; null for none (or one set by hand). */
    receivedAt(payload: T | undefined): number | null {
      return (payload && stamps.get(payload)?.receivedAt) ?? null
    },
  }
}

export type TodayFetch<T> = (options: { refresh: boolean; signal: AbortSignal }) => Promise<T>

/** The two calls 刷新 makes on the query client (react-query's QueryClient). */
export interface RefreshClient {
  cancelQueries(filters: { queryKey: QueryKey; exact: boolean }): Promise<void>
  refetchQueries(filters: { queryKey: QueryKey; exact: boolean }, options: { throwOnError: boolean }): Promise<void>
}

/**
 * The query's options for every observer of `key`, 刷新, and the age of a shown snapshot.
 *
 * Polls and window-focus fetches join a request in flight (react-query); 刷新 replaces it: whatever is in
 * flight is cancelled (its late answer never lands), then a new request leaves with `refresh` set. That
 * holds during the very first load too, when react-query would otherwise join the load without it.
 */
export function createTodayFeed<T extends object>(key: QueryKey, fetchToday: TodayFetch<T>, now: () => number = Date.now) {
  const order = createSnapshotOrder<T>(now)
  // Read (and cleared) by the next request that leaves: the one 刷新 starts right after setting it.
  let refreshNext = false
  const options = {
    queryKey: key,
    queryFn: async ({ signal }: { signal: AbortSignal }): Promise<T> => {
      const refresh = refreshNext
      refreshNext = false
      const seq = order.issue()
      return order.received(await fetchToday({ refresh, signal }), seq)
    },
    structuralSharing: (previous: unknown, next: unknown): T => order.pick(previous as T | undefined, next as T),
  }

  /** 刷新: a new request with `refresh` (skipping the backend's quote cache); rejects when it fails. */
  async function refresh(client: RefreshClient): Promise<void> {
    await client.cancelQueries({ queryKey: key, exact: true })
    refreshNext = true
    await client.refetchQueries({ queryKey: key, exact: true }, { throwOnError: true })
  }

  return { options, refresh, receivedAt: order.receivedAt }
}
