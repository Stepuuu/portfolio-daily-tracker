// The broker-file importer API (dashboard/backend/api/imports.py; its docstring is the contract). Nothing here
// books anything: `proposeImport` makes a standard ledger proposal that is confirmed with
// `confirmLedgerProposal` (services/ledger.ts). Uploads and every request that re-reads the file can take
// longer than the default timeout on a large export.
import api from './api'
import { windowParams } from '@/features/import/flow'
import type { ImportPatch, ImportSession, ProposeResult, RowsView, Statement } from '@/features/import/types'

/** Parsing or re-analysing a 20,000-row export can take a while; never cut it at the default 10 s. */
const IMPORT_TIMEOUT_MS = 120_000

export const IMPORTS_KEY = ['imports'] as const
export const importKey = (id: string, view?: RowsView) =>
  (view ? ['imports', id, view.status ?? 'all', view.offset, view.limit] : ['imports', id]) as readonly unknown[]

/** `POST /parse`: the file (≤ 5 MB), the ledger account, optionally the kind of statement; the rows of `view`. */
export async function parseImport(file: File, account: string, statement: Statement | 'auto' | undefined, view: RowsView): Promise<ImportSession> {
  const form = new FormData()
  form.append('file', file, file.name)
  form.append('account', account)
  if (statement && statement !== 'auto') form.append('statement', statement)
  return (await api.post<ImportSession>('/imports/parse', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    params: windowParams(view),
    timeout: IMPORT_TIMEOUT_MS,
  })).data
}

/** `GET /{id}`: the session recomputed against the current ledger, with one page of rows. */
export async function fetchImport(id: string, view: RowsView): Promise<ImportSession> {
  return (await api.get<ImportSession>(`/imports/${encodeURIComponent(id)}`, { params: windowParams(view), timeout: IMPORT_TIMEOUT_MS })).data
}

/** `PATCH /{id}`: atomic; a refused request (422, 409, 503) changes nothing. Answers with the rows of `view`. */
export async function patchImport(id: string, body: ImportPatch, view: RowsView): Promise<ImportSession> {
  return (await api.patch<ImportSession>(`/imports/${encodeURIComponent(id)}`, body, { params: windowParams(view), timeout: IMPORT_TIMEOUT_MS })).data
}

/** `POST /{id}/propose`: the ledger proposal for the next batch (the same one again while nothing changed). */
export async function proposeImport(id: string): Promise<ProposeResult> {
  return (await api.post<ProposeResult>(`/imports/${encodeURIComponent(id)}/propose`, undefined, { timeout: IMPORT_TIMEOUT_MS })).data
}

/** `DELETE /{id}`: the session and its parsed rows; its unconfirmed proposals are withdrawn. */
export async function deleteImport(id: string): Promise<void> {
  await api.delete(`/imports/${encodeURIComponent(id)}`, { timeout: IMPORT_TIMEOUT_MS })
}
