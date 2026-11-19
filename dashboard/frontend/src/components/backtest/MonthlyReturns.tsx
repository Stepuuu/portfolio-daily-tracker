// The monthly returns as the service computed them (`monthly_returns`: each month-end net value against
// the previous month end, the first month against the capital): a row per calendar month, a column per
// year. Nothing is added up or compounded here; a month outside the run is "—" and says so.
import { DataTable, cx, numeric, type Column } from '@/components/ui'
import { UNAVAILABLE } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { gainTone, monthlyRows, pctText, type MonthlyRow } from '@/features/backtest/result'
import type { RunResult } from '@/features/backtest/types'

const OUTSIDE = '—'

function Cell({ value }: { value: number | null | undefined }) {
  if (value === undefined) return <span className="text-fg-3" title="不在回测区间内">{OUTSIDE}</span>
  const text = pctText(value)
  return <span className={cx(numeric, text === UNAVAILABLE ? 'text-fg-3' : pnlTextClass(gainTone(value)))}>{text}</span>
}

/** One row per calendar month, one column per year: twelve rows fit a phone and a laptop alike. */
export function MonthlyReturns({ result }: { result: RunResult }) {
  const years = monthlyRows(result.monthly_returns)
  const months = Array.from({ length: 12 }, (_, index) => index)
  const columns: Column<number>[] = [
    { key: 'month', header: '月份', render: (month) => <span className={cx('text-fg', numeric)}>{month + 1}月</span> },
    ...years.map((row: MonthlyRow): Column<number> => ({
      key: `y${row.year}`,
      header: `${row.year}年`,
      numeric: true,
      render: (month) => <Cell value={row.months[month]} />,
    })),
  ]
  return (
    <section aria-label="月度收益" data-chart="monthly" className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-title text-fg">月度收益</h3>
        <span className="text-table text-fg-3">每月月末净值比上月月末（第一个月比本金），回测服务算出；首尾两个月可能不是整月；“—”表示不在回测区间内</span>
      </div>
      {years.length ? (
        <DataTable
          caption="月度收益"
          columns={columns}
          rows={months}
          rowKey={(month) => String(month)}
          mobile="cards"
          density="compact"
        />
      ) : (
        <p className="text-table text-fg-3">不可用：这条结果里没有月度数据（较早保存的记录没有这一项）。</p>
      )}
    </section>
  )
}
