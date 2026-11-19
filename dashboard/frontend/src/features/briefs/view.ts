// What the 今日简报 page shows for each brief and fact. Pure TypeScript (relative `.ts` imports only) so
// node tests can load it. Numbers are only formatted here, never computed or rounded: every value comes
// from the backend with its digits, source and time; null stays 不可用 and the backend's sentence (why, or
// what the number means) goes on the line under it, never in the value's place.
import { UNAVAILABLE, fmtDate, fmtLong, fmtMoney, fmtPct, fmtTime, toneOf, type Shown } from '../../lib/format.ts'
import type { PnlDirection } from '../../lib/pnlColor.ts'
import { statusChip, type Chip } from '../today/quoteStatus.ts'
import { DISPLAY_ZONE, sourceLabel } from '../today/session.ts'
import type { QuoteStatus } from '../today/types.ts'
import type { Brief, BriefDay, BriefItem, BriefKind, Fact, FactItem, FactRef } from './types.ts'

export const KINDS: BriefKind[] = ['premarket', 'postmarket']
export const KIND_LABEL: Record<BriefKind, string> = { premarket: '盘前', postmarket: '盘后' }

/** While a brief is being generated the page asks every 2 s; on today's page, or while a retry is scheduled,
 * once a minute. */
export const POLL_BUSY_MS = 2_000
export const POLL_TODAY_MS = 60_000

const QUOTE_STATUSES = new Set<string>(['live', 'delayed', 'closed', 'stale', 'prev_close', 'time_unknown', 'unavailable'])

// -- dates --------------------------------------------------------------------------------------

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** "2026-11-17 周二". */
export function dateLabel(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  if (!match) return date
  const day = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))).getUTCDay()
  return `${date} ${WEEKDAYS[day]}`
}

/** "11-17 08:45" or "08:45:12" when the time is on `day` (Beijing). */
export function timeLabel(iso: string | null | undefined, day: string | null, seconds = false): string {
  if (!iso) return ''
  const date = fmtDate(iso, { timeZone: DISPLAY_ZONE })
  if (date === UNAVAILABLE) return ''
  const time = fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds })
  return day && date !== day ? `${date.slice(5)} ${fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds: false })}` : time
}

/**
 * The date the demo should open on: the newest stored brief, when the page was opened without a date
 * and the current trading day has none. Elsewhere null (the page stays on the day it shows).
 */
export function demoJump(day: BriefDay | undefined, explicitDate: string | null): string | null {
  if (!day || explicitDate || day.mode !== 'demo' || !day.latest_date || day.latest_date === day.date) return null
  return KINDS.some((kind) => day.items[kind].brief) ? null : day.latest_date
}

/**
 * Ask again while something is being generated, while either brief has an automatic retry scheduled (its run
 * row's `next_attempt_at`: a failed post-market brief retried at 16:00 must replace the failure on an open
 * page), and now and then on the current day while a brief is still to come.
 */
export function pollInterval(day: BriefDay | undefined): number | false {
  if (!day) return false
  const items = KINDS.map((kind) => day.items[kind])
  if (items.some((item) => item.state === 'running' || item.state === 'pending')) return POLL_BUSY_MS
  if (items.some((item) => Boolean(item.run?.next_attempt_at) && item.run?.status !== 'ok')) return POLL_TODAY_MS
  if (day.date === day.current_trading_date && items.some((item) => item.state === 'scheduled' || item.state === 'due')) {
    return POLL_TODAY_MS
  }
  return false
}

export const CALENDAR_UNVERIFIED = '交易日历未核实（按工作日推算）'

/** The mark of a day whose mainland calendar is only the weekday fallback (it may be a holiday), else null. */
export function calendarMark(day: BriefDay | undefined, date: string | null | undefined): string | null {
  if (!day || !date) return null
  return day.calendar?.[date] === 'weekday_only' ? CALENDAR_UNVERIFIED : null
}

// -- a column -----------------------------------------------------------------------------------

export type ColumnTone = 'neutral' | 'warn' | 'busy'

export interface ColumnState {
  /** The line under the column title: "08:45 生成" / "生成中" / "失败" ... */
  status: string
  tone: ColumnTone
  /** Why, in a sentence (the backend's reason), or null. */
  reason: string | null
  /** Show the stored brief below (a failed run keeps what it built). */
  showBrief: boolean
  /** 'run' for the first manual run of the day, 'rerun' after a failed or skipped one: offered only when the
   * backend's `can_run` says the run would be accepted. */
  action: 'run' | 'rerun' | null
  /** Why it cannot be run now (the backend's `run_refusal`), shown instead of the action. */
  actionBlocked: string | null
}

export function columnState(item: BriefItem, demo: boolean): ColumnState {
  const generated = item.brief ? timeLabel(item.brief.generated_at, item.trading_date, false) : ''
  const base = { reason: item.reason, showBrief: Boolean(item.brief), action: null, actionBlocked: null } as const
  const offer = (action: 'run' | 'rerun'): Pick<ColumnState, 'action' | 'actionBlocked'> => {
    if (demo) return { action: null, actionBlocked: null }
    return item.can_run ? { action, actionBlocked: null } : { action: null, actionBlocked: item.run_refusal }
  }
  switch (item.state) {
    case 'ok':
      return { ...base, status: generated ? `${generated} 生成` : '已生成', tone: 'neutral', reason: null }
    case 'demo':
      return { ...base, status: item.brief ? `演示数据 · ${generated} 生成` : '演示数据', tone: 'neutral' }
    case 'running':
      return { ...base, status: '生成中', tone: 'busy', reason: null }
    case 'pending':
      return { ...base, status: '等待生成', tone: 'busy', reason: null }
    case 'failed':
      return { ...base, status: '失败', tone: 'warn', ...offer('rerun') }
    case 'skipped':
    case 'unavailable':
      return { ...base, status: item.state_text, tone: 'neutral', ...offer('rerun') }
    case 'scheduled':
      return { ...base, status: `尚未生成 · ${item.time} 自动生成`, tone: 'neutral', reason: null }
    case 'due':
      return { ...base, status: '尚未生成', tone: 'neutral', ...offer('run') }
    default:
      return { ...base, status: item.state_text, tone: 'neutral' }
  }
}

/** Amber summary of what is not known or not verified in a brief, or null when nothing is. */
export function qualityNote(brief: Brief): string | null {
  return brief.quality.level === 'complete' ? null : brief.quality.text
}

// -- a fact -------------------------------------------------------------------------------------

export interface FactValue extends Shown {
  /** The backend's value; past 8 places ≈ rounded to 8 with the full value in `full` (lib/format.ts `fmtLong`). */
  text: string
  /** Red up / green down for signed facts; null otherwise. */
  tone: PnlDirection | null
  missing: boolean
}

const MONEY = new Set(['CNY', 'HKD', 'USD'])

/**
 * The figure on the right of a row: the backend's value with every digit it has (money and percentages are
 * padded to two places, never rounded; past 8 places ≈ and 8 places, the full value kept), a count as its
 * number, 不可用 for null. Never a sentence: a zero review count is "0" and its "没有到期的复查" goes on the
 * detail line.
 */
export function factValue(fact: Fact): FactValue {
  if (fact.value === null || fact.value === undefined) return { text: UNAVAILABLE, tone: null, missing: true }
  const value = fact.value
  const signed = Boolean(fact.signed)
  const unit = fact.unit
  let shown: Shown
  if (unit === 'pct') shown = fmtLong(value, (v) => fmtPct(v, { signed, exact: true }))
  else if (unit && MONEY.has(unit)) shown = fmtLong(value, (v) => fmtMoney(v, unit, { signed, exact: true }))
  else shown = { text: String(value) }
  return { ...shown, tone: signed ? toneOf(String(value)) : null, missing: false }
}

/** "3 项", "1 条": a phrase that only repeats the count. */
function onlyTheCount(fact: Fact): boolean {
  return fact.unit === 'count' && new RegExp(`^${String(fact.value)} ?(项|条|笔|个)$`).test(fact.text ?? '')
}

/**
 * The line under the label: the backend's sentence, unless the value or the sub-rows already say it
 * (a deviation's message repeated in its trade row, a list of names repeated as rows). For a null value it
 * is the reason (without the leading 不可用, which the value says).
 */
export function factDetail(fact: Fact): string | null {
  if (fact.value === null || fact.value === undefined) {
    return fact.text.replace(/^不可用：/, '') || null
  }
  if (onlyTheCount(fact)) return null
  const rows = (fact.items ?? []).map((item) => itemText(item).text)
  if (fact.text && rows.length > 0 && fact.text.split('、').every((part) => rows.some((row) => row.includes(part)))) {
    return null
  }
  return fact.text || null
}

/**
 * The amber marks of a value that is not a verified figure, each on its own: the quote's state when it is
 * not a fresh or verified closing price, 估算 whenever the value is estimated (a live price converted at a
 * stale rate included), 盘中价 when it comes from a session still trading; 未查询 for a source not asked.
 */
export function factChips(fact: Fact, day: string | null): Chip[] {
  const status = fact.status
  if (!status) return []
  if (fact.value === null || fact.value === undefined) {
    return status.code === 'not_queried' ? [{ label: '未查询', tone: 'warn', title: status.reason ?? '没有查询这个数据源' }] : []
  }
  const chips: Chip[] = []
  const quoteChip = QUOTE_STATUSES.has(status.code) && status.code !== 'live' && (status.code !== 'closed' || status.estimated)
  if (quoteChip) {
    const quote = {
      source: fact.source, quote_time: fact.as_of, fetched_at: null, status: status.code as QuoteStatus,
      reason: status.reason, session_date: null,
    }
    const chip = statusChip(quote, day, status.estimated, status.reason)
    if (chip) chips.push(status.estimated ? { ...chip, tone: 'warn', title: status.reason ?? chip.title } : chip)
  }
  if (status.estimated) chips.push({ label: '估算', tone: 'warn', title: status.reason ?? '基于未经核实的行情或汇率' })
  if (status.provisional) {
    chips.push({ label: '盘中价', tone: 'warn', title: status.reason ?? '交易时段尚未结束：这是盘中价，不是收盘价，之后会变' })
  }
  return chips
}

/**
 * "东财 15:00" (quote facts), or the source alone when the time is the brief's own; a converted amount adds
 * the rate it used, with that rate's source and time: "模拟行情 05:00 · 汇率 USD 7.1 模拟行情 15:44".
 */
export function factMeta(fact: Fact, generatedAt: string | null, day: string | null): string {
  const source = sourceLabel(fact.source)
  const time = fact.as_of && fact.as_of !== generatedAt ? timeLabel(fact.as_of, day) : ''
  const own = [source, time].filter(Boolean).join(' ')
  if (!fact.fx || !fact.fx.rate) return own
  const rate = [`汇率 ${fact.fx.currency} ${fact.fx.rate}`, sourceLabel(fact.fx.source), fact.fx.as_of ? timeLabel(fact.fx.as_of, day) : '']
    .filter(Boolean).join(' ')
  return own ? `${own} · ${rate}` : rate
}

export function isWarning(fact: Fact): boolean {
  return fact.level === 'warning'
}

// -- links --------------------------------------------------------------------------------------

/** Metrics about a holding: its ticker leads to the Today page; anything else to the market page. */
const HOLDING_METRICS = new Set(['day_pnl_cny', 'trade_amount'])

export interface Target {
  href: string
  label: string
}

/** Where a fact's (or sub-row's) reference leads, among the routes that exist; null for none. */
export function refTarget(ref: FactRef | null | undefined, routes: ReadonlySet<string>, metric?: string | null): Target | null {
  if (!ref) return null
  if (ref.plan_id !== undefined && routes.has('/plans')) return { href: `/plans?plan=${ref.plan_id}`, label: `查看计划 #${ref.plan_id}` }
  if ((ref.alert_event_id !== undefined || ref.alert_rule_id !== undefined) && routes.has('/alerts')) {
    return { href: '/alerts', label: '查看提醒' }
  }
  if (ref.journal_id !== undefined && routes.has('/notes')) return { href: '/notes', label: '查看笔记' }
  if (ref.ticker) {
    const holding = metric ? HOLDING_METRICS.has(metric) : false
    const href = holding || !routes.has('/market') ? '/' : '/market'
    if (routes.has(href)) return { href, label: holding || href === '/' ? '在今日页查看' : '在行情页查看' }
  }
  return null
}

/** A sub-row's display: its text, or a headline (third-party text, shown as plain text and labelled). */
export function itemText(item: FactItem): { text: string; thirdParty: boolean; when: string | null } {
  if (item.title && typeof item.title.untrusted_text === 'string') {
    return { text: item.title.untrusted_text, thirdParty: true, when: item.published ?? null }
  }
  return { text: item.text ?? '', thirdParty: false, when: null }
}
