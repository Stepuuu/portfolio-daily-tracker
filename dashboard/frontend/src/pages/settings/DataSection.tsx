// 设置 › 数据与行情: whether the quote source answers (the setup checklist's probe, `/api/setup/status`, shared
// with the wizard), the daily-bar cache the backtests read (`/api/backtest/data/cache`, managed on 回测 › 数据)
// and which years each market's holiday table covers (`/api/settings/calendar`). Read-only: these are
// configured by environment variables (the line to change is shown) or managed on their own pages.
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { Button, InlineAlert, Panel, Skeleton, buttonClass, cx, numeric } from '@/components/ui'
import { Checklist } from '@/components/setup/Checklist'
import { getApiErrorMessage } from '@/services/api'
import { CACHE_KEY, fetchCacheStats } from '@/services/backtest'
import { CALENDAR_KEY, fetchCalendarCoverage } from '@/services/settings'
import { SETUP_STATUS_KEY, fetchSetupStatus } from '@/services/setup'
import { calendarLine, cacheFacts } from '@/features/settings/data'

function Failure({ title, error, retry }: { title: string; error: unknown; retry: () => void }) {
  return (
    <InlineAlert tone="warn" title={title} action={<Button size="sm" onClick={retry}>重试</Button>}>
      {getApiErrorMessage(error)}
    </InlineAlert>
  )
}

function QuotesPanel() {
  const status = useQuery({ queryKey: SETUP_STATUS_KEY, queryFn: () => fetchSetupStatus(true), staleTime: 5_000 })
  const items = (status.data?.items ?? []).filter((item) => item.id === 'quotes')
  return (
    <Panel
      title="行情源"
      description="今日、自选和提醒用的实时行情；检查时取一次价格。"
      actions={<Button size="sm" icon={RefreshCw} loading={status.isFetching} onClick={() => void status.refetch()}>重新检查</Button>}
    >
      {status.isPending && <Skeleton lines={2} />}
      {status.isError && <Failure title="行情源状态不可用" error={status.error} retry={() => void status.refetch()} />}
      {status.data && (items.length ? <Checklist items={items} label="行情源的检查结果" compact /> : <p className="text-body text-fg-3">行情源状态不可用。</p>)}
    </Panel>
  )
}

function BarCachePanel() {
  const stats = useQuery({ queryKey: [...CACHE_KEY, 'stats'], queryFn: fetchCacheStats, retry: 1 })
  const facts = stats.data ? cacheFacts(stats.data) : null
  return (
    <Panel
      title="日线缓存"
      description="回测读取的本地日线；下载、导入和删除在回测的数据页。"
      actions={<Link to="/backtest?tab=data" className={buttonClass({ size: 'sm' })}>打开回测 › 数据</Link>}
    >
      {stats.isPending && <Skeleton lines={2} />}
      {stats.isError && <Failure title="日线缓存状态不可用" error={stats.error} retry={() => void stats.refetch()} />}
      {facts && (facts.kept ? (
        <dl className={cx('grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 text-body', numeric)}>
          {facts.rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-fg-3">{label}</dt>
              <dd className="min-w-0 break-words text-fg">{value}</dd>
            </div>
          ))}
        </dl>
      ) : <p className="text-body text-fg-2">{facts.note}</p>)}
    </Panel>
  )
}

function CalendarPanel() {
  const coverage = useQuery({ queryKey: CALENDAR_KEY, queryFn: fetchCalendarCoverage, staleTime: 60 * 60_000 })
  return (
    <Panel title="交易日历" description="收录了节假日表的年份按交易所日历判断交易日；其他年份只按工作日推算，可能把节假日当成交易日。">
      {coverage.isPending && <Skeleton lines={3} />}
      {coverage.isError && <Failure title="交易日历不可用" error={coverage.error} retry={() => void coverage.refetch()} />}
      {coverage.data && (
        <ul aria-label="节假日表收录的年份" className="flex flex-col divide-y divide-line">
          {Object.entries(coverage.data.markets).map(([market, entry]) => {
            const line = calendarLine(entry.years, coverage.data.today)
            return (
              <li key={market} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2 first:pt-0 last:pb-0">
                <span className="font-medium text-fg">{entry.label}</span>
                <span className={cx('text-table', line.warn ? 'text-warn' : 'text-fg-2', numeric)}>{line.text}</span>
              </li>
            )
          })}
        </ul>
      )}
    </Panel>
  )
}

export function DataSection() {
  return (
    <>
      <QuotesPanel />
      <BarCachePanel />
      <CalendarPanel />
    </>
  )
}

export default DataSection
