// The 待确认 tray: everything waiting for the owner's confirmation, grouped 账本 / 提醒 / 计划 / 教训 / 其他.
// Generic proposals are reviewed and confirmed here, in place. Ledger proposals open their review: the
// `ledgerReview` slot renders it inside the tray (EN-1's ProposalReview); without it, 核对方案 opens the
// ledger page's existing review (`/ledger?proposal=<id>`), which is reused rather than rewritten.
import { useState, type ComponentType } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge, Button, Drawer, EmptyState, cx, focusRing, numeric, toast } from '@/components/ui'
import { fmtDateTime, fmtRelative } from '@/lib/format'
import { getApiErrorMessage } from '@/services/api'
import { LEDGER_KEY, PROPOSALS_KEY, confirmProposal, rejectProposal } from '@/services/proposals'
import {
  GROUP_LABEL, LIST_LABEL, describeLedgerProposal, groupProposals, payloadFields, sourceLabel, trayView,
  type GenericProposal, type LedgerProposal,
} from '@/features/pending/proposals'
import { useAppMode } from './useAppMode'
import { usePending } from './usePending'

export interface LedgerReviewProps {
  proposalId: string
  /** Called after the proposal was confirmed or discarded. */
  onDone: () => void
}

export interface PendingTrayProps {
  open: boolean
  onClose: () => void
  /** A ledger proposal's review, rendered inside the tray. Omitted: 核对方案 opens the ledger page's review. */
  ledgerReview?: ComponentType<LedgerReviewProps>
}

function GroupHeading({ children, count }: { children: string; count: number }) {
  return (
    <h3 className="flex items-center gap-2 px-1 pb-1 text-caption font-medium text-fg-3">
      {children}
      <span className={cx('text-fg-3', numeric)}>{count}</span>
    </h3>
  )
}

function LedgerItem({
  proposal, review: Review, onOpen,
}: { proposal: LedgerProposal; review?: ComponentType<LedgerReviewProps>; onOpen: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const warnings = proposal.warnings?.length ?? 0
  return (
    <li className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-body text-fg">{describeLedgerProposal(proposal)}</p>
          <p className={cx('mt-0.5 text-caption text-fg-3', numeric)}>
            {proposal.events.length} 笔待入账{proposal.created_at ? ` · ${fmtRelative(proposal.created_at)}` : ''}
            {warnings > 0 && <span className="text-warn"> · {warnings} 条提示</span>}
          </p>
        </div>
        {Review ? (
          <Button size="sm" variant="secondary" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
            {expanded ? '收起' : '核对方案'}
          </Button>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => onOpen(proposal.id)}>核对方案</Button>
        )}
      </div>
      {Review && expanded && (
        <div className="mt-3">
          <Review proposalId={proposal.id} onDone={() => setExpanded(false)} />
        </div>
      )}
    </li>
  )
}

function GenericItem({ proposal, readOnlyReason }: { proposal: GenericProposal; readOnlyReason: string | undefined }) {
  const [expanded, setExpanded] = useState(false)
  const client = useQueryClient()
  const done = () => client.invalidateQueries({ queryKey: PROPOSALS_KEY })
  const confirm = useMutation({
    mutationFn: () => confirmProposal(proposal.id),
    onSuccess: () => {
      toast.success(`已确认：${proposal.summary}`)
      void done()
    },
  })
  const reject = useMutation({
    mutationFn: () => rejectProposal(proposal.id),
    onSuccess: () => {
      toast.info(`已放弃：${proposal.summary}`)
      void done()
    },
  })
  const failure = confirm.error ?? reject.error
  const fields = payloadFields(proposal.payload)
  const busy = confirm.isPending || reject.isPending
  return (
    <li className="py-3">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className={cx('flex w-full items-start gap-2 rounded-md text-left', focusRing)}
      >
        {expanded
          ? <ChevronDown size={16} aria-hidden className="mt-[3px] shrink-0 text-fg-3" />
          : <ChevronRight size={16} aria-hidden className="mt-[3px] shrink-0 text-fg-3" />}
        <span className="min-w-0">
          <span className="block text-body text-fg">{proposal.summary}</span>
          <span className="mt-0.5 block text-caption text-fg-3">
            {sourceLabel(proposal.source)} · {fmtRelative(proposal.created_at)} · {fmtDateTime(proposal.expires_at)} 前有效
          </span>
        </span>
      </button>
      {expanded && (
        <dl className="ml-6 mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-table">
          {fields.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-fg-3">{key}</dt>
              <dd className={cx('min-w-0 break-words text-fg', numeric)}>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {(proposal.error || failure) && (
        <p className="ml-6 mt-2 text-table text-warn">{failure ? getApiErrorMessage(failure) : proposal.error}</p>
      )}
      <div className="ml-6 mt-2 flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="primary"
          loading={confirm.isPending}
          disabled={busy}
          disabledReason={readOnlyReason}
          onClick={() => confirm.mutate()}
        >
          确认
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} disabledReason={readOnlyReason} onClick={() => reject.mutate()}>
          放弃
        </Button>
      </div>
    </li>
  )
}

export function PendingTray({ open, onClose, ledgerReview }: PendingTrayProps) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const { current, outdated, generic, ledger } = usePending()
  const groups = groupProposals(generic.data?.items ?? [])
  // "Nothing waiting" only when both lists answered with nothing; a list still loading says so, a failed
  // one is 不可用 (rules in features/pending/proposals.ts).
  const view = trayView({
    ledger: { status: ledger.status, count: current.length },
    generic: { status: generic.status, count: groups.length },
  })
  const failure = { ledger: ledger.error, generic: generic.error }
  const retry = { ledger: LEDGER_KEY, generic: PROPOSALS_KEY }
  const openLedger = (id?: string) => {
    onClose()
    navigate(id ? `/ledger?proposal=${encodeURIComponent(id)}` : '/ledger')
  }
  return (
    <Drawer open={open} onClose={onClose} title="待确认" description="确认之前不会写入任何数据。">
      {view.unavailable.map((list) => (
        <p key={list} role="alert" className="mb-3 text-table text-warn">
          {LIST_LABEL[list]} 不可用：{getApiErrorMessage(failure[list])}
          <Button size="sm" variant="ghost" className="ml-2" onClick={() => void client.invalidateQueries({ queryKey: retry[list] })}>重试</Button>
        </p>
      ))}
      {view.loading && <p role="status" className="mb-3 text-table text-fg-3">正在读取待确认的方案…</p>}
      {view.empty ? (
        <EmptyState title="没有待确认的事项" description="助手或导入生成的方案会在这里等你核对。" />
      ) : (
        <div className="flex flex-col gap-5">
          {current.length > 0 && (
            <section aria-label="账本方案">
              <GroupHeading count={current.length}>{GROUP_LABEL.ledger}</GroupHeading>
              <ul className="divide-y divide-line border-y border-line">
                {current.map((proposal) => (
                  <LedgerItem key={proposal.id} proposal={proposal} review={ledgerReview} onOpen={openLedger} />
                ))}
              </ul>
            </section>
          )}
          {groups.map(([group, items]) => (
            <section key={group} aria-label={`${GROUP_LABEL[group]}方案`}>
              <GroupHeading count={items.length}>{GROUP_LABEL[group]}</GroupHeading>
              <ul className="divide-y divide-line border-y border-line">
                {items.map((proposal) => <GenericItem key={proposal.id} proposal={proposal} readOnlyReason={readOnlyReason} />)}
              </ul>
            </section>
          ))}
        </div>
      )}
      {outdated.length > 0 && (
        <p className="mt-5 flex flex-wrap items-center gap-2 text-table text-fg-3">
          <Badge>已过期 {outdated.length}</Badge>
          账本在预览之后有了变化，这些方案需要重新预览。
          <Button size="sm" variant="ghost" onClick={() => openLedger()}>打开账本</Button>
        </p>
      )}
    </Drawer>
  )
}
