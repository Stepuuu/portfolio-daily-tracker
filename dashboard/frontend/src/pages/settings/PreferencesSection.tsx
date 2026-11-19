// 设置 › 交易偏好: what the assistant remembers about the person (`/api/memory`): the profile's closed choices
// (experience, style, risk, sectors) and notes with one 保存; emotional triggers and lessons, each added or
// deleted on its own; the assistant's pending lesson and profile proposals (confirm or reject, as in the
// 待确认 tray); and the rejected ones, which are kept so the assistant does not propose them again; 删除
// takes that back (`DELETE /api/proposals/{id}/decision`). Every write takes readOnlyReason. Without the
// assistant service (the demo, research-only mode) the memory is 不可用 and says why.
import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, X } from 'lucide-react'
import {
  Badge, Button, Checkbox, Dialog, EmptyState, Field, IconButton, InlineAlert, Input, Panel, Select, Skeleton, Textarea,
  cx, numeric, toast,
} from '@/components/ui'
import { useAppMode } from '@/components/shell/useAppMode'
import { getApiErrorMessage } from '@/services/api'
import {
  MEMORY_KEY, addLesson, addSector, addTrigger, fetchMemory, removeLesson, removeSector, removeTrigger, resetMemory,
  updateProfile,
} from '@/services/memory'
import { confirmProposal, rejectProposal } from '@/services/proposals'
import { fetchMemoryProposals, forgetProposal, memoryProposalsKey } from '@/services/settings'
import { fmtDateTime, fmtRelative } from '@/lib/format'
import { sourceLabel, type GenericProposal } from '@/features/pending/proposals'
import {
  EXPERIENCE_CHOICES, ITEM_MAX, LESSON_TYPE_LABEL, NOTES_MAX, RISK_CHOICES, STYLE_CHOICES, itemProblem, lessonText,
  profileChanges, profileForm, sectorChoices, toggleSector, type MemoryLesson, type MemoryState, type ProfileForm,
} from '@/features/settings/memory'

/** After any change: the memory and every proposal list (the tray's count included). */
function useRefresh() {
  const client = useQueryClient()
  return () => Promise.all([
    client.invalidateQueries({ queryKey: MEMORY_KEY }),
    client.invalidateQueries({ queryKey: ['proposals'] }),
  ])
}

function ProfilePanel({ memory }: { memory: MemoryState }) {
  const { readOnlyReason } = useAppMode()
  const refresh = useRefresh()
  const saved = profileForm(memory)
  const [form, setForm] = useState<ProfileForm>(saved)
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const patch = (next: Partial<ProfileForm>) => setForm((current) => ({ ...current, ...next }))

  async function save(event: FormEvent) {
    event.preventDefault()
    if (readOnlyReason) return
    const changes = profileChanges(saved, form)
    if (changes.empty) {
      toast.info('没有需要保存的改动')
      return
    }
    setSaving(true)
    setFailure(null)
    const done: string[] = []
    try {
      if (Object.keys(changes.profile).length) {
        await updateProfile(changes.profile)
        done.push('画像')
      }
      for (const sector of changes.add) {
        await addSector(sector)
        done.push(`加入「${sector}」`)
      }
      for (const sector of changes.remove) {
        await removeSector(sector)
        done.push(`去掉「${sector}」`)
      }
      toast.success('已保存交易偏好')
    } catch (error) {
      setFailure(`${done.length ? `已保存：${done.join('、')}；其余没有保存：` : ''}${getApiErrorMessage(error)}`)
    } finally {
      setSaving(false)
      await refresh()
    }
  }

  return (
    <Panel title="交易画像" description="助手在每次对话里参考这些设置；它只能提出修改，确认后才生效。">
      <form onSubmit={(event) => void save(event)} noValidate className="flex flex-col gap-4">
        <fieldset disabled={Boolean(readOnlyReason) || saving} className="flex min-w-0 flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="交易经验">
              <Select value={form.experience_level} placeholder="未设置" options={EXPERIENCE_CHOICES} onChange={(event) => patch({ experience_level: event.target.value })} />
            </Field>
            <Field label="交易风格">
              <Select value={form.trading_style} placeholder="未设置" options={STYLE_CHOICES} onChange={(event) => patch({ trading_style: event.target.value })} />
            </Field>
            <Field label="风险偏好">
              <Select value={form.risk_tolerance} placeholder="未设置" options={RISK_CHOICES} onChange={(event) => patch({ risk_tolerance: event.target.value })} />
            </Field>
          </div>
          <fieldset className="flex min-w-0 flex-col gap-2">
            <legend className="mb-1 text-table font-medium text-fg-2">偏好板块</legend>
            <div className="grid grid-cols-3 gap-x-4 gap-y-2 sm:grid-cols-5">
              {sectorChoices(saved.sectors).map(({ value, extra }) => (
                <Checkbox
                  key={value}
                  label={value}
                  description={extra ? '不在常用列表中' : undefined}
                  checked={form.sectors.includes(value)}
                  onChange={(event) => patch({ sectors: toggleSector(form.sectors, value, event.target.checked) })}
                />
              ))}
            </div>
          </fieldset>
          <Field label="备注" hint={`选填，最多 ${NOTES_MAX} 字，如持仓周期、单只仓位上限`}>
            <Textarea value={form.notes} maxLength={NOTES_MAX} rows={3} onChange={(event) => patch({ notes: event.target.value })} />
          </Field>
        </fieldset>
        {failure && <InlineAlert tone="warn" title="没有全部保存">{failure}</InlineAlert>}
        <div>
          <Button type="submit" variant="primary" loading={saving} disabledReason={readOnlyReason}>保存</Button>
        </div>
      </form>
    </Panel>
  )
}

/** Add one text item (a trigger or a lesson): a field and 添加, refused while the mode is read-only. */
function AddItem({ label, placeholder, existing, add }: {
  label: string; placeholder: string; existing: string[]; add: (text: string) => Promise<void>
}) {
  const { readOnlyReason } = useAppMode()
  const [text, setText] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (readOnlyReason) return
    const found = itemProblem(text, existing)
    setProblem(found)
    if (found) return
    setBusy(true)
    try {
      await add(text.trim())
      setText('')
    } catch (error) {
      setProblem(getApiErrorMessage(error))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={(event) => void submit(event)} noValidate className="flex min-w-0 items-start gap-2">
      <Field label={label} labelHidden error={problem} className="min-w-0 flex-1">
        <Input value={text} maxLength={ITEM_MAX} placeholder={placeholder} disabled={Boolean(readOnlyReason)} onChange={(event) => setText(event.target.value)} />
      </Field>
      <Button type="submit" icon={Plus} loading={busy} disabledReason={readOnlyReason} tipAlign="end">添加</Button>
    </form>
  )
}

function TriggersPanel({ memory }: { memory: MemoryState }) {
  const { readOnlyReason } = useAppMode()
  const refresh = useRefresh()
  const triggers = memory.preferences?.emotional_triggers ?? []
  const remove = useMutation({
    mutationFn: removeTrigger,
    onSuccess: (_, trigger) => {
      toast.success(`已删除「${trigger}」`)
      void refresh()
    },
    onError: (error) => toast.warn(`没有删除：${getApiErrorMessage(error)}`),
  })
  return (
    <Panel title="情绪触发点" description="容易让你冲动交易的情况，助手会在对话里提醒你。">
      <div className="flex flex-col gap-3">
        {triggers.length ? (
          <ul aria-label="情绪触发点" className="flex flex-wrap gap-2">
            {triggers.map((trigger) => (
              <li key={trigger} className="inline-flex max-w-full items-center gap-1 rounded-full border border-line bg-surface-2 py-0.5 pl-3 pr-1 text-table text-fg">
                <span className="min-w-0 break-words">{trigger}</span>
                <IconButton icon={X} size="sm" label={`删除「${trigger}」`} disabledReason={readOnlyReason} onClick={() => remove.mutate(trigger)} />
              </li>
            ))}
          </ul>
        ) : <p className="text-body text-fg-3">还没有记录。</p>}
        <AddItem
          label="新的情绪触发点"
          placeholder="如：看到涨停就想追"
          existing={triggers}
          add={async (text) => {
            await addTrigger(text)
            toast.success(`已记录「${text}」`)
            await refresh()
          }}
        />
      </div>
    </Panel>
  )
}

function LessonsPanel({ memory }: { memory: MemoryState }) {
  const { readOnlyReason } = useAppMode()
  const refresh = useRefresh()
  const lessons = memory.history?.lessons ?? []
  // The lesson to delete and its place in the list as shown: the backend deletes it only if that place still
  // holds it (another tab may have deleted an earlier one), and says so otherwise.
  const [pending, setPending] = useState<{ index: number; lesson: MemoryLesson } | null>(null)
  const remove = useMutation({
    mutationFn: ({ index, lesson }: { index: number; lesson: MemoryLesson }) => removeLesson(index, lesson),
    onSuccess: () => {
      toast.success('已删除这条教训')
      setPending(null)
      void refresh()
    },
    onError: (error) => {
      toast.warn(`没有删除：${getApiErrorMessage(error)}`)
      setPending(null)
      void refresh()
    },
  })
  const target = pending?.lesson ?? null
  return (
    <Panel title="交易教训" description="助手记住的经验和教训；它提出的新教训在下面确认后才会加进来。">
      <div className="flex flex-col gap-3">
        {lessons.length ? (
          <ul aria-label="交易教训" className="flex flex-col divide-y divide-line">
            {lessons.map((lesson, index) => {
              const { text, event } = lessonText(lesson)
              return (
                <li key={`${index}:${lesson.date}:${text}`} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-body text-fg">{text}</p>
                    {event && <p className="break-words text-table text-fg-2">事件：{event}</p>}
                    <p className={cx('mt-0.5 text-caption text-fg-3', numeric)}>
                      {[lesson.date, LESSON_TYPE_LABEL[lesson.type] ?? lesson.type, lesson.symbol].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                  <IconButton icon={Trash2} size="sm" label={`删除教训：${text.slice(0, 20)}`} disabledReason={readOnlyReason} tipAlign="end" onClick={() => setPending({ index, lesson })} />
                </li>
              )
            })}
          </ul>
        ) : <p className="text-body text-fg-3">还没有记录。</p>}
        <AddItem
          label="新的交易教训"
          placeholder="记录一条交易教训"
          existing={lessons.map((lesson) => lessonText(lesson).text)}
          add={async (text) => {
            await addLesson(text)
            toast.success('已记录这条教训')
            await refresh()
          }}
        />
      </div>
      <Dialog
        open={target !== null}
        onClose={() => setPending(null)}
        title="删除这条教训？"
        size="sm"
        footer={(
          <>
            <Button data-autofocus onClick={() => setPending(null)}>取消</Button>
            <Button variant="primary" loading={remove.isPending} disabledReason={readOnlyReason} onClick={() => pending !== null && remove.mutate(pending)}>删除</Button>
          </>
        )}
      >
        <p className="break-words text-body text-fg">{target ? lessonText(target).text : ''}</p>
        <p className="mt-2 text-table text-fg-3">删除后助手不再参考它；如果这是助手提出、你确认过的教训，它也不会再提出同样的内容。</p>
      </Dialog>
    </Panel>
  )
}

function ProposalItem({ proposal, rejected }: { proposal: GenericProposal; rejected: boolean }) {
  const { readOnlyReason } = useAppMode()
  const refresh = useRefresh()
  const confirm = useMutation({
    mutationFn: () => confirmProposal(proposal.id),
    onSuccess: () => { toast.success(`已确认：${proposal.summary}`); void refresh() },
  })
  const reject = useMutation({
    mutationFn: () => rejectProposal(proposal.id),
    onSuccess: () => { toast.info(`已放弃：${proposal.summary}`); void refresh() },
  })
  const forget = useMutation({
    mutationFn: () => forgetProposal(proposal.id),
    onSuccess: () => { toast.success('已删除这次放弃：助手以后可能再提出这项内容'); void refresh() },
  })
  const failure = confirm.error ?? reject.error ?? forget.error
  const busy = confirm.isPending || reject.isPending || forget.isPending
  const resolved = proposal.resolved_at
  return (
    <li className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words text-body text-fg">{proposal.summary}</p>
          <p className={cx('mt-0.5 text-caption text-fg-3', numeric)}>
            {proposal.kind === 'profile' ? '画像' : '教训'} · {sourceLabel(proposal.source)} · {rejected
              ? `放弃于 ${resolved ? fmtDateTime(resolved) : '不可用'}`
              : `${fmtRelative(proposal.created_at)}提出 · ${fmtDateTime(proposal.expires_at)} 前有效`}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {rejected ? (
            <Button size="sm" icon={Trash2} loading={forget.isPending} disabledReason={readOnlyReason} tipAlign="end" onClick={() => forget.mutate()}>删除</Button>
          ) : (
            <>
              <Button size="sm" variant="primary" loading={confirm.isPending} disabled={busy} disabledReason={readOnlyReason} onClick={() => confirm.mutate()}>确认</Button>
              <Button size="sm" variant="ghost" disabled={busy} disabledReason={readOnlyReason} tipAlign="end" onClick={() => reject.mutate()}>放弃</Button>
            </>
          )}
        </div>
      </div>
      {(proposal.error || failure) && <p className="text-table text-warn">{failure ? getApiErrorMessage(failure) : proposal.error}</p>}
    </li>
  )
}

function ProposalsPanel({ status }: { status: 'pending' | 'rejected' }) {
  const list = useQuery({ queryKey: memoryProposalsKey(status), queryFn: () => fetchMemoryProposals(status), refetchInterval: status === 'pending' ? 15_000 : false })
  const rejected = status === 'rejected'
  return (
    <Panel
      title={rejected ? '已放弃的提议' : '待确认的提议'}
      description={rejected
        ? '助手不会再提出这些内容。删除一条，它以后可能再次提出。'
        : '助手从对话里提出的教训和画像修改；也在右上角的待确认里。'}
      actions={list.data?.length ? <Badge>{list.data.length}</Badge> : undefined}
    >
      {list.isPending && <Skeleton lines={2} />}
      {list.isError && (
        <InlineAlert tone="warn" title="提议列表不可用" action={<Button size="sm" onClick={() => void list.refetch()}>重试</Button>}>
          {getApiErrorMessage(list.error)}
        </InlineAlert>
      )}
      {list.data && (list.data.length ? (
        <ul aria-label={rejected ? '已放弃的提议' : '待确认的提议'} className="flex flex-col divide-y divide-line">
          {list.data.map((proposal) => <ProposalItem key={proposal.id} proposal={proposal} rejected={rejected} />)}
        </ul>
      ) : <p className="text-body text-fg-3">{rejected ? '没有放弃过的提议。' : '没有待确认的提议。'}</p>)}
    </Panel>
  )
}

function ResetPanel() {
  const { readOnlyReason } = useAppMode()
  const refresh = useRefresh()
  const [open, setOpen] = useState(false)
  const reset = useMutation({
    mutationFn: resetMemory,
    onSuccess: () => {
      toast.success('已清空助手记忆')
      setOpen(false)
      void refresh()
    },
  })
  return (
    <Panel title="清空助手记忆" description="画像、偏好板块、情绪触发点和交易教训全部删除，不能恢复；已放弃的提议不受影响。">
      <Button variant="warn-outline" icon={Trash2} disabledReason={readOnlyReason} onClick={() => setOpen(true)}>清空助手记忆</Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="清空助手记忆？"
        size="sm"
        footer={(
          <>
            <Button data-autofocus onClick={() => setOpen(false)}>取消</Button>
            <Button variant="primary" loading={reset.isPending} disabledReason={readOnlyReason} onClick={() => reset.mutate()}>清空</Button>
          </>
        )}
      >
        <p className="text-body text-fg">助手会忘记你的画像、偏好板块、情绪触发点和全部交易教训，这一步不能撤销。</p>
        {reset.error && <p className="mt-2 text-table text-warn">{getApiErrorMessage(reset.error)}</p>}
      </Dialog>
    </Panel>
  )
}

export function PreferencesSection() {
  const memory = useQuery({ queryKey: MEMORY_KEY, queryFn: fetchMemory, retry: false })
  return (
    <>
      {memory.isPending && <Panel title="交易画像"><Skeleton lines={4} /></Panel>}
      {memory.isError && (
        <Panel title="交易画像">
          <EmptyState
            title="助手的记忆不可用"
            description={`${getApiErrorMessage(memory.error)}。记忆只在助手服务运行时可读（演示模式和研究模式没有）。`}
            action={<Button size="sm" onClick={() => void memory.refetch()}>重试</Button>}
          />
        </Panel>
      )}
      {memory.data && (
        <>
          {/* Keyed by what is saved, so a save (or a confirmed profile proposal) starts the form from it. */}
          <ProfilePanel key={JSON.stringify(profileForm(memory.data))} memory={memory.data} />
          <TriggersPanel memory={memory.data} />
          <LessonsPanel memory={memory.data} />
        </>
      )}
      <ProposalsPanel status="pending" />
      <ProposalsPanel status="rejected" />
      {memory.data && <ResetPanel />}
    </>
  )
}

export default PreferencesSection
