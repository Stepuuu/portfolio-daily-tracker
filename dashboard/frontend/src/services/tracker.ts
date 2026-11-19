import api from './api'

// ===== Types =====
export interface TrackerPosition {
  name: string
  ticker: string
  quantity: number
  cost_price: number
  current_price: number
  currency: string
  fx_rate: number
  market_value_cny: number
  cost_value_cny: number
  profit_cny: number
  profit_pct: number | null
  weight_in_group: number
  estimated_reason?: string   // its price is an estimate (Today's 估算)
}

export interface TrackerGroup {
  cost_basis: number
  positions: TrackerPosition[]
  fund: number
  cash: number
  cash_balances?: Record<string, number>
  cash_values_cny?: Record<string, number>
  positions_value: number
  total_value: number
  profit: number
  return_pct: number | null
}

export interface TrackerSnapshot {
  date: string
  generated_at: string
  synthetic?: boolean
  demo?: boolean
  positions_as_of?: string | null
  market_data?: {
    cached_prices?: { ticker: string; snapshot_date: string }[]
    fx_fallbacks?: { currency: string; source: string }[]
  }
  fx_rates: Record<string, number>
  groups: Record<string, TrackerGroup>
  summary: {
    total_value: number
    total_cost: number
    total_profit: number
    total_return_pct: number | null
    prev_date?: string | null
    // null: no earlier day to compare with (the first snapshot)
    prev_total_value: number | null
    daily_change: number | null
    daily_change_pct: number | null
    market_daily_change?: number | null
    market_daily_change_pct?: number | null
    capital_change?: number | null
    max_drawdown_pct: number | null
    month_start_value: number
    month_change: number
    month_market_change?: number
    month_return_pct: number | null
    // A figure of the day rests on an estimated input (Today's 估算), and which
    estimated?: boolean
    estimated_reasons?: string[]
  }
}

export interface TrackerSummary {
  date: string | null
  total_value: number
  total_cost: number
  total_profit: number
  total_return_pct: number | null
  daily_change: number | null
  daily_change_pct: number | null
  market_daily_change?: number | null
  market_daily_change_pct?: number | null
  capital_change?: number | null
  max_drawdown_pct: number | null
  month_change?: number
  month_market_change?: number
  month_return_pct: number | null
  groups: Record<string, {
    total_value: number
    cost_basis: number
    profit: number
    return_pct: number | null
    positions_count: number
  }>
}

export interface HistoryRow {
  date: string
  total_value: number
  total_cost: number
  total_profit: number
  return_pct: number | null
  daily_change: number | null      // null: no earlier day to compare with (the first snapshot)
  daily_change_pct: number | null
  market_daily_change?: number | null
  market_daily_change_pct?: number | null
  capital_change?: number | null
  max_drawdown_pct: number | null
  // From the day's snapshot: it rests on an estimated input (Today's 估算), and which
  estimated?: boolean
  estimated_reasons?: string[]
}

// ===== Service =====
export const trackerService = {
  async getDates(): Promise<{ dates: string[]; count: number }> {
    const r = await api.get('/tracker/dates')
    return r.data
  },

  async getSnapshot(date?: string): Promise<TrackerSnapshot> {
    const r = await api.get('/tracker/snapshot', { params: date ? { date } : {} })
    return r.data
  },

  async getHoldings(date?: string) {
    const r = await api.get('/tracker/holdings', { params: date ? { date } : {} })
    return r.data
  },

  async getHistory(limit = 60): Promise<{ history: HistoryRow[]; count: number }> {
    const r = await api.get('/tracker/history', { params: { limit } })
    return r.data
  },

  async getSummary(): Promise<TrackerSummary> {
    const r = await api.get('/tracker/summary')
    return r.data
  },

  async getConfig() {
    const r = await api.get('/tracker/config')
    return r.data
  },

  async getGroupDetail(groupName: string, date?: string) {
    const r = await api.get(`/tracker/group/${encodeURIComponent(groupName)}`, {
      params: date ? { date } : {}
    })
    return r.data
  },
}
