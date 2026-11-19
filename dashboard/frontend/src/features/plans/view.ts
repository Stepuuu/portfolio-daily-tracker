// What the plans pages say: status chips, the one-line summary of a plan, distance texts from the
// `market` block, trade relations and deviation tones. Pure functions over `/api/plans` payloads. Prices and
// distances go through lib/format.ts `fmtLong`: a plan's own prices have at most 8 places (the API's rule) and
// keep every digit, a linked trade's price from an import may have more and is ≈ rounded to 8; a builder that
// takes `pick` gives the full line too (`longLine`).
import {
  UNAVAILABLE, fmtDateTime, fmtLong, fmtMoney, fmtPct, fmtPrice, fmtQty, fmtTime, shownText, type Shown, type ShownPick,
} from '../../lib/format.ts'
import { compare } from '../market/decimal.ts'
import { quoteLine, type QuoteLine } from '../today/quoteStatus.ts'
import type { Quote } from '../today/types.ts'
import type { Deviation, LinkRelation, Plan, PlanMarket, PlanStatus } from './types.ts'

export type Tone = 'neutral' | 'accent' | 'warn' | 'up' | 'down'

/** What a plan field the user left empty reads: never blank, never 0. */
export const UNFILLED = '未填写'

const SHOWN_ZONE = 'Asia/Shanghai'

// -- one listing in any spelling -------------------------------------------------------------------

/**
 * What makes two tickers one listing, as the backend's `identity_key`: Hong Kong codes are numbers
 * (HKG:700 = HKG:00700) and a US class share is one symbol with a dot or a dash (NYSE:BRK.B = NYSE:BRK-B).
 */
export function listingKey(ticker: string): string {
  const text = ticker.trim().toUpperCase()
  // A Hong Kong code is a number: 700, 0700 and 00700 are one (0 is no code).
  const hk = /^HKG:0*(\d{1,5})$/.exec(text)
  if (hk && hk[1] !== '0') return `HKG:${hk[1].padStart(5, '0')}`
  const us = /^(NASDAQ|NYSE):(.+)$/.exec(text)
  if (us) return `${us[1]}:${us[2].replace(/-/g, '.')}`
  return text
}

export function sameListing(a: string, b: string): boolean {
  return listingKey(a) === listingKey(b)
}

/** The plans of one listing (any spelling), and of `account` when given (a plan for every account covers it); all without a ticker. */
export function plansOfListing<T extends Pick<Plan, 'ticker' | 'account'>>(items: T[], ticker: string | null | undefined, account?: string | null): T[] {
  if (!ticker) return items
  return items.filter((item) => sameListing(item.ticker, ticker) && (!account || item.account === null || item.account === account))
}

// -- status ---------------------------------------------------------------------------------------

const STATUS_TONE: Record<PlanStatus, Tone> = { draft: 'neutral', active: 'accent', closed: 'neutral', cancelled: 'neutral' }
const STATUS_LABEL: Record<PlanStatus, string> = { draft: '草稿', active: '启用中', closed: '已关闭', cancelled: '已取消' }

/** The chip of a status: the API's own `status_label` when it has one. */
export function statusChip(plan: Pick<Plan, 'status' | 'status_label'>): { label: string; tone: Tone } {
  return { label: plan.status_label || STATUS_LABEL[plan.status] || plan.status, tone: STATUS_TONE[plan.status] ?? 'neutral' }
}

export const isEnded = (status: PlanStatus) => status === 'closed' || status === 'cancelled'

export type PlanFilter = 'active' | 'draft' | 'ended' | 'all'

export const FILTERS: { value: PlanFilter; label: string }[] = [
  { value: 'active', label: '进行中' },
  { value: 'draft', label: '草稿' },
  { value: 'ended', label: '已结束' },
  { value: 'all', label: '全部' },
]

export function matchesFilter(plan: Pick<Plan, 'status'>, filter: PlanFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'ended') return isEnded(plan.status)
  return plan.status === filter
}

/** The filter with plans in it, for opening the page on something useful: active, else draft, else all. */
export function defaultFilter(plans: Pick<Plan, 'status'>[]): PlanFilter {
  if (plans.some((plan) => plan.status === 'active')) return 'active'
  if (plans.some((plan) => plan.status === 'draft')) return 'draft'
  return 'all'
}

// -- the numbers of a plan --------------------------------------------------------------------------

/** A plan figure (a price, a quantity, a weight) as its digits, through `fmtLong`. */
const figure = (value: string, pick: ShownPick) => pick(fmtLong(value, fmtQty))

/** "58–61", "≥58", "≤61", or null when no entry price is set. */
export function entryRange(plan: Pick<Plan, 'entry_low' | 'entry_high'>, pick: ShownPick = shownText): string | null {
  const { entry_low: low, entry_high: high } = plan
  if (low !== null && high !== null) return fmtQty(low) === fmtQty(high) ? figure(low, pick) : `${figure(low, pick)}–${figure(high, pick)}`
  if (low !== null) return `≥${figure(low, pick)}`
  if (high !== null) return `≤${figure(high, pick)}`
  return null
}

/** "15%" of the portfolio or "1,200 股"; null when no size is set. */
export function plannedSize(plan: Pick<Plan, 'planned_weight_pct' | 'planned_quantity'>, pick: ShownPick = shownText): string | null {
  if (plan.planned_quantity !== null) return `${figure(plan.planned_quantity, pick)} 股`
  if (plan.planned_weight_pct !== null) return `${figure(plan.planned_weight_pct, pick)}%`
  return null
}

/**
 * "入场 58–61 · 止损 54 · 目标 66 · 计划 1,200 股"; unset parts are left out, except the stop, which says
 * 止损未填写 (a plan without a stop is the one that most needs saying so).
 */
export function planSummary(
  plan: Pick<Plan, 'entry_low' | 'entry_high' | 'stop_price' | 'target_price' | 'planned_weight_pct' | 'planned_quantity'>, pick: ShownPick = shownText,
): string {
  const entry = entryRange(plan, pick)
  const size = plannedSize(plan, pick)
  const others = [
    entry && `入场 ${entry}`,
    plan.target_price !== null && `目标 ${figure(plan.target_price, pick)}`,
    size && `计划 ${size}`,
  ].filter((part): part is string => Boolean(part))
  if (plan.stop_price === null && !others.length) return '尚未填写价格和仓位'
  const stop = plan.stop_price === null ? `止损${UNFILLED}` : `止损 ${figure(plan.stop_price, pick)}`
  return [...others.slice(0, entry ? 1 : 0), stop, ...others.slice(entry ? 1 : 0)].join(' · ')
}

// -- distances from the market block ----------------------------------------------------------------

export interface DistanceText extends Shown {
  /** What the cell shows: "-11.8%", "已触及", "未设止损" or 不可用 (past 8 places rounded by `fmtLong`, `full` kept). */
  text: string
  /** Why it is unavailable (the API's reason), else null. */
  reason: string | null
  /** warn when the level is reached or within 5 %; neutral otherwise (red and green mean up and down only). */
  tone: 'neutral' | 'warn'
}

const NEAR_PCT = '5'

/** Within 5 % of the level (exact decimals; an unreadable value is not near). */
const near = (pct: string) => (compare(pct, NEAR_PCT) ?? 1) <= 0

function distance(
  market: PlanMarket | null | undefined,
  level: string | null,
  unset: string,
  pct: string | null | undefined,
  reached: boolean | null | undefined,
  reachedText: string,
  sign: -1 | 1,
): DistanceText {
  if (level === null) return { text: unset, reason: null, tone: 'neutral' }
  if (!market) return { text: UNAVAILABLE, reason: '行情尚未取得', tone: 'neutral' }
  if (reached) return { text: reachedText, reason: null, tone: 'warn' }
  if (pct === null || pct === undefined) {
    return { text: UNAVAILABLE, reason: market.price_reason ?? '行情不可用', tone: 'neutral' }
  }
  // The comparison only decides the tone (within 5 %); the text is the backend's own digits.
  const shown = sign === -1 ? `-${pct}` : `+${pct}`
  return { ...fmtLong(shown, (value) => fmtPct(value, { signed: true, exact: true })), reason: null, tone: near(pct) ? 'warn' : 'neutral' }
}

/** Distance to the stop as the move needed to reach it: price 61.20, stop 54 gives "-11.8%". */
export function stopDistance(plan: Pick<Plan, 'stop_price'>, market: PlanMarket | null | undefined): DistanceText {
  return distance(market, plan.stop_price, '未设止损', market?.distance_to_stop_pct, market?.stop_reached, '已触及止损', -1)
}

/** Distance to the target as the move needed to reach it: "+7.8%". */
export function targetDistance(plan: Pick<Plan, 'target_price'>, market: PlanMarket | null | undefined): DistanceText {
  return distance(market, plan.target_price, '未设目标', market?.distance_to_target_pct, market?.target_reached, '已到目标', 1)
}

/**
 * The text of the chip on a holding's row in Today: "计划 · 距止损 -11.76%". Reached and the distance are the
 * backend's (`stop_reached`, `distance_to_stop_pct`), never inferred from a rounded percentage; amber when
 * reached or within 5 %. The stop first, else the target, else just 计划.
 */
export function chipText(plan: Pick<Plan, 'stop_price' | 'target_price'>, market: PlanMarket | null | undefined): { text: string; warn: boolean; reason: string | null } {
  const missing = market ? market.price_reason ?? '行情不可用' : '行情尚未取得'
  if (plan.stop_price !== null) {
    if (market?.stop_reached) return { text: '计划 · 已触及止损', warn: true, reason: null }
    const pct = market?.distance_to_stop_pct ?? null
    if (pct === null) return { text: `计划 · 距止损 ${UNAVAILABLE}`, warn: false, reason: missing }
    return { text: `计划 · 距止损 ${fmtPct(`-${pct}`, { signed: true, exact: true })}`, warn: near(pct), reason: null }
  }
  if (plan.target_price !== null) {
    if (market?.target_reached) return { text: '计划 · 已到目标', warn: false, reason: null }
    const pct = market?.distance_to_target_pct ?? null
    if (pct === null) return { text: `计划 · 距目标 ${UNAVAILABLE}`, warn: false, reason: missing }
    return { text: `计划 · 距目标 ${fmtPct(`+${pct}`, { signed: true, exact: true })}`, warn: false, reason: null }
  }
  return { text: '计划', warn: false, reason: null }
}

// -- position against the plan -------------------------------------------------------------------------

/** "持有 1,000 股 / 计划 1,200 股", with what is unknown said as it is. */
export function quantityLine(market: PlanMarket | null | undefined, plan: Pick<Plan, 'planned_quantity'>): { text: string; reason: string | null } {
  if (!market) return { text: UNAVAILABLE, reason: '行情尚未取得' }
  const held = market.position_quantity === null ? null : fmtQty(market.position_quantity)
  const planned = plan.planned_quantity === null ? null : `${fmtQty(plan.planned_quantity)} 股`
  if (held === null) return { text: planned ? `计划 ${planned}` : UNAVAILABLE, reason: market.weight_reason }
  return { text: planned ? `持有 ${held} 股 / 计划 ${planned}` : `持有 ${held} 股`, reason: null }
}

/** "占组合 8.2% / 计划 15%"; `estimated` marks a figure built on unverified quotes. */
export function weightLine(market: PlanMarket | null | undefined, plan: Pick<Plan, 'planned_weight_pct'>): { text: string; estimated: boolean; reason: string | null } {
  if (!market) return { text: UNAVAILABLE, estimated: false, reason: '行情尚未取得' }
  const planned = plan.planned_weight_pct === null ? null : `计划 ${fmtQty(plan.planned_weight_pct)}%`
  if (market.weight_pct === null) {
    return { text: planned ? `${UNAVAILABLE} / ${planned}` : UNAVAILABLE, estimated: false, reason: market.weight_reason }
  }
  const now = `占组合 ${fmtPct(market.weight_pct, { exact: true })}`
  return { text: planned ? `${now} / ${planned}` : now, estimated: market.weight_estimated, reason: market.weight_estimated ? market.weight_reason : null }
}

// -- the market block of an active plan ---------------------------------------------------------------------

export interface MarketState {
  /** none: not active; pending: still loading; failed: the detail request failed before any answer; ready. */
  kind: 'none' | 'pending' | 'failed' | 'ready'
  reason: string | null
}

/**
 * Whether an open plan's (draft or active) market numbers can be shown; an ended plan has none. A failed
 * request is 不可用 with its reason, never a loading state that never ends; a failed refresh after an
 * answer keeps that answer.
 */
export function marketState(plan: Pick<Plan, 'status'>, detail: { market: PlanMarket | null } | undefined, error: string | undefined): MarketState {
  if (isEnded(plan.status)) return { kind: 'none', reason: null }
  if (detail) return { kind: 'ready', reason: null }
  if (error) return { kind: 'failed', reason: `行情读取失败：${error}` }
  return { kind: 'pending', reason: null }
}

// -- linked trades -----------------------------------------------------------------------------------------

const RELATION_LABEL: Record<LinkRelation, string> = { entry: '建仓', add: '加仓', reduce: '减仓', exit: '清仓', none: '无计划' }

export function relationLabel(relation: LinkRelation | string): string {
  return RELATION_LABEL[relation as LinkRelation] ?? relation
}

/** A deviation warns (amber) when the ledger says warning, otherwise it is only information. */
export function deviationTone(deviation: Pick<Deviation, 'level'>): 'warn' | 'neutral' {
  return deviation.level === 'warning' ? 'warn' : 'neutral'
}

/** "买入 100 股 @ 60.50" for a linked event (an imported price of 成交金额 ÷ 数量 is rounded to 8 places, marked). */
export function tradeText(link: { kind: string; quantity: string | null; price: string | null }, pick: ShownPick = shownText): string {
  const kind = link.kind === 'buy' ? '买入' : link.kind === 'sell' ? '卖出' : link.kind
  const quantity = link.quantity === null ? '' : ` ${figure(link.quantity, pick)} 股`
  const price = link.price === null ? '' : ` @ ${figure(link.price, pick)}`
  return `${kind}${quantity}${price}`
}

/** Alert kinds the backend creates for a plan, in Chinese. */
const ALERT_KIND: Record<string, string> = { stop_loss: '止损', target_price: '目标价', day_change_pct: '涨跌幅', position_weight_max: '仓位上限' }

/** "止损提醒：跌到 54 以下" style line for a rule; the threshold is a price or a percentage of the portfolio. */
export function alertRuleText(rule: { kind: string; threshold: string; enabled: boolean }, pick: ShownPick = shownText): string {
  const name = ALERT_KIND[rule.kind] ?? rule.kind
  const value = figure(rule.threshold, pick)
  const line = rule.kind === 'stop_loss' ? `价格跌到 ${value} 或以下`
    : rule.kind === 'target_price' ? `价格涨到 ${value} 或以上`
      : rule.kind === 'position_weight_max' ? `占组合超过 ${value}%`
        : `阈值 ${value}`
  return `${name}提醒：${line}${rule.enabled ? '' : '（已停用）'}`
}

/**
 * A price in the plan's currency, with the symbol unless it is CNY: every digit the backend has, at least
 * the market's two or three; null when the plan has none (the caller says 未填).
 */
export function planPrice(value: string | null, currency: string, pick: ShownPick = shownText): string | null {
  if (value === null) return null
  return pick(fmtLong(value, (v) => fmtPrice(v, currency, 'stock', { symbol: currency !== 'CNY', exact: true })))
}

export interface LossView {
  /** What the cell shows: "¥8,400.00"; the plan's own currency while the rate is missing; 未填写; 不可用. */
  text: string
  /** The CNY figure rests on a rate that is not verified: show 估算 (amber), as on Today. */
  estimated: boolean
  /** The rate it was converted at (source, time) and why it is an estimate, or why there is no figure. */
  note: string | null
  /** 未填写 and 不可用 are shown muted. */
  muted: boolean
}

/**
 * The loss at the stop. In CNY as the backend converted it, with the rate's source and time; 估算 when that
 * rate is not verified. A draft that lacks the entry, the stop or the planned quantity reads 未填写.
 */
export function maxLossView(plan: Pick<Plan, 'max_loss' | 'currency' | 'entry_low' | 'entry_high' | 'stop_price' | 'planned_quantity'>): LossView {
  const loss = plan.max_loss
  if (loss.amount === null) {
    const unfilled = (plan.entry_low === null && plan.entry_high === null) || plan.stop_price === null || plan.planned_quantity === null
    return { text: unfilled ? UNFILLED : UNAVAILABLE, estimated: false, note: loss.reason, muted: true }
  }
  if (loss.locked_gain !== null && loss.locked_gain !== undefined) {
    // A stop at or above the entry: nothing is lost at the stop (zero needs no rate), and the gain it locks in.
    const gain = fmtMoney(loss.locked_gain, loss.currency, { exact: true })
    return { text: fmtMoney(loss.amount_cny ?? loss.amount, 'CNY', { exact: true }), estimated: false,
      note: `止损价不低于入场价：触及止损时不亏损，按计划股数锁定盈利 ${gain}`, muted: false }
  }
  const native = fmtMoney(loss.amount, loss.currency, { exact: true })
  if (loss.amount_cny === null) return { text: native, estimated: false, note: loss.reason, muted: false }
  const text = fmtMoney(loss.amount_cny, 'CNY', { exact: true })
  if (loss.currency === 'CNY') return { text, estimated: false, note: null, muted: false }
  const estimated = loss.estimated === true
  const when = loss.fx_quote_time ? fmtDateTime(loss.fx_quote_time, { timeZone: SHOWN_ZONE }) : '时间未知'
  const rate = `按 ${loss.fx_source ?? '来源未知'} ${loss.currency} 汇率 ${loss.fx_rate ?? UNAVAILABLE}（${when}）折算 ${native}`
  return { text, estimated, note: estimated && loss.estimated_reason ? `${rate}；${loss.estimated_reason}` : rate, muted: false }
}

// -- the chip on a Today holding -------------------------------------------------------------------

export interface ChipView {
  text: string
  warn: boolean
  /** The plan the chip opens; null when the plans could not be read (the chip retries). */
  planId: number | null
  title: string
}

type ChipPlan = Pick<Plan, 'id' | 'ticker' | 'account' | 'status' | 'stop_price' | 'target_price'> & {
  activated_at?: string | null
  market?: PlanMarket | null
}

/**
 * The chip on a Today row of `ticker` in `account`: the active plan of that listing (any spelling) and account
 * (a plan for every account covers it), with the backend's distance and reached flags from the plan's
 * `market` (`GET /api/plans?market=1`). Amber when the stop is reached or near, or the quote is not verified;
 * the title names the quote's source and time. null while loading and for a row without a plan. When the
 * plans cannot be read the chip says 计划 不可用 instead of disappearing; a failed refresh keeps the answer.
 */
export function planChipView(items: ChipPlan[] | undefined, failed: string | null, ticker: string, account: string | null | undefined): ChipView | null {
  if (!items) return failed ? { text: '计划 不可用', warn: true, planId: null, title: `交易计划读取失败：${failed}。点击重试` } : null
  const covering = items.filter((item) => item.status === 'active' && sameListing(item.ticker, ticker)
    && (item.account === null || item.account === (account ?? null)))
  if (!covering.length) return null
  // Only one should be active; with duplicates an older store may hold, the latest is the plan trades follow.
  const plan = covering.reduce((latest, item) => ((item.activated_at ?? '') > (latest.activated_at ?? '')
    || ((item.activated_at ?? '') === (latest.activated_at ?? '') && item.id > latest.id) ? item : latest))
  const market = plan.market ?? null
  const chip = chipText(plan, market)
  const line = market ? marketQuoteLine(market, null) : null
  const title = ['查看交易计划', line?.meta && `行情 ${line.meta}`, line?.chip && chipWords(line.chip), line?.estimate && chipWords(line.estimate), chip.reason]
    .filter(Boolean).join('；')
  return { text: chip.text, warn: chip.warn || line?.chip?.tone === 'warn' || Boolean(line?.estimate), planId: plan.id, title }
}

// -- a price on the plans page -----------------------------------------------------------------------

/**
 * The quote line of a plan's price, as Today's row has it: the backend's `price_estimated` (a stale quote, a New
 * York close before it settles) turns the status chip amber, or adds an amber 估算, exactly as on Today.
 */
export function marketQuoteLine(market: Pick<PlanMarket, 'quote' | 'price_estimated' | 'price_estimated_reason'>, today: string | null): QuoteLine | null {
  if (!market.quote) return null
  return quoteLine(market.quote as unknown as Quote, today, market.price_estimated === true, market.price_estimated_reason ?? null)
}

const chipWords = (chip: { label: string; title?: string }) => (chip.title ? `${chip.label}：${chip.title}` : chip.label)

export interface PriceView {
  /** The backend's price with every digit ("3.456"), or 不可用. */
  text: string
  /** warn: the quote is not verified, or the latest refresh failed and the price may be old. */
  tone: 'neutral' | 'warn'
  /** "已过期 09:40", "可能不是最新", or null for a fresh price. */
  label: string | null
  /** Source and time of the quote, and why it is marked; the reason when there is no price. */
  title: string
}

/**
 * An open plan's current price as the backend has it, with its source and time; amber with 已过期 hh:mm (or the
 * quote's own status) when the quote is not verified, and 可能不是最新 when the detail's latest refresh failed
 * (`outdated`: when the shown data was read, and why the refresh failed), so an old price never looks current.
 */
export function priceView(market: PlanMarket | null | undefined, currency: string, outdated: { at: number; error: string } | null, today: string | null): PriceView {
  if (!market) return { text: UNAVAILABLE, tone: 'neutral', label: null, title: '行情尚未取得' }
  if (market.price === null) return { text: UNAVAILABLE, tone: 'neutral', label: null, title: market.price_reason ?? '行情不可用' }
  const text = planPrice(market.price, currency) ?? UNAVAILABLE
  const line = marketQuoteLine(market, today)
  const parts = [line?.meta && `行情 ${line.meta}`, line?.chip && chipWords(line.chip), line?.estimate && chipWords(line.estimate)]
  let label = line?.chip && line.chip.tone === 'warn' ? line.chip.label : line?.estimate ? line.estimate.label : null
  let tone: PriceView['tone'] = label ? 'warn' : 'neutral'
  if (outdated) {
    label = '可能不是最新'
    tone = 'warn'
    parts.push(`刷新失败：${outdated.error}；显示的是 ${fmtTime(outdated.at, { timeZone: SHOWN_ZONE, seconds: false })} 读到的数据`)
  }
  return { text, tone, label, title: parts.filter(Boolean).join('；') }
}

// -- what changed under an edit ----------------------------------------------------------------------

type ChangePlan = Pick<Plan, 'status' | 'status_label' | 'ticker' | 'account' | 'thesis' | 'invalidation' | 'entry_low' | 'entry_high'
  | 'stop_price' | 'target_price' | 'planned_weight_pct' | 'planned_quantity' | 'review_on'>

/** What differs between the plan an edit started from and the plan as it is now, in words ("止损 54 → 55"). */
export function changedFields(before: ChangePlan, after: ChangePlan): string[] {
  const shown = (value: string | null) => value ?? UNFILLED
  const price = (value: string | null) => (value === null ? UNFILLED : fmtQty(value))
  const pairs: [string, string, string][] = [
    ['状态', statusChip(before).label, statusChip(after).label],
    ['标的', before.ticker, after.ticker],
    ['账户', before.account ?? '全部账户', after.account ?? '全部账户'],
    ['入场区间', shown(entryRange(before)), shown(entryRange(after))],
    ['止损', price(before.stop_price), price(after.stop_price)],
    ['目标', price(before.target_price), price(after.target_price)],
    ['计划仓位', shown(plannedSize(before)), shown(plannedSize(after))],
    ['复查日', shown(before.review_on), shown(after.review_on)],
  ]
  const texts: [string, string, string][] = [['买入理由', before.thesis, after.thesis], ['判断失效条件', before.invalidation, after.invalidation]]
  const differ = ([, a, b]: [string, string, string]) => a !== b
  const words = ([label, a, b]: [string, string, string]) => `${label} ${a} → ${b}`
  // Status, ticker and account first; then the texts (named, not quoted: they are long); then the numbers.
  return [...pairs.slice(0, 3).filter(differ).map(words), ...texts.filter(differ).map(([label]) => label),
    ...pairs.slice(3).filter(differ).map(words)]
}

// -- due for review ------------------------------------------------------------------------------------

/** Active plans whose review date has come (on or before `today`), oldest first: Today's 今日事项 lists them. */
export function duePlans<T extends Pick<Plan, 'status' | 'review_on'>>(items: T[] | undefined, today: string | null): T[] {
  if (!items || !today) return []
  return items.filter((item) => item.status === 'active' && item.review_on !== null && item.review_on <= today)
    .sort((a, b) => (a.review_on ?? '').localeCompare(b.review_on ?? ''))
}

// -- names and buying ----------------------------------------------------------------------------------

/** The instrument's name: the ledger's for a holding (`names` by `listingKey`), else the market block's. */
export function planName(plan: Pick<Plan, 'ticker'>, names: Map<string, string>, market: Pick<PlanMarket, 'name'> | null | undefined): string | undefined {
  return names.get(listingKey(plan.ticker)) ?? market?.name ?? undefined
}

/** The entry dialog's draft for buying under a plan: the plan's id goes along, so the preview names the plan. */
export function buyDraft(plan: Pick<Plan, 'id' | 'ticker' | 'account' | 'currency'>, name: string | undefined) {
  return { kind: 'buy', ticker: plan.ticker, name, account: plan.account ?? undefined, currency: plan.currency, planId: plan.id, source: 'plan' as const }
}
