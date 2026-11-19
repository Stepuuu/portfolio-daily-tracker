// 持仓与成本: what each account holds by the confirmed ledger, at weighted average cost (buy fees included),
// with each account's realized profit (also for an account with nothing left open). Not market value: live
// prices are on 今日. Every figure keeps the ledger's digits up to 8 places (an average cost that repeats is
// ≈ rounded to 8, the full value on hover); sorting compares the exact decimals.
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Badge, Button, DataTable, EmptyState, LongValue, buttonClass, cx, numeric, type Column } from '@/components/ui'
import { fmtLong, fmtMoney, fmtPrice, fmtQty, toneOf, type Shown } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { instrumentKind } from '@/features/today/rows'
import { decMul, decRanks } from '@/features/ledger/decimal'
import { closedAccounts } from '@/features/ledger/events'
import type { LedgerAccount, LedgerPosition } from '@/features/ledger/types'
import { useShell } from '@/components/shell/shellStore'
import { holdingLink } from '@/features/backtest/model'

type Row = LedgerPosition & { account: string; basis: string | null }

/** Money in a column that mixes currencies: every currency named by its symbol, CNY included. */
function money(value: string | null, currency: string, signed = false): Shown {
  return fmtLong(value, (v) => fmtMoney(v, currency, { exact: true, signed }))
}

function columns(rows: Row[]): Column<Row>[] {
  const index = new Map(rows.map((row, position) => [row, position]))
  const quantityRank = decRanks(rows.map((row) => row.quantity))
  const basisRank = decRanks(rows.map((row) => row.basis))
  const rank = (ranks: (number | null)[]) => (row: Row) => ranks[index.get(row) ?? -1] ?? null
  return [
    {
      key: 'name', header: '标的', sortValue: (row) => row.name,
      render: (row) => (
        <span className="whitespace-nowrap">
          {row.name !== row.ticker && <span className="mr-1.5 text-fg">{row.name}</span>}
          <span className={row.name !== row.ticker ? 'text-fg-3' : 'text-fg'}>{row.ticker}</span>
        </span>
      ),
    },
    { key: 'quantity', header: '数量', numeric: true, render: (row) => <LongValue value={fmtLong(row.quantity, fmtQty)} />, sortValue: rank(quantityRank) },
    {
      key: 'cost', header: '加权成本', numeric: true,
      render: (row) => <LongValue value={fmtLong(row.cost_price, (v) => fmtPrice(v, row.currency, instrumentKind(row.ticker), { symbol: true, exact: true }))} />,
    },
    { key: 'basis', header: '成本金额', numeric: true, render: (row) => <LongValue value={money(row.basis, row.currency)} />, sortValue: rank(basisRank) },
    { key: 'currency', header: '币种', hideBelow: 'sm', render: (row) => <span className="text-fg-2">{row.currency}</span> },
  ]
}

function RealizedList({ entries }: { entries: [string, string][] }) {
  return (
    <>
      {entries.map(([unit, value], index) => (
        <span key={unit} className={pnlTextClass(toneOf(value))}>
          {index > 0 && ' · '}<LongValue value={money(value, unit, true)} />
        </span>
      ))}
    </>
  )
}

function Realized({ account }: { account: LedgerAccount }) {
  const entries = Object.entries(account.realized).filter(([, value]) => toneOf(value) !== 'flat')
  if (!entries.length) return null
  return (
    <span className={cx('ml-3 font-normal text-fg-2', numeric)}>
      已实现盈亏 <RealizedList entries={entries} />
    </span>
  )
}

export interface PositionsTableProps {
  accounts: Record<string, LedgerAccount>
  loading?: boolean
  /** Why 买 / 卖 are off (demo, or the app mode is not known). */
  writeBlocked?: string
}

export function PositionsTable({ accounts, loading = false, writeBlocked }: PositionsTableProps) {
  const openEntry = useShell((state) => state.openEntry)
  const rows: Row[] = useMemo(() => Object.entries(accounts).flatMap(([account, state]) =>
    Object.values(state.positions).map((position) => ({ ...position, account, basis: decMul(position.quantity, position.cost_price) }))), [accounts])
  const table = useMemo(() => columns(rows), [rows])
  const closed = closedAccounts(accounts)
  return (
    <div className="flex flex-col">
      <DataTable
        caption="持仓与成本"
        columns={table}
        rows={rows}
        rowKey={(row) => `${row.account}:${row.ticker}`}
        loading={loading}
        mobile="cards"
        groupBy={{
          key: (row) => row.account,
          header: (account, group) => (
            <span>
              {account}
              <Badge className="ml-2">{group.length} 项</Badge>
              {accounts[account] && <Realized account={accounts[account]} />}
            </span>
          ),
        }}
        card={(row) => (
          // One figure per line: an average cost past 8 places (≈ and 8 places) does not fit half a phone card.
          <div className="flex flex-col gap-1">
            <span className="min-w-0 break-words text-body font-medium">
              {row.name !== row.ticker && <span className="mr-1.5 text-fg">{row.name}</span>}
              <span className={row.name !== row.ticker ? 'font-normal text-fg-3' : 'text-fg'}>{row.ticker}</span>
            </span>
            <dl className={cx('flex flex-col gap-0.5 text-table', numeric)}>
              {table.filter((column) => column.key !== 'name').map((column) => (
                <div key={column.key} className="flex min-w-0 items-baseline justify-between gap-3">
                  <dt className="shrink-0 text-caption text-fg-3">{column.header}</dt>
                  <dd className="min-w-0 break-all text-right">{column.render(row)}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        rowActions={(row) => {
          const draft = { ticker: row.ticker, name: row.name !== row.ticker ? row.name : undefined, account: row.account }
          return (
            <>
              <Button size="sm" variant="ghost" disabledReason={writeBlocked} onClick={() => openEntry({ ...draft, kind: 'buy' })}>买</Button>
              <Button size="sm" variant="ghost" disabledReason={writeBlocked} tipAlign="end" onClick={() => openEntry({ ...draft, kind: 'sell' })}>卖</Button>
              <Link to={holdingLink(row.ticker, row.basis)} aria-label={`回测这只持仓：${row.name}`} className={buttonClass({ variant: 'ghost', size: 'sm' })}>回测</Link>
            </>
          )
        }}
        empty={<EmptyState title="没有持仓" description={closed.length ? '账户都已清仓；已实现盈亏见下方。' : '买入并确认后，持仓和加权成本会出现在这里。'} />}
      />
      {closed.length > 0 && (
        <ul aria-label="已清仓账户的已实现盈亏" className="divide-y divide-line border-t border-line">
          {closed.map(([account, realized]) => (
            <li key={account} className={cx('flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2 text-table text-fg-2', numeric)}>
              <span className="min-w-0 break-words font-medium text-fg">{account}</span>
              <Badge>无持仓</Badge>
              <span>已实现盈亏 <RealizedList entries={realized} /></span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
