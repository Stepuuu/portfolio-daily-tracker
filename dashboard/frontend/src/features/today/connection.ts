// When the shell says the connection is down. Rule (plan 2.4, F1): the data shown is older than twice the
// poll interval, or the last request failed. Age is measured on the browser's clock from the moment the
// answer shown arrived (features/today/feed.ts stamps it), so a clock difference between the browser and
// the server never raises a false alarm. A request in flight changes nothing: the data on screen is as old
// as it is until an answer arrives (a hanging refresh, or a tab back from the background that is
// fetching, still shows the age). Which answer is shown is decided by request order (feed.ts), never by
// the payload's own time.

export type ConnectionState = 'ok' | 'stale' | 'failed'

export interface ConnectionInput {
  /** Browser time (ms) when the answer shown arrived; null before the first one. */
  receivedAt: number | null
  /** The poll interval that applied after that response. */
  intervalMs: number
  /** The latest request failed (after its retries). */
  failed: boolean
  now: number
}

export function connectionState({ receivedAt, intervalMs, failed, now }: ConnectionInput): ConnectionState {
  if (failed) return 'failed'
  if (receivedAt === null) return 'ok' // first load: the page shows its own loading state
  return now - receivedAt > 2 * intervalMs ? 'stale' : 'ok'
}
