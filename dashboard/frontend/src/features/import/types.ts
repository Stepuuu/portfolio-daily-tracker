// Payloads of the broker-file importer (dashboard/backend/api/imports.py, whose module docstring is the
// contract; row statuses and needs in dashboard/core/importers/transactions.py). Money and quantities are
// decimal strings exactly as the backend computed them. Statuses, reasons, needs and labels are open sets:
// the page renders what the backend sends and never relies on a closed list of them.
import type { LedgerEvent, Proposal } from '../ledger/types.ts'

export type Statement = 'transactions' | 'holdings' | 'template'

export interface SheetOption {
  name: string
  hidden: boolean
  data_rows: number
}

export interface ImportFormat {
  type: string
  /** "GBK 制表符分隔文本（扩展名是 .xls）". */
  label: string
  encoding: string | null
  delimiter: string | null
  /** The sheet (or web-page table) being imported. */
  sheet: string | null
  sheets: string[]
  /** Sheets that hold data. */
  sheet_options: SheetOption[]
  /** The person chose the sheet (PATCH sheet). */
  sheet_chosen: boolean
  header_row: number
  extension: string
  title_lines: string[]
}

export interface ImportPreset {
  id: string
  label: string
  verified: boolean
  /** "预设未经你的券商文件验证", or null. */
  note: string | null
}

export interface ImportColumn {
  index: number
  header: string
  field: string | null
  field_label: string | null
}

/** Field → column index; `fees` is a list of columns. */
export type ImportMapping = Record<string, number | number[]>

export interface ImportField {
  field: string
  label: string
  required: boolean
}

export interface ColumnConflict {
  field: string
  label: string
  columns: number[]
  /** The column in use; null for a fee column listed twice. */
  chosen: number | null
  /** Rows where the columns differ (at most 20 listed). */
  rows: number[]
  resolved: boolean
}

export interface AccountValue {
  value: string
  rows: number
  accepted: boolean
}

export interface AccountColumn {
  dimension: string
  label: string
  values: AccountValue[]
}

export interface RememberedIdentity {
  dimension: string
  label: string
  value: string
  state: string
}

export interface ImportAccounts {
  mixed: boolean
  /** single | dominant | chosen | saved; "dominant" is a preview only and a blocker until chosen. */
  source: string
  columns: AccountColumn[]
  accepted: string[]
  accept_rows_without_account: boolean
  rows_without_account: number
  /** Rows this decision makes 需处理 (needs "account"): the whole file's count. */
  rows_kept_out: number
  summary: string | null
  /** What earlier imports into this ledger account confirmed or excluded (newer backends). */
  remembered?: RememberedIdentity[]
}

/** Per status, plus `total`, `included_rows` and `included_events`. */
export type ImportCounts = Record<string, number>

export interface SkippedGroup {
  status: string
  label: string
  count: number
  rows: number[]
  amount?: string
}

export interface ReconCash {
  currency: string
  file: string
  ledger: string
  difference: string
  status: string
  label: string
  row: number
}

export interface ReconPosition {
  ticker: string | null
  code: string
  name: string
  file: string | null
  ledger: string | null
  difference: string | null
  status: string
  label: string
  row: number
}

export type Reconciliation =
  | {
    available: true
    as_of: string | null
    cash: ReconCash[]
    positions: ReconPosition[]
    causes: string[]
    summary: string
    consistent: boolean
  }
  | { available: false; reason: string }

export interface HoldingsReconRow {
  ticker: string | null
  name: string
  row: number
  file_quantity: string | null
  ledger_quantity: string | null
  difference: string | null
  status: string
  label: string
  file_cost: string | null
  ledger_cost: string | null
  cost_note?: string | null
  entry: { kind: string; account: string; ticker: string; quantity: string; price: string | null; source: string } | null
}

export interface OpeningValues {
  date?: string
  cash_balances?: Record<string, string>
  contributed_cny?: string
  fund_cny?: string
}

export interface HoldingsView {
  mode: 'opening' | 'reconcile' | string
  summary: { label: string; value: string }[]
  cost_note: string
  opening: OpeningValues | null
  positions: { ticker: string; name?: string; quantity: string; cost_price: string }[]
  suggested_contributed_cny?: { value: string | null; label: string }
  reconciliation?: HoldingsReconRow[]
}

export interface BatchRecord {
  number: number
  proposal_id: string
  events: number
  rows: number[]
  created_at: string
  state: string
}

export interface ImportBatches {
  confirmed: number
  /** The unconfirmed proposal; stale when the ledger or the session changed since it was prepared. */
  pending: (BatchRecord & { stale: boolean }) | null
  remaining_events: number
  remaining_batches: number
  total: number
  next: number | null
  limit: number
  history: BatchRecord[]
}

export interface ImportRow {
  row: number
  cells: string[]
  order: number
  status: string
  status_label: string
  include: boolean
  can_include: boolean
  category: string
  category_label: string
  action: string
  date: string | null
  time: string | null
  code: string
  name: string
  ticker: string | null
  currency: string | null
  quantity: string | null
  price: string | null
  turnover: string | null
  cash_change: string | null
  fee: string | null
  /** columns | derived (|发生金额| − 成交金额) | edited; null without a fee. */
  fee_source: string | null
  /** columns | derived (成交金额 ÷ 数量: the file's price was rounded or missing) | edited; null without a trade price. */
  price_source?: string | null
  cash_balance: string | null
  share_balance: string | null
  external_id: string | null
  /** Ledger events exactly as they will be proposed. */
  events: LedgerEvent[]
  /** Why the row has its status. */
  messages: string[]
  /** What was inferred. */
  notes: string[]
  /** What resolves it: a row edit field, "mapping" or "account" (open set). */
  needs: string[]
  choices: Record<string, string[]>
  account_identity: { dimension: string; label: string; value: string }[]
  edits: Record<string, unknown>
  /** The ledger event this row may repeat, as booked, with the listing's name, where it came from and, for a split, the shares it added. */
  duplicate_of: (Partial<LedgerEvent> & { source_label?: string; added_quantity?: string }) | null
  existing: Partial<LedgerEvent> | null
  merged_into: number | null
  pair_row: number | null
}

export interface ImportSession {
  id: string
  account: string
  account_exists: boolean
  created_at: string
  updated_at: string
  file: { name: string; size: number; sha256: string }
  format: ImportFormat
  statement: Statement
  statement_label: string
  preset: ImportPreset
  columns: ImportColumn[]
  mapping: ImportMapping
  mapping_source: string | null
  fields: ImportField[]
  column_conflicts: ColumnConflict[]
  accounts: ImportAccounts | null
  short_code_market: string | null
  date_range: { from: string; to: string } | null
  counts: ImportCounts
  status_labels: Record<string, string>
  skipped: SkippedGroup[]
  reconciliation: Reconciliation | null
  holdings: HoldingsView | null
  batches: ImportBatches
  blockers: string[]
  warnings: string[]
  ledger_revision: number
  /** Absent in the propose response's `import`. */
  rows?: ImportRow[]
  rows_total?: number
  rows_offset?: number
}

export interface ImportBatch {
  number: number
  total: number
  label: string
  events: number
  rows: number[]
  reused: boolean
}

export interface ProposeResult {
  proposal: Proposal
  batch: ImportBatch
  import: ImportSession
}

/** One item of PATCH `rows`. Numbers are decimal strings. */
export interface RowEdit {
  row: number
  include?: boolean
  ticker?: string | null
  kind?: string | null
  quantity?: string | null
  price?: string | null
  fee?: string | null
  amount?: string | null
  currency?: string | null
  fx_rate?: string | null
  date?: string | null
  allow_margin?: boolean | null
  reset?: boolean
}

export interface ImportPatch {
  sheet?: string
  statement?: Statement
  mapping?: Record<string, number | number[] | null>
  account_filter?: string[] | null
  short_code_market?: string | null
  rows?: RowEdit[]
  opening?: OpeningValues
}

/** Which rows a page of the table shows. */
export interface RowsView {
  /** A row status, or null for every row. */
  status: string | null
  offset: number
  limit: number
}
