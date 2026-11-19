// Instrument search on the client: the result type of `GET /api/instruments/search`, how a result is
// shown, the order of the choices in the list, what Enter and leaving the field may take, the exchange
// inference for what was typed, and which tickers are one listing.
//
// The inference mirrors the shared rule in dashboard/core/importers/codes.py (and
// core/market/search.py `infer`): the backend decides, this copy only lets the field react before the
// answer arrives (normalising `sha:600519`, resolving a complete `EXCH:CODE` when the user tabs away, and
// keeping exact codes usable when the search request fails). Listing identity is the backend's
// (`identity` on Today rows, watchlist items and alert rules, from core/market/search.py
// `identity_key`); `identityKey` here is its exact copy, used only where a payload carries none, and
// tests/fixtures/instrument_identity.json holds the vectors both must agree on. Pure TypeScript, no `@/`
// imports, so node tests can load it.

export type InstrumentKind = 'stock' | 'etf' | 'fund' | 'bond' | 'index'

export interface InstrumentResult {
  /** Ledger ticker such as SHA:600519; null only for an unsupported code with no ledger prefix. */
  ticker: string | null
  name: string | null
  exchange: string | null
  currency: string | null
  kind: InstrumentKind
  supported: boolean
  /** Why it is unsupported (or, with `needs_confirmation`, what to check), in Chinese. */
  reason: string | null
  /** A letter symbol that may be NASDAQ or NYSE (pick once, remembered), or a code to check. */
  needs_confirmation: boolean
  in_holdings: boolean
  in_watchlist: boolean
  matched_by: 'holdings' | 'watchlist' | 'alias' | 'index' | 'input'
}

export interface InstrumentSearch {
  query: string
  results: InstrumentResult[]
  source: string
  as_of: string | null
  index_available: boolean
  index_reason: string | null
}

// ---------------------------------------------------------------------------------------------
// Labels

const EXCHANGE_LABEL: Record<string, string> = {
  SHA: '上交所', SHE: '深交所', HKG: '港股', NASDAQ: 'NASDAQ', NYSE: 'NYSE', IDX: '指数', BSE: '北交所',
}
const KIND_LABEL: Partial<Record<InstrumentKind, string>> = { etf: 'ETF', fund: '基金', bond: '债券', index: '指数' }
const CURRENCY: Record<string, string> = { SHA: 'CNY', SHE: 'CNY', HKG: 'HKD', NASDAQ: 'USD', NYSE: 'USD' }

/** "上交所", "港股", "NASDAQ"; an unknown exchange is returned as it is. */
export function exchangeLabel(exchange: string | null | undefined): string {
  return exchange ? EXCHANGE_LABEL[exchange] ?? exchange : ''
}

/** "ETF", "基金", "债券", "指数"; empty for stocks. */
export function kindLabel(kind: InstrumentKind | null | undefined): string {
  return kind ? KIND_LABEL[kind] ?? '' : ''
}

/** The code part of a ticker: "SHA:600519" → "600519". */
export function codeOf(ticker: string | null | undefined): string {
  if (!ticker) return ''
  const index = ticker.indexOf(':')
  return index >= 0 ? ticker.slice(index + 1) : ticker
}

export interface ResultDisplay {
  /** The name, else the code, else what was typed. */
  title: string
  /** "600519 · 上交所 · CNY". */
  meta: string
  /** Quiet tags: 持仓, 自选, and the kind when it is not a stock. */
  tags: string[]
  /** Unsupported: the reason. Needs checking: what to check. */
  note: string | null
  disabled: boolean
}

/** How a search result reads in the list: `名称 代码 · 市场 · 币种` with quiet tags. */
export function describeResult(result: InstrumentResult, query = ''): ResultDisplay {
  const code = codeOf(result.ticker)
  const title = result.name || code || query.trim().toUpperCase() || '未知代码'
  const meta = [result.name ? code : '', exchangeLabel(result.exchange), result.currency ?? ''].filter(Boolean).join(' · ')
  const tags = [
    result.in_holdings ? '持仓' : '',
    result.in_watchlist ? '自选' : '',
    kindLabel(result.kind),
  ].filter(Boolean)
  const disabled = !result.supported || !result.ticker
  return { title, meta, tags, note: disabled || result.needs_confirmation ? result.reason : null, disabled }
}

/** "贵州茅台 SHA:600519", for toasts and the assistant. */
export function instrumentLabel(ticker: string, name?: string | null): string {
  return name && name !== ticker ? `${name} ${ticker}` : ticker
}

// ---------------------------------------------------------------------------------------------
// Tickers

const QUALIFIED = /^([A-Z]+):(.+)$/
const US_SYMBOL = /^[A-Z][A-Z0-9.-]{0,14}$/

/** NFKC-like cleanup of what was typed: full-width to ASCII, no spaces, upper case. */
export function normalizeQuery(text: string): string {
  return (text ?? '')
    .replace(/[！-～]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xfee0))
    .replace(/\s+/g, '')
    .slice(0, 40)
}

// ---------------------------------------------------------------------------------------------
// Listing identity (exact copy of core/market/search.py `identity_key`)

/** Python's `str.strip()` whitespace (`str.isspace`), which differs from `String.prototype.trim`. */
const PY_SPACE = '[\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]'
const PY_STRIP = new RegExp(`^${PY_SPACE}+|${PY_SPACE}+$`, 'g')
/** core/market/instruments.py `_TICKER`, matched in full. */
const PARSED = /^([A-Z]+):([A-Z0-9.-]{1,20})$/
const LEDGER_EXCHANGES = ['SHA', 'SHE', 'HKG', 'NASDAQ', 'NYSE']

/**
 * What makes two tickers one listing, exactly as the backend's `identity_key`: Hong Kong codes by their
 * number (HKG:700 = HKG:00700), US symbols with a dot for a dash (NYSE:BRK-B = NYSE:BRK.B), anything
 * else as the canonical upper-case ticker; text that is no ticker comes back as it was given. Use the
 * payload's `identity` (`identityOf`) when it has one: this copy is the fallback.
 */
export function identityKey(ticker: string | null | undefined): string | null {
  if (!ticker) return null
  const text = ticker.replace(PY_STRIP, '').toUpperCase()
  const parsed = PARSED.exec(text)
  if (!parsed) return ticker
  const [, exchange, code] = parsed
  if (exchange === 'IDX') return INDEX_CODES.includes(code) ? text : ticker
  if (!LEDGER_EXCHANGES.includes(exchange)) return ticker
  if (exchange === 'NASDAQ' || exchange === 'NYSE') return `${exchange}:${code.replace(/-/g, '.')}`
  if (exchange === 'HKG' && /^\d{1,5}$/.test(code) && Number(code) !== 0) return `HKG:${String(Number(code)).padStart(5, '0')}`
  return text
}

/** Something that names a listing: a ticker and, from the backend, its identity. */
export interface Identified {
  ticker: string | null
  identity?: string | null
}

/** The listing identity of a payload item: the backend's `identity`, else the same rule applied here. */
export function identityOf(item: Identified): string | null {
  return item.identity || identityKey(item.ticker)
}

/** The same listing (any spelling): HKG:0700 = hkg:00700, NYSE:BRK-B = NYSE:BRK.B. */
export function sameTicker(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false
  const x = a.trim().toUpperCase()
  const y = b.trim().toUpperCase()
  return x === y || identityKey(x) === identityKey(y)
}

/** Whether two payload items are one listing, by the backend's identity where they carry it. */
export function sameListing(a: Identified, b: Identified): boolean {
  const x = identityOf(a)
  return x !== null && x === identityOf(b)
}

/** A typed `exch:code` in the canonical upper-case form, or null when it is not one. */
export function qualifiedInput(text: string): string | null {
  const raw = normalizeQuery(text).toUpperCase()
  return QUALIFIED.test(raw) ? raw : null
}

// ---------------------------------------------------------------------------------------------
// Exchange inference (mirror of core/importers/codes.py)

export interface Inferred {
  ticker: string | null
  exchange: string | null
  currency: string | null
  kind: InstrumentKind
  supported: boolean
  reason: string | null
  /** Tickers to choose from (a letter symbol: NASDAQ or NYSE). */
  choices: string[]
}

const A_SHARE: [string, 'SHA' | 'SHE', InstrumentKind][] = [
  ['60', 'SHA', 'stock'], ['68', 'SHA', 'stock'], ['50', 'SHA', 'fund'], ['51', 'SHA', 'etf'], ['52', 'SHA', 'etf'],
  ['53', 'SHA', 'etf'], ['56', 'SHA', 'etf'], ['58', 'SHA', 'etf'], ['11', 'SHA', 'bond'],
  ['00', 'SHE', 'stock'], ['30', 'SHE', 'stock'], ['15', 'SHE', 'etf'], ['16', 'SHE', 'fund'], ['18', 'SHE', 'fund'],
  ['12', 'SHE', 'bond'],
]
const REPO = ['204', '1318']
const BSE = ['92', '4', '8']
const B_SHARE = ['900', '200', '201']
const INDEX_CODES = ['000001', '000016', '000300', '000688', '000852', '000905', '399001', '399006', 'HSI', 'HSTECH', 'IXIC', 'DJIA', 'SPX']
const INDEX_CURRENCY: Record<string, string> = { HSI: 'HKD', HSTECH: 'HKD', IXIC: 'USD', DJIA: 'USD', SPX: 'USD' }

const starts = (code: string, prefixes: string[]) => prefixes.some((prefix) => code.startsWith(prefix))

function result(ticker: string | null, exchange: string | null, kind: InstrumentKind, supported = true, reason: string | null = null): Inferred {
  return { ticker, exchange, currency: exchange ? CURRENCY[exchange] ?? null : null, kind, supported, reason, choices: [] }
}

function unsupported(reason: string, exchange: string | null = null, kind: InstrumentKind = 'stock'): Inferred {
  return { ...result(null, exchange, kind, false, reason), currency: exchange === 'BSE' ? 'CNY' : null }
}

/** A six-digit mainland code, optionally with the exchange it was typed with. */
function aShare(code: string, exchange?: 'SHA' | 'SHE'): Inferred {
  if (!exchange && code.startsWith('39')) {
    return { ...result(`SHE:${code}`, 'SHE', 'index', false, `代码 ${code} 在深交所属于指数编号段（39xxxx），不是可交易的证券`) }
  }
  if (starts(code, REPO)) return { ...unsupported('逆回购代码不是上市证券，导入交割单时会自动处理。'), currency: 'CNY' }
  if (starts(code, BSE)) return unsupported('北交所证券：账本暂不支持北交所。', 'BSE')
  if (starts(code, B_SHARE)) return unsupported('B 股：账本暂不支持 B 股（外币计价）。')
  if (code.startsWith('7')) return unsupported(`${code} 是申购或配售代码，不是上市证券代码。请选择对应的正式代码。`)
  const match = A_SHARE.find(([prefix]) => code.startsWith(prefix))
  if (!match) return unsupported(`无法从代码 ${code} 判断交易所。`)
  const [, right, kind] = match
  if (exchange && exchange !== right) {
    return result(`${exchange}:${code}`, exchange, kind, false, `代码 ${code} 属于${exchangeLabel(right)}，应写作 ${right}:${code}。`)
  }
  return result(`${right}:${code}`, right, kind)
}

function hongKong(digits: string, known: string[]): Inferred {
  const number = Number(digits)
  if (!Number.isInteger(number) || number === 0) return unsupported('港股代码必须是 1 到 5 位数字。')
  const reused = known.find((ticker) => /^HKG:\d+$/.test(ticker) && Number(ticker.slice(4)) === number)
  return result(reused ?? `HKG:${String(number).padStart(5, '0')}`, 'HKG', 'stock')
}

function usSymbol(symbol: string, known: string[]): Inferred {
  const candidates = [`NASDAQ:${symbol}`, `NYSE:${symbol}`]
  const remembered = known.find((ticker) => candidates.includes(ticker))
  if (remembered) return result(remembered, remembered.split(':')[0], 'stock')
  return { ...result(null, null, 'stock', true, '美股代码需要选择交易所（NASDAQ 或 NYSE），选择后会记住。'), currency: 'USD', choices: candidates }
}

/**
 * What the typed text resolves to without any list, or null when it does not look like a code (a
 * name). `known` are tickers the ledger or the watchlist already use (Hong Kong form, US exchange).
 */
export function inferInstrument(text: string, known: string[] = []): Inferred | null {
  const raw = normalizeQuery(text).toUpperCase()
  if (!raw) return null
  const qualified = QUALIFIED.exec(raw)
  if (qualified) {
    const [, exchange, code] = qualified
    if (exchange === 'IDX') {
      return INDEX_CODES.includes(code)
        ? { ...result(`IDX:${code}`, 'IDX', 'index'), currency: INDEX_CURRENCY[code] ?? 'CNY' }
        : unsupported(`未知的指数代码。可用：${INDEX_CODES.map((item) => `IDX:${item}`).join('、')}`, 'IDX', 'index')
    }
    if (exchange === 'SHA' || exchange === 'SHE') {
      if (!/^\d{6}$/.test(code)) return unsupported('A 股代码必须是 6 位数字。')
      if (exchange === 'SHA' && code.startsWith('000')) {
        return result(raw, 'SHA', 'index', false, `代码 ${code} 在上交所属于指数编号段（000xxx），不是可交易的证券${INDEX_CODES.includes(code) ? `；如要查看指数请写作 IDX:${code}` : ''}`)
      }
      if (exchange === 'SHE' && code.startsWith('39')) {
        return result(raw, 'SHE', 'index', false, `代码 ${code} 在深交所属于指数编号段（39xxxx），不是可交易的证券${INDEX_CODES.includes(code) ? `；如要查看指数请写作 IDX:${code}` : ''}`)
      }
      return aShare(code, exchange)
    }
    if (exchange === 'HKG') return /^\d{1,5}$/.test(code) ? hongKong(code, known) : unsupported('港股代码必须是 1 到 5 位数字。')
    if (exchange === 'NASDAQ' || exchange === 'NYSE') {
      return US_SYMBOL.test(code) ? result(raw, exchange, 'stock') : unsupported('美股代码应以字母开头，例如 NASDAQ:AAPL。')
    }
    return unsupported('代码格式不正确。请使用“交易所:代码”，例如 SHA:600519、SHE:000001、HKG:00700、NASDAQ:AAPL。')
  }
  if (/^\d{6}$/.test(raw)) return aShare(raw)
  const suffixed = /^(\d{1,6})\.(SH|SS|SZ|HK|BJ)$/.exec(raw) ?? /^(SH|SZ|HK|BJ)(\d{1,6})$/.exec(raw)
  if (suffixed) {
    const [digits, market] = /^\d/.test(suffixed[1]) ? [suffixed[1], suffixed[2]] : [suffixed[2], suffixed[1]]
    if (market === 'BJ') return unsupported('北交所证券：账本暂不支持北交所。', 'BSE')
    // core/importers/codes.py: a suffixed Hong Kong code of six digits is not taken (HKG:100000 is refused).
    if (market === 'HK') return digits.length > 5 ? unsupported(`${raw}：港股代码是 1 到 5 位数字。请核对或选择正式代码。`, 'HKG') : hongKong(digits, known)
    return aShare(digits.padStart(6, '0'), market === 'SZ' ? 'SHE' : 'SHA')
  }
  if (/^\d{1,5}$/.test(raw)) return hongKong(raw, known)
  if (US_SYMBOL.test(raw)) return usSymbol(raw, known)
  return null
}

/** Search results for an inferred code, for when the search request fails (no names, no tags). */
export function inferredResults(text: string, known: string[] = []): InstrumentResult[] {
  const inferred = inferInstrument(text, known)
  if (!inferred) return []
  const base = { name: null, in_holdings: false, in_watchlist: false, matched_by: 'input' as const }
  if (inferred.choices.length) {
    return inferred.choices.map((ticker) => ({
      ...base, ticker, exchange: ticker.split(':')[0], currency: 'USD', kind: 'stock' as const, supported: true,
      reason: inferred.reason, needs_confirmation: true,
    }))
  }
  return [{
    ...base, ticker: inferred.ticker, exchange: inferred.exchange, currency: inferred.currency, kind: inferred.kind,
    supported: inferred.supported, reason: inferred.reason, needs_confirmation: false,
  }]
}

// ---------------------------------------------------------------------------------------------
// The list

/** A letter symbol offered on two exchanges: picking one is remembered. */
export function isExchangeChoice(result: InstrumentResult): boolean {
  return result.needs_confirmation && (result.exchange === 'NASDAQ' || result.exchange === 'NYSE') && Boolean(result.ticker)
}

/**
 * Results with exchange choices already made on this page (`symbol → ticker`) applied: the pair
 * becomes the chosen listing, without waiting for the backend to have stored the choice.
 */
export function applyChosenExchanges(results: InstrumentResult[], chosen: Map<string, string>): InstrumentResult[] {
  if (chosen.size === 0) return results
  return results.flatMap((result) => {
    if (!isExchangeChoice(result)) return [result]
    const ticker = chosen.get(codeOf(result.ticker).toUpperCase())
    if (!ticker) return [result]
    return sameTicker(ticker, result.ticker) ? [{ ...result, needs_confirmation: false, reason: null }] : []
  })
}

export type ListItem =
  | { type: 'heading'; key: string; text: string }
  | { type: 'option'; key: string; result: InstrumentResult }

/**
 * The rows of the result list, in the backend's order (holdings, watchlist, remembered choices, the
 * name index): the filter applied, repeats dropped, and the NASDAQ / NYSE pair of a letter symbol under
 * one heading. Codes that cannot be used stay in place, shown disabled with their reason.
 */
export function arrangeResults(results: InstrumentResult[], filter?: (result: InstrumentResult) => boolean): ListItem[] {
  const items: ListItem[] = []
  const seen = new Set<string>()
  const headed = new Set<string>()
  results.forEach((result, index) => {
    if (filter && !filter(result)) return
    const identity = optionKey(result)
    if (seen.has(identity)) return
    seen.add(identity)
    if (isExchangeChoice(result)) {
      const symbol = codeOf(result.ticker)
      if (!headed.has(symbol)) {
        headed.add(symbol)
        items.push({ type: 'heading', key: `h:${symbol}`, text: `${symbol} 在哪个交易所？选择后会记住` })
      }
    }
    items.push({ type: 'option', key: result.ticker ?? `${identity}:${index}`, result })
  })
  return items
}

/** The option rows of `items` (headings are not choosable). */
export function optionsOf(items: ListItem[]): InstrumentResult[] {
  return items.flatMap((item) => (item.type === 'option' ? [item.result] : []))
}

/**
 * The option index after pressing an arrow key (or Home / End) on option `current` (-1: none).
 * Disabled options are visited too, so their reason is read out; they just cannot be picked.
 */
export function moveActive(count: number, current: number, key: string): number {
  if (count === 0) return -1
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  if (key === 'ArrowDown') return current < 0 ? 0 : (current + 1) % count
  if (key === 'ArrowUp') return current < 0 ? count - 1 : (current - 1 + count) % count
  return current
}

/** A key per listing in the list: one for every spelling of a ticker, else the row's own words. */
export function optionKey(result: InstrumentResult): string {
  return result.ticker ? `t:${identityKey(result.ticker)}` : `r:${result.exchange}:${result.name}:${result.reason}`
}

/** A row that can be taken as it is (not disabled, not an exchange or a code still to check). */
export function usableResult(result: InstrumentResult): boolean {
  return result.supported && Boolean(result.ticker) && !result.needs_confirmation
}

/**
 * The usable rows that are exactly what was typed, one per listing: a full ticker (`SHA:600519`, any
 * spelling of a Hong Kong code or a US symbol), or a bare code read on each row's own exchange (`700` on
 * HKG:00700, `000001` on both SHE:000001 and IDX:000001). A letter symbol whose exchange is not known
 * yet, or a code to check, never matches.
 */
export function codeMatches(query: string, results: InstrumentResult[]): InstrumentResult[] {
  const raw = normalizeQuery(query).toUpperCase()
  if (!raw) return []
  const usable = results.filter(usableResult)
  const matches = QUALIFIED.test(raw)
    ? usable.filter((result) => sameTicker(result.ticker, raw))
    : usable.filter((result) => Boolean(result.exchange) && identityKey(`${result.exchange}:${raw}`) === identityKey(result.ticker))
  return [...new Map(matches.map((result) => [identityKey(result.ticker), result])).values()]
}

/** The one usable row that is exactly what was typed, or null (none, or several: ambiguous). */
export function exactMatch(query: string, results: InstrumentResult[]): InstrumentResult | null {
  const matches = codeMatches(query, results)
  return matches.length === 1 ? matches[0] : null
}

/**
 * What typed text may resolve to without a pick (leaving the field; an Enter pressed before the answer
 * arrived): only a complete `EXCH:CODE` that is exactly one usable listing among `options`, the rows the
 * field would show (its filter applied). Anything else stays text.
 */
export function typedChoice(value: string, options: InstrumentResult[]): InstrumentResult | null {
  return qualifiedInput(value) ? exactMatch(value, options) : null
}

export type EnterAction = { kind: 'choose'; result: InstrumentResult } | { kind: 'wait' } | { kind: 'stay' }

export interface EnterInput {
  /** The text in the field. */
  value: string
  /** The highlighted row, from the rows on screen now (null when none is highlighted). */
  active: InstrumentResult | null
  /** The rows on screen answer this text (not the previous one, still shown while typing). */
  fresh: boolean
  /** The rows on screen, the field's filter applied. */
  options: InstrumentResult[]
  /** Enter with nothing highlighted opens the first row (the global search). */
  pickFirst: boolean
}

/**
 * What Enter does. A highlighted row is taken (if it can be); otherwise rows of the previous text are
 * not used (`wait`: the field then takes only `typedChoice` once the answer comes); on this text's rows,
 * the code typed if it is exactly one listing, never one of several (`000001` on SHE:000001 and
 * IDX:000001), else the only usable row, or, in the global search, the first one.
 */
export function enterAction({ value, active, fresh, options, pickFirst }: EnterInput): EnterAction {
  if (active) return active.supported && active.ticker ? { kind: 'choose', result: active } : { kind: 'stay' }
  if (!normalizeQuery(value)) return { kind: 'stay' }
  if (!fresh) return { kind: 'wait' }
  const matches = codeMatches(value, options)
  if (matches.length === 1) return { kind: 'choose', result: matches[0] }
  if (matches.length > 1) return { kind: 'stay' }
  const usable = options.filter(usableResult)
  if (usable.length === 1 || (pickFirst && usable.length > 0)) return { kind: 'choose', result: usable[0] }
  return { kind: 'stay' }
}
