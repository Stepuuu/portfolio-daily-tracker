import { create } from 'zustand'
import type { ChatMessage, Suggestion, Risk, Portfolio, UserMemory } from '@/types'

interface AppState {
  // 对话状态
  messages: ChatMessage[]
  isLoading: boolean
  currentResponse: string

  // 建议和风险
  suggestions: Suggestion[]
  risks: Risk[]

  // 持仓
  portfolio: Portfolio | null

  // 用户记忆
  memory: UserMemory | null

  // 动作
  addMessage: (message: ChatMessage) => void
  setMessages: (messages: ChatMessage[]) => void
  setLoading: (loading: boolean) => void
  setCurrentResponse: (response: string) => void
  appendToCurrentResponse: (chunk: string) => void
  setSuggestions: (suggestions: Suggestion[]) => void
  setRisks: (risks: Risk[]) => void
  setPortfolio: (portfolio: Portfolio) => void
  setMemory: (memory: UserMemory) => void
  clearMessages: () => void
}

export const useAppStore = create<AppState>((set) => ({
  // 初始状态
  messages: [],
  isLoading: false,
  currentResponse: '',
  suggestions: [],
  risks: [],
  portfolio: null,
  memory: null,

  // 动作
  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  setMessages: (messages) => set({ messages }),

  setLoading: (isLoading) => set({ isLoading }),

  setCurrentResponse: (currentResponse) => set({ currentResponse }),

  appendToCurrentResponse: (chunk) =>
    set((state) => ({
      currentResponse: state.currentResponse + chunk,
    })),

  setSuggestions: (suggestions) => set({ suggestions }),

  setRisks: (risks) => set({ risks }),

  setPortfolio: (portfolio) => set({ portfolio }),

  setMemory: (memory) => set({ memory }),

  clearMessages: () => set({ messages: [], currentResponse: '' }),
}))
