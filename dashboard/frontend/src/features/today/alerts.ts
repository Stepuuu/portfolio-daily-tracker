// The 今日提醒 panel: today's fired alerts (`GET /api/alerts/events`) as one line each, and the monitor's
// state (`GET /api/alerts/status`) in a few words. The honest limits come with it: alerts are checked
// only while the backend runs and a market is open, and they are not orders.
import { fmtDate, fmtPct, fmtQty, fmtTime } from '../../lib/format.ts'
import { monitorLine, type MonitorStatus } from '../alerts/monitor.ts'
import type { ChipTone } from './quoteStatus.ts'
import { DISPLAY_ZONE, isoDay, sourceLabel } from './session.ts'

export interface AlertEventLike {
  id: number
  ticker: string
  name: string | null
  kind: string
  threshold: string
  value: string | null
  price: string | null
  quote_source: string | null
  quote_time: string | null
  fired_at: string
}

export interface AlertStatusLike {
  status: string
  message?: string
  rules_enabled?: number
  notice?: string
}

/** Fires on `today` (YYYY-MM-DD, Beijing), newest first as the backend lists them; none while it is unknown. */
export function todaysEvents<E extends AlertEventLike>(events: E[] | undefined, today: string | null): E[] {
  const day = isoDay(today)
  if (!events || !day) return []
  return events.filter((event) => fmtDate(event.fired_at, { timeZone: DISPLAY_ZONE }) === day)
}

/** "10:05", "示例宽基 跌破止损 50，现价 49.95", "模拟行情 10:05:10". */
export function alertLine(event: AlertEventLike): { time: string; text: string; source: string; sell: boolean } {
  const subject = event.name && event.name !== event.ticker ? event.name : event.ticker
  const price = event.price ? fmtQty(event.price) : null
  const threshold = fmtQty(event.threshold)
  const now = price ? `，现价 ${price}` : ''
  let text: string
  switch (event.kind) {
    case 'stop_loss':
      text = `${subject} 跌破止损 ${threshold}${now}`
      break
    case 'target_price':
      text = `${subject} 达到目标价 ${threshold}${now}`
      break
    case 'day_change_pct':
      // exact-ok: the stored value keeps six places for the engine's comparison; the backend's own alert
      // text shows it at two, half up (core/alerts/messages.percent), and so does this line.
      text = `${subject} 今日涨跌 ${fmtPct(event.value, { signed: true })}，达到设定的 ${fmtPct(event.threshold, { signed: true, exact: true })}`
      break
    case 'position_weight_max':
      // exact-ok: as above, two places like the backend's alert text.
      text = `${subject} 仓位 ${fmtPct(event.value)}，达到上限 ${fmtPct(event.threshold, { exact: true })}`
      break
    default:
      text = `${subject} 触发提醒`
  }
  const when = event.quote_time ? fmtTime(event.quote_time, { timeZone: DISPLAY_ZONE }) : ''
  return {
    time: fmtTime(event.fired_at, { timeZone: DISPLAY_ZONE, seconds: false }),
    text,
    source: [sourceLabel(event.quote_source), when].filter(Boolean).join(' '),
    sell: event.kind === 'stop_loss',
  }
}

export interface AlertsListInput<E extends AlertEventLike> {
  status: AlertStatusLike
  /** The history as last received; undefined before the first answer. */
  events: E[] | undefined
  /** The latest request for the history failed. */
  failed: boolean
  error: string | null
  /** Browser time (ms) of the last successful answer; 0 for none. */
  updatedAt: number
  /** Today's date (YYYY-MM-DD, Beijing) from `/api/today`; null until it is known. */
  today: string | null
}

export interface AlertsListState<E extends AlertEventLike> {
  /** loading: no answer yet; unavailable: none ever came; empty: nothing fired today; list: `fired`. */
  kind: 'loading' | 'unavailable' | 'empty' | 'list'
  fired: E[]
  /** The line shown instead of a list (loading, unavailable, empty), else null. */
  text: string | null
  /** A later refresh failed: what is shown may be outdated. */
  stale: boolean
  staleText: string | null
}

const LOADING = '正在读取今天的提醒…'

/**
 * What the 今日提醒 list may say. Only an answered, current history, read against a known date, may claim
 * that nothing fired; a pending request (or a date not known yet) is loading, a failed one is 不可用 with
 * its reason, and a failed refresh after an answer marks the list (or the "nothing yet") as of the last
 * answer.
 */
export function alertsListState<E extends AlertEventLike>(input: AlertsListInput<E>): AlertsListState<E> {
  if (input.events === undefined) {
    return input.failed
      ? { kind: 'unavailable', fired: [], text: `提醒记录暂不可用${input.error ? `：${input.error}` : ''}。今天是否触发过提醒，现在无法确认。`, stale: false, staleText: null }
      : { kind: 'loading', fired: [], text: LOADING, stale: false, staleText: null }
  }
  // The history answered but which day is "today" is not known yet (/api/today is slower, or its time
  // could not be read): it cannot say what fired today.
  if (!isoDay(input.today)) return { kind: 'loading', fired: [], text: LOADING, stale: false, staleText: null }
  const fired = todaysEvents(input.events, input.today)
  const asOf = input.updatedAt ? fmtTime(input.updatedAt, { timeZone: DISPLAY_ZONE, seconds: false }) : null
  const stale = input.failed
  const staleText = stale
    ? `提醒记录可能不是最新：${asOf ? `${asOf} 之后` : ''}刷新失败${input.error ? `（${input.error}）` : ''}。`
    : null
  if (fired.length) return { kind: 'list', fired, text: null, stale, staleText }
  const rules = input.status.rules_enabled
  const none = rules === undefined
    ? '今天还没有触发提醒。'
    : rules ? `今天还没有触发提醒（已启用 ${rules} 条规则）。` : '设置止损、目标价等规则后，当天触发的提醒会列在这里。'
  return { kind: 'empty', fired, text: stale && rules && asOf ? `截至 ${asOf}，${none}` : none, stale, staleText }
}

/**
 * "提醒监控 运行中", whether it deserves amber and the longer explanation: the 提醒 page's own words
 * (`monitorLine`), so both pages say the same about who checks the rules, also in the demo.
 */
export function monitorState(status: MonitorStatus | undefined): { text: string; tone: ChipTone; title: string } | null {
  const line = monitorLine(status)
  if (!line) return null
  return { text: `提醒监控 ${line.label}`, tone: line.warn ? 'warn' : 'neutral', title: line.message }
}

/** The short version of the backend's guarantee notice, shown under the panel title. */
export const ALERT_LIMITS = '仅在后端运行且市场交易时检查 · 后端关闭时不会提醒 · 不是委托，不会下单'
