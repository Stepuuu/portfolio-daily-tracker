import {
  Fragment, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type MouseEvent, type ReactNode,
} from 'react'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'
import { cx, focusRing, focusRingInset, numeric } from './cx'
import { EmptyState, Skeleton } from './feedback'
import { useMediaQuery } from './hooks'

export interface Column<T> {
  key: string
  header: ReactNode
  /** Plain-text name for card layouts when `header` is not a string. */
  label?: string
  align?: 'left' | 'right' | 'center'
  /** Right-aligned tabular figures, header included. Use for every number column. */
  numeric?: boolean
  width?: number | string
  render: (row: T) => ReactNode
  /** Makes the column sortable. Return null for unknown values; they always sort last. */
  sortValue?: (row: T) => number | string | null
  /** Hide the column on screens narrower than this breakpoint (and in mobile cards). */
  hideBelow?: 'sm' | 'md' | 'lg'
  /** Let long text wrap (notes). Cells stay on one line otherwise, and a narrow table scrolls sideways. */
  wrap?: boolean
}

export interface GroupBy<T> {
  key: (row: T) => string
  /** Label of the group row (first column). */
  header: (key: string, rows: T[]) => ReactNode
  /** Figures for the group row, aligned under their columns (e.g. account totals from the server). */
  summary?: (key: string, rows: T[]) => Partial<Record<string, ReactNode>>
  /** A row after the group, spanning the table. */
  footer?: (key: string, rows: T[]) => ReactNode
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  /** What the table lists; read by screen readers (not shown). Required. */
  caption: string
  onRowClick?: (row: T) => void
  /** Buttons for a row; shown on hover and keyboard focus (always on touch screens). */
  rowActions?: (row: T) => ReactNode
  groupBy?: GroupBy<T>
  /** A line under the table (totals, "共 214 行"). */
  footer?: ReactNode
  /** First load: skeleton rows. With rows already shown, they stay and the table is marked busy. */
  loading?: boolean
  /** Shown when there are no rows; an EmptyState with the next step. */
  empty?: ReactNode
  /** Header sticks to the top of the scrolling page (offset by `--sticky-top`). Default true. */
  stickyHeader?: boolean
  /** Rows of 32 px (compact) or 36 px (default); 44 px on touch screens either way. */
  density?: 'compact' | 'comfortable'
  /** Below 640 px: scroll sideways (default) or show one card per row. */
  mobile?: 'scroll' | 'cards'
  /** Custom card for `mobile="cards"`; the default lists the visible columns. */
  card?: (row: T) => ReactNode
  /** Extra line under a row, spanning the table (plan chips, notes). */
  renderRowExtra?: (row: T) => ReactNode
  /** Limit the height and scroll inside the table (the header sticks within it). */
  maxHeight?: number | string
  defaultSort?: { key: string; direction: 'asc' | 'desc' }
  rowClassName?: (row: T) => string | undefined
  className?: string
}

type SortState = { key: string; direction: 'asc' | 'desc' } | null

/** Label / value pairs of a phone card; blank values (cash rows have no price) are left out. */
function CardFields<T>({ columns, values }: { columns: Column<T>[]; values: ReactNode[] }) {
  const fields = columns.map((column, index) => ({ column, value: values[index] })).filter((field) => !isBlank(field.value))
  if (fields.length === 0) return null
  return (
    <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 font-normal">
      {fields.map(({ column, value }) => (
        <div key={column.key} className="flex min-w-0 items-baseline justify-between gap-2">
          <dt className="shrink-0 text-caption text-fg-3">{columnLabel(column)}</dt>
          <dd className={cx('min-w-0 truncate text-right text-table', column.numeric && numeric)}>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

const HIDE = { sm: 'max-sm:hidden', md: 'max-md:hidden', lg: 'max-lg:hidden' } as const
const ALIGN = { left: 'text-left', right: 'text-right', center: 'text-center' } as const
// Clicks on these inside a row do not trigger onRowClick.
const INTERACTIVE = 'a,button,input,select,textarea,label,summary,[role="button"],[role="checkbox"],[data-row-ignore]'

function compareValues(a: number | string, b: number | string): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return String(a).localeCompare(String(b), 'zh-CN', { numeric: true })
}

function columnLabel<T>(column: Column<T>): string {
  return column.label ?? (typeof column.header === 'string' ? column.header : column.key)
}

const isBlank = (value: ReactNode) => value === null || value === undefined || value === false || value === ''

/**
 * The table for every list of records: holdings, ledger events, imports, alerts, backtest trades.
 * Number columns are right-aligned with tabular figures; the header sticks while the page scrolls; the
 * table scrolls sideways inside itself only when it does not fit.
 */
export function DataTable<T>({
  columns, rows, rowKey, caption, onRowClick, rowActions, groupBy, footer, loading = false, empty,
  stickyHeader = true, density = 'comfortable', mobile = 'scroll', card, renderRowExtra, maxHeight,
  defaultSort, rowClassName, className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState>(defaultSort ?? null)
  const phone = useMediaQuery('(max-width: 639px)')
  const cards = mobile === 'cards' && phone
  const wrapper = useRef<HTMLDivElement>(null)
  const table = useRef<HTMLTableElement>(null)
  const [overflowing, setOverflowing] = useState(false)

  useLayoutEffect(() => {
    const outer = wrapper.current
    const inner = table.current
    if (!outer || !inner || typeof ResizeObserver === 'undefined') return
    const check = () => setOverflowing(inner.offsetWidth > outer.clientWidth + 1)
    check()
    const observer = new ResizeObserver(check)
    observer.observe(outer)
    observer.observe(inner)
    return () => observer.disconnect()
  }, [cards])

  const sortRows = useMemo(() => {
    const column = sort && columns.find((item) => item.key === sort.key)
    if (!sort || !column?.sortValue) return (list: T[]) => list
    const value = column.sortValue
    const factor = sort.direction === 'asc' ? 1 : -1
    return (list: T[]) =>
      list
        .map((row, index) => ({ row, index, value: value(row) }))
        .sort((a, b) => {
          if (a.value === null || a.value === undefined) return b.value === null || b.value === undefined ? a.index - b.index : 1
          if (b.value === null || b.value === undefined) return -1
          return factor * compareValues(a.value, b.value) || a.index - b.index
        })
        .map((item) => item.row)
  }, [sort, columns])

  const groups = useMemo(() => {
    if (!groupBy) return null
    const order: string[] = []
    const byKey = new Map<string, T[]>()
    for (const row of rows) {
      const key = groupBy.key(row)
      if (!byKey.has(key)) {
        byKey.set(key, [])
        order.push(key)
      }
      byKey.get(key)!.push(row)
    }
    return order.map((key) => ({ key, rows: byKey.get(key)! }))
  }, [rows, groupBy])

  const toggleSort = (column: Column<T>) => {
    const first = column.numeric ? 'desc' : 'asc'
    setSort((current) => {
      if (!current || current.key !== column.key) return { key: column.key, direction: first }
      if (current.direction === first) return { key: column.key, direction: first === 'asc' ? 'desc' : 'asc' }
      return null
    })
  }

  const clickable = Boolean(onRowClick)
  const rowProps = (row: T) =>
    clickable
      ? {
        tabIndex: 0,
        onClick: (event: MouseEvent<HTMLElement>) => {
          if (!(event.target as Element).closest(INTERACTIVE)) onRowClick!(row)
        },
        onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
          if ((event.key === 'Enter' || event.key === ' ') && event.target === event.currentTarget) {
            event.preventDefault()
            onRowClick!(row)
          }
        },
      }
      : {}

  const colCount = columns.length + (rowActions ? 1 : 0)
  const isEmpty = rows.length === 0 && !loading
  const emptyContent = empty ?? <EmptyState title="暂无记录" />
  const busyText = loading ? <span role="status" className="sr-only">正在加载{caption}</span> : null

  // ---------------------------------------------------------------- cards (phones)
  if (cards) {
    const cardColumns = columns.filter((column) => !column.hideBelow)
    const [first, ...rest] = cardColumns
    const renderCard = (row: T) => (
      <li
        key={rowKey(row)}
        {...rowProps(row)}
        className={cx('px-4 py-3', clickable && 'cursor-pointer hover:bg-surface-2', clickable && focusRingInset, rowClassName?.(row))}
      >
        {card ? card(row) : (
          <>
            {first && <div className="min-w-0 text-body font-medium text-fg">{first.render(row)}</div>}
            <CardFields columns={rest} values={rest.map((column) => column.render(row))} />
          </>
        )}
        {renderRowExtra?.(row)}
        {rowActions && <div className="mt-2 flex flex-wrap gap-2">{rowActions(row)}</div>}
      </li>
    )
    return (
      <div ref={wrapper} className={cx('relative min-w-0', className)} aria-busy={loading || undefined}>
        {busyText}
        {loading && rows.length === 0 ? (
          <ul aria-label={caption} className="divide-y divide-line">
            {Array.from({ length: 4 }, (_, index) => (
              <li key={index} className="px-4 py-3"><Skeleton lines={2} /></li>
            ))}
          </ul>
        ) : isEmpty ? (
          emptyContent
        ) : (
          <ul aria-label={caption} className="divide-y divide-line">
            {groups
              ? groups.map((group) => (
                <Fragment key={group.key}>
                  <li className="bg-surface-2/50 px-4 py-2 text-table font-medium text-fg">
                    <div className="min-w-0">{groupBy!.header(group.key, group.rows)}</div>
                    {groupBy!.summary && (() => {
                      const summary = groupBy!.summary(group.key, group.rows)
                      return <CardFields columns={rest} values={rest.map((column) => summary[column.key])} />
                    })()}
                  </li>
                  {sortRows(group.rows).map(renderCard)}
                  {groupBy!.footer && <li className="px-4 py-2 text-table text-fg-2">{groupBy!.footer(group.key, group.rows)}</li>}
                </Fragment>
              ))
              : sortRows(rows).map(renderCard)}
          </ul>
        )}
        {footer && <div className="border-t border-line px-4 py-2 text-table text-fg-2">{footer}</div>}
      </div>
    )
  }

  // ---------------------------------------------------------------- table
  const rowHeight = density === 'compact' ? 'h-8 coarse:h-11' : 'h-9 coarse:h-11'
  const cell = 'border-b border-line px-3 align-middle first:pl-4 last:pr-4'
  const bodyCell = (column: Column<T>) =>
    cx(
      cell, rowHeight, 'text-table',
      ALIGN[column.align ?? (column.numeric ? 'right' : 'left')],
      column.wrap ? 'min-w-[12rem]' : 'whitespace-nowrap',
      column.numeric && numeric,
      column.hideBelow && HIDE[column.hideBelow],
    )
  const sticky = stickyHeader && !overflowing
  const headCell = (column?: Column<T>) =>
    cx(
      'h-9 whitespace-nowrap bg-surface-2 px-3 text-caption font-medium text-fg-3 first:pl-4 last:pr-4',
      'shadow-[inset_0_-1px_0_rgb(var(--border))]',
      sticky && cx('sticky z-[1]', maxHeight !== undefined ? 'top-0' : 'top-[var(--sticky-top,0px)]'),
      column && ALIGN[column.align ?? (column.numeric ? 'right' : 'left')],
      column?.hideBelow && HIDE[column.hideBelow],
    )

  const renderRow = (row: T) => {
    const key = rowKey(row)
    const extra = renderRowExtra?.(row)
    return (
      <Fragment key={key}>
        <tr
          {...rowProps(row)}
          className={cx(
            'group/row transition-colors duration-[120ms] hover:bg-surface-2',
            clickable && cx('cursor-pointer focus-visible:bg-surface-2', focusRingInset),
            rowClassName?.(row),
          )}
        >
          {columns.map((column) => (
            <td key={column.key} className={cx(bodyCell(column), extra && 'border-b-0')}>{column.render(row)}</td>
          ))}
          {rowActions && (
            <td className={cx(cell, rowHeight, 'w-px whitespace-nowrap text-right', extra && 'border-b-0')}>
              <div className="flex justify-end gap-1 opacity-0 transition-opacity duration-[120ms] group-focus-within/row:opacity-100 group-hover/row:opacity-100 coarse:opacity-100">
                {rowActions(row)}
              </div>
            </td>
          )}
        </tr>
        {extra && (
          <tr>
            <td colSpan={colCount} className="border-b border-line px-4 pb-2 text-table text-fg-2">{extra}</td>
          </tr>
        )}
      </Fragment>
    )
  }

  return (
    <div
      ref={wrapper}
      // relative: absolutely positioned descendants (sr-only text, tooltips) stay inside the scroll box.
      className={cx('relative min-w-0', (overflowing || maxHeight !== undefined) && 'overflow-auto', className)}
      style={maxHeight !== undefined ? { maxHeight } : undefined}
    >
      {busyText}
      <table
        ref={table}
        aria-busy={loading || undefined}
        className="w-full border-separate border-spacing-0 text-fg [&>tbody:last-of-type>tr:last-child>*]:border-b-0"
      >
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key
              const direction = active ? sort!.direction : null
              const right = (column.align ?? (column.numeric ? 'right' : 'left')) === 'right'
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={column.sortValue ? (direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none') : undefined}
                  style={column.width !== undefined ? { width: column.width } : undefined}
                  className={headCell(column)}
                >
                  {column.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      className={cx('group/sort inline-flex items-center gap-1 rounded-sm hover:text-fg', right && 'flex-row-reverse', active && 'text-fg-2', focusRing)}
                    >
                      {column.header}
                      {direction === 'asc' ? (
                        <ArrowUp size={12} aria-hidden />
                      ) : direction === 'desc' ? (
                        <ArrowDown size={12} aria-hidden />
                      ) : (
                        <ChevronsUpDown size={12} aria-hidden className="opacity-0 group-hover/sort:opacity-100 group-focus-visible/sort:opacity-100" />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
            {rowActions && (
              <th scope="col" className={headCell()}><span className="sr-only">操作</span></th>
            )}
          </tr>
        </thead>
        {loading && rows.length === 0 ? (
          <tbody>
            {Array.from({ length: 5 }, (_, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column.key} className={bodyCell(column)}>
                    <Skeleton w={column.numeric ? 56 : index % 2 ? '45%' : '65%'} className={column.numeric ? 'ml-auto' : undefined} />
                  </td>
                ))}
                {rowActions && <td className={cx(cell, rowHeight)} />}
              </tr>
            ))}
          </tbody>
        ) : isEmpty ? (
          <tbody>
            <tr>
              <td colSpan={colCount} className={cell}>{emptyContent}</td>
            </tr>
          </tbody>
        ) : groups ? (
          groups.map((group) => {
            const summary = groupBy!.summary?.(group.key, group.rows)
            return (
              <tbody key={group.key}>
                <tr className="bg-surface-2/50">
                  <th
                    scope="rowgroup"
                    colSpan={summary ? 1 : colCount}
                    className={cx(cell, rowHeight, 'whitespace-nowrap text-left text-table font-medium text-fg')}
                  >
                    {groupBy!.header(group.key, group.rows)}
                  </th>
                  {summary && columns.slice(1).map((column) => (
                    <td key={column.key} className={cx(bodyCell(column), 'font-medium')}>{summary[column.key]}</td>
                  ))}
                  {summary && rowActions && <td className={cx(cell, rowHeight)} />}
                </tr>
                {sortRows(group.rows).map(renderRow)}
                {groupBy!.footer && (
                  <tr>
                    <td colSpan={colCount} className={cx(cell, rowHeight, 'text-table text-fg-2')}>{groupBy!.footer(group.key, group.rows)}</td>
                  </tr>
                )}
              </tbody>
            )
          })
        ) : (
          <tbody>{sortRows(rows).map(renderRow)}</tbody>
        )}
        {footer && (
          <tfoot>
            <tr>
              <td colSpan={colCount} className="border-t border-line px-4 py-2 text-table text-fg-2">{footer}</td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}
