// 设置 › 自动化与通知: the daily routines (`GET /api/routines/status`, backend/api/routines.py) as table rows:
// switch and time (from the environment, `settings`), today's run, the last success and the next attempt.
// What the status cannot say is 不可用, never blank. Pure TypeScript (node tests).
import { fmtDateTime, fmtTime } from '../../lib/format.ts'
import { DISPLAY_ZONE } from '../today/session.ts'
import { isWaiting, type RoutineRun, type RoutineStatus, type RoutinesStatus } from '../today/snapshot.ts'

export const UNAVAILABLE = '不可用'

export interface RoutineSetting {
  enabled: boolean
  time: string
  problem?: string | null
  env: { enabled: string; time?: string }
}

export interface RoutineStatusFull extends RoutineStatus {
  last_success?: { trading_date: string; finished_at: string | null; summary?: string } | null
  outstanding?: (RoutineRun & { trading_date?: string })[]
}

export interface RoutinesReport extends RoutinesStatus {
  routines?: RoutineStatusFull[]
  settings?: Record<string, RoutineSetting | { enabled: boolean; env: { enabled: string } }>
}

export interface RoutineRow {
  kind: string
  title: string
  /** 开 / 关 / 不可用 */
  switch: string
  /** "15:40", or 不可用 */
  time: string
  /** A configured time the backend did not accept (it uses the default then). */
  problem: string | null
  /** 已完成 15:41 · 今日休市 · 等待运行 …; the run's summary goes to `detail`. */
  today: string
  /** Failed, waiting for a retry, or unavailable: shown in amber (a wait for a close that is not final yet is not). */
  todayWarn: boolean
  detail: string | null
  last: string
  next: string
  /** The environment variables that change it, as `NAME=value` lines. */
  lines: string[]
}

/** The kinds in the order the day runs them; anything else after them, by title. */
const ORDER = ['premarket', 'close_snapshot', 'postmarket']

const at = (iso: string): string => fmtDateTime(iso, { timeZone: DISPLAY_ZONE })

function todayText(routine: RoutineStatusFull): { text: string; warn: boolean; detail: string | null } {
  const run = routine.today
  if (routine.error) return { text: UNAVAILABLE, warn: true, detail: routine.error }
  if (!run) {
    if (!routine.trading_date || !routine.due_at) return { text: '今日休市', warn: false, detail: null }
    return { text: '还没运行', warn: false, detail: null }
  }
  const label = run.status_text || run.status
  const finished = run.finished_at ? ` ${fmtTime(run.finished_at, { timeZone: DISPLAY_ZONE, seconds: false })}` : ''
  const warn = run.status === 'failed' || (run.status === 'pending' && !isWaiting(run)) || run.status === 'unavailable'
  return { text: run.status === 'ok' ? `${label}${finished}` : label, warn, detail: run.summary || null }
}

export function routineRows(report: RoutinesReport | undefined): RoutineRow[] {
  const routines = [...(report?.routines ?? [])].sort((a, b) => {
    const ia = ORDER.indexOf(a.kind)
    const ib = ORDER.indexOf(b.kind)
    if (ia !== ib) return (ia === -1 ? ORDER.length : ia) - (ib === -1 ? ORDER.length : ib)
    return (a.title ?? a.kind).localeCompare(b.title ?? b.kind)
  })
  return routines.map((routine) => {
    const setting = report?.settings?.[routine.kind] as RoutineSetting | undefined
    const today = todayText(routine)
    const lines: string[] = []
    if (setting?.env?.enabled) lines.push(`${setting.env.enabled}=${setting.enabled ? '1' : '0'}`)
    if (setting?.env?.time && setting.time) lines.push(`${setting.env.time}=${setting.time}`)
    return {
      kind: routine.kind,
      title: routine.title || routine.kind,
      switch: setting ? (setting.enabled ? '开' : '关') : UNAVAILABLE,
      time: setting?.time || UNAVAILABLE,
      problem: setting?.problem ?? null,
      today: today.text,
      todayWarn: today.warn,
      detail: today.detail,
      last: routine.last_success?.finished_at ? at(routine.last_success.finished_at) : '还没有',
      next: routine.next_due_at ? at(routine.next_due_at) : (setting && !setting.enabled ? '已关闭' : UNAVAILABLE),
      lines,
    }
  })
}
