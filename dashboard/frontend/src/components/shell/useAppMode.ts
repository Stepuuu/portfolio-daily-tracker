import { useEffect } from 'react'
import { isCancelledError, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { APP_STATUS_KEY, answersAsThisApp, fetchAppStatus, type AppStatus } from '@/services/app'
import { httpStatus } from '@/services/api'
import { DEMO_REASON, UNKNOWN_MODE_REASON, isMissingRoute, resolveAppMode, statusMode, type Mode } from '@/lib/navigation'

export { DEMO_REASON, UNKNOWN_MODE_REASON }

export interface AppMode {
  /**
   * The mode navigation follows. Undefined until the backend has answered; full for an older backend
   * confirmed to lack the endpoint, and also while `unknown` (every page shown, read-only).
   */
  mode: Mode | undefined
  demo: boolean
  lab: boolean
  /** The status could not be read (timeout, 5xx, an unconfirmed 404, no known mode): nothing is writable. */
  unknown: boolean
  /** Write controls are disabled: demo, unknown, or not answered yet. */
  readOnly: boolean
  /**
   * Pass to every control that writes or sends to the model (`disabledReason`, or return early); undefined
   * when allowed.
   */
  readOnlyReason: string | undefined
  status: AppStatus | undefined
  /** Ask again now (the unknown notice's 重试). */
  retry: () => void
}

type StatusRead =
  | { ok: true; status: AppStatus }
  /** `absent`: an older backend of this app without the endpoint (full mode). */
  | { ok: false; httpStatus: number | undefined; absent: boolean }

/**
 * One read of the status, settled either way: a failure is a result too (with its HTTP status), so a
 * retry never resets the shell to "not answered yet" (which unmounted the desk and refetched on every
 * remount). Two attempts, a second apart, before a failure counts.
 *
 * An answer without a mode the shell knows (`{}`, a new mode) is a failure like a 503: unknown, retried.
 * A 404 means "an older backend without this endpoint" only when FastAPI itself said the route does not
 * exist and the backend answers another route every version has (`answersAsThisApp`); a 404 from a proxy,
 * a dev server or anything else in front of it is the unknown mode, retried.
 */
async function readStatus(): Promise<StatusRead> {
  let failure: StatusRead = { ok: false, httpStatus: undefined, absent: false }
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (attempt) await new Promise((resolve) => setTimeout(resolve, 1000))
    try {
      const body = await fetchAppStatus()
      const mode = statusMode(body)
      if (mode) return { ok: true, status: { ...(body as AppStatus), mode } }
      failure = { ok: false, httpStatus: 200, absent: false }
    } catch (exc) {
      const code = httpStatus(exc)
      const body = (exc as { response?: { data?: unknown } } | undefined)?.response?.data
      if (isMissingRoute(code, body) && await answersAsThisApp()) return { ok: false, httpStatus: code, absent: true }
      failure = { ok: false, httpStatus: code, absent: false }
    }
  }
  return failure
}

// Its own key: the cached value is a settled read (StatusRead), not the endpoint's AppStatus.
const STATUS_READ_KEY = [...APP_STATUS_KEY, 'read'] as const

/**
 * How often the mode is read again: every 15 s while it is unknown, every minute once known. The backend's
 * mode can change under an open tab (`make demo`, then `make start`, or the reverse), so a known mode is
 * not kept for good; `watchBackendReturn` reads it again at once when the backend answers after an outage.
 */
export function statusRefetchInterval(read: StatusRead | undefined): number | false {
  if (!read) return false
  return !read.ok && !read.absent ? 15_000 : 60_000
}

/** The backend could not be reached: no answer at all (stopped, network), or a proxy's 502 / 503 / 504. */
function unreachable(error: unknown): boolean {
  if (isCancelledError(error)) return false
  const code = httpStatus(error)
  return code === undefined || code === 502 || code === 503 || code === 504
}

/**
 * Calls `onReturn` once each time a query succeeds after one failed because the backend could not be
 * reached: it may have restarted in another mode. Returns the unsubscribe.
 */
export function watchBackendReturn(client: QueryClient, onReturn: () => void): () => void {
  let down = false
  return client.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated') return
    if (event.action.type === 'error' && unreachable(event.action.error)) down = true
    else if (event.action.type === 'success' && down) {
      down = false
      onReturn()
    }
  })
}

/** For the shell (mounted once): the mode is read again whenever the backend answers after an outage. */
export function useModeFollowsBackend(): void {
  const client = useQueryClient()
  useEffect(() => watchBackendReturn(client, () => void client.refetchQueries({ queryKey: STATUS_READ_KEY, exact: true })), [client])
}

/**
 * How the backend runs (`GET /api/app/status`): read on page load, again every minute (every 15 s while
 * unknown) and when the backend answers after an outage (`useModeFollowsBackend` in the shell). The banner
 * and every write control follow it without a reload.
 */
export function useAppMode(): AppMode {
  const query = useQuery({
    queryKey: STATUS_READ_KEY,
    queryFn: readStatus,
    staleTime: Infinity,
    retry: false,
    refetchOnMount: false,
    refetchInterval: (current) => statusRefetchInterval(current.state.data),
  })
  const read = query.data
  const resolved = resolveAppMode({
    mode: read?.ok ? read.status.mode : undefined,
    failed: read !== undefined && !read.ok,
    status: read && !read.ok ? read.httpStatus : undefined,
    absent: read !== undefined && !read.ok && read.absent,
  })
  return {
    ...resolved,
    demo: resolved.mode === 'demo',
    lab: resolved.mode === 'lab',
    status: read?.ok ? read.status : undefined,
    retry: () => void query.refetch(),
  }
}
