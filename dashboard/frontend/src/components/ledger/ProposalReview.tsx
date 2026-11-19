// 核对入账: what a ledger preview will book, before anything is booked. The events, the 项目 / 变更前 /
// 变更后 table, the ledger's warnings, the trade-plan check and an `extra` slot (plans package). 确认入账
// books it (toast 「已入账 N 笔 · 版本 R」); a preview made before the ledger last changed cannot be
// confirmed. Used by the 记一笔 dialog, the ledger page (`/ledger?proposal=<id>`) and, through
// `LedgerProposalReview`, in place in the 待确认 tray.
//
// One confirmation, one booking: the request carries the id of the proposal on screen when 确认入账 was
// pressed (never re-read later, never retried or queued while offline); an answer for another proposal
// never lands on this one; a keypress confirms only when it started on the button after the review was
// shown (a held Enter from the form does not). When the answer is lost, nothing claims 没有入账: the
// proposal is read back and the review says 已入账, 未入账 or 结果未知.
import {
  forwardRef, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent, type MouseEvent, type ReactNode,
} from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check } from 'lucide-react'
import { Badge, Button, InlineAlert, LongValue, Skeleton, cx, numeric, toast, type ButtonProps } from '@/components/ui'
import type { ToastAction } from '@/lib/toast'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { confirmLedgerProposal, discardLedgerProposal, fetchProposal, proposalKey } from '@/services/ledger'
import {
  SHOWN_EVENTS, confirmFailure, eventLineShown, isOutdated, ledgerErrorText, proposalStatus, receiptMessage, shownEvents, warningText,
} from '@/features/ledger/review'
import { noteMarks, tickerNames } from '@/features/ledger/events'
import type { Proposal, Receipt } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import type { LedgerReviewProps } from '@/components/shell/PendingTray'
import { ChangesTable } from './ChangesTable'
import { PlanCheck } from './PlanCheck'
import { invalidateLedger, useLedger, useLedgerEvents, useProposal } from './useLedger'

/** Stands in while there is no proposal yet (the review hook needs one on every render); never shown or sent. */
export const NO_PROPOSAL: Proposal = {
  id: '', revision: -1, events: [], before: { accounts: {}, date: null }, after: { accounts: {}, date: null }, duplicates: 0, warnings: [],
}

export interface ReviewOptions {
  /** After 确认入账 succeeded (the toast is already shown). */
  onConfirmed?: (receipt: Receipt) => void
  /** After 放弃 discarded the preview. */
  onDiscarded?: () => void
  /** A button on the confirmation toast, e.g. 再记一笔. */
  toastAction?: (receipt: Receipt) => ToastAction | undefined
}

/**
 * What became of the last 确认入账 of this proposal: refused by the backend (nothing booked), read back after
 * a lost answer (still pending or gone: 未入账), being read back, or not knowable (结果未知).
 */
export type ConfirmOutcome =
  | { id: string; state: 'refused'; message: string }
  | { id: string; state: 'not_booked'; message: string }
  | { id: string; state: 'checking' }
  | { id: string; state: 'unknown' }

export interface ReviewState {
  proposal: Proposal
  confirm: () => void
  discard: () => void
  confirming: boolean
  discarding: boolean
  /** Why the last confirm or discard failed, in Chinese. */
  error: string | null
  /** Booked already (a receipt exists). */
  done: boolean
  /** The ledger changed after this preview: it must be previewed again. */
  outdated: boolean
  /** Why writes are off (demo, or the app mode is not known). */
  blocked?: string
  /** The last confirmation's outcome when it did not book (null otherwise). */
  outcome?: ConfirmOutcome | null
  /** Read the proposal back again (after 结果未知). */
  recheck?: () => void
  /** 确认入账 cannot book this preview (refused, expired, outcome not known); the reason is on screen. */
  unconfirmable?: boolean
}

const UNKNOWN_TEXT = '确认请求没有得到回应，无法确定是否已入账。请刷新后核对流水，不要重复提交。'
const STILL_PENDING = '确认请求没有送达，这份预览仍未入账。可以再次确认：同一份预览不会重复入账。'
const GONE = '这份预览已不存在（已放弃或已失效），没有入账。'
const EXPIRED = '这份预览超过 2 小时没有确认，已失效，没有入账。请重新预览。'

/** Confirm and discard for one preview, shared by the review body and the buttons wherever they sit. */
export function useProposalReview(proposal: Proposal, options: ReviewOptions = {}): ReviewState {
  const client = useQueryClient()
  const ledger = useLedger()
  const { readOnlyReason } = useAppMode()
  const [outcome, setOutcome] = useState<ConfirmOutcome | null>(null)
  const [receipts, setReceipts] = useState<Record<string, Receipt>>({})
  // The proposal on screen now; answers for any other id are not attached to it.
  const shown = useRef(proposal.id)
  const latest = useRef(options)
  useLayoutEffect(() => {
    shown.current = proposal.id
    latest.current = options
  })
  const settle = useCallback((next: ConfirmOutcome | null, id: string) => {
    if (shown.current === id) setOutcome(next)
  }, [])

  const booked = useCallback(async (id: string, receipt: Receipt, how: 'confirmed' | 'found') => {
    client.setQueryData<Proposal>(proposalKey(id), (old) => (old ? { ...old, receipt } : old))
    setReceipts((current) => ({ ...current, [id]: receipt }))
    const mine = shown.current === id
    if (how === 'confirmed') toast.success(receiptMessage(receipt), { action: mine ? latest.current.toastAction?.(receipt) : undefined })
    else toast.info(`这份方案已经入账：${receiptMessage(receipt)}`)
    if (mine) {
      setOutcome(null)
      latest.current.onConfirmed?.(receipt)
    }
    await invalidateLedger(client)
  }, [client])

  /** After an answer was lost: what the backend has now for `id`. */
  const readBack = useCallback(async (id: string) => {
    settle({ id, state: 'checking' }, id)
    try {
      const stored = await client.fetchQuery({ queryKey: proposalKey(id), queryFn: () => fetchProposal(id), staleTime: 0 })
      if (stored.receipt) await booked(id, stored.receipt, 'confirmed')
      else settle({ id, state: 'not_booked', message: stored.expired ? EXPIRED : STILL_PENDING }, id)
    } catch (exc) {
      settle(httpStatus(exc) === 404 ? { id, state: 'not_booked', message: GONE } : { id, state: 'unknown' }, id)
    }
  }, [booked, client, settle])

  const confirm = useMutation({
    // The id is the mutation's variable, captured at the click: a later proposal on screen never changes it.
    mutationFn: (id: string) => confirmLedgerProposal(id),
    retry: 0,
    networkMode: 'always',
    onSuccess: (receipt, id) => booked(id, receipt, 'confirmed'),
    onError: (error, id) => {
      if (confirmFailure(httpStatus(error)) === 'refused') {
        settle({ id, state: 'refused', message: ledgerErrorText(getApiErrorMessage(error)) }, id)
        // Revision, binding or expiry: the preview can never be confirmed; read the ledger and this proposal again.
        void invalidateLedger(client)
      } else {
        void readBack(id)
      }
    },
  })
  const discard = useMutation({
    mutationFn: (id: string) => discardLedgerProposal(id),
    retry: 0,
    networkMode: 'always',
    onSuccess: async (_, id) => {
      if (shown.current === id) {
        toast.info('已放弃这份预览，没有入账')
        latest.current.onDiscarded?.()
      }
      await invalidateLedger(client)
    },
    onError: async (error, id) => {
      // Booked meanwhile (another tab, the tray): show it as 已入账.
      if (httpStatus(error) !== 409) return
      try {
        const stored = await client.fetchQuery({ queryKey: proposalKey(id), queryFn: () => fetchProposal(id), staleTime: 0 })
        if (stored.receipt) await booked(id, stored.receipt, 'found')
      } catch {
        // The error line below still says why nothing was discarded.
      }
    },
  })
  // A new preview starts without the last one's result or error.
  const { reset: resetConfirm } = confirm
  const { reset: resetDiscard } = discard
  useEffect(() => {
    resetConfirm()
    resetDiscard()
    setOutcome(null)
  }, [proposal.id, resetConfirm, resetDiscard])

  const receipt = proposal.receipt ?? receipts[proposal.id] ?? null
  const done = Boolean(receipt)
  const own = outcome && outcome.id === proposal.id ? outcome : null
  const confirming = (confirm.isPending && confirm.variables === proposal.id) || own?.state === 'checking'
  const discarding = discard.isPending && discard.variables === proposal.id
  const discardError = discard.error && discard.variables === proposal.id
    ? (httpStatus(discard.error) === 409 ? '这份方案已经入账，不能放弃。' : ledgerErrorText(getApiErrorMessage(discard.error)))
    : null
  const outdated = !done && isOutdated(proposal.revision, ledger.data?.revision)
  const expired = !done && Boolean(proposal.expired)
  const message = own && (own.state === 'refused' || own.state === 'not_booked') ? own.message : null
  return {
    proposal: receipt ? { ...proposal, receipt } : proposal,
    confirm: () => {
      const id = proposal.id
      if (!id || done || confirm.isPending || own?.state === 'checking') return
      settle(null, id)
      confirm.mutate(id)
    },
    discard: () => {
      if (proposal.id && !discard.isPending) discard.mutate(proposal.id)
    },
    confirming,
    discarding,
    error: done ? discardError : message ?? (expired ? EXPIRED : null) ?? discardError,
    done,
    // Revisions only grow: a cached revision below the preview's is just not refetched yet.
    outdated,
    blocked: readOnlyReason,
    outcome: own,
    recheck: () => void readBack(proposal.id),
    unconfirmable: expired || own?.state === 'refused' || own?.state === 'unknown' || own?.state === 'checking'
      || (own?.state === 'not_booked' && own.message !== STILL_PENDING),
  }
}

export interface ProposalReviewProps {
  proposal: Proposal
  /** Reuse a review state created by the caller (when the buttons sit in a dialog footer). */
  review?: ReviewState
  /** Show the buttons under the review (the tray); a dialog puts `ProposalReviewActions` in its footer. */
  actions?: boolean
  /** 返回修改 (the entry dialog); otherwise the secondary button is 放弃. */
  onBack?: () => void
  onConfirmed?: ReviewOptions['onConfirmed']
  onDiscarded?: ReviewOptions['onDiscarded']
  /** Under the plan check: the plans package's callout. */
  extra?: ReactNode
  className?: string
}

export function ProposalReview({ review: given, ...props }: ProposalReviewProps) {
  // Hooks run in the same order every time; an outside state simply wins.
  const own = useProposalReview(props.proposal, { onConfirmed: props.onConfirmed, onDiscarded: props.onDiscarded })
  return <ReviewBody {...props} review={given ?? own} />
}

function ReviewBody({ review, actions = false, onBack, extra, className }: ProposalReviewProps & { review: ReviewState }) {
  const proposal = review.proposal
  const needsHistory = proposal.events.some((event) => event.kind === 'reverse')
  const history = useLedgerEvents(needsHistory)
  const ledger = useLedger()
  const [expanded, setExpanded] = useState(false)
  const lookup = useMemo(() => {
    const byId = new Map((history.data ?? []).map((event) => [event.id, event]))
    return (id: string) => byId.get(id)
  }, [history.data])
  const names = useMemo(() => tickerNames(history.data ?? [], ledger.data?.accounts), [history.data, ledger.data])
  const shown = shownEvents(proposal.events, expanded)
  const outcome = review.outcome
  return (
    <section aria-label="核对入账" className={cx('flex min-w-0 flex-col gap-4 text-body', className)}>
      <div className="flex flex-col gap-1">
        <p className={cx('font-medium', review.done ? 'text-accent' : 'text-fg', numeric)}>
          {review.done && <Check size={16} aria-hidden className="mr-1 inline-block align-[-3px]" />}
          {proposalStatus(proposal)}
        </p>
        {shown.length > 0 && (
          <ul className={cx('flex flex-col gap-1 text-table text-fg-2', numeric)}>
            {shown.map((event) => {
              const marks = event.kind === 'reverse' ? [] : noteMarks(event.note)
              return (
                <li key={event.id} className="break-words">
                  <LongValue value={eventLineShown(event, lookup, names)} />
                  {marks.length > 0 && (
                    <span className="mt-0.5 flex flex-wrap gap-1">
                      {marks.map((mark) => <Badge key={mark} tone="warn">{mark}</Badge>)}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        )}
        {proposal.events.length > SHOWN_EVENTS && (
          <Button size="sm" variant="ghost" className="self-start text-accent hover:text-accent" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
            {expanded ? `只显示前 ${SHOWN_EVENTS} 笔` : `显示全部 ${proposal.events.length} 笔`}
          </Button>
        )}
      </div>

      <ChangesTable before={proposal.before} after={proposal.after} />

      {proposal.warnings.length > 0 && (
        <ul aria-label="提示" className="flex flex-col gap-1 text-table text-warn">
          {proposal.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-1.5">
              <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0" />
              <span className="min-w-0 break-words">{warningText(warning)}</span>
            </li>
          ))}
        </ul>
      )}

      <PlanCheck items={proposal.plan_check} error={proposal.plan_check_error} />
      {extra}

      {review.outdated && (
        <InlineAlert tone="warn" title="预览已过期">
          账本在这次预览之后有了变化，确认前请{onBack ? '返回修改后' : ''}重新预览。
        </InlineAlert>
      )}
      {outcome?.state === 'checking' && (
        <p role="status" className="text-table text-fg-2">没有收到确认结果，正在向账本核对这份预览是否已入账…</p>
      )}
      {outcome?.state === 'unknown' && (
        <InlineAlert
          tone="warn"
          title="结果未知"
          action={review.recheck ? <Button size="sm" variant="ghost" onClick={review.recheck}>重新核对</Button> : undefined}
        >
          {UNKNOWN_TEXT}
        </InlineAlert>
      )}
      {review.error && (
        <InlineAlert tone="warn" title={review.done ? undefined : outcome?.state === 'not_booked' ? '未入账' : '没有入账'}>{review.error}</InlineAlert>
      )}
      {actions && (
        <div className="flex flex-wrap justify-end gap-2">
          <ProposalReviewActions review={review} onBack={onBack} />
        </div>
      )}
    </section>
  )
}

export interface ProposalReviewActionsProps {
  review: ReviewState
  onBack?: () => void
  /** Hide 放弃 (the ledger page puts its own close button there once booked). */
  discardLabel?: string
}

const ACTIVATION_KEYS = new Set(['Enter', ' '])

/**
 * Keyboard activation of 确认入账 only from a fresh press on the button itself, made after the review was
 * shown: a held Enter (key repeat) from the form, or a press that began elsewhere, does not confirm. A
 * click without a key held (mouse, touch, a screen reader's activation) is not affected.
 */
function useFreshActivation(armKey: string) {
  const shownAt = useRef(0)
  const held = useRef(new Map<string, { at: number; target: EventTarget | null }>())
  useLayoutEffect(() => {
    shownAt.current = performance.now()
  }, [armKey])
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (!event.repeat) held.current.set(event.key, { at: performance.now(), target: event.target })
    }
    const up = (event: KeyboardEvent) => {
      held.current.delete(event.key)
    }
    const clear = () => held.current.clear()
    document.addEventListener('keydown', down, true)
    document.addEventListener('keyup', up, true)
    window.addEventListener('blur', clear)
    return () => {
      document.removeEventListener('keydown', down, true)
      document.removeEventListener('keyup', up, true)
      window.removeEventListener('blur', clear)
    }
  }, [])
  const onKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (ACTIVATION_KEYS.has(event.key) && event.repeat) event.preventDefault()
  }
  /** False when this click comes from a key press that did not start fresh on the button. */
  const accepts = (event: MouseEvent<HTMLButtonElement>) => {
    if (event.detail !== 0) return true
    for (const key of ACTIVATION_KEYS) {
      const press = held.current.get(key)
      if (press && (press.at < shownAt.current || press.target !== event.currentTarget)) return false
    }
    return true
  }
  return { onKeyDown, accepts }
}

/** 返回修改 or 放弃, then 确认入账 (已入账 once booked). Enter on the focused 确认入账 confirms. */
export const ProposalReviewActions = forwardRef<HTMLButtonElement, ProposalReviewActionsProps>(function ProposalReviewActions(
  { review, onBack, discardLabel = '放弃' }, ref,
) {
  const busy = review.confirming || review.discarding
  const guard = useFreshActivation(review.proposal.id)
  const confirmProps: ButtonProps = {
    variant: 'primary',
    icon: review.done ? Check : undefined,
    loading: review.confirming,
    disabled: review.done || review.outdated || review.discarding || Boolean(review.unconfirmable),
    disabledReason: review.done ? undefined : review.blocked,
    tipAlign: 'end',
    onKeyDown: guard.onKeyDown,
    onClick: (event) => {
      if (!guard.accepts(event)) {
        event.preventDefault()
        return
      }
      review.confirm()
    },
  }
  return (
    <>
      {onBack ? (
        <Button variant="ghost" disabled={busy} onClick={onBack}>返回修改</Button>
      ) : !review.done && (
        <Button
          variant="ghost"
          loading={review.discarding}
          disabled={review.confirming}
          disabledReason={review.blocked}
          onClick={review.discard}
        >
          {discardLabel}
        </Button>
      )}
      <Button ref={ref} {...confirmProps} aria-keyshortcuts="Enter">
        {review.done ? '已入账' : '确认入账'}
      </Button>
    </>
  )
})

/**
 * The 待确认 tray's in-place review (`PendingTray`'s `ledgerReview` slot): loads the proposal by id,
 * shows it with 放弃 / 确认入账, and calls `onDone` once it was booked or discarded.
 */
export function LedgerProposalReview({ proposalId, onDone }: LedgerReviewProps) {
  const query = useProposal(proposalId)
  if (query.isPending) {
    return (
      <div role="status" aria-label="正在读取方案" className="flex flex-col gap-2 py-2">
        <Skeleton lines={3} />
      </div>
    )
  }
  if (query.isError || !query.data) {
    return (
      <InlineAlert tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void query.refetch()}>重试</Button>}>
        {ledgerErrorText(getApiErrorMessage(query.error))}
      </InlineAlert>
    )
  }
  return (
    <ProposalReview
      proposal={query.data}
      actions
      onConfirmed={onDone}
      onDiscarded={onDone}
    />
  )
}

export default ProposalReview
