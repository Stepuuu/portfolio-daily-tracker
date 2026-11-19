// The 48 px bar above every page: section title, market session, compact totals (not on 今日), search,
// 记一笔, 待确认, notifications and the assistant. Research-only mode keeps the title and search only.
import { useLocation } from 'react-router-dom'
import { MessageSquare, Plus } from 'lucide-react'
import { Button, cx, numeric } from '@/components/ui'
import { fmtMoney, toneOf } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { sessionChip } from '@/features/today/session'
import { GlobalSearch } from './GlobalSearch'
import { NotificationBell } from './NotificationBell'
import { useShell } from './shellStore'
import { useAppMode } from './useAppMode'
import { useNavModel } from './useNavModel'
import { usePending } from './usePending'
import { useTodayFeed } from './useTodayFeed'
import { TIP_BELOW } from './tips'

function SessionChip() {
  const today = useTodayFeed()
  const chip = sessionChip(today.data)
  if (!chip) return null
  return (
    <span className="hidden items-center gap-1.5 whitespace-nowrap text-table text-fg-2 sm:inline-flex">
      <span aria-hidden className={cx('h-1.5 w-1.5 rounded-full', chip.moving ? 'bg-accent' : 'bg-fg-3')} />
      {chip.text}
      {chip.qualifier && (
        <span className="text-caption text-warn" title={chip.title ?? undefined}>
          {chip.qualifier}
          <span className="sr-only">：{chip.title}</span>
        </span>
      )}
    </span>
  )
}

/** "总资产 ¥151,234.00 · 今日 +¥400.00" for every page but 今日 (which shows them in full). */
function HeaderTotals() {
  const { data } = useTodayFeed()
  if (!data || data.accounts.length === 0) return null
  const { total_cny: total, day_pnl_cny: day, quality } = data.totals
  return (
    <dl className={cx('hidden items-baseline gap-x-2 whitespace-nowrap text-table xl:flex', numeric)}>
      <dt className="text-fg-3">总资产</dt>
      <dd className={total === null ? 'text-fg-3' : 'text-fg'}>{fmtMoney(total, 'CNY', { exact: true })}</dd>
      <dt className="ml-3 text-fg-3">今日</dt>
      <dd className={day === null ? 'text-fg-3' : pnlTextClass(toneOf(day))}>{fmtMoney(day, 'CNY', { signed: true, exact: true })}</dd>
      {quality === 'estimated' && <dd className="text-caption text-warn" title="部分行情或汇率未经核实，按最近数据估算">估算</dd>}
    </dl>
  )
}

function PendingChip() {
  const { count } = usePending()
  const openTray = useShell((state) => state.setTrayOpen)
  if (!count) return null
  return (
    <Button size="sm" onClick={() => openTray(true)} aria-label={`待确认 ${count} 项`} className="gap-1.5">
      待确认
      <span className={cx('rounded-full bg-accent px-1.5 text-caption font-medium leading-4 text-accent-fg', numeric)}>{count}</span>
    </Button>
  )
}

export function Header() {
  const { pathname } = useLocation()
  const { current } = useNavModel()
  const { mode, lab, readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  const assistantOpen = useShell((state) => state.assistantOpen)
  const toggleAssistant = useShell((state) => state.toggleAssistant)
  const desk = mode !== undefined && !lab
  const home = pathname === '/'
  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-bg px-4 text-body text-fg md:px-6">
      <div className="flex min-w-0 items-center gap-4">
        <p className="truncate text-title text-fg">{current?.section.label ?? ''}</p>
        {desk && !home && <SessionChip />}
      </div>
      <div className="ml-auto flex items-center gap-2">
        {desk && !home && <HeaderTotals />}
        <GlobalSearch />
        {desk && (
          <>
            <span className={cx('hidden md:inline-flex', TIP_BELOW)}>
              <Button
                size="sm"
                variant="primary"
                icon={Plus}
                onClick={() => openEntry()}
                disabledReason={readOnlyReason}
                tipAlign="end"
                aria-keyshortcuts="N"
              >
                记一笔
              </Button>
            </span>
            <PendingChip />
            <NotificationBell />
            <span className="hidden md:inline-flex">
              <Button
                size="sm"
                variant="ghost"
                icon={MessageSquare}
                aria-pressed={assistantOpen}
                onClick={toggleAssistant}
                className={cx(assistantOpen && 'bg-fg/[.06] text-fg')}
              >
                助手
              </Button>
            </span>
          </>
        )}
      </div>
    </header>
  )
}
