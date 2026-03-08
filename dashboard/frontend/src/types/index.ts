// 持仓相关类型
export interface Position {
  symbol: string
  name: string
  market: 'a_share' | 'hk' | 'us'
  quantity: number
  available_qty: number
  cost_price: number
  current_price: number
  profit: number
  profit_pct: number
  market_value: number
}

export interface Portfolio {
  positions: Position[]
  cash: number
  total_market_value: number
  total_assets: number
  total_profit: number
}

export interface PortfolioSummary {
  total_positions: number
  total_assets: number
  cash: number
  market_value: number
  total_profit: number
  profit_pct: number
  winners_count: number
  losers_count: number
  top_winner: Position | null
  top_loser: Position | null
}

// 行情相关类型
export interface Quote {
  symbol: string
  name: string
  price: number
  open: number
  high: number
  low: number
  prev_close: number
  volume: number
  change: number
  change_pct: number
  amount?: number        // 成交额
  turnover?: number      // 换手率
  pe?: number           // 市盈率
  timestamp?: string    // 更新时间
}

// 对话相关类型
export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
  image?: string  // 为了向后兼容旧的单张图片数据
  images?: string[]  // 支持多张图片
}

export interface ChatResponse {
  response: string
  suggestions: Suggestion[]
  risks: Risk[]
  sentiment: 'bullish' | 'bearish' | 'neutral'
  memory_updates: MemoryUpdate[]
  imported_positions?: number  // 导入的持仓数量
}

// 建议相关类型
export interface Suggestion {
  type: 'buy' | 'sell' | 'hold' | 'reduce' | 'add'
  symbol: string | null
  reason: string
  target_price: number | null
  stop_loss: number | null
  position_size: string | null
  confidence: 'high' | 'medium' | 'low'
}

export interface Risk {
  level: 'high' | 'medium' | 'low'
  type: string
  description: string
  suggestion: string
}

// 记忆相关类型
export interface UserProfile {
  name: string
  experience_level: 'beginner' | 'intermediate' | 'expert'
  trading_style: 'day' | 'swing' | 'position' | 'value'
  risk_tolerance: 'conservative' | 'moderate' | 'aggressive'
  notes: string
}

export interface TradingPreferences {
  preferred_sectors: string[]
  avoid_sectors: string[]
  max_single_position: number
  preferred_holding_period: string
  emotional_triggers: string[]
}

export interface TradingLesson {
  date: string
  type: 'win' | 'loss' | 'mistake' | 'insight'
  symbol: string | null
  description: string
  lesson: string
}

export interface UserGoals {
  short_term: string[]
  long_term: string[]
  monthly_target: number | null
  annual_target: number | null
}

export interface UserMemory {
  profile: UserProfile
  preferences: TradingPreferences
  history: {
    lessons: TradingLesson[]
  }
  goals: UserGoals
}

export interface MemoryUpdate {
  category: string
  content: string
  confidence: number
}

// API 响应类型
export interface ApiResponse<T> {
  data?: T
  error?: string
  message?: string
}
