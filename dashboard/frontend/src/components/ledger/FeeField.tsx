// 费用: prefilled from the account's fee profile. The estimate for exactly the inputs on screen shows in the
// empty field with its basis and breakdown underneath (按账户费率估算：佣金 · 过户费 · 印花税), marked 估算;
// leaving the field empty books the estimate, fetched again at 预览入账 against the account's current
// profile (a cached one is never booked) and recorded as an estimate; typing a number (0 included) books
// that. Without a profile for the market (HK, US by default) there is no estimate and the fee must be
// typed: it is never booked as 0 silently.
import type { ReactNode } from 'react'
import { Badge, Field, MoneyInput, numeric } from '@/components/ui'
import { fmtNumber } from '@/lib/format'
import { feeHint } from '@/features/ledger/fees'
import type { FeeEstimate } from '@/features/ledger/types'

export interface FeeFieldProps {
  currency: string
  value: string
  onChange: (value: string) => void
  /** The estimate for the current inputs; undefined until quantity and price are known. */
  estimate?: FeeEstimate | null
  loading?: boolean
  /** The estimate request failed. */
  failed?: boolean
  error?: string
}

export function FeeField({ currency, value, onChange, estimate, loading = false, failed = false, error }: FeeFieldProps) {
  const total = estimate?.total ?? null
  const required = !loading && total === null
  let hint: ReactNode
  if (loading && !estimate) hint = '正在按账户费率估算…'
  else if (failed) hint = '暂时无法估算费用，请按成交单填写'
  else if (!estimate) hint = '填写数量和价格后按账户费率估算'
  else if (total === null) hint = `${feeHint(estimate)}，请按成交单填写`
  else {
    hint = (
      <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {!value && <Badge tone="warn" title="按费率计算，不是成交单上的费用">估算</Badge>}
        <span className={numeric}>
          {feeHint(estimate)}
          <span className="text-fg-3">{value ? '；已改为手动填写' : '；留空按估算入账（预览时重算）'}</span>
        </span>
      </span>
    )
  }
  return (
    <Field label="费用" required={required} hint={hint} error={error}>
      <MoneyInput
        currency={currency}
        value={value}
        onValueChange={onChange}
        placeholder={total !== null ? fmtNumber(total, { exact: true }) : '按成交单填写'}
      />
    </Field>
  )
}
