// The ledger API (dashboard/backend/api/ledger.py): every change is previewed (a proposal) and then
// confirmed; nothing here writes without that second step. Accounts' fee profiles and the fee estimate
// (backend/api/accounts.py) are at the end. Query keys start with 'ledger' so one invalidation refreshes
// every ledger view.
import api from './api'
import type {
  FeeEstimate, FeeProfile, JournalEntry, LedgerAsOf, LedgerEvent, LedgerSnapshot, Proposal, Receipt,
} from '@/features/ledger/types'
import type { LedgerCalendar } from '@/lib/entryDefaults'
import { LEDGER_KEY } from './proposals'

export { LEDGER_KEY }
export const LEDGER_EVENTS_KEY = ['ledger-events'] as const
export const LEDGER_JOURNAL_KEY = ['ledger-journal'] as const
export const LEDGER_CALENDAR_KEY = ['ledger-calendar'] as const
export const proposalKey = (id: string) => ['ledger-proposal', id] as const
/** The ledger as of a date, at a revision (a new revision reads it again). */
export const asOfKey = (date: string, revision: number | undefined) => ['ledger-as-of', date, revision ?? null] as const
export const PROFILES_KEY = ['accounts', 'profiles'] as const

/** State, revision and pending proposals; same key and endpoint as the 待确认 count. */
export async function fetchLedger(): Promise<LedgerSnapshot> {
  return (await api.get<LedgerSnapshot>('/ledger')).data
}

export async function fetchEvents(): Promise<LedgerEvent[]> {
  return (await api.get<{ events: LedgerEvent[] }>('/ledger/events')).data.events
}

/**
 * Preview events; returns the proposal with `before`, `after`, warnings and (with plans) `plan_check`.
 * `transient`: a preview made while typing (记一笔, 冲销); the backend lets it expire (`expires_at`) when it is
 * abandoned, and lists it apart from the assistant's and imports' proposals.
 */
export async function proposeEvents(events: Record<string, unknown>[], options: { transient?: boolean } = {}): Promise<Proposal> {
  return (await api.post<Proposal>('/ledger/proposals', { events, ...(options.transient ? { transient: true } : {}) })).data
}

/** Today (Beijing time) and each market's latest verified trading session, by the backend's calendar. */
export async function fetchCalendar(): Promise<LedgerCalendar> {
  return (await api.get<LedgerCalendar>('/ledger/calendar')).data
}

/** The confirmed ledger at the end of `date`: what a back-dated entry is checked against. */
export async function fetchAsOf(date: string, signal?: AbortSignal): Promise<LedgerAsOf> {
  return (await api.get<LedgerAsOf>('/ledger/as-of', { params: { date }, signal })).data
}

export async function fetchProposal(id: string): Promise<Proposal> {
  return (await api.get<Proposal>(`/ledger/proposals/${encodeURIComponent(id)}`)).data
}

/** Confirming twice returns the same receipt and books nothing twice. Never retried or queued by the caller. */
export async function confirmLedgerProposal(id: string): Promise<Receipt> {
  return (await api.post<Receipt>(`/ledger/proposals/${encodeURIComponent(id)}/confirm`)).data
}

/** Discard a pending preview; 409 when it was booked meanwhile (the caller shows it as 已入账). */
export async function discardLedgerProposal(id: string): Promise<void> {
  await api.delete(`/ledger/proposals/${encodeURIComponent(id)}`)
}

/**
 * Discard a preview that is being left behind (the dialog closed, the tab is going away): a `keepalive`
 * request outlives the page. Resolves to whether that is settled (discarded, already gone or booked, or
 * refused for good); false only without an answer or with a server error, worth trying again. Never throws.
 */
export async function abandonLedgerProposal(id: string): Promise<boolean> {
  try {
    const response = await fetch(`/api/ledger/proposals/${encodeURIComponent(id)}`, { method: 'DELETE', keepalive: true })
    return response.status < 500
  } catch {
    return false
  }
}

/**
 * Opening balances from the legacy holdings file dated on or before `date`. `contributions` gives the
 * 累计净投入 (CNY) of the accounts the file has none for (`missingContributions` of the refusal).
 */
export async function previewOpening(date: string, contributions?: Record<string, string>): Promise<Proposal> {
  return (await api.post<Proposal>('/ledger/opening-preview', contributions ? { date, contributions } : { date })).data
}

export const BACKUP_URL = '/api/ledger/backup.sqlite3'
export const EXPORT_URL = '/api/ledger/export.json'
/** The standard CSV template (UTF-8, one `external_id` per row), imported on /ledger/import. */
export const TEMPLATE_URL = '/api/ledger/template.csv'

/** Research notes are read with `fetchJournal` (services/today.ts, key LEDGER_JOURNAL_KEY, shared with Today). */
export async function addJournalEntry(entry: Pick<JournalEntry, 'ticker' | 'thesis' | 'invalidation' | 'review_on'>): Promise<JournalEntry> {
  return (await api.post<JournalEntry>('/ledger/journal', entry)).data
}

// ------------------------------------------------------------------------------------------------
// Accounts

/** Saved profiles and the preset that applies to accounts without one. */
export async function fetchProfiles(): Promise<{ items: FeeProfile[]; preset: FeeProfile }> {
  return (await api.get<{ items: FeeProfile[]; preset: FeeProfile }>('/accounts/profiles')).data
}

export async function saveProfile(account: string, profile: Omit<FeeProfile, 'is_default' | 'account' | 'updated_at'>): Promise<FeeProfile> {
  return (await api.put<FeeProfile>('/accounts/profile', profile, { params: { account } })).data
}

/** GET, so it also works in the read-only demo. */
export async function fetchFeeEstimate(params: {
  account: string; ticker: string; side: 'buy' | 'sell'; quantity: string; price: string; date: string
}, signal?: AbortSignal): Promise<FeeEstimate> {
  return (await api.get<FeeEstimate>('/accounts/fee-estimate', { params, signal })).data
}
