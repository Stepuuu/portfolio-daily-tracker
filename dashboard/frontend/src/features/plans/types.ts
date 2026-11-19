// The `/api/plans` payloads. The authoritative contract is the docstring at the top of
// dashboard/backend/api/plans.py and dashboard/core/plans.py: numbers are decimal strings, null means
// not set (or, in `market`, unknown with a `*_reason`), times are ISO-8601 UTC, messages are Chinese.

export type PlanStatus = 'draft' | 'active' | 'closed' | 'cancelled'
export type LinkRelation = 'entry' | 'add' | 'reduce' | 'exit' | 'none'

export interface MaxLoss {
  amount: string | null
  currency: string
  amount_cny: string | null
  fx_rate: string | null
  fx_status: string | null
  /** Where the rate of a converted amount comes from, and its time (ISO-8601); null without a conversion. */
  fx_source?: string | null
  fx_quote_time?: string | null
  /** The CNY amount rests on a rate that is not verified (stale, time unknown): shown as 估算 with the reason. */
  estimated?: boolean
  estimated_reason?: string | null
  entry_basis: 'entry_high' | 'entry_low' | null
  /** A stop at or above the entry (an active plan's stop moved up): the amounts are 0 and this is the gain it locks in. */
  locked_gain?: string | null
  /** A Chinese sentence showing the arithmetic; shown to the user as it is. */
  formula: string | null
  reason: string | null
}

export interface Plan {
  id: number
  ticker: string
  account: string | null
  direction: 'buy'
  thesis: string
  invalidation: string
  entry_low: string | null
  entry_high: string | null
  stop_price: string | null
  target_price: string | null
  planned_weight_pct: string | null
  planned_quantity: string | null
  review_on: string | null
  status: PlanStatus
  status_label: string
  close_reason: string | null
  source: string
  created_at: string
  updated_at: string
  activated_at: string | null
  ended_at: string | null
  currency: string
  max_loss: MaxLoss
  max_loss_cny: string | null
  /** Other active plans for the same listing and an overlapping scope (only an older store can hold them). */
  conflicts?: number[]
  /** Changes with every write; an edit sends it back as `if_version` and a stale one is refused (409). */
  version?: string
}

export interface PlanQuote {
  status: string
  reason: string | null
  source: string | null
  quote_time: string | null
  [key: string]: unknown
}

/** The plan against the current price and position (`core.plans.market_view`). */
export interface PlanMarket {
  /** The instrument's name: the ledger's for a holding, else the quote's; null when unknown. */
  name?: string | null
  price: string | null
  prev_close: string | null
  change_pct: string | null
  quote: PlanQuote | null
  price_reason: string | null
  /** The price is not verified (a stale quote, a New York close before it settles), as Today marks the row. */
  price_estimated?: boolean
  price_estimated_reason?: string | null
  position_quantity: string | null
  planned_quantity: string | null
  quantity_vs_plan: string | null
  weight_pct: string | null
  weight_estimated: boolean
  weight_reason: string | null
  planned_weight_pct: string | null
  weight_vs_plan_pct: string | null
  over_planned_weight: boolean | null
  stop_price: string | null
  distance_to_stop: string | null
  distance_to_stop_pct: string | null
  stop_reached: boolean | null
  target_price: string | null
  distance_to_target: string | null
  distance_to_target_pct: string | null
  target_reached: boolean | null
  as_of: string | null
}

export interface Deviation {
  code: string
  level: 'warning' | 'info'
  message: string
  data: Record<string, unknown>
}

export interface PlanLink {
  event_id: string
  event_date: string
  kind: 'buy' | 'sell'
  ticker: string
  account: string | null
  quantity: string | null
  price: string | null
  plan_id: number | null
  relation: LinkRelation
  deviations: Deviation[]
  proposal_id: string | null
  created_at: string
  /** True when the ledger has reversed the event since; null when the ledger could not be read. */
  reversed: boolean | null
}

export interface PlanAlertRule {
  id: number
  ticker: string
  account: string | null
  kind: string
  threshold: string
  enabled: boolean
  cooldown_seconds: number
  note: string | null
  source: string
}

/** One plan as the list returns it (no links or alerts; `market` only with `?market=1`, null for an ended plan). */
export interface PlanItem extends Plan {
  market?: PlanMarket | null
}

export interface PlanDetail extends Plan {
  /** null when they could not be read (`links_error` says so): unavailable, not "none". */
  links: PlanLink[] | null
  links_error?: string | null
  /** null when they could not be read (`alerts_error`). */
  alerts: PlanAlertRule[] | null
  alerts_error?: string | null
  alert_notes: string[]
  market: PlanMarket
}

export interface PlanList {
  items: PlanItem[]
  as_of: string
}

/** The body of POST /api/plans and PATCH /api/plans/{id}: decimal strings, null for empty. */
export interface PlanBody {
  ticker: string
  account: string | null
  direction: 'buy'
  thesis: string
  invalidation: string
  entry_low: string | null
  entry_high: string | null
  stop_price: string | null
  target_price: string | null
  planned_weight_pct: string | null
  planned_quantity: string | null
  review_on: string | null
}

/** A PATCH body: only the changed fields, and the `version` the edit was made against. */
export type PlanEdit = Partial<PlanBody> & { if_version?: string }
