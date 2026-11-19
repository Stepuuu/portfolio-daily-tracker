// 账本 › 流水 (/ledger): the confirmed events with filters and 冲销, under the account lines and the previews
// waiting for confirmation. `?proposal=<id>` opens that preview's review.
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { FileUp } from 'lucide-react'
import { Button, buttonClass, toast } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { proposeEvents } from '@/services/ledger'
import { tickerNames } from '@/features/ledger/events'
import { ledgerErrorText } from '@/features/ledger/review'
import type { LedgerEvent } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { EventsTable } from '@/components/ledger/EventsTable'
import { useLedgerCalendar, useLedgerEvents } from '@/components/ledger/useLedger'
import { LedgerFrame } from './LedgerFrame'

export default function LedgerEvents() {
  const { readOnlyReason } = useAppMode()
  const history = useLedgerEvents()
  // The backend's today (Beijing time); null while it cannot say.
  const today = useLedgerCalendar().calendar?.today ?? null
  const reverse = useMutation({
    // A reversal takes effect on its own date, which cannot be before the event it undoes. A preview made
    // here and left unconfirmed expires (transient).
    mutationFn: (event: LedgerEvent) => proposeEvents(
      [{ kind: 'reverse', date: today && today > event.date ? today : event.date, target_id: event.id }], { transient: true }),
    onError: (error) => toast.warn(`无法冲销：${ledgerErrorText(getApiErrorMessage(error))}`),
  })
  const events = useMemo(() => history.data ?? [], [history.data])

  return (
    <LedgerFrame
      title="账本 · 流水"
      actions={(
        <Link to="/ledger/import" className={buttonClass()} title="券商交割单、对账单、持仓，或本系统的 CSV 模板">
          <FileUp size={16} aria-hidden className="shrink-0" />
          导入
        </Link>
      )}
    >
      {(ledger, openProposal) => (
        <>
          {history.isError && (
            <p role="alert" className="text-table text-warn">
              流水读取失败：{getApiErrorMessage(history.error)}
              <Button size="sm" variant="ghost" className="ml-2" onClick={() => void history.refetch()}>重试</Button>
            </p>
          )}
          <EventsTable
            events={events}
            accounts={Object.keys(ledger.accounts)}
            names={tickerNames(events, ledger.accounts)}
            today={today}
            loading={history.isPending}
            reversing={reverse.isPending ? reverse.variables?.id ?? null : null}
            writeBlocked={readOnlyReason}
            onReverse={(event) => reverse.mutate(event, { onSuccess: (proposal) => openProposal(proposal.id) })}
          />
        </>
      )}
    </LedgerFrame>
  )
}
