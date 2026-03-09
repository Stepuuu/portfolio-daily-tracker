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
  profit_pct: number
  weight_in_group: number
}

export interface TrackerGroup {
  cost_basis: number
  positions: TrackerPosition[]
  fund: number
  cash: number
  positions_value: number
  total_value: number
  profit: number
  return_pct: number
}

export interface TrackerSnapshot {
  date: string
  generated_at: string
  fx_rates: Record<string, number>
  groups: Record<string, TrackerGroup>
  summary: {
    total_value: number
    total_cost: number
    total_profit: number
    total_return_pct: number
    prev_date?: string | null
    prev_total_value: number
    daily_change: number
    daily_change_pct: number
    market_daily_change?: number
    market_daily_change_pct?: number
    capital_change?: number
    max_drawdown_pct: number
    month_start_value: number
    month_change: number
    month_market_change?: number
    month_return_pct: number
  }
}

export interface TrackerSummary {
  date: string | null
  total_value: number
  total_cost: number
  total_profit: number
  total_return_pct: number
  daily_change: number
  daily_change_pct: number
  market_daily_change?: number
  market_daily_change_pct?: number
  capital_change?: number
  max_drawdown_pct: number
  month_change?: number
  month_market_change?: number
  month_return_pct: number
  groups: Record<string, {
    total_value: number
    cost_basis: number
    profit: number
    return_pct: number
    positions_count: number
  }>
}

export interface HistoryRow {
  date: string
  total_value: number
  total_cost: number
  total_profit: number
  return_pct: number
  daily_change: number
  daily_change_pct: number
  market_daily_change?: number
  market_daily_change_pct?: number
  capital_change?: number
  max_drawdown_pct: number
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
