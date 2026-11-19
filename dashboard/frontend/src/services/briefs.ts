// `GET /api/briefs` (backend/api/briefs.py) and the manual run through the routine scheduler
// (`POST /api/routines/{kind}/run`, backend/api/routines.py). Errors are `{detail}` in Chinese (409 when
// the scheduler cannot take the run now, 403 in demo mode): show `getApiErrorMessage`.
import api from './api'
import type { BriefDay, BriefKind, RunRow } from '@/features/briefs/types'

export type { BriefDay, BriefKind } from '@/features/briefs/types'

export const BRIEFS_KEY = ['briefs'] as const
export const briefsKey = (date: string | null) => [...BRIEFS_KEY, date ?? 'current'] as const

/** Both briefs of `date` (default: the current A-share trading day) with their run state. */
export async function fetchBriefs(date: string | null): Promise<BriefDay> {
  return (await api.get<BriefDay>('/briefs', { params: date ? { date } : undefined })).data
}

/** Ask the scheduler to generate today's brief of `kind` now. */
export async function runBrief(kind: BriefKind): Promise<{ accepted: boolean; run: RunRow; message: string }> {
  return (await api.post(`/routines/${kind}/run`)).data
}
