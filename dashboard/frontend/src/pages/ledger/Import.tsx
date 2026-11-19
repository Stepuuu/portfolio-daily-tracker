// 账本 › 导入 (/ledger/import): a broker export (交割单, 资金对账单, 持仓, or the system's CSV template) in four
// steps on one page: 1 选择文件, 2 核对列, 3 预览与去重, 4 确认. The import session lives in the backend; its id
// and the step are in the URL (`?session=<id>&step=3&status=review&page=2`), so a reload resumes it, and a
// batch waiting for confirmation resumes its review. Nothing is booked before 确认入账 in the shared review;
// demo and unknown modes upload nothing.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { RotateCcw } from 'lucide-react'
import { Button, Dialog, EmptyState, InlineAlert, Panel, Skeleton, TableSkeleton, cx, numeric, toast } from '@/components/ui'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { deleteImport, importKey, proposeImport } from '@/services/imports'
import { proposalKey } from '@/services/ledger'
import {
  mappingProblems, readImportUrl, resumeStep, structuralProblems, writeImportUrl, type ImportUrl, type StepId,
} from '@/features/import/flow'
import { deleteFailure, proposeFailure, readFailure, type Failure } from '@/features/import/errors'
import { FIRST_PAGE, PAGE_SIZE, REVIEW_SAMPLE, pageCount } from '@/features/import/rows'
import type { ImportRow, ImportSession, RowsView } from '@/features/import/types'
import type { Proposal, Receipt } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { invalidateLedger, useLedger, useProposal } from '@/components/ledger/useLedger'
import { ConfirmStep } from '@/components/import/ConfirmStep'
import { MappingStep } from '@/components/import/MappingStep'
import { FailureAlert } from '@/components/import/Notices'
import { PreviewStep } from '@/components/import/PreviewStep'
import { RowEditDialog } from '@/components/import/RowEditDialog'
import { StepIndicator } from '@/components/import/StepIndicator'
import { UploadStep } from '@/components/import/UploadStep'
import { reloadSession, useImportPatch, useImportSession, type FlowFailure } from '@/components/import/useImport'

interface Prepared {
  proposal: Proposal
  /** "第 1/3 批". */
  label: string
}

export default function LedgerImport() {
  const [params, setParams] = useSearchParams()
  const url = useMemo(() => readImportUrl(params), [params])
  const { readOnlyReason } = useAppMode()
  const client = useQueryClient()
  const ledger = useLedger()
  const accounts = useMemo(() => Object.keys(ledger.data?.accounts ?? {}), [ledger.data])

  const go = useCallback((next: Partial<ImportUrl>, replace = false) => {
    setParams((current) => new URLSearchParams(writeImportUrl({ ...readImportUrl(current), ...next }).slice(1)), { replace })
  }, [setParams])

  const view: RowsView = useMemo(() => ({ status: url.status, offset: (url.page - 1) * PAGE_SIZE, limit: PAGE_SIZE }), [url.status, url.page])
  const query = useImportSession(url.session, view)
  const session = query.data && query.data.id === url.session ? query.data : undefined

  // Per-session state, dropped when another session (or none) is shown.
  const [gone, setGone] = useState<Failure | null>(null)
  const [acks, setAcks] = useState<string[]>([])
  const [reconAck, setReconAck] = useState<string | null>(null)
  const [notice, setNotice] = useState<FlowFailure | null>(null)
  const [prepared, setPrepared] = useState<Prepared | null>(null)
  const [proposing, setProposing] = useState<FlowFailure | null>(null)
  const [editing, setEditing] = useState<ImportRow | null>(null)
  const [discarding, setDiscarding] = useState(false)
  useEffect(() => {
    setGone(null)
    setAcks([])
    setReconAck(null)
    setNotice(null)
    setPrepared(null)
    setProposing(null)
    setEditing(null)
  }, [url.session])

  const onGone = useCallback((failure: Failure) => setGone(failure), [])
  const patch = useImportPatch(url.session, view, onGone)
  const readError = query.isError && !session ? readFailure(httpStatus(query.error), getApiErrorMessage(query.error)) : null
  const lost = gone ?? (readError?.next === 'restart' ? readError : null)

  const restart = useCallback(() => {
    setGone(null)
    go({ session: null, step: null, status: null, page: 1 })
  }, [go])
  const reload = useCallback(() => {
    patch.clearFailure()
    setNotice(null)
    if (url.session) void reloadSession(client, url.session)
  }, [client, patch, url.session])

  // A loaded session continues where it was: a pending batch's review, else the URL's step when allowed.
  const resolved = useRef<string | null>(null)
  useEffect(() => {
    if (!session || resolved.current === session.id) return
    resolved.current = session.id
    const step = resumeStep(session, url.step)
    if (step !== url.step) go({ step }, true)
  }, [session, url.step, go])

  const pending = session?.batches.pending ?? null
  const pendingId = !prepared && pending && !pending.stale ? pending.proposal_id : null
  let step: StepId = url.session ? url.step ?? 2 : 1
  if (session && step >= 3 && structuralProblems(session).length > 0) step = 2
  if (session && step === 4 && !prepared && !pendingId && session.batches.confirmed === 0) step = 3
  // The URL follows a step the session cannot be at (a conflict appeared, nothing is left to confirm).
  useEffect(() => {
    if (session && resolved.current === session.id && url.step !== null && url.step !== step) go({ step }, true)
  }, [session, step, url.step, go])
  const pendingQuery = useProposal(step === 4 ? pendingId : null)
  const proposal = prepared?.proposal ?? (pendingQuery.data && pendingQuery.data.id === pendingId ? pendingQuery.data : null)
  const batchLabel = prepared?.label ?? (pending ? `第 ${pending.number}/${Math.max(session?.batches.total ?? 0, pending.number)} 批` : null)

  // A page past the end (rows left the filter after an edit) moves to the last page.
  const total = session?.rows_total ?? 0
  useEffect(() => {
    if (session && url.page > 1 && total > 0 && (url.page - 1) * PAGE_SIZE >= total) go({ page: pageCount(total) }, true)
  }, [session, total, url.page, go])

  // A retry of a refused propose calls the mutation through a ref (it cannot name itself while being defined).
  const proposeAgain = useRef<() => void>(() => {})
  const propose = useMutation({
    mutationFn: () => proposeImport(url.session!),
    onSuccess: async (result) => {
      setProposing(null)
      setNotice(null)
      client.setQueryData(proposalKey(result.proposal.id), result.proposal)
      setPrepared({ proposal: result.proposal, label: result.batch.label })
      go({ step: 4 })
      await Promise.all([reloadSession(client, result.import.id), invalidateLedger(client)])
    },
    onError: async (error) => {
      const refused: FlowFailure = { ...proposeFailure(httpStatus(error), getApiErrorMessage(error)) }
      if (refused.next === 'restart') return setGone(refused)
      refused.retry = () => proposeAgain.current()
      setProposing(refused)
      // 409: the ledger or the session changed, or a blocker applies: show the preview as it is now.
      if (refused.next === 'again' && url.session) await reloadSession(client, url.session)
    },
  })

  proposeAgain.current = () => propose.mutate()

  const remove = useMutation({
    mutationFn: () => deleteImport(url.session!),
    onSuccess: async () => {
      setDiscarding(false)
      toast.info('已放弃这次导入，账本没有变化')
      restart()
      await invalidateLedger(client)
    },
    onError: (error) => {
      const refused = deleteFailure(httpStatus(error), getApiErrorMessage(error))
      if (refused.next === 'restart') {
        setDiscarding(false)
        restart()
      }
    },
  })
  const removeError = remove.error ? deleteFailure(httpStatus(remove.error), getApiErrorMessage(remove.error)) : null

  // The count of rows the account choice keeps out is the session's (accounts.rows_kept_out); the first page of
  // 需处理 rows only gives the backend's reasons for them.
  const keptOut = step === 2 && (session?.accounts?.rows_kept_out ?? 0) > 0
  const reviewRowsQuery = useImportSession(url.session, REVIEW_SAMPLE, keptOut)
  const reviewRows = keptOut && reviewRowsQuery.data && reviewRowsQuery.data.id === url.session ? reviewRowsQuery.data.rows : undefined

  const reachable = (target: StepId) => {
    if (target === 1) return true
    if (!session) return false
    if (target === 2) return true
    if (target === 3) return mappingProblems(session, acks).length === 0
    return Boolean(prepared || pendingId || session.batches.confirmed > 0)
  }

  const onUploaded = (created: ImportSession) => {
    client.setQueryData(importKey(created.id, FIRST_PAGE), created)
    resolved.current = created.id
    go({ session: created.id, step: 2, status: null, page: 1 })
  }

  const body = () => {
    if (lost) {
      return (
        <Panel>
          <EmptyState
            title={lost.title}
            description={`${lost.message || '导入记录不存在或已过期。'}导入记录在上传 30 天后自动删除；账本不受影响。`}
            action={<Button variant="primary" icon={RotateCcw} onClick={restart}>重新开始</Button>}
          />
        </Panel>
      )
    }
    if (step === 1 || !url.session) {
      return (
        <UploadStep
          accounts={accounts}
          readOnlyReason={readOnlyReason}
          onUploaded={onUploaded}
          current={session}
          onReplace={() => setDiscarding(true)}
        />
      )
    }
    if (!session) {
      if (readError) {
        return <FailureAlert failure={{ ...readError, retry: () => void query.refetch() }} />
      }
      return (
        <Panel padding="none">
          <div className="px-4 pt-4"><Skeleton lines={2} w={420} /></div>
          <TableSkeleton rows={6} cols={7} label="正在读取导入记录" />
        </Panel>
      )
    }
    if (step === 2) {
      return (
        <MappingStep
          session={session}
          sample={session.rows ?? []}
          reviewRows={reviewRows}
          onPatch={(change) => void patch.patch(change)}
          busy={patch.pending}
          failure={patch.failure}
          onReload={reload}
          disabledReason={readOnlyReason}
          acknowledged={acks}
          onAcknowledge={(field) => setAcks((list) => [...list, field])}
          onContinue={() => go({ step: 3 })}
          onDiscard={() => setDiscarding(true)}
        />
      )
    }
    if (step === 3) {
      return (
        <PreviewStep
          session={session}
          rows={session.rows ?? []}
          total={total}
          offset={view.offset}
          status={url.status}
          loading={query.isFetching && query.isPlaceholderData}
          onFilter={(status) => go({ status, page: 1 })}
          onPage={(page) => go({ page })}
          onPatch={(change) => patch.patch(change)}
          onEdit={setEditing}
          busy={patch.pending}
          failure={patch.failure}
          notice={notice}
          onReload={reload}
          reconAck={reconAck}
          onReconAck={setReconAck}
          onPropose={() => propose.mutate()}
          proposing={propose.isPending}
          proposeFailure={proposing}
          onResume={pending && !pending.stale ? () => go({ step: 4 }) : undefined}
          onEditMapping={() => go({ step: 2 })}
          onDiscard={() => setDiscarding(true)}
          disabledReason={readOnlyReason}
        />
      )
    }
    return (
      <ConfirmStep
        session={session}
        proposal={proposal}
        loadingProposal={Boolean(pendingId) && pendingQuery.isPending}
        batchLabel={batchLabel}
        onConfirmed={(receipt: Receipt) => {
          if (proposal) setPrepared({ proposal: { ...proposal, receipt }, label: batchLabel ?? '' })
        }}
        onWithdrawn={(failure) => {
          setPrepared(null)
          setReconAck(null)
          setNotice({ ...failure, next: 'none' })
          go({ step: 3 })
        }}
        onDiscarded={() => {
          setPrepared(null)
          go({ step: 3 })
        }}
        onBack={() => go({ step: 3 })}
        onNextBatch={() => {
          setPrepared(null)
          propose.mutate()
        }}
        proposing={propose.isPending}
        proposeFailure={proposing}
        onReload={reload}
        onRestart={restart}
      />
    )
  }

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 text-body text-fg">
      <h1 className="sr-only">账本 · 导入</h1>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <StepIndicator current={lost ? 1 : step} reachable={reachable} onSelect={(target) => go({ step: target })} />
        {session && !lost && (
          <p className={cx('text-table text-fg-2', numeric)}>
            账户 <span className="font-medium text-fg">{session.account}</span>
            <span className="text-fg-3"> · {session.file.name}</span>
          </p>
        )}
      </div>
      {body()}

      <RowEditDialog
        row={editing}
        statement={session?.statement ?? 'transactions'}
        columns={session?.columns}
        onClose={() => setEditing(null)}
        patch={(change) => patch.patch(change, { local: true })}
        busy={patch.pending}
        disabledReason={readOnlyReason}
        onReload={reload}
        onGoToMapping={() => go({ step: 2 })}
      />

      <Dialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        size="sm"
        title="放弃这次导入？"
        footer={(
          <>
            <Button variant="ghost" data-autofocus onClick={() => setDiscarding(false)}>取消</Button>
            <Button loading={remove.isPending} disabledReason={readOnlyReason} onClick={() => remove.mutate()}>放弃导入</Button>
          </>
        )}
      >
        <div className="flex flex-col gap-3 text-body">
          <p>这次导入的列对应选择会保留，对行的修改不会保留；还没确认的入账预览会撤回。已入账的批次不受影响。</p>
          {removeError && removeError.next !== 'restart' && (
            <InlineAlert tone="warn" title={removeError.title}>{removeError.message}</InlineAlert>
          )}
        </div>
      </Dialog>
    </div>
  )
}
