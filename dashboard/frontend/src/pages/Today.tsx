// 今日 (/): the confirmed ledger's holdings at live prices, with honest data-quality labels; what is
// waiting for confirmation; today's alerts and due items. Data: GET /api/today (contract in
// core/market/today.py), polled by the shell every 5 s in session and every 60 s otherwise.
import { Link } from 'react-router-dom'
import { BookOpen, FileUp, RefreshCw } from 'lucide-react'
import { Button, EmptyState, InlineAlert, Panel, buttonClass, cx } from '@/components/ui'
import { snapshotDate } from '@/features/today/session'
import { getApiErrorMessage } from '@/services/api'
import { useRefreshToday, useTodayFeed } from '@/components/shell/useTodayFeed'
import { HoldingsTable } from '@/components/today/HoldingsTable'
import { PendingProposals } from '@/components/today/PendingProposals'
import { SessionBar } from '@/components/today/SessionBar'
import { TodayAgenda, useAgenda } from '@/components/today/TodayAgenda'
import { TodayAlerts, useAlertsFeed } from '@/components/today/TodayAlerts'
import { TotalsBar, TotalsSkeleton } from '@/components/today/TotalsBar'
import { PlanHost } from '@/components/plans/PlanHost'
import { renderRowExtra } from '@/components/today/rowExtras'
import { SetupHint } from '@/components/setup/SetupHint'

function Empty({ notice }: { notice?: string }) {
  return (
    <Panel>
      <EmptyState
        icon={BookOpen}
        title="还没有已确认的持仓"
        description={notice ?? '在账本中确认期初余额后，这里会按实时行情显示持仓。'}
        action={(
          <>
            <Link to="/setup" className={buttonClass({ variant: 'primary' })}>开始设置</Link>
            <Link to="/ledger" className={buttonClass({ variant: 'secondary' })}>
              去账本设置期初余额
            </Link>
            <Link to="/ledger/import" className={buttonClass()}>
              <FileUp size={16} aria-hidden className="shrink-0" />
              导入券商文件
            </Link>
          </>
        )}
      />
    </Panel>
  )
}

export default function Today() {
  const today = useTodayFeed()
  const { refresh, refreshing, error: refreshError } = useRefreshToday()
  const data = today.data
  // Null until /today answers with a readable time: nothing is "today's" before that.
  const date = snapshotDate(data)
  const agenda = useAgenda(date)
  const alerts = useAlertsFeed()
  const empty = data !== undefined && data.accounts.length === 0
  // A read that could not see one consistent ledger version says so; it is not an empty portfolio.
  const unreadable = empty && data.ledger_revision === null
  const alertsShown = alerts.shown(date, empty)
  const panels = Number(agenda.due.length > 0 || agenda.unavailable !== null || agenda.plans.length > 0 || agenda.plansUnavailable !== null) + Number(alertsShown)

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 text-body text-fg">
      <h1 className="sr-only">今日</h1>
      <div className="flex items-start justify-between gap-x-6">
        <SessionBar data={data} />
        <Button size="sm" icon={RefreshCw} loading={refreshing} onClick={() => void refresh()} title="立即重新取价（跳过行情缓存）">
          刷新
        </Button>
      </div>

      {(refreshError || (today.isError && !data)) && (
        <InlineAlert
          tone="warn"
          title={data ? '刷新失败' : '无法读取今日数据'}
          action={<Button size="sm" variant="secondary" onClick={() => void refresh()}>重试</Button>}
        >
          {refreshError ?? getApiErrorMessage(today.error)}
        </InlineAlert>
      )}

      {!empty && (data ? <TotalsBar data={data} /> : !today.isError && <TotalsSkeleton />)}

      <PendingProposals />

      {data?.notices.map((notice) => (
        <InlineAlert key={notice.kind} tone="info">{notice.message}</InlineAlert>
      ))}

      {unreadable ? (
        <InlineAlert tone="warn" title="暂时读不到账本" action={<Button size="sm" variant="secondary" onClick={() => void refresh()}>重试</Button>}>
          {data.notice}
        </InlineAlert>
      ) : empty ? (
        <>
          <SetupHint />
          <Empty notice={data.notice} />
        </>
      ) : (
        (data || !today.isError) && <HoldingsTable data={data} renderRowExtra={renderRowExtra} />
      )}

      {panels > 0 && (
        <div className={cx('grid gap-6', panels > 1 && 'lg:grid-cols-2')}>
          <TodayAgenda today={date} />
          <TodayAlerts today={date} quiet={empty} />
        </div>
      )}
      <PlanHost />
    </div>
  )
}
