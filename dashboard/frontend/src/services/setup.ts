// The setup wizard's API (dashboard/backend/api/setup.py). The model key goes out once, in the body of
// `saveLlm`, and never comes back: no response carries it. Callers pass it straight from the field and
// clear the field afterwards; nothing here caches the request.
import api from './api'
import type { Proposal } from '@/features/ledger/types'
import type {
  LegacyDraft, LegacyProposalBody, LlmCurrent, LlmSaveBody, LlmSettings, LlmTestResult, SetupFlags, SetupStatus,
} from '@/features/setup/types'

export const SETUP_KEY = ['setup'] as const
export const SETUP_STATUS_KEY = ['setup', 'status'] as const
export const SETUP_LLM_KEY = ['setup', 'llm'] as const
export const legacyDraftKey = (source: string | null, date: string | null) => ['setup', 'legacy', source, date] as const

/** The checklist; `quotes: false` skips the quote probe (the redirect check needs no network). */
export async function fetchSetupStatus(quotes = true): Promise<SetupStatus> {
  return (await api.get<SetupStatus>('/setup/status', { params: quotes ? undefined : { quotes: 0 } })).data
}

export async function completeSetup(): Promise<SetupFlags> {
  return (await api.post<SetupFlags>('/setup/complete')).data
}

export async function dismissSetup(): Promise<SetupFlags> {
  return (await api.post<SetupFlags>('/setup/dismiss')).data
}

export async function fetchLlmSettings(): Promise<LlmSettings> {
  return (await api.get<LlmSettings>('/setup/llm')).data
}

export async function saveLlm(body: LlmSaveBody): Promise<{ saved: boolean; current: LlmCurrent; reloaded: boolean }> {
  return (await api.post('/setup/llm', body)).data
}

/** One bounded request to the saved model (up to about 17 s on the server). */
export async function testLlm(): Promise<LlmTestResult> {
  return (await api.post<LlmTestResult>('/setup/llm/test', undefined, { timeout: 30_000 })).data
}

export async function fetchLegacyDraft(source: string | null, date: string | null): Promise<LegacyDraft> {
  const params: Record<string, string> = {}
  if (source) params.source = source
  if (date) params.date = date
  return (await api.get<LegacyDraft>('/setup/legacy', { params })).data
}

/** A ledger proposal, reviewed and confirmed like any other (`/api/ledger/proposals/{id}/confirm`). */
export async function proposeLegacy(body: LegacyProposalBody): Promise<Proposal> {
  return (await api.post<Proposal>('/setup/legacy/proposals', body)).data
}
