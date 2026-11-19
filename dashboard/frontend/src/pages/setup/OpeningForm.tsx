// 从零开始: the first account and its opening balance, with the ledger's own opening fields (OpeningFields),
// previewed and confirmed through the standard review (ProposalReview). Nothing is booked before 确认入账.
// 累计净投入 can take the cost-based suggestion, labelled, when every amount is in CNY.
import { useMemo, useState, type FormEvent } from 'react'
import { Eye } from 'lucide-react'
import { Button, DateInput, Field, InlineAlert, Input, LongValue, Panel, cx, numeric } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { discardLedgerProposal, proposeEvents } from '@/services/ledger'
import { zonedToday } from '@/lib/entryDefaults'
import { buildEvents, emptyValues, validateEntry, type EntryErrors, type EntryValues } from '@/features/ledger/entry'
import { ledgerErrorText } from '@/features/ledger/review'
import type { Proposal } from '@/features/ledger/types'
import { decSign } from '@/features/ledger/decimal'
import { ESTIMATE_LABEL, draftValueText, openingEstimate, openingHasForeign } from '@/features/setup/opening'
import { useAppMode } from '@/components/shell/useAppMode'
import { OpeningFields } from '@/components/ledger/OpeningFields'
import { ProposalReview } from '@/components/ledger/ProposalReview'
import { useLedger } from '@/components/ledger/useLedger'

export function OpeningForm({ onConfirmed }: { onConfirmed: () => void }) {
  const { readOnlyReason } = useAppMode()
  const ledger = useLedger()
  const accounts = useMemo(() => Object.keys(ledger.data?.accounts ?? {}), [ledger.data])
  const [values, setValues] = useState<EntryValues>(() => emptyValues({
    kind: 'opening', account: accounts.length ? '' : '主账户', date: zonedToday(),
  }))
  const [errors, setErrors] = useState<EntryErrors>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [booked, setBooked] = useState(false)
  const estimate = openingEstimate(values)
  const foreign = openingHasForeign(values)
  const patch = (next: Partial<EntryValues>) => setValues((current) => ({ ...current, ...next }))
  const context = { accounts, ticker: null, held: null, feeEstimate: null }

  const preview = async (event: FormEvent) => {
    event.preventDefault()
    const found = validateEntry(values, context)
    setErrors(found)
    if (Object.keys(found).length) return
    setBusy(true)
    setFailure(null)
    try {
      // A preview made while typing, like 记一笔's: left unconfirmed, it expires instead of waiting in 待确认.
      setProposal(await proposeEvents(buildEvents(values, context), { transient: true }))
    } catch (exc) {
      setFailure(ledgerErrorText(getApiErrorMessage(exc)))
    } finally {
      setBusy(false)
    }
  }

  /** 返回修改 drops the preview, so it does not wait in 待确认. */
  const back = () => {
    if (proposal && !booked) void discardLedgerProposal(proposal.id).catch(() => undefined)
    setProposal(null)
  }

  if (proposal) {
    return (
      <Panel title={booked ? '已入账' : '核对期初余额'} description={booked ? undefined : '确认后账本启用；之后的买卖、分红和出入金都从记一笔录入。'}>
        <ProposalReview
          proposal={proposal}
          actions
          onBack={booked ? undefined : back}
          onConfirmed={() => {
            setBooked(true)
            onConfirmed()
          }}
        />
      </Panel>
    )
  }

  return (
    <Panel title="新建账户" description="账户名、期初日期，以及那一天的现金、持仓和累计净投入。">
      <form onSubmit={(event) => void preview(event)} noValidate className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="账户名称" required error={errors.account}>
            <Input value={values.account} maxLength={240} onChange={(event) => patch({ account: event.target.value })} />
          </Field>
          <Field label="期初日期" required hint="之后的交易记在这一天之后" error={errors.date}>
            <DateInput value={values.date} onChange={(event) => patch({ date: event.target.value })} />
          </Field>
        </div>
        <OpeningFields values={values} onChange={patch} errors={errors} />
        {foreign ? (
          <p className="text-table text-fg-2">有港币、美元现金或非人民币持仓时，累计净投入请按实际转入的人民币本金填写。</p>
        ) : estimate !== null && decSign(estimate) !== 0 && (
          <div className={cx('flex flex-wrap items-center gap-x-3 gap-y-1 text-table text-fg-2', numeric)}>
            <span>累计净投入建议：<LongValue value={draftValueText('money', estimate)} />（{ESTIMATE_LABEL}）</span>
            <Button size="sm" variant="ghost" onClick={() => patch({ contributed: estimate })}>填入建议值</Button>
          </div>
        )}
        {failure && <InlineAlert tone="warn" title="无法预览">{failure}</InlineAlert>}
        <div>
          <Button type="submit" variant="primary" icon={Eye} loading={busy} disabledReason={readOnlyReason}>预览入账</Button>
        </div>
      </form>
    </Panel>
  )
}
