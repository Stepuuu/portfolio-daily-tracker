// Queries for plans. The list is one request for every status, filtered in the page; an open plan's
// (draft or active) numbers against the market and its name come from its detail (the list has no
// `market`), so details are fetched for open plans; ended plans show their own numbers only.
import { useMemo } from 'react'
import { useQuery, useQueries, useQueryClient } from '@tanstack/react-query'
import {
  ACTIVE_PLANS_KEY, PLAN_LIST_KEY, PLANS_KEY, fetchPlan, fetchPlans, planDetailKey,
} from '@/services/plans'
import { getApiErrorMessage } from '@/services/api'
import { todayQuery, type TodayPayload } from '@/services/today'
import type { PlanDetail, PlanItem } from './types'
import { isEnded, listingKey } from './view'

const LIST_REFRESH_MS = 30_000
const DETAIL_REFRESH_MS = 30_000

/** Every plan, newest first. */
export function usePlanList() {
  return useQuery({ queryKey: PLAN_LIST_KEY, queryFn: () => fetchPlans('all'), refetchInterval: LIST_REFRESH_MS, retry: 1 })
}

/** The active plans with their market blocks, fetched once for the page that shows chips and the agenda (Today). */
export function useActivePlans() {
  return useQuery({ queryKey: ACTIVE_PLANS_KEY, queryFn: () => fetchPlans('active', { market: true }), staleTime: 15_000, refetchInterval: 60_000, retry: 1 })
}

export function usePlanDetail(id: number | null) {
  return useQuery({
    queryKey: planDetailKey(id ?? 0),
    queryFn: () => fetchPlan(id as number),
    enabled: id !== null,
    refetchInterval: DETAIL_REFRESH_MS,
    retry: 1,
  })
}

export interface OpenDetails {
  /** Details of the open plans (name, price, distances, position), by plan id. */
  details: Map<number, PlanDetail>
  /** Plan id -> why its detail request failed, for the plans that have none to show. */
  errors: Map<number, string>
  /** Plan id -> when the shown detail was read and why its latest refresh failed: its price may be old. */
  outdated: Map<number, { at: number; error: string }>
}

/** Details of the open plans (draft or active) in `items`, and the requests that failed (never shown as loading). */
export function useOpenDetails(items: PlanItem[] | undefined): OpenDetails {
  const ids = (items ?? []).filter((plan) => !isEnded(plan.status)).map((plan) => plan.id)
  const results = useQueries({
    queries: ids.map((id) => ({ queryKey: planDetailKey(id), queryFn: () => fetchPlan(id), refetchInterval: DETAIL_REFRESH_MS, retry: 1 })),
  })
  const stamp = results.map((result) => `${result.dataUpdatedAt}:${result.errorUpdatedAt}`).join(',')
  return useMemo(() => {
    const details = new Map<number, PlanDetail>()
    const errors = new Map<number, string>()
    const outdated = new Map<number, { at: number; error: string }>()
    results.forEach((result, index) => {
      if (result.data) details.set(result.data.id, result.data)
      if (result.data && result.isError) outdated.set(result.data.id, { at: result.dataUpdatedAt, error: getApiErrorMessage(result.error) })
      else if (result.isError) errors.set(ids[index], getApiErrorMessage(result.error))
    })
    return { details, errors, outdated }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp])
}

/** Refresh every plan query after a write. */
export function useRefreshPlans() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: PLANS_KEY })
}

/** The cached Today payload (never fetches): account names, held tickers' names and prices. */
export function useCachedToday(): TodayPayload | undefined {
  // enabled: false reads the shell's cached payload without fetching; the Today query's own options come
  // along so a fetch started by another observer never runs with these and loses its ordering rule.
  return useQuery<TodayPayload>({ ...todayQuery, enabled: false }).data
}

export interface TodayLookup {
  accounts: string[]
  /** Listing (`listingKey`, any spelling) -> display name, for tickers the ledger holds. */
  names: Map<string, string>
  /** Listing (`listingKey`) -> current price, for tickers the ledger holds. */
  prices: Map<string, string | null>
}

export function useTodayLookup(): TodayLookup {
  const today = useCachedToday()
  return useMemo(() => {
    const names = new Map<string, string>()
    const prices = new Map<string, string | null>()
    for (const account of today?.accounts ?? []) {
      for (const row of [...account.positions, ...account.closed_today]) {
        const key = listingKey(row.ticker)
        // Today names a row by its ticker when it knows no name: that is no name.
        if (row.name && row.name !== row.ticker && !names.has(key)) names.set(key, row.name)
        if (!prices.has(key) || prices.get(key) === null) prices.set(key, row.price)
      }
    }
    return { accounts: (today?.accounts ?? []).map((account) => account.name), names, prices }
  }, [today])
}
