// Payloads of the ledger and accounts APIs (dashboard/backend/api/ledger.py, accounts.py; accounting in
// dashboard/core/ledger.py). Money and quantities are decimal strings exactly as the ledger stores them.

export interface LedgerPosition {
  ticker: string
  name: string
  quantity: string
  cost_price: string
  currency: string
}

export interface LedgerAccount {
  cash_balances: Record<string, string>
  positions: Record<string, LedgerPosition>
  contributed_cny: string
  fund_cny: string
  realized: Record<string, string>
}

export interface LedgerState {
  accounts: Record<string, LedgerAccount>
  /** Date of the latest event, or null for an empty ledger. */
  date: string | null
}

/** A stored or proposed event (core/ledger.py `normalize`). */
export interface LedgerEvent {
  id: string
  kind: string
  date: string
  source?: string
  external_id?: string
  account?: string
  target_id?: string
  ticker?: string
  name?: string
  note?: string
  quantity?: string
  price?: string
  fee?: string
  currency?: string
  amount?: string
  fx_rate?: string
  to_account?: string
  to_currency?: string
  received_amount?: string
  ratio?: string
  allow_margin?: boolean
  cash_balances?: Record<string, string>
  positions?: { ticker: string; name?: string; quantity: string; cost_price: string }[]
  contributed_cny?: string
  fund_cny?: string
  /** Order of confirmation, on stored events. */
  _sequence?: number
}

export interface Receipt {
  proposal_id: string
  revision: number
  applied: number
  duplicates: number
  state?: LedgerState
}

export type PlanRelation = 'entry' | 'add' | 'reduce' | 'exit' | 'none'

export interface PlanDeviation {
  code: string
  level: 'warning' | 'info'
  message: string
  data?: Record<string, unknown>
}

/** One buy or sell of a proposal compared with the trade plans (core/plans.py `check_events`). */
export interface PlanCheckItem {
  event_ref: string | null
  index: number
  kind: string
  date: string
  ticker: string
  account: string | null
  quantity: string | null
  price: string | null
  plan_id: number | null
  relation: PlanRelation
  deviations: PlanDeviation[]
}

export interface Proposal {
  id: string
  /** The ledger revision it was previewed against; confirmable only while that is current. */
  revision: number
  created_at?: string
  events: LedgerEvent[]
  before: LedgerState
  after: LedgerState
  duplicates: number
  warnings: string[]
  receipt?: Receipt | null
  /** A manual preview's end of life (ISO); after it, it cannot be confirmed (`expired`). */
  expires_at?: string
  expired?: boolean
  /** `feishu-card`: the Feishu card made it and only the card confirms it (the web may discard it). */
  confirm_on?: string
  /** Present once any trade plan exists. */
  plan_check?: PlanCheckItem[]
  /** The plan check failed; the proposal is fine. */
  plan_check_error?: string
}

/**
 * `GET /api/ledger/as-of?date=`: the confirmed ledger at the end of that day, as an entry dated that day is
 * checked against it (core.ledger.replay_for_entry: a reversal dated later already counts).
 */
export interface LedgerAsOf extends LedgerState {
  revision: number
  as_of: string
  /** No longer sent (it belonged to the dated view, core.ledger.replay_as_of). */
  as_of_corrected_accounts?: string[]
}

/** `GET /api/ledger`. */
export interface LedgerSnapshot extends LedgerState {
  revision: number
  pending: Proposal[]
}

export interface JournalEntry {
  id: string
  ticker: string
  thesis: string
  invalidation: string
  review_on: string
  created_at: string
}

/** A side of an HK or US fee model, in the trade currency. */
export interface FeeSide {
  rate: string
  min: string
  fixed: string
}

export interface FeeProfile {
  account?: string
  broker: string
  a_share: { commission_rate: string; commission_min: string; waive_minimum: boolean }
  hk: { buy: FeeSide | null; sell: FeeSide | null } | null
  us: { buy: FeeSide | null; sell: FeeSide | null } | null
  /** No saved profile: the preset applies. */
  is_default?: boolean
  updated_at?: string
}

/** `GET /api/accounts/fee-estimate`. `total` null with a `reason` when no honest estimate exists. */
export interface FeeEstimate {
  account: string
  ticker: string
  side: 'buy' | 'sell'
  quantity: string
  price: string
  date: string
  currency: string
  profile_is_default: boolean
  total: string | null
  parts: { label: string; amount: string }[]
  /** "按账户费率估算" or "按默认费率估算". */
  basis: string
  reason: string | null
}
