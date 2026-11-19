// The 记一笔 form without React: which fields a kind needs, what is wrong with the input, the events it
// proposes, and the cash line shown before the preview. The backend's preview is the authority; nothing
// here is booked. Money is exact decimal strings (./decimal.ts), never binary floats. Pure TypeScript.
//
// Evidence, not defaults: a fee left empty books the backend's estimate, a price filled from a quote and an
// accepted reference FX rate keep their origin. The ledger has no field for that origin, so it travels in
// the event's note as marks such as 〔费用为估算：按账户费率估算〕 (`provenanceMarks`); events.ts
// (`noteText`, `noteMarks`) shows them apart from the person's own words.
import { decAdd, decCmp, decMul, decNeg, decPositive, decSign, decSub, isDecimal } from './decimal.ts'
import type { EntryKind } from './kinds.ts'

export type Currency = 'CNY' | 'HKD' | 'USD'
export const CURRENCIES: Currency[] = ['CNY', 'HKD', 'USD']

const MARKET_CURRENCY: Record<string, Currency> = { SHA: 'CNY', SHE: 'CNY', HKG: 'HKD', NASDAQ: 'USD', NYSE: 'USD' }
/** The ledger's ticker rule (core/ledger.py `instrument`). */
const TICKER = /^(SHA|SHE|HKG|NASDAQ|NYSE):[A-Z0-9.-]{1,20}$/
const DATE = /^\d{4}-\d{2}-\d{2}$/
const MAX_NOTE = 240

/** The 标的 field searches (components/inputs/TickerInput): what it says until one listing is picked. */
export const TICKER_HINT = '从列表中选择；可输入名称或代码搜索（如 茅台、600519、00700、AAPL）'
/** A plain code field (the opening's positions) takes the ledger's form only. */
export const QUALIFIED_TICKER_HINT = '代码需带交易所前缀，如 SHA:600519、HKG:00700、NASDAQ:AAPL'

// B shares trade in USD (SHA:900xxx) or HKD (SHE:200xxx, SHE:201xxx); the ledger books every SHA/SHE listing in
// CNY, so it refuses them (core/ledger.py `B_SHARE_REFUSAL`, prefixes as core/market/instruments.py).
const B_SHARE_PREFIXES: Record<string, string[]> = { SHA: ['900'], SHE: ['200', '201'] }
const B_SHARE_CURRENCY: Record<string, Currency> = { SHA: 'USD', SHE: 'HKD' }
export const B_SHARE_REFUSAL = '{ticker} 是 B 股，以 {currency} 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。'

/** The backend's refusal for a B share ("SHA:900901 是 B 股，以 USD 交易；…"); null for anything bookable. */
export function bShareRefusal(ticker: string | null | undefined): string | null {
  const [exchange, code = ''] = (ticker ?? '').split(':', 2)
  const prefixes = B_SHARE_PREFIXES[exchange]
  if (!prefixes || !/^\d{6}$/.test(code) || !prefixes.some((prefix) => code.startsWith(prefix))) return null
  return B_SHARE_REFUSAL.replace('{ticker}', ticker!).replace('{currency}', B_SHARE_CURRENCY[exchange])
}

/** "sha:600519 " → "SHA:600519"; null when the text is not an exchange-qualified ticker. */
export function parseTicker(text: string | null | undefined): string | null {
  const value = (text ?? '').trim().toUpperCase()
  return TICKER.test(value) ? value : null
}

/** "SHA" for "SHA:600519". */
export function exchangeOf(ticker: string | null | undefined): string | null {
  const prefix = (ticker ?? '').split(':', 1)[0].trim().toUpperCase()
  return prefix in MARKET_CURRENCY ? prefix : null
}

/** The trade currency of a ticker's market; null when unknown. */
export function tickerCurrency(ticker: string | null | undefined): Currency | null {
  const exchange = exchangeOf(ticker)
  return exchange ? MARKET_CURRENCY[exchange] : null
}

/** Quantity stepper: 100 shares for A-shares; none for Hong Kong (board lots vary) and the US. */
export function lotStep(ticker: string | null | undefined): number | null {
  const exchange = exchangeOf(ticker)
  return exchange === 'SHA' || exchange === 'SHE' ? 100 : null
}

export interface HeldPosition {
  ticker: string
  name?: string
}

/**
 * The ticker the field means: an exchange-qualified code, or the exact name or code of something the
 * account holds ("示例宽基", "600519"). Anything else waits for the search to pick a result.
 */
export function resolveTicker(text: string, held: HeldPosition[] = []): string | null {
  const direct = parseTicker(text)
  if (direct) return direct
  const wanted = text.trim().toUpperCase()
  if (!wanted) return null
  const matches = held.filter((position) => {
    const code = position.ticker.split(':').slice(1).join(':')
    return position.name?.trim().toUpperCase() === wanted || code === wanted
  })
  return matches.length === 1 ? matches[0].ticker : null
}

// ------------------------------------------------------------------------------------------------
// Fields per kind

export interface KindFields {
  /** 标的: required, optional (dividend, fee) or not asked. */
  ticker: 'required' | 'optional' | null
  /** 数量, 价格, 费用 (buy, sell). */
  trade: boolean
  /** One amount, and what it is called. */
  amount: string | null
  /** Where the currency comes from: the ticker's market, a choice, or always CNY. */
  currency: 'ticker' | 'choose' | 'cny' | null
  /** A CNY rate for foreign cash moving in or out of an account (changes 累计净投入). */
  fxRate: boolean
  toAccount: boolean
  /** 换汇: currency received and the amount actually received. */
  exchange: boolean
  ratio: boolean
  /** The fee of a fund purchase or redemption (CNY). */
  fundFee: boolean
  /** 允许融资: a buy may take cash below zero. */
  margin: boolean
  opening: boolean
}

const NONE: KindFields = {
  ticker: null, trade: false, amount: null, currency: null, fxRate: false, toAccount: false, exchange: false, ratio: false,
  fundFee: false, margin: false, opening: false,
}

export const KIND_FIELDS: Record<EntryKind, KindFields> = {
  buy: { ...NONE, ticker: 'required', trade: true, currency: 'ticker', margin: true },
  sell: { ...NONE, ticker: 'required', trade: true, currency: 'ticker' },
  dividend: { ...NONE, ticker: 'optional', amount: '分红金额', currency: 'choose' },
  deposit: { ...NONE, amount: '入金金额', currency: 'choose', fxRate: true },
  withdrawal: { ...NONE, amount: '出金金额', currency: 'choose', fxRate: true },
  fee: { ...NONE, ticker: 'optional', amount: '费用金额', currency: 'choose' },
  interest: { ...NONE, amount: '利息金额', currency: 'choose' },
  transfer: { ...NONE, amount: '转出金额', currency: 'choose', fxRate: true, toAccount: true },
  fx: { ...NONE, amount: '换出金额', currency: 'choose', exchange: true },
  split: { ...NONE, ticker: 'required', ratio: true },
  fund_buy: { ...NONE, amount: '申购金额', currency: 'cny', fundFee: true, margin: true },
  fund_sell: { ...NONE, amount: '赎回金额', currency: 'cny', fundFee: true },
  fund_value: { ...NONE, amount: '调整后的基金合计', currency: 'cny' },
  opening: { ...NONE, opening: true },
}

// ------------------------------------------------------------------------------------------------
// Values

export interface OpeningPositionInput {
  ticker: string
  quantity: string
  cost: string
}

/** A price filled from a quote (按现价 / 按昨收): where it came from, kept while the price is unchanged. */
export interface PriceFill {
  ticker: string
  basis: 'price' | 'prev_close'
  value: string
  /** The quote source, already in words ("模拟行情"). */
  source: string | null
  /** The quote's time (ISO). */
  time: string | null
  /** Not a verified quote (stale, a previous close standing in, a time unknown, a market without a calendar). */
  estimated: boolean
}

/** An FX rate taken from the reference rate (按参考汇率): kept while the rate is unchanged. */
export interface RateFill {
  value: string
  source: string | null
  time: string | null
}

export interface EntryValues {
  kind: EntryKind
  account: string
  date: string
  /** What is in the 标的 field. */
  ticker: string
  /** The instrument's name when known (a search result, a holding, the quote). */
  name: string
  quantity: string
  price: string
  /** '' books the estimate. */
  fee: string
  currency: Currency
  amount: string
  fxRate: string
  toAccount: string
  toCurrency: Currency
  receivedAmount: string
  ratio: string
  note: string
  allowMargin: boolean
  openingCash: Record<Currency, string>
  contributed: string
  fundValue: string
  positions: OpeningPositionInput[]
  /** The price came from a quote; cleared when the price is edited or the ticker changes. */
  priceFill?: PriceFill | null
  /** 汇率 came from the reference rate; cleared when it is edited or the currency changes. */
  rateFill?: RateFill | null
}

export function emptyValues(overrides: Partial<EntryValues> = {}): EntryValues {
  return {
    kind: 'buy', account: '', date: '', ticker: '', name: '', quantity: '', price: '', fee: '', currency: 'CNY',
    amount: '', fxRate: '', toAccount: '', toCurrency: 'USD', receivedAmount: '', ratio: '', note: '', allowMargin: false,
    openingCash: { CNY: '', HKD: '', USD: '' }, contributed: '', fundValue: '', positions: [{ ticker: '', quantity: '', cost: '' }],
    priceFill: null, rateFill: null,
    ...overrides,
  }
}

/** The currency the entry is in: the ticker's for trades, CNY for funds, else the chosen one. */
export function entryCurrency(values: EntryValues, ticker: string | null): Currency | null {
  const fields = KIND_FIELDS[values.kind]
  if (fields.currency === 'ticker') return tickerCurrency(ticker)
  if (fields.currency === 'cny') return 'CNY'
  if (fields.currency === 'choose') return values.currency
  return null
}

/** Whether a CNY rate is needed: foreign cash moving in or out (deposit, withdrawal, transfer). */
export function needsFxRate(values: EntryValues): boolean {
  return KIND_FIELDS[values.kind].fxRate && values.currency !== 'CNY'
}

/** A new text in 标的 (`ticker` is what it resolves to): a price filled from another ticker's quote goes. */
export function withTicker(values: EntryValues, text: string, ticker: string | null): EntryValues {
  const fill = values.priceFill
  const next = { ...values, ticker: text }
  return fill && fill.ticker !== ticker ? { ...next, price: fill.value === values.price ? '' : values.price, priceFill: null } : next
}

/** Another currency: a rate typed or accepted for the old one never carries over. */
export function withCurrency(values: EntryValues, currency: Currency): EntryValues {
  if (values.currency === currency) return values
  return { ...values, currency, fxRate: '', rateFill: null }
}

// ------------------------------------------------------------------------------------------------
// Validation

export interface EntryContext {
  /** Accounts of the ledger, in order. */
  accounts: string[]
  /** The ticker the 标的 field resolves to. */
  ticker: string | null
  /**
   * Shares of that ticker held in the account on the entry's date (sell, split), by the backend's ledger as of
   * that date; null when none; undefined while that is not known (nothing is checked then: the preview decides).
   */
  held: string | null | undefined
  /** The fee estimate's total; null when there is none (the fee must then be typed). */
  feeEstimate: string | null
  /** What the estimate is based on ("按账户费率估算"); recorded with an estimated fee. */
  feeBasis?: string | null
  /** Where the dialog was opened from; becomes the events' `source` for imports and recognitions. */
  source?: string
}

export type EntryErrors = Partial<Record<string, string>>

function positive(value: string): boolean {
  return isDecimal(value) && decPositive(value)
}

function nonNegative(value: string): boolean {
  return isDecimal(value) && decSign(value) !== -1
}

function validDate(value: string): boolean {
  if (!DATE.test(value)) return false
  const [y, m, d] = value.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
}

/** What is wrong with the input, by field name; empty when it can be previewed. */
export function validateEntry(values: EntryValues, context: EntryContext): EntryErrors {
  const errors: EntryErrors = {}
  const fields = KIND_FIELDS[values.kind]
  const account = values.account.trim()
  if (fields.opening) {
    if (!account) errors.account = '请填写新账户的名称'
    else if (account.length > MAX_NOTE) errors.account = '账户名最多 240 个字符'
    else if (context.accounts.includes(account)) errors.account = '已有同名账户，请换一个名称'
  } else if (!account) {
    errors.account = context.accounts.length ? '请选择账户' : '还没有账户，请先建立期初余额'
  }
  if (!values.date) errors.date = '请填写日期'
  else if (!validDate(values.date)) errors.date = '日期格式应为 YYYY-MM-DD'

  if (fields.ticker === 'required' || (fields.ticker === 'optional' && values.ticker.trim())) {
    if (!values.ticker.trim()) errors.ticker = '请输入标的'
    else if (!context.ticker) errors.ticker = '请从列表中选择标的（可输入代码或名称搜索）。'
    else if (bShareRefusal(context.ticker)) errors.ticker = bShareRefusal(context.ticker)!
  }
  if (fields.trade) {
    if (!values.quantity) errors.quantity = '请输入数量'
    else if (!positive(values.quantity)) errors.quantity = '数量必须大于 0'
    else if (values.kind === 'sell' && typeof context.held === 'string' && decCmp(values.quantity, context.held) === 1) {
      errors.quantity = `超过可卖数量 ${context.held}`
    }
    if (values.kind === 'sell' && context.ticker && !errors.ticker && context.held === null) {
      errors.ticker = '该账户没有这只标的的持仓'
    }
    if (!values.price) errors.price = '请输入成交价格'
    else if (!positive(values.price)) errors.price = '价格必须大于 0'
    if (values.fee) {
      if (!nonNegative(values.fee)) errors.fee = '费用不能为负数'
    } else if (context.feeEstimate === null) {
      errors.fee = '请填写成交单上的费用'
    }
  }
  if (fields.amount) {
    const ok = values.kind === 'fund_value' ? nonNegative(values.amount) : positive(values.amount)
    if (!values.amount) errors.amount = `请填写${fields.amount}`
    else if (!ok) errors.amount = values.kind === 'fund_value' ? '金额不能为负数' : '金额必须大于 0'
  }
  if (needsFxRate(values)) {
    if (!values.fxRate) errors.fxRate = `请填写汇率：1 ${values.currency} = ? CNY`
    else if (!positive(values.fxRate)) errors.fxRate = '汇率必须大于 0'
  }
  if (fields.toAccount) {
    const target = values.toAccount.trim()
    if (!target) errors.toAccount = '请选择转入账户'
    else if (target === account) errors.toAccount = '转入账户不能是转出账户'
    else if (!context.accounts.includes(target)) errors.toAccount = '转入账户不存在'
  }
  if (fields.exchange) {
    if (values.toCurrency === values.currency) errors.toCurrency = '换入币种不能与换出币种相同'
    if (!values.receivedAmount) errors.receivedAmount = '请填写实际到账金额'
    else if (!positive(values.receivedAmount)) errors.receivedAmount = '金额必须大于 0'
  }
  if (fields.ratio) {
    if (!values.ratio) errors.ratio = '请填写比例'
    else if (!positive(values.ratio)) errors.ratio = '比例必须大于 0'
    if (context.ticker && !errors.ticker && context.held === null) errors.ticker = '该账户没有这只标的的持仓'
  }
  if (fields.fundFee && values.fee && !nonNegative(values.fee)) errors.fee = '费用不能为负数'
  if (fields.opening) {
    for (const currency of CURRENCIES) {
      const value = values.openingCash[currency]
      if (value && !isDecimal(value)) errors[`cash.${currency}`] = '请输入数字'
    }
    if (!values.contributed) errors.contributed = '请填写累计净投入'
    else if (!isDecimal(values.contributed)) errors.contributed = '请输入数字'
    if (values.fundValue && !nonNegative(values.fundValue)) errors.fundValue = '金额不能为负数'
    const seen = new Set<string>()
    values.positions.forEach((row, index) => {
      if (!row.ticker.trim() && !row.quantity && !row.cost) return
      const ticker = parseTicker(row.ticker)
      if (!ticker) errors[`positions.${index}.ticker`] = QUALIFIED_TICKER_HINT
      else if (bShareRefusal(ticker)) errors[`positions.${index}.ticker`] = bShareRefusal(ticker)!
      else if (seen.has(ticker)) errors[`positions.${index}.ticker`] = '同一标的只能填写一行'
      else seen.add(ticker)
      if (!positive(row.quantity)) errors[`positions.${index}.quantity`] = '数量必须大于 0'
      if (!row.cost) errors[`positions.${index}.cost`] = '请填写单位成本'
      else if (!nonNegative(row.cost)) errors[`positions.${index}.cost`] = '成本不能为负数'
    })
  }
  const marks = provenanceMarks(values, context).join('')
  const room = marks ? MAX_NOTE - marks.length - 1 : MAX_NOTE
  if (values.note.trim().length > room) {
    errors.note = marks ? `备注最多 ${room} 个字（另有 ${marks.length + 1} 个字的记账依据）` : '备注最多 240 个字'
  }
  return errors
}

// ------------------------------------------------------------------------------------------------
// Events

export type EventInput = Record<string, unknown>

/** The fee that will be booked: what was typed, else the estimate. */
export function feeToBook(values: EntryValues, estimate: string | null): string | null {
  return values.fee || estimate
}

/** "2026-11-16 15:00" in Beijing time; '' for a missing or unreadable time. */
function stamp(iso: string | null | undefined): string {
  if (!iso) return ''
  const moment = new Date(iso)
  if (Number.isNaN(moment.getTime())) return ''
  const parts: Record<string, string> = {}
  const format = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  })
  for (const part of format.formatToParts(moment)) parts[part.type] = part.value
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

function origin(source: string | null, time: string | null): string {
  return [source, stamp(time)].filter(Boolean).join(' ') || '来源不明'
}

/**
 * The booking marks of an entry, without brackets' surroundings: 〔费用为估算：按账户费率估算〕 for a fee left
 * to the estimate, 〔价格取自昨收：模拟行情 2026-11-16 15:00，估算〕 for a price filled from a quote, 〔汇率取自
 * 参考汇率：模拟行情 2026-11-19 10:32〕 for an accepted reference rate (its time says which day's rate it is).
 */
export function provenanceMarks(values: EntryValues, context: Pick<EntryContext, 'feeEstimate' | 'feeBasis'>): string[] {
  const fields = KIND_FIELDS[values.kind]
  const marks: string[] = []
  if (fields.trade && !values.fee && context.feeEstimate !== null) marks.push(`〔费用为估算：${context.feeBasis || '按费率估算'}〕`)
  const price = values.priceFill
  if (fields.trade && price && price.value === values.price) {
    marks.push(`〔价格取自${price.basis === 'price' ? '现价' : '昨收'}：${origin(price.source, price.time)}${price.estimated ? '，估算' : ''}〕`)
  }
  const rate = values.rateFill
  if (needsFxRate(values) && rate && rate.value === values.fxRate) marks.push(`〔汇率取自参考汇率：${origin(rate.source, rate.time)}〕`)
  return marks
}

/** The events to propose (`POST /api/ledger/proposals`). Call after `validateEntry` found nothing. */
export function buildEvents(values: EntryValues, context: EntryContext): EventInput[] {
  const fields = KIND_FIELDS[values.kind]
  const event: EventInput = { kind: values.kind, date: values.date, account: values.account.trim() }
  const ticker = context.ticker
  if (fields.opening) {
    event.cash_balances = Object.fromEntries(CURRENCIES.map((currency) => [currency, values.openingCash[currency] || '0']))
    event.contributed_cny = values.contributed
    event.fund_cny = values.fundValue || '0'
    event.positions = values.positions
      .filter((row) => row.ticker.trim())
      .map((row) => ({ ticker: parseTicker(row.ticker), quantity: row.quantity, cost_price: row.cost }))
  }
  if (ticker && fields.ticker) {
    event.ticker = ticker
    if (values.name.trim() && values.name.trim() !== ticker) event.name = values.name.trim()
  }
  if (fields.trade) {
    event.currency = tickerCurrency(ticker)
    event.quantity = values.quantity
    event.price = values.price
    event.fee = feeToBook(values, context.feeEstimate) ?? '0'
  }
  if (fields.amount) {
    event.currency = entryCurrency(values, ticker)
    event.amount = values.amount
  }
  if (needsFxRate(values)) event.fx_rate = values.fxRate
  if (fields.toAccount) event.to_account = values.toAccount.trim()
  if (fields.exchange) {
    event.to_currency = values.toCurrency
    event.received_amount = values.receivedAmount
  }
  if (fields.ratio) event.ratio = values.ratio
  if (fields.fundFee) event.fee = values.fee || '0'
  if (fields.margin && values.allowMargin) event.allow_margin = true
  const note = [values.note.trim(), provenanceMarks(values, context).join('')].filter(Boolean).join(' ')
  if (note) event.note = note
  if (context.source) event.source = context.source
  return [event]
}

// ------------------------------------------------------------------------------------------------
// The cash line

export interface CashEffect {
  currency: Currency
  /** Quantity × price, for trades. */
  turnover: string | null
  /** The change to the cash balance; null until the amounts are known. */
  delta: string | null
  /** null while the balance on the entry's date is not known. */
  before: string | null
  after: string | null
  /** The balance would go below zero. */
  short: boolean
  /** Also received, for 换汇: the amount in the other currency. */
  received?: { currency: Currency; before: string | null; after: string | null }
  /** The fee is not known yet, so `delta` leaves it out. */
  feeUnknown: boolean
}

/**
 * Cash before and after the entry in the account's own currency, for display. `cash` is the account's
 * balances on the entry's date (a missing currency is 0, as in the ledger; undefined or null: not known, so
 * there is no before, after or shortfall); `fee` the fee that would be booked.
 */
export function cashEffect(
  values: EntryValues, ticker: string | null, cash: Record<string, string> | null | undefined, fee: string | null,
): CashEffect | null {
  const currency = entryCurrency(values, ticker)
  if (!currency || values.kind === 'split' || values.kind === 'fund_value' || values.kind === 'opening') return null
  const before = cash ? cash[currency] ?? '0' : null
  let turnover: string | null = null
  let delta: string | null = null
  let feeUnknown = false
  const amount = positive(values.amount) ? values.amount : null
  switch (values.kind) {
    case 'buy':
    case 'sell': {
      turnover = positive(values.quantity) && positive(values.price) ? decMul(values.quantity, values.price) : null
      const charge = fee && nonNegative(fee) ? fee : '0'
      feeUnknown = !(fee && nonNegative(fee))
      if (turnover) delta = values.kind === 'buy' ? decNeg(decAdd(turnover, charge)) : decSub(turnover, charge)
      break
    }
    case 'deposit':
    case 'dividend':
    case 'interest':
      delta = amount
      break
    case 'withdrawal':
    case 'fee':
    case 'transfer':
    case 'fx':
      delta = amount && decNeg(amount)
      break
    case 'fund_buy':
    case 'fund_sell': {
      const charge = values.fee && nonNegative(values.fee) ? values.fee : '0'
      if (amount) delta = values.kind === 'fund_buy' ? decNeg(decAdd(amount, charge)) : decSub(amount, charge)
      break
    }
    default:
      break
  }
  const after = delta === null || before === null ? null : decAdd(before, delta)
  const effect: CashEffect = {
    currency, turnover, delta, before, after, feeUnknown,
    short: after !== null && delta !== null && decSign(delta) === -1 && decSign(after) === -1,
  }
  if (values.kind === 'fx' && values.toCurrency !== currency) {
    const received = positive(values.receivedAmount) ? values.receivedAmount : null
    const other = cash ? cash[values.toCurrency] ?? '0' : null
    effect.received = { currency: values.toCurrency, before: other, after: received && other !== null ? decAdd(other, received) : null }
  }
  return effect
}
