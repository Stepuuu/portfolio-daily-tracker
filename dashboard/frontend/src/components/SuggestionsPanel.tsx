import {
  TrendingUp,
  TrendingDown,
  Minus,
  AlertTriangle,
  CheckCircle,
  Info,
} from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { portfolioService } from '@/services'
import { useAppStore } from '@/store'
import type { Suggestion, Risk } from '@/types'
import { clsx } from 'clsx'

export default function SuggestionsPanel() {
  const { suggestions, risks } = useAppStore()
  
  const { data: portfolio } = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioService.getPortfolio,
    staleTime: 1000 * 60, // 缓存1分钟
  })
  
  const getStockName = (symbol: string) => {
    if (!portfolio || !portfolio.positions) return ''
    const position = portfolio.positions.find(p => p.symbol === symbol || symbol.includes(p.symbol))
    return position ? position.name : ''
  }

  const getSuggestionIcon = (type: string) => {
    switch (type) {
      case 'buy':
      case 'add':
        return <TrendingUp className="h-5 w-5 text-green-400" />
      case 'sell':
      case 'reduce':
        return <TrendingDown className="h-5 w-5 text-red-400" />
      case 'hold':
      default:
        return <Minus className="h-5 w-5 text-yellow-400" />
    }
  }

  const getSuggestionLabel = (type: string) => {
    switch (type) {
      case 'buy':
        return '买入'
      case 'sell':
        return '卖出'
      case 'hold':
        return '持有'
      case 'add':
        return '加仓'
      case 'reduce':
        return '减仓'
      default:
        return type
    }
  }

  const getConfidenceColor = (confidence: string) => {
    switch (confidence) {
      case 'high':
        return 'text-green-400'
      case 'medium':
        return 'text-yellow-400'
      case 'low':
        return 'text-slate-400'
      default:
        return 'text-slate-400'
    }
  }

  const getRiskIcon = (level: string) => {
    switch (level) {
      case 'high':
        return <AlertTriangle className="h-5 w-5 text-red-400" />
      case 'medium':
        return <Info className="h-5 w-5 text-yellow-400" />
      case 'low':
        return <CheckCircle className="h-5 w-5 text-green-400" />
      default:
        return <Info className="h-5 w-5 text-slate-400" />
    }
  }

  const getRiskBgColor = (level: string) => {
    switch (level) {
      case 'high':
        return 'bg-red-900/20 border-red-800/50'
      case 'medium':
        return 'bg-yellow-900/20 border-yellow-800/50'
      case 'low':
        return 'bg-green-900/20 border-green-800/50'
      default:
        return 'bg-slate-800 border-slate-700'
    }
  }

  const renderSuggestion = (suggestion: Suggestion, index: number) => (
    <div
      key={index}
      className="p-3 bg-slate-700/50 rounded-lg border border-slate-600"
    >
      <div className="flex items-start gap-3">
        {getSuggestionIcon(suggestion.type)}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-100">{getSuggestionLabel(suggestion.type)}</span>
            {suggestion.symbol && (
              <div className="flex items-baseline gap-1.5 px-2 py-0.5 bg-slate-800 rounded">
                <span className="text-sm font-medium text-slate-300">{getStockName(suggestion.symbol) || suggestion.symbol}</span>
                {getStockName(suggestion.symbol) && <span className="text-xs text-slate-500">{suggestion.symbol}</span>}
              </div>
            )}
            <span
              className={clsx(
                'text-xs px-2 py-0.5 rounded',
                getConfidenceColor(suggestion.confidence)
              )}
            >
              {suggestion.confidence === 'high'
                ? '高置信度'
                : suggestion.confidence === 'medium'
                ? '中置信度'
                : '低置信度'}
            </span>
          </div>
          <p className="text-sm text-slate-300 mt-1">{suggestion.reason}</p>
          {(suggestion.target_price || suggestion.stop_loss || suggestion.position_size) && (
            <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-400">
              {suggestion.target_price && (
                <span>目标价: {suggestion.target_price}</span>
              )}
              {suggestion.stop_loss && (
                <span>止损价: {suggestion.stop_loss}</span>
              )}
              {suggestion.position_size && (
                <span>仓位: {suggestion.position_size}</span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )

  const renderRisk = (risk: Risk, index: number) => (
    <div
      key={index}
      className={clsx(
        'p-3 rounded-lg border',
        getRiskBgColor(risk.level)
      )}
    >
      <div className="flex items-start gap-3">
        {getRiskIcon(risk.level)}
        <div className="flex-1 min-w-0">
          <div className="font-medium">{risk.type}</div>
          <p className="text-sm text-slate-300 mt-1">{risk.description}</p>
          {risk.suggestion && (
            <p className="text-sm text-slate-400 mt-2">
              建议: {risk.suggestion}
            </p>
          )}
        </div>
      </div>
    </div>
  )

  const hasSuggestions = suggestions.length > 0
  const hasRisks = risks.length > 0

  if (!hasSuggestions && !hasRisks) {
    return (
      <div className="card">
        <h2 className="text-lg font-semibold mb-4">建议与风险</h2>
        <div className="text-center py-8 text-slate-400">
          暂无建议和风险提示
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <h2 className="text-lg font-semibold mb-4">建议与风险</h2>

      {hasSuggestions && (
        <div className="mb-6">
          <h3 className="text-sm font-medium text-slate-400 mb-3">交易建议</h3>
          <div className="space-y-3">
            {suggestions.map(renderSuggestion)}
          </div>
        </div>
      )}

      {hasRisks && (
        <div>
          <h3 className="text-sm font-medium text-slate-400 mb-3">风险提示</h3>
          <div className="space-y-3">
            {risks.map(renderRisk)}
          </div>
        </div>
      )}
    </div>
  )
}
