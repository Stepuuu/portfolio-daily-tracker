// 项目 / 变更前 / 变更后 for each account a preview touches. A value that did not exist (a new account, a
// new position, a position sold out) reads 无, never 0. An average cost with more than 8 places is ≈ rounded
// to 8, its full value on hover and for screen readers.
import { Badge, DataTable, LongValue, cx, type Column } from '@/components/ui'
import { changeFigure, changeRows, type ChangeRow } from '@/features/ledger/review'
import type { LedgerState } from '@/features/ledger/types'

type Row = ChangeRow & { account: string; created: boolean }

function Value({ row, side }: { row: Row; side: 'before' | 'after' }) {
  return <LongValue value={changeFigure(row, side)} className={cx(row[side] === null ? 'text-fg-3' : side === 'after' ? 'text-fg' : 'text-fg-2')} />
}

const COLUMNS: Column<Row>[] = [
  {
    key: 'item', header: '项目', wrap: true,
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="text-fg">{row.label}</span>
        {row.detail && <span className="text-caption text-fg-3">{row.detail}</span>}
      </span>
    ),
  },
  { key: 'before', header: '变更前', numeric: true, render: (row) => <Value row={row} side="before" /> },
  { key: 'after', header: '变更后', numeric: true, render: (row) => <Value row={row} side="after" /> },
]

export function ChangesTable({ before, after, className }: { before: LedgerState; after: LedgerState; className?: string }) {
  const groups = changeRows({ before, after })
  const rows: Row[] = groups.flatMap((group) => group.rows.map((row) => ({ ...row, account: group.account, created: group.created })))
  if (!groups.length) return <p className="text-table text-fg-3">账户余额和持仓没有变化。</p>
  const created = new Set(groups.filter((group) => group.created).map((group) => group.account))
  return (
    <div className={cx('min-w-0 overflow-hidden rounded-md border border-line', className)}>
      <DataTable
        caption="入账前后的变化"
        columns={COLUMNS}
        rows={rows}
        rowKey={(row) => `${row.account}:${row.key}`}
        stickyHeader={false}
        density="compact"
        groupBy={{
          key: (row) => row.account,
          header: (account) => (
            <span className="inline-flex items-center gap-2">
              {account}
              {created.has(account) && <Badge tone="accent">新账户</Badge>}
            </span>
          ),
        }}
      />
    </div>
  )
}
