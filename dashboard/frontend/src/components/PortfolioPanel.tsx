import { useQuery } from '@tanstack/react-query'
import { TrendingUp, TrendingDown, RefreshCw } from 'lucide-react'
import { portfolioService } from '@/services'
import type { Position } from '@/types'

export default function PortfolioPanel() {
  const {
    data: portfolio,
    isLoading,
    refetch,
    isRefetching,
  } = useQuery({
    queryKey: ['portfolio', 'live'],
    queryFn: portfolioService.getLivePortfolio,
    refetchInterval: 60000,
    refetchOnWindowFocus: false,
  })

  const formatMoney = (value: number) => {
    return new Intl.NumberFormat('zh-CN', {
      style: 'currency',
      currency: 'CNY',
      minimumFractionDigits: 2,
    }).format(value)
  }

  const formatPercent = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(2)}%`
  }

  const renderPosition = (position: Position) => {
    const isProfit = position.profit >= 0

    return (
      <div
        key={position.symbol}
        className="flex items-center justify-between py-3 border-b border-slate-700 last:border-b-0"
      >
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{position.name}</span>
            <span className="text-sm text-slate-400">{position.symbol}</span>
          </div>
          <div className="text-sm text-slate-400 mt-1">
            {position.quantity}股 | 成本 {position.cost_price.toFixed(2)}
          </div>
        </div>

        <div className="text-right">
          <div className="font-medium">{position.current_price.toFixed(2)}</div>
          <div
            className={`flex items-center justify-end gap-1 text-sm ${
              isProfit ? 'profit-positive' : 'profit-negative'
            }`}
          >
            {isProfit ? (
              <TrendingUp className="h-4 w-4" />
            ) : (
              <TrendingDown className="h-4 w-4" />
            )}
            <span>{formatMoney(position.profit)}</span>
            <span>({formatPercent(position.profit_pct)})</span>
          </div>
        </div>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">持仓</h2>
        </div>
        <div className="flex items-center justify-center py-8 text-slate-400">
          加载中...
        </div>
      </div>
    )
  }

  if (!portfolio || portfolio.positions.length === 0) {
    return (
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">持仓</h2>
          <button
            onClick={() => refetch()}
            className="p-2 text-slate-400 hover:text-white rounded-lg transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${isRefetching ? 'animate-spin' : ''}`} />
          </button>
        </div>
        <div className="text-center py-8 text-slate-400">
          暂无持仓
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">持仓</h2>
        <button
          onClick={() => refetch()}
          className="p-2 text-slate-400 hover:text-white rounded-lg transition-colors"
        >
          <RefreshCw className={`h-4 w-4 ${isRefetching ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* 持仓汇总 */}
      <div className="grid grid-cols-2 gap-4 mb-4 p-3 bg-slate-700/50 rounded-lg">
        <div>
          <div className="text-sm text-slate-400">持仓市值</div>
          <div className="font-semibold">
            {formatMoney(portfolio.total_market_value)}
          </div>
        </div>
        <div>
          <div className="text-sm text-slate-400">总盈亏</div>
          <div
            className={`font-semibold ${
              portfolio.total_profit >= 0 ? 'profit-positive' : 'profit-negative'
            }`}
          >
            {formatMoney(portfolio.total_profit)}
          </div>
        </div>
      </div>

      {/* 持仓列表 */}
      <div className="divide-y divide-slate-700">
        {portfolio.positions.map(renderPosition)}
      </div>
    </div>
  )
}
