// The monitor line of the 提醒 page (`GET /api/alerts/status`, core/alerts/monitor.py): who checks the
// rules (this backend process, another process holding the lease, nobody, or nobody by design in the
// demo), when it last checked, the rules it skips and a clock warning, in the backend's words; plus
// today's fires and how each was delivered. `monitorLine` is the one state-to-text function: Today's
// panel can use it too. Pure TypeScript so node tests can load it.
import { fmtDate, fmtTime } from '../../lib/format.ts'
import { DISPLAY_ZONE } from '../today/session.ts'
import type { AlertEventLike } from '../today/alerts.ts'

export interface SkippedRule {
  rule_id: number
  ticker?: string | null
  kind?: string | null
  code?: string | null
  message?: string | null
  since?: string | null
  held_since?: string | null
}

export interface MonitorStatus {
  /** A live lease, a healthy heartbeat, the monitor enabled and not the demo. */
  running: boolean
  /** Whether a monitor runs inside this backend process. */
  in_process?: boolean
  /** This process is the read-only demo (status `demo`). */
  demo?: boolean
  enabled?: boolean
  /** Shown state: ok, waiting, standby, demo, unresponsive …; `active_status` is the checking monitor's. */
  status: string
  active_status?: string
  role?: 'active' | 'standby' | null
  /** Who holds a live lease: this process, another one, or nobody (null). Absent from older backends. */
  lease_holder?: 'this_process' | 'another_process' | null
  lease_holder_pid?: number | null
  message: string
  /** Session-clock time of the last cycle (as `as_of` elsewhere). */
  last_cycle_at: string | null
  rules_enabled: number
  rules_errored?: number
  errored_rule_ids?: number[]
  rules_skipped?: number
  skipped_rules?: SkippedRule[]
  clock_warning?: string | null
  notice?: string
}

export interface MonitorLine {
  /** 运行中, 另一个进程在检查, 未运行, 演示模式不检查提醒 … */
  label: string
  /** Amber when alerts may not be checked: nobody checks, another process does unverified, rules skipped. */
  warn: boolean
  /** "上次检查 10:32:05"; null when it never ran. */
  checked: string | null
  /** The longer explanation, for the tooltip. */
  message: string
  /** Warnings shown as text under the line (the backend's clock warning). */
  notes: string[]
}

/** The checking monitor's state while it runs: label and whether it deserves amber. */
const RUNNING: Record<string, [string, boolean]> = {
  ok: ['运行中', false],
  waiting: ['运行中 · 等待开盘', false],
  no_rules: ['运行中 · 没有启用的规则', false],
  rule_errors: ['运行中 · 部分规则无法检查', true],
  degraded: ['行情不稳定', true],
  paused: ['行情中断，已暂停', true],
  error: ['检查出错', true],
  superseded: ['运行中', false],
  stale_cycle: ['运行中', false],
}

/** States in which nobody checks the rules. */
const STOPPED: Record<string, string> = {
  not_started: '未运行', stopped: '已停止', unresponsive: '没有响应', disabled: '已关闭',
}

const DEMO_LINE = '演示模式不检查提醒'

function clockTime(iso: string | null, today: string | null): string | null {
  if (!iso) return null
  const day = fmtDate(iso, { timeZone: DISPLAY_ZONE })
  const time = fmtTime(iso, { timeZone: DISPLAY_ZONE })
  return `${today && day !== today ? `${day.slice(5)} ` : ''}${time}`
}

/** "运行中 · 2 条规则无法检查 / 暂停检查" on top of the checking monitor's own state. */
function runningLabel(status: MonitorStatus, state: string): [string, boolean] {
  let [label, warn] = RUNNING[state] ?? ['运行中', false]
  if (state === 'rule_errors' && status.rules_errored) label = `运行中 · ${status.rules_errored} 条规则无法检查`
  const skipped = status.rules_skipped ?? status.skipped_rules?.length ?? 0
  if (skipped > 0) {
    label = `${label} · ${skipped} 条规则暂停检查`
    warn = true
  }
  return [label, warn]
}

/**
 * What the monitor line says, from the status payload. `options.demo` is the app's own mode, for a
 * backend older than the status's `demo` field.
 */
export function monitorLine(status: MonitorStatus | undefined, today: string | null = null, options: { demo?: boolean } = {}): MonitorLine | null {
  if (!status) return null
  const notes = status.clock_warning ? [status.clock_warning] : []
  const time = clockTime(status.last_cycle_at, today)
  const line = (label: string, warn: boolean, message = status.message, checked = time ? `上次检查 ${time}` : null): MonitorLine => ({
    label, warn: warn || notes.length > 0, checked, message, notes,
  })
  if (status.demo || status.status === 'demo' || (options.demo && !status.in_process)) {
    return { label: DEMO_LINE, warn: false, checked: null, message: status.status === 'demo' ? status.message : '演示数据只读，提醒监控不会启动。', notes: [] }
  }
  const active = status.active_status ?? status.status
  if (status.status === 'disabled' || active === 'disabled') return line('已关闭', true)

  // An older backend (no lease_holder): its status word, and another process when none runs here.
  if (status.lease_holder === undefined) {
    if (status.status === 'standby') return line('待命 · 另一个后端进程在检查', false)
    if (STOPPED[status.status] || !status.running) return line(STOPPED[status.status] ?? '未运行', true)
    if (status.in_process === false) {
      return line('另一个进程在检查', true, `本进程没有运行提醒监控；另一个进程${time ? `在 ${time} ` : ''}检查过，本页无法确认它此刻仍在运行。`, time ? `对方上次检查 ${time}` : null)
    }
    const [label, warn] = runningLabel(status, status.status)
    return line(label, warn)
  }

  // Nobody holds a live lease: nobody checks now, whatever the last heartbeat said.
  if (status.lease_holder === null || !status.running) {
    const stopped = STOPPED[active]
    if (status.role === 'standby') {
      return line(`待命 · 检查方${stopped ?? '没有续期'}`, true, `${status.message} 现在没有进程在检查提醒。`)
    }
    return line(stopped ?? '未运行', true, stopped ? status.message : '没有进程持有检查权，提醒现在没有被检查。')
  }
  if (status.lease_holder === 'this_process') {
    const [label, warn] = runningLabel(status, active)
    return line(label, warn)
  }
  // Another process holds the lease.
  const pid = status.lease_holder_pid ? `（pid ${status.lease_holder_pid}）` : ''
  if (status.role === 'standby') {
    // This process stands by and takes over when the holder stops renewing.
    const [label, warn] = runningLabel(status, active)
    return line(label === '运行中' ? '待命 · 另一个后端进程在检查' : `待命 · 另一个后端进程在检查 · ${label.replace(/^运行中 · /, '')}`, warn)
  }
  // Amber: this backend cannot vouch that the other process still runs.
  const [label] = runningLabel(status, active)
  return line(
    label === '运行中' ? '另一个进程在检查' : `另一个进程在检查 · ${label.replace(/^运行中 · /, '')}`,
    true,
    `本进程没有运行提醒监控；另一个进程${pid}持有检查权${time ? `，${time} 检查过` : ''}。本页无法确认它此刻仍在运行。`,
    time ? `对方上次检查 ${time}` : null,
  )
}

/** The caveat shown next to the monitor state, word for word in the wireframe. */
export const MONITOR_CAVEAT = '仅交易时段运行，后端关闭时不会提醒；不是保证成交的止损'

export interface AlertEvent extends AlertEventLike {
  rule_id: number
  account: string | null
  trading_date: string
  prev_close: string | null
  quote_status: string | null
  delivery: string
  /** For a suppressed fire: whether a published digest covers it yet; null otherwise. */
  digest_sent?: boolean | null
  title: string
  body: string
}

const SELL_KINDS = ['stop_loss', 'target_price', 'position_weight_max']

/** Whether a fire suggests recording a sale: a stop, a target reached, a position over its cap. */
export function suggestsSale(event: Pick<AlertEventLike, 'kind'>): boolean {
  return SELL_KINDS.includes(event.kind)
}

/** Fires per rule among `events` on `today` (Beijing date). Pass every fire of the day (`splitDay`). */
export function firesToday(events: Pick<AlertEvent, 'rule_id' | 'fired_at'>[] | undefined, today: string | null): Map<number, number> {
  const counts = new Map<number, number>()
  if (!today) return counts
  for (const event of events ?? []) {
    if (fmtDate(event.fired_at, { timeZone: DISPLAY_ZONE }) !== today) continue
    counts.set(event.rule_id, (counts.get(event.rule_id) ?? 0) + 1)
  }
  return counts
}

/**
 * The rules table's 今日触发 counts from the day's fires query: known only while its latest read succeeded.
 * After a refresh fails, the counts read before may miss a later fire, so they are 不可用 (with the
 * reason), never shown as current. `failure` is the error's text, or null while the query is not failing.
 */
export function dayCounts(events: Pick<AlertEvent, 'rule_id' | 'fired_at'>[] | undefined, failure: string | null, today: string | null):
  { state: 'loading' } | { state: 'unavailable'; reason: string } | { state: 'ok'; counts: Map<number, number> } {
  if (failure !== null) return { state: 'unavailable', reason: `今天的触发记录暂时无法读取：${failure}` }
  if (!events) return { state: 'loading' }
  return { state: 'ok', counts: firesToday(events, today) }
}

/** Above the 今日触发 list kept from an earlier read after a refresh failed. */
export function staleDayNote(failure: string): string {
  return `最近一次刷新失败（${failure}），下面是之前读到的记录，之后的触发可能没有列出。`
}

/**
 * One page of fires (newest first) split at `today` (Beijing date): its fires of today, and whether it
 * reached an earlier day (or is empty), so that no further page can hold more of today.
 */
export function splitDay<E extends Pick<AlertEvent, 'fired_at'>>(items: E[], today: string): { today: E[]; done: boolean } {
  const dates = items.map((event) => fmtDate(event.fired_at, { timeZone: DISPLAY_ZONE }))
  return {
    today: items.filter((_event, index) => dates[index] === today),
    done: items.length === 0 || dates.some((date) => date < today),
  }
}

/**
 * 已通知 / 已汇总 (held back over the cap and covered by a published digest) / 已抑制（等待下一次汇总）
 * (held back, no digest yet: also when the backend does not say) / 重复.
 */
export function deliveryLabel(delivery: string, digestSent?: boolean | null): string {
  if (delivery === 'suppressed') return digestSent ? '已汇总' : '已抑制（等待下一次汇总）'
  if (delivery === 'notified') return '已通知'
  if (delivery === 'duplicate') return '重复'
  return delivery
}
