// `GET /api/briefs` (backend/api/briefs.py; the brief and fact format is documented in
// dashboard/core/routines/briefs.py). Every number is computed by the backend from the ledger and
// labelled quotes; `value: null` means unknown and `text` says why. The page never computes a figure.

export type BriefKind = 'premarket' | 'postmarket'

/** `code`: a quote status (live, delayed, closed, stale, prev_close, time_unknown, unavailable), or
 * 'estimated' (a total built on unverified inputs) / 'provisional' (a total with an intraday price) /
 * 'not_queried' (a source the brief did not ask). `provisional`: computed from a price of a session still
 * trading, not its close; it will change. */
export interface FactStatus {
  code: string
  reason: string | null
  estimated: boolean
  provisional?: boolean
}

/** The rate an amount was converted to CNY at: where and when it was quoted. */
export interface FactFx {
  currency: string
  rate: string | null
  source: string | null
  as_of: string | null
  status: string
  reason: string | null
  estimated: boolean
}

export interface FactRef {
  ticker?: string
  account?: string
  plan_id?: number
  alert_rule_id?: number
  alert_event_id?: number
  ledger_event_id?: string
  journal_id?: string
  proposal_ids?: string[]
}

/** Third-party text (headlines) arrives only inside `untrusted_text`. */
export interface UntrustedText {
  untrusted_text: string
}

export interface FactItem {
  text?: string
  ref?: FactRef
  level?: 'warning' | 'info' | null
  published?: string | null
  title?: UntrustedText
  kind?: UntrustedText
}

export type FactUnit = 'CNY' | 'HKD' | 'USD' | 'pct' | 'points' | 'count' | 'date' | 'state'

export interface Fact {
  label: string
  value: string | number | null
  unit: FactUnit | null
  text: string
  source: string | null
  as_of: string | null
  status: FactStatus | null
  ref: FactRef | null
  metric: string | null
  figures?: Record<string, string>
  signed?: boolean
  items?: FactItem[]
  level?: 'warning' | 'info'
  fx?: FactFx
}

export interface BriefSection {
  id: string
  title: string
  facts: Fact[]
  note?: string
  status?: { code: string; reason: string }
}

export interface Commentary {
  status: 'ok' | 'failed' | 'unavailable'
  text: string | null
  model: string | null
  generated_at: string
  error?: string
  ungrounded?: string[] | null
  grounding_note?: string | null
}

export interface BriefQuality {
  level: 'complete' | 'estimated' | 'incomplete'
  estimated: number
  provisional?: number
  unavailable: number
  text: string
}

export interface Brief {
  kind: BriefKind
  title: string
  trading_date: string
  generated_at: string
  sections: BriefSection[]
  quality: BriefQuality
  notices: { kind: string; text: string; status?: string }[]
  commentary: Commentary | null
  version: number
}

export interface RunRow {
  id: number
  kind: string
  trading_date: string
  status: 'pending' | 'running' | 'ok' | 'failed' | 'skipped' | 'unavailable'
  status_text: string
  attempts: number
  summary: string
  error_category: string | null
  next_attempt_at: string | null
  started_at: string | null
  finished_at: string | null
}

export type BriefState =
  | 'ok' | 'failed' | 'running' | 'pending' | 'skipped' | 'unavailable' | 'scheduled' | 'due' | 'missing'
  | 'not_trading_day' | 'disabled' | 'demo'

export interface BriefItem {
  kind: BriefKind
  title: string
  trading_date: string
  enabled: boolean
  time: string
  due_at: string | null
  state: BriefState
  state_text: string
  reason: string | null
  run: RunRow | null
  brief: Brief | null
  can_run: boolean
  run_refusal: string | null
}

export interface BriefDay {
  date: string
  current_trading_date: string
  is_trading_day: boolean
  previous_date: string
  next_date: string | null
  latest_date: string | null
  /** The mainland calendar of the day, the days around it and the current one: 'weekday_only' when the
   * holiday table does not know the year (the day may be a holiday). */
  calendar?: Record<string, 'calendar' | 'weekday_only'>
  now: string
  mode: 'normal' | 'demo' | 'lab_only'
  scheduler_alive: boolean
  items: Record<BriefKind, BriefItem>
}
