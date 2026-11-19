// 行情 › 提醒 (/alerts): who checks the rules (this process, another one, nobody, or nobody by design in
// the demo) and when it last checked, with its honest limits; the rules with their current price,
// distance and the state the backend reports (skipped rules in amber with the reason); today's fires
// (every one of the day, read to the end) with 记一笔卖出; the history. New rules come from the 新建提醒
// dialog (also opened from Today, the watchlist and the stock drawer). Rules a trade plan owns are
// read-only here. Data: /api/alerts/{rules,events,status}; writes refused in the demo.
import { useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellPlus, BellRing } from 'lucide-react'
import { Badge, Button, Dialog, EmptyState, InlineAlert, Panel, cx, numeric, toast } from '@/components/ui'
import { fmtDate } from '@/lib/format'
import { DISPLAY_ZONE } from '@/features/today/session'
import { MONITOR_CAVEAT, dayCounts, monitorLine, staleDayNote } from '@/features/alerts/monitor'
import { conditionText, kindLabel, sourceInfo, type AlertRule } from '@/features/alerts/rules'
import { useTodayFeed } from '@/components/shell/useTodayFeed'
import { useAppMode } from '@/components/shell/useAppMode'
import { openEditAlert, openNewAlert } from '@/components/alerts/alertDialogStore'
import { RulesTable, type FiredCounts } from '@/components/alerts/RulesTable'
import { AlertHistory, FiredToday } from '@/components/alerts/AlertEvents'
import { errorText, nameOf, useAlertRules, useNames } from '@/components/market/hooks'
import {
  ALERTS_KEY, STATUS_KEY, deleteAlertRule, fetchDayEvents, fetchEvents, fetchMonitorStatus, updateAlertRule,
} from '@/services/alerts'

const EVENTS_KEY = [...ALERTS_KEY, 'events', 'history']
const DAY_KEY = [...ALERTS_KEY, 'events', 'day']

function MonitorBar({ today, onNew, readOnly, demo }: { today: string | null; onNew: () => void; readOnly?: string; demo: boolean }) {
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: fetchMonitorStatus, refetchInterval: 15_000, retry: false })
  // The read-only demo never runs the monitor (the status says `demo`; an older backend is told by the
  // app's mode): that is by design, not a fault.
  const line = monitorLine(status.data, today, { demo })
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <p className={cx('flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-2', numeric)}>
          <span className="font-medium text-fg">监控</span>
          {status.isError ? (
            <Badge tone="warn" title={errorText(status.error)}>状态未知</Badge>
          ) : line ? (
            line.warn ? <Badge tone="warn" title={line.message}>{line.label}</Badge> : <span title={line.message}>{line.label}</span>
          ) : (
            <span className="text-fg-3">读取中…</span>
          )}
          {line?.checked && <><span aria-hidden className="text-fg-3">·</span><span>{line.checked}</span></>}
        </p>
        {/* Why nobody (or nobody verified) checks: the line's own words; skipped rules are on their rows. */}
        {line && line.warn && line.message && !line.label.startsWith('运行中') && (
          <p className="mt-0.5 max-w-[60rem] text-caption text-warn">{line.message}</p>
        )}
        {line?.notes.map((note) => <p key={note} className="mt-0.5 max-w-[60rem] text-caption text-warn">{note}</p>)}
        <p className="mt-0.5 text-caption text-fg-3" title={status.data?.notice}>{MONITOR_CAVEAT}</p>
      </div>
      <Button variant="primary" icon={BellPlus} disabledReason={readOnly} tipAlign="end" onClick={onNew}>新建提醒</Button>
    </div>
  )
}

export default function Alerts() {
  const client = useQueryClient()
  const { demo, readOnlyReason } = useAppMode()
  const readOnly = readOnlyReason
  const todayFeed = useTodayFeed().data
  const today = todayFeed ? fmtDate(todayFeed.as_of, { timeZone: DISPLAY_ZONE }) : fmtDate(new Date(), { timeZone: DISPLAY_ZONE })
  const rules = useAlertRules(true)
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: fetchMonitorStatus, refetchInterval: 15_000, retry: false })
  // Every fire of today, read page by page to the start of the day: the counts and 今日触发 list.
  const day = useQuery({
    queryKey: [...DAY_KEY, today],
    queryFn: ({ signal }) => fetchDayEvents(today, signal),
    refetchInterval: 15_000,
    retry: false,
  })
  const events = useInfiniteQuery({
    queryKey: EVENTS_KEY,
    queryFn: ({ pageParam }) => fetchEvents({ limit: 50, before: pageParam }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before ?? undefined,
    refetchInterval: 15_000,
    retry: false,
  })
  const [busy, setBusy] = useState<Set<number>>(new Set())
  const [deleting, setDeleting] = useState<AlertRule | null>(null)

  const items = rules.data?.items ?? []
  const allEvents = events.data?.pages.flatMap((page) => page.items) ?? []
  const names = useNames([...items.map((rule) => rule.ticker), ...allEvents.map((event) => event.ticker)])
  const nameFor = (ticker: string) => nameOf(names, ticker) ?? allEvents.find((event) => event.ticker === ticker && event.name)?.name ?? null
  const firedList = day.data ?? []
  // A failed refresh leaves the earlier read cached: its counts are not today's any more (不可用).
  const dayFailure = day.isError ? errorText(day.error) : null
  const fired: FiredCounts = dayCounts(day.data, dayFailure, today)
  const errored = new Set(status.data?.errored_rule_ids ?? [])
  const byId = new Map(items.map((rule) => [rule.id, rule]))
  const planRules = items.some((rule) => sourceInfo(rule.source).readOnly)
  const enabled = items.filter((rule) => rule.enabled).length

  const refresh = () => client.invalidateQueries({ queryKey: ALERTS_KEY })
  const mark = (id: number, on: boolean) => setBusy((current) => {
    const next = new Set(current)
    if (on) next.add(id)
    else next.delete(id)
    return next
  })

  const toggle = useMutation({
    mutationFn: (rule: AlertRule) => updateAlertRule(rule.id, { enabled: !rule.enabled }),
    onMutate: (rule) => mark(rule.id, true),
    onSuccess: (rule) => {
      toast.success(`${rule.enabled ? '已启用' : '已停用'}：${nameFor(rule.ticker) ?? rule.ticker} ${kindLabel(rule.kind)} ${conditionText(rule)}`)
      void refresh()
    },
    onError: (exc) => toast.warn(errorText(exc)),
    onSettled: (_data, _error, rule) => mark(rule.id, false),
  })

  const remove = useMutation({
    mutationFn: (rule: AlertRule) => deleteAlertRule(rule.id),
    onSuccess: ({ rule }) => {
      setDeleting(null)
      toast.success(`已删除提醒：${nameFor(rule.ticker) ?? rule.ticker} ${kindLabel(rule.kind)} ${conditionText(rule)}`)
      void refresh()
    },
    onError: (exc) => toast.warn(errorText(exc)),
  })

  const newAlert = () => openNewAlert()

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 text-body text-fg">
      <h1 className="sr-only">提醒</h1>
      <MonitorBar today={today} onNew={newAlert} readOnly={readOnly} demo={demo} />

      {rules.isError && !rules.data ? (
        <InlineAlert tone="warn" title="无法读取提醒规则" action={<Button size="sm" onClick={() => void rules.refetch()}>重试</Button>}>
          {errorText(rules.error)}
        </InlineAlert>
      ) : (
        <Panel
          padding="none"
          title="规则"
          description={rules.data ? <span className={numeric}>{items.length} 条 · 启用 {enabled} 条</span> : undefined}
        >
          <RulesTable
            rules={items}
            loading={rules.isPending}
            names={nameFor}
            firesToday={fired}
            today={today}
            errored={errored}
            readOnly={readOnly}
            busy={busy}
            onEdit={(rule) => openEditAlert(rule, nameFor(rule.ticker))}
            onToggle={(rule) => toggle.mutate(rule)}
            onDelete={setDeleting}
            empty={(
              <EmptyState
                icon={BellRing}
                title="还没有提醒"
                description="设一个止损或目标价，到价时会通知你；只通知，不会下单。"
                action={readOnly ? undefined : <Button variant="primary" icon={BellPlus} onClick={newAlert}>新建提醒</Button>}
              />
            )}
          />
          {planRules && <p className="border-t border-line px-4 py-2 text-table text-fg-3">来自交易计划的规则在这里只读，在计划中修改。</p>}
        </Panel>
      )}

      {/* Nothing set and nothing fired yet: the rules panel's next step is all there is to show. */}
      {(items.length > 0 || allEvents.length > 0 || firedList.length > 0 || events.isError || day.isError) && (
        <Panel padding="none" title="今日触发" description={firedList.length && dayFailure === null ? <span className={numeric}>{firedList.length} 次</span> : undefined}>
          {day.isError && !day.data ? (
            <p className="px-4 pb-4 text-table text-warn">今天的触发记录暂时无法读取（{errorText(day.error)}），今天是否触发过提醒现在无法确认。</p>
          ) : day.isPending ? (
            <p className="px-4 pb-4 text-table text-fg-3">正在读取今天的触发记录…</p>
          ) : (
            <>
              {dayFailure !== null && <p className="px-4 pb-2 text-table text-warn">{staleDayNote(dayFailure)}</p>}
              <FiredToday events={firedList} rules={byId} readOnly={readOnly} />
            </>
          )}
        </Panel>
      )}

      {events.isError && !events.data && (
        <p className="text-table text-warn">提醒历史暂时无法读取：{errorText(events.error)}</p>
      )}
      {allEvents.length > 0 && (
        <Panel padding="none" title="历史">
          <AlertHistory
            events={allEvents}
            loading={events.isPending}
            hasMore={Boolean(events.hasNextPage)}
            loadingMore={events.isFetchingNextPage}
            onMore={() => void events.fetchNextPage()}
          />
        </Panel>
      )}

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        size="sm"
        title="删除这条提醒？"
        footer={(
          <>
            <Button variant="ghost" data-autofocus onClick={() => setDeleting(null)}>取消</Button>
            <Button loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting)}>删除</Button>
          </>
        )}
      >
        {deleting && (
          <p className="text-body text-fg-2">
            {nameFor(deleting.ticker) ?? deleting.ticker} {kindLabel(deleting.kind)} <span className={numeric}>{conditionText(deleting)}</span>。
            删除后不再检查；已触发的记录会保留。
          </p>
        )}
      </Dialog>
    </div>
  )
}
