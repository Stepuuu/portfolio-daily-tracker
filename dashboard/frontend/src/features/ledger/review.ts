// What a ledger preview says, in words: one line per event, the 项目 / 变更前 / 变更后 rows per account
// ("无" where a value did not exist, never 0), the backend's warnings and refusals in Chinese, and the
// trade-plan check. Every amount keeps the backend's digits (an ETF fill at 1.234 with a 0.004 fee reads
// exactly so); a figure with more than 8 places (a derived price, an average cost) is shown ≈ rounded to 8
// with its full value kept (lib/format.ts `fmtLong`). Pure TypeScript.
import {
  fmtLong, fmtMoney, fmtNumber, fmtPrice, fmtQty, longLine, shownText, type NumericInput, type Shown, type ShownPick,
} from '../../lib/format.ts'
import { feeEstimated, noteText } from './events.ts'
import { kindLabel } from './kinds.ts'
import type { LedgerAccount, LedgerEvent, LedgerState, PlanCheckItem, PlanRelation, Proposal } from './types.ts'

const CURRENCY_ORDER = ['CNY', 'HKD', 'USD']

function byCurrency(a: string, b: string): number {
  const rank = (unit: string) => (CURRENCY_ORDER.includes(unit) ? CURRENCY_ORDER.indexOf(unit) : CURRENCY_ORDER.length)
  return rank(a) - rank(b) || a.localeCompare(b)
}

/** Money in the event's currency, every digit kept: "¥1,000.00", "HK$500.004". */
function cash(amount: string | undefined, currency: string | undefined): Shown {
  return fmtLong(amount, (value) => fmtMoney(value, currency ?? 'CNY', { exact: true }))
}

/** "费用 5.01", "费用 5.01（估算）" when the fee booked was the estimate. */
function feeText(event: LedgerEvent, pick: ShownPick): string {
  return `费用 ${pick(fmtLong(event.fee ?? '0', (value) => fmtNumber(value, { exact: true })))}${feeEstimated(event) ? '（估算）' : ''}`
}

/** "示例宽基 SHA:DEMO", or the ticker alone when the name is the ticker. */
export function subjectOf(event: Pick<LedgerEvent, 'ticker' | 'name'>, names?: Record<string, string>): string {
  if (!event.ticker) return ''
  const name = event.name || names?.[event.ticker]
  return name && name !== event.ticker ? `${name} ${event.ticker}` : event.ticker
}

/**
 * One line for an event: "2026-11-17 买入 示例宽基 SHA:DEMO 100 股 @ 61.20 · 费用 5.00 · 长期账户".
 * `lookup` finds the event a reversal undoes; `names` gives names for tickers stored without one. A figure
 * with more than 8 places reads "≈…" (`pick` = `fullText` gives every digit; see `eventLineShown`).
 */
export function eventLine(
  event: LedgerEvent, lookup?: (id: string) => LedgerEvent | undefined, names?: Record<string, string>, pick: ShownPick = shownText,
): string {
  const kind = kindLabel(event.kind)
  const qty = (value: NumericInput) => pick(fmtLong(value, fmtQty))
  const subject = subjectOf(event, names)
  const account = event.account ?? ''
  let body: string
  switch (event.kind) {
    case 'buy':
    case 'sell': {
      const currency = event.currency ?? 'CNY'
      const price = fmtLong(event.price, (value) => fmtPrice(value, currency, 'stock', { symbol: currency !== 'CNY', exact: true }))
      body = [kind, subject, `${qty(event.quantity)} 股 @ ${pick(price)}`].join(' ')
      body += ` · ${feeText(event, pick)}`
      break
    }
    case 'transfer':
      body = `${kind} ${pick(cash(event.amount, event.currency))} · ${account} → ${event.to_account ?? ''}`
      break
    case 'fx':
      body = `${kind} ${pick(cash(event.amount, event.currency))} → ${pick(cash(event.received_amount, event.to_currency))}`
      break
    case 'split':
      body = [kind, subject, `比例 ${qty(event.ratio)}`].join(' ')
      break
    case 'fund_buy':
    case 'fund_sell':
      body = `${kind} ${pick(cash(event.amount, 'CNY'))} · ${feeText(event, pick)}`
      break
    case 'fund_value':
      body = `${kind} 调整为 ${pick(cash(event.amount, 'CNY'))}`
      break
    case 'opening': {
      const count = event.positions?.length ?? 0
      body = `${kind} ${account}${count ? ` · ${count} 项持仓` : ''}`
      break
    }
    case 'reverse': {
      const target = event.target_id ? lookup?.(event.target_id) : undefined
      body = target ? `${kind}：${eventLine(target, undefined, names, pick)}` : `${kind}一笔记录`
      break
    }
    default:
      body = [kind, subject, pick(cash(event.amount, event.currency))].filter(Boolean).join(' ')
  }
  if (event.fx_rate && event.currency && event.currency !== 'CNY') body += ` · 汇率 ${pick(fmtLong(event.fx_rate, String))}`
  const tail = ['transfer', 'opening', 'reverse'].includes(event.kind) || !account ? '' : ` · ${account}`
  const words = event.kind !== 'reverse' ? noteText(event.note) : ''
  const note = words ? ` · ${words}` : ''
  return `${event.date} ${body}${tail}${note}`
}

/** `eventLine` for the page: as shown, with the full line as its title when a figure in it was rounded. */
export function eventLineShown(
  event: LedgerEvent, lookup?: (id: string) => LedgerEvent | undefined, names?: Record<string, string>,
): Shown {
  return longLine((pick) => eventLine(event, lookup, names, pick))
}

/** "3 笔待入账 · 跳过 1 笔重复记录", or the receipt "已入账 3 笔 · 版本 12". */
export function proposalStatus(proposal: Pick<Proposal, 'events' | 'duplicates' | 'receipt'>): string {
  const receipt = proposal.receipt
  const head = receipt ? `已入账 ${receipt.applied} 笔 · 版本 ${receipt.revision}` : `${proposal.events.length} 笔待入账`
  const duplicates = receipt ? receipt.duplicates : proposal.duplicates
  return duplicates ? `${head} · 跳过 ${duplicates} 笔重复记录` : head
}

/** The toast after a confirmation: "已入账 1 笔 · 版本 12". */
export function receiptMessage(receipt: { applied: number; revision: number; duplicates: number }): string {
  const head = `已入账 ${receipt.applied} 笔 · 版本 ${receipt.revision}`
  return receipt.duplicates ? `${head} · 跳过 ${receipt.duplicates} 笔重复记录` : head
}

// ------------------------------------------------------------------------------------------------
// Changes

export type ChangeFormat = 'money' | 'quantity' | 'price'

export interface ChangeRow {
  key: string
  /** "CNY 现金", "示例宽基 数量". */
  label: string
  /** The ticker under the label, for position rows. */
  detail?: string
  /** null: the item did not exist (shown as 无, never 0). */
  before: string | null
  after: string | null
  format: ChangeFormat
  currency: string
}

export interface AccountChanges {
  account: string
  /** The account is created by this preview. */
  created: boolean
  rows: ChangeRow[]
}

function own<T>(record: Record<string, T> | undefined, key: string): T | undefined {
  return record && Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined
}

function accountRows(before: LedgerAccount | undefined, after: LedgerAccount | undefined): ChangeRow[] {
  const rows: ChangeRow[] = []
  const push = (row: ChangeRow) => {
    if (row.before !== row.after) rows.push(row)
  }
  const units = [...new Set([...Object.keys(before?.cash_balances ?? {}), ...Object.keys(after?.cash_balances ?? {})])].sort(byCurrency)
  for (const unit of units) {
    push({ key: `cash:${unit}`, label: `${unit} 现金`, before: own(before?.cash_balances, unit) ?? null,
      after: own(after?.cash_balances, unit) ?? null, format: 'money', currency: unit })
  }
  const tickers = [...new Set([...Object.keys(before?.positions ?? {}), ...Object.keys(after?.positions ?? {})])]
  for (const ticker of tickers) {
    const prev = own(before?.positions, ticker)
    const next = own(after?.positions, ticker)
    const name = next?.name ?? prev?.name ?? ticker
    const currency = next?.currency ?? prev?.currency ?? 'CNY'
    const label = name !== ticker ? name : ticker
    const detail = name !== ticker ? ticker : undefined
    push({ key: `qty:${ticker}`, label: `${label} 数量`, detail, before: prev?.quantity ?? null, after: next?.quantity ?? null,
      format: 'quantity', currency })
    push({ key: `cost:${ticker}`, label: `${label} 单位成本`, detail, before: prev?.cost_price ?? null,
      after: next?.cost_price ?? null, format: 'price', currency })
  }
  push({ key: 'contributed', label: '累计净投入', before: before?.contributed_cny ?? null, after: after?.contributed_cny ?? null,
    format: 'money', currency: 'CNY' })
  push({ key: 'fund', label: '基金估值', before: before?.fund_cny ?? null, after: after?.fund_cny ?? null, format: 'money', currency: 'CNY' })
  const realized = [...new Set([...Object.keys(before?.realized ?? {}), ...Object.keys(after?.realized ?? {})])].sort(byCurrency)
  for (const unit of realized) {
    push({ key: `realized:${unit}`, label: `已实现盈亏 ${unit}`, before: own(before?.realized, unit) ?? null,
      after: own(after?.realized, unit) ?? null, format: 'money', currency: unit })
  }
  return rows
}

/** The changed items of every account the preview touches, in account order. */
export function changeRows(proposal: { before: LedgerState; after: LedgerState }): AccountChanges[] {
  const names = [...new Set([...Object.keys(proposal.before.accounts ?? {}), ...Object.keys(proposal.after.accounts ?? {})])]
  return names
    .map((account) => {
      const before = own(proposal.before.accounts, account)
      const after = own(proposal.after.accounts, account)
      return { account, created: !before && Boolean(after), rows: accountRows(before, after) }
    })
    .filter((group) => group.rows.length > 0 || group.created)
}

/**
 * A change value for the table: 无 when it did not exist, else money / quantity / price, with every digit up
 * to 8 places (an average cost that repeats is ≈ rounded to 8, its full value in `full`).
 */
export function changeFigure(row: ChangeRow, side: 'before' | 'after'): Shown {
  const value = row[side]
  if (value === null) return { text: '无' }
  if (row.format === 'quantity') return fmtLong(value, fmtQty)
  if (row.format === 'price') return fmtLong(value, (v) => fmtPrice(v, row.currency, 'stock', { symbol: row.currency !== 'CNY', exact: true }))
  return fmtLong(value, (v) => fmtMoney(v, row.currency, { exact: true }))
}

/** `changeFigure` as text. */
export function changeValue(row: ChangeRow, side: 'before' | 'after'): string {
  return changeFigure(row, side).text
}

/** What the web says of a proposal the Feishu card made: it is confirmed there, never here. */
export const CARD_ONLY = '在飞书卡片上确认：这份方案来自飞书卡片，卡片确认后会更新清单并生成日报。这里只能放弃它。'

/** Why this proposal is confirmed elsewhere (the Feishu card), or null when it is confirmed here. */
export function confirmElsewhere(proposal: { confirm_on?: string; receipt?: unknown }): string | null {
  return proposal.confirm_on === 'feishu-card' && !proposal.receipt ? CARD_ONLY : null
}

/** A preview made before the ledger's current revision can no longer be confirmed (the list and the review agree). */
export function isOutdated(proposalRevision: number, ledgerRevision: number | null | undefined): boolean {
  return typeof ledgerRevision === 'number' && proposalRevision < ledgerRevision
}

/** Event lines shown before 显示全部. */
export const SHOWN_EVENTS = 50

/** The events listed in the review: the first 50, or all of them once expanded. */
export function shownEvents<T>(events: T[], expanded: boolean): T[] {
  return expanded ? events : events.slice(0, SHOWN_EVENTS)
}

/**
 * What a failed 确认入账 means. A refusal (the backend answered 4xx) booked nothing; without an answer (network,
 * timeout, a gateway's 5xx) it may have been booked, so the outcome is read back before anything is said.
 */
export function confirmFailure(status: number | undefined): 'refused' | 'unknown' {
  return status !== undefined && status >= 400 && status < 500 && status !== 408 ? 'refused' : 'unknown'
}

// ------------------------------------------------------------------------------------------------
// Warnings

const NEGATIVE_CASH = /^(.+) (CNY|HKD|USD): negative cash (-?[\d.]+)$/

/** The ledger's warnings in Chinese (it writes the negative-cash one in English). */
export function warningText(warning: string): string {
  const match = NEGATIVE_CASH.exec(warning)
  if (match) return `${match[1]} 的 ${match[2]} 现金将为负数：${fmtMoney(match[3], match[2], { exact: true })}`
  return warning
}

// The ledger's own refusals (core/ledger.py, backend/api/ledger.py) are English; every one has its Chinese
// text here (a test reads both sources and fails on a message without one). Chinese messages pass through.
const LEDGER_ERRORS: [RegExp, (match: RegExpExecArray) => string][] = [
  [/^Insufficient native cash/, () => '现金不足：勾选“允许融资”可记为负余额，或减少数量'],
  [/^Cannot sell more shares than held/, () => '卖出数量超过持仓'],
  [/^Trade currency does not match/, () => '币种与标的所在市场不符'],
  [/^Split requires an existing position/, () => '拆股/送转需要该账户已有这只标的'],
  [/^Transfer requires a different existing destination/, () => '转入账户必须是另一个已有账户'],
  [/^FX exchange requires two different currencies/, () => '换汇需要两种不同的币种'],
  [/^Insufficient CNY cash for fund purchase/, () => '人民币现金不足，无法申购；勾选“允许融资”可记为负余额'],
  [/^Fund redemption exceeds recorded fund value/, () => '赎回金额超过记录的基金估值，请先调整基金估值'],
  [/^Aggregate fund values use CNY/, () => '基金估值只能按人民币记录'],
  [/^Aggregate fund transactions use CNY/, () => '基金申购和赎回只能按人民币记录'],
  [/^Reversal target is missing/, () => '要冲销的记录不存在、已被冲销，或本身就是一笔冲销'],
  [/^Reversal must follow the original event/, () => '冲销的日期不能早于原记录'],
  [/^Account already exists: (.*)$/, (m) => `已有账户「${m[1]}」，期初余额只能建立一次`],
  [/^Create an opening balance for account: (.*)$/, (m) => `账户「${m[1]}」还没有期初余额`],
  [/^Duplicate opening position/, () => '期初持仓中同一标的出现了两次'],
  [/^The ledger changed after this preview/, () => '账本在预览之后有了变化，请重新预览后再确认'],
  [/^The ledger changed while this proposal was prepared/, () => '准备方案时账本有了变化，请重新生成方案'],
  [/^An external ID already exists with different content/, () => '有流水号相同但内容不同的已入账记录，本次没有导入'],
  [/^Use an exchange-qualified ticker/, () => '代码需带交易所前缀，如 SHA:600519、HKG:00700、NASDAQ:AAPL'],
  [/^Date must be YYYY-MM-DD|^Invalid calendar date/, () => '日期格式应为 YYYY-MM-DD'],
  [/^An explicit numeric amount is required/, () => '金额需要填写数字'],
  [/^Invalid numeric amount/, () => '金额不是有效的数字'],
  [/^Amount must be finite and within supported bounds/, () => '金额超出可记录的范围'],
  [/^Amount has excessive decimal precision/, () => '金额的小数位数过多'],
  [/^Amount has an invalid sign/, () => '金额的正负不对：这里需要大于 0（或不小于 0）的数'],
  [/^Currency must be CNY, HKD or USD/, () => '币种只能是 CNY、HKD 或 USD'],
  [/^(\S+) is required \(maximum 240 characters\)/, (m) => `${FIELD_LABEL[m[1]] ?? m[1]}不能为空，最多 240 个字符`],
  [/^Each event must be an object/, () => '每一笔记录都必须是一个对象，请刷新页面后重试'],
  [/^Unknown event fields: ?(.*)$/, (m) => `记录中有账本不认识的字段${m[1] ? `：${m[1]}` : ''}`],
  [/^allow_margin must be a boolean/, () => '“允许融资”只能是或否'],
  [/^Unsupported event kind/, () => '账本不支持这种记录类型'],
  [/^cash_balances must be an object/, () => '期初现金的格式不对'],
  [/^positions must be an array/, () => '期初持仓最多 1000 行'],
  [/^Invalid opening position fields/, () => '期初持仓只能填写代码、名称、数量和单位成本'],
  [/^Event is too large/, () => '这笔记录过大，无法入账'],
  [/^Submit between 1 and 1000 events per proposal/, () => '一份方案需包含 1 到 1000 笔记录'],
  [/^Proposal not found/, () => '方案不存在或已被放弃'],
  [/^No dated holdings found/, () => '没有找到这个日期或之前的旧持仓文件，请手动建立期初余额'],
  [/^Legacy cash disagrees with CNY cash_balances/, () => '旧持仓文件中的现金与人民币现金余额不一致，请先核对后再迁移'],
  [/^Use the standard CSV template/, () => '请使用标准 CSV 模板，并为每一行填写 external_id'],
  [/^CSV must be UTF-8 text/, () => 'CSV 需为 UTF-8 编码且小于 2 MB'],
  [/^CSV requires unique column headers/, () => 'CSV 的列名有重复，请让每一列的表头各不相同'],
  [/^CSV row (\S+) needs an external_id/, (m) => `CSV 第 ${m[1]} 行缺少 external_id`],
  [/^CSV row (\S+) has the wrong number of columns/, (m) => `CSV 第 ${m[1]} 行的列数不对`],
  [/^Use the opening or reversal preview for these event types/, () => '期初余额和冲销不能从 CSV 导入，请在账本页分别建立'],
  [/^Import at most 1000 CSV rows at a time/, () => '一次最多导入 1000 行 CSV'],
  [/^Write both the thesis and the conditions/, () => '请同时写下投资逻辑和判断失效的条件'],
  [/^Demo mode is read-only/, () => '演示模式只读，不能修改账本'],
  // A request the API's schema refused ("events: List should have at least 1 item ..."): a bug or an old page.
  [/^[a-z_]+(?:\.[a-z0-9_]+)*: [A-Z]/, () => '提交的内容格式不对，请刷新页面后重试'],
]

const FIELD_LABEL: Record<string, string> = {
  account: '账户', kind: '类型', source: '来源', external_id: '流水号', target_id: '冲销对象', ticker: '标的', note: '备注',
  name: '名称', to_account: '转入账户',
}

/**
 * The accounts an opening preview of the old holdings file asks the 累计净投入 of (a 422 from
 * `POST /ledger/opening-preview` with `missing_contributions`); empty for any other error.
 */
export function missingContributions(error: unknown): string[] {
  const data = (error as { response?: { status?: number; data?: { missing_contributions?: unknown } } } | null)?.response
  const accounts = data?.status === 422 ? data.data?.missing_contributions : undefined
  return Array.isArray(accounts) ? accounts.filter((name): name is string => typeof name === 'string') : []
}

/** The accounts asked for (`missingContributions`) when the preview was for `date`, else none: another date can
 * select another holdings file, which may have every contribution. */
export function contributionsAsked(prompt: { date: string; accounts: string[] } | null, date: string): string[] {
  return prompt && prompt.date === date ? prompt.accounts : []
}

/** A ledger error in Chinese; messages that are already Chinese (or unknown) are kept. */
export function ledgerErrorText(message: string): string {
  for (const [pattern, text] of LEDGER_ERRORS) {
    const match = pattern.exec(message)
    if (match) return text(match)
  }
  return message
}

// ------------------------------------------------------------------------------------------------
// Trade plans

export const RELATION_LABEL: Record<PlanRelation, string> = {
  entry: '建仓', add: '加仓', reduce: '减仓', exit: '清仓', none: '无计划',
}

export interface PlanCheckLine {
  key: string
  /** "买入 SHA:DEMO 100 股". */
  title: string
  /** "计划 #12 · 加仓", or "无计划". */
  relation: string
  hasPlan: boolean
  /** `asOf`: "行情时间 2026-11-17 10:32", the quotes a weight was valued at (`data.quotes_as_of`). */
  deviations: { key: string; level: 'warning' | 'info'; message: string; asOf?: string }[]
}

/** "行情时间 2026-11-17 10:32" (Beijing time) for an ISO time; undefined without one. */
function quotesTime(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const moment = new Date(value)
  if (Number.isNaN(moment.getTime())) return undefined
  const parts: Record<string, string> = {}
  const format = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  })
  for (const part of format.formatToParts(moment)) parts[part.type] = part.value
  return `行情时间 ${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

/** The plan check as lines; trades with no plan and nothing to say are left out. */
export function planCheckLines(items: PlanCheckItem[] | undefined): PlanCheckLine[] {
  return (items ?? [])
    .filter((item) => item.relation !== 'none' || item.deviations.length > 0)
    .map((item) => {
      const hasPlan = item.relation !== 'none' && item.plan_id !== null
      const title = [kindLabel(item.kind), item.ticker, item.quantity ? `${fmtQty(item.quantity)} 股` : ''].filter(Boolean).join(' ')
      const relation = hasPlan ? `计划 #${item.plan_id} · ${RELATION_LABEL[item.relation]}` : RELATION_LABEL.none
      return {
        key: `${item.index}:${item.event_ref ?? ''}`,
        title,
        relation,
        hasPlan,
        deviations: item.deviations.map((deviation, index) => ({
          key: `${deviation.code}:${index}`, level: deviation.level === 'info' ? 'info' as const : 'warning' as const,
          message: deviation.message, asOf: quotesTime(deviation.data?.quotes_as_of),
        })),
      }
    })
}

/** How many plan deviations are warnings (the review's summary line). */
export function planWarningCount(items: PlanCheckItem[] | undefined): number {
  return (items ?? []).reduce((count, item) => count + item.deviations.filter((d) => d.level !== 'info').length, 0)
}
