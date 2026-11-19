// 新建提醒 / 编辑提醒: ticker by search (holdings first: a pick of a usable listing, never a guess), the
// kind as a segmented control, the threshold with the current price and the distance shown as it is typed
// ("现价 61.20 · 距止损 -11.8%"), one-click thresholds that name the price they rest on (amber when it is
// not verified), the account always shown (cleared when the instrument changes), and for a position cap
// the weight the monitor would use: 不可用 when it cannot be known, marked 估算 with no promise to fire
// when it is not verified. The API's validation messages appear on the field they are about. Opened from
// anywhere with `openNewAlert(draft)` / `openEditAlert(rule)`.
import { useId, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { AxiosError } from 'axios'
import { AlertTriangle } from 'lucide-react'
import {
  Button, Dialog, Field, Input, InlineAlert, NumberInput, SegmentedControl, Select, cx, numeric, toast,
} from '@/components/ui'
import { currencySymbol, fmtDate } from '@/lib/format'
import { exchangeLabel, sameTicker } from '@/lib/instrument'
import {
  KIND_OPTIONS, accountAfterTickerChange, conditionText, fieldForError, kindLabel, previewLine, priceBasis, quickPicks, splitThreshold,
  thresholdValue, tickerError, weightContext, type AlertField, type AlertKind,
} from '@/features/alerts/rules'
import { currencyOf, fmtTickerPrice } from '@/features/market/format'
import { DISPLAY_ZONE } from '@/features/today/session'
import { TickerInput } from '@/components/inputs/TickerInput'
import { useAppMode } from '@/components/shell/useAppMode'
import { useTodayFeed } from '@/components/shell/useTodayFeed'
import { ALERTS_KEY, createAlertRule, updateAlertRule } from '@/services/alerts'
import { errorText, useQuote } from '@/components/market/hooks'
import { useAlertDialog, type AlertDraft } from './alertDialogStore'

const WHOLE = ''

type Errors = Partial<Record<AlertField | 'form', string>>

function AlertForm({ draft, formId, onDone }: { draft: AlertDraft; formId: string; onDone: () => void }) {
  const editing = useAlertDialog((state) => state.rule)
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const todayFeed = useTodayFeed()
  const today = todayFeed.data
  const initial = splitThreshold(draft.kind ?? 'stop_loss', draft.threshold ?? '')
  const [ticker, setTicker] = useState(draft.ticker ?? '')
  const [name, setName] = useState<string | null>(draft.name ?? null)
  // The listing the field holds by a pick (or as the opener gave it); typed text alone is not one.
  const [picked, setPicked] = useState<{ ticker: string | null; supported: boolean } | null>(
    draft.ticker ? { ticker: draft.ticker, supported: true } : null,
  )
  const [kind, setKind] = useState<AlertKind>(draft.kind ?? 'stop_loss')
  const [magnitude, setMagnitude] = useState(initial.magnitude)
  const [direction, setDirection] = useState<'down' | 'up'>(initial.direction)
  const [account, setAccount] = useState(draft.account ?? WHOLE)
  const [note, setNote] = useState(draft.note ?? '')
  const [errors, setErrors] = useState<Errors>({})

  const chosen = picked?.ticker && picked.supported && sameTicker(picked.ticker, ticker) ? picked.ticker : null
  const complete = editing ? editing.ticker : chosen
  const quote = useQuote(complete)
  const object = quote.data?.quote ?? null
  const currency = object?.currency ?? (complete ? currencyOf(complete) : 'CNY')
  // The search may not know a name (no name index); the quote usually does.
  const label = name ?? object?.name ?? null
  const accounts = (today?.accounts ?? []).map((item) => item.name)
  const date = today ? fmtDate(today.as_of, { timeZone: DISPLAY_ZONE }) : null
  const weight = complete ? weightContext(today, complete, account || null, todayFeed.isError) : undefined
  const threshold = thresholdValue(kind, magnitude, direction)
  const index = Boolean(complete?.toUpperCase().startsWith('IDX:'))
  const preview = complete
    ? previewLine(kind, threshold, {
      ticker: complete, price: object?.price ?? null, prevClose: object?.prev_close ?? null, changePct: object?.change_pct ?? null, weight,
      priceReason: quote.isPending ? null : object?.estimated_reason ?? object?.quote.reason ?? (quote.isError ? errorText(quote.error) : null),
      // As the monitor sees it: live, delayed or a confirmed close; anything else is not used to fire.
      priceVerified: Boolean(object && !object.estimated && ['live', 'delayed', 'closed'].includes(object.quote.status)),
    })
    : null
  const picks = complete ? quickPicks(kind, object?.price ?? null, complete) : []
  const basis = complete && (kind === 'stop_loss' || kind === 'target_price') && picks.length ? priceBasis(object, complete, date) : null
  const wholeLabel = kind === 'position_weight_max' ? '整个组合' : '不限账户'

  // A different kind clears a threshold typed for the old one (a price is not a percentage).
  const changeKind = (next: AlertKind) => {
    if (next === kind) return
    setKind(next)
    setMagnitude('')
    setErrors((current) => ({ ...current, threshold: undefined, kind: undefined }))
  }

  /** A different instrument picked: the account chosen for the old one (or the opener's) does not carry over. */
  const changeTicker = (next: string | null) => {
    const from = picked?.ticker ?? draft.ticker ?? ''
    if (account) setAccount(accountAfterTickerChange(account, from, next ?? ''))
  }

  const save = useMutation({
    mutationFn: async () => {
      const body = { threshold, account: account || null, note: note.trim() || null }
      if (editing) return updateAlertRule(editing.id, body)
      return createAlertRule({ ticker: complete!, kind, ...body })
    },
    onSuccess: (rule) => {
      void client.invalidateQueries({ queryKey: ALERTS_KEY })
      toast.success(`${editing ? '已修改提醒' : '已设提醒'}：${label ?? rule.ticker} ${kindLabel(rule.kind)} ${conditionText(rule)}`)
      onDone()
    },
    onError: (exc) => {
      const detail = errorText(exc)
      const status = (exc as AxiosError)?.response?.status
      const field = status === 422 || status === 409 ? fieldForError(detail) : null
      setErrors(field ? { [field]: detail } : { form: detail })
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const next: Errors = {}
    const problem = editing ? null : tickerError(ticker, picked)
    if (problem) next.ticker = problem
    if (!magnitude.trim()) next.threshold = '请填写阈值。'
    if (index && kind === 'position_weight_max') next.kind = '指数不是持仓，不能设置仓位上限提醒。'
    setErrors(next)
    if (Object.keys(next).length === 0 && !readOnlyReason && !save.isPending) save.mutate()
  }

  const thresholdLabel = { stop_loss: '止损价', target_price: '目标价', day_change_pct: '涨跌幅', position_weight_max: '仓位上限' }[kind]
  const hint = preview && (
    <span className="flex flex-col gap-0.5">
      <span className={numeric}>{preview.text}</span>
      {(preview.warn || preview.note) && (
        <span className="flex items-start gap-1 text-warn">
          <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0" />
          {preview.warn ?? preview.note}
        </span>
      )}
    </span>
  )

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      {readOnlyReason && <InlineAlert tone="info">{readOnlyReason}：可以查看，不能保存。</InlineAlert>}
      {editing ? (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-body">
          <dt className="text-table text-fg-2">标的</dt>
          <dd>{label ? `${label} ` : ''}<span className={cx('text-fg-3', numeric)}>{editing.ticker}</span></dd>
          <dt className="text-table text-fg-2">类型</dt>
          <dd>{kindLabel(editing.kind)}</dd>
        </dl>
      ) : (
        <>
          <Field
            label="标的"
            required
            error={errors.ticker}
            hint={complete && label ? [label, exchangeLabel(complete.split(':')[0]), currency].join(' · ') : '从列表中选择；可输入代码或名称搜索'}
          >
            <TickerInput
              value={ticker}
              invalid={Boolean(errors.ticker)}
              onChange={(value) => {
                setTicker(value)
                if (name && value !== ticker) setName(null)
                setErrors((current) => ({ ...current, ticker: undefined }))
              }}
              onSelect={(choice) => {
                changeTicker(choice.ticker)
                setPicked({ ticker: choice.ticker, supported: choice.supported })
                setName(choice.name)
              }}
            />
          </Field>
          <Field label="类型" error={errors.kind}>
            <SegmentedControl<AlertKind>
              options={KIND_OPTIONS.map((option) => ({
                ...option, disabled: option.value === 'position_weight_max' && index,
              }))}
              value={kind}
              onChange={changeKind}
            />
          </Field>
        </>
      )}

      <Field label={thresholdLabel} required error={errors.threshold} hint={hint}>
        {kind === 'day_change_pct' ? (
          <div className="flex flex-wrap items-center gap-2">
            <SegmentedControl<'down' | 'up'>
              ariaLabel="方向"
              options={[{ value: 'down', label: '跌幅达到' }, { value: 'up', label: '涨幅达到' }]}
              value={direction}
              onChange={setDirection}
            />
            <NumberInput
              className="w-32"
              value={magnitude}
              onValueChange={(value) => {
                setMagnitude(value)
                setErrors((current) => ({ ...current, threshold: undefined }))
              }}
              suffix="%"
              data-autofocus={draft.ticker ? true : undefined}
              aria-invalid={errors.threshold ? true : undefined}
            />
          </div>
        ) : (
          <NumberInput
            className="w-48"
            value={magnitude}
            onValueChange={(value) => {
              setMagnitude(value)
              setErrors((current) => ({ ...current, threshold: undefined }))
            }}
            prefix={kind === 'position_weight_max' ? undefined : currencySymbol(currency)}
            suffix={kind === 'position_weight_max' ? '%' : undefined}
            // A prefilled ticker: start on the threshold (the dialog focuses [data-autofocus]).
            data-autofocus={draft.ticker ? true : undefined}
            aria-invalid={errors.threshold ? true : undefined}
          />
        )}
      </Field>
      {picks.length > 0 && (
        <div className="-mt-2 flex flex-wrap items-center gap-2" role="group" aria-label="常用阈值">
          {basis && (
            <p className={cx('w-full text-caption', basis.warn ? 'text-warn' : 'text-fg-3', numeric)}>{basis.text}</p>
          )}
          <span className="text-caption text-fg-3">常用</span>
          {picks.map((pick) => (
            <Button
              key={pick.label}
              size="sm"
              aria-pressed={thresholdValue(kind, magnitude, direction) === pick.value}
              className={cx(numeric, 'aria-pressed:border-accent aria-pressed:text-accent')}
              onClick={() => {
                const split = splitThreshold(kind, pick.value)
                setMagnitude(split.magnitude)
                if (kind === 'day_change_pct') setDirection(split.direction)
                setErrors((current) => ({ ...current, threshold: undefined }))
              }}
            >
              {kind === 'stop_loss' || kind === 'target_price' ? `${pick.label} · ${fmtTickerPrice(pick.value, complete!, currency)}` : pick.label}
            </Button>
          ))}
        </div>
      )}

      <Field
        label="账户"
        error={errors.account}
        hint={kind === 'position_weight_max' ? '不选账户时按整个组合计算仓位' : '触发后记卖出时预填这个账户；不选则不限账户'}
      >
        <Select
          value={account}
          onChange={(event) => setAccount(event.target.value)}
          options={[
            { value: WHOLE, label: wholeLabel },
            ...accounts.map((item) => ({ value: item, label: item })),
            // An account the ledger does not list (an old rule, a renamed account) stays visible, never hidden.
            ...(account && !accounts.includes(account) ? [{ value: account, label: `${account}（账本中没有）` }] : []),
          ]}
        />
      </Field>

      <Field label="备注" error={errors.note} hint="选填，触发时一起显示">
        <Input value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} placeholder="例如：跌破就减半仓" />
      </Field>

      {errors.form && <InlineAlert tone="warn">{errors.form}</InlineAlert>}
      <p className="text-caption text-fg-3">提醒只在后端运行、市场交易时检查；只发通知，不会下单。</p>
    </form>
  )
}

/** The dialog, mounted once by the shell. */
export default function AlertRuleDialog() {
  const draft = useAlertDialog((state) => state.draft)
  const rule = useAlertDialog((state) => state.rule)
  const close = useAlertDialog((state) => state.close)
  const { readOnlyReason } = useAppMode()
  const formId = useId()
  return (
    <Dialog
      open={draft !== null}
      onClose={close}
      title={rule ? '编辑提醒' : '新建提醒'}
      closeOnBackdrop={false}
      footer={(
        <>
          <Button variant="ghost" onClick={close}>取消</Button>
          <Button variant="primary" type="submit" form={formId} disabledReason={readOnlyReason} tipAlign="end">
            {rule ? '保存修改' : '保存提醒'}
          </Button>
        </>
      )}
    >
      {/* The dialog mounts its content on open, so every opening starts a fresh form. */}
      {draft && <AlertForm draft={draft} formId={formId} onDone={close} />}
    </Dialog>
  )
}
