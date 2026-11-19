// 流水: every confirmed event, newest first, with filters (账户, 类型, 标的, 日期), where each came from, and
// 冲销 per row (a reversal is previewed and confirmed like any entry; history is never deleted).
// Filtered by an account, a transfer shows that account's side and a reversal of its events is listed;
// the date presets run up to the backend's today (Beijing time); the order applies to the whole list before
// a page of it is shown. Amounts keep the ledger's digits (more than 8 places, such as a price derived from
// 成交金额 ÷ 数量, is ≈ rounded to 8 with the full value on hover); a fee, price or rate that was not typed
// carries its booking mark.
import { useMemo, useState } from 'react'
import { Search, Undo2 } from 'lucide-react'
import {
  Badge, Button, DataTable, EmptyState, Input, LongValue, Panel, SegmentedControl, Select, cx, numeric, type Column,
} from '@/components/ui'
import { fmtLong, fmtMoney, fmtPrice, fmtQty, UNAVAILABLE } from '@/lib/format'
import { instrumentKind } from '@/features/today/rows'
import {
  ALL, cardLine, eventCash, feeEstimated, filterEvents, noteMarks, noteText, reversedIds, sortEvents, sourceLabel, type DateRange, type EventFilter,
} from '@/features/ledger/events'
import { kindLabel } from '@/features/ledger/kinds'
import type { LedgerEvent } from '@/features/ledger/types'

const PAGE = 200

const RANGES: { value: DateRange; label: string }[] = [
  { value: 'week', label: '本周' }, { value: 'month', label: '本月' }, { value: 'year', label: '今年' }, { value: 'all', label: '全部' },
]

type Order = 'desc' | 'asc'
const ORDERS: { value: Order; label: string }[] = [{ value: 'desc', label: '新的在前' }, { value: 'asc', label: '旧的在前' }]

/** Money in a column that mixes currencies: every currency by its symbol, CNY included; every digit kept. */
function cash(amount: string | null | undefined, currency: string | null | undefined, signed = false) {
  if (amount === null || amount === undefined) return ''
  return <LongValue value={fmtLong(amount, (value) => fmtMoney(value, currency || 'CNY', { signed, exact: true }))} />
}

const quantity = (value: string | undefined) => <LongValue value={fmtLong(value, fmtQty)} />

function Subject({ event, names, lookup }: { event: LedgerEvent; names: Record<string, string>; lookup: Map<string, LedgerEvent> }) {
  if (event.kind === 'reverse') {
    const target = event.target_id ? lookup.get(event.target_id) : undefined
    return <span className="text-fg-2">{target ? `${target.date} ${kindLabel(target.kind)} ${target.ticker ?? ''}` : '一笔记录'}</span>
  }
  if (event.kind === 'opening') return <span className="text-fg-2">{event.positions?.length ?? 0} 项持仓</span>
  if (event.kind === 'fx') return <span className="text-fg-2">{event.currency} → {event.to_currency}</span>
  if (event.kind === 'transfer') return <span className="text-fg-2">{event.account} → {event.to_account}</span>
  if (!event.ticker) return <span className="text-fg-3">现金</span>
  const name = event.name ?? names[event.ticker]
  return (
    <span className="whitespace-nowrap">
      {name && name !== event.ticker && <span className="mr-1.5 text-fg">{name}</span>}
      <span className={name && name !== event.ticker ? 'text-fg-3' : 'text-fg'}>{event.ticker}</span>
    </span>
  )
}

const CASH_KINDS = new Set(['buy', 'sell', 'deposit', 'withdrawal', 'dividend', 'fee', 'interest', 'transfer', 'fx', 'fund_buy', 'fund_sell'])

/** The cash effect seen from `side` (the filtered account); 不可用 when a cash event lacks its figures. */
function Amount({ event, side }: { event: LedgerEvent; side: string | null }) {
  const effect = eventCash(event, side)
  if (event.kind === 'fund_value') return <span className="text-fg-2">估值 {cash(event.amount, 'CNY')}</span>
  if (effect.amount === null) return CASH_KINDS.has(event.kind) ? <span className="text-fg-3">{UNAVAILABLE}</span> : null
  const main = cash(effect.amount, effect.currency, true)
  if (!effect.received) return <>{main}</>
  return <>{main} → {cash(effect.received.amount, effect.received.currency, true)}</>
}

function Marks({ event }: { event: LedgerEvent }) {
  const marks = event.kind === 'reverse' ? [] : noteMarks(event.note)
  const text = event.kind === 'reverse' ? '' : noteText(event.note)
  if (!marks.length && !text) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-fg-3">
      {text && <span className="break-words">备注：{text}</span>}
      {marks.map((mark) => <Badge key={mark} tone="warn">{mark}</Badge>)}
    </span>
  )
}

export interface EventsTableProps {
  events: LedgerEvent[]
  accounts: string[]
  names: Record<string, string>
  /** The backend's today (YYYY-MM-DD, Beijing time); null while unknown: the date presets are off then. */
  today: string | null
  loading?: boolean
  /** 冲销 for a row; absent hides the action. */
  onReverse?: (event: LedgerEvent) => void
  reversing?: string | null
  /** Why 冲销 is off (demo, or the app mode is not known). */
  writeBlocked?: string
}

export function EventsTable({ events, accounts, names, today, loading = false, onReverse, reversing, writeBlocked }: EventsTableProps) {
  const [filter, setFilter] = useState<EventFilter>({ account: ALL, kind: ALL, query: '', range: 'all' })
  const [order, setOrder] = useState<Order>('desc')
  const [limit, setLimit] = useState(PAGE)
  const reversed = useMemo(() => reversedIds(events), [events])
  const lookup = useMemo(() => new Map(events.map((event) => [event.id, event])), [events])
  const sorted = useMemo(() => sortEvents(events, order), [events, order])
  const filtered = useMemo(() => filterEvents(sorted, filter, today, names), [sorted, filter, today, names])
  const shown = filtered.slice(0, limit)
  const kinds = useMemo(() => [...new Set(events.map((event) => event.kind))], [events])
  const filtering = filter.account !== ALL || filter.kind !== ALL || filter.query.trim() !== '' || filter.range !== 'all'
  const side = filter.account || null
  const set = (next: Partial<EventFilter>) => {
    setFilter((current) => ({ ...current, ...next }))
    setLimit(PAGE)
  }

  const columns: Column<LedgerEvent>[] = [
    { key: 'date', header: '日期', width: 104, render: (e) => <span className={numeric}>{e.date}</span> },
    {
      key: 'kind', header: '类型',
      render: (e) => (
        <span className="inline-flex items-center gap-1.5">
          {kindLabel(e.kind)}
          {reversed.has(e.id) && <Badge>已冲销</Badge>}
        </span>
      ),
    },
    { key: 'account', header: '账户', hideBelow: 'md', render: (e) => <span className="text-fg-2">{e.account ?? ''}</span> },
    { key: 'subject', header: '标的', render: (e) => <Subject event={e} names={names} lookup={lookup} /> },
    {
      key: 'quantity', header: '数量', numeric: true,
      render: (e) => (e.kind === 'split' ? <>×{quantity(e.ratio)}</> : e.quantity ? quantity(e.quantity) : ''),
    },
    {
      key: 'price', header: '价格', numeric: true, hideBelow: 'sm',
      render: (e) => (e.price
        ? <LongValue value={fmtLong(e.price, (value) => fmtPrice(value, e.currency ?? 'CNY', instrumentKind(e.ticker), { symbol: true, exact: true }))} />
        : ''),
    },
    { key: 'amount', header: side ? `金额（${side} 视角，原币）` : '金额（原币）', numeric: true, render: (e) => <Amount event={e} side={side} /> },
    {
      key: 'fee', header: '费用', numeric: true, hideBelow: 'sm',
      render: (e) => (e.fee && ['buy', 'sell', 'fund_buy', 'fund_sell'].includes(e.kind) ? (
        <span className="inline-flex items-center justify-end gap-1">
          {cash(e.fee, e.kind.startsWith('fund') ? 'CNY' : e.currency ?? 'CNY')}
          {feeEstimated(e) && <Badge tone="warn" title="按费率估算，不是成交单上的费用">估算</Badge>}
        </span>
      ) : ''),
    },
    { key: 'source', header: '来源', hideBelow: 'lg', render: (e) => <span className="text-fg-2">{sourceLabel(e.source)}</span> },
  ]

  const canReverse = (e: LedgerEvent) => Boolean(onReverse) && e.kind !== 'reverse' && !reversed.has(e.id)
  const reverseButton = (e: LedgerEvent) => canReverse(e) ? (
    <Button
      size="sm"
      variant="ghost"
      icon={Undo2}
      loading={reversing === e.id}
      disabledReason={writeBlocked}
      tipAlign="end"
      onClick={() => onReverse?.(e)}
      aria-label={`冲销 ${e.date} ${kindLabel(e.kind)}${e.ticker ? ` ${e.ticker}` : ''}`}
    >
      冲销
    </Button>
  ) : null
  const dates = filtered.map((event) => event.date)
  const first = dates.length ? dates.reduce((a, b) => (a < b ? a : b)) : null
  const last = dates.length ? dates.reduce((a, b) => (a > b ? a : b)) : null
  const ranges = RANGES.map((range) => ({ ...range, disabled: range.value !== 'all' && !today }))
  const clear = () => set({ account: ALL, kind: ALL, query: '', range: 'all' })

  return (
    <div className="flex flex-col gap-3">
      <div role="group" aria-label="筛选" className="flex flex-wrap items-center gap-2">
        <Select size="sm" aria-label="账户" className="w-32" value={filter.account} onChange={(event) => set({ account: event.target.value })}
          options={[{ value: ALL, label: '全部账户' }, ...accounts.map((name) => ({ value: name, label: name }))]} />
        <Select size="sm" aria-label="类型" className="w-32" value={filter.kind} onChange={(event) => set({ kind: event.target.value })}
          options={[{ value: ALL, label: '全部类型' }, ...kinds.map((kind) => ({ value: kind, label: kindLabel(kind) }))]} />
        <Input size="sm" aria-label="标的" placeholder="代码或名称" prefix={<Search size={14} aria-hidden />} className="w-40"
          value={filter.query} onChange={(event) => set({ query: event.target.value })} />
        <SegmentedControl<DateRange> size="sm" ariaLabel="日期范围" value={today ? filter.range : 'all'} onChange={(range) => set({ range })} options={ranges} />
        <SegmentedControl<Order> size="sm" ariaLabel="排序" value={order} onChange={(next) => { setOrder(next); setLimit(PAGE) }} options={ORDERS} />
        {filtering && <Button size="sm" variant="ghost" onClick={clear}>清除筛选</Button>}
      </div>
      <Panel padding="none" aria-label="流水">
        <DataTable
          caption="账本流水"
          columns={columns}
          rows={shown}
          rowKey={(e) => e.id}
          loading={loading}
          mobile="cards"
          rowClassName={(e) => (reversed.has(e.id) ? '[&>td]:text-fg-3' : undefined)}
          rowActions={onReverse ? reverseButton : undefined}
          renderRowExtra={(e) => <Marks event={e} />}
          card={(e) => (
            <div className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-body text-fg">
                  {kindLabel(e.kind)} <Subject event={e} names={names} lookup={lookup} />
                </span>
                <span className={cx('shrink-0 text-body', numeric)}><Amount event={e} side={side} /></span>
              </div>
              <p className={cx('text-caption text-fg-3', numeric)}>
                <LongValue value={cardLine(e)} />
                {reversed.has(e.id) && <Badge className="ml-2">已冲销</Badge>}
              </p>
            </div>
          )}
          empty={filtering
            ? <EmptyState title="没有符合筛选的记录" action={<Button size="sm" onClick={clear}>清除筛选</Button>} />
            : <EmptyState title="还没有已确认的记录" description="记一笔并确认后，流水会出现在这里。" />}
          footer={filtered.length ? (
            <span className={cx('flex flex-wrap items-center justify-between gap-2', numeric)}>
              <span>共 {filtered.length} 笔 · {first} 至 {last}{filter.range !== 'all' && today ? `（截至 ${today}）` : ''}</span>
              {filtered.length > shown.length && (
                <Button size="sm" variant="ghost" onClick={() => setLimit((value) => value + PAGE * 5)}>
                  显示{order === 'desc' ? '更早' : '更晚'}的 {Math.min(filtered.length - shown.length, PAGE * 5)} 笔
                </Button>
              )}
            </span>
          ) : undefined}
        />
      </Panel>
    </div>
  )
}
