// `/api/plans` (backend/api/plans.py): trade plans, their alert rules, linked trades and the market block.
// Errors are `{detail}` in Chinese (404, 409, 422, 503; 403 in demo mode): show `getApiErrorMessage`.
import api from './api'
import type { PlanBody, PlanDetail, PlanEdit, PlanList } from '@/features/plans/types'

export type { PlanBody, PlanDetail, PlanEdit, PlanList } from '@/features/plans/types'

/** Query keys. Every plan query starts with PLANS_KEY, so one invalidation refreshes them all. */
export const PLANS_KEY = ['plans'] as const
export const PLAN_LIST_KEY = [...PLANS_KEY, 'list'] as const
export const ACTIVE_PLANS_KEY = [...PLANS_KEY, 'active', 'market'] as const
export const planDetailKey = (id: number) => [...PLANS_KEY, 'detail', id] as const

export type PlanStatusQuery = 'draft' | 'active' | 'closed' | 'cancelled' | 'all'

/** The plans; `market` adds each open plan's market block (one Today payload and one quote request). */
export async function fetchPlans(status: PlanStatusQuery = 'all', options: { ticker?: string; market?: boolean } = {}): Promise<PlanList> {
  const params = { status, ...(options.ticker ? { ticker: options.ticker } : {}), ...(options.market ? { market: 1 } : {}) }
  return (await api.get<PlanList>('/plans', { params })).data
}

export async function fetchPlan(id: number): Promise<PlanDetail> {
  return (await api.get<PlanDetail>(`/plans/${id}`)).data
}

export async function createPlan(body: PlanBody): Promise<PlanDetail> {
  return (await api.post<PlanDetail>('/plans', body)).data
}

/** Only the changed fields, with `if_version`: a plan changed since is refused with 409. */
export async function updatePlan(id: number, body: PlanEdit): Promise<PlanDetail> {
  return (await api.patch<PlanDetail>(`/plans/${id}`, body)).data
}

/** draft -> active; creates the alert rules. Repeating it repairs them. */
export async function activatePlan(id: number): Promise<PlanDetail> {
  return (await api.post<PlanDetail>(`/plans/${id}/activate`)).data
}

/** active -> closed; the reason is required. */
export async function closePlan(id: number, reason: string): Promise<PlanDetail> {
  return (await api.post<PlanDetail>(`/plans/${id}/close`, { reason })).data
}

/** draft or active -> cancelled; the reason is optional. */
export async function cancelPlan(id: number, reason?: string): Promise<PlanDetail> {
  return (await api.post<PlanDetail>(`/plans/${id}/cancel`, reason ? { reason } : {})).data
}
