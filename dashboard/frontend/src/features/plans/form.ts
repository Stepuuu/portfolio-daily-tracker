// The new / edit form of a plan: its state, the check of what only the user can leave out, the request
// body (decimal strings, empty becomes null, planned weight XOR planned quantity) and the loss estimate.
// Everything else (price order, ranges, a second active plan) is the backend's to judge; its Chinese
// message is shown as it is.
import { fmtNumber } from '../../lib/format.ts'
import { decMul, decPositive, decSub } from '../ledger/decimal.ts'
import type { Plan, PlanBody, PlanEdit } from './types.ts'

export type SizeMode = 'weight' | 'quantity'

export interface PlanForm {
  ticker: string
  /** '' = every account. */
  account: string
  thesis: string
  invalidation: string
  entryLow: string
  entryHigh: string
  stop: string
  target: string
  sizeMode: SizeMode
  size: string
  reviewOn: string
}

/** The review date a new plan starts with: a month from today. */
export const REVIEW_DAYS = 30

/** "2026-11-17" plus `days`, as "YYYY-MM-DD" (calendar arithmetic in UTC, no time zone involved). */
export function addDays(date: string, days: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return null
  // exact-ok: calendar date arithmetic on year, month and day, not a money or price figure
  const moved = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days))
  return moved.toISOString().slice(0, 10)
}

export function emptyForm(today: string | null, preset: Partial<PlanForm> = {}): PlanForm {
  return {
    ticker: '', account: '', thesis: '', invalidation: '', entryLow: '', entryHigh: '', stop: '', target: '',
    sizeMode: 'weight', size: '', reviewOn: (today && addDays(today, REVIEW_DAYS)) || '',
    ...preset,
  }
}

const text = (value: string | null) => value ?? ''

export function formFromPlan(plan: Plan): PlanForm {
  return {
    ticker: plan.ticker,
    account: text(plan.account),
    thesis: plan.thesis,
    invalidation: plan.invalidation,
    entryLow: text(plan.entry_low),
    entryHigh: text(plan.entry_high),
    stop: text(plan.stop_price),
    target: text(plan.target_price),
    sizeMode: plan.planned_quantity !== null ? 'quantity' : 'weight',
    size: text(plan.planned_quantity ?? plan.planned_weight_pct),
    reviewOn: text(plan.review_on),
  }
}

export type FormField = 'ticker' | 'thesis' | 'invalidation'

/** What is missing before anything is sent: the three required fields. */
export function missingFields(form: PlanForm): Partial<Record<FormField, string>> {
  const errors: Partial<Record<FormField, string>> = {}
  if (!form.ticker.trim()) errors.ticker = '请从列表中选择标的（可输入代码或名称搜索）。'
  if (!form.thesis.trim()) errors.thesis = '请写下为什么买'
  if (!form.invalidation.trim()) errors.invalidation = '请写下什么情况说明你错了'
  return errors
}

const orNull = (value: string): string | null => {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * The request body. Numbers stay the strings the user typed (the backend normalises them); an empty
 * field is null, which also clears it on an edit. Only the chosen size is sent: the other is null.
 */
export function formToBody(form: PlanForm): PlanBody {
  const size = orNull(form.size)
  return {
    ticker: form.ticker.trim(),
    account: orNull(form.account),
    direction: 'buy',
    thesis: form.thesis.trim(),
    invalidation: form.invalidation.trim(),
    entry_low: orNull(form.entryLow),
    entry_high: orNull(form.entryHigh),
    stop_price: orNull(form.stop),
    target_price: orNull(form.target),
    planned_weight_pct: form.sizeMode === 'weight' ? size : null,
    planned_quantity: form.sizeMode === 'quantity' ? size : null,
    review_on: orNull(form.reviewOn),
  }
}

const KEPT_WHEN_ACTIVE: (keyof PlanBody)[] = ['ticker', 'account', 'direction']

/**
 * The PATCH body of an edit: only the fields that differ from the plan the form was opened with, and that
 * plan's `version` as `if_version`, so the backend refuses the edit (409) when the plan changed meanwhile
 * instead of a stale form reverting it. An active plan keeps its ticker, account and direction. null when
 * nothing changed (nothing to send).
 */
export function editRequest(plan: Plan, form: PlanForm): PlanEdit | null {
  const before = formToBody(formFromPlan(plan))
  const after = formToBody(form)
  const changes: Partial<PlanBody> = {}
  for (const key of Object.keys(after) as (keyof PlanBody)[]) {
    if (plan.status === 'active' && KEPT_WHEN_ACTIVE.includes(key)) continue
    if (after[key] !== before[key]) (changes as Record<string, unknown>)[key] = after[key]
  }
  if (!Object.keys(changes).length) return null
  return plan.version ? { ...changes, if_version: plan.version } : changes
}

const CURRENCY_BY_EXCHANGE: Record<string, string> = { SHA: 'CNY', SHE: 'CNY', HKG: 'HKD', NASDAQ: 'USD', NYSE: 'USD' }
// B shares (as core/market/instruments.py): six-digit codes with these prefixes trade in foreign currency,
// although the ledger books them in CNY. A plan's prices and its loss are in the trading currency.
const B_SHARES: Record<string, { prefixes: string[]; currency: string }> = {
  SHA: { prefixes: ['900'], currency: 'USD' },
  SHE: { prefixes: ['200', '201'], currency: 'HKD' },
}

/** The trading currency of a ledger ticker such as SHA:600519 (USD for SHA:900901), or null while it is not one. */
export function currencyOf(ticker: string): string | null {
  const [exchange, code = ''] = ticker.trim().toUpperCase().split(':')
  const currency = CURRENCY_BY_EXCHANGE[exchange]
  if (!currency) return null
  const b = B_SHARES[exchange]
  return b && /^\d{6}$/.test(code) && b.prefixes.some((prefix) => code.startsWith(prefix)) ? b.currency : currency
}

export interface LossEstimate {
  /** The exact product, every digit kept (round only when shown). */
  amount: string
  currency: string
  /** The backend's sentence, without its currency conversion (done after saving). */
  formula: string
  /** A stop at or above the entry: the amount is 0 and this is the gain the stop locks in. */
  lockedGain?: string
}

/**
 * The backend's formula, `(entry - stop) x planned quantity`, with the entry at the top of the range
 * (or the lower bound when that is all there is). Only with a planned quantity: with a weight the
 * backend does not compute a loss either. In the ticker's trading currency (USD or HKD for a B share);
 * the conversion to CNY is the backend's, after saving. A stop at or above the entry gives 0 and the gain it
 * locks in (`lockedGain`), as the backend does. Exact decimal arithmetic
 * (features/ledger/decimal), never binary floats; the amount is shown to the cent like the backend's.
 */
export function estimateMaxLoss(form: PlanForm): LossEstimate | null {
  if (form.sizeMode !== 'quantity') return null
  const currency = currencyOf(form.ticker)
  if (!currency) return null
  const high = orNull(form.entryHigh)
  const entry = high ?? orNull(form.entryLow)
  const stop = orNull(form.stop)
  const quantity = orNull(form.size)
  if (entry === null || stop === null || quantity === null) return null
  if (!decPositive(entry) || !decPositive(stop) || !decPositive(quantity)) return null
  const gap = decSub(entry, stop)
  if (gap === null) return null
  const label = high !== null ? '入场价上限' : '入场价下限'
  if (!decPositive(gap)) {
    // An active plan's stop moved to breakeven or into profit: no loss (never a negative one), a locked-in gain.
    const rise = decSub(stop, entry)
    const gain = rise === null ? null : decMul(rise, quantity)
    if (gain === null) return null
    const [shownStop, shownEntry] = [fmtNumber(stop, { exact: true }), fmtNumber(entry, { exact: true })]
    return {
      amount: '0',
      currency,
      lockedGain: gain,
      formula: `止损价 ${shownStop} 不低于${label} ${shownEntry}：触及止损时不亏损，按计划股数 ${quantity} 锁定盈利 (${shownStop} − ${shownEntry}) × ${quantity} = ${fmtNumber(gain)} ${currency}`,
    }
  }
  const amount = decMul(gap, quantity)
  if (amount === null) return null
  return {
    amount,
    currency,
    formula: `(${label} ${fmtNumber(entry, { exact: true })} − 止损价 ${fmtNumber(stop, { exact: true })}) × 计划股数 ${quantity} = ${fmtNumber(amount)} ${currency}`,
  }
}
