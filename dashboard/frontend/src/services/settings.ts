// What 设置 reads and writes besides the APIs other pages already have: the chat model choice
// (`/api/settings`), the holiday table's years, the assistant's lesson and profile proposals (including the
// rejected ones, which can be deleted), running a routine now, and file downloads that report a refusal.
import api from './api'
import type { GenericProposal } from '@/features/pending/proposals'
import { MEMORY_PROPOSAL_KINDS } from '@/features/settings/memory'

// -- the chat model (`/api/settings/config`, the v3 configuration's API groups) ---------------------

export interface ChatModel {
  id: string
  name: string
  description: string
  supports_vision: boolean
}

export interface ChatConfig {
  current_api_group: string
  current_model: string
  api_groups: Record<string, { name: string; description: string; models: ChatModel[] }>
}

export const CHAT_CONFIG_KEY = ['settings'] as const

export async function fetchChatConfig(): Promise<ChatConfig> {
  return (await api.get<ChatConfig>('/settings/config')).data
}

async function post(path: string, body: Record<string, string>): Promise<void> {
  const response = (await api.post<{ success?: boolean; error?: string }>(path, body)).data
  if (response?.success === false) throw new Error(response.error || '配置更新失败')
}

/**
 * Switch the API group (when it changed) and then the model; returns what was changed. A failure after the
 * group switched says so, so the page can tell the person what is saved now.
 */
export async function saveChatModel(current: ChatConfig, group: string, model: string): Promise<string[]> {
  const changed: string[] = []
  try {
    if (group !== current.current_api_group) {
      await post('/settings/api-group', { group_name: group })
      changed.push('API 组')
    }
    if (model !== current.current_model || changed.length) {
      await post('/settings/model', { model_id: model })
      changed.push('模型')
    }
  } catch (error) {
    if (changed.length) throw new PartialSave(changed, error)
    throw error
  }
  return changed
}

/** Thrown when the API group switched but the model did not: `saved` names what changed, `reason` why the rest failed. */
export class PartialSave extends Error {
  saved: string[]
  reason: unknown
  constructor(saved: string[], reason: unknown) {
    super(`${saved.join('、')}已更新，但后续保存失败`)
    this.saved = saved
    this.reason = reason
  }
}

// -- the calendar's coverage ---------------------------------------------------------------------------

export interface CalendarCoverage {
  today: string
  markets: Record<string, { label: string; years: number[] }>
}

export const CALENDAR_KEY = ['settings', 'calendar'] as const

export async function fetchCalendarCoverage(): Promise<CalendarCoverage> {
  return (await api.get<CalendarCoverage>('/settings/calendar')).data
}

// -- the assistant's memory proposals ----------------------------------------------------------------

export const memoryProposalsKey = (status: 'pending' | 'rejected') => ['proposals', 'memory', status] as const

/** Lesson and profile proposals with `status`, newest first (`GET /api/proposals?kind=…`). */
export async function fetchMemoryProposals(status: 'pending' | 'rejected'): Promise<GenericProposal[]> {
  const params = new URLSearchParams({ status })
  for (const kind of MEMORY_PROPOSAL_KINDS) params.append('kind', kind)
  return (await api.get<{ items: GenericProposal[] }>(`/proposals?${params}`)).data.items
}

/** Delete a rejected lesson or profile proposal: the assistant may propose that text again. */
export async function forgetProposal(id: string): Promise<void> {
  await api.delete(`/proposals/${encodeURIComponent(id)}/decision`)
}

// -- routines ---------------------------------------------------------------------------------------------

export const ROUTINES_KEY = ['routines-status'] as const

/** Run today's routine now (202 with the backend's message; 409 when the scheduler is not running). */
export async function runRoutine(kind: string): Promise<string> {
  const body = (await api.post<{ message?: string }>(`/routines/${encodeURIComponent(kind)}/run`)).data
  return body?.message || '已安排运行，稍后刷新状态查看结果。'
}

// -- files ----------------------------------------------------------------------------------------------

export const LEDGER_BACKUP_URL = '/api/ledger/backup.sqlite3'
export const LEDGER_EXPORT_URL = '/api/ledger/export.json'
export const DESK_BACKUP_URL = '/api/desk/backup.sqlite3'

/**
 * Save a file the backend sends, under `filename`. A refusal (no ledger yet, a busy store) is thrown with the
 * backend's own words instead of being saved as the file.
 */
export async function downloadFile(url: string, filename: string): Promise<void> {
  const response = await fetch(url, { credentials: 'same-origin' })
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      if (body && typeof body.detail === 'string') detail = body.detail
    } catch {
      // not JSON: the status says enough
    }
    throw new Error(detail || `下载失败（${response.status}）`)
  }
  const href = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(href), 10_000)
}
