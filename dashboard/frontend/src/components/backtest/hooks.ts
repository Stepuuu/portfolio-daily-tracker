// Data hooks of the backtest page: presets, strategies, an instrument's defaults (debounced), a run that
// is polled until it finishes, and the AI reading polled until it is written; and a chart's width.
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fmtDate } from '@/lib/format'
import { isFinished } from '@/features/backtest/result'
import { freshestReflection, pollReflection, type ReflectionState } from '@/features/backtest/reflection'
import {
  PRESETS_KEY, STRATEGIES_KEY, defaultsKey, fetchDefaults, fetchPresets, fetchRun, fetchStrategies, reflectRun, refusal, runKey,
  type ReflectionSnapshot, type RunSnapshot,
} from '@/services/backtest'

/** Today in Beijing time (the service counts periods back from its own today). */
export function useToday(): string {
  const [today] = useState(() => fmtDate(new Date(), { timeZone: 'Asia/Shanghai' }))
  return today
}

export function usePresets() {
  return useQuery({ queryKey: PRESETS_KEY, queryFn: fetchPresets, staleTime: Infinity, retry: 1 })
}

export function useStrategies(enabled = true) {
  return useQuery({ queryKey: STRATEGIES_KEY, queryFn: fetchStrategies, staleTime: 60_000, enabled, retry: 1 })
}

/** `value`, once it has not changed for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return settled
}

const LOOKS_COMPLETE = /^([A-Z]+:[A-Z0-9.]+|\d{4,6}|\d{1,5}\.HK|[A-Z]{1,5}(\.US)?)$/i

/** GET /defaults for the typed instrument, once typing pauses; the refusal (unknown code) as `error`. */
export function useDefaults(ticker: string) {
  const settled = useDebounced(ticker.trim().toUpperCase(), 400)
  const enabled = LOOKS_COMPLETE.test(settled)
  const query = useQuery({
    queryKey: defaultsKey(settled),
    queryFn: ({ signal }) => fetchDefaults(settled, signal),
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  })
  return {
    data: enabled ? query.data : undefined,
    loading: enabled && query.isFetching,
    error: enabled && query.isError ? refusal(query.error).message : null,
    settled,
  }
}

/**
 * A run: its state polled every second until it finishes, then the full result once. The state is read
 * again whenever the run is shown (its AI reading may have been written since); the reading comes from
 * the newer of the two reads, never from the full result cached when the run finished.
 */
export function useRun(runId: string | null) {
  const state = useQuery({
    queryKey: runKey(runId ?? '', false),
    queryFn: ({ signal }) => fetchRun(runId!, false, signal),
    enabled: Boolean(runId),
    staleTime: 0,
    refetchOnMount: 'always',
    retry: (count, error) => count < 2 && refusal(error).status !== 404,
    refetchInterval: (query) => (query.state.data && isFinished(query.state.data.status) ? false : 1000),
    refetchIntervalInBackground: true,
  })
  const finished = state.data?.status === 'completed'
  const full = useQuery({
    queryKey: runKey(runId ?? '', true),
    queryFn: ({ signal }) => fetchRun(runId!, true, signal),
    enabled: Boolean(runId) && finished,
    staleTime: Infinity,
    retry: 1,
  })
  const snapshot: RunSnapshot | undefined = full.data ?? state.data
  return {
    snapshot,
    reflection: freshestReflection({ data: state.data, updatedAt: state.dataUpdatedAt }, { data: full.data, updatedAt: full.dataUpdatedAt }),
    result: full.data?.result ?? null,
    loading: Boolean(runId) && (state.isPending || (finished && full.isPending)),
    error: state.isError ? refusal(state.error) : full.isError ? refusal(full.error) : null,
    refetch: () => {
      void state.refetch()
      if (finished) void full.refetch()
    },
  }
}

export interface ReflectionView {
  status: 'none' | 'pending' | 'completed' | 'failed' | 'timeout'
  result: ReflectionSnapshot['result']
  message: string | null
}

/**
 * The AI reading of a run: what the snapshot says, then `start()` asks for one and polls every 2 s
 * (bounded) until it is written. Polling stops when the page is left or another run is shown.
 */
export function useReflection(runId: string | null, initial: ReflectionSnapshot | undefined) {
  const client = useQueryClient()
  const [view, setView] = useState<ReflectionView>({ status: 'none', result: null, message: null })
  const [starting, setStarting] = useState(false)
  const token = useRef(0)

  // A finished reading goes into both cached reads of the run, so reopening it shows the reading.
  const keep = (id: string, reflection: ReflectionSnapshot) => {
    for (const withResult of [false, true]) {
      client.setQueryData<RunSnapshot>(runKey(id, withResult), (old) => (old ? { ...old, reflection } : old))
    }
  }

  const poll = (id: string) => {
    const mine = ++token.current
    setView((current) => ({ ...current, status: 'pending', message: null }))
    void pollReflection(id, (run) => fetchRun(run, false), {
      isCancelled: () => token.current !== mine,
      onUpdate: (state: ReflectionState) => {
        if (token.current !== mine) return
        setView({ status: state.status === 'cancelled' ? 'none' : state.status, result: state.result, message: state.message })
        if (state.status === 'completed' || state.status === 'failed') keep(id, { status: state.status, result: state.result, message: state.message })
      },
    }).then((state) => {
      if (token.current === mine && state.status === 'timeout') setView({ status: 'timeout', result: null, message: state.message })
    })
  }

  // A new run (or a run opened from history) starts from what its snapshot says.
  const initialStatus = initial?.status
  useEffect(() => {
    token.current += 1
    if (!runId || !initial) {
      setView({ status: 'none', result: null, message: null })
      return
    }
    const status = initial.status
    if (status === 'completed') setView({ status: 'completed', result: initial.result, message: null })
    else if (status === 'failed') setView({ status: 'failed', result: null, message: initial.message })
    else if (status === 'pending') poll(runId)
    else setView({ status: 'none', result: null, message: null })
    return () => {
      token.current += 1
    }
  }, [runId, initialStatus]) // the snapshot object changes on every poll; its status is what matters

  const start = async () => {
    if (!runId) return
    setStarting(true)
    try {
      const answer = await reflectRun(runId)
      if (answer.reflection?.status === 'completed') {
        setView({ status: 'completed', result: answer.reflection.result, message: null })
        keep(runId, answer.reflection)
      } else poll(runId)
    } catch (exc) {
      setView({ status: 'failed', result: null, message: refusal(exc).message })
    } finally {
      setStarting(false)
    }
  }
  return { view, start, starting }
}

/** The width of a chart's box in pixels, followed as the layout changes (0 until measured). */
export function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') return
    // exact-ok: a pixel width, not a figure
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}
