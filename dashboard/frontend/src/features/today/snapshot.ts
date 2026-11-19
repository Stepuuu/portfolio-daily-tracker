// The "今日快照" chip on the session line, from `GET /api/routines/status` (kind `close_snapshot`).
import { fmtTime } from '../../lib/format.ts'
import type { Chip } from './quoteStatus.ts'
import { DISPLAY_ZONE } from './session.ts'

export interface RoutineRun {
  status: 'pending' | 'running' | 'ok' | 'failed' | 'skipped' | 'unavailable'
  status_text?: string
  summary?: string
  /** `waiting` on a `pending` run that waits for a moment (a close not final yet): not a failure. */
  error_category?: string | null
  finished_at?: string | null
  next_attempt_at?: string | null
}

export interface RoutineStatus {
  kind: string
  title?: string
  trading_date?: string | null
  due_at?: string | null
  next_due_at?: string | null
  today?: RoutineRun | null
  error?: string
}

export interface RoutinesStatus {
  now?: string
  scheduler?: { alive: boolean; mode?: string; note?: string | null }
  routines?: RoutineStatus[]
}

export const SNAPSHOT_KIND = 'close_snapshot'

/** A run that waits for a moment (the close snapshot before a market's close is final): not a failure or a retry. */
export const isWaiting = (run: RoutineRun): boolean => run.status === 'pending' && run.error_category === 'waiting'

const time = (iso: string | null | undefined) => (iso ? fmtTime(iso, { timeZone: DISPLAY_ZONE, seconds: false }) : '')

/** The chip, or null when the backend has no close-snapshot routine (the chip is then not shown). */
export function snapshotChip(status: RoutinesStatus | undefined, now: number = Date.now()): Chip | null {
  const routine = status?.routines?.find((item) => item.kind === SNAPSHOT_KIND)
  if (!routine) return null
  const name = '今日快照'
  if (routine.error) return { label: `${name} 状态不可用`, tone: 'warn', title: routine.error }
  const run = routine.today
  const alive = status?.scheduler?.alive ?? false
  const note = status?.scheduler?.note ?? undefined
  if (run) {
    switch (run.status) {
      case 'ok': return { label: `${name} 已完成${run.finished_at ? ` ${time(run.finished_at)}` : ''}`, tone: 'neutral', title: run.summary }
      case 'running': return { label: `${name} 生成中`, tone: 'neutral', title: run.summary }
      case 'pending':
        if (isWaiting(run)) {
          return { label: `${name} ${run.next_attempt_at ? `${time(run.next_attempt_at)} 后记入` : '等待中'}`, tone: 'neutral', title: run.summary }
        }
        return { label: `${name} 待重试${run.next_attempt_at ? ` ${time(run.next_attempt_at)}` : ''}`, tone: 'warn', title: run.summary }
      case 'failed': return { label: `${name} 失败`, tone: 'warn', title: run.summary || '收盘快照没有生成，可在设置中重试' }
      case 'skipped': return { label: `${name} 已跳过`, tone: 'neutral', title: run.summary }
      default: return { label: `${name} 不可用`, tone: 'warn', title: run.summary }
    }
  }
  if (!routine.trading_date || !routine.due_at) return { label: '今日休市 · 无快照', tone: 'neutral' }
  const due = Date.parse(routine.due_at)
  if (!alive) return { label: `${name} 不会自动生成`, tone: 'warn', title: note ?? '后台调度器未运行' }
  if (Number.isFinite(due) && due > now) return { label: `${name} ${time(routine.due_at)} 自动生成`, tone: 'neutral' }
  return { label: `${name} 即将生成`, tone: 'neutral' }
}
