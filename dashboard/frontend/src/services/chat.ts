import api, { getApiErrorMessage } from './api'
import type { ChatResponse } from '@/types'
import { readChatStream, ChatStreamCancelledError, ChatStreamTimeoutError } from './chatStream'

const CHAT_REQUEST_TIMEOUT_MS = 120000

export const chatService = {
  // 发送消息（带结构化数据提取）
  async sendMessage(message: string, extractData = true): Promise<ChatResponse> {
    const response = await api.post<ChatResponse>('/chat/message', {
      message,
      stream: false,
      extract_data: extractData,
    }, {
      timeout: CHAT_REQUEST_TIMEOUT_MS,
    })
    return response.data
  },

  // 流式发送消息
  // `options.signal` cancels the reply (rejects with ChatStreamCancelledError); a reply that goes
  // silent for `options.inactivityTimeoutMs` rejects with ChatStreamTimeoutError instead of
  // waiting forever. Text passed to onChunk before either stays with the caller.
  async streamMessage(
    message: string,
    onChunk: (chunk: string) => void,
    onDone: () => void,
    options: { signal?: AbortSignal; inactivityTimeoutMs?: number } = {}
  ): Promise<void> {
    await readChatStream(
      '/api/chat/message',
      { message, stream: true, extract_data: false },
      onChunk,
      options
    )
    onDone()
  },

  // 带图片发送消息
  async sendMessageWithImage(
    message: string,
    imageFiles: File[],
    extractData = true
  ): Promise<ChatResponse> {
    const formData = new FormData()
    formData.append('message', message)
    imageFiles.forEach(file => {
      formData.append('images', file)
    })
    formData.append('extract_data', String(extractData))

    const response = await api.post<ChatResponse>('/chat/message-with-image', formData, {
      timeout: CHAT_REQUEST_TIMEOUT_MS,
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })
    return response.data
  },

  // 对流式返回后的文本做结构化提取
  async extractResponse(message: string, responseText: string): Promise<ChatResponse> {
    const response = await api.post<ChatResponse>('/chat/extract', {
      message,
      response: responseText,
    }, {
      timeout: CHAT_REQUEST_TIMEOUT_MS,
    })
    return response.data
  },

  // 获取对话历史
  async getHistory() {
    const response = await api.get('/chat/history')
    return response.data.history
  },

  // 清空对话历史（开始新对话）
  async clearHistory() {
    const response = await api.delete('/chat/history')
    return response.data
  },

  // 获取所有对话记录列表
  async getConversations() {
    const response = await api.get('/chat/conversations')
    return response.data.conversations
  },

  // 获取指定日期的对话记录
  async getConversationByDate(date: string) {
    const response = await api.get(`/chat/conversations/${date}`)
    return response.data
  },

  // 加载指定对话到当前会话中
  async loadConversation(date: string) {
    const response = await api.post(`/chat/conversations/${date}/load`)
    return response.data
  },
}

export { getApiErrorMessage, ChatStreamCancelledError, ChatStreamTimeoutError }
