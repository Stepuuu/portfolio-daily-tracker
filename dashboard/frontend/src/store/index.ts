// The assistant's conversation, kept outside the chat panel so it survives closing and reopening the dock.
// (The v3.2 portfolio, suggestion, risk and memory fields are gone: holdings come from the ledger, memory is
// read where it is edited, in 设置 › 交易偏好.)
import { create } from 'zustand'
import {
  beginOp, finishOp, isCurrentOp, initialConversationOp,
  type ConversationOpKind, type ConversationOpState,
} from '@/lib/conversationOps'
import type { ChatMessage } from '@/types'

interface AppState {
  // 对话状态
  messages: ChatMessage[]
  isLoading: boolean
  currentResponse: string
  // Stops the reply that is streaming right now; null when nothing can be stopped.
  // Kept here rather than in the chat panel so it survives leaving and reopening the page.
  cancelStream: (() => void) | null
  // Send / resume / new-conversation share this; see lib/conversationOps.ts. It lives here so it
  // outlives the history modal and the chat panel.
  conversationOp: ConversationOpState

  // 动作
  addMessage: (message: ChatMessage) => void
  setMessages: (messages: ChatMessage[]) => void
  setLoading: (loading: boolean) => void
  setCurrentResponse: (response: string) => void
  setCancelStream: (cancel: (() => void) | null) => void
  appendToCurrentResponse: (chunk: string) => void
  clearMessages: () => void
  // null when another operation is already running.
  beginConversationOp: (kind: ConversationOpKind, opts?: { force?: boolean }) => number | null
  // true when this operation is still the current one and its result may be applied.
  finishConversationOp: (token: number) => boolean
  isConversationOpCurrent: (token: number) => boolean
}

export const useAppStore = create<AppState>((set, get) => ({
  // 初始状态
  messages: [],
  isLoading: false,
  currentResponse: '',
  cancelStream: null,
  conversationOp: initialConversationOp,

  // 动作
  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  setMessages: (messages) => set({ messages }),

  setLoading: (isLoading) => set({ isLoading }),

  setCurrentResponse: (currentResponse) => set({ currentResponse }),

  setCancelStream: (cancelStream) => set({ cancelStream }),

  appendToCurrentResponse: (chunk) =>
    set((state) => ({
      currentResponse: state.currentResponse + chunk,
    })),

  clearMessages: () => set({ messages: [], currentResponse: '' }),

  beginConversationOp: (kind, opts) => {
    const started = beginOp(get().conversationOp, kind, opts)
    if (!started) return null
    set({ conversationOp: started.state })
    return started.token
  },

  finishConversationOp: (token) => {
    const done = finishOp(get().conversationOp, token)
    if (done.apply) set({ conversationOp: done.state })
    return done.apply
  },

  isConversationOpCurrent: (token) => isCurrentOp(get().conversationOp, token),
}))
