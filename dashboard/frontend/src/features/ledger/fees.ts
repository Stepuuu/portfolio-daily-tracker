// Fee estimates for the form, and the account fee profile as a settings form. Rates are stored as
// fractions of turnover ("0.00025") and edited in 万分之 (2.5 = 万2.5), the way brokers quote them.
// Conversions are exact (decimal strings), figures keep their digits, and the form checks the server's own
// bounds (core/fees.py: a rate at most 1% of turnover with 10 decimals as a fraction, a minimum or fixed fee
// at most 100,000 with 4 decimals). Pure TypeScript.
import { fmtNumber } from '../../lib/format.ts'
import { decCmp, decShift, decSign, isDecimal } from './decimal.ts'

/** core/fees.py MAX_RATE (0.01) in 万分之, and RATE_SCALE (10) less the 4 places of 万分之. */
const MAX_RATE_WAN = '100'
const RATE_WAN_PLACES = 6
/** core/fees.py MAX_AMOUNT and AMOUNT_SCALE. */
const MAX_AMOUNT = '100000'
const AMOUNT_PLACES = 4

function places(value: string): number {
  const fraction = value.trim().split('.')[1] ?? ''
  return fraction.replace(/0+$/, '').length
}
import type { FeeEstimate, FeeProfile, FeeSide } from './types.ts'

/** "佣金 5.00 · 过户费 0.06". */
export function feeBreakdown(estimate: Pick<FeeEstimate, 'parts'>): string {
  return estimate.parts.map((part) => `${part.label} ${fmtNumber(part.amount, { exact: true })}`).join(' · ')
}

/**
 * The line under the fee field: "按账户费率估算：佣金 5.00 · 过户费 0.06", or why there is no estimate.
 * The form shows the estimate as the field's placeholder; an empty field books it.
 */
export function feeHint(estimate: FeeEstimate | null | undefined): string | null {
  if (!estimate) return null
  if (estimate.total === null) return estimate.reason ?? '没有可用的费率估算'
  const parts = feeBreakdown(estimate)
  return parts ? `${estimate.basis}：${parts}` : estimate.basis
}

// ------------------------------------------------------------------------------------------------
// The profile form

export interface SideForm {
  rate: string
  min: string
  fixed: string
}

export interface MarketForm {
  enabled: boolean
  buy: SideForm
  sell: SideForm
}

export interface ProfileForm {
  broker: string
  /** 万分之. */
  commissionRate: string
  commissionMin: string
  waiveMinimum: boolean
  hk: MarketForm
  us: MarketForm
}

const EMPTY_SIDE: SideForm = { rate: '', min: '', fixed: '' }

/** Fraction → 万分之: "0.00025" → "2.5". */
export function toWan(fraction: string | null | undefined): string {
  return decShift(fraction, 4) ?? ''
}

/** 万分之 → fraction: "2.5" → "0.00025". */
export function fromWan(wan: string): string | null {
  return decShift(wan, -4)
}

function sideForm(side: FeeSide | null | undefined): SideForm {
  return side ? { rate: toWan(side.rate), min: side.min, fixed: side.fixed } : { ...EMPTY_SIDE }
}

function marketForm(model: FeeProfile['hk']): MarketForm {
  return { enabled: Boolean(model && (model.buy || model.sell)), buy: sideForm(model?.buy), sell: sideForm(model?.sell) }
}

export function profileToForm(profile: FeeProfile): ProfileForm {
  return {
    broker: profile.broker ?? '',
    commissionRate: toWan(profile.a_share.commission_rate),
    commissionMin: profile.a_share.commission_min,
    waiveMinimum: profile.a_share.waive_minimum,
    hk: marketForm(profile.hk),
    us: marketForm(profile.us),
  }
}

export type ProfileErrors = Partial<Record<string, string>>

function nonNegative(value: string): boolean {
  return isDecimal(value) && decSign(value) !== -1
}

/** What is wrong with a rate in 万分之 that is a non-negative number; null when the server accepts it. */
function rateProblem(wan: string, name: string): string | null {
  if (decCmp(wan, MAX_RATE_WAN) === 1) return `${name}最高 100‱（成交额的 1%）`
  if (places(wan) > RATE_WAN_PLACES) return `${name}最多 ${RATE_WAN_PLACES} 位小数（万分之）`
  return null
}

/** What is wrong with a minimum or fixed fee that is a non-negative number; null when the server accepts it. */
function amountProblem(value: string): string | null {
  if (places(value) > AMOUNT_PLACES) return `最多 ${AMOUNT_PLACES} 位小数`
  if (decCmp(value, MAX_AMOUNT) === 1) return '最多 100,000'
  return null
}

function readSide(side: SideForm, prefix: string, errors: ProfileErrors): FeeSide | null {
  const blank = !side.rate && !side.min && !side.fixed
  if (blank) return null
  for (const key of ['rate', 'min', 'fixed'] as const) {
    const problem = side[key] && nonNegative(side[key]) ? (key === 'rate' ? rateProblem(side[key], '费率') : amountProblem(side[key])) : null
    if (!side[key]) errors[`${prefix}.${key}`] = '请填写（不收的项填 0）'
    else if (!nonNegative(side[key])) errors[`${prefix}.${key}`] = '请输入不小于 0 的数字'
    else if (problem) errors[`${prefix}.${key}`] = problem
  }
  const rate = fromWan(side.rate)
  return rate === null ? null : { rate, min: side.min, fixed: side.fixed }
}

/** The profile to save (`PUT /api/accounts/profile`), or the errors by field. */
export function formToProfile(form: ProfileForm): { profile: Omit<FeeProfile, 'is_default' | 'account' | 'updated_at'> | null; errors: ProfileErrors } {
  const errors: ProfileErrors = {}
  if (form.broker.length > 40) errors.broker = '券商名称最多 40 个字'
  if (!form.commissionRate) errors.commissionRate = '请填写佣金率'
  else if (!nonNegative(form.commissionRate)) errors.commissionRate = '请输入不小于 0 的数字'
  else if (rateProblem(form.commissionRate, '佣金率')) errors.commissionRate = rateProblem(form.commissionRate, '佣金率')!
  if (!form.waiveMinimum) {
    if (!form.commissionMin) errors.commissionMin = '请填写最低佣金（免五时可不填）'
    else if (!nonNegative(form.commissionMin)) errors.commissionMin = '请输入不小于 0 的数字'
    else if (amountProblem(form.commissionMin)) errors.commissionMin = amountProblem(form.commissionMin)!
  } else if (form.commissionMin && !nonNegative(form.commissionMin)) {
    errors.commissionMin = '请输入不小于 0 的数字'
  } else if (form.commissionMin && amountProblem(form.commissionMin)) {
    errors.commissionMin = amountProblem(form.commissionMin)!
  }
  const market = (value: MarketForm, prefix: string) => {
    if (!value.enabled) return null
    const buy = readSide(value.buy, `${prefix}.buy`, errors)
    const sell = readSide(value.sell, `${prefix}.sell`, errors)
    if (!buy && !sell) {
      errors[`${prefix}.buy.rate`] = '至少填写买入或卖出一边，或关闭这个市场'
      return null
    }
    return { buy, sell }
  }
  const hk = market(form.hk, 'hk')
  const us = market(form.us, 'us')
  if (Object.keys(errors).length) return { profile: null, errors }
  return {
    profile: {
      broker: form.broker.trim(),
      a_share: {
        commission_rate: fromWan(form.commissionRate)!,
        commission_min: form.commissionMin || '0',
        waive_minimum: form.waiveMinimum,
      },
      hk,
      us,
    },
    errors,
  }
}

/** "万2.5 · 最低 ¥5" / "万1 · 免五": a profile in a few words. */
export function profileSummary(profile: FeeProfile): string {
  const rate = `万${toWan(profile.a_share.commission_rate) || '0'}`
  const minimum = profile.a_share.waive_minimum ? '免五' : `最低 ¥${fmtNumber(profile.a_share.commission_min, { exact: true })}`
  const markets = [profile.hk ? '港股已设置' : '', profile.us ? '美股已设置' : ''].filter(Boolean)
  return [rate, minimum, ...markets].join(' · ')
}
