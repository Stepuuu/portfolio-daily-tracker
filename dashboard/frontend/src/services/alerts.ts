// Price alerts (backend/api/alerts.py): rules with their current price and distance, create / change /
// delete, the log of fired alerts and the monitor's status. Rules a trade plan created carry
// `source: plan:<id>`; this page leaves them to the plan. Demo mode answers every write with 403.
import api from './api'
import type { AlertRule } from '@/features/alerts/rules'
import { splitDay, type AlertEvent, type MonitorStatus } from '@/features/alerts/monitor'

export type { AlertEvent, AlertRule, MonitorStatus }

/** Root key: invalidating it refreshes the rules, the events and Today's 今日提醒 panel. */
export const ALERTS_KEY = ['alerts'] as const
export const RULES_KEY = ['alerts', 'rules'] as const
/** The same keys Today's panel uses (components/today/TodayAlerts.tsx). */
export const STATUS_KEY = ['alerts', 'status'] as const

export interface NewRule {
  ticker: string
  kind: string
  threshold: string
  account?: string | null
  note?: string | null
}

export async function fetchAlertRules(options: { quotes?: boolean; signal?: AbortSignal } = {}): Promise<{ items: AlertRule[]; as_of: string }> {
  const response = await api.get('/alerts/rules', { params: { quotes: options.quotes === false ? 0 : 1 }, signal: options.signal })
  return response.data
}

export async function createAlertRule(rule: NewRule): Promise<AlertRule> {
  return (await api.post<AlertRule>('/alerts/rules', rule)).data
}

export async function updateAlertRule(
  id: number, changes: Partial<Pick<AlertRule, 'threshold' | 'account' | 'enabled' | 'note'>>,
): Promise<AlertRule> {
  return (await api.patch<AlertRule>(`/alerts/rules/${id}`, changes)).data
}

export async function deleteAlertRule(id: number): Promise<{ deleted: true; rule: AlertRule }> {
  return (await api.delete(`/alerts/rules/${id}`)).data
}

export async function fetchEvents(
  options: { limit?: number; before?: number | null; ruleId?: number; signal?: AbortSignal } = {},
): Promise<{ items: AlertEvent[]; next_before: number | null }> {
  const params: Record<string, number> = { limit: options.limit ?? 50 }
  if (options.before) params.before = options.before
  if (options.ruleId) params.rule_id = options.ruleId
  return (await api.get('/alerts/events', { params, signal: options.signal })).data
}

/** The largest page the API serves, and how many pages of one day are read before giving up. */
const DAY_PAGE = 200
const DAY_PAGES = 25

/**
 * Every fire of `today` (Beijing date), newest first: pages are read until one reaches an earlier day
 * or the log ends, so a count is never that of the first page only. Throws when the day does not fit in
 * DAY_PAGES pages (the page then says 不可用 instead of a partial count).
 */
export async function fetchDayEvents(today: string, signal?: AbortSignal): Promise<AlertEvent[]> {
  const fired: AlertEvent[] = []
  let before: number | null = null
  for (let page = 0; page < DAY_PAGES; page += 1) {
    const data = await fetchEvents({ limit: DAY_PAGE, before, signal })
    const part = splitDay(data.items, today)
    fired.push(...part.today)
    if (part.done || data.next_before === null) return fired
    before = data.next_before
  }
  throw new Error(`今天的触发记录超过 ${DAY_PAGE * DAY_PAGES} 条，无法全部读取`)
}

export async function fetchMonitorStatus(): Promise<MonitorStatus> {
  return (await api.get<MonitorStatus>('/alerts/status')).data
}
