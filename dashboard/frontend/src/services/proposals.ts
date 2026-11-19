// Everything waiting for confirmation: ledger proposals (`GET /api/ledger` → `pending`, reviewed on the
// ledger page) and generic proposals (`/api/proposals`: alert rules, plans, lessons, watchlist).
import api from './api'
import type { GenericProposal, LedgerPending } from '@/features/pending/proposals'

export type { GenericProposal, LedgerPending, LedgerProposal } from '@/features/pending/proposals'

/**
 * Same key and endpoint as the ledger page's own query, so a confirmation there updates the 待确认 count
 * and the Today banner at once.
 */
export const LEDGER_KEY = ['ledger'] as const
export const PROPOSALS_KEY = ['proposals', 'pending'] as const

export async function fetchLedgerPending(): Promise<LedgerPending> {
  return (await api.get<LedgerPending>('/ledger')).data
}

export async function fetchPendingProposals(): Promise<{ items: GenericProposal[]; pending: number }> {
  return (await api.get<{ items: GenericProposal[]; pending: number }>('/proposals', { params: { status: 'pending' } })).data
}

export async function confirmProposal(id: string): Promise<GenericProposal> {
  return (await api.post<GenericProposal>(`/proposals/${encodeURIComponent(id)}/confirm`)).data
}

export async function rejectProposal(id: string): Promise<GenericProposal> {
  return (await api.delete<GenericProposal>(`/proposals/${encodeURIComponent(id)}`)).data
}
