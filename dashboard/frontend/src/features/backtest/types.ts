// Payloads of the backtest API (dashboard/backend/api/backtest.py, dashboard/backtesting/service.py).
// Numbers are JSON numbers as the service computed them (ratios such as 0.182345 for +18.2345%, money
// rounded to cents); null where a figure has no value, with the reason next to it (metrics.undefined,
// facts.*_reason). Fields the service may add later are tolerated: everything optional is optional.

export type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ReflectionStatus = 'none' | 'pending' | 'completed' | 'failed'

/** One parameter of a preset, as GET /presets describes it. A date parameter has unit "date". */
export interface PresetParam {
  key: string
  label: string
  unit: string | null
  default: number | string | null
  min: number | null
  max: number | null
  step: number | null
}

export interface Preset {
  id: string
  name: string
  summary: string
  params: PresetParam[]
}

export interface PresetList {
  available: boolean
  presets: Preset[]
  detail?: string
}

export interface DefaultsTax {
  id: string
  name: string
  rate: number
  sides: string[]
  side_label: string
}

/** GET /defaults?ticker=: the market, currency, lot rule and fee assumptions of one instrument. */
export interface Defaults {
  ticker: string
  name: string | null
  market: string
  market_label: string
  kind: string
  currency: string
  demo: boolean
  capital: number
  period: string
  lot: { minimum: number; step: number; assumed: boolean; note: string | null }
  commission: { rate: number; min: number; source: string }
  taxes: DefaultsTax[]
  slippage_pct: number
  adjust: string
  benchmark: { ticker: string; name: string | null } | null
  fee_schedule_version?: string
  local_data?: Record<string, { first?: string; last?: string; rows?: number }>
  line_zh: string
  assumptions_zh: string
}

/** The body of POST /runs (RunSpec). Omitted fields take the service's defaults. */
export interface RunBody {
  ticker: string
  preset?: string
  strategy?: string
  params?: Record<string, number | string | boolean>
  period?: '1y' | '3y' | '5y' | 'custom'
  start_date?: string
  end_date?: string
  capital?: number
  commission_buy?: number
  commission_sell?: number
  min_commission?: number
  slippage_pct?: number
  lot_size?: number
  adjust?: string
  allow_unadjusted?: boolean
  benchmark?: string
  save?: boolean
}

export interface Caveat {
  code: string
  level: string
  text: string
}

export interface StrategyFacts {
  kind?: string | null
  id?: string | null
  name?: string | null
  class?: string | null
  params?: Record<string, unknown> | null
}

export interface Facts {
  ticker: string
  label?: string
  name?: string | null
  market?: string
  market_label?: string
  currency: string
  kind?: string
  source?: string | null
  demo?: boolean
  strategy?: StrategyFacts
  period?: {
    preset?: string
    requested_start?: string
    requested_end?: string
    first_date?: string | null
    last_date?: string | null
    calendar_days?: number | null
    bars?: number | null
    span_label?: string | null
  }
  first_date?: string | null
  last_date?: string | null
  capital?: number | null
  final_value?: number | null
  total_return?: number | null
  annualized_return?: number | null
  same_stock_return?: number | null
  same_stock_reason?: string | null
  index_ticker?: string | null
  index_name?: string | null
  index_return?: number | null
  index_reason?: string | null
  max_drawdown?: number | null
  max_drawdown_peak?: string | null
  max_drawdown_trough?: string | null
  closed_trades?: number | null
  winning_trades?: number | null
  losing_trades?: number | null
  open_at_end?: number | null
  fills?: number | null
  fees_total?: number | null
  adjust?: string
  warmup_bars?: number | null
  warmup_auto?: boolean
  lot?: { minimum?: number; step?: number; assumed?: boolean; override?: number | null }
  commission?: {
    buy?: number | null
    sell?: number | null
    min?: number | null
    default_rate?: number | null
    default_min?: number | null
    defaulted?: boolean
    sell_rate_includes?: string[]
  }
  slippage_pct?: number | null
  /** What was still held after the last bar, valued at the last close (null: nothing). */
  end_mark?: EndMark | null
}

export interface EquityRow {
  date: string
  total_value: number
  cash?: number
  market_value?: number
  net_value: number
  drawdown: number
}

export interface CurvePoint {
  date: string
  value: number
}

export interface SameStockPart {
  ticker?: string
  total_return: number | null
  curve: CurvePoint[]
  basis?: string
  fills?: number
  fees_total?: number | null
  entry?: { date: string; price: number; quantity: number } | null
  reason: string | null
}

export interface IndexPart {
  ticker: string
  name?: string | null
  total_return: number | null
  curve: CurvePoint[]
  source?: string | null
  offline?: boolean
  closed_dates?: string[]
}

export interface RoundTrip {
  symbol?: string
  entry_date: string
  exit_date: string
  quantity: number
  entry_price: number | null
  exit_price: number | null
  pnl: number | null
  return_pct: number | null
  holding_days: number | null
  fills?: number
  exit_reason?: string
  exit_reason_zh?: string | null
  /** The hypothetical sale of what is still held at the end (a valuation, not a sale). */
  end_mark?: boolean
  /** suspended / limit_down / t_plus_one: the end mark could not really have been sold. */
  blocked?: string | null
  blocked_quantity?: number | null
  /** The service's Chinese sentence for `blocked`. */
  blocked_zh?: string | null
}

export interface Fill {
  date: string
  symbol?: string
  direction: string
  quantity: number
  price: number
  commission?: number | null
  fees?: Record<string, number> | null
  pnl?: number | null
  reason?: string | null
  reason_zh?: string | null
  end_mark?: boolean
  blocked?: string | null
  blocked_quantity?: number | null
  blocked_zh?: string | null
}

export interface Unfilled {
  symbol?: string
  direction?: string
  order_type?: string
  signal_date?: string | null
  date?: string | null
  quantity?: number | null
  reason?: string | null
  reason_zh?: string | null
  outcome?: string | null
  [key: string]: unknown
}

export interface Coverage {
  ticker: string
  adjust?: string
  source?: string | null
  requested_start?: string | null
  requested_end?: string | null
  first?: string | null
  last?: string | null
  rows?: number | null
  complete?: boolean | null
  offline?: boolean | null
  fetched_at?: string | null
  sha256?: string | null
  features_sha256?: string | null
  warmup_rows?: number | null
  warnings?: string[]
  /** Days whose adjusted bars come from an earlier download this one did not return (not checked). */
  unverified?: string[]
  /** Days where the bars switch from one download to another. */
  splice_seams?: string[]
  /** Bars saved before that day's close was final, dropped and not downloaded again. */
  dropped_provisional?: string[]
  provisional_last_bar?: boolean
  /** The last trading day up to the requested end whose close has settled (null: unknown). */
  last_session?: string | null
  /** The bars stop before last_session. */
  ends_early?: boolean | null
}

/** The hypothetical sale of what is still held after the last bar (valued at the last close). */
export interface EndMarkPosition {
  symbol: string
  date?: string
  quantity?: number
  price?: number
  pnl?: number
  /** suspended / limit_down / t_plus_one: it could not really have been sold that day. */
  blocked?: string | null
  blocked_quantity?: number | null
  /** The service's Chinese words for `blocked`, when it sends them. */
  blocked_zh?: string | null
  reason_zh?: string | null
}

export interface EndMark {
  date?: string
  pnl?: number
  positions: EndMarkPosition[]
}

export interface Manifest {
  engine_version?: string
  spec?: Record<string, any>
  coverage?: Coverage[]
  fee_schedule_version?: string
  data_sha256?: string
  data_hash_version?: number
  created_at?: string
  demo?: boolean
  rerun_of?: string | null
  resolved?: Record<string, any>
}

export interface RerunInfo {
  of?: string | null
  same_data?: boolean | null
  same_strategy?: boolean | null
  data_sha256_before?: string | null
  data_sha256_after?: string | null
  metrics_identical?: boolean
  changed_metrics?: Record<string, [unknown, unknown]>
}

/** One traded bar after the warm-up, adjusted as the run was (the fills are on the same prices). */
export interface PriceRow {
  date: string
  open: number
  high: number
  low: number
  close: number
}

/** One month: month-end net value against the previous month end (the first month: the capital). */
export interface MonthlyReturn {
  year: number
  month: number
  return: number | null
}

/** result.legacy: a run saved without a RunResult (v3.2.1 or earlier), translated read-only by the service. */
export interface LegacyInfo {
  saved_by: string
  note_zh: string
  /** The parts it does not have (manifest, benchmark, round_trips, unfilled, end_mark, prices, ...). */
  unavailable: { key: string; label_zh: string }[]
  /** The other statistics as saved; v3.2.1 wrote 0 where it had none, so they are not shown. */
  saved_metrics?: Record<string, unknown>
}

export interface RunResult {
  run_id: string
  created_at?: string
  facts: Facts
  headline_zh: string
  caveats: Caveat[]
  metrics: Record<string, unknown> & { undefined?: Record<string, string> }
  metric_notes?: Record<string, string>
  equity: EquityRow[]
  benchmark: { same_stock: SameStockPart | null; index: IndexPart | null }
  round_trips: RoundTrip[]
  fills: Fill[]
  unfilled: Unfilled[]
  warmup?: Record<string, unknown>
  assumptions?: { line_zh?: string; slippage_pct?: number | null; [key: string]: unknown }
  rerun?: RerunInfo | null
  manifest: Manifest
  end_mark?: EndMark | null
  /** A record saved by v3.2.1 or earlier, read as a result: what it lacks (GET /runs/{id} of such a row). */
  legacy?: LegacyInfo | null
  /** Absent in records saved before the field existed. */
  prices?: PriceRow[]
  monthly_returns?: MonthlyReturn[]
}

export interface ReflectionResult {
  title?: string
  analysis?: string
  source?: string
  lessons?: string[]
  improvements?: string[]
  timestamp?: string
}

export interface ReflectionSnapshot {
  status: ReflectionStatus | string
  result: ReflectionResult | null
  message: string | null
}

/** GET /runs/{id}: the run's state; `result` once completed (null with include_result=false). */
export interface RunSnapshot {
  run_id: string
  kind?: string
  status: RunStatus
  stage: string | null
  stage_label: string | null
  stage_detail: string | null
  progress: number | null
  stages: { id: string; label: string }[]
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  error: { status: number; message: string } | null
  saved_to_history?: boolean
  reflection: ReflectionSnapshot
  result: RunResult | null
}

/** One row of GET /history. */
export interface HistoryRow {
  run_id: string
  run_date?: string | null
  strategy_name?: string | null
  symbol?: string | null
  ticker?: string | null
  currency?: string | null
  market?: string | null
  initial_cash?: number | null
  final_value?: number | null
  total_return?: number | null
  annualized_return?: number | null
  max_drawdown?: number | null
  start_date?: string | null
  end_date?: string | null
  /** The first and last bar the run used (start_date / end_date: as requested). */
  first_date?: string | null
  last_date?: string | null
  headline_zh?: string | null
  preset?: string | null
  period_preset?: string | null
  closed_trades?: number | null
  data_sha256?: string | null
  /** null for a record saved by v3.2.1 or earlier (no run manifest: it cannot be rerun). */
  engine_version?: string | null
  rerun_of?: string | null
  starred?: number | boolean | null
}

export interface HistoryList {
  runs: HistoryRow[]
  stats: { total_runs?: number; kept?: boolean; [key: string]: unknown }
}

export interface CachedSymbol {
  symbol: string
  rows: number
  start_date?: string | null
  end_date?: string | null
}

/** One price adjustment of a cached instrument (DataStore.describe): what it holds and where it came from. */
export interface CacheAdjustment {
  /** qfq, hfq, or "" (不复权). */
  adjust: string
  rows?: number | null
  first?: string | null
  last?: string | null
  coverage?: string[][]
  sources?: string[]
  fetched_at?: string | null
  /** true: inside one recorded download; false: not; null: nothing recorded (unknown). */
  complete?: boolean | null
}

/** One adjustment of a POST /data/download answer: what the cache holds for it after the update. */
export interface DownloadAdjustment {
  adjust: string
  label?: string | null
  ok: boolean
  rows_saved?: number | null
  rows?: number | null
  first?: string | null
  last?: string | null
  /** The last trading day up to end_date whose close has settled (null: unknown). */
  last_session?: string | null
  /** last >= last_session (null: unknown). */
  up_to_date?: boolean | null
  /** A failed download's fixed Chinese reason (ok false). */
  error?: string | null
}

/** POST /data/download; `adjustments` from a service that refreshes every adjustment (all_adjustments). */
export interface DownloadAnswer {
  symbol: string
  rows_saved: number
  start_date?: string
  end_date?: string
  adjustments?: DownloadAdjustment[]
}

/** One instrument of GET /data/cache `symbol_details`. */
export interface CacheDetail {
  symbol: string
  start_date?: string | null
  end_date?: string | null
  /** The source of its latest download or import; null when the cache predates source records. */
  source?: string | null
  fetched_at?: string | null
  complete?: boolean | null
  adjustments?: CacheAdjustment[]
}

export interface CacheStats {
  kept?: boolean
  symbol_details?: CacheDetail[]
  [key: string]: unknown
}

/** One strategy of GET /strategies (built-in examples and, when enabled, custom files). */
export interface StrategyInfo {
  id: string
  name: string
  class: string
  parameters: Record<string, unknown>
  description: string
  builtin: boolean
  file: string
}

export interface StrategySource {
  id: string
  name: string
  class_name: string
  source_code: string
  file_path: string
  description: string
  builtin: boolean
}

/** A refused request: the service's Chinese `detail` and, for RunSpec checks, `errors` per field. */
export interface ApiRefusal {
  status: number | undefined
  message: string
  errors: { field: string; message: string }[]
}
