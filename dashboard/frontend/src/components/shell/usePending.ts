import { useQuery } from '@tanstack/react-query'
import { LEDGER_KEY, PROPOSALS_KEY, fetchLedgerPending, fetchPendingProposals } from '@/services/proposals'
import { pendingCount, splitLedgerPending } from '@/features/pending/proposals'
import { useAppMode } from './useAppMode'

/** Ledger and generic proposals waiting for confirmation: the 待确认 chip, the tray and Today's banner. */
export function usePending() {
  const { mode } = useAppMode()
  const enabled = mode !== undefined && mode !== 'lab'
  const ledger = useQuery({ queryKey: LEDGER_KEY, queryFn: fetchLedgerPending, enabled, refetchInterval: 15_000 })
  const generic = useQuery({ queryKey: PROPOSALS_KEY, queryFn: fetchPendingProposals, enabled, refetchInterval: 30_000, retry: false })
  const { current, outdated } = splitLedgerPending(ledger.data)
  return {
    ledger,
    generic,
    /** Ledger proposals made against the current revision (confirmable), newest first. */
    current,
    /** Ledger previews the ledger has moved past; they must be previewed again. */
    outdated,
    count: pendingCount(ledger.data, generic.data?.items),
  }
}
