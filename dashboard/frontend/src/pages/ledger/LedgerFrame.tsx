// What every 账本 tab shares: one summary line per account, 备份 / 导出 / 记一笔, the previews waiting for
// confirmation, and the review of `?proposal=<id>` (Today's 核对方案 links land here). Before anything was
// ever confirmed it shows how to start: a new account, or the opening balances of the old holdings file. A
// ledger whose accounts were all reversed away still shows its history (and how to start again).
import { useEffect, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { BookOpen, Download, FileJson, Plus } from 'lucide-react'
import { Button, DateInput, EmptyState, Field, InlineAlert, MoneyInput, Panel, Skeleton, TableSkeleton } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { BACKUP_URL, EXPORT_URL, previewOpening } from '@/services/ledger'
import { isDecimal } from '@/features/ledger/decimal'
import { contributionsAsked, ledgerErrorText, missingContributions } from '@/features/ledger/review'
import type { LedgerSnapshot } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useShell } from '@/components/shell/shellStore'
import { AccountLines } from '@/components/ledger/AccountLines'
import { PendingList } from '@/components/ledger/PendingList'
import { ProposalDialog } from '@/components/ledger/ProposalDialog'
import { download, useLedger, useLedgerCalendar } from '@/components/ledger/useLedger'

/** Open and close the review of a proposal through the URL (`?proposal=<id>`), so a link or a reload shows it. */
export function useProposalParam(): [string | null, (id: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const id = params.get('proposal')
  const set = (next: string | null) => {
    setParams((current) => {
      const copy = new URLSearchParams(current)
      if (next) copy.set('proposal', next)
      else copy.delete('proposal')
      return copy
    }, { replace: !next })
  }
  return [id || null, set]
}

function Start({ onPreview }: { onPreview: (id: string) => void }) {
  const { readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  // The backend's today until the person picks a date (none while the backend cannot say).
  const today = useLedgerCalendar().calendar?.today ?? ''
  const [date, setDate] = useState(today)
  const [picked, setPicked] = useState(false)
  useEffect(() => {
    if (!picked) setDate(today)
  }, [today, picked])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The accounts whose 累计净投入 the holdings file of that date lacks: asked for, never taken as 0.
  const [prompt, setPrompt] = useState<{ date: string; accounts: string[] } | null>(null)
  const asked = contributionsAsked(prompt, date)
  const [contributions, setContributions] = useState<Record<string, string>>({})
  const unfilled = asked.some((name) => !isDecimal(contributions[name] ?? ''))
  const preview = async () => {
    setBusy(true)
    setError(null)
    try {
      const given = asked.length ? Object.fromEntries(asked.map((name) => [name, contributions[name]])) : undefined
      onPreview((await previewOpening(date, given)).id)
    } catch (exc) {
      const missing = missingContributions(exc)
      if (missing.length) setPrompt({ date, accounts: missing })
      setError(ledgerErrorText(getApiErrorMessage(exc)))
    } finally {
      setBusy(false)
    }
  }
  const blocked = readOnlyReason ?? (!date ? '请先选择期初日期' : unfilled ? '请填写每个账户的累计净投入' : undefined)
  return (
    <Panel>
      <EmptyState
        icon={BookOpen}
        title="还没有账户"
        description="先记下每个账户的期初现金、持仓和累计净投入；之后的买卖、分红和出入金都从这里记。"
        action={(
          <Button variant="primary" icon={Plus} disabledReason={readOnlyReason} onClick={() => openEntry({ kind: 'opening' })}>
            新建账户
          </Button>
        )}
      />
      <div className="mx-auto flex max-w-md flex-col gap-3 border-t border-line pt-4">
        <p className="text-table text-fg-2">已有旧的持仓记录？按某一天的持仓文件生成期初余额，核对后再入账。</p>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="期初日期" className="w-44">
            <DateInput value={date} onChange={(event) => { setPicked(true); setDate(event.target.value); setError(null) }} />
          </Field>
          <Button loading={busy} disabledReason={blocked} onClick={() => void preview()}>预览已有持仓</Button>
        </div>
        {error && <InlineAlert tone="warn">{error}</InlineAlert>}
        {asked.length > 0 && (
          <div className="grid gap-3 sm:grid-cols-2">
            {asked.map((name) => (
              <Field key={name} label={`${name} 累计净投入（CNY）`} required hint="历年转入减转出的本金合计">
                <MoneyInput
                  currency="CNY"
                  allowNegative
                  value={contributions[name] ?? ''}
                  onValueChange={(value) => setContributions((current) => ({ ...current, [name]: value }))}
                />
              </Field>
            ))}
          </div>
        )}
      </div>
    </Panel>
  )
}

export interface LedgerFrameProps {
  /** Screen-reader title of the tab ("账本 · 流水"). */
  title: string
  /** More page actions, before 记一笔 (the 流水 tab's 导入). */
  actions?: ReactNode
  children: (ledger: LedgerSnapshot, openProposal: (id: string) => void) => ReactNode
}

export function LedgerFrame({ title, actions, children }: LedgerFrameProps) {
  const ledger = useLedger()
  const { readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  const [proposalId, setProposal] = useProposalParam()
  const data = ledger.data
  const empty = data !== undefined && Object.keys(data.accounts).length === 0
  // Confirmed events exist (revision > 0) even when no account is left: the history stays on screen.
  const history = data !== undefined && data.revision > 0

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 text-body text-fg">
      <h1 className="sr-only">{title}</h1>
      {ledger.isPending && <Skeleton lines={2} w={360} />}
      {data && (!empty || history) && (
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          {/* At least 20rem (the whole row on a phone): the buttons wrap below instead of squeezing each figure onto its own line. */}
          <AccountLines accounts={data.accounts} className="min-w-[min(100%,20rem)] flex-1" />
          <div className="flex flex-wrap items-center gap-2">
            <Button icon={Download} onClick={() => download(BACKUP_URL)} title="下载账本数据库备份（ledger-backup.sqlite3）">备份</Button>
            <Button icon={FileJson} onClick={() => download(EXPORT_URL)} title="导出全部流水（transactions.json）">导出</Button>
            {actions}
            <Button
              variant="primary"
              icon={Plus}
              onClick={() => openEntry()}
              disabledReason={readOnlyReason}
              tipAlign="end"
              aria-keyshortcuts="N"
            >
              记一笔
            </Button>
          </div>
        </div>
      )}

      {ledger.isError && !data && (
        <InlineAlert tone="warn" title="账本读取失败" action={<Button size="sm" onClick={() => void ledger.refetch()}>重试</Button>}>
          {getApiErrorMessage(ledger.error)}
        </InlineAlert>
      )}
      {ledger.isError && data && (
        <InlineAlert tone="warn" action={<Button size="sm" onClick={() => void ledger.refetch()}>重试</Button>}>
          暂时无法刷新账本，显示的是上一次读取的内容。
        </InlineAlert>
      )}

      {ledger.isPending && <Panel padding="none"><TableSkeleton rows={6} cols={6} label="正在读取账本" /></Panel>}
      {data && <PendingList ledger={data} onReview={setProposal} />}
      {empty && <Start onPreview={setProposal} />}
      {data && (!empty || history) && children(data, setProposal)}

      <ProposalDialog proposalId={proposalId} onClose={() => setProposal(null)} />
    </div>
  )
}
