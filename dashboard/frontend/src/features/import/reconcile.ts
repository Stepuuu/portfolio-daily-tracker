// 对账 without React: each cash and position line of the backend's reconciliation as 一致, 不一致 (both
// figures and the difference) or 未核对 (with the reason), and whether a mismatch must be acknowledged before a
// batch is proposed. A status the page does not know counts as a mismatch: it is shown and must be
// acknowledged, never taken for 一致. Pure TypeScript.
import { fmtMoney, fmtQty } from '../../lib/format.ts'
import type { Reconciliation } from './types.ts'

export type ReconState = 'ok' | 'mismatch' | 'unchecked'

export interface ReconLine {
  key: string
  /** "CNY 现金", "贵州茅台 SHA:600519". */
  label: string
  state: ReconState
  /** 一致 / 不一致 / 未核对. */
  stateText: string
  /** After the import, by the ledger. */
  ours: string | null
  /** The broker's own balance in the file. */
  theirs: string | null
  /** The backend's words for a difference or why it was not checked ("差 CNY 12.00", "代码未确定，未核对"). */
  detail: string | null
  row: number
}

const STATE_TEXT: Record<ReconState, string> = { ok: '一致', mismatch: '不一致', unchecked: '未核对' }

function stateOf(status: string): ReconState {
  if (status === '一致') return 'ok'
  if (status === '未核对') return 'unchecked'
  return 'mismatch'
}

const shares = (value: string | null) => (value === null || value === undefined ? null : `${fmtQty(value)} 股`)

export function reconLines(recon: Reconciliation | null | undefined): ReconLine[] {
  if (!recon || !recon.available) return []
  const lines: ReconLine[] = []
  for (const item of recon.cash ?? []) {
    const state = stateOf(item.status)
    lines.push({
      key: `cash:${item.currency}`, label: `${item.currency} 现金`, state, stateText: STATE_TEXT[state],
      ours: fmtMoney(item.ledger, item.currency), theirs: fmtMoney(item.file, item.currency),
      detail: state === 'ok' ? null : item.label, row: item.row,
    })
  }
  for (const item of recon.positions ?? []) {
    const state = stateOf(item.status)
    const code = item.ticker ?? item.code
    lines.push({
      key: `position:${code}`, label: item.name && item.name !== code ? `${item.name} ${code}` : code, state, stateText: STATE_TEXT[state],
      ours: shares(item.ledger), theirs: shares(item.file), detail: state === 'ok' ? null : item.label, row: item.row,
    })
  }
  return lines
}

/** A mismatch must be acknowledged before proposing. */
export function reconNeedsAck(recon: Reconciliation | null | undefined): boolean {
  return reconLines(recon).some((line) => line.state === 'mismatch')
}

/** Identifies the mismatches acknowledged: a different set of differences needs a new acknowledgement. */
export function reconAckKey(recon: Reconciliation | null | undefined): string {
  if (!recon || !recon.available) return ''
  const cash = (recon.cash ?? []).filter((item) => stateOf(item.status) === 'mismatch').map((item) => `${item.currency}:${item.file}:${item.ledger}`)
  const positions = (recon.positions ?? []).filter((item) => stateOf(item.status) === 'mismatch')
    .map((item) => `${item.ticker ?? item.code}:${item.file}:${item.ledger}`)
  return [...cash, ...positions].join('|')
}

/** "一致 3 · 不一致 1 · 未核对 1". */
export function reconTally(lines: ReconLine[]): string {
  const counts: Record<ReconState, number> = { ok: 0, mismatch: 0, unchecked: 0 }
  for (const line of lines) counts[line.state] += 1
  return (['ok', 'mismatch', 'unchecked'] as ReconState[]).filter((state) => counts[state]).map((state) => `${STATE_TEXT[state]} ${counts[state]}`).join(' · ')
}
