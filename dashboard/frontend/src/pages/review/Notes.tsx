// 计划与复盘 › 笔记 (/notes): research notes written for later review, moved here from the ledger page with
// the same API (`/api/ledger/journal`) and behaviour: write the thesis and what would prove it wrong, pick a
// review date; notes are append-only and travel with the ledger backup. Due notes show on 今日. Reading
// them never creates the ledger file or its table (demo included); "due" counts by the backend's today.
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookText } from 'lucide-react'
import {
  Badge, Button, DateInput, EmptyState, Field, InlineAlert, Panel, Skeleton, Textarea, cx, numeric, toast,
} from '@/components/ui'
import { TickerInput } from '@/components/inputs/TickerInput'
import { getApiErrorMessage } from '@/services/api'
import { addJournalEntry, LEDGER_JOURNAL_KEY } from '@/services/ledger'
import { fetchJournal } from '@/services/today'
import { fmtDate } from '@/lib/format'
import { ledgerErrorText } from '@/features/ledger/review'
import { useAppMode } from '@/components/shell/useAppMode'
import { useLedgerCalendar } from '@/components/ledger/useLedger'

const EMPTY = { ticker: '', thesis: '', invalidation: '', review_on: '' }

export default function Notes() {
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const journal = useQuery({ queryKey: LEDGER_JOURNAL_KEY, queryFn: fetchJournal, retry: false })
  const [form, setForm] = useState(EMPTY)
  const [submitted, setSubmitted] = useState(false)
  const today = useLedgerCalendar().calendar?.today ?? null
  const save = useMutation({
    mutationFn: () => addJournalEntry({ ...form, ticker: form.ticker.trim() }),
    onSuccess: async () => {
      setForm(EMPTY)
      setSubmitted(false)
      toast.success('研究记录已保存')
      await client.invalidateQueries({ queryKey: LEDGER_JOURNAL_KEY })
    },
  })
  const errors = submitted ? {
    ticker: form.ticker.trim() ? undefined : '请从列表中选择标的（可输入代码或名称搜索）。',
    thesis: form.thesis.trim() ? undefined : '请写下投资逻辑或复盘结论',
    invalidation: form.invalidation.trim() ? undefined : '请写下判断失效的条件',
    review_on: form.review_on ? undefined : '请选择复查日期',
  } : {}
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSubmitted(true)
    if (!form.ticker.trim() || !form.thesis.trim() || !form.invalidation.trim() || !form.review_on) return
    save.mutate()
  }
  const entries = journal.data?.entries ?? []

  return (
    <div className="mx-auto grid max-w-[1600px] gap-6 text-body text-fg lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <h1 className="sr-only">笔记</h1>
      <Panel title="写给未来的自己" description="保留当时的判断，到期复查；笔记随账本一起备份。" className="self-start">
        <form onSubmit={submit} noValidate className="flex flex-col gap-4">
          <Field label="标的" required error={errors.ticker}>
            <TickerInput value={form.ticker} onChange={(ticker) => setForm((f) => ({ ...f, ticker }))} onSelect={(choice) => choice.ticker && setForm((f) => ({ ...f, ticker: choice.ticker! }))} />
          </Field>
          <Field label="投资逻辑 / 本次复盘结论" required error={errors.thesis}>
            <Textarea rows={4} maxLength={4000} value={form.thesis} onChange={(event) => setForm((f) => ({ ...f, thesis: event.target.value }))} />
          </Field>
          <Field label="什么情况说明判断失效？" required error={errors.invalidation}>
            <Textarea rows={3} maxLength={4000} value={form.invalidation} onChange={(event) => setForm((f) => ({ ...f, invalidation: event.target.value }))} />
          </Field>
          <Field label="计划复查日期" required error={errors.review_on}>
            <DateInput value={form.review_on} onChange={(event) => setForm((f) => ({ ...f, review_on: event.target.value }))} />
          </Field>
          {save.error && <InlineAlert tone="warn" title="没有保存">{ledgerErrorText(getApiErrorMessage(save.error))}</InlineAlert>}
          <Button type="submit" variant="primary" className="self-start" loading={save.isPending} disabledReason={readOnlyReason}>
            保存研究记录
          </Button>
        </form>
      </Panel>

      <Panel title="笔记" description={entries.length ? `共 ${entries.length} 条，新的在前` : undefined} aria-label="研究笔记">
        {journal.isPending ? (
          <Skeleton lines={4} />
        ) : journal.isError ? (
          <InlineAlert tone="warn" title="笔记读取失败" action={<Button size="sm" onClick={() => void journal.refetch()}>重试</Button>}>
            {getApiErrorMessage(journal.error)}
          </InlineAlert>
        ) : entries.length === 0 ? (
          <EmptyState icon={BookText} title="还没有笔记" description="先写下判断和失效条件，再安排一个复查日期。" />
        ) : (
          <ul className="-my-3 divide-y divide-line">
            {entries.map((entry) => {
              const due = today !== null && entry.review_on <= today
              return (
                <li key={entry.id} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-body font-medium text-fg">{entry.ticker}</h3>
                    <span className={cx('flex items-center gap-2 text-caption text-fg-3', numeric)}>
                      写于 {fmtDate(entry.created_at)}
                      {due ? <Badge tone="warn">复查到期 {entry.review_on}</Badge> : <span>复查 {entry.review_on}</span>}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap break-words text-body text-fg">{entry.thesis}</p>
                  <p className="mt-2 text-caption text-fg-3">判断失效条件</p>
                  <p className="whitespace-pre-wrap break-words text-table text-fg-2">{entry.invalidation}</p>
                </li>
              )
            })}
          </ul>
        )}
      </Panel>
    </div>
  )
}
