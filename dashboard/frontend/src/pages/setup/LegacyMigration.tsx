// 迁移旧版持仓: the v3.2 holdings file read as it stands (GET /api/setup/legacy). Every value is shown with
// where it came from; a value that cannot be read is an empty field with the reason, never a guess. The
// answers (or 不迁移这一行) go to the backend, which re-reads the file and makes an ordinary ledger proposal,
// reviewed and confirmed with ProposalReview. The file itself is never changed.
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Eye, FileWarning } from 'lucide-react'
import {
  Button, Checkbox, DateInput, EmptyState, Field, InlineAlert, Input, LongValue, MoneyInput, NumberInput, Panel, Select,
  Skeleton, cx, numeric,
} from '@/components/ui'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { discardLedgerProposal } from '@/services/ledger'
import { fetchLegacyDraft, legacyDraftKey, proposeLegacy } from '@/services/setup'
import { ledgerErrorText } from '@/features/ledger/review'
import { tickerCurrency } from '@/features/ledger/entry'
import type { Proposal } from '@/features/ledger/types'
import {
  ESTIMATE_LABEL, accountEstimate, answerErrors, draftSummary, draftValueText, emptyAnswers, legacyControls, legacySources,
  proposalBody, skippableRows, type LegacyAnswers,
} from '@/features/setup/opening'
import type { DraftAccount, DraftPosition, DraftValue, LegacyDraft } from '@/features/setup/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { ProposalReview } from '@/components/ledger/ProposalReview'
import { useLedger } from '@/components/ledger/useLedger'

type Answering = {
  answers: LegacyAnswers
  errors: Record<string, string>
  setFill: (key: string, value: string) => void
}

function Problem({ item, error }: { item: DraftValue; error?: string }) {
  return (
    <p className="flex flex-col text-table">
      {error && <span className="text-warn">{error}</span>}
      <span className="text-warn">{item.problem}</span>
    </p>
  )
}

/** A value read from the file, or the field to type it in. */
function ValueCell({ item, label, kind, currency, answering, skipped }: {
  item: DraftValue
  label: string
  kind: 'money' | 'quantity' | 'price' | 'ticker'
  currency?: string
  answering: Answering
  skipped?: boolean
}) {
  if (item.value !== null) {
    return (
      <span className="flex min-w-0 flex-col">
        <LongValue value={draftValueText(kind, item.value, currency ?? 'CNY')} className={cx('break-words text-fg', numeric)} />
        {item.note && <span className="text-caption text-fg-3">{item.note}</span>}
      </span>
    )
  }
  if (skipped) return <span className="text-fg-3">不迁移</span>
  const value = answering.answers.fills[item.key] ?? ''
  const error = answering.errors[item.key]
  const common = {
    'aria-label': `${label}（需要填写）`,
    'aria-invalid': error ? true : undefined,
    value,
  }
  return (
    <span className="flex min-w-0 flex-col gap-1">
      {kind === 'ticker' ? (
        <Input {...common} placeholder={item.choices?.[0] ?? 'SHA:600519'} autoComplete="off" spellCheck={false}
          onChange={(event) => answering.setFill(item.key, event.target.value)} />
      ) : kind === 'quantity' ? (
        <NumberInput {...common} placeholder="数量" onValueChange={(next) => answering.setFill(item.key, next)} />
      ) : (
        <MoneyInput {...common} currency={currency ?? 'CNY'} allowNegative={kind === 'money'} placeholder="需要填写"
          onValueChange={(next) => answering.setFill(item.key, next)} />
      )}
      {kind === 'ticker' && item.choices && item.choices.length > 0 && (
        <span className="flex flex-wrap gap-1">
          {item.choices.map((choice) => (
            <Button key={choice} size="sm" variant="ghost" onClick={() => answering.setFill(item.key, choice)}>{choice}</Button>
          ))}
        </span>
      )}
      <Problem item={item} error={error} />
    </span>
  )
}

function PositionRow({ row, account, answering, skippable, onSkip }: {
  row: DraftPosition; account: string; answering: Answering; skippable: boolean; onSkip: (skip: boolean) => void
}) {
  const skipped = answering.answers.skip.includes(row.key)
  const ticker = row.ticker.value ?? answering.answers.fills[row.ticker.key] ?? null
  const currency = tickerCurrency(ticker) ?? 'CNY'
  const subject = row.name ?? row.ticker.value ?? `第 ${row.index + 1} 行`
  return (
    <li className={cx('grid gap-x-4 gap-y-2 py-3 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]', skipped && 'opacity-70')}>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="font-medium text-fg">{row.name ?? '（原文件没有名称）'}</span>
        <ValueCell item={row.ticker} label={`${account} ${subject} 代码`} kind="ticker" answering={answering} skipped={skipped} />
        {skippable && <Checkbox checked={skipped} onChange={(event) => onSkip(event.target.checked)} label="不迁移这一行" />}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-caption text-fg-3">数量</span>
        <ValueCell item={row.quantity} label={`${account} ${subject} 数量`} kind="quantity" answering={answering} skipped={skipped} />
      </div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-caption text-fg-3">成本价（{currency}）</span>
        <ValueCell item={row.cost_price} label={`${account} ${subject} 成本价`} kind="price" currency={currency} answering={answering} skipped={skipped} />
      </div>
    </li>
  )
}

function AccountBlock({ account, answering, skippable, onSkip, onAccountName, estimateOf }: {
  account: DraftAccount
  answering: Answering
  skippable: Set<string>
  onSkip: (row: string, skip: boolean) => void
  onAccountName: (name: string) => void
  estimateOf: (account: DraftAccount) => string | null
}) {
  const estimate = account.contributed_cny.value === null ? estimateOf(account) : null
  return (
    <section aria-label={`旧账户 ${account.account}`} className="flex min-w-0 flex-col gap-3 border-t border-line pt-4 first:border-t-0 first:pt-0">
      {account.account_editable ? (
        <Field label="账户名称" required hint="旧版持仓页只有一个账户，给它起个名字" error={answering.errors.account} className="max-w-xs">
          <Input value={answering.answers.accountName} maxLength={240} onChange={(event) => onAccountName(event.target.value)} />
        </Field>
      ) : (
        <h3 className="text-title text-fg">{account.account}</h3>
      )}
      {account.exists ? (
        <p className="text-table text-fg-2">账本中已有这个账户，不再迁移。</p>
      ) : (
        <>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-3">
            {account.cash.map((item) => (
              <div key={item.key} className="min-w-0">
                <dt className="text-caption text-fg-3">{item.currency} 现金</dt>
                <dd><ValueCell item={item} label={`${account.account} ${item.currency} 现金`} kind="money" currency={item.currency} answering={answering} /></dd>
              </div>
            ))}
            <div className="min-w-0">
              <dt className="text-caption text-fg-3">累计净投入（CNY）</dt>
              <dd className="flex flex-col gap-1">
                <ValueCell item={account.contributed_cny} label={`${account.account} 累计净投入`} kind="money" answering={answering} />
                {estimate !== null && (
                  <span className={cx('flex flex-wrap items-center gap-x-2 text-table text-fg-2', numeric)}>
                    建议 <LongValue value={draftValueText('money', estimate)} />（{ESTIMATE_LABEL}）
                    <Button size="sm" variant="ghost" onClick={() => answering.setFill(account.contributed_cny.key, estimate)}>填入</Button>
                  </span>
                )}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-caption text-fg-3">基金估值（CNY）</dt>
              <dd><ValueCell item={account.fund_cny} label={`${account.account} 基金估值`} kind="money" answering={answering} /></dd>
            </div>
          </dl>
          {account.positions.length > 0 ? (
            <ul aria-label={`${account.account} 的持仓`} className="flex flex-col divide-y divide-line">
              {account.positions.map((row) => (
                <PositionRow key={row.key} row={row} account={account.account} answering={answering}
                  skippable={skippable.has(row.key)} onSkip={(skip) => onSkip(row.key, skip)} />
              ))}
            </ul>
          ) : (
            <p className="text-table text-fg-3">没有持仓。</p>
          )}
        </>
      )}
    </section>
  )
}

function DraftView({ draft, onPreview }: { draft: LegacyDraft; onPreview: (proposal: Proposal) => void }) {
  const { readOnlyReason } = useAppMode()
  const ledger = useLedger()
  const [answers, setAnswers] = useState<LegacyAnswers>(() => emptyAnswers(draft))
  const [shown, setShown] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const skippable = useMemo(() => skippableRows(draft), [draft])
  const accounts = Object.keys(ledger.data?.accounts ?? {})
  const answering: Answering = {
    answers,
    errors: shown,
    setFill: (key, value) => setAnswers((current) => ({ ...current, fills: { ...current.fills, [key]: value } })),
  }
  const submit = async () => {
    const found = answerErrors(draft, answers, accounts)
    setShown(found)
    if (Object.keys(found).length) return
    setBusy(true)
    setFailure(null)
    try {
      onPreview(await proposeLegacy(proposalBody(draft, answers)))
    } catch (exc) {
      setFailure(ledgerErrorText(getApiErrorMessage(exc)))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-col gap-4">
      <p className={cx('text-table text-fg-2', numeric)}>
        读取 {draft.file}{draft.file_date ? `（${draft.file_date}）` : ''} · {draftSummary(draft)}。原文件不会被修改。
      </p>
      {draft.needs_input.length > 0 && (
        <InlineAlert tone="warn" title={`${draft.needs_input.length} 项无法从文件读取`}>
          请按券商记录填写，或不迁移那一行；不会按估计值代填。
        </InlineAlert>
      )}
      {draft.accounts.map((account) => (
        <AccountBlock
          key={account.key}
          account={account}
          answering={answering}
          skippable={skippable}
          onSkip={(row, skip) => setAnswers((current) => ({
            ...current, skip: skip ? [...current.skip, row] : current.skip.filter((key) => key !== row),
          }))}
          onAccountName={(accountName) => setAnswers((current) => ({ ...current, accountName }))}
          estimateOf={(account) => accountEstimate(account, answers)}
        />
      ))}
      {draft.skipped.length > 0 && (
        <p className="text-table text-fg-3">
          不迁移：{draft.skipped.map((row) => `${row.account} ${row.name ?? row.ticker ?? ''}（${row.reason}）`).join('；')}
        </p>
      )}
      {failure && <InlineAlert tone="warn" title="无法生成预览">{failure}</InlineAlert>}
      <div>
        <Button variant="primary" icon={Eye} loading={busy} disabledReason={readOnlyReason} onClick={() => void submit()}>
          生成入账预览
        </Button>
      </div>
    </div>
  )
}

export function LegacyMigration({ onConfirmed }: { onConfirmed: () => void }) {
  const [source, setSource] = useState<'holdings' | 'portfolio' | null>(null)
  const [date, setDate] = useState<string | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [booked, setBooked] = useState(false)
  const draft = useQuery({ queryKey: legacyDraftKey(source, date), queryFn: () => fetchLegacyDraft(source, date), retry: false })
  const data = draft.data
  // The last draft that loaded: its sources and source keep the controls in place while another date loads or fails.
  const [last, setLast] = useState<LegacyDraft | null>(null)
  if (data && data !== last) setLast(data)
  const known = data ?? last

  if (proposal) {
    return (
      <Panel title={booked ? '已入账' : '核对迁移'} description={booked ? '旧文件保持不变，之后不再读取。' : '确认后账本启用；旧文件不会被修改。'}>
        <ProposalReview
          proposal={proposal}
          actions
          onBack={booked ? undefined : () => {
            void discardLedgerProposal(proposal.id).catch(() => undefined)
            setProposal(null)
          }}
          onConfirmed={() => {
            setBooked(true)
            onConfirmed()
          }}
        />
      </Panel>
    )
  }

  let body
  if (draft.isPending) {
    body = <Skeleton lines={4} />
  } else if (draft.isError && httpStatus(draft.error) === 404) {
    body = <EmptyState icon={FileWarning} title="没有发现旧版持仓文件" description="可以从零开始新建账户，或导入券商文件。" />
  } else if (draft.isError) {
    body = (
      <InlineAlert tone="warn" title="读不了旧持仓文件" action={<Button size="sm" onClick={() => void draft.refetch()}>重试</Button>}>
        {getApiErrorMessage(draft.error)}
      </InlineAlert>
    )
  } else if (data) {
    body = <DraftView key={`${data.token}:${data.date}`} draft={data} onPreview={setProposal} />
  }

  const sources = known ? legacySources(known) : []
  const shownSource = source ?? known?.source ?? null
  const controls = legacyControls({
    date, loaded: data, last, failed: draft.isError, missing: draft.isError && httpStatus(draft.error) === 404,
  })
  return (
    <Panel title="迁移旧版持仓" description="把 v3.2 的持仓文件变成期初余额：先预览，核对后入账。">
      <div className="flex flex-col gap-4">
        {controls.show && (
          <div className="flex flex-wrap items-end gap-3">
            {sources.length > 1 && (
              <Field label="旧文件" className="w-56">
                <Select
                  value={shownSource ?? ''}
                  onChange={(event) => {
                    const next = event.target.value as 'holdings' | 'portfolio'
                    setSource(next)
                    setDate(null)
                  }}
                  options={sources.map((item) => ({ value: item.source, label: item.source === 'holdings' ? `每日持仓（${item.file}）` : '旧版持仓页（portfolio.json）' }))}
                />
              </Field>
            )}
            <Field label="期初日期" hint={shownSource === 'holdings' ? '按这一天或之前最近的持仓文件' : '旧持仓对应的日期'} className="w-48">
              <DateInput value={controls.date} onChange={(event) => event.target.value && setDate(event.target.value)} />
            </Field>
          </div>
        )}
        {body}
      </div>
    </Panel>
  )
}
