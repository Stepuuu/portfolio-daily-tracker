// Fictional data for the UI gallery (/__ui). Names and codes are made up; values are decimal strings,
// as the backend sends them. Nothing here is summed on the client: group totals come "from the server".
import type { Currency, InstrumentKind } from '@/lib/format'

export interface Holding {
  id: string
  account: string
  name: string
  ticker: string
  kind: InstrumentKind | 'cash'
  currency: Currency
  price: string | null
  changePct: string | null
  dayPnl: string | null
  quantity: string | null
  cost: string | null
  marketValue: string | null
  totalPnl: string | null
  totalPnlPct: string | null
  weightPct: string | null
  source: string
  /** Quote state: 实时 / 延迟 / 已过期 hh:mm / 收盘价 / 时间未知 / 不可用. */
  quote: 'live' | 'delayed' | 'stale' | 'close'
  quoteTime: string
}

export const holdings: Holding[] = [
  {
    id: 'h1', account: '主账户', name: '示例科技', ticker: 'SHA:609001', kind: 'stock', currency: 'CNY',
    price: '168.20', changePct: '1.20', dayPnl: '1990.00', quantity: '1000', cost: '150.00', marketValue: '168200.00',
    totalPnl: '18200.00', totalPnlPct: '12.13', weightPct: '27.3', source: '东财', quote: 'live', quoteTime: '10:32:05',
  },
  {
    id: 'h2', account: '主账户', name: '远航能源', ticker: 'SHE:009102', kind: 'stock', currency: 'CNY',
    price: '23.45', changePct: '-0.85', dayPnl: '-201.00', quantity: '1000', cost: '25.10', marketValue: '23450.00',
    totalPnl: '-1650.00', totalPnlPct: '-6.57', weightPct: '3.8', source: '东财', quote: 'live', quoteTime: '10:32:05',
  },
  {
    id: 'h3', account: '主账户', name: '青山药业', ticker: 'SHE:309105', kind: 'stock', currency: 'CNY',
    price: '41.08', changePct: '0.00', dayPnl: '0.00', quantity: '2000', cost: '38.50', marketValue: '82160.00',
    totalPnl: '5160.00', totalPnlPct: '6.70', weightPct: '13.3', source: '东财', quote: 'live', quoteTime: '10:32:05',
  },
  {
    id: 'h4', account: '主账户', name: '示例红利ETF', ticker: 'SHA:519901', kind: 'etf', currency: 'CNY',
    price: '1.234', changePct: '0.33', dayPnl: '40.00', quantity: '10000', cost: '1.180', marketValue: '12340.00',
    totalPnl: '540.00', totalPnlPct: '4.58', weightPct: '2.0', source: '东财', quote: 'delayed', quoteTime: '10:31:40',
  },
  {
    id: 'c1', account: '主账户', name: '现金 CNY', ticker: '', kind: 'cash', currency: 'CNY',
    price: null, changePct: null, dayPnl: null, quantity: null, cost: null, marketValue: '120000.00',
    totalPnl: null, totalPnlPct: null, weightPct: '19.5', source: '', quote: 'live', quoteTime: '',
  },
  {
    id: 'h5', account: '港美账户', name: '示例控股', ticker: 'HKG:09901', kind: 'stock', currency: 'HKD',
    price: null, changePct: null, dayPnl: null, quantity: '200', cost: '380.000', marketValue: null,
    totalPnl: null, totalPnlPct: null, weightPct: null, source: '东财', quote: 'stale', quoteTime: '10:20',
  },
  {
    id: 'h6', account: '港美账户', name: '示例云', ticker: 'NASDAQ:DEMO', kind: 'stock', currency: 'USD',
    price: '189.99', changePct: '-2.15', dayPnl: '-417.00', quantity: '100', cost: '150.00', marketValue: '18999.00',
    totalPnl: '3999.00', totalPnlPct: '26.66', weightPct: '22.0', source: 'Yahoo', quote: 'close', quoteTime: '11-16',
  },
  {
    id: 'c2', account: '港美账户', name: '现金 HKD', ticker: '', kind: 'cash', currency: 'HKD',
    price: null, changePct: null, dayPnl: null, quantity: null, cost: null, marketValue: '5000.00',
    totalPnl: null, totalPnlPct: null, weightPct: '0.7', source: '', quote: 'live', quoteTime: '',
  },
]

/** Headline totals in CNY, as `/api/today` would send them. */
export const totals = {
  totalValue: '616954.45', dayPnl: '-1152.55', dayPnlPct: '-0.19', totalPnl: '51284.45', totalPnlPct: '9.06',
  cash: [['CNY', '120000.00'], ['HKD', '5000.00']] as [Currency, string][],
}

/** Per-account totals in CNY, as `/api/today` would send them (null when a price is missing). */
export const accountTotals: Record<string, { dayPnl: string | null; marketValue: string | null; totalPnl: string | null; missing: number }> = {
  主账户: { dayPnl: '1829.00', marketValue: '406150.00', totalPnl: '22250.00', missing: 0 },
  港美账户: { dayPnl: null, marketValue: null, totalPnl: null, missing: 1 },
}

export interface LedgerEvent {
  id: string
  date: string
  kind: string
  account: string
  name: string
  ticker: string
  quantity: string | null
  price: string | null
  amount: string
  currency: Currency
  fee: string | null
  source: string
  note?: string
}

const NAMES: [string, string, string][] = [
  ['示例科技', 'SHA:609001', '168.20'],
  ['远航能源', 'SHE:009102', '23.45'],
  ['青山药业', 'SHE:309105', '41.08'],
  ['示例红利ETF', 'SHA:519901', '1.234'],
]

const SOURCES = ['交割单导入', '手工', '交割单导入', '助手', '交割单导入', '截图识别']

/** 24 events over the last weeks, newest first; enough rows to scroll past the sticky header. */
export const ledgerEvents: LedgerEvent[] = Array.from({ length: 24 }, (_, index) => {
  const day = 17 - Math.floor(index / 2)
  const date = `2026-11-${String(Math.max(day, 1)).padStart(2, '0')}`
  const [name, ticker, price] = NAMES[index % NAMES.length]
  const sell = index % 5 === 3
  const quantity = String((index % 3 + 1) * (ticker === 'SHA:519901' ? 1000 : 100))
  if (index % 7 === 6) {
    return {
      id: `e${index}`, date, kind: '入金', account: '主账户', name: '', ticker: '', quantity: null, price: null,
      amount: '50000.00', currency: 'CNY', fee: null, source: '手工',
    }
  }
  if (index === 9) {
    return {
      id: `e${index}`, date, kind: '分红', account: '主账户', name: '青山药业', ticker: 'SHE:309105', quantity: null, price: null,
      amount: '1936.00', currency: 'CNY', fee: null, source: '公司行动',
    }
  }
  const turnover = (Number(price) * Number(quantity)).toFixed(2)
  const fee = Math.max(5, Number(turnover) * 0.00025 + Number(turnover) * 0.00001 + (sell ? Number(turnover) * 0.0005 : 0)).toFixed(2)
  return {
    id: `e${index}`, date, kind: sell ? '卖出' : '买入', account: index % 4 === 2 ? '港美账户' : '主账户', name, ticker,
    quantity, price, amount: sell ? turnover : `-${turnover}`, currency: 'CNY', fee, source: SOURCES[index % SOURCES.length],
    note: index === 2 ? '分批建仓第二笔，计划仓位 15%' : undefined,
  }
})
