// Payloads of the setup wizard's API (dashboard/backend/api/setup.py; checklist in core/setup_checks.py, the
// model step in core/llm_setup.py, the legacy migration in core/legacy_migration.py). Amounts are decimal
// strings exactly as the backend read them. No type here carries an API key: the backend never sends one.
import type { Mode } from '../../lib/navigation.ts'

export type ItemState = 'done' | 'todo' | 'optional' | 'unavailable'
export type ItemId = 'opening' | 'legacy' | 'fees' | 'quotes' | 'alerts' | 'routines' | 'notifications' | 'llm'

/** A page of the app (`href`) or a line to put in the environment / `.env` (`command`). */
export interface SetupAction {
  label: string
  href?: string
  command?: string
}

export interface SetupItem {
  id: ItemId
  label: string
  state: ItemState
  detail: string
  action: SetupAction | null
}

export interface SetupFlags {
  /** null when the flags could not be read. */
  setup_complete: boolean | null
  completed_at: string | null
  dismissed: boolean | null
  dismissed_at: string | null
}

export interface SetupStatus extends SetupFlags {
  mode: Mode
  items: SetupItem[]
  /** The ledger has a confirmed opening balance; null when the ledger could not be read. */
  ledger_ready: boolean | null
}

// -- the model -------------------------------------------------------------------------------

export interface PresetModel {
  id: string
  name: string
  supports_vision: boolean
}

export interface LlmPreset {
  id: string
  label: string
  group: string
  name: string
  description: string
  provider_type: 'openai' | 'claude'
  base_url: string
  base_url_editable: boolean
  models: PresetModel[]
  /** For a fixed official host: the environment variable its key may come from, and whether the server has it. */
  env_key?: { name: string; available: boolean } | null
  /**
   * Where saving this preset needs no typed key, exactly as the API decides (core/llm_setup.py `_keyless`):
   * the stored key's `scheme://host:port`, the hosts whose key the server environment provides, loopback hosts.
   * `saved_key`: a key is stored for this preset's group (elsewhere the save then asks for one for the new address).
   */
  keyless?: { origins: string[]; env: { host: string; name: string }[]; loopback: boolean; saved_key: boolean }
}

export interface LlmCurrent {
  /** The preset the saved API group belongs to; null for a group configured by hand in config.json. */
  preset: string | null
  group: string
  group_name: string
  /** Without user info, query or fragment. */
  base_url: string | null
  model: string
  supports_vision: boolean | null
  key: { configured: boolean; source: 'config' | 'env' | null; env: string | null }
  /** A loopback address (a local server may need no key). */
  local: boolean
}

export interface LlmSettings {
  presets: LlmPreset[]
  current: LlmCurrent | null
}

export interface LlmSaveBody {
  preset: string
  model: string
  base_url?: string
  api_key?: string
  supports_vision?: boolean
}

export interface LlmTestResult {
  ok: boolean
  /** ok | auth | forbidden | not_found | rejected | rate_limited | upstream | http | timeout | connection | parse | provider | not_configured | no_key | bad_url */
  code: string
  /** One of the backend's fixed texts. */
  message: string
  model?: string
  latency_ms?: number
  status?: number | null
}

// -- the legacy migration ----------------------------------------------------------------------

export interface DraftValue {
  key: string
  /** Read from the file; null when it could not be (then `problem` says why). */
  value: string | null
  problem: string | null
  note: string | null
  currency?: string
  choices?: string[]
}

export interface DraftPosition {
  key: string
  index: number
  name: string | null
  ticker: DraftValue
  quantity: DraftValue
  cost_price: DraftValue
}

export interface DraftAccount {
  key: string
  account: string
  /** portfolio.json: the one account's name is chosen here. */
  account_editable: boolean
  /** The ledger already has this account: it is not migrated again. */
  exists: boolean
  cash: DraftValue[]
  contributed_cny: DraftValue
  fund_cny: DraftValue
  positions: DraftPosition[]
}

export type NeedKind = 'ticker' | 'quantity' | 'cost' | 'cash' | 'contributed' | 'fund'

export interface NeedsInput {
  key: string
  kind: NeedKind
  account: string
  label: string
  problem: string
  choices: string[]
  /** The position row (`a0.p1`) the value belongs to; such a row may be left out instead. */
  row: string | null
}

export interface LegacySource {
  source: 'holdings' | 'portfolio'
  file: string
  date: string | null
  count: number
  /** Something in it is not in the ledger yet (absent from an older backend: then offered). */
  pending?: boolean
}

export interface LegacyDraft {
  source: 'holdings' | 'portfolio'
  file: string
  file_date: string | null
  /** The opening date the draft was made for. */
  date: string
  /** Fingerprint of the file; the proposal is refused when the file changed since. */
  token: string
  accounts: DraftAccount[]
  needs_input: NeedsInput[]
  skipped: { account: string; name: string | null; ticker: string | null; reason: string }[]
  sources: LegacySource[]
}

export interface LegacyProposalBody {
  source: 'holdings' | 'portfolio'
  date: string
  token: string
  fills: Record<string, string>
  skip: string[]
  account_name?: string
}
