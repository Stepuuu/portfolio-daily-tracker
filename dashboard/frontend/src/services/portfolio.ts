import api from './api'
import type { Portfolio, PortfolioSummary } from '@/types'

export const portfolioService = {
  // 获取持仓
  async getPortfolio(): Promise<Portfolio> {
    const response = await api.get<Portfolio>('/portfolio')
    return response.data
  },

  // 获取实时持仓（先刷新行情再返回）
  async getLivePortfolio(): Promise<Portfolio> {
    const response = await api.get<Portfolio>('/portfolio/live')
    return response.data
  },

  // 获取持仓摘要
  async getSummary(): Promise<PortfolioSummary> {
    const response = await api.get<PortfolioSummary>('/portfolio/summary')
    return response.data
  },

  // 添加持仓
  async addPosition(data: {
    symbol: string
    name: string
    quantity: number
    cost_price: number
    market?: string
  }) {
    const response = await api.post('/portfolio/add', {
      ...data,
      market: data.market || 'a_share',
    })
    return response.data
  },

  // 更新持仓
  async updatePosition(symbol: string, data: { quantity?: number; cost_price?: number }) {
    const response = await api.put(`/portfolio/${symbol}`, data)
    return response.data
  },

  // 删除持仓
  async removePosition(symbol: string) {
    const response = await api.delete(`/portfolio/${symbol}`)
    return response.data
  },

  // 刷新持仓价格
  async refresh() {
    const response = await api.post('/portfolio/refresh')
    return response.data
  },
}
