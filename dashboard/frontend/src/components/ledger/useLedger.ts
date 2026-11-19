// Ledger queries shared by the 记一笔 dialog, the review and the 账本 pages.
import { useCallback } from 'react'
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  LEDGER_CALENDAR_KEY, LEDGER_EVENTS_KEY, LEDGER_KEY, asOfKey, fetchAsOf, fetchCalendar, fetchEvents, fetchLedger,
  fetchProposal, proposalKey,
} from '@/services/ledger'
import { TODAY_KEY } from '@/services/today'
import type { LedgerCalendar } from '@/lib/entryDefaults'
import { useAppMode } from '@/components/shell/useAppMode'

/**
 * `GET /api/ledger`: accounts, revision and pending proposals (refreshed every 15 s, like the 待确认 count).
 * Not before the mode is known: the shell mounts the 记一笔 form until it learns it is research-only.
 */
export function useLedger() {
  const { mode } = useAppMode()
  return useQuery({ queryKey: LEDGER_KEY, queryFn: fetchLedger, refetchInterval: 15_000, enabled: mode !== undefined && mode !== 'lab' })
}

/**
 * Every confirmed event, oldest first. Keyed by the ledger's revision, so a booking made anywhere (another
 * tab, the assistant) shows in 流水 as soon as the 15-second ledger poll sees the new revision.
 */
export function useLedgerEvents(enabled = true) {
  const revision = useLedger().data?.revision
  return useQuery({
    queryKey: [...LEDGER_EVENTS_KEY, revision ?? null],
    queryFn: fetchEvents,
    enabled,
    placeholderData: (previous) => previous,
  })
}

/** Older than this, a calendar read is not trusted for "today" (the day may have changed since). */
const CALENDAR_MAX_AGE_MS = 5 * 60_000

/**
 * `GET /api/ledger/calendar`: today (Beijing time) and each market's latest verified session, by the backend's
 * clock. `calendar` is null while unknown, after a failed read, or when the last good read is too old.
 */
export function useLedgerCalendar(): { calendar: LedgerCalendar | null; loading: boolean } {
  const { mode } = useAppMode()
  const query = useQuery({
    queryKey: LEDGER_CALENDAR_KEY, queryFn: fetchCalendar, enabled: mode !== undefined && mode !== 'lab', refetchInterval: 60_000, staleTime: 30_000, retry: 1,
  })
  const fresh = query.data && !query.isError && Date.now() - query.dataUpdatedAt < CALENDAR_MAX_AGE_MS
  return { calendar: fresh ? query.data! : null, loading: query.isPending }
}

/** The confirmed ledger at the end of `date` (null: not asked), read again when the revision moves. */
export function useLedgerAsOf(date: string | null) {
  const revision = useLedger().data?.revision
  return useQuery({
    queryKey: asOfKey(date ?? '', revision),
    queryFn: ({ signal }) => fetchAsOf(date!, signal),
    enabled: Boolean(date) && revision !== undefined,
    staleTime: 15_000,
    retry: false,
  })
}

/** One proposal by id (a pending preview, or a confirmed one with its receipt). */
export function useProposal(id: string | null | undefined) {
  return useQuery({ queryKey: proposalKey(id ?? ''), queryFn: () => fetchProposal(id!), enabled: Boolean(id), retry: false })
}

/** After a confirmation: every ledger view, the 待确认 count and Today's holdings read again. */
export async function invalidateLedger(client: QueryClient): Promise<void> {
  await Promise.all([
    client.invalidateQueries({ predicate: (query) => String(query.queryKey[0]).startsWith('ledger') }),
    client.invalidateQueries({ queryKey: TODAY_KEY }),
  ])
}

export function useInvalidateLedger() {
  const client = useQueryClient()
  return useCallback(() => invalidateLedger(client), [client])
}

/** Start a browser download of a ledger file (backup, export, template). */
export function download(url: string): void {
  const link = document.createElement('a')
  link.href = url
  link.download = ''
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}
