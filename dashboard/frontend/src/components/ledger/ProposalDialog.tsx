// The ledger page's review of one proposal, opened by `/ledger?proposal=<id>` (Today's 核对方案 links, the
// pending list, a reversal, an import). A booked proposal shows its durable receipt, also after a reload;
// an unknown id says so and offers nothing to confirm.
import { useLayoutEffect, useRef } from 'react'
import { Button, Dialog, InlineAlert, Skeleton } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { ledgerErrorText } from '@/features/ledger/review'
import { NO_PROPOSAL, ProposalReview, ProposalReviewActions, useProposalReview } from './ProposalReview'
import { useProposal } from './useLedger'

export interface ProposalDialogProps {
  proposalId: string | null
  onClose: () => void
}

export function ProposalDialog({ proposalId, onClose }: ProposalDialogProps) {
  const query = useProposal(proposalId)
  const proposal = query.data && query.data.id === proposalId ? query.data : null
  const review = useProposalReview(proposal ?? NO_PROPOSAL, { onDiscarded: onClose })
  const confirmRef = useRef<HTMLButtonElement>(null)
  const ready = proposal !== null
  useLayoutEffect(() => {
    if (ready) confirmRef.current?.focus()
  }, [ready])

  let footer
  if (proposal) {
    footer = (
      <>
        {review.done && <Button variant="ghost" onClick={onClose}>关闭</Button>}
        <ProposalReviewActions ref={confirmRef} review={review} discardLabel="放弃预览" />
      </>
    )
  } else if (query.isError) {
    footer = <Button variant="primary" data-autofocus onClick={onClose}>关闭</Button>
  }

  return (
    <Dialog open={Boolean(proposalId)} onClose={onClose} title={review.done ? '已入账' : '核对入账'} size="lg" footer={footer}>
      {proposal ? (
        <ProposalReview proposal={proposal} review={review} />
      ) : query.isError ? (
        <InlineAlert tone="warn" title="打不开这份方案">{ledgerErrorText(getApiErrorMessage(query.error))}</InlineAlert>
      ) : (
        <div role="status" aria-label="正在读取方案" className="flex flex-col gap-3">
          <Skeleton lines={2} />
          <Skeleton h={120} />
        </div>
      )}
    </Dialog>
  )
}
