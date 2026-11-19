// `GET /api/today` (holdings at live prices), `GET /api/quotes`, and the alert and routine status the
// Today page shows next to them. Contracts: core/market/today.py, backend/api/{alerts,routines}.py.
import { queryOptions } from '@tanstack/react-query'
import api from './api'
import { createTodayFeed } from '@/features/today/feed'
import type { QuotesPayload, TodayPayload } from '@/features/today/types'
import type { RoutinesStatus } from '@/features/today/snapshot'

export type { QuotesPayload, TodayPayload }

/** Query key shared by the shell (session chip, header totals, connection banner) and the Today page. */
export const TODAY_KEY = ['today'] as const

const feed = createTodayFeed<TodayPayload>(TODAY_KEY, (options) => fetchToday(options))

/**
 * The Today query's own options, for every observer of TODAY_KEY: whichever observer's options a fetch
 * runs with, the answer to the latest request is shown (features/today/feed.ts), never decided by the
 * payload's `as_of` or ledger revision.
 */
export const todayQuery = queryOptions<TodayPayload>(feed.options)

/**
 * 刷新: replaces any request in flight (also the first load) with one that skips the backend's quote cache.
 * Rejects when it fails; a CancelledError means a newer request replaced it.
 */
export const refreshToday = feed.refresh

/** Browser time (ms) when the Today snapshot shown arrived; null for none. Its age is measured from it. */
export const todayReceivedAt = feed.receivedAt

/** The Today payload. `refresh` skips the backend's short quote cache (the 刷新 button only). */
export async function fetchToday(options: { refresh?: boolean; signal?: AbortSignal } = {}): Promise<TodayPayload> {
  const response = await api.get<TodayPayload>('/today', {
    params: options.refresh ? { refresh: 1 } : undefined,
    signal: options.signal,
  })
  return response.data
}

/** Quote objects for up to 50 tickers, in request order. */
export async function fetchQuotes(tickers: string[], refresh = false): Promise<QuotesPayload> {
  const response = await api.get<QuotesPayload>('/quotes', { params: { tickers: tickers.join(','), ...(refresh ? { refresh: 1 } : {}) } })
  return response.data
}

export interface AlertEvent {
  id: number
  rule_id: number
  ticker: string
  name: string | null
  account: string | null
  kind: string
  threshold: string
  trading_date: string
  value: string | null
  price: string | null
  prev_close: string | null
  quote_source: string | null
  quote_time: string | null
  quote_status: string | null
  fired_at: string
  delivery: string
  title: string
  body: string
}

export interface AlertStatus {
  running: boolean
  enabled: boolean
  status: string
  message: string
  rules_enabled: number
  last_cycle_at: string | null
  paused: boolean
  quote_source: string
  notice: string
}

export async function fetchAlertEvents(limit = 20): Promise<{ items: AlertEvent[]; next_before: number | null }> {
  return (await api.get('/alerts/events', { params: { limit } })).data
}

export async function fetchAlertStatus(): Promise<AlertStatus> {
  return (await api.get<AlertStatus>('/alerts/status')).data
}

export async function fetchRoutinesStatus(): Promise<RoutinesStatus> {
  return (await api.get<RoutinesStatus>('/routines/status')).data
}

export interface JournalEntry {
  id: string
  ticker: string
  thesis: string
  invalidation: string
  review_on: string
  created_at: string
}

/** Research notes (the ledger page's 研究复盘 tab); Today lists the ones due for review. */
export async function fetchJournal(): Promise<{ entries: JournalEntry[] }> {
  return (await api.get<{ entries: JournalEntry[] }>('/ledger/journal')).data
}
