import { RefreshCw, Bell } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { portfolioService } from '@/services'

export default function Header() {
  const { data: portfolio, refetch, isRefetching } = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioService.getPortfolio,
    refetchInterval: 60000, // 每分钟自动刷新
  })

  const formatMoney = (value: number) => {
    return new Intl.NumberFormat('zh-CN', {
      style: 'currency',
      currency: 'CNY',
    }).format(value)
  }

  const formatPercent = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(2)}%`
  }

  return (
    <header className="flex h-16 items-center justify-between border-b border-slate-700 bg-slate-800 px-6">
      {/* 账户概览 */}
      <div className="flex items-center space-x-8">
        {portfolio && (
          <>
            <div>
              <div className="text-sm text-slate-400">总资产</div>
              <div className="text-lg font-semibold">
                {formatMoney(portfolio.total_assets)}
              </div>
            </div>
            <div>
              <div className="text-sm text-slate-400">持仓市值</div>
              <div className="text-lg font-semibold">
                {formatMoney(portfolio.total_market_value)}
              </div>
            </div>
            <div>
              <div className="text-sm text-slate-400">可用现金</div>
              <div className="text-lg font-semibold">
                {formatMoney(portfolio.cash)}
              </div>
            </div>
            <div>
              <div className="text-sm text-slate-400">总盈亏</div>
              <div
                className={`text-lg font-semibold ${
                  portfolio.total_profit >= 0 ? 'profit-positive' : 'profit-negative'
                }`}
              >
                {formatMoney(portfolio.total_profit)}
              </div>
            </div>
          </>
        )}
      </div>

      {/* 操作按钮 */}
      <div className="flex items-center space-x-4">
        <button
          onClick={() => refetch()}
          disabled={isRefetching}
          className="flex items-center px-3 py-2 text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
          title="刷新数据"
        >
          <RefreshCw className={`h-5 w-5 ${isRefetching ? 'animate-spin' : ''}`} />
        </button>
        <button
          className="flex items-center px-3 py-2 text-slate-300 hover:text-white hover:bg-slate-700 rounded-lg transition-colors"
          title="通知"
        >
          <Bell className="h-5 w-5" />
        </button>
      </div>
    </header>
  )
}
