// 今日触发 (each fire of the day with 记一笔卖出 and, for a plan's rule, 查看计划) and 历史 (every fire, newest
// first, older pages on request). One line per fire, in the words of Today's 今日提醒 panel. A fire held
// back over the cap reads 已抑制（等待下一次汇总） until a published digest covers it (`digest_sent`).
import { Link } from 'react-router-dom'
import { Badge, Button, DataTable, EmptyState, cx, focusRing, numeric, type Column } from '@/components/ui'
import { fmtDateTime } from '@/lib/format'
import { alertLine } from '@/features/today/alerts'
import { DISPLAY_ZONE } from '@/features/today/session'
import { deliveryLabel, suggestsSale, type AlertEvent } from '@/features/alerts/monitor'
import { sourceInfo, type AlertRule } from '@/features/alerts/rules'
import { useShell } from '@/components/shell/shellStore'
import { planLink } from './RulesTable'

export function FiredToday({
  events, rules, readOnly,
}: { events: AlertEvent[]; rules: Map<number, AlertRule>; readOnly?: string }) {
  const openEntry = useShell((state) => state.openEntry)
  if (events.length === 0) {
    return <p className="px-4 pb-4 text-table text-fg-3">今天还没有触发提醒。</p>
  }
  return (
    <ul className="divide-y divide-line border-t border-line text-body">
      {events.map((event) => {
        const line = alertLine(event)
        const plan = sourceInfo(rules.get(event.rule_id)?.source).planId
        return (
          <li key={event.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5">
            <span className="min-w-0">
              <span className={cx('mr-2 text-fg-3', numeric)}>{line.time}</span>
              {line.text}
              {line.source && <span className={cx('ml-1 text-table text-fg-3', numeric)}>（{line.source}）</span>}
              {event.delivery === 'suppressed' && (
                <Badge
                  className="ml-2"
                  tone={event.digest_sent ? 'neutral' : 'warn'}
                  title={event.digest_sent ? '同一时间段提醒太多，这条已合并在汇总通知里' : '同一时间段提醒太多，这条没有单独推送，会合并在下一次汇总通知里'}
                >
                  {deliveryLabel(event.delivery, event.digest_sent)}
                </Badge>
              )}
            </span>
            <span className="flex shrink-0 items-center gap-2">
              {plan && planLink(plan) && (
                <Link to={planLink(plan)!} className={cx('rounded-sm text-table text-accent hover:underline', focusRing)}>查看计划</Link>
              )}
              {suggestsSale(event) && (
                <Button
                  size="sm"
                  disabledReason={readOnly}
                  tipAlign="end"
                  onClick={() => openEntry({ kind: 'sell', ticker: event.ticker, name: event.name ?? undefined, account: event.account ?? undefined })}
                >
                  记一笔卖出
                </Button>
              )}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function AlertHistory({
  events, loading, hasMore, loadingMore, onMore,
}: { events: AlertEvent[]; loading: boolean; hasMore: boolean; loadingMore: boolean; onMore: () => void }) {
  const columns: Column<AlertEvent>[] = [
    { key: 'time', header: '时间', render: (event) => <span className={numeric}>{fmtDateTime(event.fired_at, { timeZone: DISPLAY_ZONE })}</span> },
    { key: 'text', header: '内容', wrap: true, render: (event) => alertLine(event).text },
    { key: 'source', header: '行情来源', hideBelow: 'md', render: (event) => <span className={cx('text-fg-3', numeric)}>{alertLine(event).source}</span> },
    {
      key: 'delivery', header: '通知', hideBelow: 'sm',
      render: (event) => (
        <span className={event.delivery === 'suppressed' && !event.digest_sent ? 'text-warn' : 'text-fg-2'}>{deliveryLabel(event.delivery, event.digest_sent)}</span>
      ),
    },
  ]
  return (
    <DataTable
      caption="提醒历史"
      columns={columns}
      rows={events}
      rowKey={(event) => String(event.id)}
      loading={loading}
      mobile="cards"
      card={(event) => {
        const line = alertLine(event)
        return (
          <div className="flex flex-col gap-0.5 text-body">
            <span className={cx('text-caption text-fg-3', numeric)}>
              {fmtDateTime(event.fired_at, { timeZone: DISPLAY_ZONE })} · {deliveryLabel(event.delivery, event.digest_sent)}
            </span>
            <span className="text-fg">{line.text}</span>
            {line.source && <span className={cx('text-caption text-fg-3', numeric)}>{line.source}</span>}
          </div>
        )
      }}
      density="compact"
      empty={<EmptyState title="还没有触发过提醒" description="规则触发后，每一次都会记在这里。" />}
      footer={hasMore ? (
        <Button size="sm" variant="ghost" loading={loadingMore} onClick={onMore}>加载更早的记录</Button>
      ) : undefined}
    />
  )
}
