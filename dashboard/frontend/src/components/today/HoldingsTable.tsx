// Holdings by account at live prices. Cells are in the instrument's own currency (HK$ / $ shown), group
// rows in CNY from the backend; nothing is added up here. A missing price makes every figure that needs
// it say 不可用, never 0. Positions sold out today stay listed (their day profit is in the totals).
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Link } from 'react-router-dom'
import { MoreHorizontal } from 'lucide-react'
import {
  Badge, Button, DataTable, IconButton, LongValue, Panel, Popover, Select, buttonClass, cx, focusRing, numeric, type Column,
} from '@/components/ui'
import { UNAVAILABLE, fmtMoney, fmtPct, fmtPrice, fmtQty } from '@/lib/format'
import {
  accountNames, accountSummaries, accountWeightsEstimated, costFigure, holdingRows, instrumentKind, priceMoves, priceSnapshot,
  totalsEstimateReason, weightEstimate, weightText, type HoldingRow, type Move,
} from '@/features/today/rows'
import { snapshotDate } from '@/features/today/session'
import type { TodayPayload } from '@/services/today'
import { assistantPrefillFor, useShell } from '@/components/shell/shellStore'
import { useAppMode } from '@/components/shell/useAppMode'
import { openNewAlert } from '@/components/alerts/alertDialogStore'
import { openStockDrawer } from '@/components/market/stockDrawerStore'
import { holdingLink } from '@/features/backtest/model'
import { decMul } from '@/features/ledger/decimal'
import { QuoteLine } from './QuoteLine'
import { Unavailable, money, signed } from './cells'

export interface HoldingsTableProps {
  data: TodayPayload | undefined
  /**
   * Chips after a holding's quote line, e.g. `[计划 15%] [止损 1,520]`. Later packages (plans, alerts)
   * contribute through components/today/rowExtras.tsx; return null for nothing.
   */
  renderRowExtra?: (ticker: string, account?: string) => ReactNode
}

const ALL = '__all__'
const position = (row: HoldingRow) => row.position
const isHolding = (row: HoldingRow) => row.kind === 'position' || row.kind === 'closed'
// exact-ok: a sort key only; the cells show the backend's strings.
const sortNumber = (value: string | null | undefined) => (value === null || value === undefined ? null : Number(value))

/** The element's width, kept up to date (the table drops columns when the assistant dock narrows it). */
function useWidth<E extends HTMLElement>(): [RefObject<E>, number | null] {
  const ref = useRef<E>(null)
  const [width, setWidth] = useState<number | null>(null)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') return
    // exact-ok: the element's width in pixels, not a figure.
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

/** Columns left out when the table is narrower than these widths (dock open, small laptop). */
const NARROW: [number, string[]][] = [[1000, ['cost', 'pnlPct']], [820, ['qty', 'weight']]]

/** Keeps the last prices and returns which rows moved in the latest data, with a key to restart the flash. */
function useFlash(rows: HoldingRow[]): { moves: Map<string, Move>; round: number } {
  const previous = useRef<Map<string, string>>(new Map())
  const round = useRef(0)
  const moves = useMemo(() => {
    round.current += 1
    return priceMoves(previous.current, rows)
  }, [rows])
  useEffect(() => {
    previous.current = priceSnapshot(rows)
  }, [rows])
  return { moves, round: round.current }
}

function MenuItem({ children, onClick, hint }: { children: ReactNode; onClick?: () => void; hint?: string }) {
  return (
    <button
      type="button"
      aria-disabled={hint ? true : undefined}
      onClick={hint ? undefined : onClick}
      className={cx(
        'flex h-8 w-full items-center justify-between gap-4 rounded-md px-2 text-left text-body focus-visible:bg-surface-2 focus-visible:outline-none',
        hint ? 'cursor-not-allowed text-fg-3' : 'text-fg hover:bg-surface-2',
      )}
    >
      {children}
      {hint && <span className="text-caption">{hint}</span>}
    </button>
  )
}

/** A holding's name; it opens the stock drawer (quote, chart, alerts). */
function StockLink({ ticker, name, className }: { ticker: string; name: string; className?: string }) {
  return (
    <button
      type="button"
      onClick={() => openStockDrawer(ticker, name)}
      className={cx('truncate rounded-sm text-left font-medium text-fg hover:underline', focusRing, className)}
    >
      {name}
    </button>
  )
}

function RowActions({ row, readOnlyReason }: { row: HoldingRow; readOnlyReason: string | undefined }) {
  const openEntry = useShell((state) => state.openEntry)
  const openAssistant = useShell((state) => state.openAssistant)
  const draft = { ticker: row.ticker ?? undefined, name: row.name, account: row.account, currency: row.currency }
  const reason = readOnlyReason
  return (
    <>
      <Button size="sm" variant="ghost" disabledReason={reason} onClick={() => openEntry({ ...draft, kind: 'buy' })}>买</Button>
      {row.kind === 'position' && (
        <Button size="sm" variant="ghost" disabledReason={reason} tipAlign="end" onClick={() => openEntry({ ...draft, kind: 'sell' })}>卖</Button>
      )}
      {row.kind === 'position' && row.ticker && (
        <Link to={holdingLink(row.ticker, decMul(row.position?.quantity, row.position?.cost_price))} aria-label={`回测这只持仓：${row.name}`} className={buttonClass({ variant: 'ghost', size: 'sm' })}>回测</Link>
      )}
      <Popover
        align="end"
        label={`${row.name} 更多操作`}
        className="w-44 p-1"
        trigger={<IconButton icon={MoreHorizontal} label="更多操作" size="sm" tipAlign="end" />}
      >
        {(close) => (
          <div className="flex flex-col">
            <MenuItem
              hint={readOnlyReason}
              onClick={() => {
                close()
                openNewAlert({ ticker: row.ticker ?? undefined, name: row.name, account: row.account })
              }}
            >
              设提醒
            </MenuItem>
            <MenuItem
              hint={readOnlyReason}
              onClick={() => {
                close()
                openAssistant(assistantPrefillFor(row.ticker))
              }}
            >
              问助手
            </MenuItem>
          </div>
        )}
      </Popover>
    </>
  )
}

export function HoldingsTable({ data, renderRowExtra }: HoldingsTableProps) {
  const { readOnlyReason } = useAppMode()
  const [account, setAccount] = useState(ALL)
  const rows = useMemo(() => holdingRows(data), [data])
  const summaries = useMemo(() => accountSummaries(data), [data])
  // Weights are of the whole portfolio: an estimated total makes every one an estimate.
  const totalsQuality = data?.totals.quality
  const totalsReason = totalsEstimateReason(data)
  const names = accountNames(data)
  const today = snapshotDate(data)
  const { moves, round } = useFlash(rows)
  const [box, width] = useWidth<HTMLDivElement>()
  const dropped = new Set(NARROW.flatMap(([limit, keys]) => (width !== null && width < limit ? keys : [])))
  const shown = account === ALL || !names.includes(account) ? rows : rows.filter((row) => row.account === account)

  const flash = (row: HoldingRow, text: ReactNode) => {
    const move = moves.get(row.key)
    if (!move) return text
    return (
      <span key={`${row.key}:${round}`} className={cx('-mx-1 rounded-sm px-1 motion-reduce:animate-none', move === 'up' ? 'animate-flash-up' : 'animate-flash-down')}>
        {text}
      </span>
    )
  }

  const allColumns: Column<HoldingRow>[] = [
    {
      key: 'name', header: '名称 / 代码', label: '名称',
      render: (row) => {
        const quote = position(row)
        return (
          <div className="min-w-0 py-1.5">
            <div className="flex min-w-0 items-center gap-2">
              {quote && row.ticker ? (
                <StockLink ticker={row.ticker} name={row.name} />
              ) : (
                <span className={cx('truncate', row.kind === 'cash' || row.kind === 'fund' ? 'text-fg-2' : 'font-medium text-fg')}>{row.name}</span>
              )}
              {row.kind === 'closed' && <Badge>今日清仓</Badge>}
            </div>
            {quote && <QuoteLine row={quote} today={today} extra={renderRowExtra && row.ticker ? renderRowExtra(row.ticker, row.account) : null} />}
          </div>
        )
      },
      sortValue: (row) => (isHolding(row) ? row.name : null),
    },
    {
      key: 'price', header: '现价', numeric: true,
      render: (row) => {
        const quote = position(row)
        if (!quote) return ''
        if (quote.price === null) return <Unavailable title={quote.quote.reason ?? undefined} />
        return flash(row, fmtPrice(quote.price, row.currency, instrumentKind(row.ticker), { symbol: row.currency !== 'CNY', exact: true }))
      },
    },
    {
      key: 'change', header: '涨跌幅', numeric: true,
      render: (row) => (position(row) ? signed(fmtPct(position(row)!.change_pct, { signed: true, exact: true }), position(row)!.change_pct) : ''),
      sortValue: (row) => sortNumber(position(row)?.change_pct),
    },
    {
      key: 'day', header: '今日盈亏', numeric: true,
      render: (row) => {
        const quote = position(row)
        if (!quote) return ''
        return signed(money(quote.day_pnl, row.currency, true), quote.day_pnl)
      },
      sortValue: (row) => sortNumber(position(row)?.day_pnl_cny),
    },
    {
      key: 'qty', header: '持仓', numeric: true, hideBelow: 'md',
      render: (row) => {
        const quote = position(row)
        if (!quote) return ''
        return quote.quantity === null || quote.quantity === undefined ? <Unavailable /> : fmtQty(quote.quantity)
      },
    },
    {
      key: 'cost', header: '成本', numeric: true, hideBelow: 'lg',
      render: (row) => {
        const cost = costFigure(row)
        if (cost === null) return ''
        return cost.text === UNAVAILABLE ? <Unavailable title="账本中没有这项持仓的成本" /> : <LongValue value={cost} />
      },
    },
    {
      key: 'value', header: '市值', numeric: true,
      render: (row) => {
        if (row.kind === 'cash' || row.kind === 'fund') return money(row.amount, row.currency)
        if (row.kind === 'closed') return ''
        return money(position(row)!.market_value, row.currency)
      },
      sortValue: (row) => (row.kind === 'position' ? sortNumber(position(row)!.market_value_cny) : null),
    },
    {
      key: 'pnl', header: '持仓盈亏', numeric: true, hideBelow: 'sm',
      render: (row) => (row.kind === 'position' ? signed(money(position(row)!.total_pnl, row.currency, true), position(row)!.total_pnl) : ''),
      sortValue: (row) => (row.kind === 'position' ? sortNumber(position(row)!.total_pnl_cny) : null),
    },
    {
      key: 'pnlPct', header: '收益率', numeric: true, hideBelow: 'lg',
      render: (row) => (row.kind === 'position' ? signed(fmtPct(position(row)!.total_pnl_pct, { signed: true, exact: true }), position(row)!.total_pnl_pct) : ''),
      sortValue: (row) => (row.kind === 'position' ? sortNumber(position(row)!.total_pnl_pct) : null),
    },
    {
      key: 'weight', header: '占比', numeric: true, hideBelow: 'md',
      render: (row) => {
        if (row.kind !== 'position') return ''
        const held = position(row)!
        if (held.weight_pct === null) return <Unavailable />
        const mark = weightEstimate(held, totalsQuality, totalsReason)
        return mark
          ? <span title={mark}>{weightText(held)}<span className="ml-1 text-caption text-warn">估算</span></span>
          : weightText(held)
      },
      sortValue: (row) => (row.kind === 'position' ? sortNumber(position(row)!.weight_pct) : null),
    },
  ]

  const columns = allColumns.filter((column) => !dropped.has(column.key))

  const card = (row: HoldingRow) => {
    const quote = position(row)
    if (!quote) {
      return (
        <div className="flex items-baseline justify-between gap-3 text-body">
          <span className="text-fg-2">{row.name}</span>
          <span className={numeric}>{money(row.amount, row.currency)}</span>
        </div>
      )
    }
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              {row.ticker ? <StockLink ticker={row.ticker} name={row.name} className="text-body" /> : <span className="truncate text-body font-medium text-fg">{row.name}</span>}
              {row.kind === 'closed' && <Badge>今日清仓</Badge>}
            </div>
            <QuoteLine row={quote} today={today} extra={renderRowExtra && row.ticker ? renderRowExtra(row.ticker, row.account) : null} />
          </div>
          <div className={cx('shrink-0 text-right text-body', numeric)}>
            {row.kind === 'closed' ? <span className="text-fg-3">已清仓</span> : money(quote.market_value, row.currency)}
          </div>
        </div>
        <dl className={cx('grid grid-cols-3 gap-x-3 text-table', numeric)}>
          <div><dt className="text-caption text-fg-3">现价</dt><dd>{quote.price === null ? <Unavailable /> : flash(row, fmtPrice(quote.price, row.currency, instrumentKind(row.ticker), { symbol: row.currency !== 'CNY', exact: true }))}</dd></div>
          <div><dt className="text-caption text-fg-3">涨跌幅</dt><dd>{signed(fmtPct(quote.change_pct, { signed: true, exact: true }), quote.change_pct)}</dd></div>
          <div className="text-right"><dt className="text-caption text-fg-3">今日盈亏</dt><dd>{signed(money(quote.day_pnl, row.currency, true), quote.day_pnl)}</dd></div>
        </dl>
      </div>
    )
  }

  return (
    <div ref={box} className="min-w-0">
      <Panel
        padding="none"
        title="持仓"
        actions={names.length > 1 ? (
          <Select
            size="sm"
            aria-label="显示账户"
            className="w-36"
            value={account}
            onChange={(event) => setAccount(event.target.value)}
            options={[{ value: ALL, label: '全部账户' }, ...names.map((name) => ({ value: name, label: name }))]}
          />
        ) : undefined}
      >
        <DataTable
          caption="持仓"
          columns={columns}
          rows={shown}
          rowKey={(row) => row.key}
          loading={!data}
          mobile="cards"
          card={card}
          rowClassName={(row) => (row.kind === 'closed' ? 'text-fg-2' : undefined)}
          rowActions={(row) => (isHolding(row) && row.ticker ? <RowActions row={row} readOnlyReason={readOnlyReason} /> : null)}
          groupBy={{
            key: (row) => row.account,
            header: (key) => {
              const summary = summaries.get(key)
              return (
                <span>
                  {key}
                  <span className="ml-2 font-normal text-fg-3">
                    {summary?.holdings ?? 0} 项持仓
                    {summary?.closedToday ? ` · 今日清仓 ${summary.closedToday}` : ''}
                    {summary?.missing ? ` · ${summary.missing}` : ''}
                  </span>
                  {summary?.quality === 'estimated' && <span className="ml-2 text-caption font-normal text-warn" title="部分行情或汇率未经核实，账户合计为估算">估算</span>}
                  {summary && accountWeightsEstimated(summary, totalsQuality) && (
                    <span className="ml-2 text-caption font-normal text-warn" title={`账户合计已核实；占比按组合合计计算，组合合计为估算（${totalsReason ?? '组合中有未经核实的价格或汇率'}）`}>占比估算</span>
                  )}
                </span>
              )
            },
            summary: (key) => {
              const summary = summaries.get(key)
              if (!summary) return {}
              return {
                day: signed(fmtMoney(summary.dayPnlCny, 'CNY', { signed: true, exact: true }), summary.dayPnlCny),
                value: summary.totalCny === null ? <Unavailable /> : fmtMoney(summary.totalCny, 'CNY', { exact: true }),
                pnl: signed(fmtMoney(summary.totalPnlCny, 'CNY', { signed: true, exact: true }), summary.totalPnlCny),
              }
            },
          }}
          footer="港股、美股按原币显示；账户行为人民币合计（含现金和基金），按当前汇率折算。今日盈亏不含分红和单独记录的费用。"
        />
      </Panel>
    </div>
  )
}
