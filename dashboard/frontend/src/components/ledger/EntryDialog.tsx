// 记一笔: the one place a trade, dividend or cash movement is entered (plan EN-1). Opened by the shell from
// the header button, the N key, the bottom bar, a holdings row's 买 / 卖 (prefilled), an alert or a plan.
// Two steps in the same dialog: the form, then 核对入账 (the backend's preview). Enter moves forward each
// time: 预览入账, then 确认入账, each from its own key press. Nothing is booked before that second press.
//
// Each opening is a session: a preview that answers after the dialog was closed or reopened belongs to no
// form and is discarded, never shown. A preview left behind (返回修改, 取消, closing the tab) is discarded
// with a request that outlives the page; one that cannot be discarded is retried later, and the backend
// lets an abandoned manual preview expire after 2 hours.
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { Eye } from 'lucide-react'
import { Button, Dialog, InlineAlert } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { abandonLedgerProposal, proposeEvents } from '@/services/ledger'
import { browserStorage, rememberEntry } from '@/lib/entryDefaults'
import { KIND_FIELDS, entryCurrency, parseTicker, type EntryValues, type EventInput } from '@/features/ledger/entry'
import { kindLabel } from '@/features/ledger/kinds'
import { ledgerErrorText } from '@/features/ledger/review'
import type { Proposal } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useShell } from '@/components/shell/shellStore'
import { EntryForm } from './EntryForm'
import { NO_PROPOSAL, ProposalReview, ProposalReviewActions, useProposalReview } from './ProposalReview'

/**
 * What the dialog opens with (plan `EntryPrefill`). Every field is optional; what is given wins over the
 * remembered defaults. Amounts are decimal strings.
 */
export interface EntryDraft {
  /** Ledger event kind: buy, sell, dividend, deposit, withdrawal, fx, transfer, split, fee, interest, fund_*, opening. */
  kind?: string
  /** Ledger ticker, e.g. SHA:600519. */
  ticker?: string
  /** Display name of the ticker. */
  name?: string
  account?: string
  /** Currency of a cash entry (trades take their market's). */
  currency?: string
  quantity?: string
  price?: string
  /** YYYY-MM-DD. */
  date?: string
  note?: string
  /** The trade plan the entry comes from (shown; the preview checks plans by ticker on its own). */
  planId?: string | number
  /** Where it was opened: `import`, `screenshot` and `assistant` become the events' `source`. */
  source?: 'today' | 'alert' | 'plan' | 'assistant' | 'import' | 'screenshot'
}

/** The plan's name for the same thing. */
export type EntryPrefill = EntryDraft

export interface EntryDialogProps {
  open: boolean
  onClose: () => void
  /** Prefill from where the dialog was opened (a holdings row's 买 / 卖), else empty. */
  draft?: EntryDraft | null
}

/** "买入 示例宽基 SHA:DEMO · 长期账户". */
export function describeDraft(draft: EntryDraft | null | undefined): string {
  if (!draft) return ''
  const subject = [draft.name, draft.ticker].filter(Boolean).join(' ')
  const head = [draft.kind ? kindLabel(draft.kind) : '', subject].filter(Boolean).join(' ')
  return [head, draft.account].filter(Boolean).join(' · ')
}

// Previews this page left behind whose discard did not reach the backend; tried again on the next preview.
const unreconciled = new Set<string>()

async function abandon(id: string): Promise<void> {
  if (await abandonLedgerProposal(id)) unreconciled.delete(id)
  else unreconciled.add(id)
}

function retryAbandoned(): void {
  for (const id of [...unreconciled]) void abandon(id)
}

export function EntryDialog({ open, onClose, draft }: EntryDialogProps) {
  const formId = useId()
  const { readOnlyReason } = useAppMode()
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [values, setValues] = useState<EntryValues | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [session, setSession] = useState({ open, draft, count: 0 })
  const confirmRef = useRef<HTMLButtonElement>(null)
  const lastField = useRef<HTMLElement | null>(null)
  // The session whose answers may still land: changes when the dialog closes or opens again.
  const live = useRef<number | null>(open ? session.count : null)

  // A new opening (or a new draft) starts from scratch.
  if (session.open !== open || session.draft !== draft) {
    setSession({ open, draft, count: session.count + (open ? 1 : 0) })
    if (open) {
      setProposal(null)
      setValues(null)
      setError(null)
      setBusy(false)
    }
  }
  useLayoutEffect(() => {
    live.current = open ? session.count : null
  }, [open, session.count])

  const finish = () => {
    if (values) {
      rememberEntry(browserStorage(), {
        account: values.account, kind: values.kind,
        currency: KIND_FIELDS[values.kind].currency === 'choose' ? values.currency : undefined,
      })
    }
    setProposal(null)
    onClose()
  }
  const review = useProposalReview(proposal ?? NO_PROPOSAL, {
    onConfirmed: finish,
    toastAction: () => {
      if (!values) return undefined
      const next = { account: values.account, date: values.date, kind: values.kind === 'opening' ? undefined : values.kind }
      return { label: '再记一笔', onClick: () => useShell.getState().openEntry(next) }
    },
  })

  // Before paint, so a fresh Enter right after the review appears confirms it (a held one does not).
  useLayoutEffect(() => {
    if (proposal) confirmRef.current?.focus()
  }, [proposal])

  // Closing the tab while reviewing leaves nothing waiting in 待确认.
  const pending = proposal && !review.done ? proposal.id : null
  useEffect(() => {
    if (!pending) return
    const leave = () => void abandon(pending)
    window.addEventListener('pagehide', leave)
    return () => window.removeEventListener('pagehide', leave)
  }, [pending])

  const token = session.count
  const preview = async (events: EventInput[], entered: EntryValues) => {
    setError(null)
    retryAbandoned()
    try {
      const result = await proposeEvents(events, { transient: true })
      // Closed or reopened meanwhile: this answer belongs to no form on screen.
      if (live.current !== token) {
        void abandon(result.id)
        return
      }
      setValues(entered)
      setProposal(result)
    } catch (exc) {
      if (live.current === token) setError(ledgerErrorText(getApiErrorMessage(exc)))
    }
  }

  /** Leaving the review without booking drops the preview, so it does not wait in 待确认. */
  const dropPreview = () => {
    if (proposal && !review.done) void abandon(proposal.id)
  }
  const back = () => {
    dropPreview()
    setProposal(null)
    window.requestAnimationFrame(() => {
      const form = document.getElementById(formId)
      const field = lastField.current
      const target = field && form?.contains(field) && !field.matches(':disabled')
        ? field
        : form?.querySelector<HTMLElement>('input:not([type=hidden]):not([disabled]), select')
      target?.focus()
    })
  }
  const close = () => {
    dropPreview()
    onClose()
  }

  const reviewing = proposal !== null
  const footer = reviewing ? (
    <ProposalReviewActions ref={confirmRef} review={review} onBack={back} />
  ) : (
    <>
      <Button variant="ghost" onClick={close}>取消</Button>
      <Button
        type="submit"
        form={formId}
        variant="primary"
        icon={Eye}
        loading={busy}
        disabledReason={readOnlyReason}
        tipAlign="end"
        aria-keyshortcuts="Enter"
      >
        预览入账
      </Button>
    </>
  )

  return (
    <Dialog
      open={open}
      onClose={close}
      title={reviewing ? '核对入账' : '记一笔'}
      description={reviewing && values ? subtitle(values) : undefined}
      size="md"
      closeOnBackdrop={false}
      footer={footer}
    >
      <div hidden={reviewing}>
        {error && <InlineAlert tone="warn" title="无法预览" className="mb-4">{error}</InlineAlert>}
        <EntryForm
          key={session.count}
          id={formId}
          draft={draft ?? {}}
          onPreview={preview}
          onBusyChange={(next) => {
            if (live.current === token) setBusy(next)
          }}
          onClose={close}
          lastFieldRef={lastField}
        />
      </div>
      {proposal && <ProposalReview proposal={proposal} review={review} onBack={back} />}
    </Dialog>
  )
}

function subtitle(values: EntryValues): string {
  const ticker = parseTicker(values.ticker)
  const currency = entryCurrency(values, ticker)
  return [kindLabel(values.kind), values.account, values.date, currency && KIND_FIELDS[values.kind].currency === 'choose' ? currency : '']
    .filter(Boolean).join(' · ')
}

export default EntryDialog
