// 待确认 on the ledger page: previews waiting for confirmation (from the assistant, an import, an earlier
// entry), each with 核对 (opens its review) and 放弃. A preview below the ledger's revision is 已过期 (the
// same rule as the review, against the revision read with this list); a manual preview says when it
// lapses. Discarding one that was booked meanwhile says 已入账.
import { useMutation } from '@tanstack/react-query'
import { Badge, Button, Panel, cx, numeric, toast } from '@/components/ui'
import { fmtRelative } from '@/lib/format'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { discardLedgerProposal } from '@/services/ledger'
import { describeLedgerProposal } from '@/features/pending/proposals'
import { isOutdated, ledgerErrorText } from '@/features/ledger/review'
import type { LedgerSnapshot, Proposal } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useInvalidateLedger } from './useLedger'

export interface PendingListProps {
  ledger: LedgerSnapshot
  onReview: (id: string) => void
}

function PendingRow({ proposal, outdated, onReview }: { proposal: Proposal; outdated: boolean; onReview: (id: string) => void }) {
  const { readOnlyReason } = useAppMode()
  const invalidate = useInvalidateLedger()
  const discard = useMutation({
    mutationFn: () => discardLedgerProposal(proposal.id),
    retry: 0,
    networkMode: 'always',
    onSuccess: async () => {
      toast.info('已放弃这份预览，没有入账')
      await invalidate()
    },
    onError: async (error) => {
      if (httpStatus(error) === 409) {
        toast.info('这份方案已经入账，没有放弃')
        await invalidate()
      } else {
        toast.warn(ledgerErrorText(getApiErrorMessage(error)))
      }
    },
  })
  const text = proposal.events.length ? describeLedgerProposal(proposal) : `重复导入 · 跳过 ${proposal.duplicates} 笔`
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2">
      <div className="min-w-0">
        <p className="break-words text-body text-fg">{text}</p>
        <p className={cx('text-caption text-fg-3', numeric)}>
          {proposal.events.length} 笔待入账{proposal.created_at ? ` · ${fmtRelative(proposal.created_at)}` : ''}
          {proposal.expires_at ? ` · 未确认将在 ${fmtRelative(proposal.expires_at)}失效` : ''}
          {proposal.warnings?.length ? <span className="text-warn"> · {proposal.warnings.length} 条提示</span> : null}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {outdated && <Badge tone="warn" title="账本在预览之后有了变化，需要重新预览">已过期</Badge>}
        <Button size="sm" variant="secondary" onClick={() => onReview(proposal.id)} aria-label={`核对：${text}`}>核对</Button>
        <Button
          size="sm"
          variant="ghost"
          loading={discard.isPending}
          disabledReason={readOnlyReason}
          tipAlign="end"
          onClick={() => discard.mutate()}
          aria-label={`放弃：${text}`}
        >
          放弃
        </Button>
      </div>
    </li>
  )
}

export function PendingList({ ledger, onReview }: PendingListProps) {
  if (!ledger.pending.length) return null
  return (
    <Panel title="待确认" description="确认之前不会入账。" aria-label="待确认方案">
      <ul className="-my-2 divide-y divide-line">
        {ledger.pending.map((proposal) => (
          <PendingRow key={proposal.id} proposal={proposal} outdated={isOutdated(proposal.revision, ledger.revision)} onReview={onReview} />
        ))}
      </ul>
    </Panel>
  )
}
