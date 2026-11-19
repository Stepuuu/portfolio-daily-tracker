// Today's banner for ledger proposals waiting for confirmation: the first three, each with a link to
// its review on the ledger page. The region name and the link text are what tests/browser_smoke.py
// looks for (`待确认账本方案`, `核对方案`).
import { Link } from 'react-router-dom'
import { Info } from 'lucide-react'
import { Button, cx, focusRing } from '@/components/ui'
import { describeLedgerProposal } from '@/features/pending/proposals'
import { usePending } from '@/components/shell/usePending'
import { useShell } from '@/components/shell/shellStore'

const SHOWN = 3

export function PendingProposals() {
  const { current } = usePending()
  const openTray = useShell((state) => state.setTrayOpen)
  if (!current.length) return null
  const events = current.reduce((count, proposal) => count + proposal.events.length, 0)
  return (
    <section aria-label="待确认账本方案" className="flex items-start gap-3 rounded-md bg-fg/[.05] px-3 py-2 text-body">
      <Info size={16} aria-hidden className="mt-[3px] shrink-0 text-fg-3" />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
        <p className="font-medium text-fg">待确认 {events} 笔，尚未入账</p>
        <ul className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          {current.slice(0, SHOWN).map((proposal) => {
            const text = describeLedgerProposal(proposal)
            return (
              <li key={proposal.id} className="flex min-w-0 items-center gap-2 text-fg-2">
                <span className="min-w-0 truncate">{text}</span>
                <Link
                  to={`/ledger?proposal=${encodeURIComponent(proposal.id)}`}
                  aria-label={`核对方案：${text}`}
                  className={cx('shrink-0 rounded-sm font-medium text-accent underline-offset-4 hover:underline', focusRing)}
                >
                  核对方案
                </Link>
              </li>
            )
          })}
        </ul>
      </div>
      {current.length > SHOWN && (
        <Button size="sm" variant="ghost" onClick={() => openTray(true)}>全部 {current.length} 个方案</Button>
      )}
    </section>
  )
}
