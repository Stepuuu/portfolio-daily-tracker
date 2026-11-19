// The watchlist table: 名称 / 代码 (with the quote's source, time and state), 现价, 涨跌幅, 涨跌额, 备注
// (edited in place), 提醒 (rules for the listing; 不可用 when the rules could not be read, never 0) and row
// actions (上移 · 下移 · 设提醒 · 移出). A row opens the stock drawer. Phones get two-line rows like Today's
// holdings. An unavailable quote reads 不可用 with its reason, never 0.
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, BellPlus, X } from 'lucide-react'
import { Badge, Button, DataTable, IconButton, Input, cx, focusRing, numeric, type Column } from '@/components/ui'
import { fmtPct, UNAVAILABLE } from '@/lib/format'
import { changeAmount, displayName, hasPrice, ruleCountFor, type WatchlistItem } from '@/features/market/watchlist'
import { currencyOf, fmtTickerPrice } from '@/features/market/format'
import { signed } from '@/components/today/cells'
import { openNewAlert } from '@/components/alerts/alertDialogStore'
import { QuoteMeta } from './QuoteMeta'
import { openStockDrawer } from './stockDrawerStore'

export interface WatchlistTableProps {
  items: WatchlistItem[]
  loading: boolean
  today: string | null
  /** Alert rules per listing identity; null while unknown (`rulesState` says why). */
  ruleCounts: Map<string, { total: number; enabled: number }> | null
  /** The rules request: still loading, or failed (with its message). */
  rulesState: { loading: boolean; error: string | null }
  /** Why writes are refused (demo, or the app mode is not known), else undefined. */
  readOnly?: string
  onMove: (ticker: string, delta: -1 | 1) => void
  onRemove: (item: WatchlistItem) => void
  onNote: (ticker: string, note: string | null) => Promise<unknown>
  empty: ReactNode
}

function NoteCell({ item, readOnly, onSave }: { item: WatchlistItem; readOnly?: string; onSave: (note: string | null) => Promise<unknown> }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(item.note ?? '')
  const [saving, setSaving] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  /** Back to the note's button once it is rendered again (after save or Esc). */
  const refocus = useRef(false)
  useEffect(() => {
    if (!editing) setText(item.note ?? '')
  }, [item.note, editing])
  useEffect(() => {
    if (editing) input.current?.focus()
    else if (refocus.current) {
      refocus.current = false
      trigger.current?.focus()
    }
  }, [editing])
  const stop = () => {
    refocus.current = true
    setEditing(false)
  }
  const finish = async (save: boolean) => {
    if (!editing || saving) return
    const next = text.trim() || null
    if (save && next !== (item.note ?? null)) {
      setSaving(true)
      try {
        await onSave(next)
      } catch {
        // The page shows the error; keep what was typed so nothing is lost.
        setSaving(false)
        return
      }
      setSaving(false)
    }
    stop()
  }
  if (readOnly) return <span className="block min-w-0 whitespace-normal text-fg-2">{item.note ?? ''}</span>
  if (editing) {
    return (
      <Input
        ref={input}
        size="sm"
        value={text}
        maxLength={200}
        aria-label={`${displayName(item)} 的备注`}
        placeholder="写点什么，回车保存"
        disabled={saving}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
          if (event.nativeEvent.isComposing) return
          if (event.key === 'Enter') {
            event.preventDefault()
            void finish(true)
          } else if (event.key === 'Escape') {
            event.preventDefault()
            event.stopPropagation()
            setText(item.note ?? '')
            stop()
          }
        }}
        onBlur={() => void finish(true)}
      />
    )
  }
  return (
    <button
      ref={trigger}
      type="button"
      onClick={() => setEditing(true)}
      aria-label={item.note ? `修改备注：${item.note}` : `给 ${displayName(item)} 添加备注`}
      className={cx('block w-full min-w-0 rounded-sm text-left hover:text-fg', item.note ? 'whitespace-normal text-fg-2' : 'text-fg-3 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 coarse:opacity-100', focusRing)}
    >
      {item.note ?? '添加备注'}
    </button>
  )
}

export function WatchlistTable({ items, loading, today, ruleCounts, rulesState, readOnly, onMove, onRemove, onNote, empty }: WatchlistTableProps) {
  const price = (item: WatchlistItem) => {
    const quote = item.quote
    if (!hasPrice(quote)) return <span className="text-fg-3" title={quote?.quote.reason ?? '没有可用行情'}>{UNAVAILABLE}</span>
    const currency = quote.currency ?? currencyOf(item.ticker)
    return fmtTickerPrice(quote.price, item.ticker, currency, { symbol: currency !== 'CNY', exact: true })
  }
  const pct = (item: WatchlistItem) => {
    const value = hasPrice(item.quote) ? item.quote.change_pct : null
    return signed(fmtPct(value, { signed: true, exact: true }), value, item.quote?.quote.reason ?? undefined)
  }
  const amount = (item: WatchlistItem) => {
    const value = changeAmount(item.quote)
    return signed(fmtTickerPrice(value, item.ticker, item.quote?.currency ?? currencyOf(item.ticker), { signed: true, exact: true }), value)
  }
  const rules = (item: WatchlistItem) => ruleCountFor(ruleCounts, item)
  const rulesCell = (item: WatchlistItem) => {
    const count = rules(item)
    if (count === null) {
      return rulesState.loading
        ? <span className="text-fg-3" title="正在读取提醒规则">…</span>
        : <span className="text-fg-3" title={`提醒规则暂时无法读取${rulesState.error ? `：${rulesState.error}` : ''}`}>{UNAVAILABLE}</span>
    }
    return <span className={count ? 'text-fg' : 'text-fg-3'} title={count ? `${count} 条启用的提醒` : '没有启用的提醒'}>{count}</span>
  }
  const name = (item: WatchlistItem) => (
    <div className="min-w-0 py-1.5">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate font-medium text-fg">{displayName(item)}</span>
        {item.in_holdings && <Badge>持仓</Badge>}
      </div>
      <QuoteMeta ticker={item.ticker} quote={item.quote} today={today} />
    </div>
  )
  const position = (item: WatchlistItem) => items.findIndex((entry) => entry.ticker === item.ticker)

  const columns: Column<WatchlistItem>[] = [
    { key: 'name', header: '名称 / 代码', label: '名称', render: name },
    { key: 'price', header: '现价', numeric: true, render: price },
    { key: 'pct', header: '涨跌幅', numeric: true, render: pct },
    { key: 'amount', header: '涨跌额', numeric: true, hideBelow: 'md', render: amount },
    {
      key: 'note', header: '备注', wrap: true, hideBelow: 'md', width: '28%',
      render: (item) => <NoteCell item={item} readOnly={readOnly} onSave={(note) => onNote(item.ticker, note)} />,
    },
    {
      key: 'alerts', header: '提醒', numeric: true, hideBelow: 'sm',
      render: rulesCell,
    },
  ]

  const actions = (item: WatchlistItem) => {
    const index = position(item)
    const label = displayName(item)
    return (
      <>
        <IconButton icon={ArrowUp} size="sm" label={`上移 ${label}`} disabled={index <= 0 && !readOnly} disabledReason={readOnly} onClick={() => onMove(item.ticker, -1)} />
        <IconButton icon={ArrowDown} size="sm" label={`下移 ${label}`} disabled={index >= items.length - 1 && !readOnly} disabledReason={readOnly} onClick={() => onMove(item.ticker, 1)} />
        <Button size="sm" variant="ghost" icon={BellPlus} disabledReason={readOnly} onClick={() => openNewAlert({ ticker: item.ticker, name: label })}>设提醒</Button>
        <IconButton icon={X} size="sm" label={`移出 ${label}`} disabledReason={readOnly} tipAlign="end" onClick={() => onRemove(item)} />
      </>
    )
  }

  const card = (item: WatchlistItem) => (
    <div className="flex flex-col gap-1">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-body font-medium text-fg">{displayName(item)}</span>
            {item.in_holdings && <Badge>持仓</Badge>}
            {(rules(item) ?? 0) > 0 && <Badge>提醒 {rules(item)}</Badge>}
            {rules(item) === null && !rulesState.loading && <Badge title={rulesState.error ?? undefined}>提醒 {UNAVAILABLE}</Badge>}
          </div>
          <QuoteMeta ticker={item.ticker} quote={item.quote} today={today} />
        </div>
        <div className={cx('shrink-0 text-right text-body', numeric)}>
          <div>{price(item)}</div>
          <div className="text-table">{pct(item)} <span className="ml-1">{amount(item)}</span></div>
        </div>
      </div>
      {item.note && <p className="text-table text-fg-2">{item.note}</p>}
    </div>
  )

  return (
    <DataTable
      caption="自选"
      columns={columns}
      rows={items}
      rowKey={(item) => item.ticker}
      loading={loading}
      mobile="cards"
      card={card}
      onRowClick={(item) => openStockDrawer(item.ticker, displayName(item))}
      rowActions={actions}
      empty={empty}
    />
  )
}
