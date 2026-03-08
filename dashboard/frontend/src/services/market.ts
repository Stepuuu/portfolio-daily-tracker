import api from './api'
import type { Quote } from '@/types'

export const marketService = {
  // 获取单只股票行情
  async getQuote(symbol: string, market = 'a_share'): Promise<Quote> {
    const response = await api.get<Quote>(`/market/quote/${symbol}`, {
      params: { market },
    })
    return response.data
  },

  // 批量获取行情
  async getQuotes(symbols: { symbol: string; market?: string }[]): Promise<Quote[]> {
    const response = await api.post('/market/quotes', {
      symbols: symbols.map((s) => ({
        symbol: s.symbol,
        market: s.market || 'a_share',
      })),
    })
    return response.data.quotes
  },

  // 获取指数行情
  async getIndices() {
    const response = await api.get('/market/indices')
    return response.data.indices
  },

  // 搜索股票
  async searchStocks(keyword: string) {
    const response = await api.get(`/market/search/${keyword}`)
    return response.data.results
  },
}
