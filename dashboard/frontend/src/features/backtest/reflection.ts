// The AI reading (AI 解读) of a run is written in the background after POST /runs/{id}/reflect: the run's
// GET /api/backtest/runs/{id}?include_result=false snapshot carries {reflection: {status, result, message}}.
// pollReflection asks every 2 s until it is completed, failed or none, a bounded number of times, and
// stops at once when cancelled (the page was left, or a newer run is shown).

export type ReflectionOutcome = 'pending' | 'completed' | 'failed' | 'timeout' | 'cancelled'

export interface ReflectionState {
  status: ReflectionOutcome
  result: any
  message: string | null
  attempts: number
}

export interface PollOptions {
  intervalMs?: number
  maxAttempts?: number
  sleep?: (ms: number) => Promise<void>
  onUpdate?: (state: ReflectionState) => void
  isCancelled?: () => boolean
}

export const POLL_INTERVAL_MS = 2000
export const POLL_MAX_ATTEMPTS = 90 // three minutes at the default interval

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export async function pollReflection(
  runId: string,
  fetchRun: (runId: string) => Promise<any>,
  options: PollOptions = {},
): Promise<ReflectionState> {
  const interval = options.intervalMs ?? POLL_INTERVAL_MS
  const maxAttempts = options.maxAttempts ?? POLL_MAX_ATTEMPTS
  const sleep = options.sleep ?? wait
  const cancelled = (attempts: number): ReflectionState => ({ status: 'cancelled', result: null, message: null, attempts })
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await sleep(interval)
    if (options.isCancelled?.()) return cancelled(attempt - 1)
    let reflection: any
    try {
      reflection = (await fetchRun(runId))?.reflection
    } catch {
      if (options.isCancelled?.()) return cancelled(attempt)
      // A failed request (network, a restart) counts as an attempt and is retried.
      options.onUpdate?.({ status: 'pending', result: null, message: '查询失败，正在重试', attempts: attempt })
      continue
    }
    if (options.isCancelled?.()) return cancelled(attempt)
    const status = reflection?.status
    if (status === 'completed' || status === 'failed') {
      const state: ReflectionState = {
        status, result: reflection.result ?? null, message: reflection.message ?? null, attempts: attempt,
      }
      options.onUpdate?.(state)
      return state
    }
    if (status !== 'pending') {
      // Nothing is being written for this run (the server restarted, or never started one).
      const state: ReflectionState = {
        status: 'failed', result: null, message: '服务器上没有正在生成的解读（可能已重启），可以再生成一次', attempts: attempt,
      }
      options.onUpdate?.(state)
      return state
    }
    options.onUpdate?.({ status: 'pending', result: null, message: null, attempts: attempt })
  }
  return {
    status: 'timeout', result: null, attempts: maxAttempts,
    // exact-ok: seconds of waiting, not a figure
    message: `等了约 ${Math.round((interval * maxAttempts) / 1000)} 秒解读还没有生成；稍后在历史记录里打开这次回测可以看到`,
  }
}

/** One cached read of a run, with when it arrived (react-query's dataUpdatedAt). */
export interface SnapshotRead<T> {
  data: { reflection?: T } | undefined
  updatedAt: number
}

/**
 * The reading to show for a run: the one in the newer of its two reads. The full result is cached once
 * when the run finishes; the reading is written later (on request), so a run reopened from 历史 must not
 * take it from that cache when its state was read afterwards.
 */
export function freshestReflection<T>(state: SnapshotRead<T>, full: SnapshotRead<T>): T | undefined {
  const reads = [state, full].filter((read) => read.data?.reflection !== undefined)
  if (!reads.length) return undefined
  return reads.reduce((newest, read) => (read.updatedAt > newest.updatedAt ? read : newest)).data!.reflection
}
