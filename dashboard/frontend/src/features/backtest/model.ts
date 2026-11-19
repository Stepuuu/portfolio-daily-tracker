// 快速回测: the form, its request, its errors, and the links other pages use to open it. Pure TypeScript
// (no `@/` imports, no JSX) so node tests can load it.
//
// URL contract (WP6). Other pages link to the backtest page with
//
//   /backtest?ticker=EXCH:CODE&preset=<id>&entry=<YYYY-MM-DD>&from=holding|plan|watch
//
// and these optional keys:
//   capital=<amount>        本金 in the instrument's currency (a holding passes its cost)
//   period=1y|3y|5y         the period preset (default 3y)
//   <param key>=<value>     a parameter of the chosen preset by its key in GET /presets, e.g.
//                           stop_pct=8&target_pct=20 (unknown keys are ignored)
//   entry_price=, stop_price=, target_price=
//                           a trade plan's absolute prices: when the preset has stop_pct / target_pct and
//                           they are not given, they are converted to % of entry_price on the page, and
//                           the conversion is shown next to the fields
//   plan=<id>               the trade plan the link came from (shown only)
//   strategy=<id>           a strategy id from GET /strategies instead of a preset
//   run=<id>                open that run (in progress, finished or saved); the other keys are ignored
//   tab=history|data|strategies
// `entry` fills the preset's date parameter (unit "date", e.g. stop_target's entry_date). A link
// prefills the form and never starts a run by itself. Build links with `backtestHref`, `holdingLink`,
// `planLink` or `watchLink`.
//
// Requests (POST /api/backtest/runs, RunSpec in dashboard/backtesting/service.py): fields the person did
// not touch are left out, so the service applies its own defaults (period 3y, capital 100,000 in the
// market's currency, the market's commission and lot rule, slippage 0.05%, 前复权, the market's index).
// The three commission fields are the broker commission only: RunSpec has no fee_basis field (it rejects
// unknown fields) because on this route they never include stamp duty; taxes and levies are charged by
// the dated fee schedule. Percentages are typed in percent units ("0.025" for 0.025%) and sent as
// fractions (0.00025), converted on the decimal string, not through float arithmetic.
import { fmtPct } from '../../lib/format.ts'
import { decAdd, decCmp, decMul } from '../ledger/decimal.ts'
import { percentChange } from '../market/decimal.ts'
import type { ApiRefusal, Defaults, Preset, PresetParam, RunBody, StrategyInfo } from './types.ts'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

export type PeriodPreset = '1y' | '3y' | '5y' | 'custom'
export type AdjustChoice = 'qfq' | 'hfq' | 'none'
export type BenchmarkChoice = 'auto' | 'none' | 'custom'
export type TabId = 'quick' | 'history' | 'data' | 'strategies'
export type LinkSource = 'holding' | 'plan' | 'watch'

export const TABS: { id: TabId; label: string }[] = [
  { id: 'quick', label: '快速回测' },
  { id: 'history', label: '历史' },
  { id: 'data', label: '数据' },
  { id: 'strategies', label: '策略文件' },
]

export const PERIODS: { value: PeriodPreset; label: string }[] = [
  { value: '1y', label: '近1年' },
  { value: '3y', label: '近3年' },
  { value: '5y', label: '近5年' },
  { value: 'custom', label: '自定义' },
]

/** The method of a run: a preset from GET /presets or a strategy from GET /strategies. */
export type Method = { kind: 'preset'; id: string } | { kind: 'strategy'; id: string }

export interface FormState {
  ticker: string
  method: Method | null
  /** Parameters as typed, by key; '' (or missing) means the default. Booleans of a strategy as 'true'/'false'. */
  params: Record<string, string>
  period: PeriodPreset
  start: string
  end: string
  capital: string
  /** Percent units: "0.025" is 0.025%. */
  commissionBuy: string
  commissionSell: string
  minCommission: string
  /** Percent units: "0.05" is 0.05%. */
  slippage: string
  lotSize: string
  adjust: AdjustChoice
  allowUnadjusted: boolean
  benchmark: BenchmarkChoice
  benchmarkCode: string
}

export const EMPTY_FORM: FormState = {
  ticker: '', method: null, params: {}, period: '3y', start: '', end: '', capital: '', commissionBuy: '',
  commissionSell: '', minCommission: '', slippage: '', lotSize: '', adjust: 'qfq', allowUnadjusted: false,
  benchmark: 'auto', benchmarkCode: '',
}

/** The method a fresh form starts with: 均线趋势 when the service has it, else the first preset. */
export function defaultMethod(presets: Preset[]): Method | null {
  const preset = presets.find((item) => item.id === 'ma_trend') ?? presets[0]
  return preset ? { kind: 'preset', id: preset.id } : null
}

export const isDateParam = (param: Pick<PresetParam, 'unit'>) => param.unit === 'date'

// -------------------------------------------------------------------------------------------------
// Periods

const DAY = /^\d{4}-\d{2}-\d{2}$/

function validDay(text: string): boolean {
  if (!DAY.test(text)) return false
  const [y, m, d] = text.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
}

/** `day` minus `years` whole years; 29 February becomes 28 February (as the service counts). */
export function yearsBefore(day: string, years: number): string {
  const [y, m, d] = day.split('-').map(Number)
  const year = y - years
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
  const dd = m === 2 && d === 29 && !leap ? 28 : d
  return `${String(year).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(dd).padStart(2, '0')}`
}

/**
 * The dates a period preset means on `today` (YYYY-MM-DD): the service counts back from its own today and
 * ends at the latest complete bar, so this is what the form shows, not what is sent.
 */
export function periodRange(period: PeriodPreset, today: string, form?: Pick<FormState, 'start' | 'end'>): { start: string; end: string } {
  if (period === 'custom') return { start: form?.start ?? '', end: form?.end || today }
  const years = { '1y': 1, '3y': 3, '5y': 5 }[period]
  return { start: yearsBefore(today, years), end: today }
}

// -------------------------------------------------------------------------------------------------
// Numbers typed into the form

/** "0.025" (percent units) as the fraction 0.00025, moving the decimal point on the text. */
export function percentToFraction(text: string): number {
  return Number(`${text.trim()}e-2`) // exact-ok: typed text to the request's number, point moved on the text
}

/** 0.00025 as "0.025": a backend fraction in percent units, every digit kept, for placeholders. */
export function fractionToPercentText(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return ''
  return fmtPct(value, { ratio: true, exact: true, digits: 0 }).replace('%', '')
}

const NUMBER = /^-?\d+(?:\.\d+)?$/

interface Bounds {
  label: string
  min?: number | null
  max?: number | null
  /** min and max exclude the bound itself. */
  minExclusive?: boolean
  maxExclusive?: boolean
  integer?: boolean
  unit?: string
}

function boundText(value: number, unit: string | undefined): string {
  return `${value}${unit ?? ''}`
}

/** A Chinese message when `text` is not a number within the bounds, else null. '' is fine (default). */
export function checkNumber(text: string, bounds: Bounds): string | null {
  const value = text.trim()
  if (value === '') return null
  if (!NUMBER.test(value)) return `${bounds.label}应为数字`
  const number = Number(value) // exact-ok: checking typed input against bounds
  if (bounds.integer && !Number.isInteger(number)) return `${bounds.label}应为整数`
  const { min, max, unit } = bounds
  const low = min !== null && min !== undefined && (bounds.minExclusive ? number <= min : number < min)
  const high = max !== null && max !== undefined && (bounds.maxExclusive ? number >= max : number > max)
  if (low || high) {
    if (min !== null && min !== undefined && max !== null && max !== undefined && !bounds.minExclusive && !bounds.maxExclusive) {
      return `${bounds.label}应在 ${boundText(min, unit)} 到 ${boundText(max, unit)} 之间`
    }
    if (low) return `${bounds.label}${bounds.minExclusive ? '必须大于' : '不能小于'} ${boundText(min!, unit)}`
    return `${bounds.label}${bounds.maxExclusive ? '必须小于' : '不能大于'} ${boundText(max!, unit)}`
  }
  return null
}

// -------------------------------------------------------------------------------------------------
// The request

/** Field keys for errors: the form's fields, `param:<key>` for a parameter, `form` for the whole form. */
export type FieldKey =
  | 'ticker' | 'method' | 'start' | 'end' | 'capital' | 'commissionBuy' | 'commissionSell' | 'minCommission'
  | 'slippage' | 'lotSize' | 'adjust' | 'benchmark' | 'form' | `param:${string}`

export type FieldErrors = Partial<Record<FieldKey, string>>

export type BuildResult = { ok: true; body: RunBody } | { ok: false; errors: FieldErrors }

export interface BuildContext {
  presets: Preset[]
  strategies?: StrategyInfo[]
}

/** The parameter fields of the chosen method: the preset's schema, or a strategy's own parameters. */
export function methodParams(method: Method | null, context: BuildContext): PresetParam[] {
  if (!method) return []
  if (method.kind === 'preset') return context.presets.find((item) => item.id === method.id)?.params ?? []
  const strategy = context.strategies?.find((item) => item.id === method.id)
  return Object.entries(strategy?.parameters ?? {}).map(([key, value]) => ({
    key, label: key, unit: null, min: null, max: null, step: null,
    default: typeof value === 'number' || typeof value === 'string' ? value : typeof value === 'boolean' ? String(value) : null,
  }))
}

function strategyValue(text: string, original: unknown): number | string | boolean {
  if (typeof original === 'boolean') return text === 'true'
  // exact-ok: typed input to the request's number
  if (typeof original === 'number' && NUMBER.test(text.trim())) return Number(text.trim())
  return text
}

/**
 * The POST /runs body for the form, or the errors to show next to the fields (Chinese). Untouched fields
 * are left out; `save` is left to the service (true: the run goes into 历史, except in the demo).
 */
export function buildRunBody(form: FormState, context: BuildContext): BuildResult {
  const errors: FieldErrors = {}
  const body: RunBody = { ticker: form.ticker.trim() }
  if (!body.ticker) errors.ticker = '请填写要回测的标的，例如 600519 或 SHA:600519'

  if (!form.method) {
    errors.method = '请选择一个方法'
  } else if (form.method.kind === 'preset') {
    const preset = context.presets.find((item) => item.id === form.method!.id)
    if (!preset) errors.method = '这个方法现在不可用，请重新选择'
    body.preset = form.method.id
    const params: Record<string, number | string> = {}
    for (const param of preset?.params ?? []) {
      const text = (form.params[param.key] ?? '').trim()
      if (text === '') continue
      if (isDateParam(param)) {
        if (!validDay(text)) errors[`param:${param.key}`] = `${param.label}应为 YYYY-MM-DD 格式的日期`
        params[param.key] = text
        continue
      }
      const problem = checkNumber(text, { label: param.label, min: param.min, max: param.max, unit: unitSuffix(param.unit) })
      if (problem) errors[`param:${param.key}`] = problem
      params[param.key] = Number(text) // exact-ok: typed input to the request's number
    }
    if (Object.keys(params).length) body.params = params
  } else {
    body.strategy = form.method.id
    const strategy = context.strategies?.find((item) => item.id === form.method!.id)
    const params: Record<string, number | string | boolean> = {}
    for (const [key, original] of Object.entries(strategy?.parameters ?? {})) {
      const text = form.params[key]
      if (text === undefined || text.trim() === '') continue
      if (typeof original === 'number' && !NUMBER.test(text.trim())) errors[`param:${key}`] = `${key} 应为数字`
      params[key] = strategyValue(text, original)
    }
    if (Object.keys(params).length) body.params = params
  }

  if (form.period === 'custom') {
    body.period = 'custom'
    if (!form.start.trim()) errors.start = '自定义区间需要开始日期'
    else if (!validDay(form.start.trim())) errors.start = '开始日期应为 YYYY-MM-DD'
    else body.start_date = form.start.trim()
    if (form.end.trim()) {
      if (!validDay(form.end.trim())) errors.end = '结束日期应为 YYYY-MM-DD'
      else body.end_date = form.end.trim()
    }
    if (body.start_date && body.end_date && body.start_date >= body.end_date) errors.end = '结束日期必须晚于开始日期'
  } else if (form.period !== '3y') {
    body.period = form.period
  }

  const numberField = (key: FieldKey, text: string, bounds: Bounds, set: (value: number) => void) => {
    if (!text.trim()) return
    const problem = checkNumber(text, bounds)
    if (problem) errors[key] = problem
    else set(Number(text.trim())) // exact-ok: typed input to the request's number
  }
  numberField('capital', form.capital, { label: '本金', min: 0, minExclusive: true, max: 1e12 }, (v) => { body.capital = v })
  const rate = (key: FieldKey, text: string, label: string, set: (value: number) => void) => {
    if (!text.trim()) return
    const problem = checkNumber(text, { label, min: 0, max: 1, maxExclusive: true, unit: '%' })
    if (problem) errors[key] = problem
    else set(percentToFraction(text))
  }
  rate('commissionBuy', form.commissionBuy, '买入佣金率', (v) => { body.commission_buy = v })
  rate('commissionSell', form.commissionSell, '卖出佣金率', (v) => { body.commission_sell = v })
  numberField('minCommission', form.minCommission, { label: '每笔最低佣金', min: 0, max: 1000 }, (v) => { body.min_commission = v })
  if (form.slippage.trim()) {
    const problem = checkNumber(form.slippage, { label: '滑点', min: 0, max: 5, maxExclusive: true, unit: '%' })
    if (problem) errors.slippage = problem
    else body.slippage_pct = percentToFraction(form.slippage)
  }
  numberField('lotSize', form.lotSize, { label: '每手股数', min: 1, max: 1_000_000, integer: true }, (v) => { body.lot_size = v })
  if (form.adjust !== 'qfq') body.adjust = form.adjust === 'none' ? 'none' : form.adjust
  if (form.adjust === 'none' && form.allowUnadjusted) body.allow_unadjusted = true
  if (form.benchmark === 'none') body.benchmark = 'none'
  if (form.benchmark === 'custom') {
    const code = form.benchmarkCode.trim().toUpperCase()
    if (!code) errors.benchmark = '请填写指数代码，例如 IDX:000905'
    else body.benchmark = code
  }
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, body }
}

function unitSuffix(unit: string | null | undefined): string | undefined {
  if (!unit || unit === 'date') return undefined
  return unit === '%' ? '%' : ` ${unit}`
}

/** The unit shown inside a parameter field ("日", "%", "点"); none for dates. */
export function paramUnit(param: Pick<PresetParam, 'unit'>): string | undefined {
  return param.unit && param.unit !== 'date' ? param.unit : undefined
}

/** One line under a parameter: its default and range, e.g. "默认 20 日 · 可填 2–250". */
export function paramHint(param: PresetParam): string {
  if (isDateParam(param)) return param.default ? `默认 ${param.default}` : '留空表示从区间第一个交易日买入'
  const unit = param.unit && param.unit !== '%' ? ` ${param.unit}` : param.unit ?? ''
  const parts: string[] = []
  if (param.default !== null && param.default !== undefined && param.default !== '') parts.push(`默认 ${param.default}${unit}`)
  if (param.min !== null && param.max !== null) parts.push(`可填 ${param.min}–${param.max}${unit}`)
  return parts.join(' · ')
}

// -------------------------------------------------------------------------------------------------
// Refusals of POST /runs, next to the field they are about

const SPEC_FIELDS: Record<string, FieldKey> = {
  ticker: 'ticker', preset: 'method', strategy: 'method', period: 'start', start_date: 'start', end_date: 'end',
  capital: 'capital', commission_rate: 'commissionBuy', commission_buy: 'commissionBuy', commission_sell: 'commissionSell',
  min_commission: 'minCommission', slippage_pct: 'slippage', lot_size: 'lotSize', adjust: 'adjust',
  allow_unadjusted: 'adjust', benchmark: 'benchmark',
}

// Words that name a field in the service's messages, checked in this order.
const MESSAGE_FIELDS: [RegExp, FieldKey][] = [
  [/开始日期|start_date|自定义区间/, 'start'],
  [/结束日期|end_date/, 'end'],
  [/对比基准|指数代码|IDX:/, 'benchmark'],
  [/本金|初始资金/, 'capital'],
  [/买入佣金/, 'commissionBuy'],
  [/卖出佣金/, 'commissionSell'],
  [/最低佣金/, 'minCommission'],
  [/滑点/, 'slippage'],
  [/每手|整手|lot_size/, 'lotSize'],
  [/复权|除权/, 'adjust'],
  [/预设方法|方法|策略/, 'method'],
  [/标的|代码|演示模式只能|指数，不能/, 'ticker'],
]

// Messages about two parameters together (the presets' own checks, backtesting/presets.py), shown next to
// each of them when the method has both.
const PARAM_PAIRS: [RegExp, string[]][] = [
  [/买入的 RSI 线.*卖出的 RSI 线/, ['buy_below', 'sell_above']],
]

/**
 * Where to show the service's refusal of a run: the field each message is about (from `errors[].field`,
 * a parameter's 「label」, or the words of the message), else `form`. Messages are shown as the service
 * wrote them (Chinese). A server error (5xx) is never a field's fault.
 */
export function refusalErrors(refusal: ApiRefusal, params: PresetParam[]): FieldErrors {
  const out: FieldErrors = {}
  const add = (key: FieldKey, message: string) => {
    out[key] = out[key] ? `${out[key]}；${message}` : message
  }
  if (refusal.status === undefined || refusal.status >= 500 || refusal.status === 429) {
    add('form', refusal.message)
    return out
  }
  const items = refusal.errors.length ? refusal.errors : [{ field: '', message: refusal.message }]
  for (const item of items) {
    const message = item.message || refusal.message
    const field = item.field ? SPEC_FIELDS[item.field] : undefined
    const param = params.find((p) => message.includes(`「${p.label}」`) || message.startsWith(p.label))
      ?? (item.field === 'params' || !field ? params.find((p) => message.includes(p.label)) : undefined)
    if (param && (!field || item.field === 'params')) {
      add(`param:${param.key}`, message)
      continue
    }
    if (field) {
      add(field, message)
      continue
    }
    const pair = PARAM_PAIRS.find(([pattern, keys]) => pattern.test(message) && keys.every((key) => params.some((p) => p.key === key)))
    if (pair) {
      for (const key of pair[1]) add(`param:${key}`, message)
      continue
    }
    const matched = MESSAGE_FIELDS.find(([pattern]) => pattern.test(message))
    add(matched ? matched[1] : 'form', message)
  }
  return out
}

// -------------------------------------------------------------------------------------------------
// Links into the page

export interface EntryLink {
  ticker: string | null
  preset: string | null
  strategy: string | null
  entry: string | null
  from: LinkSource | null
  capital: string | null
  period: PeriodPreset | null
  params: Record<string, string>
  entryPrice: string | null
  stopPrice: string | null
  targetPrice: string | null
  plan: string | null
  run: string | null
  tab: TabId | null
}

const RESERVED = new Set(['ticker', 'preset', 'strategy', 'entry', 'from', 'capital', 'period', 'plan', 'run', 'tab',
  'entry_price', 'stop_price', 'target_price'])

const clean = (value: string | null) => (value === null || value.trim() === '' ? null : value.trim())

/** The link's keys (see the contract at the top of this file). */
export function parseLink(search: string | URLSearchParams): EntryLink {
  const query = typeof search === 'string' ? new URLSearchParams(search.startsWith('?') ? search.slice(1) : search) : search
  const from = clean(query.get('from'))
  const period = clean(query.get('period'))
  const tab = clean(query.get('tab'))
  const params: Record<string, string> = {}
  query.forEach((value, key) => {
    if (!RESERVED.has(key) && value.trim() !== '') params[key] = value.trim()
  })
  return {
    ticker: clean(query.get('ticker'))?.toUpperCase().replace('：', ':') ?? null,
    preset: clean(query.get('preset')),
    strategy: clean(query.get('strategy')),
    entry: clean(query.get('entry')),
    from: from === 'holding' || from === 'plan' || from === 'watch' ? from : null,
    capital: clean(query.get('capital')),
    period: period === '1y' || period === '3y' || period === '5y' ? period : null,
    params,
    entryPrice: clean(query.get('entry_price')),
    stopPrice: clean(query.get('stop_price')),
    targetPrice: clean(query.get('target_price')),
    plan: clean(query.get('plan')),
    run: clean(query.get('run')),
    tab: tab === 'history' || tab === 'data' || tab === 'strategies' || tab === 'quick' ? tab : null,
  }
}

/** True when the link asks to prefill the form (as opposed to opening a run or a tab). */
export function hasPrefill(link: EntryLink): boolean {
  return Boolean(link.ticker || link.preset || link.strategy || link.entry)
}

export interface Prefill {
  form: FormState
  /** Lines to show near the form: where the link came from and any conversion it made. */
  notes: string[]
  /** Parameter conversions shown under the field, by parameter key. */
  conversions: Record<string, string>
}

const SOURCE_TEXT: Record<LinkSource, string> = { holding: '来自持仓', plan: '来自交易计划', watch: '来自行情' }

function stripSign(text: string): string {
  return text.startsWith('-') ? text.slice(1) : text
}

/** The form a link asks for, on top of `base` (presets and strategies decide which keys apply). */
export function prefillFromLink(link: EntryLink, context: BuildContext, base: FormState = EMPTY_FORM): Prefill {
  const form: FormState = { ...base, params: {} }
  const notes: string[] = []
  const conversions: Record<string, string> = {}
  if (link.ticker) form.ticker = link.ticker
  let method: Method | null = base.method
  if (link.strategy && (!context.strategies || context.strategies.some((item) => item.id === link.strategy))) {
    method = { kind: 'strategy', id: link.strategy }
  } else if (link.preset) {
    if (context.presets.some((item) => item.id === link.preset)) method = { kind: 'preset', id: link.preset }
    else notes.push(`链接里的方法 ${link.preset} 现在没有，已改用默认方法`)
  }
  form.method = method ?? defaultMethod(context.presets)
  const params = methodParams(form.method, context)
  for (const param of params) {
    const given = link.params[param.key]
    if (given !== undefined) form.params[param.key] = given
    else if (isDateParam(param) && link.entry) form.params[param.key] = link.entry
  }
  // A plan's absolute prices as % of its entry price, for presets that take stop / target in %.
  if (link.entryPrice) {
    const convert = (key: string, price: string | null, what: string, below: boolean) => {
      const param = params.find((item) => item.key === key)
      if (!param || !price || form.params[key] !== undefined) return
      const change = percentChange(link.entryPrice, price, 2)
      if (change === null) return
      const lower = change.startsWith('-')
      if (lower !== below || change === '0' || change === '0.00') {
        conversions[key] = `计划${what}价 ${price} ${below ? '不低于' : '不高于'}买入价 ${link.entryPrice}，没有换算`
        return
      }
      form.params[key] = stripSign(change)
      conversions[key] = `按计划换算：${what}价 ${price} 相对计划买入价 ${link.entryPrice} 为 ${stripSign(change)}%`
    }
    convert('stop_pct', link.stopPrice, '止损', true)
    convert('target_pct', link.targetPrice, '止盈', false)
  }
  if (link.period) form.period = link.period
  if (link.capital) form.capital = link.capital
  if (link.from) notes.unshift(SOURCE_TEXT[link.from] + (link.plan ? `（计划 #${link.plan}）` : ''))
  return { form, notes, conversions }
}

/** `/backtest?...` for the given keys; empty values are left out. */
export function backtestHref(keys: Record<string, string | number | null | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(keys)) {
    if (value === null || value === undefined || String(value).trim() === '') continue
    query.set(key, String(value))
  }
  const text = query.toString()
  return text ? `/backtest?${text}` : '/backtest'
}

/** From a holding: 均线趋势 on the stock, with the position's cost as 本金 when it is known and positive. */
export function holdingLink(ticker: string, cost?: string | null): string {
  // exact-ok: only the sign of the cost is read; the text is passed on as it is
  const capital = cost && /^\d+(?:\.\d+)?$/.test(cost.trim()) && Number(cost) > 0 ? cost.trim() : null
  return backtestHref({ ticker, preset: 'ma_trend', from: 'holding', capital })
}

/** From the stock drawer or the watchlist. */
export function watchLink(ticker: string): string {
  return backtestHref({ ticker, preset: 'ma_trend', from: 'watch' })
}

export interface PlanLinkInput {
  id: number | string
  ticker: string
  entry_low: string | null
  entry_high: string | null
  stop_price: string | null
  target_price: string | null
  /** The day the plan applies from (activation, else creation), YYYY-MM-DD. */
  entry_day: string | null
}

/**
 * From a trade plan: the 止损止盈 preset bought on the plan's day, with its absolute stop and target (the
 * page converts them to % of the entry price: the middle of the entry range, or its one bound).
 */
export function planLink(plan: PlanLinkInput): string {
  const low = plan.entry_low?.trim() || null
  const high = plan.entry_high?.trim() || null
  const entry = low && high ? decMul(decAdd(low, high), '0.5') : low ?? high
  return backtestHref({
    ticker: plan.ticker, preset: 'stop_target', entry: plan.entry_day, from: 'plan', plan: plan.id,
    entry_price: entry, stop_price: plan.stop_price, target_price: plan.target_price,
  })
}

// -------------------------------------------------------------------------------------------------
// What the form says about the defaults

const UNITS: Record<string, string> = { CNY: '元', HKD: '港元', USD: '美元' }

/** The market default commission and the taxes the schedule adds, for the hint under the fee fields. */
export function feeDefaultsLine(defaults: Pick<Defaults, 'commission' | 'currency' | 'taxes'> | null | undefined): string {
  if (!defaults?.commission || typeof defaults.commission.rate !== 'number') {
    return '留空按市场默认佣金；印花税、过户费等法定费用由系统按成交日另计，不要算进佣金率'
  }
  const unit = UNITS[defaults.currency] ?? defaults.currency
  const { rate, min } = defaults.commission
  const commission = rate === 0 && !min ? '佣金 0' : `佣金 ${fractionToPercentText(rate)}%，每笔最低 ${min ?? 0} ${unit}`
  const taxes = (defaults.taxes ?? []).map((tax) => `${tax.side_label ?? ''}${tax.name ?? ''}`).filter(Boolean)
  const extra = taxes.length ? `；${taxes.join('、')}按成交日的法定费率另计，不含在佣金里` : '；这个市场没有另计的印花税'
  return `留空按市场默认：${commission}${extra}`
}

/** Edited advanced settings in words, e.g. ["本金 50000", "滑点 0.1%"], for the line above the run button. */
export function changedSettings(
  form: FormState,
  currency: string | null,
  defaults?: { capital?: number | null; slippage_pct?: number | null } | null,
): string[] {
  const unit = currency ? UNITS[currency] ?? currency : ''
  const out: string[] = []
  // A value equal to the market default (as a run opened from history carries it) is not an edit.
  const same = (typed: string, value: number | null | undefined) => finite(value) && decCmp(typed, String(value)) === 0
  const capital = form.capital.trim()
  if (capital && !same(capital, defaults?.capital)) out.push(`本金 ${capital}${unit ? ` ${unit}` : ''}`)
  if (form.commissionBuy.trim() || form.commissionSell.trim()) {
    out.push(`佣金 买 ${form.commissionBuy.trim() || '默认'}${form.commissionBuy.trim() ? '%' : ''} / 卖 ${form.commissionSell.trim() || '默认'}${form.commissionSell.trim() ? '%' : ''}`)
  }
  if (form.minCommission.trim()) out.push(`每笔最低佣金 ${form.minCommission.trim()}${unit ? ` ${unit}` : ''}`)
  const slippage = form.slippage.trim()
  if (slippage && !(finite(defaults?.slippage_pct) && decCmp(slippage, fractionToPercentText(defaults?.slippage_pct)) === 0)) out.push(`滑点 ${slippage}%`)
  if (form.lotSize.trim()) out.push(`每手 ${form.lotSize.trim()} 股`)
  if (form.adjust !== 'qfq') out.push(form.adjust === 'hfq' ? '后复权' : '不复权')
  if (form.benchmark === 'none') out.push('不对比指数')
  if (form.benchmark === 'custom' && form.benchmarkCode.trim()) out.push(`对比 ${form.benchmarkCode.trim().toUpperCase()}`)
  return out
}

/**
 * The form a finished run was made with (its manifest spec), to change a parameter and run again: the
 * method, its parameters, the period, and the settings the run used (capital, fees, slippage, lot,
 * adjustment, index). What the spec does not carry stays as in `base`.
 */
export function formFromSpec(spec: Record<string, any> | null | undefined, base: FormState = EMPTY_FORM): FormState {
  if (!spec || typeof spec !== 'object') return base
  const strategy = spec.strategy ?? {}
  const method: Method | null = strategy.kind === 'preset' && strategy.id
    ? { kind: 'preset', id: String(strategy.id) }
    : strategy.kind === 'strategy' && strategy.id ? { kind: 'strategy', id: String(strategy.id) } : base.method
  const params: Record<string, string> = {}
  for (const [key, value] of Object.entries((strategy.params ?? {}) as Record<string, unknown>)) {
    if (value === null || value === undefined) continue
    params[key] = typeof value === 'object' ? JSON.stringify(value) : String(value)
  }
  const period = spec.period?.preset
  const plain = (value: unknown, fallback: string) => (finite(value) ? String(value) : fallback)
  const percent = (value: unknown, fallback: string) => (finite(value) ? fractionToPercentText(value) : fallback)
  const benchmark = typeof spec.benchmark === 'string' ? spec.benchmark.trim() : ''
  return {
    ...base,
    ticker: typeof spec.ticker === 'string' ? spec.ticker : base.ticker,
    method,
    params,
    period: period === '1y' || period === '3y' || period === '5y' || period === 'custom' ? period : base.period,
    start: period === 'custom' ? spec.period?.start ?? '' : '',
    end: period === 'custom' ? spec.period?.requested_end ?? spec.period?.end ?? '' : '',
    capital: plain(spec.capital, base.capital),
    commissionBuy: percent(spec.commission_buy, base.commissionBuy),
    commissionSell: percent(spec.commission_sell, base.commissionSell),
    minCommission: plain(spec.min_commission, base.minCommission),
    slippage: percent(spec.slippage_pct, base.slippage),
    lotSize: plain(spec.lot_size, base.lotSize),
    // The service stores 不复权 as an empty string ('none' is how the form sends it).
    adjust: spec.adjust === '' || spec.adjust === 'none' ? 'none' : spec.adjust === 'qfq' || spec.adjust === 'hfq' ? spec.adjust : base.adjust,
    allowUnadjusted: typeof spec.allow_unadjusted === 'boolean' ? spec.allow_unadjusted : base.allowUnadjusted,
    benchmark: !benchmark ? base.benchmark : benchmark === 'auto' || benchmark === 'none' ? benchmark : 'custom',
    benchmarkCode: benchmark && benchmark !== 'auto' && benchmark !== 'none' ? benchmark : base.benchmarkCode,
  }
}
