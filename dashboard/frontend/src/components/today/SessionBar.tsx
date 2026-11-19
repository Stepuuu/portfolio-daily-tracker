// The line under the page title: market states, how fresh the quotes are, and the close snapshot.
// "A股 盘中 10:32:05 · 东财 3 秒前 · 美股 已收盘 · 今日快照 15:40 自动生成"
import { useQuery } from '@tanstack/react-query'
import { Badge, cx, numeric } from '@/components/ui'
import { fmtRelative, fmtTime } from '@/lib/format'
import { DISPLAY_ZONE, WEEKDAY_ONLY_QUALIFIER, marketStates, sourceLabel, type MarketState } from '@/features/today/session'
import { snapshotChip } from '@/features/today/snapshot'
import { fetchRoutinesStatus, type TodayPayload } from '@/services/today'
import { useNow } from '@/components/shell/useNow'
import { snapshotReceivedAt } from '@/components/shell/useTodayFeed'
import { Dot } from './cells'

export function SessionBar({ data }: { data: TodayPayload | undefined }) {
  const now = useNow(1000)
  const routines = useQuery({ queryKey: ['routines-status'], queryFn: fetchRoutinesStatus, refetchInterval: 60_000, retry: false })
  const states = marketStates(data)
  const snapshot = snapshotChip(routines.data, now)
  if (!data) return <p className="min-w-0 py-1 text-table text-fg-3">正在读取行情…</p>
  // How old the snapshot on screen is: from when it arrived (else its own time).
  const receivedAt = snapshotReceivedAt(data)
  const [first, ...others] = states
  // A phase decided by weekday alone (no holiday table for the year) keeps its amber qualifier.
  const unsure = (state: MarketState) => state.uncertain && (
    <Badge tone="warn" title={state.reason ?? undefined} className="ml-1">
      {WEEKDAY_ONLY_QUALIFIER}
      <span className="sr-only">：{state.reason}</span>
    </Badge>
  )
  const items = [
    first && (
      <span key="market" className="inline-flex items-center gap-1.5">
        <span aria-hidden className={cx('h-1.5 w-1.5 rounded-full', states.some((state) => state.moving) ? 'bg-accent' : 'bg-fg-3')} />
        {first.label} {first.phase}{unsure(first)} <span className={numeric}>{fmtTime(data.as_of, { timeZone: DISPLAY_ZONE })}</span>
      </span>
    ),
    <span key="source" title={`数据时间 ${fmtTime(data.as_of, { timeZone: DISPLAY_ZONE })}（北京时间）`}>
      {sourceLabel(data.source)} <span className={numeric}>{fmtRelative(receivedAt ?? data.as_of, now)}</span>
    </span>,
    ...others.map((state) => <span key={state.market} className="inline-flex items-center">{state.label} {state.phase}{unsure(state)}</span>),
    snapshot && (snapshot.tone === 'warn'
      ? <Badge key="snapshot" tone="warn" title={snapshot.title}>{snapshot.label}</Badge>
      : <span key="snapshot" title={snapshot.title}>{snapshot.label}</span>),
  ].filter(Boolean)
  return (
    <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 py-1 text-table text-fg-2">
      {items.flatMap((item, index) => (index ? [<Dot key={`dot-${index}`} />, item] : [item]))}
    </p>
  )
}
