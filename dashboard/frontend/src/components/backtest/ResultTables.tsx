// The trades of a result: the completed round trips with their exit reason, what was still held at the
// end (valued at the last close, never shown as a sale), every fill with the fees charged on its date,
// and the orders that did not fill with the reason each was not filled.
import { Badge, DataTable, EmptyState, LongValue, cx, numeric, type Column } from '@/components/ui'
import { UNAVAILABLE, fmtQty, type Shown } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import {
  directionText, exitReason, feeFigure, fillKind, gainTone, moneyFigure, pctFigure, priceFigure, rowBlocked, splitRoundTrips,
  unfilledOutcome, unfilledReason,
} from '@/features/backtest/result'
import type { EndMark, Facts, Fill, RoundTrip, Unfilled } from '@/features/backtest/types'

type Keyed<T> = T & { key: string }

/** Rows with their position as the key: two fills can match in every field. */
const keyed = <T,>(rows: T[]): Keyed<T>[] => rows.map((row, index) => ({ ...row, key: String(index) }))
const byKey = (row: { key: string }) => row.key

/** A gain or loss figure, red up / green down; past 8 places rounded with the full value on hover (lib/format.ts `fmtLong`). */
function Signed({ value, tone }: { value: Shown; tone: 'up' | 'down' | 'flat' }) {
  return <LongValue value={value} className={cx(numeric, value.text === UNAVAILABLE ? 'text-fg-3' : pnlTextClass(tone))} />
}

function Heading({ title, count, note }: { title: string; count: number; note?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2">
      <h3 className="text-title text-fg">{title}</h3>
      <span className={cx('text-table text-fg-3', numeric)}>{count} 条</span>
      {note && <span className="text-table text-fg-3">{note}</span>}
    </div>
  )
}

/** A part a record saved by an older version does not have: its heading and why it is 不可用. */
function Missing({ title, text }: { title: string; text: string }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-2">
      <h3 className="text-title text-fg">{title}</h3>
      <p className="text-table text-fg-3">{text}</p>
    </section>
  )
}

export function RoundTripsTable({ trips, facts, endMark, unavailable }: { trips: RoundTrip[]; facts: Facts; endMark: EndMark | null; unavailable?: string | null }) {
  if (unavailable) return <Missing title="完整交易" text={unavailable} />
  const { closed, held } = splitRoundTrips(trips)
  const columns: Column<Keyed<RoundTrip>>[] = [
    { key: 'entry', header: '买入日', render: (row) => <span className={numeric}>{row.entry_date}</span>, sortValue: (row) => row.entry_date },
    { key: 'exit', header: '卖出日', render: (row) => <span className={numeric}>{row.exit_date}</span> },
    { key: 'qty', header: '股数', numeric: true, render: (row) => fmtQty(row.quantity) },
    { key: 'entry_price', header: '买入均价', numeric: true, hideBelow: 'md', render: (row) => <LongValue value={priceFigure(row.entry_price, facts)} /> },
    { key: 'exit_price', header: '卖出均价', numeric: true, hideBelow: 'md', render: (row) => <LongValue value={priceFigure(row.exit_price, facts)} /> },
    { key: 'pnl', header: '盈亏', numeric: true, render: (row) => <Signed value={moneyFigure(row.pnl, facts.currency, { signed: true })} tone={gainTone(row.pnl)} /> },
    { key: 'ret', header: '收益率', numeric: true, render: (row) => <Signed value={pctFigure(row.return_pct)} tone={gainTone(row.return_pct)} /> },
    { key: 'days', header: '持有', numeric: true, hideBelow: 'sm', render: (row) => (row.holding_days === null ? UNAVAILABLE : `${fmtQty(row.holding_days)} 天`) },
    { key: 'reason', header: '离场原因', wrap: true, render: (row) => exitReason(row) },
  ]
  return (
    <>
      <section aria-label="完整交易" className="flex min-w-0 flex-col gap-2">
        <Heading title="完整交易" count={closed.length} note="从空仓买入到全部卖出算一笔" />
        <DataTable
          caption="完整交易"
          columns={columns}
          rows={keyed(closed)}
          rowKey={byKey}
          mobile="cards"
          density="compact"
          maxHeight={420}
          empty={<EmptyState title="没有完整交易" description={held.length ? '买入后一直持有到数据结束，没有卖出；见下面的期末仍持有。' : '这段时间里这个方法没有买入后卖出。'} />}
        />
      </section>
      {held.length > 0 && <HeldAtEndTable trips={held} facts={facts} endMark={endMark} />}
    </>
  )
}

/** What was still held after the last bar: valued at the last close, not sold (and maybe not sellable). */
function HeldAtEndTable({ trips, facts, endMark }: { trips: RoundTrip[]; facts: Facts; endMark: EndMark | null }) {
  const columns: Column<Keyed<RoundTrip>>[] = [
    { key: 'entry', header: '买入日', render: (row) => <span className={numeric}>{row.entry_date}</span> },
    { key: 'mark', header: '估值日', render: (row) => <span className={numeric}>{row.exit_date}</span> },
    { key: 'qty', header: '股数', numeric: true, render: (row) => fmtQty(row.quantity) },
    { key: 'entry_price', header: '买入均价', numeric: true, hideBelow: 'md', render: (row) => <LongValue value={priceFigure(row.entry_price, facts)} /> },
    { key: 'mark_price', header: '最后收盘价', numeric: true, hideBelow: 'md', render: (row) => <LongValue value={priceFigure(row.exit_price, facts)} /> },
    { key: 'pnl', header: '浮动盈亏', numeric: true, render: (row) => <Signed value={moneyFigure(row.pnl, facts.currency, { signed: true })} tone={gainTone(row.pnl)} /> },
    { key: 'ret', header: '浮动收益率', numeric: true, render: (row) => <Signed value={pctFigure(row.return_pct)} tone={gainTone(row.return_pct)} /> },
    { key: 'days', header: '持有', numeric: true, hideBelow: 'sm', render: (row) => (row.holding_days === null ? UNAVAILABLE : `${fmtQty(row.holding_days)} 天`) },
    {
      key: 'state', header: '状态', wrap: true,
      render: (row) => {
        const blocked = rowBlocked(row, endMark)
        return (
          <span className="inline-flex flex-wrap items-center gap-1">
            <Badge tone="warn" title="期末仍持有，按最后收盘价估值，没有真的卖出">期末估值（未卖出）</Badge>
            {blocked && <span className="whitespace-normal text-table text-warn">{blocked}</span>}
          </span>
        )
      },
    },
  ]
  return (
    <section aria-label="期末仍持有" className="flex min-w-0 flex-col gap-2">
      <Heading title="期末仍持有" count={trips.length} note="数据结束时还没卖，按最后收盘价估值，不算完整交易" />
      <DataTable caption="期末仍持有" columns={columns} rows={keyed(trips)} rowKey={byKey} mobile="cards" density="compact" />
    </section>
  )
}

export function FillsTable({ fills, facts, endMark, detailUnavailable }: { fills: Fill[]; facts: Facts; endMark: EndMark | null; detailUnavailable?: string | null }) {
  const columns: Column<Keyed<Fill>>[] = [
    { key: 'date', header: '日期', render: (row) => <span className={numeric}>{row.date}</span> },
    {
      key: 'dir', header: '方向', wrap: true,
      render: (row) => {
        const kind = fillKind(row, endMark)
        if (!kind.mark) return kind.text
        return (
          <span className="inline-flex flex-wrap items-center gap-1">
            <Badge tone="warn">{kind.text}</Badge>
            {kind.blocked && <span className="whitespace-normal text-table text-warn">{kind.blocked}</span>}
          </span>
        )
      },
    },
    { key: 'qty', header: '股数', numeric: true, render: (row) => fmtQty(row.quantity) },
    {
      key: 'price', header: '成交价', numeric: true,
      render: (row) => (row.end_mark || row.reason === 'end_mark' ? (
        <span title="按最后收盘价估值，不是成交价"><LongValue value={priceFigure(row.price, facts)} /><span className="sr-only">（按最后收盘价估值，不是成交价）</span><span aria-hidden className="ml-1 text-fg-3">估值</span></span>
      ) : <LongValue value={priceFigure(row.price, facts)} />),
    },
    { key: 'fees', header: '费用（按成交日费率）', wrap: true, render: (row) => <LongValue value={feeFigure(row, facts.currency)} className={cx('text-table', numeric)} /> },
    {
      key: 'pnl', header: '盈亏', numeric: true, hideBelow: 'sm',
      render: (row) => (row.direction === 'sell' ? <Signed value={moneyFigure(row.pnl, facts.currency, { signed: true })} tone={gainTone(row.pnl)} /> : ''),
    },
    { key: 'reason', header: '原因', hideBelow: 'md', render: (row) => (row.end_mark || row.reason === 'end_mark' ? '按最后收盘价估值' : row.reason_zh ?? '') },
  ]
  return (
    <section aria-label="逐笔成交" className="flex min-w-0 flex-col gap-2">
      <Heading title="逐笔成交" count={fills.length} note={detailUnavailable ? undefined : '次日开盘价加减滑点成交'} />
      {detailUnavailable && <p className="text-table text-fg-3">{detailUnavailable}；费用一栏只有当时记下的佣金。</p>}
      <DataTable
        caption="逐笔成交"
        columns={columns}
        rows={keyed(fills)}
        rowKey={byKey}
        mobile="cards"
        density="compact"
        maxHeight={420}
        empty={<EmptyState title="没有成交" description="这个方法在这段时间里没有发出能成交的买卖。" />}
      />
    </section>
  )
}

export function UnfilledTable({ entries, unavailable }: { entries: Unfilled[]; unavailable?: string | null }) {
  if (unavailable) return <Missing title="没有成交的委托" text={unavailable} />
  const columns: Column<Keyed<Unfilled>>[] = [
    { key: 'signal', header: '信号日', render: (row) => <span className={numeric}>{row.signal_date ?? row.date ?? UNAVAILABLE}</span> },
    { key: 'dir', header: '方向', render: (row) => directionText(row.direction) },
    { key: 'qty', header: '股数', numeric: true, render: (row) => (typeof row.quantity === 'number' ? fmtQty(row.quantity) : UNAVAILABLE) },
    { key: 'reason', header: '没有成交的原因', wrap: true, render: (row) => <span className="text-warn">{unfilledReason(row)}</span> },
    { key: 'outcome', header: '结果', render: (row) => unfilledOutcome(row) },
  ]
  return (
    <section aria-label="没有成交的委托" className="flex min-w-0 flex-col gap-2">
      <Heading title="没有成交的委托" count={entries.length} />
      <DataTable
        caption="没有成交的委托"
        columns={columns}
        rows={keyed(entries)}
        rowKey={byKey}
        mobile="cards"
        density="compact"
        maxHeight={320}
        empty={<EmptyState title="每个信号都成交了" />}
      />
    </section>
  )
}
