// Market sessions, quote sources and the polling rhythm of the Today view. Pure TypeScript (relative
// `.ts` imports only) so node tests can load it.
import { fmtDate } from '../../lib/format.ts'
import type { Market, MarketSession, TodayPayload, TodaySession } from './types.ts'

/** Times on the page are Beijing time, as the backend sends them. */
export const DISPLAY_ZONE = 'Asia/Shanghai'

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/

/** "2026-11-17" when `value` is a calendar date written that way; null otherwise (null, 不可用, junk). */
export function isoDay(value: string | null | undefined): string | null {
  return typeof value === 'string' && ISO_DAY.test(value) ? value : null
}

/**
 * Today's date in Beijing from the Today payload (its `as_of`, the server's "now"). Null before the first
 * answer and when that time cannot be read: then nothing may be called "today's" (alerts, due notes).
 */
export function snapshotDate(payload: Pick<TodayPayload, 'as_of'> | undefined): string | null {
  return payload ? isoDay(fmtDate(payload.as_of, { timeZone: DISPLAY_ZONE })) : null
}

export const MARKETS: Market[] = ['CN', 'HK', 'US']
export const MARKET_LABEL: Record<Market, string> = { CN: 'A股', HK: '港股', US: '美股' }
const MARKET_ZONE: Record<Market, string> = { CN: 'Asia/Shanghai', HK: 'Asia/Hong_Kong', US: 'America/New_York' }
const EXCHANGE_MARKET: Record<string, Market> = { SHA: 'CN', SHE: 'CN', HKG: 'HK', NASDAQ: 'US', NYSE: 'US' }

/** The market of a ledger ticker (`SHA:600519` → CN); null for indexes and unknown prefixes. */
export function marketOf(ticker: string | null | undefined): Market | null {
  const prefix = typeof ticker === 'string' ? ticker.split(':', 1)[0].trim().toUpperCase() : ''
  return EXCHANGE_MARKET[prefix] ?? null
}

/** Polls every 5 s while a market the portfolio trades in is open, every 60 s otherwise. */
export const POLL_IN_SESSION_MS = 5_000
export const POLL_OUT_OF_SESSION_MS = 60_000

const MOVING_PHASES = new Set(['call_auction', 'morning', 'afternoon', 'open'])

/** Prices move: continuous trading, or the A-share opening auction. */
export function isMoving(session: MarketSession | undefined): boolean {
  return Boolean(session && session.is_trading_day && MOVING_PHASES.has(session.phase))
}

function marketSession(session: TodaySession | undefined, market: Market): MarketSession | undefined {
  if (!session) return undefined
  return session.markets?.[market] ?? (session.market === market ? session : undefined)
}

/** Markets of the open positions and of today's sold-out rows, in CN, HK, US order. */
export function heldMarkets(payload: Pick<TodayPayload, 'accounts'> | undefined): Market[] {
  const found = new Set<Market>()
  for (const account of payload?.accounts ?? []) {
    for (const row of [...account.positions, ...account.closed_today]) {
      const market = marketOf(row.ticker)
      if (market) found.add(market)
    }
  }
  return MARKETS.filter((market) => found.has(market))
}

/** The markets the page talks about: the ones held, or the A-share market when nothing is held. */
export function relevantMarkets(payload: Pick<TodayPayload, 'accounts'> | undefined): Market[] {
  const held = heldMarkets(payload)
  return held.length ? held : ['CN']
}

/** How long to wait before the next `/api/today` poll. */
export function pollIntervalMs(payload: Pick<TodayPayload, 'accounts' | 'session'> | undefined): number {
  if (!payload) return POLL_OUT_OF_SESSION_MS
  const moving = relevantMarkets(payload).some((market) => isMoving(marketSession(payload.session, market)))
  return moving ? POLL_IN_SESSION_MS : POLL_OUT_OF_SESSION_MS
}

/** "盘中", "午间休市", "已收盘", "休市" (not a trading day) … for one market. */
export function phaseLabel(session: MarketSession, market: Market): string {
  if (!session.is_trading_day) return '休市'
  switch (session.phase) {
    case 'pre_open': return '未开盘'
    case 'call_auction': return '集合竞价'
    case 'morning':
    case 'afternoon':
    case 'open': return '盘中'
    case 'lunch_break': return '午间休市'
    default: {
      // HK and US say only "closed": before the open when the next open is on this trading day.
      if (market !== 'CN' && session.next_open && fmtDate(session.next_open, { timeZone: MARKET_ZONE[market] }) === session.trading_day) {
        return '未开盘'
      }
      return '已收盘'
    }
  }
}

export interface MarketState {
  market: Market
  label: string
  phase: string
  moving: boolean
  /** The phase was decided by weekday alone (`calendar_confidence: 'weekday_only'`): it may be a holiday. */
  uncertain: boolean
  /** Why, for a tooltip; null when the calendar is known. */
  reason: string | null
}

/** The amber qualifier after a phase decided by weekday alone. */
export const WEEKDAY_ONLY_QUALIFIER = '按工作日推算'

function uncertaintyReason(label: string): string {
  return `没有${label}这一年的交易所假期表，开市与否按工作日推算，可能遇到休市`
}

/** One entry per relevant market, for the session line and the header chip. */
export function marketStates(payload: Pick<TodayPayload, 'accounts' | 'session'> | undefined): MarketState[] {
  if (!payload?.session) return []
  return relevantMarkets(payload).flatMap((market) => {
    const session = marketSession(payload.session, market)
    if (!session) return []
    const label = MARKET_LABEL[market]
    const uncertain = session.calendar_confidence === 'weekday_only'
    return [{
      market, label, phase: phaseLabel(session, market), moving: isMoving(session), uncertain,
      reason: uncertain ? uncertaintyReason(label) : null,
    }]
  })
}

export interface SessionChip {
  text: string
  moving: boolean
  /** Some market the chip names has its phase from weekdays only: show `qualifier` in amber with `title`. */
  uncertain: boolean
  qualifier: string | null
  title: string | null
}

function chip(text: string, moving: boolean, named: MarketState[]): SessionChip {
  const unsure = named.filter((state) => state.uncertain)
  return {
    text, moving, uncertain: unsure.length > 0,
    qualifier: unsure.length ? WEEKDAY_ONLY_QUALIFIER : null,
    title: unsure.length ? unsure.map((state) => state.reason).join('；') : null,
  }
}

/**
 * The header chip: the markets trading now ("A股·港股 盘中"), else the first relevant market's state. A
 * phase decided by weekday alone keeps that uncertainty (`qualifier`, amber), never an unqualified 盘中.
 */
export function sessionChip(payload: Pick<TodayPayload, 'accounts' | 'session'> | undefined): SessionChip | null {
  const states = marketStates(payload)
  if (!states.length) return null
  const moving = states.filter((state) => state.moving)
  if (moving.length) {
    const phases = new Set(moving.map((state) => state.phase))
    if (phases.size === 1) return chip(`${moving.map((state) => state.label).join('·')} ${moving[0].phase}`, true, moving)
    return chip(moving.map((state) => `${state.label} ${state.phase}`).join(' · '), true, moving)
  }
  return chip(`${states[0].label} ${states[0].phase}`, false, [states[0]])
}

const SOURCE_LABEL: Record<string, string> = { eastmoney: '东财', tencent: '腾讯', yahoo: '雅虎', fake: '模拟行情', none: '无行情源' }

/** "东财", "东财+腾讯+雅虎", "模拟行情": a quote source name as the page shows it. */
export function sourceLabel(source: string | null | undefined): string {
  if (!source) return ''
  return source.split('+').map((part) => SOURCE_LABEL[part.trim()] ?? part.trim()).join('+')
}
