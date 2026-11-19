// The backtest API (dashboard/backend/api/backtest.py; RunSpec and RunResult in dashboard/backtesting/
// service.py). Runs: POST /runs (202, run_id) → poll GET /runs/{id}?include_result=false for the stage →
// GET /runs/{id} for the result; a saved run is found by the same GET. Refusals a person can fix are 4xx
// with a Chinese `detail` (and `errors: [{field, message}]` for RunSpec checks): read them with `refusal`.
// Demo mode allows runs, cancel and the AI reading; history and the bar cache are not kept there, and the
// other writes answer 403.
import axios from 'axios'
import api, { getApiErrorMessage, httpStatus } from './api'
import type {
  ApiRefusal, CacheStats, CachedSymbol, Defaults, DownloadAnswer, HistoryList, PresetList, ReflectionSnapshot, RunBody, RunSnapshot,
  StrategyInfo, StrategySource,
} from '@/features/backtest/types'

export type * from '@/features/backtest/types'

const BASE = '/backtest'
/** Planning a run or a rerun reads the bar cache and the ledger's names; give it longer than a quote. */
const SLOW = { timeout: 30_000 }

export const BACKTEST_KEY = ['backtest'] as const
export const PRESETS_KEY = [...BACKTEST_KEY, 'presets'] as const
export const HISTORY_KEY = [...BACKTEST_KEY, 'history'] as const
export const CACHE_KEY = [...BACKTEST_KEY, 'cache'] as const
export const STRATEGIES_KEY = [...BACKTEST_KEY, 'strategies'] as const
export const runKey = (id: string, withResult: boolean) => [...BACKTEST_KEY, 'run', id, withResult ? 'result' : 'state'] as const
export const defaultsKey = (ticker: string) => [...BACKTEST_KEY, 'defaults', ticker] as const

/** A failed request as status, the service's Chinese message and its per-field errors. */
export function refusal(error: unknown): ApiRefusal {
  const status = httpStatus(error)
  const data = axios.isAxiosError(error) ? (error.response?.data as { detail?: unknown; errors?: unknown } | undefined) : undefined
  const errors = Array.isArray(data?.errors)
    ? (data!.errors as unknown[]).filter((item): item is { field: string; message: string } =>
      Boolean(item) && typeof (item as { message?: unknown }).message === 'string')
      .map((item) => ({ field: String(item.field ?? ''), message: item.message }))
    : []
  let message = getApiErrorMessage(error)
  if (status === 403 && message === 'Demo mode is read-only') message = '演示模式只读：这个操作会改动保存的数据'
  if (status !== undefined && status >= 500 && typeof data?.detail !== 'string') message = `服务器出错（${status}），请稍后再试`
  return { status, message, errors }
}

export async function fetchPresets(): Promise<PresetList> {
  return (await api.get<PresetList>(`${BASE}/presets`)).data
}

export async function fetchDefaults(ticker: string, signal?: AbortSignal): Promise<Defaults> {
  return (await api.get<Defaults>(`${BASE}/defaults`, { params: { ticker }, signal, ...SLOW })).data
}

export async function startRun(body: RunBody): Promise<{ run_id: string; status: string }> {
  return (await api.post(`${BASE}/runs`, body, SLOW)).data
}

/** The run's state; `withResult` false leaves the RunResult out (`?include_result=false`) for polling. */
export async function fetchRun(id: string, withResult = true, signal?: AbortSignal): Promise<RunSnapshot> {
  const query = withResult ? '' : '?include_result=false'
  return (await api.get<RunSnapshot>(`${BASE}/runs/${encodeURIComponent(id)}${query}`, { signal, ...SLOW })).data
}

export async function cancelRun(id: string): Promise<RunSnapshot> {
  return (await api.post(`${BASE}/runs/${encodeURIComponent(id)}/cancel`)).data
}

/** Starts the AI reading (202); its outcome appears in the run's `reflection`. */
export async function reflectRun(id: string): Promise<{ run_id: string; reflection: ReflectionSnapshot }> {
  return (await api.post(`${BASE}/runs/${encodeURIComponent(id)}/reflect`, undefined, SLOW)).data
}

/** The download link of a finished run: CSV (every table, with the manifest) or JSON (the whole result). */
export function exportUrl(id: string, format: 'csv' | 'json' = 'csv'): string {
  return `/api${BASE}/runs/${encodeURIComponent(id)}/export?format=${format}`
}

/** One page of saved runs, newest first; `offset` skips the ones already listed. */
export async function fetchHistory(limit = 100, offset = 0): Promise<HistoryList> {
  return (await api.get<HistoryList>(`${BASE}/history`, { params: { limit, offset } })).data
}

export async function deleteHistory(id: string): Promise<void> {
  await api.delete(`${BASE}/history/${encodeURIComponent(id)}`)
}

/** Reruns a saved run by its manifest; `save` false keeps the rerun out of the history. */
export async function rerunHistory(id: string, save = false): Promise<{ run_id: string; rerun_of: string; status: string }> {
  return (await api.post(`${BASE}/history/${encodeURIComponent(id)}/rerun`, { save }, SLOW)).data
}

export async function fetchCachedSymbols(): Promise<{ symbols: CachedSymbol[] }> {
  return (await api.get(`${BASE}/data/symbols`)).data
}

export async function fetchCacheStats(): Promise<CacheStats> {
  return (await api.get<CacheStats>(`${BASE}/data/cache`)).data
}

/**
 * Downloads (or refreshes) bars into the cache; the provider can take a while. `all_adjustments` also
 * refreshes every other adjustment the cache holds for the symbol (an index is kept 不复权), each reported.
 */
export async function downloadBars(body: { symbol: string; start_date: string; end_date: string; adjust?: string; all_adjustments?: boolean }): Promise<DownloadAnswer> {
  return (await api.post(`${BASE}/data/download`, body, { timeout: 120_000 })).data
}

export interface CsvImportAnswer {
  symbol: string
  rows_imported: number
  filename: string | null
  adjust: string
  sorted_by_date?: boolean
  provisional_dates?: string[]
  provisional_note?: string
}

/** Imports daily bars from a UTF-8 CSV (date, open, high, low, close, volume) under `symbol` and `adjust`. */
export async function importBarsCsv(file: File, symbol: string, adjust: string): Promise<CsvImportAnswer> {
  const form = new FormData()
  form.append('file', file)
  form.append('symbol', symbol)
  form.append('adjust', adjust)
  return (await api.post(`${BASE}/data/import-csv`, form, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 60_000 })).data
}

export async function deleteCachedSymbol(symbol: string): Promise<{ deleted_rows: number }> {
  return (await api.delete(`${BASE}/data/cache/${encodeURIComponent(symbol)}`)).data
}

export async function fetchStrategies(): Promise<{ strategies: StrategyInfo[] }> {
  return (await api.get(`${BASE}/strategies`, SLOW)).data
}

export async function fetchStrategySource(id: string): Promise<StrategySource> {
  return (await api.get<StrategySource>(`${BASE}/strategies/${encodeURIComponent(id)}/source`, SLOW)).data
}
