// 今日事项: what is due today: research notes and active trade plans whose review date has come. Hidden when
// nothing is due; when the notes or the plans cannot be read it says so instead (that is not "nothing due").
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Panel, cx, focusRing, numeric } from '@/components/ui'
import { fetchJournal, type JournalEntry } from '@/services/today'
import { getApiErrorMessage } from '@/services/api'
import { useActivePlans } from '@/features/plans/hooks'
import type { PlanItem } from '@/features/plans/types'
import { duePlans } from '@/features/plans/view'
import { isoDay } from '@/features/today/session'

/** Notes whose review date has come; none while today's date is not known (a non-date never compares). */
export function dueEntries(entries: JournalEntry[] | undefined, today: string | null): JournalEntry[] {
  const day = isoDay(today)
  if (!entries || !day) return []
  return entries.filter((entry) => isoDay(entry.review_on) && entry.review_on <= day).sort((a, b) => a.review_on.localeCompare(b.review_on))
}

export interface Agenda {
  due: JournalEntry[]
  /** The notes could not be read (no answer yet, or the latest request failed): why, else null. */
  unavailable: string | null
  /** Active trade plans whose review date has come (the shared Today query of active plans). */
  plans: PlanItem[]
  /** The plans could not be read: why, else null. */
  plansUnavailable: string | null
}

export function useAgenda(today: string | null): Agenda {
  const journal = useQuery({ queryKey: ['ledger-journal'], queryFn: fetchJournal, refetchInterval: 300_000, retry: false })
  const plans = useActivePlans()
  return {
    due: dueEntries(journal.data?.entries, today),
    // A failure is not "nothing due": the panel says the notes could not be read.
    unavailable: journal.isError ? getApiErrorMessage(journal.error) : null,
    plans: duePlans(plans.data?.items, isoDay(today)),
    plansUnavailable: plans.isError ? getApiErrorMessage(plans.error) : null,
  }
}

export function TodayAgenda({ today, className }: { today: string | null; className?: string }) {
  const { due, unavailable, plans, plansUnavailable } = useAgenda(today)
  if (!due.length && !unavailable && !plans.length && !plansUnavailable) return null
  return (
    <Panel className={className} title="今日事项">
      {unavailable && (
        <p className="mb-2 text-table text-warn">
          {due.length ? '列表可能不是最新：' : ''}研究笔记的复查日暂时读不到（{unavailable}），今天是否有到期的复查，现在无法确认。
        </p>
      )}
      {plansUnavailable && (
        <p className="mb-2 text-table text-warn">
          {plans.length ? '列表可能不是最新：' : ''}交易计划的复查日暂时读不到（{plansUnavailable}），今天是否有到期的计划，现在无法确认。
        </p>
      )}
      {plans.length > 0 && (
        <ul aria-label="到期的交易计划" className={cx('-my-2 divide-y divide-line text-body', due.length > 0 && 'mb-2')}>
          {plans.map((plan) => (
            <li key={plan.id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">
                复查到期：{plan.ticker} 交易计划 #{plan.id}
                <span className={cx('ml-2 text-table text-fg-3', numeric)}>{plan.review_on}</span>
              </span>
              <Link to={`/plans?plan=${plan.id}`} className={cx('shrink-0 rounded-sm text-table text-accent underline-offset-4 hover:underline', focusRing)}>打开</Link>
            </li>
          ))}
        </ul>
      )}
      {due.length > 0 && (
        <ul className="-my-2 divide-y divide-line text-body">
          {due.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">
                复查到期：{entry.ticker} 研究笔记
                <span className={cx('ml-2 text-table text-fg-3', numeric)}>{entry.review_on}</span>
              </span>
              <Link to="/notes" className={cx('shrink-0 rounded-sm text-table text-accent underline-offset-4 hover:underline', focusRing)}>打开</Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
