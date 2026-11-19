// `GET /api/app/status` (backend/api/app_status.py): how the backend was started, for the shell.
import api from './api'
import type { Mode } from '@/lib/navigation'

export interface AppStatus {
  /** full, lab (TRACKER_LAB_ONLY=1: research only) or demo (read-only fictional data). */
  mode: Mode
  ledger: { active: boolean | null; revision: number | null }
  version: string
}

export const APP_STATUS_KEY = ['app-status'] as const

/** The status as the backend sent it: check its `mode` (lib/navigation `statusMode`) before use. */
export async function fetchAppStatus(): Promise<unknown> {
  return (await api.get<unknown>('/app/status')).data
}

/**
 * Whether this is a backend of this app that answers its other routes: `GET /api/settings/config`, which
 * every version has (since v3), answers 200 with its current API group. Used before taking a 404 from the
 * status endpoint for an older backend without it.
 */
export async function answersAsThisApp(): Promise<boolean> {
  try {
    const body = (await api.get<unknown>('/settings/config')).data as { current_api_group?: unknown } | null
    return !!body && typeof body === 'object' && typeof body.current_api_group === 'string'
  } catch {
    return false
  }
}
