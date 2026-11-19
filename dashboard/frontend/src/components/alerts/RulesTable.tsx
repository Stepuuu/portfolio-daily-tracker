// The rules table of 提醒: 标的, 类型, 条件 (the stored threshold, digit for digit), 现价 / 距离, 来源, 状态
// (what the backend says: 暂停检查 in amber with its reason and since when, a duplicate disabled by the
// system with why), 今日触发 (counted over the whole day; 不可用 while unknown, never 0), and
// 编辑 · 停用 · 删除. Rules a trade plan owns are read-only here (changed in the plan), with a link to the
// plans page when it exists.
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, DataTable, cx, focusRing, numeric, type Column } from '@/components/ui'
import { UNAVAILABLE } from '@/lib/format'
import { conditionText, distanceView, kindLabel, ruleState, sourceInfo, type AlertRule } from '@/features/alerts/rules'
import { openStockDrawer } from '@/components/market/stockDrawerStore'
import { navPages } from '@/routes'

/** The plans page, when its package has landed (the link is shown only then). */
export const PLANS_PATH = navPages.some((page) => page.path === '/plans') ? '/plans' : null

/** The plans page opened on one plan's drawer (`/plans?plan=<id>`), when the page exists. */
export function planLink(planId: string | null): string | null {
  return PLANS_PATH && planId && /^\d{1,16}$/.test(planId) ? `${PLANS_PATH}?plan=${planId}` : PLANS_PATH
}

/** Today's fires per rule, once every fire of the day has been read. */
export type FiredCounts = { state: 'loading' } | { state: 'unavailable'; reason: string } | { state: 'ok'; counts: Map<number, number> }

export interface RulesTableProps {
  rules: AlertRule[]
  loading: boolean
  names: (ticker: string) => string | null
  firesToday: FiredCounts
  /** The page's Beijing date (from the backend's clock), for "自 10:05 起". */
  today: string | null
  /** Rules the monitor could not evaluate in its last cycle. */
  errored: Set<number>
  readOnly?: string
  busy: Set<number>
  onEdit: (rule: AlertRule) => void
  onToggle: (rule: AlertRule) => void
  onDelete: (rule: AlertRule) => void
  empty: ReactNode
}

export function RulesTable({ rules, loading, names, firesToday, today, errored, readOnly, busy, onEdit, onToggle, onDelete, empty }: RulesTableProps) {
  const subject = (rule: AlertRule) => {
    const name = names(rule.ticker)
    return (
      <div className="min-w-0 py-1.5">
        <div className="truncate font-medium text-fg">{name ?? rule.ticker}</div>
        <div className={cx('text-caption text-fg-3', numeric)}>{name ? rule.ticker : ''}{rule.account ? `${name ? ' · ' : ''}${rule.account}` : ''}</div>
      </div>
    )
  }
  const distance = (rule: AlertRule) => {
    const view = distanceView(rule)
    return (
      <span title={view.title} className="inline-flex items-baseline justify-end gap-2">
        <span className={view.now === null ? 'text-fg-3' : 'text-fg'}>{view.now ?? '不可用'}</span>
        {view.gap && <span className={view.tone === 'warn' ? 'text-warn' : 'text-fg-3'}>{view.gap}</span>}
      </span>
    )
  }
  const state = (rule: AlertRule) => {
    const view = ruleState(rule, errored.has(rule.id), today)
    const label = view.tone === 'plain'
      ? <span className="text-fg-2">{view.label}</span>
      : <Badge tone={view.tone === 'warn' ? 'warn' : 'neutral'} title={view.detail ?? undefined}>{view.label}</Badge>
    return (
      <span className="inline-flex max-w-[18rem] flex-col items-start gap-0.5 whitespace-normal py-1">
        {label}
        {view.detail && view.tone !== 'plain' && (
          <span className={cx('text-caption', view.tone === 'warn' ? 'text-warn' : 'text-fg-3')}>{view.detail}</span>
        )}
      </span>
    )
  }
  const fired = (rule: AlertRule): { text: string; count: number | null; title?: string } => {
    if (firesToday.state === 'loading') return { text: '…', count: null, title: '正在读取今天的触发记录' }
    if (firesToday.state === 'unavailable') return { text: UNAVAILABLE, count: null, title: firesToday.reason }
    const count = firesToday.counts.get(rule.id) ?? 0
    return { text: String(count), count }
  }
  const actions = (rule: AlertRule) => {
    const source = sourceInfo(rule.source)
    if (source.readOnly) {
      const link = planLink(source.planId)
      return link ? (
        <Link to={link} className={cx('rounded-sm px-2 text-table text-accent hover:underline', focusRing)}>查看计划</Link>
      ) : (
        <span className="px-2 text-table text-fg-3">由计划管理</span>
      )
    }
    const pending = busy.has(rule.id)
    return (
      <>
        <Button size="sm" variant="ghost" disabledReason={readOnly} onClick={() => onEdit(rule)}>编辑</Button>
        <Button size="sm" variant="ghost" disabledReason={readOnly} loading={pending} onClick={() => onToggle(rule)}>{rule.enabled ? '停用' : '启用'}</Button>
        <Button size="sm" variant="ghost" disabledReason={readOnly} tipAlign="end" disabled={pending} onClick={() => onDelete(rule)}>删除</Button>
      </>
    )
  }

  const columns: Column<AlertRule>[] = [
    { key: 'subject', header: '标的', render: subject, sortValue: (rule) => names(rule.ticker) ?? rule.ticker },
    { key: 'kind', header: '类型', render: (rule) => kindLabel(rule.kind), sortValue: (rule) => rule.kind },
    { key: 'condition', header: '条件', numeric: true, render: (rule) => conditionText(rule) },
    { key: 'distance', header: '现价 / 距离', numeric: true, render: distance },
    { key: 'source', header: '来源', hideBelow: 'lg', render: (rule) => sourceInfo(rule.source).label },
    { key: 'state', header: '状态', hideBelow: 'md', wrap: true, render: state },
    {
      key: 'fired', header: '今日触发', numeric: true, hideBelow: 'md',
      render: (rule) => {
        const view = fired(rule)
        return <span className={view.count ? 'text-warn' : 'text-fg-3'} title={view.title}>{view.text}</span>
      },
      sortValue: (rule) => fired(rule).count ?? -1,
    },
  ]

  const card = (rule: AlertRule) => {
    const view = distanceView(rule)
    const count = fired(rule)
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-body font-medium text-fg">{names(rule.ticker) ?? rule.ticker}</div>
            <div className={cx('text-table text-fg-2', numeric)}>{kindLabel(rule.kind)} {conditionText(rule)}</div>
          </div>
          <div className={cx('shrink-0 text-right text-table', numeric)} title={view.title}>
            <div className={view.now === null ? 'text-fg-3' : 'text-fg'}>{view.now ?? '不可用'}</div>
            {view.gap && <div className={view.tone === 'warn' ? 'text-warn' : 'text-fg-3'}>{view.gap}</div>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-caption text-fg-3">
          <Badge>{sourceInfo(rule.source).label}</Badge>
          {(count.count ?? 0) > 0 && <Badge tone="warn">今日触发 {count.count}</Badge>}
          {count.count === null && <Badge title={count.title}>今日触发 {count.text}</Badge>}
        </div>
        <div className="text-caption">{state(rule)}</div>
      </div>
    )
  }

  return (
    <DataTable
      caption="提醒规则"
      columns={columns}
      rows={rules}
      rowKey={(rule) => String(rule.id)}
      loading={loading}
      mobile="cards"
      card={card}
      rowActions={actions}
      onRowClick={(rule) => openStockDrawer(rule.ticker, names(rule.ticker))}
      rowClassName={(rule) => (rule.enabled ? undefined : 'text-fg-3')}
      empty={empty}
    />
  )
}
