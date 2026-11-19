// One line per account, no cards: 长期账户 · CNY 20,000.00 · USD 949.00 · 净投入 ¥81,000.00 · 1 项持仓.
// Confirmed ledger figures at cost, with the ledger's digits (¥100.004 stays so; more than 8 places is ≈ rounded
// to 8 with the full value on hover); live values are on 今日. A negative balance is amber. A long account
// name wraps instead of widening the page.
import { Fragment, type ReactNode } from 'react'
import { LongValue, cx, numeric } from '@/components/ui'
import { fmtLong, fmtMoney, fmtNumber, toneOf } from '@/lib/format'
import { cashList } from '@/features/ledger/events'
import type { LedgerAccount } from '@/features/ledger/types'

const Dot = () => <span aria-hidden className="text-fg-3">·</span>

export function AccountLine({ name, account }: { name: string; account: LedgerAccount }) {
  const positions = Object.keys(account.positions).length
  const fund = account.fund_cny && toneOf(account.fund_cny) !== 'flat'
  const parts: { key: string; node: ReactNode }[] = [
    ...cashList(account).map(([unit, value]) => ({
      key: unit,
      node: (
        <span className={toneOf(value) === 'down' ? 'text-warn' : undefined}>
          {unit} <LongValue value={fmtLong(value, (v) => fmtNumber(v, { exact: true }))} />
        </span>
      ),
    })),
    { key: 'contributed', node: <>净投入 <LongValue value={fmtLong(account.contributed_cny, (v) => fmtMoney(v, 'CNY', { exact: true }))} /></> },
    ...(fund ? [{ key: 'fund', node: <>基金 <LongValue value={fmtLong(account.fund_cny, (v) => fmtMoney(v, 'CNY', { exact: true }))} /></> }] : []),
    { key: 'positions', node: <>{positions} 项持仓</> },
  ]
  return (
    <p className={cx('flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-table text-fg-2', numeric)}>
      <span className="min-w-0 max-w-full break-all font-medium text-fg">{name}</span>
      {parts.map((part) => (
        <Fragment key={part.key}>
          <Dot />
          <span className="whitespace-nowrap">{part.node}</span>
        </Fragment>
      ))}
    </p>
  )
}

export function AccountLines({ accounts, className }: { accounts: Record<string, LedgerAccount>; className?: string }) {
  return (
    <div aria-label="账户概况" role="group" className={cx('flex min-w-0 flex-col gap-1', className)}>
      {Object.entries(accounts).map(([name, account]) => <AccountLine key={name} name={name} account={account} />)}
    </div>
  )
}
