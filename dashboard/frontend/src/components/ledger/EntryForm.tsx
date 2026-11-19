// The 记一笔 form (plan EN-1). It starts from what the dialog was opened with and otherwise from the last
// account and kind used, the date the backend's calendar gives (a trade: the latest verified session of its
// market; cash: today; none when the calendar cannot say) and the account's fee profile, so an A-share buy
// needs three typed values: 标的, 数量, 价格. Enter previews; nothing is booked until the review is confirmed.
//
// What is checked is the backend's: holdings and cash are the ledger as of the entry's date (a back-dated
// sale is checked against what was held that day), and the fee left to the estimate is estimated again at
// 预览入账 against the account's current profile. Fees, prices and FX rates that did not come from the
// person keep their origin (features/ledger/entry.ts `provenanceMarks`). Logic lives in
// features/ledger/entry.ts and lib/entryDefaults.ts.
import { useEffect, useMemo, useRef, useState, type FormEvent, type MutableRefObject, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, MessageSquare } from 'lucide-react'
import {
  Badge, Button, Checkbox, DateInput, Field, InlineAlert, Input, MoneyInput, NumberInput, Select, cx, numeric,
} from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { TickerInput } from '@/components/inputs/TickerInput'
import type { InstrumentResult } from '@/services/instruments'
import { asOfKey, fetchAsOf, fetchFeeEstimate } from '@/services/ledger'
import { fetchQuotes } from '@/services/today'
import { fmtMoney, fmtQty } from '@/lib/format'
import {
  browserStorage, dateLabel, defaultAccount, defaultCurrency, defaultDate, defaultKind, marketOfTicker, type LedgerCalendar,
} from '@/lib/entryDefaults'
import { shortTime } from '@/features/today/quoteStatus'
import { decMul, decPositive, isDecimal } from '@/features/ledger/decimal'
import {
  CURRENCIES, KIND_FIELDS, TICKER_HINT, bShareRefusal, buildEvents, cashEffect, emptyValues, entryCurrency, feeToBook, lotStep,
  needsFxRate, parseTicker, resolveTicker, tickerCurrency, validateEntry, withCurrency, withTicker,
  type Currency, type EntryContext, type EntryErrors, type EntryValues, type EventInput,
} from '@/features/ledger/entry'
import { heldPosition, heldQuantity } from '@/features/ledger/events'
import { isEntryKind, type EntryKind } from '@/features/ledger/kinds'
import type { FeeEstimate, LedgerAccount, LedgerAsOf } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useTodayFeed } from '@/components/shell/useTodayFeed'
import { useShell } from '@/components/shell/shellStore'
import type { EntryDraft } from './EntryDialog'
import { FeeField } from './FeeField'
import { FxField, ReferenceRate } from './FxField'
import { KindPicker } from './KindPicker'
import { OpeningFields } from './OpeningFields'
import { QuoteHint } from './QuoteHint'
import { useLedger, useLedgerAsOf, useLedgerCalendar } from './useLedger'

export interface EntryFormProps {
  /** The form's id; the dialog's 预览入账 button submits it from the footer. */
  id: string
  draft: EntryDraft
  /** Preview these events. Resolves when the review is showing; throws with the reason otherwise. */
  onPreview: (events: EventInput[], values: EntryValues) => Promise<void>
  onBusyChange: (busy: boolean) => void
  /** Leave the dialog (the assistant link). */
  onClose: () => void
  /** Kept up to date with the field focused last, so 返回修改 can return there. */
  lastFieldRef?: MutableRefObject<HTMLElement | null>
}

type FeeParams = { account: string; ticker: string; side: 'buy' | 'sell'; quantity: string; price: string; date: string }

const feeKey = (params: FeeParams | null) => ['accounts', 'fee-estimate', params] as const

/** Events from these origins keep it as their `source`; the rest were typed here (`manual`). */
const KEPT_SOURCES = new Set(['import', 'screenshot', 'assistant'])

const MARGIN_TEXT = '现金不足：勾选“允许融资”可记为负余额，或减少数量'

function useDebounced(value: string, delay: number): string {
  const [current, setCurrent] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setCurrent(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return current
}

const positive = (value: string) => isDecimal(value) && decPositive(value)
const isDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value)

function initialValues(draft: EntryDraft, accounts: string[], known: boolean, calendar: LedgerCalendar | null): EntryValues {
  const storage = browserStorage()
  const kind = defaultKind(!known || accounts.length > 0, storage, draft.kind)
  const currency = draft.currency && (CURRENCIES as string[]).includes(draft.currency) ? draft.currency as Currency : defaultCurrency(storage)
  const trade = KIND_FIELDS[kind].trade
  return emptyValues({
    kind,
    account: kind === 'opening' ? '' : defaultAccount(accounts, storage, draft.account),
    date: defaultDate(calendar, { trade, market: trade ? marketOfTicker(draft.ticker) : null, requested: draft.date }).date,
    ticker: draft.ticker ?? '',
    name: draft.name ?? '',
    quantity: draft.quantity ?? '',
    price: draft.price ?? '',
    note: draft.note ?? '',
    currency,
  })
}

/** The account on the entry's date: undefined while not known, null when it did not exist yet. */
function accountOn(day: LedgerAsOf | undefined, account: string): LedgerAccount | null | undefined {
  if (!day) return undefined
  return day.accounts[account] ?? null
}

export function EntryForm({ id, draft, onPreview, onBusyChange, onClose, lastFieldRef }: EntryFormProps) {
  const client = useQueryClient()
  const ledger = useLedger()
  const feed = useTodayFeed()
  const { calendar } = useLedgerCalendar()
  const { readOnlyReason } = useAppMode()
  const accounts = useMemo(() => Object.keys(ledger.data?.accounts ?? {}), [ledger.data])
  const known = ledger.data !== undefined
  const today = calendar?.today ?? null

  const [values, setValues] = useState<EntryValues>(() => initialValues(draft, accounts, known, calendar))
  const [touched, setTouched] = useState({ date: Boolean(draft.date), currency: Boolean(draft.currency) })
  const [submitted, setSubmitted] = useState(false)
  const [selected, setSelected] = useState<InstrumentResult | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const busy = useRef(false)
  const formRef = useRef<HTMLFormElement>(null)
  const patch = (next: Partial<EntryValues>) => setValues((current) => ({ ...current, ...next }))

  // Data that arrives after the dialog opened fills what is still at its default.
  useEffect(() => {
    if (!known) return
    if (!accounts.length) {
      setValues((current) => (current.kind === 'opening' ? current : { ...current, kind: 'opening', account: '' }))
    } else {
      setValues((current) => (current.kind === 'opening' || current.account ? current
        : { ...current, account: defaultAccount(accounts, browserStorage(), draft.account) }))
    }
  }, [known, accounts, draft.account])

  const fields = KIND_FIELDS[values.kind]
  const currentAccount = ledger.data?.accounts[values.account]
  const held = useMemo(() => Object.values(currentAccount?.positions ?? {}).map((p) => ({ ticker: p.ticker, name: p.name })), [currentAccount])
  const ticker = selected && parseTicker(values.ticker) === selected.ticker ? selected.ticker : resolveTicker(values.ticker, held)
  const bShare = bShareRefusal(ticker)

  // -- date: from the backend's calendar; none when it cannot say ------------------------------------
  const dated = defaultDate(calendar, { trade: fields.trade, market: fields.trade ? marketOfTicker(ticker) : null, requested: draft.date })
  useEffect(() => {
    if (!touched.date) setValues((current) => (current.date === dated.date ? current : { ...current, date: dated.date }))
  }, [dated.date, touched.date])
  const setDate = (date: string) => {
    setTouched((t) => ({ ...t, date: true }))
    patch({ date })
  }

  // -- the ledger on the entry's date, by the backend ------------------------------------------------
  const dateOk = isDate(values.date)
  const asOf = useLedgerAsOf(dateOk ? values.date : null)
  const dayAccount = accountOn(asOf.data && asOf.data.as_of === values.date ? asOf.data : undefined, values.account)
  const heldQty = dayAccount === undefined ? undefined : heldQuantity(dayAccount ?? undefined, ticker)
  const dayCash = dayAccount === undefined ? undefined : dayAccount?.cash_balances ?? {}
  const backdated = Boolean(today && dateOk && values.date !== today)

  const currency = entryCurrency(values, ticker)
  const tradeCurrency = tickerCurrency(ticker) ?? 'CNY'

  // A dividend in HKD or USD follows its stock's market unless the currency was chosen by hand.
  useEffect(() => {
    if (values.kind === 'dividend' && ticker && !touched.currency) {
      const market = tickerCurrency(ticker)
      if (market) setValues((current) => withCurrency(current, market))
    }
  }, [values.kind, ticker, touched.currency])

  // A price filled from another ticker's quote goes with that ticker.
  useEffect(() => {
    setValues((current) => (current.priceFill && current.priceFill.ticker !== ticker ? withTicker(current, current.ticker, ticker) : current))
  }, [ticker])

  // FX: never prefilled; the reference rate is offered and, when taken, recorded as such.
  const fx = values.currency !== 'CNY' ? feed.data?.fx?.[values.currency] : undefined
  const wantsRate = needsFxRate(values)

  // -- quote ---------------------------------------------------------------------------------------
  const quoteQuery = useQuery({
    queryKey: ['quotes', ticker],
    queryFn: () => fetchQuotes([ticker!]),
    enabled: Boolean(ticker) && fields.trade && !bShare,
    staleTime: 5_000,
    refetchInterval: 15_000,
    retry: false,
  })
  const quotes = quoteQuery.data?.quotes ?? []
  const quote = quotes.find((item) => item.ticker === ticker) ?? (quotes.length === 1 ? quotes[0] : undefined)
  const heldName = heldPosition(currentAccount, ticker)?.name
  const name = (selected?.ticker === ticker ? selected?.name : null)
    ?? (heldName && heldName !== ticker ? heldName : null)
    ?? (draft.ticker && parseTicker(draft.ticker) === ticker ? draft.name : null)
    ?? (fields.trade ? quote?.name : null)
    ?? null

  // -- fee estimate: shown only for exactly the inputs on screen -------------------------------------
  const side = values.kind === 'sell' ? 'sell' : 'buy'
  const paramsFor = (quantity: string, price: string): FeeParams | null =>
    fields.trade && ticker && !bShare && values.account && positive(quantity) && positive(price) && dateOk
      ? { account: values.account, ticker, side, quantity, price, date: values.date }
      : null
  const currentParams = paramsFor(values.quantity, values.price)
  const currentKey = currentParams ? JSON.stringify(currentParams) : ''
  // A string, not an object: the debounce compares values, and a new object every render would never settle.
  const settledKey = useDebounced(currentKey, 300)
  const feeParams: FeeParams | null = settledKey ? JSON.parse(settledKey) : null
  const feeQuery = useQuery({
    queryKey: feeKey(feeParams),
    queryFn: ({ signal }) => fetchFeeEstimate(feeParams!, signal),
    enabled: Boolean(feeParams),
    staleTime: 60_000,
    retry: false,
  })
  const matching = Boolean(currentParams) && settledKey === currentKey
  const estimate: FeeEstimate | null = matching && feeQuery.data ? feeQuery.data : null
  const estimateFailed = matching && feeQuery.isError
  const estimateLoading = Boolean(currentParams) && !estimate && !estimateFailed

  const context: EntryContext = {
    accounts, ticker, held: heldQty, feeEstimate: estimate?.total ?? null, feeBasis: estimate?.basis ?? null,
    source: draft.source && KEPT_SOURCES.has(draft.source) ? draft.source : undefined,
  }
  const effect = cashEffect(values, ticker, dayCash, fields.trade ? feeToBook(values, estimate?.total ?? null) : null)
  const short = Boolean(effect?.short) && fields.margin

  let errors: EntryErrors = {}
  if (submitted) {
    errors = validateEntry(values, context)
    if (fields.trade && !values.fee && estimateLoading) delete errors.fee
    if (short && !values.allowMargin) errors.margin = MARGIN_TEXT
  }
  // A B share is refused as soon as it is chosen, not only at 预览入账.
  if (bShare && fields.ticker && !errors.ticker) errors = { ...errors, ticker: bShare }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    // One preview at a time, never in read-only mode (the hidden submit has no guards of its own).
    if (busy.current || readOnlyReason) return
    busy.current = true
    onBusyChange(true)
    setSubmitted(true)
    try {
      // What the backend says now: the ledger at the entry's date, and the fee at the account's current profile.
      let day: LedgerAsOf | undefined
      if (dateOk && ledger.data) {
        try {
          day = await client.fetchQuery({
            queryKey: asOfKey(values.date, ledger.data.revision), queryFn: () => fetchAsOf(values.date), staleTime: 0,
          })
        } catch {
          day = undefined   // not known: the preview decides
        }
      }
      let fresh: FeeEstimate | null = null
      if (fields.trade && !values.fee && currentParams) {
        try {
          fresh = await fetchFeeEstimate(currentParams)
          client.setQueryData(feeKey(currentParams), fresh)
        } catch {
          fresh = null
        }
      }
      const account = accountOn(day, values.account)
      const ready = { ...values, name: name ?? '' }
      const check: EntryContext = {
        ...context,
        held: account === undefined ? undefined : heldQuantity(account ?? undefined, ticker),
        feeEstimate: fields.trade && !values.fee ? fresh?.total ?? null : context.feeEstimate,
        feeBasis: fields.trade && !values.fee ? fresh?.basis ?? null : context.feeBasis,
      }
      const found = validateEntry(ready, check)
      const after = cashEffect(ready, ticker, account === undefined ? undefined : account?.cash_balances ?? {},
        fields.trade ? feeToBook(ready, check.feeEstimate) : null)
      if (fields.margin && after?.short && !values.allowMargin) found.margin = MARGIN_TEXT
      const problems = Object.values(found).filter(Boolean)
      if (problems.length) {
        // The focused field reads its own message (aria-describedby); this says how many there are.
        setAnnouncement(`有 ${problems.length} 处需要修改，请查看标出的字段`)
        window.requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus())
        return
      }
      setAnnouncement('')
      await onPreview(buildEvents(ready, check), ready)
    } finally {
      busy.current = false
      onBusyChange(false)
    }
  }

  const noAccounts = known && accounts.length === 0
  const lot = fields.trade ? lotStep(ticker) : null
  const setKind = (kind: EntryKind) => {
    if (!isEntryKind(kind)) return
    setSubmitted(false)
    setValues((current) => ({
      ...current, kind,
      account: kind === 'opening' ? '' : current.account || defaultAccount(accounts, browserStorage(), draft.account),
    }))
  }

  // -- 标的 ------------------------------------------------------------------------------------------
  let tickerHint: ReactNode = fields.ticker === 'optional' ? '分红、费用可关联一只标的，也可留空' : TICKER_HINT
  if (ticker) {
    const parts = [name, ticker, fields.trade || values.kind === 'split' ? tradeCurrency : null].filter(Boolean)
    tickerHint = <span className={cx('text-fg-2', numeric)}>{parts.join(' · ')}</span>
  } else if (values.ticker.trim() && parseTicker(values.ticker) === null) {
    tickerHint = TICKER_HINT
  }
  const sellFilter = values.kind === 'sell' || values.kind === 'split'
    ? (choice: InstrumentResult) => Boolean(heldPosition(currentAccount, choice.ticker))
    : undefined
  const autofocusTicker = !draft.ticker && fields.ticker === 'required'

  // -- 数量: what can be sold is what was held on the entry's date ------------------------------------
  const all = heldQty && values.kind === 'sell' ? (
    <Button size="sm" variant="ghost" className="-my-1 ml-1 text-accent hover:text-accent" onClick={() => patch({ quantity: heldQty })}>全部</Button>
  ) : null
  let quantityHint: ReactNode = lot ? 'A 股按 100 股一手' : undefined
  if (values.kind === 'sell' && ticker && dateOk) {
    if (heldQty) quantityHint = <span className={numeric}>可卖 {fmtQty(heldQty)} 股{backdated ? `（${values.date} 日终）` : ''}{all}</span>
    else if (asOf.isError) quantityHint = '暂时读不到该日的持仓，以预览结果为准'
    else if (heldQty === undefined) quantityHint = '正在读取该日的持仓…'
  }
  const quantityError = errors.quantity && all ? <>{errors.quantity}{all}</> : errors.quantity

  // -- 价格: a filled price shows where it came from until it is edited -------------------------------
  const fill = values.priceFill && values.priceFill.value === values.price ? values.priceFill : null
  const priceHint = fill ? (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
      <span className={numeric}>
        按{fill.basis === 'price' ? '现价' : '昨收'}填入{fill.source ? ` · ${fill.source}` : ''}{fill.time ? ` ${shortTime(fill.time, today)}` : ''}
      </span>
      {fill.estimated && <Badge tone="warn" title="行情未经核实，请按成交回报核对">估算</Badge>}
    </span>
  ) : undefined

  // -- 日期 ------------------------------------------------------------------------------------------
  const todayLabel = dateLabel(values.date, calendar)
  let dateHint: ReactNode
  if (!values.date) dateHint = dated.reason
  else if (values.date === dated.date && dated.reason) {
    dateHint = (
      <span>
        {dated.reason}
        {today && today !== values.date && (
          <Button size="sm" variant="ghost" className="-my-1 ml-1 text-accent hover:text-accent" onClick={() => setDate(today)}>改为今天</Button>
        )}
      </span>
    )
  } else if (todayLabel) dateHint = todayLabel

  return (
    <form
      id={id}
      ref={formRef}
      onSubmit={(event) => void submit(event)}
      onFocus={(event) => {
        if (lastFieldRef && event.target instanceof HTMLElement && event.target.matches('input, select, textarea')) lastFieldRef.current = event.target
      }}
      noValidate
      className="flex flex-col gap-4"
    >
      <p role="alert" className="sr-only">{announcement}</p>
      {ledger.isError && !known && (
        <InlineAlert tone="warn" title="读不到账户" action={<Button size="sm" onClick={() => void ledger.refetch()}>重试</Button>}>
          {getApiErrorMessage(ledger.error)}
        </InlineAlert>
      )}
      <KindPicker value={values.kind} onChange={setKind} disabled={noAccounts ? (kind) => kind !== 'opening' : undefined} />

      <div className="grid gap-4 sm:grid-cols-2">
        {fields.opening ? (
          <Field label="新账户名称" required error={errors.account} hint={noAccounts ? '账本还没有账户：先记下期初的现金和持仓' : undefined}>
            <Input value={values.account} onChange={(event) => patch({ account: event.target.value })} maxLength={240} placeholder="如：长期账户" data-autofocus />
          </Field>
        ) : (
          <Field label="账户" required error={errors.account}>
            <Select
              value={values.account}
              onChange={(event) => patch({ account: event.target.value })}
              placeholder={ledger.isPending ? '正在读取账户…' : '选择账户'}
              options={accounts.map((account) => ({ value: account, label: account }))}
            />
          </Field>
        )}
        <Field label="日期" required error={errors.date} hint={dateHint}>
          <DateInput value={values.date} onChange={(event) => setDate(event.target.value)} />
        </Field>
      </div>

      {fields.ticker && (
        <div className="flex flex-col gap-1">
          <Field label={fields.ticker === 'optional' ? '标的（选填）' : '标的'} required={fields.ticker === 'required'} error={errors.ticker} hint={tickerHint}>
            <TickerInput
              value={values.ticker}
              onChange={(text) => {
                patch({ ticker: text })
                if (selected && text !== selected.ticker) setSelected(null)
              }}
              onSelect={(choice) => {
                setSelected(choice)
                if (choice.ticker) patch({ ticker: choice.ticker, name: choice.name ?? '' })
              }}
              filter={sellFilter}
              {...(autofocusTicker ? { 'data-autofocus': true } : {})}
            />
          </Field>
          {fields.trade && ticker && !bShare && (
            <QuoteHint
              ticker={ticker}
              currency={tradeCurrency}
              quote={quote}
              loading={quoteQuery.isPending}
              failed={quoteQuery.isError}
              today={today}
              onPrice={(price, priceFill) => patch({ price, priceFill })}
            />
          )}
        </div>
      )}

      {fields.trade && (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="数量" required error={quantityError} hint={quantityHint}>
              <NumberInput
                value={values.quantity}
                onValueChange={(quantity) => patch({ quantity })}
                step={lot ?? undefined}
                stepButtons={Boolean(lot)}
                suffix="股"
                {...(!autofocusTicker ? { 'data-autofocus': true } : {})}
              />
            </Field>
            <Field label="价格" required error={errors.price} hint={priceHint}>
              <MoneyInput currency={tradeCurrency} value={values.price} onValueChange={(price) => patch({ price, priceFill: null })} />
            </Field>
          </div>
          <FeeField
            currency={tradeCurrency}
            value={values.fee}
            onChange={(fee) => patch({ fee })}
            estimate={currentParams ? estimate : undefined}
            loading={estimateLoading}
            failed={estimateFailed}
            error={errors.fee}
          />
        </>
      )}

      {fields.amount && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={fields.amount} required error={errors.amount}>
            <MoneyInput
              currency={currency ?? 'CNY'}
              value={values.amount}
              onValueChange={(amount) => patch({ amount })}
              {...(!fields.ticker ? { 'data-autofocus': true } : {})}
            />
          </Field>
          {fields.currency === 'choose' && (
            <Field label={values.kind === 'fx' ? '换出币种' : '币种'}>
              <Select
                value={values.currency}
                onChange={(event) => {
                  setTouched((t) => ({ ...t, currency: true }))
                  setValues((current) => withCurrency(current, event.target.value as Currency))
                }}
                options={CURRENCIES.map((unit) => ({ value: unit, label: unit }))}
              />
            </Field>
          )}
          {fields.fundFee && (
            <Field label="费用" hint="申购或赎回手续费，没有可留空" error={errors.fee}>
              <MoneyInput currency="CNY" value={values.fee} onValueChange={(fee) => patch({ fee })} placeholder="0" />
            </Field>
          )}
        </div>
      )}

      {wantsRate && (
        <FxField
          currency={values.currency}
          value={values.fxRate}
          onChange={(fxRate) => patch({ fxRate, rateFill: null })}
          onUse={(rateFill) => patch({ fxRate: rateFill.value, rateFill })}
          fill={values.rateFill}
          fx={fx}
          today={today}
          date={values.date}
          error={errors.fxRate}
        />
      )}

      {fields.toAccount && (
        <Field label="转入账户" required error={errors.toAccount}>
          <Select
            value={values.toAccount}
            onChange={(event) => patch({ toAccount: event.target.value })}
            placeholder="选择账户"
            options={accounts.filter((account) => account !== values.account).map((account) => ({ value: account, label: account }))}
          />
        </Field>
      )}

      {fields.exchange && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="换入币种" error={errors.toCurrency}>
            <Select
              value={values.toCurrency}
              onChange={(event) => patch({ toCurrency: event.target.value as Currency })}
              options={CURRENCIES.map((unit) => ({ value: unit, label: unit }))}
            />
          </Field>
          <Field
            label="实际到账金额"
            required
            error={errors.receivedAmount}
            hint={(() => {
              const foreign = values.toCurrency !== 'CNY' ? values.toCurrency : values.currency !== 'CNY' ? values.currency : null
              return foreign ? <ReferenceRate currency={foreign} fx={feed.data?.fx?.[foreign]} today={today} /> : '扣除换汇费用后实际到账的金额'
            })()}
          >
            <MoneyInput currency={values.toCurrency} value={values.receivedAmount} onValueChange={(receivedAmount) => patch({ receivedAmount })} />
          </Field>
        </div>
      )}

      {fields.ratio && (
        <Field
          label="比例（新股数 ÷ 原股数）"
          required
          error={errors.ratio}
          hint={heldQty && positive(values.ratio)
            ? <span className={numeric}>持有 {fmtQty(heldQty)} 股，送转后 {fmtQty(decMul(heldQty, values.ratio))} 股</span>
            : '如 10 送 5 填 1.5，1 拆 2 填 2'}
        >
          <NumberInput value={values.ratio} onValueChange={(ratio) => patch({ ratio })} />
        </Field>
      )}

      {fields.opening && <OpeningFields values={values} onChange={patch} errors={errors} />}

      {effect && (effect.turnover || effect.after !== null) && (
        <CashLine effect={effect} on={backdated ? values.date : null} />
      )}
      {fields.margin && (short || values.allowMargin) && (
        <div className="flex flex-col gap-1">
          <Checkbox
            checked={values.allowMargin}
            onChange={(event) => patch({ allowMargin: event.target.checked })}
            label="允许融资"
            description="现金不足时记为负余额"
            aria-invalid={errors.margin ? true : undefined}
            aria-describedby={errors.margin ? `${id}-margin-error` : undefined}
          />
          {errors.margin && <p id={`${id}-margin-error`} className="text-table text-warn">{errors.margin}</p>}
        </div>
      )}

      <Field label="备注" error={errors.note}>
        <Input value={values.note} onChange={(event) => patch({ note: event.target.value })} maxLength={240} placeholder="选填：为什么做这笔交易" />
      </Field>
      {draft.planId !== undefined && draft.planId !== null && draft.planId !== '' && (
        <p className="-mt-2 text-table text-fg-3">来自交易计划 #{draft.planId}；确认前会对照计划检查。</p>
      )}

      <p className="flex flex-wrap items-center gap-x-1 text-table text-fg-3">
        也可以：
        <Button
          size="sm"
          variant="ghost"
          icon={MessageSquare}
          className="text-accent hover:text-accent"
          onClick={() => {
            onClose()
            useShell.getState().openAssistant('帮我记一笔：')
          }}
        >
          用一句话描述 → 助手
        </Button>
      </p>
      {/* Enter in any field submits (the visible button is in the dialog footer); `submit` guards both. */}
      <button type="submit" tabIndex={-1} aria-hidden className="sr-only">预览入账</button>
    </form>
  )
}

/** 成交金额 … · CNY 现金 before → after (on the entry's date), with 现金不足 below zero. For display only. */
function CashLine({ effect, on }: { effect: NonNullable<ReturnType<typeof cashEffect>>; on: string | null }) {
  const money = (value: string | null, unit: string) => fmtMoney(value, unit, { exact: true })
  const when = on ? `（${on} 日终）` : ''
  return (
    <div aria-live="polite" className={cx('flex flex-wrap items-center gap-x-3 gap-y-1 text-table text-fg-2', numeric)}>
      {effect.turnover && <span>成交金额 <span className="text-fg">{money(effect.turnover, effect.currency)}</span></span>}
      {effect.after !== null && (
        <span>
          {effect.currency} 现金{when} {money(effect.before, effect.currency)} → <span className={effect.short ? 'text-warn' : 'text-fg'}>{money(effect.after, effect.currency)}</span>
          {effect.feeUnknown && effect.turnover && <span className="text-fg-3">（未含费用）</span>}
        </span>
      )}
      {effect.received && effect.received.after !== null && (
        <span>
          {effect.received.currency} 现金{when} {money(effect.received.before, effect.received.currency)} → <span className="text-fg">{money(effect.received.after, effect.received.currency)}</span>
        </span>
      )}
      {effect.short && <Badge tone="warn" icon={AlertTriangle}>现金不足</Badge>}
    </div>
  )
}
