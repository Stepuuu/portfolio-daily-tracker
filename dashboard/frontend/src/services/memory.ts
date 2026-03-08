import api from './api'
import type { UserMemory, UserProfile } from '@/types'

export const memoryService = {
  // 获取完整记忆
  async getMemory(): Promise<UserMemory> {
    const response = await api.get<UserMemory>('/memory')
    return response.data
  },

  // 获取用户画像
  async getProfile(): Promise<UserProfile> {
    const response = await api.get<UserProfile>('/memory/profile')
    return response.data
  },

  // 更新用户画像
  async updateProfile(profile: Partial<UserProfile>) {
    const response = await api.put('/memory/profile', profile)
    return response.data
  },

  // 获取偏好
  async getPreferences() {
    const response = await api.get('/memory/preferences')
    return response.data
  },

  // 添加偏好板块
  async addPreferredSector(sector: string) {
    const response = await api.post('/memory/preferences/sector', { sector })
    return response.data
  },

  // 删除偏好板块
  async removePreferredSector(sector: string) {
    const response = await api.delete('/memory/preferences/sector', { data: { sector } })
    return response.data
  },

  // 添加情绪触发器
  async addEmotionalTrigger(trigger: string) {
    const response = await api.post('/memory/preferences/trigger', { trigger })
    return response.data
  },

  // 删除情绪触发器
  async removeEmotionalTrigger(trigger: string) {
    const response = await api.delete('/memory/preferences/trigger', { data: { trigger } })
    return response.data
  },

  // 获取交易历史
  async getHistory() {
    const response = await api.get('/memory/history')
    return response.data
  },

  // 添加交易教训
  async addLesson(data: {
    description: string
    lesson_type: string
    lesson?: string
    symbol?: string
  }) {
    const response = await api.post('/memory/history/lesson', data)
    return response.data
  },

  // 删除交易教训
  async removeLesson(index: number) {
    const response = await api.delete(`/memory/history/lesson/${index}`)
    return response.data
  },

  // 获取目标
  async getGoals() {
    const response = await api.get('/memory/goals')
    return response.data
  },

  // 添加目标
  async addGoal(goalType: 'short_term' | 'long_term', goal: string) {
    const response = await api.post('/memory/goals', {
      goal_type: goalType,
      goal,
    })
    return response.data
  },

  // 获取记忆上下文
  async getContext(): Promise<string> {
    const response = await api.get<{ context: string }>('/memory/context')
    return response.data.context
  },

  // 重置记忆
  async reset() {
    const response = await api.delete('/memory/reset')
    return response.data
  },
}
