// What the result view says: every figure as the service computed it (its own digits) or 不可用 with the
// reason the service gave, in the run's own currency; caveats all shown, warnings first; comparisons that
// may be missing; the assumptions and data behind the run. Pure TypeScript so node tests can load it.
//
// Colours follow the product rule: a gain is up (red), a loss is down (green), and a drawdown is a loss.
// Status (completed, failed, a caveat's level) is never gain/loss: neutral or warn (amber).
import {
  UNAVAILABLE, fmtLong, fmtMoney, fmtNumber, fmtPct, fmtPrice, fmtQty, longLine, ratioPercent, toneOf, type Shown,
} from '../../lib/format.ts'
import type {
  CacheDetail, Caveat, Coverage, DownloadAnswer, LegacyInfo, EndMark, EndMarkPosition, Facts, Fill, HistoryRow, MonthlyReturn, RoundTrip, RunResult, RunSnapshot, RunStatus,
  Unfilled,
} from './types.ts'
import type { PriceData, PriceMarker } from './chart.ts'

export type Tone = 'up' | 'down' | 'flat'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

// -------------------------------------------------------------------------------------------------
// Figures

// The service's figures are binary floats: one with more than 8 places as shown (0.1 + 0.2, a slipped fill
// price) is ≈ rounded to 8 with the full value kept (lib/format.ts `fmtLong`); the *Figure forms carry it for
// the tables' titles, the *Text forms are their text.

/** A ratio as a signed percent with every digit it has: 0.182345 → "+18.2345%"; null → 不可用. */
export function pctFigure(value: unknown, options: { signed?: boolean } = {}): Shown {
  if (!finite(value)) return { text: UNAVAILABLE }
  const signed = options.signed ?? true
  return fmtLong(ratioPercent(value), (percent) => fmtPct(percent, { exact: true, digits: 2, signed }))
}

export function pctText(value: unknown, options: { signed?: boolean } = {}): string {
  return pctFigure(value, options).text
}

/** Money in the run's currency ("¥", "HK$", "$"), cents padded, nothing rounded away. */
export function moneyFigure(value: unknown, currency: string, options: { signed?: boolean } = {}): Shown {
  return finite(value) ? fmtLong(value, (v) => fmtMoney(v, currency, { exact: true, digits: 2, signed: options.signed })) : { text: UNAVAILABLE }
}

export function moneyText(value: unknown, currency: string, options: { signed?: boolean } = {}): string {
  return moneyFigure(value, currency, options).text
}

/** Direction of a gain or loss figure; null and zero are flat. */
export function gainTone(value: unknown): Tone {
  return finite(value) ? toneOf(value) : 'flat'
}

/** A drawdown is a loss: down (green) whenever it is not zero, whatever sign the figure has. */
export function drawdownTone(value: unknown): Tone {
  return finite(value) && value !== 0 ? 'down' : 'flat'
}

type MetricKind = 'pct' | 'pct_unsigned' | 'drawdown' | 'money' | 'ratio' | 'count' | 'days' | 'bars'

export interface MetricSpec {
  key: string
  label: string
  kind: MetricKind
  /** Coloured by sign (gains and losses only). */
  tone?: boolean
}

// The order of 更多指标. The service explains each one in metric_notes.
export const METRICS: MetricSpec[] = [
  { key: 'total_return', label: '总收益', kind: 'pct', tone: true },
  { key: 'annualized_return', label: '年化收益', kind: 'pct', tone: true },
  { key: 'benchmark_return', label: '一直持有的收益', kind: 'pct', tone: true },
  { key: 'alpha', label: '比一直持有多赚', kind: 'pct', tone: true },
  { key: 'max_drawdown', label: '最大回撤', kind: 'drawdown' },
  { key: 'max_drawdown_duration', label: '最长回撤期', kind: 'bars' },
  { key: 'volatility', label: '年化波动率', kind: 'pct_unsigned' },
  { key: 'downside_deviation', label: '下行波动', kind: 'pct_unsigned' },
  { key: 'var_95', label: '单日风险值（95%）', kind: 'pct', tone: true },
  { key: 'sharpe_ratio', label: '夏普比率', kind: 'ratio' },
  { key: 'sortino_ratio', label: '索提诺比率', kind: 'ratio' },
  { key: 'calmar_ratio', label: '卡玛比率', kind: 'ratio' },
  { key: 'win_rate', label: '胜率', kind: 'pct_unsigned' },
  { key: 'payoff_ratio', label: '盈亏比', kind: 'ratio' },
  { key: 'profit_factor', label: '利润因子', kind: 'ratio' },
  { key: 'avg_profit', label: '平均每笔盈利', kind: 'money', tone: true },
  { key: 'avg_loss', label: '平均每笔亏损', kind: 'money', tone: true },
  { key: 'max_single_profit', label: '最大单笔盈利', kind: 'money', tone: true },
  { key: 'max_single_loss', label: '最大单笔亏损', kind: 'money', tone: true },
  { key: 'end_mark_pnl', label: '期末持仓的浮动盈亏', kind: 'money', tone: true },
  { key: 'avg_holding_days', label: '平均持有', kind: 'days' },
  { key: 'closed_trades', label: '完成的交易', kind: 'count' },
  { key: 'total_trades', label: '交易笔数（含期末仍持有）', kind: 'count' },
  { key: 'winning_trades', label: '盈利笔数', kind: 'count' },
  { key: 'losing_trades', label: '亏损笔数', kind: 'count' },
  { key: 'breakeven_trades', label: '持平笔数', kind: 'count' },
  { key: 'final_value', label: '期末资产', kind: 'money' },
]

export interface MetricView {
  key: string
  label: string
  /** null: 不可用 (see `reason`). */
  value: string | null
  tone: Tone
  reason: string | null
  note: string | null
}

function metricValue(kind: MetricKind, value: number, currency: string): string {
  switch (kind) {
    case 'pct':
    case 'drawdown':
      return pctText(value)
    case 'pct_unsigned':
      return pctText(value, { signed: false })
    case 'money':
      return moneyText(value, currency, { signed: true })
    case 'ratio':
      return fmtNumber(value, { exact: true, digits: 2 })
    case 'days':
      return `${fmtNumber(value, { exact: true, digits: 0 })} 天`
    case 'bars':
      return `${fmtQty(value)} 个交易日`
    case 'count':
      return `${fmtQty(value)} 笔`
  }
}

/** One metric as shown; a missing or non-numeric value is 不可用 with the service's reason. */
export function metricView(result: Pick<RunResult, 'metrics' | 'metric_notes' | 'facts'>, spec: MetricSpec): MetricView {
  const raw = result.metrics?.[spec.key]
  const reasons = (result.metrics?.undefined ?? {}) as Record<string, string>
  const currency = result.facts?.currency ?? 'CNY'
  const ok = finite(raw)
  return {
    key: spec.key,
    label: spec.label,
    value: ok ? metricValue(spec.kind, raw, currency) : null,
    tone: !ok ? 'flat' : spec.kind === 'drawdown' ? drawdownTone(raw) : spec.tone ? gainTone(raw) : 'flat',
    reason: ok ? null : reasons[spec.key] ?? (raw === undefined ? '这次结果没有这个指标' : '这个指标没有定义'),
    note: result.metric_notes?.[spec.key] ?? null,
  }
}

/** 更多指标: the known metrics in order, then any other numeric metric under its own key. */
export function allMetrics(result: Pick<RunResult, 'metrics' | 'metric_notes' | 'facts'>): MetricView[] {
  const known = new Set(METRICS.map((spec) => spec.key))
  const extra = Object.keys(result.metrics ?? {})
    .filter((key) => key !== 'undefined' && !known.has(key) && (finite(result.metrics[key]) || result.metrics[key] === null))
    .map((key): MetricSpec => ({ key, label: key, kind: 'ratio' }))
  return [...METRICS, ...extra]
    .filter((spec) => spec.key in (result.metrics ?? {}))
    .map((spec) => metricView(result, spec))
}

export interface KeyFigure {
  key: string
  label: string
  value: string | null
  tone: Tone
  reason: string | null
  sub?: string
}

/** The Stat row (six at most): the run's own result. Every value is the facts' figure or 不可用. */
export function keyFigures(result: Pick<RunResult, 'facts' | 'metrics'>): KeyFigure[] {
  const facts = result.facts
  const reasons = (result.metrics?.undefined ?? {}) as Record<string, string>
  const currency = facts.currency
  const figure = (key: string, label: string, value: unknown, text: (v: number) => string, tone: Tone, reason?: string | null): KeyFigure => ({
    key, label, value: finite(value) ? text(value) : null, tone: finite(value) ? tone : 'flat',
    reason: finite(value) ? null : reason ?? reasons[key] ?? '没有这个数字',
  })
  const closed = facts.closed_trades
  const trades = figure('closed_trades', '完成交易', closed, (v) => `${fmtQty(v)} 笔`, 'flat')
  if (finite(closed) && finite(facts.winning_trades)) trades.sub = `盈利 ${fmtQty(facts.winning_trades)} 笔`
  const annual = figure('annualized_return', '年化收益', facts.annualized_return, (v) => pctText(v), gainTone(facts.annualized_return))
  return [
    figure('total_return', '总收益', facts.total_return, (v) => pctText(v), gainTone(facts.total_return)),
    annual,
    figure('max_drawdown', '最大回撤', facts.max_drawdown, (v) => pctText(v), drawdownTone(facts.max_drawdown)),
    figure('final_value', '期末资产', facts.final_value, (v) => moneyText(v, currency), 'flat'),
    trades,
    figure('fees_total', '费用合计', facts.fees_total, (v) => moneyText(v, currency), 'flat', '没有费用记录'),
  ]
}

// -------------------------------------------------------------------------------------------------
// Comparisons

export interface Comparison {
  key: 'strategy' | 'same_stock' | 'index'
  label: string
  value: string | null
  tone: Tone
  /** Why there is no value (shown with 不可用). */
  reason: string | null
}

/** 这个方法 vs 一直持有 (same stock, same costs) vs the index; each may be 不可用 with the service's reason. */
export function comparisons(result: Pick<RunResult, 'facts' | 'benchmark' | 'metrics'>): Comparison[] {
  const facts = result.facts
  const same = finite(facts.same_stock_return) ? facts.same_stock_return : result.benchmark?.same_stock?.total_return
  const index = finite(facts.index_return) ? facts.index_return : result.benchmark?.index?.total_return
  const reasons = (result.metrics?.undefined ?? {}) as Record<string, string>
  const indexLabel = facts.index_name || result.benchmark?.index?.name || facts.index_ticker || '大盘指数'
  return [
    {
      key: 'strategy', label: facts.strategy?.name ? `这个方法（${facts.strategy.name}）` : '这个方法',
      value: finite(facts.total_return) ? pctText(facts.total_return) : null, tone: gainTone(facts.total_return),
      reason: finite(facts.total_return) ? null : reasons.total_return ?? '没有算出收益',
    },
    {
      key: 'same_stock', label: '一直持有（同一只股票）',
      value: finite(same) ? pctText(same) : null, tone: gainTone(same),
      reason: finite(same) ? null : facts.same_stock_reason || result.benchmark?.same_stock?.reason || '服务没有给出原因',
    },
    {
      key: 'index', label: indexLabel,
      value: finite(index) ? pctText(index) : null, tone: gainTone(index),
      reason: finite(index) ? null
        : facts.index_reason || (facts.index_ticker || result.benchmark?.index ? '服务没有给出原因' : '这次没有选择对比指数'),
    },
  ]
}

// -------------------------------------------------------------------------------------------------
// Caveats

/** Every caveat the service sent, warnings first, none dropped (two with one code both stay). */
export function orderedCaveats(caveats: Caveat[] | null | undefined): Caveat[] {
  const list = (caveats ?? []).filter((item) => item && typeof item.text === 'string')
  return [...list.filter((item) => item.level === 'warning'), ...list.filter((item) => item.level !== 'warning')]
}

export const caveatTone = (caveat: Pick<Caveat, 'level'>): 'warn' | 'info' => (caveat.level === 'warning' ? 'warn' : 'info')

// Plain words for what a rerun found (the service's own sentence is shown under it).
export const RERUN_WORDS: Record<string, string> = {
  data_changed: '行情数据和保存时不一样',
  data_unverified: '无法核对行情数据是否和保存时一样',
  strategy_changed: '策略和保存时不一样',
  strategy_unverified: '无法核对策略是否和保存时一样',
  settings_changed: '费用或交易规则和保存时不一样',
  settings_unverified: '无法核对费用和交易规则是否和保存时一样',
  runtime_changed: '运行环境的版本变了，数字可能有极小的差别',
  engine_changed: '回测程序的版本变了',
  legacy_fees_translated: '旧版记录的卖出佣金含印花税，按旧含义计算',
}

export const RERUN_CODES = Object.keys(RERUN_WORDS)

/** true / false / null as 相同 / 不同 / 无法核对. */
export function triState(value: boolean | null | undefined): { text: string; tone: 'neutral' | 'warn' } {
  if (value === true) return { text: '相同', tone: 'neutral' }
  if (value === false) return { text: '不同', tone: 'warn' }
  return { text: '无法核对', tone: 'warn' }
}

/** The rerun's caveats in plain words, with the service's sentence. */
export function rerunFindings(result: Pick<RunResult, 'caveats'>): { code: string; title: string; text: string; tone: 'warn' | 'info' }[] {
  return orderedCaveats(result.caveats)
    .filter((item) => item.code in RERUN_WORDS)
    .map((item) => ({ code: item.code, title: RERUN_WORDS[item.code], text: item.text, tone: caveatTone(item) }))
}

// -------------------------------------------------------------------------------------------------
// Tables

const EXIT_REASON: Record<string, string> = {
  signal: '信号', stop: '止损', target: '止盈', trailing: '回落', max_hold: '到期', end_mark: '期末估值',
}

/**
 * The round trips as two lists: those completed by a sale, and what was still held after the last bar
 * (the end mark: valued at the last close, not sold). Only the first are 完整交易; their count is the
 * facts' closed_trades.
 */
export function splitRoundTrips<T extends Pick<RoundTrip, 'end_mark' | 'exit_reason'>>(trips: T[] | null | undefined): { closed: T[]; held: T[] } {
  const closed: T[] = []
  const held: T[] = []
  for (const trip of trips ?? []) (trip.end_mark || trip.exit_reason === 'end_mark' ? held : closed).push(trip)
  return { closed, held }
}

export function exitReason(trip: Pick<RoundTrip, 'exit_reason' | 'exit_reason_zh'>): string {
  return trip.exit_reason_zh || (trip.exit_reason ? EXIT_REASON[trip.exit_reason] ?? trip.exit_reason : UNAVAILABLE)
}

const UNFILLED_REASON: Record<string, string> = {
  lot_unaffordable: '一手超过可用资金', insufficient_cash: '资金不足', limit_up: '涨停买不进', limit_down: '跌停卖不出',
  suspended: '停牌或缺少当日行情', t_plus_one: 'T+1 当日不能卖出', volume_cap: '超过当日成交量的 10%',
  limit_queue: '涨跌停排队只成交一部分', below_min_lot: '数量不足一手', no_position: '没有可卖持仓',
  price_not_reached: '限价未触及', lot_rounding: '数量不是整手', end_of_data: '数据结束时仍未成交',
}

const OUTCOME: Record<string, string> = {
  not_submitted: '没有下单', expired: '过期作废', partial: '只成交一部分，其余取消', filled_late: '之后才成交',
  open_at_end: '期末仍未成交', rejected: '被拒绝', cancelled: '已取消', blocked: '没能成交',
}

/** Why an order did not fill: the service's words, else ours, else its code; never blank. */
export function unfilledReason(entry: Pick<Unfilled, 'reason' | 'reason_zh'>): string {
  if (entry.reason_zh) return entry.reason_zh
  if (entry.reason) return UNFILLED_REASON[entry.reason] ?? entry.reason
  return '服务没有给出原因'
}

export function unfilledOutcome(entry: Pick<Unfilled, 'outcome'>): string {
  return entry.outcome ? OUTCOME[entry.outcome] ?? entry.outcome : UNAVAILABLE
}

// -------------------------------------------------------------------------------------------------
// The end mark: what is still held after the last bar is valued at the last close, not sold.

const BLOCKED: Record<string, string> = {
  suspended: '最后一天停牌或没有行情', limit_down: '最后一天一字跌停', t_plus_one: '最后一天买入，A 股 T+1 当天不能卖出',
}

/** The end mark of a result: facts.end_mark (and where earlier versions put it). */
export function endMarkOf(result: Partial<Pick<RunResult, 'end_mark' | 'assumptions' | 'facts'>>): EndMark | null {
  const candidates = [result.facts?.end_mark, result.end_mark, (result.assumptions as Record<string, unknown> | undefined)?.end_mark]
  for (const item of candidates) {
    if (item && typeof item === 'object' && Array.isArray((item as EndMark).positions)) return item as EndMark
  }
  return null
}

type Blockable = Pick<EndMarkPosition, 'blocked' | 'blocked_quantity' | 'blocked_zh' | 'quantity'>

/**
 * Why an end-marked position could not really have been sold: the service's sentence (blocked_zh) as
 * it is, else ours; null when nothing blocked it.
 */
export function blockedText(position: Blockable | null | undefined): string | null {
  if (!position?.blocked) return null
  if (position.blocked_zh) return position.blocked_zh
  const part = finite(position.blocked_quantity) && finite(position.quantity) && position.blocked_quantity > 0
    && position.blocked_quantity < position.quantity ? `（其中 ${fmtQty(position.blocked_quantity)} 股）` : ''
  return `实际卖不出：${BLOCKED[position.blocked] ?? position.blocked}${part}`
}

/** The end-mark position of `symbol` (or the only one). */
export function endMarkPosition(endMark: EndMark | null, symbol?: string | null): EndMarkPosition | null {
  if (!endMark?.positions?.length) return null
  return endMark.positions.find((item) => item.symbol === symbol) ?? (endMark.positions.length === 1 ? endMark.positions[0] : null)
}

/** Why a fill or round trip that is an end mark could not be sold: its own fields, else the facts'. */
export function rowBlocked(row: Blockable & { symbol?: string | null }, endMark: EndMark | null): string | null {
  return blockedText(row) ?? blockedText(endMarkPosition(endMark, row.symbol))
}

/** A fill as listed: an end mark is 期末估值, a valuation, never shown as an ordinary sale. */
export function fillKind(fill: Pick<Fill, 'direction' | 'reason' | 'symbol' | 'end_mark' | 'blocked' | 'blocked_quantity' | 'blocked_zh' | 'quantity'>, endMark: EndMark | null): { text: string; mark: boolean; blocked: string | null } {
  if (fill.end_mark || fill.reason === 'end_mark') return { text: '期末估值（没有卖出）', mark: true, blocked: rowBlocked(fill, endMark) }
  return { text: directionText(fill.direction), mark: false, blocked: null }
}

/**
 * The AI reading as plain text: markdown markers (headings, emphasis, code ticks) are taken out, never
 * rendered, so the model's text stays text.
 */
export function plainReading(text: string | null | undefined): string {
  return String(text ?? '')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/__([^_\n]+)__/g, '$1')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1$2')
    .replace(/`([^`\n]+)`/g, '$1')
    // Emoji pictographs (a cross mark, a red circle) are not the page's language and show as boxes without an emoji font.
    .replace(/[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}][\u{FE0F}\u{200D}\p{Extended_Pictographic}]*\s?/gu, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export const directionText = (direction: string | null | undefined) => (direction === 'buy' ? '买入' : direction === 'sell' ? '卖出' : direction ?? UNAVAILABLE)

const FEE_NAMES: Record<string, string> = {
  commission: '佣金', stamp_duty: '印花税', transfer_fee: '过户费', trading_fee: '交易费', sfc_levy: '证监会征费',
  afrc_levy: '会财局征费', sec_fee: 'SEC 费',
}

/** A fill's fees as charged, by name: "佣金 ¥5.00 · 印花税 ¥8.40". */
export function feeFigure(fill: Pick<Fill, 'fees' | 'commission'>, currency: string): Shown {
  const parts = Object.entries(fill.fees ?? {}).filter(([, value]) => finite(value))
  if (!parts.length) {
    return finite(fill.commission) ? longLine((pick) => `佣金 ${pick(moneyFigure(fill.commission, currency))}`) : { text: UNAVAILABLE }
  }
  return longLine((pick) => parts.map(([name, value]) => `${FEE_NAMES[name] ?? name} ${pick(moneyFigure(value, currency))}`).join(' · '))
}

export function feeParts(fill: Pick<Fill, 'fees' | 'commission'>, currency: string): string {
  return feeFigure(fill, currency).text
}

/** Kind for price digits: A-share ETFs have three. */
export function priceKind(facts: Pick<Facts, 'kind'>): 'stock' | 'etf' {
  return facts.kind === 'etf' ? 'etf' : 'stock'
}

export function priceFigure(value: unknown, facts: Pick<Facts, 'currency' | 'kind'>): Shown {
  return finite(value) ? fmtLong(value, (v) => fmtPrice(v, facts.currency, priceKind(facts), { exact: true })) : { text: UNAVAILABLE }
}

export function priceText(value: unknown, facts: Pick<Facts, 'currency' | 'kind'>): string {
  return priceFigure(value, facts).text
}

// -------------------------------------------------------------------------------------------------
// The price chart and the monthly table

const MARKER_TITLE: Record<PriceMarker['kind'], string> = { buy: '买入', sell: '卖出', end_mark: '期末估值（没有卖出）', unfilled: '没有成交' }

/** What a marker says on hover or focus: date and kind, then price, quantity and why. */
export function markerLines(marker: PriceMarker, facts: Pick<Facts, 'currency' | 'kind'>, endMark: EndMark | null): { title: string; lines: string[] } {
  if (marker.kind === 'unfilled') {
    const entry = marker.unfilled ?? {}
    const signal = typeof entry.signal_date === 'string' && entry.signal_date !== marker.date ? `信号日 ${entry.signal_date}` : null
    return {
      title: `${marker.date} · ${directionText(entry.direction)}没有成交`,
      lines: [
        typeof entry.quantity === 'number' ? `${fmtQty(entry.quantity)} 股` : null,
        `原因：${unfilledReason(entry)}`,
        entry.outcome ? `结果：${unfilledOutcome(entry)}` : null,
        signal,
        `画在当天收盘价 ${priceText(marker.value, facts)}`,
      ].filter((line): line is string => Boolean(line)),
    }
  }
  const fill = marker.fill!
  const lines = [
    `${marker.kind === 'end_mark' ? '按最后收盘价' : '成交价'} ${priceText(fill.price, facts)}`,
    `${fmtQty(fill.quantity)} 股`,
  ]
  if (marker.kind === 'end_mark') {
    lines.push('数据结束时仍持有，只是估值，没有真的卖出')
    const blocked = rowBlocked(fill, endMark)
    if (blocked) lines.push(blocked)
  } else if (marker.kind === 'sell') {
    if (fill.reason_zh || fill.reason) lines.push(`原因：${fill.reason_zh ?? fill.reason}`)
    if (finite(fill.pnl)) lines.push(`盈亏 ${moneyText(fill.pnl, facts.currency, { signed: true })}`)
  }
  return { title: `${marker.date} · ${MARKER_TITLE[marker.kind]}`, lines }
}

/** Counts of what the price chart shows, for its summary and the line under it. */
export function markerCounts(data: Pick<PriceData, 'markers' | 'undrawn'>): { buy: number; sell: number; endMark: number; unfilled: number; undrawn: number } {
  const count = (kind: PriceMarker['kind']) => data.markers.filter((marker) => marker.kind === kind).length
  return { buy: count('buy'), sell: count('sell'), endMark: count('end_mark'), unfilled: count('unfilled'), undrawn: data.undrawn }
}

/** The price chart in words: span, first and last close, and how many of each marker. */
export function priceSummary(data: PriceData, facts: Pick<Facts, 'currency' | 'kind' | 'ticker' | 'adjust'>): string {
  const count = data.dates.length
  const closes = data.closes
  const first = closes.find(finite)
  const last = [...closes].reverse().find(finite)
  const n = markerCounts(data)
  const parts = [`买入 ${n.buy} 次`, `卖出 ${n.sell} 次`]
  if (n.endMark) parts.push(`期末估值 ${n.endMark} 次（没有卖出）`)
  if (n.unfilled) parts.push(`没有成交的委托 ${n.unfilled} 个`)
  if (n.undrawn) parts.push(`${n.undrawn} 个日期不在价格数据里，没有画出`)
  return `价格走势：${facts.ticker} ${data.dates[0] ?? ''} 至 ${data.dates[count - 1] ?? ''} 的收盘价（${adjustText(facts.adjust)}，和成交价同一口径），`
    + `从 ${priceText(first, facts)} 到 ${priceText(last, facts)}；${parts.join('，')}。左右方向键逐个查看买卖点。`
}

export interface MonthlyRow {
  year: number
  /** Twelve months: a figure, null (in the run, no figure: 不可用), undefined (outside the run). */
  months: (number | null | undefined)[]
}

/** The monthly returns as a year × month grid, oldest year first; months outside the run are undefined. */
export function monthlyRows(entries: MonthlyReturn[] | null | undefined): MonthlyRow[] {
  const valid = (entries ?? []).filter((entry) => Number.isInteger(entry?.year) && Number.isInteger(entry?.month) && entry.month >= 1 && entry.month <= 12)
  if (!valid.length) return []
  const key = (year: number, month: number) => year * 12 + month - 1
  const keys = valid.map((entry) => key(entry.year, entry.month))
  const first = Math.min(...keys)
  const last = Math.max(...keys)
  const byKey = new Map(valid.map((entry) => [key(entry.year, entry.month), finite(entry.return) ? entry.return : null]))
  const rows: MonthlyRow[] = []
  // exact-ok: whole years from a month count, not a figure
  for (let year = Math.floor(first / 12); year <= Math.floor(last / 12); year += 1) {
    const months = Array.from({ length: 12 }, (_, index) => {
      const at = key(year, index + 1)
      if (at < first || at > last) return undefined
      return byKey.has(at) ? byKey.get(at)! : null
    })
    rows.push({ year, months })
  }
  return rows
}

// -------------------------------------------------------------------------------------------------
// Assumptions and data

const ADJUST: Record<string, string> = { qfq: '前复权', hfq: '后复权', '': '不复权', none: '不复权' }

export const adjustText = (adjust: string | null | undefined) => ADJUST[adjust ?? 'qfq'] ?? String(adjust)

/** A run's adjustment as a fact: 不可用 when the result does not say (a record saved by an older version). */
export const adjustFact = (adjust: string | null | undefined) => (adjust === null || adjust === undefined ? UNAVAILABLE : adjustText(adjust))

export function sourceText(source: string | null | undefined): string {
  if (!source) return UNAVAILABLE
  if (source.startsWith('demo')) return '演示数据（程序生成，不是真实行情）'
  if (source === 'import') return 'CSV 导入'
  if (source === 'unknown') return '来源未记录（旧缓存）'
  return source
}

export function lotText(lot: Facts['lot']): string {
  if (!lot || !finite(lot.minimum)) return UNAVAILABLE
  const override = finite(lot.override) ? '（你设置的）' : ''
  if (lot.assumed) return `每手按 ${lot.minimum} 股（按市场假设，请核对）${override}`
  if (lot.minimum === 1 && lot.step === 1) return `按 1 股交易${override}`
  if (lot.step === 1) return `最少 ${lot.minimum} 股，之后 1 股递增${override}`
  return `每手 ${lot.minimum} 股${override}`
}

/** The commission the run used, as a sentence: what was set, or the market default it fell back to. */
export function commissionText(facts: Pick<Facts, 'commission' | 'currency'>): string {
  const c = facts.commission
  if (!c) return UNAVAILABLE
  const unit = { CNY: '元', HKD: '港元', USD: '美元' }[facts.currency] ?? facts.currency
  const rate = (value: number | null | undefined) => (finite(value) ? pctText(value, { signed: false }) : null)
  const fallback = rate(c.default_rate)
  const minimum = finite(c.min) ? c.min : c.default_min
  // Every figure taken from the market's defaults: say it once; otherwise mark the defaulted ones.
  const allDefault = !finite(c.buy) && !finite(c.sell) && !finite(c.min)
  const mark = allDefault ? '' : '（默认）'
  const buy = rate(c.buy) ?? (fallback ? `${fallback}${mark}` : UNAVAILABLE)
  const sell = rate(c.sell) ?? (fallback ? `${fallback}${mark}` : UNAVAILABLE)
  const minText = finite(minimum) ? `${fmtNumber(minimum, { exact: true, digits: 0 })} ${unit}${finite(c.min) ? '' : mark}` : UNAVAILABLE
  const source = !c.defaulted ? '' : allDefault ? '（都是市场默认，不是你券商的费率）' : '；标“默认”的是市场默认，不是你券商的费率'
  const included = (c.sell_rate_includes ?? []).length
    ? `；卖出佣金率已含${(c.sell_rate_includes ?? []).map((name) => FEE_NAMES[name] ?? name).join('、')}` : ''
  return `买入 ${buy}，卖出 ${sell}，每笔最低 ${minText}${source}${included}`
}

function days(list: string[] | undefined): string {
  const items = list ?? []
  return items.slice(0, 5).join('、') + (items.length > 5 ? ` 等 ${items.length} 天` : '')
}

/** What is wrong with one coverage's bars, in words (empty when nothing is). */
export function coverageProblems(item: Coverage): string[] {
  const out: string[] = []
  if (item.splice_seams?.length) out.push(`在 ${days(item.splice_seams)} 从一次下载换到另一次，两段的复权基准可能不同`)
  if (item.unverified?.length) out.push(`${days(item.unverified)} 的 K 线来自更早的下载，没有和这次下载核对`)
  if (item.dropped_provisional?.length) out.push(`去掉了收盘前存下的临时 K 线 ${days(item.dropped_provisional)}，没能重新下载`)
  if (item.ends_early) out.push(`数据到 ${item.last ?? UNAVAILABLE}，比最近收盘日 ${item.last_session ?? UNAVAILABLE} 早`)
  return out
}

export interface CoverageView {
  ticker: string
  /** Problems of these bars in words: a splice of two downloads, dropped provisional bars. */
  problems: string[]
  /** Facts about these bars that are not problems (today's bar left out before the close). */
  notes: string[]
  role: string
  adjust: string
  source: string
  range: string
  rows: string
  complete: string
  completeTone: 'neutral' | 'warn'
  fetchedAt: string
  sha: string | null
}

/** manifest.coverage as rows: the traded bars (with warm-up) and the index bars. */
export function coverageRows(result: Pick<RunResult, 'manifest' | 'facts'>): CoverageView[] {
  return (result.manifest?.coverage ?? []).map((item: Coverage, index) => ({
    ticker: item.ticker,
    problems: coverageProblems(item),
    notes: item.provisional_last_bar ? ['今天还没收盘，今天的 K 线不在数据里，数据到上一个交易日'] : [],
    role: item.ticker === result.facts.ticker || index === 0 ? '交易标的（含预热）' : '对比指数',
    adjust: adjustText(item.adjust),
    source: sourceText(item.source),
    range: item.first && item.last ? `${item.first} 至 ${item.last}` : UNAVAILABLE,
    rows: finite(item.rows) ? `${fmtQty(item.rows)} 根` : UNAVAILABLE,
    complete: item.offline ? '离线：只用了本地已有部分' : item.complete === true ? '完整' : item.complete === false ? '不完整' : UNAVAILABLE,
    completeTone: item.offline || item.complete === false || coverageProblems(item).length ? 'warn' : 'neutral',
    fetchedAt: item.fetched_at ? item.fetched_at.replace('T', ' ') : UNAVAILABLE,
    sha: item.sha256 ?? null,
  }))
}

// -------------------------------------------------------------------------------------------------
// Progress and status

export interface Step {
  id: string
  label: string
  state: 'done' | 'active' | 'pending' | 'stopped'
  detail: string | null
}

const DEFAULT_STAGES = [{ id: 'prepare_data', label: '准备数据' }, { id: 'simulate', label: '模拟交易' }, { id: 'stats', label: '统计' }]

/** The run's stages with where it is: 准备数据（本地缓存 / 正在下载）→ 模拟交易 45% → 统计. */
export function progressSteps(snapshot: Pick<RunSnapshot, 'status' | 'stage' | 'stage_detail' | 'progress' | 'stages'>): Step[] {
  const stages = snapshot.stages?.length ? snapshot.stages : DEFAULT_STAGES
  if (snapshot.status === 'completed') return stages.map((stage): Step => ({ ...stage, state: 'done', detail: null }))
  const current = stages.findIndex((stage) => stage.id === snapshot.stage)
  const stopped = snapshot.status === 'failed' || snapshot.status === 'cancelled'
  return stages.map((stage, index): Step => {
    if (current === -1) return { ...stage, state: index === 0 && !stopped ? 'active' : 'pending', detail: null }
    if (index < current) return { ...stage, state: 'done', detail: null }
    if (index > current) return { ...stage, state: 'pending', detail: null }
    const detail = stage.id === 'simulate' && finite(snapshot.progress)
      ? fmtPct(snapshot.progress, { ratio: true, digits: 0 }) // exact-ok: a progress bar, not a result
      : snapshot.stage_detail
    return { ...stage, state: stopped ? 'stopped' : 'active', detail: detail ?? null }
  })
}

/** A run's status as a chip: never red or green (a status is not a gain or a loss). */
export function statusChip(status: RunStatus | string): { text: string; tone: 'neutral' | 'accent' | 'warn' } {
  switch (status) {
    case 'queued': return { text: '排队中', tone: 'neutral' }
    case 'running': return { text: '进行中', tone: 'accent' }
    case 'completed': return { text: '已完成', tone: 'neutral' }
    case 'failed': return { text: '失败', tone: 'warn' }
    case 'cancelled': return { text: '已取消', tone: 'warn' }
    default: return { text: String(status), tone: 'neutral' }
  }
}

export const isFinished = (status: string | undefined) => status === 'completed' || status === 'failed' || status === 'cancelled'

// -------------------------------------------------------------------------------------------------
// History

export interface HistoryView {
  id: string
  instrument: string
  strategy: string
  period: string
  result: string | null
  resultTone: Tone
  date: string
  headline: string | null
  currency: string
  /** Why 重跑 is off for this row (a record saved by an older version), null when it can be rerun. */
  rerunBlocked: string | null
}

// -------------------------------------------------------------------------------------------------
// 数据: where each cached instrument's bars came from (GET /data/cache symbol_details)

export interface CacheProvenance {
  source: string
  fetchedAt: string
  complete: { text: string; tone: 'neutral' | 'warn' }
  /** One line per price adjustment: its dates, bars, completeness and download time. */
  adjustments: { adjust: string; text: string; tone: 'neutral' | 'warn' }[]
}

const completeness = (value: boolean | null | undefined): CacheProvenance['complete'] =>
  value === true ? { text: '完整', tone: 'neutral' } : value === false ? { text: '不完整', tone: 'warn' } : { text: UNAVAILABLE, tone: 'neutral' }

/** A time as the service wrote it, without the ISO "T". */
const servedTime = (value: string | null | undefined) => (value ? value.replace('T', ' ') : UNAVAILABLE)

/** An instrument's source, download time and completeness as the cache records them; 不可用 only when null. */
export function cacheProvenance(detail: CacheDetail | null | undefined): CacheProvenance {
  return {
    source: detail?.source || UNAVAILABLE,
    fetchedAt: servedTime(detail?.fetched_at),
    complete: completeness(detail?.complete),
    adjustments: (detail?.adjustments ?? []).map((item) => {
      const complete = completeness(item.complete)
      return {
        adjust: item.adjust,
        tone: complete.tone,
        text: [
          `${adjustText(item.adjust)} ${item.first ?? UNAVAILABLE} 至 ${item.last ?? UNAVAILABLE}`,
          typeof item.rows === 'number' ? `${fmtQty(item.rows)} 根` : `K 线数${UNAVAILABLE}`,
          complete.text === UNAVAILABLE ? `完整性${UNAVAILABLE}` : complete.text,
          item.fetched_at ? `下载于 ${servedTime(item.fetched_at)}` : `下载时间${UNAVAILABLE}`,
        ].join(' · '),
      }
    }),
  }
}

export interface DownloadReport {
  tone: 'neutral' | 'warn'
  lines: { text: string; tone: 'neutral' | 'warn' }[]
}

/**
 * What 更新到今天 did, per adjustment as the service reports it: how far the cache now reaches and whether
 * that is the last settled close; a failed adjustment with the service's reason and what the cache still
 * holds. An answer without the per-adjustment list gives its count only.
 */
export function downloadReport(answer: DownloadAnswer): DownloadReport {
  const entries = answer.adjustments
  if (!entries?.length) {
    return { tone: 'neutral', lines: [{ text: `保存 ${fmtQty(answer.rows_saved)} 根 K 线（没有逐项的复权方式）`, tone: 'neutral' }] }
  }
  const lines = entries.map((entry): DownloadReport['lines'][number] => {
    const label = entry.label || adjustText(entry.adjust)
    const last = entry.last ?? UNAVAILABLE
    if (!entry.ok) return { text: `${label} 没有更新：${entry.error ?? UNAVAILABLE}；缓存仍到 ${last}`, tone: 'warn' }
    if (entry.up_to_date === true) return { text: `${label} 到 ${last}（最新）`, tone: 'neutral' }
    if (entry.up_to_date === false) return { text: `${label} 到 ${last}，比最近收盘日 ${entry.last_session ?? UNAVAILABLE} 早`, tone: 'warn' }
    return { text: `${label} 到 ${last}（是否最新：${UNAVAILABLE}）`, tone: 'neutral' }
  })
  return { tone: lines.some((line) => line.tone === 'warn') ? 'warn' : 'neutral', lines }
}

/** Saved runs asked for at a time (GET /history pages by limit and offset). */
export const HISTORY_PAGE = 100

/** The offset of the next page of 历史, or undefined when the last page was not full (nothing more). */
export function nextHistoryOffset(pages: { runs: unknown[] }[], pageSize: number = HISTORY_PAGE): number | undefined {
  const last = pages[pages.length - 1]
  if (!last || last.runs.length < pageSize) return undefined
  return pages.reduce((count, page) => count + page.runs.length, 0)
}

/** Every loaded row, each run once (a run deleted between two pages moves the next one up). */
export function historyRows<T extends { run_id: string }>(pages: { runs: T[] }[] | null | undefined): T[] {
  const seen = new Set<string>()
  const rows: T[] = []
  for (const page of pages ?? []) {
    for (const row of page.runs) {
      if (seen.has(row.run_id)) continue
      seen.add(row.run_id)
      rows.push(row)
    }
  }
  return rows
}

/** One saved run as listed in 历史: headline, period, strategy, result and date. */
export function historyView(row: HistoryRow): HistoryView {
  return {
    id: row.run_id,
    instrument: row.ticker || row.symbol || UNAVAILABLE,
    strategy: row.strategy_name || row.preset || UNAVAILABLE,
    // The bars the run used; the requested range only for a record without them.
    period: row.first_date && row.last_date ? `${row.first_date} 至 ${row.last_date}`
      : row.start_date && row.end_date ? `${row.start_date} 至 ${row.end_date}` : UNAVAILABLE,
    result: finite(row.total_return) ? pctText(row.total_return) : null,
    resultTone: gainTone(row.total_return),
    date: row.run_date ? row.run_date.slice(0, 16).replace('T', ' ') : UNAVAILABLE,
    headline: row.headline_zh || null,
    currency: row.currency || 'CNY',
    // Every run of this version records its engine; a record without one was saved by v3.2.1 or earlier.
    rerunBlocked: row.engine_version ? null : LEGACY_RERUN,
  }
}

export const LEGACY_RERUN = '这条记录是旧版本保存的，缺少复现所需的设置（运行清单），无法重跑'

export interface LegacyView {
  savedBy: string
  note: string
  parts: { key: string; label: string }[]
  /** The keys of the parts it lacks. */
  missing: string[]
  /** 「不可用：旧版本没有保存…」 for a part it lacks, null for one it has. */
  unavailable: (key: string) => string | null
}

/** A result read from a record saved by an older version: what it lacks, named as the service names it. */
export function legacyView(result: { legacy?: LegacyInfo | null }): LegacyView | null {
  const legacy = result.legacy
  if (!legacy) return null
  const parts = (legacy.unavailable ?? []).map((item) => ({ key: item.key, label: item.label_zh }))
  return {
    savedBy: legacy.saved_by,
    note: legacy.note_zh,
    parts,
    missing: parts.map((part) => part.key),
    unavailable: (key) => {
      const part = parts.find((item) => item.key === key)
      return part ? `${UNAVAILABLE}：旧版本没有保存${part.label}` : null
    },
  }
}
