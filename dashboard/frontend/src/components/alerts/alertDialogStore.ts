// The new-alert dialog, openable from anywhere: Today's 设提醒, the stock drawer, the watchlist and the
// 提醒 page. `openNewAlert({ ticker, name, kind, threshold, account })` prefills it; `openEditAlert(rule)`
// changes an existing rule (threshold, account, note). Mounted once by the shell (GlobalSearch).
import { create } from 'zustand'
import type { AlertKind, AlertRule } from '@/features/alerts/rules'

export interface AlertDraft {
  ticker?: string
  name?: string | null
  kind?: AlertKind
  /** A price, or a percentage for 涨跌幅 (signed) and 仓位上限. */
  threshold?: string
  account?: string | null
  note?: string | null
}

interface AlertDialogState {
  /** null: closed. */
  draft: AlertDraft | null
  /** Set when editing an existing rule. */
  rule: AlertRule | null
  openNew: (draft?: AlertDraft) => void
  openEdit: (rule: AlertRule, name?: string | null) => void
  close: () => void
}

export const useAlertDialog = create<AlertDialogState>((set) => ({
  draft: null,
  rule: null,
  openNew: (draft = {}) => set({ draft, rule: null }),
  openEdit: (rule, name = null) => set({
    rule,
    draft: { ticker: rule.ticker, name, kind: rule.kind as AlertKind, threshold: rule.threshold, account: rule.account, note: rule.note },
  }),
  close: () => set({ draft: null, rule: null }),
}))

/** Opens the new-alert dialog, prefilled with what the caller knows. */
export function openNewAlert(draft: AlertDraft = {}) {
  useAlertDialog.getState().openNew(draft)
}

/** Opens the dialog on an existing rule (not for rules a plan owns). */
export function openEditAlert(rule: AlertRule, name?: string | null) {
  useAlertDialog.getState().openEdit(rule, name)
}
