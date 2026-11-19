// The opening balance's 累计净投入 suggestion (plan EN-5: everything the opening books, at cost: cash +
// Σ quantity × cost + the fund value, labelled as an estimate to replace with the real net deposits) and the
// legacy migration's answers. The suggestion is only offered, never filled in by itself, and only when every
// amount is in CNY: converting HKD or USD would need a rate nobody typed.
// Exact decimal strings throughout (../ledger/decimal.ts). Pure TypeScript (node tests).
import { decAdd, decMul, decSign, isDecimal } from '../ledger/decimal.ts'
import { bShareRefusal, parseTicker, tickerCurrency, type EntryValues } from '../ledger/entry.ts'
import { fmtLong, fmtMoney, fmtPrice, fmtQty, type Shown } from '../../lib/format.ts'
import type { DraftAccount, DraftValue, LegacyDraft, LegacyProposalBody, LegacySource, NeedKind } from './types.ts'

export const ESTIMATE_LABEL = '按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金'

/**
 * cash + Σ quantity × cost of positions + the fund value, in CNY: everything the opening books, at cost. Null
 * when something is foreign, missing or not a number (a fund value of null is unknown; blank is none).
 */
export function costEstimate(
  cash: { currency: string; value: string | null }[],
  positions: { ticker: string | null; quantity: string | null; cost: string | null }[],
  fund: string | null = '0',
): string | null {
  if (fund === null || !isDecimal(fund.trim() || '0')) return null
  let total: string | null = fund.trim() || '0'
  for (const entry of cash) {
    const value = entry.value?.trim() || '0'
    if (!isDecimal(value)) return null
    if (entry.currency !== 'CNY') {
      if (decSign(value) !== 0) return null
      continue
    }
    total = decAdd(total, value)
  }
  for (const row of positions) {
    if (!row.ticker && !row.quantity && !row.cost) continue
    const ticker = parseTicker(row.ticker)
    if (!ticker || tickerCurrency(ticker) !== 'CNY') return null
    if (!isDecimal(row.quantity ?? '') || !isDecimal(row.cost ?? '')) return null
    total = decAdd(total, decMul(row.quantity, row.cost))
  }
  return total
}

/** Whether the 新建账户 form holds HKD or USD (cash or a position): the suggestion is then not offered. */
export function openingHasForeign(values: EntryValues): boolean {
  const cash = (['HKD', 'USD'] as const).some((currency) => {
    const value = values.openingCash[currency].trim()
    return Boolean(value) && isDecimal(value) && decSign(value) !== 0
  })
  return cash || values.positions.some((row) => {
    const ticker = parseTicker(row.ticker)
    return ticker !== null && tickerCurrency(ticker) !== 'CNY'
  })
}

/** The suggestion for the 新建账户 form. */
export function openingEstimate(values: EntryValues): string | null {
  return costEstimate(
    (['CNY', 'HKD', 'USD'] as const).map((currency) => ({ currency, value: values.openingCash[currency] })),
    values.positions.map((row) => ({ ticker: row.ticker, quantity: row.quantity, cost: row.cost })),
    values.fundValue,
  )
}

// -- the legacy migration's answers -------------------------------------------------------------

export interface LegacyAnswers {
  /** Typed values by the key of the `needs_input` item. */
  fills: Record<string, string>
  /** Position rows left out (only rows that needed input). */
  skip: string[]
  /** The name of portfolio.json's one account. */
  accountName: string
}

export function emptyAnswers(draft: LegacyDraft): LegacyAnswers {
  const editable = draft.accounts.find((account) => account.account_editable)
  return { fills: {}, skip: [], accountName: editable?.account ?? '' }
}

/** Rows that may be left out: those with a value that needs input. */
export function skippableRows(draft: LegacyDraft): Set<string> {
  return new Set(draft.needs_input.map((item) => item.row).filter((row): row is string => Boolean(row)))
}

const VALID_NUMBER: Record<Exclude<NeedKind, 'ticker'>, (value: string) => string | null> = {
  quantity: (value) => (decSign(value) === 1 ? null : '数量必须大于 0'),
  cost: (value) => (decSign(value) === -1 ? '成本价不能为负数' : null),
  cash: () => null,
  contributed: () => null,
  fund: (value) => (decSign(value) === -1 ? '基金估值不能为负数' : null),
}

/** What is wrong with the answers, by key (and `account` for the account name); empty when they can be sent. */
export function answerErrors(draft: LegacyDraft, answers: LegacyAnswers, existingAccounts: string[] = []): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const item of draft.needs_input) {
    if (item.row && answers.skip.includes(item.row)) continue
    const value = (answers.fills[item.key] ?? '').trim()
    if (!value) {
      errors[item.key] = '请填写'
    } else if (item.kind === 'ticker') {
      if (!parseTicker(value)) errors[item.key] = '代码需带交易所前缀，如 SHA:600519'
      else if (bShareRefusal(parseTicker(value))) errors[item.key] = bShareRefusal(parseTicker(value))!
    } else if (!isDecimal(value)) {
      errors[item.key] = '请输入数字'
    } else {
      const problem = VALID_NUMBER[item.kind](value)
      if (problem) errors[item.key] = problem
    }
  }
  const editable = draft.accounts.find((account) => account.account_editable)
  if (editable) {
    const name = answers.accountName.trim()
    if (!name) errors.account = '请填写账户名称'
    else if (name.length > 240) errors.account = '账户名最多 240 个字符'
    else if (existingAccounts.includes(name)) errors.account = '账本中已有同名账户，请换一个名称'
  }
  return errors
}

/** `POST /api/setup/legacy/proposals`: only the answers to what needed input, trimmed. */
export function proposalBody(draft: LegacyDraft, answers: LegacyAnswers): LegacyProposalBody {
  const fills: Record<string, string> = {}
  for (const item of draft.needs_input) {
    if (item.row && answers.skip.includes(item.row)) continue
    const value = (answers.fills[item.key] ?? '').trim()
    if (value) fills[item.key] = item.kind === 'ticker' ? parseTicker(value) ?? value : value
  }
  const body: LegacyProposalBody = { source: draft.source, date: draft.date, token: draft.token, fills, skip: [...answers.skip] }
  if (draft.accounts.some((account) => account.account_editable)) body.account_name = answers.accountName.trim()
  return body
}

/** The value a cell shows: the file's, else what was typed, else null (an input is shown). */
export function answered(item: DraftValue, answers: LegacyAnswers): string | null {
  return item.value ?? ((answers.fills[item.key] ?? '').trim() || null)
}

/** The 累计净投入 suggestion for one legacy account, from its read and typed values. */
export function accountEstimate(account: DraftAccount, answers: LegacyAnswers): string | null {
  return costEstimate(
    account.cash.map((item) => ({ currency: item.currency ?? 'CNY', value: answered(item, answers) })),
    account.positions
      .filter((row) => !answers.skip.includes(row.key))
      .map((row) => ({ ticker: answered(row.ticker, answers), quantity: answered(row.quantity, answers), cost: answered(row.cost_price, answers) })),
    answered(account.fund_cny, answers),
  )
}

/**
 * The 旧文件 / 期初日期 controls: shown once a draft loaded, and also after a failure other than "no file" (an
 * undated portfolio.json asks for a date before it can be read). The date is the one chosen, else the loaded
 * draft's; after a failure it is empty rather than the last file's date, which would not apply.
 */
export function legacyControls(input: {
  date: string | null; loaded: LegacyDraft | undefined; last: LegacyDraft | null; failed: boolean; missing: boolean
}): { show: boolean; date: string } {
  const show = Boolean(input.loaded ?? input.last) || (input.failed && !input.missing)
  return { show, date: input.date ?? input.loaded?.date ?? (input.failed ? '' : input.last?.date ?? '') }
}

/** The sources the migration offers: only those with something not migrated yet. */
export function legacySources(draft: LegacyDraft): LegacySource[] {
  return draft.sources.filter((item) => item.pending !== false)
}

/** A value read from the legacy file, as shown: every digit, a long one rounded to 8 places with "≈" (fmtLong). */
export function draftValueText(kind: 'money' | 'quantity' | 'price' | 'ticker', value: string, currency = 'CNY'): Shown {
  if (kind === 'ticker') return { text: value }
  if (kind === 'quantity') return fmtLong(value, fmtQty)
  if (kind === 'price') return fmtLong(value, (shown) => fmtPrice(shown, currency, 'stock', { symbol: true, exact: true }))
  return fmtLong(value, (shown) => fmtMoney(shown, currency, { exact: true }))
}

/** "2 个账户 · 3 只持仓 · 1 项需要填写". */
export function draftSummary(draft: LegacyDraft): string {
  const accounts = draft.accounts.filter((account) => !account.exists)
  const positions = accounts.reduce((sum, account) => sum + account.positions.length, 0)
  const parts = [`${accounts.length} 个账户`, `${positions} 只持仓`]
  if (draft.needs_input.length) parts.push(`${draft.needs_input.length} 项需要填写`)
  const existing = draft.accounts.length - accounts.length
  if (existing) parts.push(`${existing} 个账户已在账本中`)
  return parts.join(' · ')
}
