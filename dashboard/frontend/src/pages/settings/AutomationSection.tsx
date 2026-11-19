// 设置 › 自动化与通知: the alert monitor (`/api/alerts/status`, in `monitorLine`'s words), the daily routines
// (`/api/routines/status`: close snapshot, pre- and post-market briefs, with switch, time, today's run, last
// success and next attempt, and 立即运行) and the notification channels. Switches and times are environment
// variables read when the backend starts, so each item shows its current value and the exact line to change
// it: the setup checklist's items, rendered by the wizard's own component, and `EnvHowTo`.
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, RefreshCw } from 'lucide-react'
import {
  Badge, Button, DataTable, InlineAlert, Panel, Skeleton, buttonClass, cx, numeric, toast, type Column,
} from '@/components/ui'
import { Checklist, CommandLine, EnvHowTo } from '@/components/setup/Checklist'
import { useAppMode } from '@/components/shell/useAppMode'
import { getApiErrorMessage } from '@/services/api'
import { STATUS_KEY, fetchMonitorStatus } from '@/services/alerts'
import { ROUTINES_KEY, runRoutine } from '@/services/settings'
import { SETUP_KEY, SETUP_STATUS_KEY, fetchSetupStatus } from '@/services/setup'
import { fetchRoutinesStatus } from '@/services/today'
import { MONITOR_CAVEAT, monitorLine } from '@/features/alerts/monitor'
import { fmtDate } from '@/lib/format'
import { DISPLAY_ZONE } from '@/features/today/session'
import { routineRows, type RoutineRow, type RoutinesReport } from '@/features/settings/routines'
import type { SetupItem } from '@/features/setup/types'

function useChecklist() {
  return useQuery({ queryKey: SETUP_STATUS_KEY, queryFn: () => fetchSetupStatus(true), staleTime: 5_000 })
}

function itemsOf(items: SetupItem[] | undefined, id: SetupItem['id']): SetupItem[] {
  return (items ?? []).filter((item) => item.id === id)
}

/** The monitor as `/api/alerts/status` sees it; the setup item adds only the line that starts it. */
function MonitorPanel({ checklist, refresh }: { checklist: ReturnType<typeof useChecklist>; refresh: () => void }) {
  const { demo } = useAppMode()
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: fetchMonitorStatus, refetchInterval: 15_000, retry: false })
  const line = monitorLine(status.data, fmtDate(new Date(), { timeZone: DISPLAY_ZONE }), { demo })
  const fix = itemsOf(checklist.data?.items, 'alerts')[0]?.action
  return (
    <Panel
      title="价格提醒监控"
      description={MONITOR_CAVEAT}
      actions={(
        <>
          {/* Every panel of the section: the setup checks, the routines and the monitor. */}
          <Button size="sm" icon={RefreshCw} loading={checklist.isFetching} onClick={refresh}>重新检查</Button>
          <Link to="/alerts" className={buttonClass({ size: 'sm' })}>查看提醒</Link>
        </>
      )}
    >
      <div className="flex flex-col gap-4">
        {status.isPending && <Skeleton w={260} />}
        {status.isError && <p className="text-body text-warn">监控状态不可用：{getApiErrorMessage(status.error)}</p>}
        {line && (
          <div className="flex flex-col gap-1">
            <p className="flex flex-wrap items-center gap-2 text-body">
              <Badge tone={line.warn ? 'warn' : 'neutral'}>{line.label}</Badge>
              {line.checked && <span className={cx('text-table text-fg-3', numeric)}>{line.checked}</span>}
            </p>
            <p className="text-table text-fg-2">{line.message}</p>
            {line.notes.map((note) => <p key={note} className="text-table text-warn">{note}</p>)}
          </div>
        )}
        {fix?.command && <CommandLine label={fix.label} text={fix.command} />}
      </div>
    </Panel>
  )
}

function RunButton({ row }: { row: RoutineRow }) {
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const run = useMutation({
    mutationFn: () => runRoutine(row.kind),
    onSuccess: (message) => {
      toast.success(`${row.title}：${message}`)
      void client.invalidateQueries({ queryKey: ROUTINES_KEY })
    },
    onError: (error) => toast.warn(`${row.title}没有运行：${getApiErrorMessage(error)}`),
  })
  return (
    <Button
      size="sm"
      icon={Play}
      loading={run.isPending}
      disabledReason={readOnlyReason}
      tipAlign="end"
      onClick={() => run.mutate()}
      aria-label={`立即运行${row.title}`}
    >
      立即运行
    </Button>
  )
}

const COLUMNS: Column<RoutineRow>[] = [
  { key: 'title', header: '任务', render: (row) => <span className="font-medium text-fg">{row.title}</span> },
  { key: 'switch', header: '开关', render: (row) => <span className={row.switch === '开' ? 'text-fg' : 'text-fg-3'}>{row.switch}</span> },
  {
    key: 'time', header: '时间', numeric: true,
    render: (row) => <span title={row.problem ?? undefined} className={row.problem ? 'text-warn' : undefined}>{row.time}</span>,
  },
  {
    key: 'today', header: '今天', wrap: true,
    render: (row) => <span title={row.detail ?? undefined} className={row.todayWarn ? 'text-warn' : 'text-fg-2'}>{row.today}</span>,
  },
  { key: 'last', header: '上次成功', numeric: true, hideBelow: 'lg', render: (row) => row.last },
  { key: 'next', header: '下次自动运行', numeric: true, render: (row) => row.next },
]

/** A routine on a phone: the times on lines of their own, never cut. */
function RoutineCard({ row }: { row: RoutineRow }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-body font-medium text-fg">{row.title}</span>
        <span className={cx('text-table', row.problem ? 'text-warn' : 'text-fg-2', numeric)} title={row.problem ?? undefined}>
          {row.switch} · {row.time}
        </span>
      </div>
      <p className={cx('break-words text-table', row.todayWarn ? 'text-warn' : 'text-fg-2')}>今天：{row.today}</p>
      <p className={cx('text-table text-fg-3', numeric)}>上次成功：{row.last}</p>
      <p className={cx('text-table text-fg-3', numeric)}>下次自动运行：{row.next}</p>
    </div>
  )
}

function RoutinesPanel({ checklist }: { checklist: SetupItem[] | undefined }) {
  const status = useQuery({ queryKey: ROUTINES_KEY, queryFn: fetchRoutinesStatus, refetchInterval: 60_000, retry: false })
  const report = status.data as RoutinesReport | undefined
  const rows = routineRows(report)
  const note = report?.scheduler?.note
  const problems = rows.filter((row) => row.problem)
  const item = itemsOf(checklist, 'routines')[0]
  return (
    <Panel title="每日例行任务" description="交易日按时间自动运行；后端关闭期间不会运行，启动后补做当天的。" padding="none">
      {status.isError && (
        <div className="px-4 pb-4">
          <InlineAlert tone="warn" title="例行任务状态不可用" action={<Button size="sm" onClick={() => void status.refetch()}>重试</Button>}>
            {getApiErrorMessage(status.error)}
          </InlineAlert>
        </div>
      )}
      {note && <p className="px-4 pb-3 text-table text-warn">{note}</p>}
      {!status.isError && (
        <DataTable
          caption="每日例行任务"
          columns={COLUMNS}
          rows={rows}
          rowKey={(row) => row.kind}
          loading={status.isPending}
          stickyHeader={false}
          mobile="cards"
          card={(row) => <RoutineCard row={row} />}
          rowActions={(row) => <RunButton row={row} />}
          empty={<p className="px-4 py-6 text-body text-fg-3">后端没有登记例行任务。</p>}
        />
      )}
      {(problems.length > 0 || item?.action?.command) && (
        <div className="flex flex-col gap-3 border-t border-line p-4">
          {problems.map((row) => <p key={row.kind} className="text-table text-warn">{row.title}：{row.problem}</p>)}
          {item?.action?.command && <CommandLine label={item.action.label} text={item.action.command} />}
        </div>
      )}
    </Panel>
  )
}

export function AutomationSection() {
  const client = useQueryClient()
  const checklist = useChecklist()
  const items = checklist.data?.items
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: SETUP_KEY }),
    client.invalidateQueries({ queryKey: ROUTINES_KEY }),
    client.invalidateQueries({ queryKey: STATUS_KEY }),
  ])
  return (
    <>
      {checklist.isError && (
        <InlineAlert tone="warn" title="设置检查不可用">{getApiErrorMessage(checklist.error)}</InlineAlert>
      )}
      <MonitorPanel checklist={checklist} refresh={() => void refresh()} />
      <RoutinesPanel checklist={items} />
      <Panel title="通知渠道" description="提醒和例行任务的结果先进站内通知；配置了外部渠道时同时推送。">
        {checklist.isPending && <Skeleton lines={2} />}
        {items && <Checklist items={itemsOf(items, 'notifications')} label="通知渠道" compact />}
      </Panel>
      <Panel title="怎样修改" description="上面的开关和时间在后端启动时读取，不在页面里保存。">
        <EnvHowTo />
      </Panel>
    </>
  )
}

export default AutomationSection
