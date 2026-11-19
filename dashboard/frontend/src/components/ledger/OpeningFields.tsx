// 新账户（期初余额）: the account's cash per currency, its 累计净投入, an optional fund value and the positions
// it starts with. One opening per account; everything after it is an ordinary entry. A row's problems are
// described by its own message (aria-describedby) on each field they concern. B shares are refused per row.
import { useId } from 'react'
import { Plus, X } from 'lucide-react'
import { Button, Field, IconButton, Input, MoneyInput, NumberInput, cx, numeric } from '@/components/ui'
import { CURRENCIES, parseTicker, tickerCurrency, type EntryValues, type OpeningPositionInput } from '@/features/ledger/entry'
import type { EntryErrors } from '@/features/ledger/entry'

const MAX_ROWS = 100

export interface OpeningFieldsProps {
  values: EntryValues
  onChange: (patch: Partial<EntryValues>) => void
  errors: EntryErrors
}

export function OpeningFields({ values, onChange, errors }: OpeningFieldsProps) {
  const base = useId()
  const setRow = (index: number, patch: Partial<OpeningPositionInput>) =>
    onChange({ positions: values.positions.map((row, i) => (i === index ? { ...row, ...patch } : row)) })
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        {CURRENCIES.map((currency) => (
          <Field key={currency} label={`${currency} 期初现金`} error={errors[`cash.${currency}`]}>
            <MoneyInput
              currency={currency}
              allowNegative
              value={values.openingCash[currency]}
              onValueChange={(value) => onChange({ openingCash: { ...values.openingCash, [currency]: value } })}
              placeholder="0"
            />
          </Field>
        ))}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="累计净投入（CNY）" required hint="历年转入减转出的本金合计" error={errors.contributed}>
          <MoneyInput currency="CNY" allowNegative value={values.contributed} onValueChange={(contributed) => onChange({ contributed })} />
        </Field>
        <Field label="基金估值（CNY）" hint="场外基金合计，没有可留空" error={errors.fundValue}>
          <MoneyInput currency="CNY" value={values.fundValue} onValueChange={(fundValue) => onChange({ fundValue })} placeholder="0" />
        </Field>
      </div>
      <fieldset className="min-w-0">
        <legend className="mb-1 text-table font-medium text-fg-2">期初持仓</legend>
        <p className="mb-2 text-table text-fg-3">每行一只标的：代码、数量和单位成本（原币），没有可留空。</p>
        <ul className="flex flex-col gap-2">
          {values.positions.map((row, index) => {
            const currency = tickerCurrency(parseTicker(row.ticker)) ?? 'CNY'
            const rowErrors = [errors[`positions.${index}.ticker`], errors[`positions.${index}.quantity`], errors[`positions.${index}.cost`]]
              .filter(Boolean)
            const errorId = `${base}-row-${index}-error`
            const describe = (field: string) => (errors[`positions.${index}.${field}`] ? errorId : undefined)
            return (
              <li key={index} className="flex flex-col gap-1">
                <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_auto] items-center gap-2">
                  <Input
                    aria-label={`期初持仓 ${index + 1} 代码`}
                    aria-invalid={errors[`positions.${index}.ticker`] ? true : undefined}
                    aria-describedby={describe('ticker')}
                    placeholder="SHA:600519"
                    autoComplete="off"
                    spellCheck={false}
                    value={row.ticker}
                    onChange={(event) => setRow(index, { ticker: event.target.value })}
                  />
                  <NumberInput
                    aria-label={`期初持仓 ${index + 1} 数量`}
                    aria-invalid={errors[`positions.${index}.quantity`] ? true : undefined}
                    aria-describedby={describe('quantity')}
                    placeholder="数量"
                    value={row.quantity}
                    onValueChange={(quantity) => setRow(index, { quantity })}
                  />
                  <MoneyInput
                    aria-label={`期初持仓 ${index + 1} 单位成本`}
                    aria-invalid={errors[`positions.${index}.cost`] ? true : undefined}
                    aria-describedby={describe('cost')}
                    currency={currency}
                    placeholder="成本"
                    value={row.cost}
                    onValueChange={(cost) => setRow(index, { cost })}
                  />
                  <IconButton
                    icon={X}
                    label="删除这一行"
                    size="sm"
                    disabled={values.positions.length === 1}
                    onClick={() => onChange({ positions: values.positions.filter((_, i) => i !== index) })}
                  />
                </div>
                {rowErrors.length > 0 && <p id={errorId} className={cx('break-words text-table text-warn', numeric)}>{rowErrors.join('；')}</p>}
              </li>
            )
          })}
        </ul>
        <Button
          size="sm"
          variant="ghost"
          icon={Plus}
          className="mt-2"
          disabled={values.positions.length >= MAX_ROWS}
          onClick={() => onChange({ positions: [...values.positions, { ticker: '', quantity: '', cost: '' }] })}
        >
          添加一行
        </Button>
      </fieldset>
    </div>
  )
}
