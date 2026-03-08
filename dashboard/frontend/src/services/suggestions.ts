import api from './api'
import type { Suggestion, Risk } from '@/types'

export const suggestionsService = {
  // 获取最近的建议
  async getSuggestions(): Promise<Suggestion[]> {
    const response = await api.get('/suggestions')
    return response.data.suggestions
  },

  // 获取最近的风险
  async getRisks(): Promise<Risk[]> {
    const response = await api.get('/suggestions/risks')
    return response.data.risks
  },

  // 获取所有洞察
  async getAllInsights(): Promise<{
    suggestions: Suggestion[]
    risks: Risk[]
  }> {
    const response = await api.get('/suggestions/all')
    return response.data
  },

  // 获取摘要
  async getSummary() {
    const response = await api.get('/suggestions/summary')
    return response.data
  },
}
