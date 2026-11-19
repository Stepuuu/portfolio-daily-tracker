// 3 预览与去重: the rows by status with what each will book, the skipped rows, the opening values a new
// account's holdings need, 对账 (a mismatch must be acknowledged), the backend's blockers, and
// 生成入账预览 for the current batch.
import type { ReactNode } from 'react'
import { FileCheck2, Trash2 } from 'lucide-react'
import { Button, InlineAlert, Panel, cx, numeric } from '@/components/ui'
import { allImported, nothingToBook, proposeState } from '@/features/import/flow'
import { reconAckKey, reconNeedsAck } from '@/features/import/reconcile'
import { skippedLine } from '@/features/import/rows'
import type { ImportPatch, ImportRow, ImportSession } from '@/features/import/types'
import { FileSummary } from './FileSummary'
import { HoldingsReconcile, OpeningForm } from './HoldingsPanels'
import { ImportRowsTable } from './ImportRowsTable'
import { BlockerList, FailureAlert } from './Notices'
import { ReconciliationPanel } from './ReconciliationPanel'
import type { FlowFailure } from './useImport'

export interface PreviewStepProps {
  session: ImportSession
  rows: ImportRow[]
  total: number
  offset: number
  status: string | null
  loading: boolean
  onFilter: (status: string | null) => void
  onPage: (page: number) => void
  onPatch: (body: ImportPatch) => Promise<unknown> | void
  onEdit: (row: ImportRow) => void
  busy: boolean
  /** A PATCH refusal, or why the page came back here (a withdrawn proposal). */
  failure: FlowFailure | null
  notice: FlowFailure | null
  onReload: () => void
  /** The acknowledged reconciliation (its key), or null. */
  reconAck: string | null
  onReconAck: (key: string | null) => void
  onPropose: () => void
  proposing: boolean
  proposeFailure: FlowFailure | null
  /** A pending batch waits for confirmation: go to its review. */
  onResume?: () => void
  onEditMapping: () => void
  onDiscard: () => void
  disabledReason?: string
}

function Line({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-2 border-t border-line pt-4">{children}</div>
}

export function PreviewStep(props: PreviewStepProps) {
  const {
    session, rows, total, offset, status, loading, onFilter, onPage, onPatch, onEdit, busy, failure, notice, onReload, reconAck,
    onReconAck, onPropose, proposing, proposeFailure, onResume, onEditMapping, onDiscard, disabledReason,
  } = props
  const propose = proposeState(session)
  const holdings = session.statement === 'holdings' ? session.holdings : null
  const recon = session.reconciliation
  const ackKey = reconAckKey(recon)
  const needsAck = reconNeedsAck(recon)
  const acked = !needsAck || reconAck === ackKey
  const skipped = skippedLine(session)
  const nothing = nothingToBook(session)
  const pending = session.batches?.pending
  const reason = disabledReason ?? (busy ? undefined : propose.reason ?? (acked ? undefined : '请先在“对账”里确认差异'))
  const blocked = busy || Boolean(disabledReason)

  return (
    <Panel title="预览与去重" description="每一行入账后的样子；已导入、可能重复和需处理的行默认不入账。">
      <div className="flex flex-col gap-4">
        <FileSummary session={session} compact onEditMapping={onEditMapping} />
        {notice && <FailureAlert failure={notice} onReload={onReload} />}
        {failure && <FailureAlert failure={failure} onReload={onReload} />}
        {pending && !pending.stale && onResume && (
          <InlineAlert tone="info" title={`第 ${pending.number} 批的入账预览还没有确认`} action={<Button size="sm" onClick={onResume}>继续核对</Button>}>
            修改这里的任何内容都会撤回那份预览。
          </InlineAlert>
        )}
        {nothing && allImported(session) && (
          <InlineAlert tone="info" title="全部已导入">这个文件里的记录已经都在账本里，不会重复入账。</InlineAlert>
        )}

        {holdings?.mode === 'opening' && (
          <OpeningForm key={JSON.stringify(holdings.opening ?? {})} holdings={holdings} onPatch={onPatch} busy={busy} disabledReason={disabledReason} />
        )}

        <ImportRowsTable
          session={session}
          rows={rows}
          total={total}
          offset={offset}
          status={status}
          loading={loading}
          onFilter={onFilter}
          onPage={onPage}
          onInclude={(row, include) => void onPatch({ rows: [{ row: row.row, include }] })}
          onEdit={onEdit}
          onSkip={(row) => void onPatch({ rows: [{ row: row.row, kind: 'skip' }] })}
          onReset={(row) => void onPatch({ rows: [{ row: row.row, reset: true }] })}
          busy={busy}
          disabledReason={disabledReason}
        />

        {skipped.length > 0 && (
          <p aria-label="跳过的行" className={cx('text-table text-fg-2', numeric)}>
            <span className="text-fg-3">不入账：</span>{skipped.map((item) => item.text).join(' · ')}
          </p>
        )}
        {session.warnings.length > 0 && (
          <ul aria-label="提示" className="flex flex-col gap-1 text-table text-fg-2">
            {session.warnings.map((warning) => <li key={warning} className="break-words">{warning}</li>)}
          </ul>
        )}

        {holdings?.mode === 'reconcile' && <Line><HoldingsReconcile holdings={holdings} disabledReason={disabledReason} /></Line>}
        {!holdings && recon && (
          <Line>
            <ReconciliationPanel
              recon={recon}
              acknowledged={acked}
              onAcknowledge={(value) => onReconAck(value ? ackKey : null)}
              disabled={blocked}
            />
          </Line>
        )}

        <BlockerList blockers={session.blockers} title="生成入账预览前需要处理" />
        {proposeFailure && <FailureAlert failure={proposeFailure} onAgain={onPropose} onReload={onReload} />}

        <div className="flex flex-col gap-2 border-t border-line pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="ghost" icon={Trash2} disabled={busy || proposing} disabledReason={disabledReason} onClick={onDiscard}>放弃这次导入</Button>
            <Button
              variant="primary"
              icon={FileCheck2}
              loading={proposing}
              disabled={busy || !propose.allowed || !acked}
              disabledReason={disabledReason}
              title={reason}
              tipAlign="end"
              onClick={onPropose}
            >
              {propose.label}
            </Button>
          </div>
          {propose.detail && <p className={cx('text-right text-table text-fg-3', numeric)}>{propose.detail}</p>}
          {!disabledReason && reason && <p className="text-right text-table text-fg-3">{reason}</p>}
        </div>
      </div>
    </Panel>
  )
}
