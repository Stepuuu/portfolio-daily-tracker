// `GET /api/instruments/search` for a field being typed in: debounced, the previous request cancelled,
// answers kept for a short while so going back and forth is instant. Nothing is searched while an input
// method is composing (拼音 half typed); the caller passes `query: null` then.
import { useEffect, useRef, useState } from 'react'
import { getApiErrorMessage } from '@/services/api'
import { searchInstruments } from '@/services/instruments'
import type { InstrumentSearch } from '@/lib/instrument'

const DEBOUNCE_MS = 150
const KEEP_MS = 20_000
const LIMIT = 12

const cache = new Map<string, { at: number; data: InstrumentSearch }>()

/** Forget the cached answers (after a watchlist change or a remembered exchange: the tags changed). */
export function clearInstrumentCache() {
  cache.clear()
}

function cached(query: string): InstrumentSearch | null {
  const entry = cache.get(query)
  if (!entry) return null
  if (Date.now() - entry.at > KEEP_MS) {
    cache.delete(query)
    return null
  }
  return entry.data
}

/** One search, outside a field (resolving a typed code when the field loses focus). */
export async function searchOnce(query: string): Promise<InstrumentSearch> {
  const hit = cached(query)
  if (hit) return hit
  const data = await searchInstruments(query, LIMIT)
  cache.set(query, { at: Date.now(), data })
  return data
}

export interface SearchState {
  /** The query the shown answer is for. */
  query: string | null
  data: InstrumentSearch | null
  loading: boolean
  error: string | null
}

/**
 * Searches for `query` (an empty string lists holdings, then the watchlist); `null` searches nothing
 * and keeps the last answer on screen.
 */
export function useInstrumentSearch(query: string | null): SearchState {
  const [state, setState] = useState<SearchState>({ query: null, data: null, loading: false, error: null })
  const sequence = useRef(0)

  useEffect(() => {
    if (query === null) return
    const id = ++sequence.current
    const hit = cached(query)
    if (hit) {
      setState({ query, data: hit, loading: false, error: null })
      return
    }
    setState((previous) => ({ ...previous, loading: true, error: null }))
    const controller = new AbortController()
    const timer = window.setTimeout(async () => {
      try {
        const data = await searchInstruments(query, LIMIT, controller.signal)
        cache.set(query, { at: Date.now(), data })
        if (id === sequence.current) setState({ query, data, loading: false, error: null })
      } catch (exc) {
        if (controller.signal.aborted || id !== sequence.current) return
        setState({ query, data: null, loading: false, error: getApiErrorMessage(exc) })
      }
    }, query ? DEBOUNCE_MS : 0)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  return state
}
