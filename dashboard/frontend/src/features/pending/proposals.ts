// What is waiting for the owner's confirmation: ledger proposals (`GET /api/ledger` → `pending`) and the
// generic ones (`GET /api/proposals`: alert rules, plans, lessons, watchlist changes). Pure functions for
// the 待确认 count, the tray's groups and the one-line descriptions.
import { fmtMoney, fmtQty } from '../../lib/format.ts'
import { LEDGER_KIND_LABEL, kindLabel } from '../ledger/kinds.ts'

export interface LedgerEvent {
  id?: string
  kind: string
  date: string
  account?: string
  ticker?: string
  name?: string
  quantity?: string
  price?: string
  currency?: string
  amount?: string
  note?: string
  positions?: unknown[]
}

export interface LedgerProposal {
  id: string
  /** The ledger revision the preview was made against; confirmable only while it is current. */
  revision: number
  created_at?: string
  events: LedgerEvent[]
  duplicates?: number
  warnings?: string[]
}

export interface LedgerPending {
  revision: number
  pending: LedgerProposal[]
}

export interface GenericProposal {
  id: string
  kind: string
  status: string
  source: string
  summary: string
  payload: Record<string, unknown>
  created_at: string
  expires_at: string
  /** When it was confirmed, rejected or marked expired; null while pending. */
  resolved_at?: string | null
  error?: string | null
}

// Kind names come from the one shared map (features/ledger/kinds.ts).
export { LEDGER_KIND_LABEL }

/** Ledger proposals that can still be confirmed (made against the current revision), and the outdated ones. */
export function splitLedgerPending(ledger: LedgerPending | undefined): { current: LedgerProposal[]; outdated: LedgerProposal[] } {
  const current: LedgerProposal[] = []
  const outdated: LedgerProposal[] = []
  for (const proposal of ledger?.pending ?? []) {
    if (!proposal.events?.length) continue
    ;(proposal.revision === ledger!.revision ? current : outdated).push(proposal)
  }
  return { current, outdated }
}

/** "11-17 买入 示例宽基 100 股", "分红 青山药业 ¥1,936.00", "期初余额 长期账户". */
export function describeLedgerEvent(event: LedgerEvent): string {
  const kind = kindLabel(event.kind)
  const date = typeof event.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(event.date) ? event.date.slice(5) : ''
  const subject = event.name || event.ticker || ''
  // Every digit, as the review shows it: a $0.004 dividend is never $0.00.
  const money = event.amount ? fmtMoney(event.amount, event.currency ?? 'CNY', { exact: true }) : ''
  let text: string
  switch (event.kind) {
    case 'buy':
    case 'sell':
      text = [kind, subject, event.quantity ? `${fmtQty(event.quantity)} 股` : ''].filter(Boolean).join(' ')
      break
    case 'opening':
      text = [kind, event.account].filter(Boolean).join(' ')
      break
    case 'reverse':
      text = '冲销一笔记录'
      break
    default:
      text = [kind, subject, money].filter(Boolean).join(' ')
  }
  return [date, text].filter(Boolean).join(' ')
}

/** One line for a proposal: its first event, plus "等 3 笔" when there are more. */
export function describeLedgerProposal(proposal: LedgerProposal): string {
  const [first] = proposal.events
  if (!first) return '空方案'
  const rest = proposal.events.length - 1
  if (first.kind === 'opening') {
    const accounts = new Set(proposal.events.filter((event) => event.kind === 'opening').map((event) => event.account))
    if (accounts.size > 1 && rest + 1 === accounts.size) return `${first.date.slice(5)} 期初余额 · ${accounts.size} 个账户`
  }
  return rest > 0 ? `${describeLedgerEvent(first)} 等 ${proposal.events.length} 笔` : describeLedgerEvent(first)
}

export type PendingGroup = 'ledger' | 'alert' | 'plan' | 'lesson' | 'watchlist' | 'other'

export const GROUP_LABEL: Record<PendingGroup, string> = {
  ledger: '账本', alert: '提醒', plan: '计划', lesson: '教训', watchlist: '自选', other: '其他',
}

const KIND_GROUP: [RegExp, PendingGroup][] = [
  [/^alert/, 'alert'], [/plan/, 'plan'], [/lesson/, 'lesson'], [/watch/, 'watchlist'],
]

/** Which tray group a generic proposal kind belongs to. */
export function proposalGroup(kind: string): PendingGroup {
  return KIND_GROUP.find(([pattern]) => pattern.test(kind))?.[1] ?? 'other'
}

/** Generic proposals by group, in the tray's order (提醒, 计划, 教训, 自选, 其他). */
export function groupProposals(items: GenericProposal[]): [PendingGroup, GenericProposal[]][] {
  const order: PendingGroup[] = ['alert', 'plan', 'lesson', 'watchlist', 'other']
  const groups = new Map<PendingGroup, GenericProposal[]>()
  for (const item of items) {
    const group = proposalGroup(item.kind)
    groups.set(group, [...(groups.get(group) ?? []), item])
  }
  return order.filter((group) => groups.has(group)).map((group) => [group, groups.get(group)!])
}

/** The 待确认 count: confirmable ledger proposals plus pending generic ones. */
export function pendingCount(ledger: LedgerPending | undefined, generic: GenericProposal[] | undefined): number {
  return splitLedgerPending(ledger).current.length + (generic?.filter((item) => item.status === 'pending').length ?? 0)
}

/** One of the tray's two lists as react-query reports it, with how many items it shows. */
export interface ListRead {
  status: 'pending' | 'error' | 'success'
  count: number
}

export type PendingList = 'ledger' | 'generic'

/** How a list that could not be read is named in its 不可用 line. */
export const LIST_LABEL: Record<PendingList, string> = { ledger: '账本方案', generic: '提醒、计划等方案' }

/**
 * What the tray may say. "Nothing waiting" only when both lists answered with nothing; while one is still
 * loading it says so; a list that failed is 不可用 (never counted as empty).
 */
export function trayView(lists: Record<PendingList, ListRead>): { empty: boolean; loading: boolean; unavailable: PendingList[] } {
  const order: PendingList[] = ['ledger', 'generic']
  return {
    empty: order.every((list) => lists[list].status === 'success' && lists[list].count === 0),
    loading: order.some((list) => lists[list].status === 'pending'),
    unavailable: order.filter((list) => lists[list].status === 'error'),
  }
}

/** "助手", "例行任务", "手动": where a generic proposal came from (`source` is `agent:chat`, `routine:…`). */
export function sourceLabel(source: string): string {
  if (source.startsWith('agent')) return '助手'
  if (source.startsWith('routine')) return '例行任务'
  if (source.startsWith('import')) return '导入'
  return source
}

/** Payload fields as label / value pairs for the in-place review, in a stable order. */
export function payloadFields(payload: Record<string, unknown>): [string, string][] {
  return Object.entries(payload ?? {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]): [string, string] => [key, typeof value === 'object' ? JSON.stringify(value) : String(value)])
}
