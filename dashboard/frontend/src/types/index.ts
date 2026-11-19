// Types of the assistant's chat API (`/api/chat`). The v3.2 portfolio and memory types are gone: holdings
// come from the ledger (features/ledger, features/today), memory from features/settings/memory.ts.

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
  image?: string  // 为了向后兼容旧的单张图片数据
  images?: string[]  // 支持多张图片
}

/** What the backend extracts besides the reply. Not shown: suggestions are no longer rendered. */
export interface ChatResponse {
  response: string
  suggestions?: unknown[]
  risks?: unknown[]
  sentiment?: 'bullish' | 'bearish' | 'neutral'
  memory_updates?: MemoryUpdate[]
  imported_positions?: number  // 导入的持仓数量
}

export interface MemoryUpdate {
  category: string
  content: string
  confidence: number
}
