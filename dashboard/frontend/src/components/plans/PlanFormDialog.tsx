// 新建 / 编辑计划: short on purpose. Required: 标的, 为什么买, 什么情况说明我错了. Everything else is
// optional and prefilled where it can be (the only account that holds the ticker, a review date a month
// ahead, the current price as a placeholder). Saving makes a draft; 保存并启用 also turns the alert rules
// on and lists them. The backend's 409 / 422 messages are shown as they are. Every way of submitting (Enter,
// 保存草稿, 保存并启用) shares one in-flight guard, so a slow save is never sent twice. An edit sends only what
// changed, with the version it was made against; a plan changed meanwhile is refused (409) and the dialog
// lists what changed. The price hint keeps the backend's digits, with the quote's source, time and status.
import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, Button, DateInput, Dialog, Field, InlineAlert, MoneyInput, NumberInput, SegmentedControl, Select, Textarea, cx, toast } from '@/components/ui'
import { TickerInput } from '@/components/inputs/TickerInput'
import { useAppMode } from '@/components/shell/useAppMode'
import { fmtDate, fmtNumber, fmtPrice } from '@/lib/format'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { activatePlan, createPlan, fetchPlan, updatePlan } from '@/services/plans'
import { fetchQuotes } from '@/services/today'
import {
  currencyOf, editRequest, emptyForm, estimateMaxLoss, formFromPlan, formToBody, missingFields, type FormField, type PlanForm, type SizeMode,
} from '@/features/plans/form'
import { useCachedToday, usePlanDetail, useRefreshPlans } from '@/features/plans/hooks'
import { usePlanUi, type PlanFormRequest } from '@/features/plans/store'
import { alertRuleText, changedFields, sameListing } from '@/features/plans/view'
import type { Plan, PlanDetail } from '@/features/plans/types'
import { quoteLine } from '@/features/today/quoteStatus'
import { DISPLAY_ZONE } from '@/features/today/session'

const TICKER_SHAPE = /^[A-Za-z]{2,6}:[A-Za-z0-9.\-]{1,12}$/

function Body({ request, plan: opened }: { request: PlanFormRequest; plan: Plan | null }) {
  // The plan as it was when the form opened: the form's values and the edit's base and version. The detail
  // query refreshes underneath; following it would send the form's old values against the new version and
  // silently revert a change made elsewhere instead of refusing it (409).
  const [plan] = useState(opened)
  const close = usePlanUi((state) => state.closeForm)
  const openPlan = usePlanUi((state) => state.openPlan)
  const { readOnlyReason } = useAppMode()
  const today = useCachedToday()
  const refresh = useRefreshPlans()
  const todayDate = today ? fmtDate(today.as_of, { timeZone: DISPLAY_ZONE }) : fmtDate(new Date(), { timeZone: DISPLAY_ZONE })
  const [form, setForm] = useState<PlanForm>(() => (plan ? formFromPlan(plan) : emptyForm(todayDate, request.mode === 'new' ? request.preset : undefined)))
  const [errors, setErrors] = useState<Partial<Record<FormField, string>>>({})
  const [apiError, setApiError] = useState<{ message: string; saved: boolean; changes?: string[] } | null>(null)
  const [busy, setBusy] = useState<'draft' | 'activate' | null>(null)
  // The plan as last saved from this dialog: later saves are edits of it.
  const [saved, setSaved] = useState<Plan | null>(null)
  const [result, setResult] = useState<PlanDetail | null>(null)
  const [accountTouched, setAccountTouched] = useState(false)
  // One save at a time, whichever way it was asked for (Enter, 保存草稿, 保存并启用).
  const inFlight = useRef(false)

  const locked = plan?.status === 'active'
  const editing = plan !== null || saved !== null
  const status = plan?.status ?? 'draft'
  const set = <K extends keyof PlanForm>(key: K, value: PlanForm[K]) => setForm((current) => ({ ...current, [key]: value }))
  const currency = currencyOf(form.ticker)
  const ticker = form.ticker.trim().toUpperCase()
  const quote = useQuery({
    queryKey: ['plan-form-quote', ticker],
    queryFn: () => fetchQuotes([ticker]),
    enabled: TICKER_SHAPE.test(ticker),
    staleTime: 30_000,
    retry: false,
  })
  const quoted = quote.data?.quotes[0] ?? null
  const price = quoted?.price ?? null
  const accounts = (today?.accounts ?? []).map((account) => account.name)
  const heldIn = (today?.accounts ?? []).filter((account) => account.positions.some((row) => sameListing(row.ticker, ticker))).map((account) => account.name)
  const estimate = estimateMaxLoss(form)
  const reason = readOnlyReason

  // The only account that holds the ticker is the likely scope of the plan; a choice the user made stays.
  const changeTicker = (value: string) => {
    const held = (today?.accounts ?? []).filter((account) => account.positions.some((row) => sameListing(row.ticker, value)))
    setForm((current) => ({ ...current, ticker: value, account: accountTouched || locked ? current.account : held.length === 1 ? held[0].name : '' }))
    setErrors((current) => ({ ...current, ticker: undefined }))
  }

  const submit = async (activate: boolean) => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      await save(activate)
    } finally {
      inFlight.current = false
    }
  }

  const save = async (activate: boolean) => {
    const missing = missingFields(form)
    setErrors(missing)
    setApiError(null)
    if (Object.keys(missing).length) return
    setBusy(activate ? 'activate' : 'draft')
    const base = saved ?? plan
    let current: Plan
    try {
      if (base === null) {
        current = await createPlan(formToBody(form))
      } else {
        const edit = editRequest(base, form)
        current = edit ? await updatePlan(base.id, edit) : base
      }
      setSaved(current)
      await refresh()
    } catch (exc) {
      let changes: string[] | undefined
      if (base !== null && httpStatus(exc) === 409) {
        // Changed elsewhere since this form was opened: say what, so the person can decide.
        try {
          changes = changedFields(base, await fetchPlan(base.id))
        } catch {
          changes = undefined
        }
      }
      setApiError({ message: getApiErrorMessage(exc), saved: false, changes })
      setBusy(null)
      return
    }
    if (!activate) {
      setBusy(null)
      toast.success(plan ? '计划已保存' : '计划已保存为草稿')
      close()
      openPlan(current.id)
      return
    }
    try {
      const activated = current.status === 'active' ? await fetchPlan(current.id) : await activatePlan(current.id)
      await refresh()
      setResult(activated)
    } catch (exc) {
      setApiError({ message: getApiErrorMessage(exc), saved: true })
    } finally {
      setBusy(null)
    }
  }

  if (result) {
    const rules = (result.alerts ?? []).filter((rule) => rule.enabled)
    return (
      <Dialog
        key="done"
        open
        onClose={() => { close(); openPlan(result.id) }}
        size="md"
        title="计划已启用"
        description={rules.length ? `已创建 ${rules.length} 条提醒规则；触发时会通知你，不会自动下单。` : '没有创建新的提醒规则。'}
        footer={<Button variant="primary" data-autofocus onClick={() => { close(); openPlan(result.id) }}>查看计划</Button>}
      >
        <div className="flex flex-col gap-3">
          {result.alerts === null && <InlineAlert tone="warn">{result.alerts_error ?? '提醒规则暂时无法读取（不可用）。'}</InlineAlert>}
          {rules.length > 0 && (
            <ul aria-label="提醒规则" className="flex flex-col gap-1 text-body">
              {rules.map((rule) => <li key={rule.id}>{alertRuleText(rule)}</li>)}
            </ul>
          )}
          {result.alert_notes.map((note) => <InlineAlert key={note} tone="info">{note}</InlineAlert>)}
        </div>
      </Dialog>
    )
  }

  const sizeLabel = form.sizeMode === 'weight' ? '计划仓位（占组合）' : '计划股数'
  // The backend's digits, its source and time, and amber when the quote is not verified (stale, 昨收价 ...).
  const line = quoted ? quoteLine(quoted.quote, todayDate, quoted.estimated, quoted.estimated_reason) : null
  const priceHint = price && quoted ? (
    <span title={line?.chip?.title ?? undefined}>
      现价 {fmtPrice(price, currency ?? 'CNY', 'stock', { symbol: (currency ?? 'CNY') !== 'CNY', exact: true })}
      {line?.meta && <span className="ml-1 text-fg-3">{line.meta}</span>}
      {line?.chip && <span className={cx('ml-1', line.chip.tone === 'warn' ? 'text-warn' : 'text-fg-3')}>{line.chip.label}</span>}
    </span>
  ) : undefined
  const placeholder = price ? fmtPrice(price, currency ?? 'CNY', 'stock', { exact: true }) : ''
  const money = (label: string, key: 'entryLow' | 'entryHigh' | 'stop' | 'target', hint?: string) => (
    <Field label={label} hint={hint}>
      <MoneyInput currency={currency ?? 'CNY'} value={form[key]} onValueChange={(value) => set(key, value)} placeholder={placeholder} />
    </Field>
  )

  return (
    <Dialog
      key="form"
      open
      onClose={close}
      size="md"
      closeOnBackdrop={false}
      title={plan ? '编辑交易计划' : '新建交易计划'}
      description="写下为什么买、什么情况说明判断错了；价格和仓位可以之后再补。计划只用于记录和提醒，不会下单。"
      footer={(
        <>
          <Button variant="ghost" onClick={close}>取消</Button>
          <Button loading={busy === 'draft'} disabledReason={reason ?? (busy ? '正在保存' : undefined)} onClick={() => void submit(false)}>{editing && status !== 'draft' ? '保存' : '保存草稿'}</Button>
          {status !== 'active' && (
            <Button variant="primary" loading={busy === 'activate'} disabledReason={reason ?? (busy ? '正在保存' : undefined)} onClick={() => void submit(true)}>保存并启用</Button>
          )}
        </>
      )}
    >
      <form
        className="flex flex-col gap-4"
        noValidate
        onSubmit={(event) => { event.preventDefault(); void submit(false) }}
      >
        {apiError && (
          <InlineAlert tone="warn" title={apiError.saved ? '已保存为草稿，但没有启用' : '没有保存'}>
            {apiError.message}
            {apiError.changes && apiError.changes.length > 0 && (
              <div className="mt-1">
                期间的修改：
                <ul aria-label="期间的修改" className="mt-0.5 list-disc pl-5">
                  {apiError.changes.map((change) => <li key={change}>{change}</li>)}
                </ul>
                关闭后重新打开可以看到最新内容；你这次的修改没有保存。
              </div>
            )}
            {apiError.saved && <span className="mt-1 block">计划已保存为草稿。处理后可再次点击“保存并启用”，不会重复创建。</span>}
          </InlineAlert>
        )}
        {locked && (
          <p className="text-table text-fg-2">这是启用中的计划：保存后，它的提醒规则会立即按新的止损、目标和计划仓位更新。</p>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="标的" required error={errors.ticker} hint={locked ? '启用中的计划不能改标的' : priceHint}>
            <TickerInput
              value={form.ticker}
              onChange={changeTicker}
              onSelect={(choice) => changeTicker(choice.ticker ?? form.ticker)}
              disabled={locked || saved !== null}
              invalid={Boolean(errors.ticker)}
            />
          </Field>
          <Field label="账户" hint={locked ? '启用中的计划不能改账户' : '不选则对所有账户生效'}>
            <Select
              value={form.account}
              disabled={locked || saved !== null}
              onChange={(event) => { setAccountTouched(true); set('account', event.target.value) }}
              options={[{ value: '', label: '全部账户' }, ...accounts.map((name) => ({ value: name, label: name })),
                ...(form.account && !accounts.includes(form.account) ? [{ value: form.account, label: form.account }] : [])]}
            />
          </Field>
        </div>
        {heldIn.length > 1 && !form.account && !locked && (
          <p className="-mt-2 text-table text-fg-3">这只标的在 {heldIn.join('、')} 都有持仓，可以选定一个账户。</p>
        )}
        <Field label="为什么买" required error={errors.thesis}>
          <Textarea rows={2} value={form.thesis} onChange={(event) => { set('thesis', event.target.value); setErrors((c) => ({ ...c, thesis: undefined })) }} />
        </Field>
        <Field label="什么情况说明我错了" required error={errors.invalidation}>
          <Textarea rows={2} value={form.invalidation} onChange={(event) => { set('invalidation', event.target.value); setErrors((c) => ({ ...c, invalidation: undefined })) }} />
        </Field>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {money('入场下限', 'entryLow')}
          {money('入场上限', 'entryHigh')}
          {money('止损', 'stop')}
          {money('目标', 'target')}
        </div>
        <div className="flex flex-col gap-2">
          <SegmentedControl<SizeMode>
            ariaLabel="计划仓位的填写方式"
            options={[{ value: 'weight', label: '计划仓位 %' }, { value: 'quantity', label: '计划股数' }]}
            value={form.sizeMode}
            onChange={(value) => set('sizeMode', value)}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={sizeLabel} hint={form.sizeMode === 'weight' ? '占整个组合（含现金和基金）的比例' : '填股数后可估算最大亏损'}>
              <NumberInput
                value={form.size}
                onValueChange={(value) => set('size', value)}
                suffix={form.sizeMode === 'weight' ? '%' : '股'}
              />
            </Field>
            <Field label="复查日" hint="启用后，到期时会列在今日页的「今日事项」里">
              <DateInput value={form.reviewOn} onChange={(event) => set('reviewOn', event.target.value)} />
            </Field>
          </div>
        </div>
        <p className={cx('flex flex-wrap items-center gap-x-2 gap-y-1 text-table', estimate ? 'text-fg-2' : 'text-fg-3')} aria-live="polite">
          {estimate ? (
            <>
              <Badge tone="warn">估算</Badge>
              <span>最大亏损约 {fmtNumber(estimate.amount)} {estimate.currency}：{estimate.formula}。</span>
              {estimate.currency !== 'CNY' && <span className="text-fg-3">保存后按当前汇率折算人民币。</span>}
            </>
          ) : form.sizeMode === 'weight' ? (
            '用股数填写仓位，并填上入场价和止损，这里会估算最大亏损。'
          ) : (
            '填上入场价、止损和股数，这里会估算最大亏损；保存后以系统计算为准。'
          )}
        </p>
        <button type="submit" className="sr-only" tabIndex={-1}>保存</button>
      </form>
    </Dialog>
  )
}

export function PlanFormDialog() {
  const request = usePlanUi((state) => state.form)
  const editId = request?.mode === 'edit' ? request.id : null
  const detail = usePlanDetail(editId)
  if (!request) return null
  if (request.mode === 'edit') {
    const plan = detail.data && detail.data.id === request.id ? detail.data : null
    if (!plan) return null
    return <Body key={`edit:${request.id}`} request={request} plan={plan} />
  }
  return <Body key="new" request={request} plan={null} />
}
