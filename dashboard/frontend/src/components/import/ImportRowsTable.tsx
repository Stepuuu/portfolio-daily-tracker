// The preview table (plan: ImportRowsTable, EN-3): filters by status with the backend's counts, one page of
// rows from the backend (thousands of rows are never rendered at once), each row as it will be booked, with
// the backend's reason for every row that is not plainly new, include / exclude for bookable rows, and
// 处理 / 跳过 / 撤销修改 for the rest (a 需处理 row the page cannot fix offers only 跳过, with the backend's
// sentence saying so). A figure with more than 8 places is ≈ rounded to 8, its full value on hover; a price or
// fee the backend worked out says how under it.
import { ChevronLeft, ChevronRight, Pencil, SkipForward, Undo2 } from 'lucide-react'
import { Badge, Button, Checkbox, DataTable, EmptyState, LongValue, Tabs, cx, numeric, type Column } from '@/components/ui'
import { longLine } from '@/lib/format'
import {
  PAGE_SIZE, neededColumns, pageCount, pageLabel, rowActions, rowExplanation, rowLines, statusLabel, statusTabs, statusTone,
  type FigureField, type RowLine,
} from '@/features/import/rows'
import type { ImportRow, ImportSession } from '@/features/import/types'

export interface ImportRowsTableProps {
  session: ImportSession
  rows: ImportRow[]
  /** Rows in the current filter. */
  total: number
  offset: number
  status: string | null
  loading: boolean
  onFilter: (status: string | null) => void
  onPage: (page: number) => void
  onInclude: (row: ImportRow, include: boolean) => void
  onEdit: (row: ImportRow) => void
  onSkip: (row: ImportRow) => void
  onReset: (row: ImportRow) => void
  busy: boolean
  disabledReason?: string
}

const PENDING_TEXT = '待填写'

const NOTE: Partial<Record<FigureField, 'priceNote' | 'feeNote'>> = { price: 'priceNote', fee: 'feeNote' }

/** One cell of every line; a column the backend needs filled in says 待填写 in amber. */
function Cells({ row, lines, field, numericCell = false }: { row: ImportRow; lines: RowLine[]; field: 'date' | 'kind' | FigureField; numericCell?: boolean }) {
  const needed = neededColumns(row)
  return (
    <div className={cx('flex flex-col', numericCell && numeric)}>
      {lines.map((line) => {
        const value = line[field]
        const text = typeof value === 'string' ? value : value.text
        if (!text && needed.has(field)) return <span key={line.key} className="text-warn">{PENDING_TEXT}</span>
        const note = NOTE[field as FigureField] ? line[NOTE[field as FigureField]!] : null
        return (
          <span key={line.key} className={cx('flex flex-col', needed.has(field) && 'text-warn')}>
            <LongValue value={value} />
            {note && <span className="text-caption text-fg-3">{note}</span>}
          </span>
        )
      })}
    </div>
  )
}

function Subject({ row, lines }: { row: ImportRow; lines: RowLine[] }) {
  const needed = neededColumns(row).has('code')
  return (
    <div className="flex flex-col">
      {lines.map((line) => (
        <span key={line.key} className="whitespace-nowrap">
          {line.name && <span className="mr-1.5 text-fg">{line.name}</span>}
          {line.code ? <span className={needed ? 'text-warn' : 'text-fg-3'}>{line.code}</span> : needed && <span className="text-warn">{PENDING_TEXT}</span>}
        </span>
      ))}
    </div>
  )
}

export function ImportRowsTable({
  session, rows, total, offset, status, loading, onFilter, onPage, onInclude, onEdit, onSkip, onReset, busy, disabledReason,
}: ImportRowsTableProps) {
  const statement = session.statement
  const tabs = statusTabs(session)
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pages = pageCount(total)
  const blocked = busy || Boolean(disabledReason)
  const linesOf = new Map(rows.map((row) => [row.row, rowLines(row, statement)]))
  const lines = (row: ImportRow) => linesOf.get(row.row) ?? rowLines(row, statement)

  // In the table below 1280 px the buttons show their icon only (the name stays for screen readers and on
  // hover), so the last column is not pushed out of a 1100 px table; a phone card has room for the words.
  const actions = (row: ImportRow, compact: boolean) => {
    const text = (label: string) => <span className={cx(compact && 'max-xl:sr-only')}>{label}</span>
    // The kit shows disabledReason as its own tip; a title beside it would show a second one.
    const tip = (label: string) => (compact && !disabledReason ? label : undefined)
    const offered = rowActions(row)
    return (
      <div className="flex justify-end gap-1">
        {offered.edit && (
          <Button size="sm" variant="secondary" icon={Pencil} disabledReason={disabledReason} disabled={busy} onClick={() => onEdit(row)}
            aria-label={`处理第 ${row.row} 行`} title={tip('处理')}>
            {text('处理')}
          </Button>
        )}
        {offered.skip && (
          <Button size="sm" variant="ghost" icon={SkipForward} disabledReason={disabledReason} disabled={busy} onClick={() => onSkip(row)}
            aria-label={`跳过第 ${row.row} 行`} title={tip('跳过')}>
            {text('跳过')}
          </Button>
        )}
        {offered.reset && (
          <Button size="sm" variant="ghost" icon={Undo2} disabledReason={disabledReason} disabled={busy} onClick={() => onReset(row)}
            aria-label={`撤销第 ${row.row} 行的修改`} title={tip('撤销修改')}>
            {text('撤销修改')}
          </Button>
        )}
      </div>
    )
  }

  const include = (row: ImportRow) => (row.can_include ? (
    <Checkbox
      aria-label={`第 ${row.row} 行入账`}
      checked={row.include}
      disabled={blocked}
      onChange={(event) => onInclude(row, event.target.checked)}
    />
  ) : null)

  const columns: Column<ImportRow>[] = [
    { key: 'include', header: <span className="sr-only">入账</span>, label: '入账', width: 36, render: include },
    { key: 'row', header: '行', numeric: true, width: 48, render: (row) => <span className="text-fg-3">{row.row}</span> },
    { key: 'date', header: '日期', render: (row) => <Cells row={row} lines={lines(row)} field="date" numericCell /> },
    { key: 'kind', header: '类型', render: (row) => <Cells row={row} lines={lines(row)} field="kind" /> },
    { key: 'subject', header: '标的', render: (row) => <Subject row={row} lines={lines(row)} /> },
    { key: 'quantity', header: '数量', numeric: true, render: (row) => <Cells row={row} lines={lines(row)} field="quantity" numericCell /> },
    { key: 'price', header: statement === 'holdings' ? '成本价' : '价格', numeric: true, render: (row) => <Cells row={row} lines={lines(row)} field="price" numericCell /> },
    ...(statement === 'holdings' ? [] : [
      { key: 'fee', header: '费用', numeric: true, render: (row: ImportRow) => <Cells row={row} lines={lines(row)} field="fee" numericCell /> },
      { key: 'amount', header: '金额（原币）', numeric: true, render: (row: ImportRow) => <Cells row={row} lines={lines(row)} field="amount" numericCell /> },
    ]),
    {
      key: 'status', header: '状态',
      render: (row) => <Badge tone={statusTone(row.status)}>{statusLabel(row.status, session.status_labels) || row.status_label}</Badge>,
    },
    { key: 'actions', header: <span className="sr-only">操作</span>, label: '操作', align: 'right', render: (row) => actions(row, true) },
  ]

  const extra = (row: ImportRow) => {
    const { reasons, notes, reference, unfixable } = rowExplanation(row)
    if (!reasons.length && !notes.length && !reference && !unfixable) return null
    const tone = statusTone(row.status) === 'warn' ? 'text-warn' : 'text-fg-2'
    return (
      <div className="flex min-w-0 flex-col gap-0.5 whitespace-normal pl-[84px] max-sm:pl-0">
        {reasons.map((reason) => <span key={reason} className={cx('break-words', tone)}>{reason}</span>)}
        {unfixable && <span className={cx('break-words', tone)}>{unfixable}</span>}
        {reference && <LongValue value={reference} className={cx('break-words text-fg-2', numeric)} />}
        {notes.map((note) => <span key={note} className="break-words text-fg-3">{note}</span>)}
      </div>
    )
  }

  const card = (row: ImportRow) => {
    const all = lines(row)
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2">
            {include(row)}
            <div className="min-w-0">
              {all.map((line) => (
                <p key={line.key} className={cx('break-words text-body text-fg', numeric)}>
                  <span className="text-fg-3">{line.date}</span> {line.kind} {line.name} <span className="text-fg-3">{line.code}</span>
                </p>
              ))}
            </div>
          </div>
          <Badge tone={statusTone(row.status)} className="shrink-0">{statusLabel(row.status, session.status_labels) || row.status_label}</Badge>
        </div>
        {all.map((line) => {
          const figures = longLine((pick) => [
            line.quantity.text && `${pick(line.quantity)}${statement === 'holdings' ? ' 股' : ''}`,
            line.price.text && `@ ${pick(line.price)}${line.priceNote ? `（${line.priceNote}）` : ''}`,
            line.fee.text && `费 ${pick(line.fee)}${line.feeNote ? `（${line.feeNote}）` : ''}`,
            line.amount.text && pick(line.amount),
          ].filter(Boolean).join(' · '))
          return figures.text ? <p key={`${line.key}:n`} className={cx('text-table text-fg-2', numeric)}><LongValue value={figures} /></p> : null
        })}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-caption text-fg-3">第 {row.row} 行</p>
          {actions(row, false)}
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Tabs
        ariaLabel="按状态筛选"
        items={tabs.map((tab) => ({ id: tab.id, label: tab.label, count: tab.count }))}
        value={status ?? 'all'}
        onChange={(id) => onFilter(id === 'all' ? null : id)}
      />
      <DataTable
        caption="导入预览"
        columns={columns}
        rows={rows}
        rowKey={(row) => String(row.row)}
        loading={loading}
        stickyHeader={false}
        mobile="cards"
        card={card}
        renderRowExtra={extra}
        empty={<EmptyState title="这个筛选下没有行" />}
      />
      <div className={cx('flex flex-wrap items-center justify-between gap-2 text-table text-fg-2', numeric)}>
        <span role="status">{pageLabel(offset, rows.length, total)}</span>
        {pages > 1 && (
          <div className="flex items-center gap-1">
            <Button size="sm" variant="ghost" icon={ChevronLeft} disabled={page <= 1 || loading} onClick={() => onPage(page - 1)}>上一页</Button>
            <span className="px-1">{page} / {pages}</span>
            <Button size="sm" variant="ghost" disabled={page >= pages || loading} onClick={() => onPage(page + 1)}>
              下一页<ChevronRight size={14} aria-hidden />
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
