// The headline figures, plain text on the page (no cards): 总资产, 今日盈亏, 总盈亏, 现金, and a quiet line
// with 持仓盈亏 and 累计净投入. Every figure comes from `totals`; a null one says 不可用 and how many
// inputs are missing; every known figure of estimated totals carries its own amber qualifier with the
// inputs behind it (total assets may be 不可用 while the profits are known); a known figure whose
// percentage could not be computed shows 不可用 for it.
import type { ReactNode } from 'react'
import { Skeleton, Stat, StatGroup, cx, numeric, useMediaQuery } from '@/components/ui'
import { UNAVAILABLE, fmtMoney, fmtPct } from '@/lib/format'
import { totalsView, type EstimateItem, type Figure } from '@/features/today/totals'
import type { TodayPayload } from '@/services/today'
import { EstimateNote } from './EstimateNote'
import { Dot, Unavailable, signed } from './cells'

const HINT = {
  day: '按各标的所属交易日计算的价格变动和当日交易费用；不含分红、单独记录的费用和汇率变动。',
  total: '总资产减去账本记录的累计净投入，含已实现盈亏、分红和费用；与组合走势的总盈亏一致。',
  holding: '未实现盈亏：当前持仓按平均成本计算，不含已卖出的部分。',
}
const PCT_UNAVAILABLE = '百分比无法计算：缺少所需数据，或基数不为正'

function Label({ children, hint }: { children: ReactNode; hint: string }) {
  return <span title={hint} className="cursor-help underline decoration-line-input/50 decoration-dotted underline-offset-4">{children}</span>
}

/** The amber 估算 on one figure: a button listing the inputs, or plain text when the list is empty. */
function EstimateMark({ items, what }: { items: EstimateItem[]; what: string }) {
  if (items.length) return <EstimateNote items={items} what={what} />
  return <span className="text-table text-warn" title="部分行情或汇率未经核实，按最近数据估算">估算</span>
}

/** A figure's percentage (signed, coloured), 不可用 when the backend could not compute it, else nothing. */
function Pct({ figure }: { figure: Figure }) {
  if (figure.pct !== null) return <>{signed(fmtPct(figure.pct, { signed: true, exact: true }), figure.pct)}</>
  if (figure.pctUnavailable) return <Unavailable title={PCT_UNAVAILABLE} />
  return null
}

function pnlStat(label: ReactNode, figure: Figure, size: 'md' | 'lg', what: string, estimates: EstimateItem[], className?: string) {
  const sub = figure.pct !== null || figure.pctUnavailable || figure.estimated
    ? (
      <span className="inline-flex flex-wrap items-baseline gap-x-2">
        <Pct figure={figure} />
        {figure.estimated && <EstimateMark items={estimates} what={what} />}
      </span>
    )
    : undefined
  return (
    <Stat
      size={size}
      className={className}
      label={label}
      tone="auto"
      value={figure.value === null ? null : fmtMoney(figure.value, 'CNY', { signed: true, exact: true })}
      unavailableReason={figure.missing ?? undefined}
      sub={sub}
    />
  )
}

export function TotalsSkeleton() {
  return (
    <div aria-busy="true">
      <StatGroup className="gap-x-10">
        {['总资产', '今日盈亏', '总盈亏', '现金'].map((label) => (
          <dl key={label} className="min-w-0">
            <dt className="text-table text-fg-2">{label}</dt>
            <dd className="flex h-9 items-center"><Skeleton w={140} h={24} /></dd>
          </dl>
        ))}
      </StatGroup>
    </div>
  )
}

export function TotalsBar({ data }: { data: TodayPayload }) {
  const phone = useMediaQuery('(max-width: 639px)')
  const view = totalsView(data)
  const [firstCash, ...otherCash] = view.cash
  const contributed = data.totals.contributed_cny
  const size = phone ? 'md' : 'lg'

  const total = (
    <Stat
      size="lg"
      className={phone ? 'col-span-2' : undefined}
      label="总资产"
      value={view.total.value === null ? null : fmtMoney(view.total.value, 'CNY', { exact: true })}
      unavailableReason={view.total.missing ?? undefined}
      sub={view.total.estimated ? <EstimateMark items={view.estimates} what="总资产" /> : undefined}
    />
  )
  const cash = firstCash ? (
    <Stat
      size={size}
      label="现金"
      value={fmtMoney(firstCash[1], firstCash[0], { exact: true })}
      sub={otherCash.length ? (
        <span className={cx('text-fg-2', phone && 'basis-full text-table')}>
          {otherCash.map(([currency, amount]) => fmtMoney(amount, currency, { exact: true })).join(' · ')}
        </span>
      ) : undefined}
    />
  ) : null
  const holding = view.holding
  const details = (
    <p className={cx('flex flex-wrap items-baseline gap-x-2 gap-y-1 text-table text-fg-2', numeric)}>
      {!phone && (
        <>
          <Label hint={HINT.holding}>持仓盈亏</Label>
          {holding.value === null
            ? <span className="text-fg-3">{UNAVAILABLE}{holding.missing ? `（${holding.missing}）` : ''}</span>
            : signed(fmtMoney(holding.value, 'CNY', { signed: true, exact: true }), holding.value)}
          <Pct figure={holding} />
          {holding.estimated && <EstimateMark items={view.estimates} what="持仓盈亏" />}
          {contributed !== null && <Dot />}
        </>
      )}
      {contributed !== null && (
        <>
          <span>累计净投入</span>
          <span className="text-fg">{fmtMoney(contributed, 'CNY', { exact: true })}</span>
        </>
      )}
    </p>
  )

  if (phone) {
    return (
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-x-4 gap-y-4">
          {total}
          {pnlStat(<Label hint={HINT.day}>今日盈亏</Label>, view.day, size, '今日盈亏', view.estimates)}
          {pnlStat(<Label hint={HINT.total}>总盈亏</Label>, view.cumulative, size, '总盈亏', view.estimates)}
          {pnlStat(<Label hint={HINT.holding}>持仓盈亏</Label>, holding, size, '持仓盈亏', view.estimates)}
          {cash}
        </div>
        {details}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <StatGroup className="gap-x-10">
        {total}
        {pnlStat(<Label hint={HINT.day}>今日盈亏</Label>, view.day, size, '今日盈亏', view.estimates)}
        {pnlStat(<Label hint={HINT.total}>总盈亏</Label>, view.cumulative, size, '总盈亏', view.estimates)}
        {cash}
      </StatGroup>
      {details}
    </div>
  )
}
