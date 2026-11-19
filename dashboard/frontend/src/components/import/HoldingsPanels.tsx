// A holdings statement. For a new account: the opening values the file does not contain (date, cash per
// currency, 累计净投入), asked for when the backend needs them, with the file's own cash lines and the
// backend's estimate beside them. For an existing account: the reconciliation table, each difference with
// 记一笔 prefilled (quantity, price left blank); corrective trades are never proposed automatically.
import { useState } from 'react'
import { Plus } from 'lucide-react'
import { Badge, Button, DataTable, DateInput, Field, InlineAlert, LongValue, MoneyInput, cx, numeric, type Column } from '@/components/ui'
import { fmtLong, fmtMoney, fmtNumber, fmtPrice, fmtQty, longLine, type Shown } from '@/lib/format'
import { useShell } from '@/components/shell/shellStore'
import type { HoldingsReconRow, HoldingsView, ImportPatch, OpeningValues } from '@/features/import/types'

const CURRENCIES = ['CNY', 'HKD', 'USD'] as const

export interface OpeningFormProps {
  holdings: HoldingsView
  onPatch: (body: ImportPatch) => Promise<unknown> | void
  busy: boolean
  disabledReason?: string
}

export function OpeningForm({ holdings, onPatch, busy, disabledReason }: OpeningFormProps) {
  const stored = holdings.opening ?? {}
  const [date, setDate] = useState(stored.date ?? '')
  const [cash, setCash] = useState<Record<string, string>>(() => ({ ...(stored.cash_balances ?? {}) }))
  const [contributed, setContributed] = useState(stored.contributed_cny ?? '')
  const [fund, setFund] = useState(stored.fund_cny ?? '')
  const [error, setError] = useState<string | null>(null)
  const suggested = holdings.suggested_contributed_cny

  const save = () => {
    if (!date) return setError('请填写期初日期')
    if (!cash.CNY) return setError('请填写人民币现金（没有就填 0）')
    if (!contributed) return setError('请填写累计净投入')
    setError(null)
    const balances: Record<string, string> = {}
    for (const unit of CURRENCIES) if (cash[unit]) balances[unit] = cash[unit]
    const opening: OpeningValues = { date, cash_balances: balances, contributed_cny: contributed }
    if (fund) opening.fund_cny = fund
    void onPatch({ opening })
  }

  return (
    <section aria-label="期初" className="flex flex-col gap-3 rounded-md border border-line px-3 py-3">
      <div>
        <h3 className="text-body font-medium text-fg">期初</h3>
        <p className="text-table text-fg-3">持仓文件里没有期初日期、现金和累计净投入：填写后才能生成期初入账预览。</p>
      </div>
      {holdings.summary.length > 0 && (
        <p className={cx('text-table text-fg-2', numeric)}>
          文件里的资金信息：{holdings.summary.map((item) => `${item.label} ${fmtNumber(item.value, { exact: true })}`).join(' · ')}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="期初日期" required>
          <DateInput value={date} disabled={Boolean(disabledReason)} onChange={(event) => setDate(event.target.value)} />
        </Field>
        {CURRENCIES.map((unit) => (
          <Field key={unit} label={`${unit} 现金`} required={unit === 'CNY'} hint={unit === 'CNY' ? '逆回购的资金也计入现金' : '选填'}>
            <MoneyInput currency={unit} value={cash[unit] ?? ''} disabled={Boolean(disabledReason)}
              onValueChange={(value) => setCash((current) => ({ ...current, [unit]: value }))} />
          </Field>
        ))}
        <Field label="累计净投入（人民币）" required hint={suggested?.label}>
          <MoneyInput currency="CNY" value={contributed} disabled={Boolean(disabledReason)} onValueChange={setContributed} />
        </Field>
        <Field label="基金市值（人民币）" hint="选填">
          <MoneyInput currency="CNY" value={fund} disabled={Boolean(disabledReason)} onValueChange={setFund} />
        </Field>
      </div>
      {suggested?.value && (
        <p className={cx('flex flex-wrap items-center gap-2 text-table text-fg-2', numeric)}>
          累计净投入参考估算（现金＋持仓成本＋基金估值）：{fmtMoney(suggested.value, 'CNY', { exact: true })}
          <Button size="sm" variant="ghost" disabled={Boolean(disabledReason)} onClick={() => setContributed(suggested.value ?? '')}>填入</Button>
        </p>
      )}
      <InlineAlert tone="info">{holdings.cost_note}</InlineAlert>
      {error && <p role="alert" className="text-table text-warn">{error}</p>}
      <Button className="self-start" variant="secondary" loading={busy} disabledReason={disabledReason} onClick={save}>保存期初信息</Button>
    </section>
  )
}

export interface HoldingsReconcileProps {
  holdings: HoldingsView
  disabledReason?: string
}

export function HoldingsReconcile({ holdings, disabledReason }: HoldingsReconcileProps) {
  const openEntry = useShell((state) => state.openEntry)
  const rows = holdings.reconciliation ?? []
  const columns: Column<HoldingsReconRow>[] = [
    {
      key: 'name', header: '标的',
      render: (row) => (
        <span className="whitespace-nowrap">
          <span className="mr-1.5 text-fg">{row.name}</span>
          <span className="text-fg-3">{row.ticker ?? `第 ${row.row} 行`}</span>
        </span>
      ),
    },
    { key: 'file', header: '文件数量', numeric: true, render: (row) => (row.file_quantity === null ? '不可用' : fmtQty(row.file_quantity)) },
    { key: 'ledger', header: '账本数量', numeric: true, render: (row) => (row.ledger_quantity === null ? '不可用' : fmtQty(row.ledger_quantity)) },
    {
      key: 'status', header: '结果',
      render: (row) => (
        <span className="inline-flex items-center gap-1.5">
          <Badge tone={row.status === '一致' ? 'neutral' : row.status === '未核对' ? 'neutral' : 'warn'}>{row.status === '差额' ? '不一致' : row.status}</Badge>
          {row.status !== '一致' && <span className={cx('text-table', row.status === '未核对' ? 'text-fg-3' : 'text-warn')}>{row.label}</span>}
        </span>
      ),
    },
    {
      key: 'cost', header: '成本（文件 / 账本）', numeric: true, hideBelow: 'md',
      // The ledger's average cost can repeat (cost + fees ÷ quantity): ≈ to 8 places, the full value on hover.
      render: (row) => {
        const cost = (value: string | null): Shown => (value ? fmtLong(value, (v) => fmtPrice(v, 'CNY', 'stock', { exact: true })) : { text: '不可用' })
        const file = cost(row.file_cost)
        const ledger = cost(row.ledger_cost)
        return <LongValue value={longLine((pick) => `${pick(file)} / ${pick(ledger)}`)} />
      },
    },
    {
      key: 'entry', header: <span className="sr-only">操作</span>, label: '操作', align: 'right',
      render: (row) => (row.entry ? (
        <Button size="sm" icon={Plus} disabledReason={disabledReason}
          onClick={() => openEntry({ kind: row.entry!.kind, account: row.entry!.account, ticker: row.entry!.ticker, name: row.name, quantity: row.entry!.quantity, source: 'import' })}>
          记一笔
        </Button>
      ) : null),
    },
  ]
  return (
    <section aria-label="持仓对账" className="flex flex-col gap-2">
      <h3 className="text-body font-medium text-fg">持仓对账</h3>
      <p className="text-table text-fg-3">这个账户已有期初：持仓文件只用来核对，差异请用“记一笔”逐笔补录（价格按成交回报填写）。</p>
      <DataTable caption="持仓对账" columns={columns} rows={rows} rowKey={(row) => `${row.row}:${row.ticker ?? ''}`} mobile="cards" stickyHeader={false}
        renderRowExtra={(row) => (row.cost_note ? <span className="text-fg-3">{row.cost_note}</span> : null)} />
      <p className="text-table text-fg-3">{holdings.cost_note}</p>
    </section>
  )
}
