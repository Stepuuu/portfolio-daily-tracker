// The ledger's event kinds and their Chinese names: the one map every page labels kinds with (the tray,
// Today's banner, the 记一笔 dialog, the 账本 tables). The kinds are exactly those dashboard/core/ledger.py
// accepts (`KINDS`); tests/ledger.test.mjs checks the two lists agree. Pure TypeScript.

/** Every kind the ledger accepts. */
export const LEDGER_KINDS = [
  'opening', 'buy', 'sell', 'deposit', 'withdrawal', 'dividend', 'fee', 'interest', 'transfer', 'fx', 'split',
  'reverse', 'fund_value', 'fund_buy', 'fund_sell',
] as const

export type LedgerKind = (typeof LEDGER_KINDS)[number]

/** What 记一笔 can enter: everything but a reversal (冲销 is a button on the event it undoes). */
export type EntryKind = Exclude<LedgerKind, 'reverse'>

export const LEDGER_KIND_LABEL: Record<LedgerKind, string> = {
  opening: '期初余额',
  buy: '买入',
  sell: '卖出',
  deposit: '入金',
  withdrawal: '出金',
  dividend: '分红',
  fee: '费用',
  interest: '利息',
  transfer: '转账',
  fx: '换汇',
  split: '拆股/送转',
  reverse: '冲销',
  fund_value: '基金估值',
  fund_buy: '基金申购',
  fund_sell: '基金赎回',
}

/** "买入"; an unknown kind is shown as it is. */
export function kindLabel(kind: string | null | undefined): string {
  if (!kind) return ''
  return (LEDGER_KIND_LABEL as Record<string, string>)[kind] ?? kind
}

/** The five kinds shown as segments; the rest are under 更多. */
export const PRIMARY_KINDS: EntryKind[] = ['buy', 'sell', 'dividend', 'deposit', 'withdrawal']

/** 更多, in groups (a group with a title is listed under it). */
export const MORE_KINDS: { title?: string; kinds: EntryKind[] }[] = [
  { kinds: ['fx', 'transfer', 'split', 'fee', 'interest'] },
  { title: '基金', kinds: ['fund_buy', 'fund_sell', 'fund_value'] },
  { title: '账户', kinds: ['opening'] },
]

export const ENTRY_KINDS: EntryKind[] = [...PRIMARY_KINDS, ...MORE_KINDS.flatMap((group) => group.kinds)]

export function isEntryKind(value: unknown): value is EntryKind {
  return typeof value === 'string' && (ENTRY_KINDS as string[]).includes(value)
}

/** Menu names where the table name would be unclear on its own ("新账户（期初余额）"). */
export const MENU_LABEL: Partial<Record<EntryKind, string>> = {
  opening: '新账户（期初余额）',
  fund_value: '基金估值调整',
}
