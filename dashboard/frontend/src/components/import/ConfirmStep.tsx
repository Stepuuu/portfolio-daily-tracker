// 4 确认: the batch's ledger proposal in the shared ProposalReview (the same review as every ledger change),
// confirmed or discarded here. After confirmation: the receipt, the next batch if any, and at the end what
// this import booked with links to 流水 and 持仓与成本.
import { useLayoutEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, CheckCircle2, FileCheck2, FileUp } from 'lucide-react'
import { Button, InlineAlert, Panel, Skeleton, buttonClass, cx, numeric } from '@/components/ui'
import { confirmedEvents, proposeState } from '@/features/import/flow'
import type { Failure } from '@/features/import/errors'
import type { ImportSession } from '@/features/import/types'
import type { Proposal, Receipt } from '@/features/ledger/types'
import { NO_PROPOSAL, ProposalReview, ProposalReviewActions } from '@/components/ledger/ProposalReview'
import { FailureAlert } from './Notices'
import { useImportReview, type FlowFailure } from './useImport'

export interface ConfirmStepProps {
  session: ImportSession
  /** The batch's proposal; null while it loads or when nothing is waiting. */
  proposal: Proposal | null
  loadingProposal: boolean
  /** "第 1/3 批". */
  batchLabel: string | null
  onConfirmed: (receipt: Receipt) => void
  onWithdrawn: (failure: Failure) => void
  onDiscarded: () => void
  onBack: () => void
  onNextBatch: () => void
  proposing: boolean
  proposeFailure: FlowFailure | null
  onReload: () => void
  onRestart: () => void
}

function Summary({ session, onRestart }: { session: ImportSession; onRestart: () => void }) {
  const counts = session.counts
  const parts = [
    `已导入 ${counts.imported ?? 0} 行`,
    counts.skipped ? `跳过 ${counts.skipped} 行` : null,
    counts.review ? `需处理 ${counts.review} 行（没有入账）` : null,
    counts.possible_duplicate ? `可能重复 ${counts.possible_duplicate} 行（没有勾选）` : null,
  ].filter(Boolean)
  return (
    <section aria-label="导入结果" className="flex flex-col gap-3 rounded-md border border-line px-4 py-4">
      <p className="flex items-center gap-2 text-title text-fg">
        <CheckCircle2 size={20} aria-hidden className="text-accent" />
        导入完成
      </p>
      <p className={cx('text-body text-fg-2', numeric)}>
        这次导入共入账 {confirmedEvents(session)} 笔（{session.batches.confirmed} 批）。{parts.join(' · ')}。
      </p>
      <div className="flex flex-wrap gap-2">
        <Link to="/ledger" className={buttonClass({ variant: 'primary' })}>查看流水</Link>
        <Link to="/ledger/positions" className={buttonClass()}>持仓与成本</Link>
        <Button variant="ghost" icon={FileUp} onClick={onRestart}>导入另一个文件</Button>
      </div>
    </section>
  )
}

export function ConfirmStep({
  session, proposal, loadingProposal, batchLabel, onConfirmed, onWithdrawn, onDiscarded, onBack, onNextBatch, proposing,
  proposeFailure, onReload, onRestart,
}: ConfirmStepProps) {
  const review = useImportReview(proposal ?? NO_PROPOSAL, session.id, { onConfirmed, onWithdrawn, onDiscarded })
  const confirmRef = useRef<HTMLButtonElement>(null)
  const ready = proposal !== null && !review.done
  useLayoutEffect(() => {
    if (ready) confirmRef.current?.focus()
  }, [ready, proposal?.id])
  const next = session.batches.next
  const nextState = proposeState(session)
  // Right after 确认入账 the session is read again; until then it still lists this proposal as pending.
  const settled = !proposal || session.batches.pending?.proposal_id !== proposal.id
  const over = settled && (review.done || !proposal)
  const finished = over && next === null && session.batches.confirmed > 0

  return (
    <Panel
      title={review.done ? '已入账' : '确认入账'}
      description={batchLabel ? (review.done ? batchLabel : `${batchLabel} · 确认后才写入账本；之后的批次在这一批确认后准备。`) : undefined}
      actions={!review.done && <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>回到预览</Button>}
    >
      <div className="flex flex-col gap-4">
        {proposal ? (
          <>
            <ProposalReview proposal={review.proposal} review={review} />
            {!review.done && (
              <div className="flex flex-wrap justify-end gap-2">
                <ProposalReviewActions ref={confirmRef} review={review} discardLabel="放弃这份预览" />
              </div>
            )}
          </>
        ) : loadingProposal ? (
          <div role="status" aria-label="正在读取入账预览" className="flex flex-col gap-3">
            <Skeleton lines={2} />
            <Skeleton h={120} />
          </div>
        ) : !finished && next === null ? (
          <InlineAlert tone="info" title="没有等待确认的入账预览" action={<Button size="sm" onClick={onBack}>回到预览</Button>}>
            在“预览与去重”里生成下一批的入账预览。
          </InlineAlert>
        ) : null}

        {over && !loadingProposal && next !== null && (
          <InlineAlert
            tone="info"
            title={`还有 ${session.batches.remaining_batches} 批没有入账`}
            action={(
              <Button size="sm" variant="primary" icon={FileCheck2} loading={proposing}
                disabled={!nextState.allowed} title={nextState.reason ?? undefined} onClick={onNextBatch}>
                {nextState.label}
              </Button>
            )}
          >
            {nextState.reason ?? '上一批已入账，下一批按日期顺序继续。'}
          </InlineAlert>
        )}
        {proposeFailure && <FailureAlert failure={proposeFailure} onAgain={onNextBatch} onReload={onReload} />}
        {finished && <Summary session={session} onRestart={onRestart} />}
      </div>
    </Panel>
  )
}
