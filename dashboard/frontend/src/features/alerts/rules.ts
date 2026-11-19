// The 提醒 page's rules: the rule payload (backend/api/alerts.py, core/alerts/view.py), how a rule's
// condition (the stored threshold, digit for digit), state, distance and source read, which rules a trade
// plan owns (read-only here), what the new-alert dialog says while a threshold is typed (the price it
// rests on, the weight the monitor would use or why it would not), and which form field an API error
// belongs to. Pure TypeScript (relative `.ts` imports only) so node tests can load it.
import { fmtDate, fmtNumber, fmtPct, fmtTime } from '../../lib/format.ts'
import { identityOf, inferInstrument, qualifiedInput, sameListing, sameTicker, type Identified } from '../../lib/instrument.ts'
import { quoteLine } from '../today/quoteStatus.ts'
import { DISPLAY_ZONE } from '../today/session.ts'
import type { QuoteObject } from '../today/types.ts'
import { currencyOf, fmtTickerPrice, tickerPriceDigits } from '../market/format.ts'
import { applyPercent, compare, isDecimal, percentChange, sign, sum } from '../market/decimal.ts'

export type AlertKind = 'stop_loss' | 'target_price' | 'day_change_pct' | 'position_weight_max'

export interface RuleMarket {
  price: string | null
  prev_close: string | null
  change_pct: string | null
  weight_pct: string | null
  /** What the rule compares: the price, today's change % or the weight %. */
  value: string | null
  /** Value to threshold in the rule's unit; positive while the condition does not hold. */
  distance: string | null
  /** How far the price must move to fire, in % of the current price; null for weight rules. */
  distance_pct: string | null
  unit: 'price' | 'pct_points' | null
  /**
   * The engine's own comparison of the value with the threshold (core/alerts/view.py): true when the
   * condition holds; null with the distance. 已达到 comes from this, never from the rounded distance.
   */
  condition_met?: boolean | null
  checked_now: boolean
  skip_reason: string | null
  /** Machine code of the skip (session_closed, quote_stale, weight_estimated, stale_observation …). */
  skip_code?: string | null
  /** When the monitor began skipping the rule for this reason. */
  skipped_since?: string | null
  quote: { source: string | null; quote_time: string | null; fetched_at: string | null; status: string; reason: string | null } | null
}

export interface AlertRule {
  id: number
  ticker: string
  /** The listing (core/market/search.py `identity_key`); absent from an older backend. */
  identity?: string | null
  account: string | null
  kind: AlertKind | string
  kind_label: string
  threshold: string
  condition: string
  enabled: boolean
  /** Why the system disabled it (a duplicate of another rule); null when a person (or a plan) did. */
  disabled_reason?: string | null
  cooldown_seconds: number
  note: string | null
  /** manual, agent:chat, plan:<id> … */
  source: string
  created_at: string
  updated_at: string
  state: {
    armed: boolean
    last_fired_at: string | null
    last_trading_date: string | null
    fire_count_today: number
    fires_left_today?: number
  }
  market?: RuleMarket | null
}

export const KIND_OPTIONS: { value: AlertKind; label: string }[] = [
  { value: 'stop_loss', label: '止损' },
  { value: 'target_price', label: '目标价' },
  { value: 'day_change_pct', label: '涨跌幅' },
  { value: 'position_weight_max', label: '仓位上限' },
]

const KIND_LABEL: Record<string, string> = Object.fromEntries(KIND_OPTIONS.map((option) => [option.value, option.label]))

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind
}

export const PRICE_KINDS: string[] = ['stop_loss', 'target_price']
export const PERCENT_KINDS: string[] = ['day_change_pct', 'position_weight_max']

/** Price digits for a ticker: A-share ETFs and Hong Kong 3, otherwise 2; indexes 2. */
export const thresholdDigits = tickerPriceDigits
export { currencyOf }

/** A quote's price with every digit the backend sent (a $0.8437 listing is not "0.84"), the market's at least. */
const priceText = (value: string | null, ticker: string) => fmtTickerPrice(value, ticker, currencyOf(ticker), { exact: true })
/** A stored threshold as it is: the market's digits at least, never fewer than stored ("11.004"). */
const thresholdText = (value: string, ticker: string) => fmtTickerPrice(value, ticker, currencyOf(ticker), { exact: true })
const percentText = (value: string) => fmtPct(value, { digits: 0, exact: true })

/** The text of a threshold the page cannot read (an exponent, garbage): the rule cannot be checked. */
export const INVALID_THRESHOLD = '阈值无效'

/**
 * "≤ 54.00", "≥ 11.004", "跌幅 ≥ 4%", "涨幅 ≥ 0.001%", "仓位 ≥ 50%": the condition with the stored
 * threshold's every digit (rounding would show a level that does not fire it). An unreadable threshold
 * is `阈值无效`.
 */
export function conditionText(rule: Pick<AlertRule, 'kind' | 'threshold' | 'ticker'>): string {
  if (!isDecimal(rule.threshold)) return INVALID_THRESHOLD
  switch (rule.kind) {
    case 'stop_loss':
      return `≤ ${thresholdText(rule.threshold, rule.ticker)}`
    case 'target_price':
      return `≥ ${thresholdText(rule.threshold, rule.ticker)}`
    case 'day_change_pct': {
      const negative = sign(rule.threshold) === -1
      const magnitude = rule.threshold.trim().replace(/^[+-]/, '')
      return `${negative ? '跌幅' : '涨幅'} ≥ ${percentText(magnitude)}`
    }
    case 'position_weight_max':
      return `仓位 ≥ ${percentText(rule.threshold)}`
    default:
      return rule.threshold
  }
}

export interface SourceInfo {
  /** 手动, 计划 #12, 助手 … */
  label: string
  /** The plan that owns the rule (source plan:<id>). */
  planId: string | null
  /** Rules a plan owns are changed in the plan, not here. */
  readOnly: boolean
}

export function sourceInfo(source: string | null | undefined): SourceInfo {
  const text = source ?? ''
  const plan = /^plan:(.+)$/.exec(text)
  if (plan) return { label: `计划 #${plan[1]}`, planId: plan[1], readOnly: true }
  if (text === 'manual' || !text) return { label: '手动', planId: null, readOnly: false }
  if (text.startsWith('agent')) return { label: '助手', planId: null, readOnly: false }
  return { label: text, planId: null, readOnly: false }
}

/**
 * The enabled rules that watch a holding: its listing (the rules' `identity`, any spelling) and, when the
 * holding's account is given, rules for that account or for every account (as the plan chip matches).
 */
export function holdingRules<R extends Identified & { account: string | null; enabled: boolean }>(
  rules: R[] | undefined, ticker: string, account?: string | null,
): R[] {
  return (rules ?? []).filter((rule) => rule.enabled && sameListing(rule, { ticker })
    && (!account || rule.account === null || rule.account === account))
}

export type DistanceTone = 'plain' | 'muted' | 'warn'

/** Skip codes that are no fault: the market is closed, or quotes were not asked for. */
const QUIET_SKIPS = ['session_closed', 'quotes_not_requested']

export interface RuleState {
  /** 启用, 已停用, 暂停检查, 休市，不检查, 无法检查. */
  label: string
  tone: DistanceTone
  /** The backend's reason and since when ("行情已过期：… · 自 10:05 起"); null when there is nothing to add. */
  detail: string | null
}

/** " · 自 10:05 起", with the date when it is not `today` (the page's Beijing date, as the backend's clock has it). */
function sinceText(iso: string | null | undefined, today: string | null): string {
  if (!iso) return ''
  const day = fmtDate(iso, { timeZone: DISPLAY_ZONE })
  const time = fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds: false })
  return ` · 自 ${day === today ? '' : `${day.slice(5)} `}${time} 起`
}

/**
 * The 状态 of a rule, from what the backend sends: disabled (with the system's reason, e.g. a reconciled
 * duplicate), unreadable or errored in the last cycle (无法检查), skipped by the monitor (amber
 * 暂停检查 with the backend's reason and since when; a code this page does not know is treated the same,
 * never shown raw), or simply not checked while its market is closed.
 */
export function ruleState(rule: AlertRule, errored: boolean, today: string | null = null): RuleState {
  if (!rule.enabled) return { label: '已停用', tone: 'muted', detail: rule.disabled_reason ?? null }
  if (!isDecimal(rule.threshold)) return { label: '无法检查', tone: 'warn', detail: '阈值无法读取，这条规则不会被检查；请修改阈值或删除。' }
  if (errored) return { label: '无法检查', tone: 'warn', detail: '上一次检查无法评估这条规则，修正阈值或稍后再看' }
  const market = rule.market
  const code = market?.skip_code ?? null
  if (!market || market.checked_now || (!code && !market.skip_reason) || QUIET_SKIPS.includes(code ?? '')) {
    if (code === 'session_closed') return { label: '休市，不检查', tone: 'muted', detail: market?.skip_reason ?? null }
    return { label: '启用', tone: 'plain', detail: null }
  }
  return {
    label: '暂停检查',
    tone: 'warn',
    detail: `${market.skip_reason || '监控暂不检查这条规则（后端没有说明原因）'}${sinceText(market.skipped_since, today)}`,
  }
}

export interface DistanceView {
  /** "61.20", "+0.66%", "57.90%": what the rule watches now; null when unknown. */
  now: string | null
  /** "差 7.1%", "差 1.20 个百分点", "已达到", "已达到 · 未检查"; null when the distance is unknown. */
  gap: string | null
  tone: DistanceTone
  /** Why it is unknown or not checked (backend's words). */
  title: string | undefined
}

/** `value` to `digits`, or with every digit the backend sent when that would read as 0 though it is not. */
function gapFigure(value: string, digits: number, percent: boolean): string {
  const shown = percent ? fmtPct(value, { digits }) : fmtNumber(value, { digits })
  if (/[1-9]/.test(shown) || sign(value) === 0) return shown
  return percent ? fmtPct(value, { digits, exact: true }) : fmtNumber(value, { digits, exact: true })
}

/** 现价 / 距离 of a rule row, from the backend's `market` block. */
export function distanceView(rule: AlertRule): DistanceView {
  const market = rule.market
  if (!market) return { now: null, gap: null, tone: 'muted', title: '未请求行情' }
  const title = market.skip_reason ?? market.quote?.reason ?? undefined
  let now: string | null = null
  if (rule.kind === 'day_change_pct') now = market.change_pct === null ? null : fmtPct(market.change_pct, { signed: true, exact: true })
  else if (rule.kind === 'position_weight_max') now = market.weight_pct === null ? null : fmtPct(market.weight_pct)
  else now = market.price === null ? null : priceText(market.price, rule.ticker)
  const skipped = !market.checked_now && Boolean(market.skip_code || market.skip_reason) && !QUIET_SKIPS.includes(market.skip_code ?? '')
  if (market.distance === null) return { now, gap: null, tone: skipped ? 'warn' : now === null ? 'muted' : 'plain', title }
  // The condition holds at this price (the engine's comparison), but a rule the monitor skips will not fire on it.
  if (market.condition_met === true) return { now, gap: skipped ? '已达到 · 未检查' : '已达到', tone: 'warn', title }
  const gap = market.unit === 'price' && market.distance_pct !== null
    ? `差 ${gapFigure(market.distance_pct, 1, true)}`
    : `差 ${gapFigure(market.distance, 2, false)} 个百分点`
  return { now, gap, tone: skipped ? 'warn' : 'plain', title }
}

// ---------------------------------------------------------------------------------------------
// The new-alert dialog

/**
 * The weight the monitor would compare, from `/api/today` (core/alerts/engine.py `weight_check`): the
 * listing's weight in one account or summed over all; `unavailable` when the total, the account or a
 * position's value is missing; `estimated` when the position or the portfolio total rests on a price or
 * rate that is not verified (the monitor then skips the rule); `not_held` weighs 0 only with a verified
 * total.
 */
export type WeightContext =
  | { state: 'loading' }
  | { state: 'unavailable'; reason: string | null }
  | { state: 'not_held'; estimated: boolean; reason: string | null }
  | { state: 'value'; pct: string; estimated: boolean; reason: string | null; exact?: string }

interface TodayLike {
  accounts: { name: string; positions: (Identified & { ticker: string; weight_pct: string | null; weight_pct_exact?: string | null; estimated: boolean; estimated_reason: string | null; quote: { reason: string | null } })[] }[]
  totals: { total_cny: string | null; quality: string | null; estimated?: { ticker?: string; currency?: string; reason: string }[]; unavailable?: { reason: string; field?: string }[] }
}

function totalsReason(today: TodayLike): string | null {
  const entry = today.totals.estimated?.[0]
  if (!entry) return null
  const subject = entry.ticker ?? (entry.currency ? `汇率 ${entry.currency}` : '部分持仓')
  return `${subject}：${entry.reason}`
}

export function weightContext(today: TodayLike | undefined, ticker: string, account: string | null, failed: boolean): WeightContext {
  if (!today) return failed ? { state: 'unavailable', reason: '今日持仓暂时无法读取' } : { state: 'loading' }
  if (today.totals.total_cny === null || (sign(today.totals.total_cny) ?? 0) <= 0) {
    const reason = today.totals.unavailable?.find((entry) => entry.field !== 'day_pnl')?.reason ?? null
    return { state: 'unavailable', reason: `组合总值不可用${reason ? `（${reason}）` : ''}` }
  }
  const accounts = today.accounts.filter((item) => account === null || item.name === account)
  if (account !== null && accounts.length === 0) return { state: 'unavailable', reason: '账本中没有这个账户' }
  const listing = { ticker }
  const rows = accounts.flatMap((item) => item.positions.filter((row) => identityOf(row) === identityOf(listing)))
  const totalEstimated = today.totals.quality !== 'complete'
  const missing = rows.find((row) => row.weight_pct === null)
  if (missing) return { state: 'unavailable', reason: missing.quote.reason ?? '该持仓的市值不可用' }
  const ownEstimate = rows.find((row) => row.estimated)
  const estimated = Boolean(ownEstimate) || totalEstimated
  const reason = ownEstimate ? `${ownEstimate.ticker}：${ownEstimate.estimated_reason ?? '估值未经核实'}` : totalEstimated ? totalsReason(today) : null
  if (rows.length === 0) return { state: 'not_held', estimated: totalEstimated, reason: totalEstimated ? totalsReason(today) : null }
  // One position: the backend's figure as it is; several accounts: their exact sum.
  const pct = rows.length === 1 ? rows[0].weight_pct! : sum(rows.map((row) => row.weight_pct))!
  // What the monitor compares with a cap (core/alerts/engine.py `weight_check`): the exact weights, summed.
  const exact = sum(rows.map((row) => row.weight_pct_exact))
  if (exact === null) return { state: 'value', pct, estimated, reason }
  return { state: 'value', pct, estimated, reason, exact }
}

export interface PreviewContext {
  ticker: string
  price: string | null
  /** The quote's previous close: with the price, today's change is compared exactly as the monitor does. */
  prevClose?: string | null
  changePct: string | null
  /** The weight the monitor would use (position caps). */
  weight?: WeightContext
  /** Why the price is missing (or not verified), when it is. */
  priceReason?: string | null
  /** The price is verified (live, delayed or a confirmed close); default true. */
  priceVerified?: boolean
}

export interface Preview {
  /** "现价 61.20 · 距止损 -11.8%". */
  text: string
  /** The condition already holds on verified figures: saving fires at the next check. */
  warn: string | null
  /** Amber words about figures that are not verified: nothing is promised then. */
  note?: string | null
}

/** The condition holds on a price the monitor does not use (stale, previous close, time unknown …). */
function unverifiedNote(reason: string | null | undefined): string {
  return `按这个价格已达到条件，但价格未经核实${reason ? `（${reason}）` : ''}：行情恢复正常之前，监控不会据此提醒。`
}

const ESTIMATED_WEIGHT = '仓位是估算值：组合估值核实之前，监控不会按仓位检查这条规则，保存后也不会立即提醒。'

/**
 * Whether today's change reaches `threshold` as the monitor compares it (core/alerts/engine.py
 * `condition_met`): the price against previous close × (1 + threshold/100), exactly, since the change % the
 * quote shows is rounded (+4.9975 % reads +5.00 %). Without both prices, the shown change decides.
 */
function dayChangeReached(context: PreviewContext, threshold: string): boolean {
  const down = sign(threshold) === -1
  if (context.price !== null && context.prevClose && (sign(context.prevClose) ?? 0) > 0) {
    const trigger = applyPercent(context.prevClose, threshold, 24)
    const side = compare(context.price, trigger)
    if (side !== null) return down ? side <= 0 : side >= 0
  }
  return down ? compare(context.changePct, threshold)! <= 0 : compare(context.changePct, threshold)! >= 0
}

/**
 * Whether a verified weight meets `threshold` as the monitor compares it: on the exact weight (Today's
 * `weight_pct_exact`, summed over accounts), not on the 0.01 one shown, so 49.996 % (shown 50.00 %) stays
 * under a 50 % cap and exactly 50 % meets it. Without an exact figure the shown one decides.
 */
function weightReached(weight: { pct: string; exact?: string }, threshold: string): boolean {
  return (compare(weight.exact ?? weight.pct, threshold) ?? -1) >= 0
}

/** The live line under the threshold field. */
export function previewLine(kind: AlertKind, threshold: string, context: PreviewContext): Preview {
  const valid = isDecimal(threshold)
  if (kind === 'stop_loss' || kind === 'target_price') {
    if (context.price === null) return { text: `现价不可用${context.priceReason ? `（${context.priceReason}）` : ''}`, warn: null }
    const now = `现价 ${priceText(context.price, context.ticker)}`
    if (!valid || (sign(threshold) ?? 0) <= 0) return { text: now, warn: null }
    const gap = percentChange(context.price, threshold, 1)
    const label = kind === 'stop_loss' ? '距止损' : '距目标'
    const reached = kind === 'stop_loss' ? compare(context.price, threshold)! <= 0 : compare(context.price, threshold)! >= 0
    const text = `${now} · ${label} ${fmtPct(gap, { signed: true, digits: 1 })}`
    if (reached && context.priceVerified === false) return { text, warn: null, note: unverifiedNote(context.priceReason) }
    return {
      text,
      warn: reached ? (kind === 'stop_loss' ? '现价已在止损价或以下，保存后下一次检查就会提醒。' : '现价已达到目标价，保存后下一次检查就会提醒。') : null,
    }
  }
  if (kind === 'day_change_pct') {
    if (context.changePct === null) return { text: '今日涨跌不可用', warn: null }
    const text = `今日涨跌 ${fmtPct(context.changePct, { signed: true, exact: true })}`
    if (!valid || sign(threshold) === 0) return { text, warn: null }
    const reached = dayChangeReached(context, threshold)
    if (reached && context.priceVerified === false) return { text, warn: null, note: unverifiedNote(context.priceReason) }
    return { text, warn: reached ? '今日涨跌已达到这个幅度，保存后下一次检查就会提醒。' : null }
  }
  const weight = context.weight ?? { state: 'loading' as const }
  switch (weight.state) {
    case 'loading':
      return { text: '当前仓位读取中…', warn: null }
    case 'unavailable':
      return { text: `当前仓位不可用${weight.reason ? `（${weight.reason}）` : ''}`, warn: null }
    case 'not_held':
      return weight.estimated
        ? { text: '没有这只的持仓；组合估值未经核实，仓位按估算', warn: null, note: ESTIMATED_WEIGHT }
        : { text: '没有这只的持仓，当前仓位 0%', warn: null }
    default: {
      if (weight.estimated) {
        return { text: `当前仓位 ${fmtPct(weight.pct, { exact: true })}（估算${weight.reason ? `：${weight.reason}` : ''}）`, warn: null, note: ESTIMATED_WEIGHT }
      }
      const text = `当前仓位 ${fmtPct(weight.pct, { exact: true })}`
      if (!valid) return { text, warn: null }
      return { text, warn: weightReached(weight, threshold) ? '当前仓位已达到上限，保存后下一次检查就会提醒。' : null }
    }
  }
}

/**
 * The price the one-click stop and target levels are computed from, in words: "按现价 100.00（东财 09:40:00）";
 * amber with the backend's reason when that price is not verified (stale, previous close, time unknown,
 * an unconfirmed close). null when there is no price (no levels are offered then).
 */
export function priceBasis(quote: QuoteObject | null | undefined, ticker: string, today: string | null): { text: string; warn: boolean } | null {
  if (!quote || quote.price === null || quote.quote.status === 'unavailable') return null
  const line = quoteLine(quote.quote, today, quote.estimated, quote.estimated_reason)
  const chip = line.chip ?? line.estimate
  const where = [line.meta, chip && chip.tone === 'warn' ? null : chip?.label].filter(Boolean).join(' ')
  const price = priceText(quote.price, ticker)
  if (chip?.tone === 'warn') {
    return { text: `常用阈值按${chip.label}的价格 ${price}${where ? `（${where}）` : ''}计算，价格未经核实：${chip.title ?? quote.estimated_reason ?? '请先核对'}`, warn: true }
  }
  return { text: `常用阈值按现价 ${price}${where ? `（${where}）` : ''}计算`, warn: false }
}

/**
 * Why the ticker field cannot be saved, or null: it must hold a listing the user picked (or the opener
 * gave), and a typed `EXCH:CODE` the ledger cannot hold says why (the picker's reason).
 */
export function tickerError(text: string, picked: { ticker: string | null; supported: boolean } | null): string | null {
  const value = text.trim()
  if (!value) return '请选择标的。'
  if (picked && picked.supported && picked.ticker && sameTicker(picked.ticker, value)) return null
  const qualified = qualifiedInput(value)
  const inferred = qualified ? inferInstrument(qualified) : null
  if (inferred && !inferred.supported && inferred.reason) return inferred.reason
  return '请从列表中选择标的（可输入代码或名称搜索）。'
}

/**
 * The account after the instrument changes: kept for the same listing, otherwise cleared (no account),
 * so a rule never inherits an account the opener chose for another instrument.
 */
export function accountAfterTickerChange(account: string, from: string, to: string): string {
  return sameTicker(from, to) ? account : ''
}

export interface QuickPick {
  label: string
  value: string
}

/**
 * One-click thresholds: −5 / −8 / −10 % below the price for a stop, +5 / +10 / +20 % above it for a
 * target, common day moves and weight caps.
 */
export function quickPicks(kind: AlertKind, price: string | null, ticker: string): QuickPick[] {
  if (kind === 'stop_loss' || kind === 'target_price') {
    if (price === null || (sign(price) ?? 0) <= 0) return []
    const digits = thresholdDigits(ticker, currencyOf(ticker))
    const steps = kind === 'stop_loss' ? ['-5', '-8', '-10'] : ['5', '10', '20']
    return steps.flatMap((step) => {
      const value = applyPercent(price, step, digits)
      return value ? [{ label: `${step.startsWith('-') ? step : `+${step}`}%`, value }] : []
    })
  }
  if (kind === 'day_change_pct') return [{ label: '跌 3%', value: '-3' }, { label: '跌 5%', value: '-5' }, { label: '涨 5%', value: '5' }]
  return [{ label: '20%', value: '20' }, { label: '30%', value: '30' }, { label: '50%', value: '50' }]
}

export type AlertField = 'ticker' | 'kind' | 'threshold' | 'account' | 'note'

/** The form field an API validation message is about; null for a message about the whole rule. */
export function fieldForError(detail: string): AlertField | null {
  if (/指数不是持仓|提醒类型/.test(detail)) return 'kind'
  if (/阈值|止损价|目标价|涨跌幅|仓位上限|相同的提醒规则/.test(detail)) return 'threshold'
  if (/代码/.test(detail)) return 'ticker'
  if (/账户/.test(detail)) return 'account'
  if (/备注/.test(detail)) return 'note'
  return null
}

/** The threshold to send: day moves carry their direction as a sign. */
export function thresholdValue(kind: AlertKind, magnitude: string, direction: 'down' | 'up'): string {
  const text = magnitude.trim()
  if (kind !== 'day_change_pct' || !text) return text
  const bare = text.replace(/^[+-]/, '')
  return direction === 'down' ? `-${bare}` : bare
}

/** Splits a stored day-move threshold into its direction and magnitude, for editing. */
export function splitThreshold(kind: string, threshold: string): { magnitude: string; direction: 'down' | 'up' } {
  if (kind !== 'day_change_pct') return { magnitude: threshold, direction: 'down' }
  return threshold.startsWith('-') ? { magnitude: threshold.slice(1), direction: 'down' } : { magnitude: threshold.replace(/^\+/, ''), direction: 'up' }
}
