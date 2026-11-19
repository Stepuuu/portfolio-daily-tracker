// The import page's server state: one page of a session's rows (the summary comes with it; parse, GET and PATCH
// all answer with the counts plus the page asked for, never every row), PATCH with the
// contract's refusals (422 nothing changed, 409 changed elsewhere, 503 ledger busy), and the review of an
// import batch for `ProposalReview` whose confirmation refusals send the page back to the preview.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { fetchImport, importKey, patchImport } from '@/services/imports'
import { confirmFailure, patchFailure, type Failure } from '@/features/import/errors'
import type { ImportPatch, ImportSession, RowsView } from '@/features/import/types'
import type { Proposal, Receipt } from '@/features/ledger/types'
import { useProposalReview, type ReviewState } from '@/components/ledger/ProposalReview'

/** A refusal with what to do about it. */
export interface FlowFailure extends Failure {
  /** Send the same request again ("retry"). */
  retry?: () => void
}

/** One page of the session's rows (and the session's summary); the previous page stays shown while the next loads. */
export function useImportSession(id: string | null, view: RowsView, enabled = true) {
  return useQuery({
    queryKey: importKey(id ?? '', view),
    queryFn: () => fetchImport(id!, view),
    enabled: Boolean(id) && enabled,
    placeholderData: keepPreviousData,
    // Every read re-analyses the file against the ledger: not on every window focus.
    refetchOnWindowFocus: false,
    retry: (count, error) => httpStatus(error) !== 404 && httpStatus(error) !== 422 && count < 1,
  })
}

/**
 * A changed session: the shown page is the answer (PATCH asks for the counts plus that page); every other view
 * of it is read again (now if shown).
 */
export async function storeSession(client: QueryClient, session: ImportSession, view: RowsView): Promise<void> {
  const shown = importKey(session.id, view)
  client.setQueryData(shown, session)
  const same = JSON.stringify(shown)
  await client.invalidateQueries({ queryKey: importKey(session.id), predicate: (query) => JSON.stringify(query.queryKey) !== same })
}

/** Read every view of the session again (after a 409, a confirmation, a withdrawn proposal). */
export function reloadSession(client: QueryClient, id: string): Promise<void> {
  return client.invalidateQueries({ queryKey: importKey(id) })
}

/**
 * PATCH the session. One request at a time (two at once would refuse each other with 409). A refusal is
 * returned to the caller; unless `local`, it is also kept as the page's failure with its next step.
 * A 404 calls `onGone`.
 */
export function useImportPatch(id: string | null, view: RowsView, onGone: (failure: Failure) => void) {
  const client = useQueryClient()
  const [failure, setFailure] = useState<FlowFailure | null>(null)
  const mutation = useMutation({ mutationFn: (body: ImportPatch) => patchImport(id!, body, view) })
  const { mutateAsync } = mutation

  const run: (body: ImportPatch, options?: { local?: boolean }) => Promise<FlowFailure | null> = useCallback(async (
    body: ImportPatch, options: { local?: boolean } = {},
  ): Promise<FlowFailure | null> => {
    if (!id) return null
    setFailure(null)
    try {
      await storeSession(client, await mutateAsync(body), view)
      return null
    } catch (error) {
      const refused: FlowFailure = { ...patchFailure(httpStatus(error), getApiErrorMessage(error)) }
      refused.retry = () => void run(body, options)
      if (refused.next === 'restart') onGone(refused)
      else if (!options.local) setFailure(refused)
      return refused
    }
  }, [client, id, mutateAsync, onGone, view])

  // A new session or page starts without the last one's refusal.
  useEffect(() => setFailure(null), [id])

  return { patch: run, pending: mutation.isPending, failure, clearFailure: () => setFailure(null) }
}

export interface ImportReviewOptions {
  onConfirmed: (receipt: Receipt) => void
  /** Confirmation was refused for good: the proposal is gone; back to the preview with this failure. */
  onWithdrawn: (failure: Failure) => void
  onDiscarded: () => void
}

/**
 * Confirm or discard an import batch for `ProposalReview` (its `review` prop): the ledger's own review
 * (`useProposalReview`: the id captured at the click, no retry, a lost answer read back and shown as 已入账,
 * 仍未入账 or 结果未知), plus the import's part: the session is read again after every outcome, and a
 * confirmation the backend refused (the session changed, expired or was deleted, the ledger changed, the
 * proposal was withdrawn) or a proposal read back as gone sends the page back to the preview.
 */
export function useImportReview(proposal: Proposal, sessionId: string | null, options: ImportReviewOptions): ReviewState {
  const client = useQueryClient()
  const latest = useRef(options)
  useLayoutEffect(() => {
    latest.current = options
  })
  const refresh = useCallback(() => (sessionId ? reloadSession(client, sessionId) : Promise.resolve()), [client, sessionId])
  const review = useProposalReview(proposal, {
    onConfirmed: (receipt) => {
      latest.current.onConfirmed(receipt)
      void refresh()
    },
    onDiscarded: () => {
      latest.current.onDiscarded()
      void refresh()
    },
  })
  const { outcome, unconfirmable = false } = review
  // Each outcome object is one answer: act on it once.
  const handled = useRef<unknown>(null)
  useEffect(() => {
    if (!outcome || handled.current === outcome) return
    const failure = confirmFailure(outcome, unconfirmable)
    if (!failure) return
    handled.current = outcome
    latest.current.onWithdrawn(failure)
    void refresh()
  }, [outcome, unconfirmable, refresh])
  return review
}
