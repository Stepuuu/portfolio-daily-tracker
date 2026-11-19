// 今日提醒: today's fired alerts and the monitor's state, with the honest limits. Hidden when the backend
// has no alerts API (an older backend: 404), never filled with made-up content. Only an answered,
// current history may say that nothing fired: a pending request says so, a failed one is 不可用, and a
// failed refresh marks what is shown as possibly outdated (rules in features/today/alerts.ts).
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { Badge, Button, Panel, cx, numeric } from '@/components/ui'
import { ALERT_LIMITS, alertLine, alertsListState, monitorState, todaysEvents } from '@/features/today/alerts'
import { fetchAlertEvents, fetchAlertStatus } from '@/services/today'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { useShell } from '@/components/shell/shellStore'
import { useAppMode } from '@/components/shell/useAppMode'

export function useAlertsFeed() {
  const status = useQuery({ queryKey: ['alerts', 'status'], queryFn: fetchAlertStatus, refetchInterval: 30_000, retry: false })
  const events = useQuery({ queryKey: ['alerts', 'events'], queryFn: () => fetchAlertEvents(20), refetchInterval: 15_000, retry: false })
  // An older backend without the alerts API answers 404: no panel. Any other failure is shown.
  const absent = status.isError && httpStatus(status.error) === 404
  const list = (today: string | null) => alertsListState({
    status: status.data ?? { status: 'unavailable' },
    events: events.data?.items,
    failed: events.isError,
    error: events.isError ? getApiErrorMessage(events.error) : null,
    updatedAt: events.dataUpdatedAt,
    today,
  })
  return {
    status,
    events,
    available: !absent && (status.isSuccess || status.isError),
    list,
    /**
     * `quiet` (nothing held): the panel shows only when something fired today, or when it cannot tell
     * (history unavailable or outdated) while rules are enabled.
     */
    shown: (today: string | null, quiet: boolean) => {
      if (absent || !(status.isSuccess || status.isError)) return false
      if (!quiet) return true
      const state = list(today)
      return state.kind === 'list' || (Boolean(status.data?.rules_enabled) && (state.kind === 'unavailable' || state.stale))
    },
    firedToday: (today: string | null) => todaysEvents(events.data?.items, today).length,
  }
}

export function TodayAlerts({ today, quiet = false, className }: { today: string | null; quiet?: boolean; className?: string }) {
  const feed = useAlertsFeed()
  const { status } = feed
  const { readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  if (!feed.shown(today, quiet)) return null
  const state = feed.list(today)
  const monitor = status.data ? monitorState(status.data) : { text: '提醒监控 状态不可用', tone: 'warn' as const, title: undefined }
  const monitorTitle = monitor?.title ?? (status.isError ? getApiErrorMessage(status.error) : undefined)
  return (
    <Panel
      className={className}
      title="今日提醒"
      description={(
        <span className="mt-0.5 flex flex-col items-start gap-1" title={status.data?.notice}>
          {monitor && (monitor.tone === 'warn'
            ? <Badge tone="warn" title={monitorTitle}>{monitor.text}</Badge>
            : <span title={monitorTitle}>{monitor.text}</span>)}
          <span>{ALERT_LIMITS}</span>
        </span>
      )}
    >
      {state.staleText && (
        <p className="mb-2 flex items-start gap-1.5 text-table text-warn">
          <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0" />
          {state.staleText}
        </p>
      )}
      {state.kind === 'list' ? (
        <ul className="-my-2 divide-y divide-line text-body">
          {state.fired.map((event) => {
            const line = alertLine(event)
            return (
              <li key={event.id} className="flex items-center justify-between gap-3 py-2">
                <span className="min-w-0">
                  <span className={cx('mr-2 text-fg-3', numeric)}>{line.time}</span>
                  {line.text}
                  {line.source && <span className={cx('ml-1 text-table text-fg-3', numeric)}>（{line.source}）</span>}
                </span>
                {line.sell && (
                  <Button
                    size="sm"
                    variant="ghost"
                    tipAlign="end"
                    disabledReason={readOnlyReason}
                    onClick={() => openEntry({ kind: 'sell', ticker: event.ticker, name: event.name ?? undefined, account: event.account ?? undefined })}
                  >
                    记一笔卖出
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      ) : (
        <p
          className={cx('text-table', state.kind === 'unavailable' ? 'text-warn' : 'text-fg-3')}
          aria-busy={state.kind === 'loading' || undefined}
        >
          {state.text}
        </p>
      )}
    </Panel>
  )
}
