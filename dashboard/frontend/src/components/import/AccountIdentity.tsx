// 账户识别: which 资金账号 / 股东代码 values of the file belong to the ledger account. Rows that do not positively
// match the choice stay 需处理; how many (the backend's `rows_kept_out`) and its reasons for them (from the first
// page of 需处理 rows) are shown under the choice. The choice is a PATCH `account_filter`, saved with the
// mapping for the next file of this layout.
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Checkbox, InlineAlert, cx, numeric } from '@/components/ui'
import { ACCOUNT_SOURCE_LABEL, acceptedValues, accountFilter, accountReviewRows, needsAccountChoice } from '@/features/import/flow'
import type { ImportAccounts, ImportPatch, ImportRow } from '@/features/import/types'

export interface AccountIdentityProps {
  account: string
  accounts: ImportAccounts
  /** The first page of 需处理 rows: the backend's reasons for the rows kept out (the count is `rows_kept_out`). */
  reviewRows: ImportRow[] | undefined
  onPatch: (body: ImportPatch) => void
  busy: boolean
  disabledReason?: string
}

const STATE_LABEL: Record<string, string> = { accepted: '属于这个账户', excluded: '不属于这个账户' }

export function AccountIdentity({ account, accounts, reviewRows, onPatch, busy, disabledReason }: AccountIdentityProps) {
  const current = useMemo(() => acceptedValues(accounts), [accounts])
  const [chosen, setChosen] = useState<string[]>(current)
  const currentKey = current.join('\u0000')
  useEffect(() => setChosen(currentKey ? currentKey.split('\u0000') : []), [currentKey])
  const dominant = needsAccountChoice(accounts)
  const explicit = accounts.source === 'chosen'
  const changed = [...chosen].sort().join('\u0000') !== [...current].sort().join('\u0000')
  const kept = accountReviewRows(accounts, reviewRows)
  // Rows the account's memory (or a saved choice) keeps out: confirming makes the choice explicit and remembered.
  const unsettled = !explicit && !dominant && kept.count > 0
  const toggle = (value: string, on: boolean) => setChosen((list) => (on ? [...list, value] : list.filter((item) => item !== value)))
  const blocked = busy || Boolean(disabledReason)
  const showBlank = accounts.rows_without_account > 0 || accounts.accept_rows_without_account

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-table">
        <Badge tone={dominant ? 'warn' : 'neutral'}>{ACCOUNT_SOURCE_LABEL[accounts.source] ?? accounts.source}</Badge>
        {accounts.summary && <span className={cx('text-fg-2', numeric)}>{accounts.summary}</span>}
      </div>
      {accounts.columns.map((column) => (
        <fieldset key={column.dimension} className="flex min-w-0 flex-col gap-1.5">
          <legend className="mb-1 text-table text-fg-2">{column.label}</legend>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            {column.values.map((value) => (
              <Checkbox
                key={value.value}
                label={<span className={numeric}>{value.value} <span className="text-fg-3">· {value.rows} 行</span></span>}
                checked={chosen.includes(value.value)}
                disabled={blocked}
                onChange={(event) => toggle(value.value, event.target.checked)}
              />
            ))}
          </div>
        </fieldset>
      ))}
      {showBlank && (
        <Checkbox
          label={<span className={numeric}>没有账号的行 <span className="text-fg-3">· {accounts.rows_without_account} 行</span></span>}
          checked={chosen.includes('')}
          disabled={blocked}
          onChange={(event) => toggle('', event.target.checked)}
        />
      )}
      {(dominant || changed || unsettled || chosen.length === 0) && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={dominant || changed || unsettled ? 'primary' : 'secondary'}
            loading={busy}
            disabled={chosen.length === 0 || (!dominant && !changed && !unsettled)}
            disabledReason={disabledReason}
            onClick={() => onPatch({ account_filter: accountFilter(accounts, chosen) })}
          >
            确认：勾选的属于“{account}”
          </Button>
          {(accounts.source === 'chosen' || accounts.source === 'saved') && (
            <Button size="sm" variant="ghost" disabled={busy} disabledReason={disabledReason} onClick={() => onPatch({ account_filter: null })}>
              恢复默认
            </Button>
          )}
          {chosen.length === 0 && <span className="text-table text-warn">至少勾选一个</span>}
        </div>
      )}
      {!(dominant || changed || unsettled || chosen.length === 0) && (accounts.source === 'chosen' || accounts.source === 'saved') && (
        <Button size="sm" variant="ghost" className="self-start" disabled={busy} disabledReason={disabledReason} onClick={() => onPatch({ account_filter: null })}>
          恢复默认
        </Button>
      )}
      {kept.count > 0 && (
        <InlineAlert tone="warn" title={`${kept.count} 行不属于所选账号，会留在“需处理”，不会入账`}>
          {kept.reasons.length > 0 && (
            <ul className="flex flex-col gap-0.5 text-table">
              {kept.reasons.slice(0, 4).map((reason) => (
                <li key={reason.text} className="break-words">{kept.complete ? `${reason.count} 行：` : '例如：'}{reason.text}</li>
              ))}
            </ul>
          )}
        </InlineAlert>
      )}
      {unsettled && (
        <p className="text-table text-fg-2">
          勾选属于“{account}”的账号并确认，这些行才会入账，选择会记住；不属于的，确认后留在“需处理”，或放弃这次导入。
        </p>
      )}
      {accounts.remembered && accounts.remembered.length > 0 && (
        <div className={cx('flex flex-col gap-1 text-table', numeric)}>
          <p className="text-fg-3">以前导入“{account}”时确认过的：</p>
          <ul aria-label="记住的账号" className="flex flex-wrap gap-x-4 gap-y-1">
            {accounts.remembered.map((item) => (
              <li key={`${item.dimension}:${item.value}`} className={item.state === 'excluded' ? 'text-warn' : 'text-fg-2'}>
                {item.label} {item.value}（{STATE_LABEL[item.state] ?? item.state}）
              </li>
            ))}
          </ul>
          <p className="text-fg-3">在上面勾选并确认，就会改为你这次的选择。</p>
        </div>
      )}
    </div>
  )
}
