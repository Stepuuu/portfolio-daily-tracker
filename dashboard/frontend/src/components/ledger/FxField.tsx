// 汇率 for foreign cash moving in or out of an account (入金, 出金, 转账 in HKD or USD): how many CNY one unit
// was worth, which moves 累计净投入. The rate is evidence: it is typed from the bank's statement, or the
// reference rate `/api/today` reports is taken with [按参考汇率] and then says so (source, time, and that it
// is another day's rate when the entry is dated otherwise). Nothing is prefilled; a stale or missing
// reference is shown as such and cannot be taken. Always required; changing the currency clears it.
import { Badge, Button, Field, NumberInput, numeric } from '@/components/ui'
import { fmtDate, fmtNumber } from '@/lib/format'
import { shortTime, statusChip } from '@/features/today/quoteStatus'
import { DISPLAY_ZONE, sourceLabel } from '@/features/today/session'
import type { FxEntry } from '@/features/today/types'
import type { RateFill } from '@/features/ledger/entry'

/** A reference rate the form may offer: verified (live, delayed or a known close) and present. */
export function usableRate(fx: FxEntry | undefined): string | null {
  if (!fx?.rate) return null
  return fx.status === 'live' || fx.status === 'delayed' || fx.status === 'closed' ? fx.rate : null
}

/** The fill a taken reference rate records (source in words, its time). */
export function rateFill(fx: FxEntry): RateFill | null {
  const rate = usableRate(fx)
  return rate ? { value: rate, source: sourceLabel(fx.source) || null, time: fx.quote_time } : null
}

/** "参考汇率 7.0500 · 模拟行情 10:32" with a chip when the rate is not fresh. */
export function ReferenceRate({ currency, fx, today, onUse }: {
  currency: string; fx: FxEntry | undefined; today: string | null; onUse?: (fill: RateFill) => void
}) {
  if (!fx || fx.status === 'unavailable' || !fx.rate) {
    return <span>参考汇率不可用{fx?.reason ? `（${fx.reason}）` : ''}，请按银行实际汇率填写</span>
  }
  const chip = statusChip({ ...fx, fetched_at: null, session_date: null }, today)
  const time = shortTime(fx.quote_time, today)
  const fill = rateFill(fx)
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={numeric}>
        参考汇率 1 {currency} = {fmtNumber(fx.rate, { digits: 4, exact: true })} CNY
        {fx.source && ` · ${sourceLabel(fx.source)}`}{time && ` ${time}`}
      </span>
      {chip && <Badge tone={chip.tone} title={chip.title}>{chip.label}</Badge>}
      {onUse && fill && (
        <Button size="sm" variant="ghost" className="-my-1 text-accent hover:text-accent" onClick={() => onUse(fill)}>按参考汇率</Button>
      )}
    </span>
  )
}

export interface FxFieldProps {
  currency: string
  value: string
  /** Typed: the rate is the person's own. */
  onChange: (value: string) => void
  /** [按参考汇率]: the rate is the reference's, recorded as such. */
  onUse: (fill: RateFill) => void
  /** The reference rate in the field, while unchanged. */
  fill: RateFill | null | undefined
  fx: FxEntry | undefined
  today: string | null
  /** The entry's date: a reference rate of another day is marked. */
  date: string
  error?: string
}

export function FxField({ currency, value, onChange, onUse, fill, fx, today, date, error }: FxFieldProps) {
  const taken = fill && fill.value === value ? fill : null
  const day = taken?.time ? fmtDate(taken.time, { timeZone: DISPLAY_ZONE }) : null
  const otherDay = Boolean(taken && date && day && day !== date)
  const hint = taken ? (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={numeric}>
        已按参考汇率填入{taken.source ? ` · ${taken.source}` : ''}{taken.time ? ` ${day ?? ''} ${shortTime(taken.time, null)}` : ''}
      </span>
      {otherDay
        ? <Badge tone="warn" title={`这是 ${day} 的参考汇率，不是 ${date} 的汇率；请按银行实际汇率核对`}>{day === today ? '今天' : day}的汇率</Badge>
        : <Badge tone="warn" title="参考汇率不是银行实际成交汇率">参考</Badge>}
    </span>
  ) : (
    <ReferenceRate currency={currency} fx={fx} today={today} onUse={onUse} />
  )
  return (
    <Field label={`汇率（1 ${currency} = ? CNY）`} required error={error} hint={hint}>
      <NumberInput value={value} onValueChange={onChange} suffix="CNY" placeholder="银行实际汇率" />
    </Field>
  )
}
