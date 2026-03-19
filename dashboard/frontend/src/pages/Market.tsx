import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, TrendingUp, TrendingDown, RefreshCw, Activity, AlertCircle, Clock, Zap } from 'lucide-react'
import { marketService, portfolioService } from '@/services'
import { useAppStore } from '@/store'
import { clsx } from 'clsx'

export default function Market() {
  const [searchSymbol, setSearchSymbol] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  
  const { suggestions } = useAppStore()

  // 检查是否也是交易时段 (9:25 - 16:00 北京/上海时间，周一至周五)
  const [tradingStatus, setTradingStatus] = useState(false)
  const isTradingHours = () => {
    const now = new Date()
    const utcHours = now.getUTCHours()
    const utcMinutes = now.getUTCMinutes()
    let hours = utcHours + 8
    let day = now.getUTCDay()
    if (hours >= 24) {
      hours -= 24
      day = (day + 1) % 7
    }
    if (day === 0 || day === 6) return false
    
    const minutes = hours * 60 + utcMinutes
    const start = 9 * 60 + 25
    const end = 16 * 60
    return minutes >= start && minutes <= end
  }

  useEffect(() => {
    const checkMarketTime = () => setTradingStatus(isTradingHours())
    checkMarketTime()
    const interval = setInterval(checkMarketTime, 60000)
    return () => clearInterval(interval)
  }, [])

  const formatQueryError = (queryError: unknown) => {
    const errorLike = queryError as {
      response?: { data?: { detail?: string; message?: string }; status?: number }
      message?: string
    }

    if (errorLike?.response?.data?.detail) {
      return errorLike.response.data.detail
    }
    if (errorLike?.response?.data?.message) {
      return errorLike.response.data.message
    }
    if (errorLike?.message) {
      return errorLike.message
    }
    return '行情服务暂时不可用，请稍后重试'
  }

  const { data: quote, isLoading, isError, error, refetch, isRefetching, isFetching } = useQuery({
    queryKey: ['quote', selectedSymbol],
    queryFn: () => selectedSymbol ? marketService.getQuote(selectedSymbol) : null,
    enabled: !!selectedSymbol,
    refetchInterval: selectedSymbol && tradingStatus ? 10000 : false,
    staleTime: 5000,
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const { data: portfolio } = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioService.getPortfolio,
    refetchInterval: tradingStatus ? 30000 : false,
    staleTime: 10000,
    refetchOnWindowFocus: false,
  })

  const handleSearch = () => {
    if (searchSymbol.trim()) {
      setSelectedSymbol(searchSymbol.trim())
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }

  const formatPercent = (value: number) => {
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(2)}%`
  }

  const formatNumber = (value: number) => {
    if (value >= 100000000) {
      return `${(value / 100000000).toFixed(2)}亿`
    } else if (value >= 10000) {
      return `${(value / 10000).toFixed(2)}万`
    }
    return value.toFixed(2)
  }

  // 常用股票快捷入口
  const hotStocks = [
    { symbol: '000001', name: '上证指数' },
    { symbol: '399001', name: '深证成指' },
    { symbol: '399006', name: '创业板指' },
    { symbol: '600519', name: '贵州茅台' },
    { symbol: '000858', name: '五粮液' },
    { symbol: '601318', name: '中国平安' },
    { symbol: '600036', name: '招商银行' },
    { symbol: '000333', name: '美的集团' },
  ]

  // 按持仓进行操作建议匹配
  const activeSuggestions = suggestions.filter(s => 
    s.symbol && portfolio?.positions.some(p => p.symbol === s.symbol || s.symbol!.includes(p.symbol))
  )

  return (
    <div className="p-6 space-y-6">
      
      {/* 顶部市场状态横幅 */}
      <div className={clsx(
        "rounded-2xl p-6 border shadow-lg relative overflow-hidden transition-all duration-500",
        tradingStatus 
          ? "bg-gradient-to-br from-indigo-900/40 to-slate-800/80 border-indigo-500/30 ring-1 ring-inset ring-indigo-500/20" 
          : "bg-slate-800/80 border-slate-700"
      )}>
        {tradingStatus && (
          <div className="absolute -top-24 -right-24 w-96 h-96 bg-indigo-600/10 rounded-full blur-3xl animate-pulse" />
        )}
        <div className="relative z-10 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className={clsx(
              "w-12 h-12 rounded-xl flex items-center justify-center",
              tradingStatus ? "bg-indigo-500/20 text-indigo-400" : "bg-slate-700 text-slate-400"
            )}>
              {tradingStatus ? <Activity className="w-6 h-6 animate-pulse" /> : <Clock className="w-6 h-6" />}
            </div>
            <div>
              <h2 className="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-white to-slate-400">
                {tradingStatus ? "盘中实时智能盯盘" : "市场已收盘"}
              </h2>
              <p className="text-sm text-slate-400 mt-1">
                {tradingStatus 
                  ? "AI 正在实时监控您的持仓并捕捉交易机会 (数据自动刷新中)" 
                  : "非交易时间。您可以在此复盘或研究明日策略"
                }
              </p>
            </div>
          </div>
          {tradingStatus && (
            <div className="flex items-center gap-2 px-4 py-2 bg-indigo-500/10 rounded-full border border-indigo-500/20 text-indigo-400 text-sm font-medium">
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-3 w-3 bg-indigo-500"></span>
              </span>
              Trading Live
            </div>
          )}
        </div>

        {/* 盘中智能展现区块 */}
        {tradingStatus && portfolio && portfolio.positions.length > 0 && (
          <div className="mt-6 pt-6 border-t border-slate-700/50">
            <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2 mb-4">
              <Zap className="w-4 h-4 text-yellow-400" />
              当前持仓操作建议脉搏
            </h3>
            {activeSuggestions.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {activeSuggestions.map((s, i) => {
                  const pos = portfolio.positions.find(p => p.symbol === s.symbol || s.symbol!.includes(p.symbol))
                  const isBuy = s.type === 'buy' || s.type === 'add'
                  const isSell = s.type === 'sell' || s.type === 'reduce'
                  const isHold = s.type === 'hold'
                  
                  return (
                    <div key={i} className="bg-slate-800/50 rounded-xl p-4 border border-slate-700/50 hover:bg-slate-700/50 transition-colors">
                      <div className="flex justify-between items-start mb-2">
                        <div>
                          <span className="font-semibold text-white">{pos?.name || s.symbol}</span>
                          <span className="text-xs text-slate-400 ml-2">{s.symbol}</span>
                        </div>
                        <span className={clsx(
                          "px-2.5 py-1 text-xs font-bold rounded flex items-center gap-1",
                          isBuy && "bg-green-500/20 text-green-400",
                          isSell && "bg-red-500/20 text-red-400",
                          isHold && "bg-yellow-500/20 text-yellow-400"
                        )}>
                          {isBuy && <TrendingUp className="w-3 h-3" />}
                          {isSell && <TrendingDown className="w-3 h-3" />}
                          {isHold && <Activity className="w-3 h-3" />}
                          {
                            s.type === 'buy' ? '买入建仓' :
                            s.type === 'add' ? '逢低加仓' :
                            s.type === 'sell' ? '止盈/止损' :
                            s.type === 'reduce' ? '逢高减退' : '继续持有'
                          }
                        </span>
                      </div>
                      
                      {/* 实盘状态 */}
                      {pos && (
                        <div className="flex items-center gap-3 text-sm mb-3">
                          <span className={pos.profit >= 0 ? 'text-green-400' : 'text-red-400'}>
                            现价 {pos.current_price.toFixed(2)}
                          </span>
                          <span className="text-slate-500 border-l border-slate-600 pl-3">
                            盈亏 {formatPercent(pos.profit_pct)}
                          </span>
                        </div>
                      )}
                      
                      <p className="text-sm text-slate-300 line-clamp-2">{s.reason}</p>
                      
                      {(s.target_price || s.stop_loss) && (
                        <div className="mt-3 flex items-center gap-3 text-xs bg-slate-900/50 rounded p-2">
                          {s.target_price && <span className="text-green-400/80">目标: {s.target_price}</span>}
                          {s.stop_loss && <span className="text-red-400/80">防守: {s.stop_loss}</span>}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="text-slate-400 text-sm flex items-center gap-2 bg-slate-800/30 p-4 rounded-lg">
                <AlertCircle className="w-4 h-4 text-slate-500" />
                当前时段，您的持仓暂无特别触发操作信号，建议按照既定策略耐心持股。如需最新分析可前往对话区。
              </div>
            )}
          </div>
        )}
      </div>

      {/* 搜索栏 */}
      <div className="bg-slate-800 rounded-2xl p-4 border border-slate-700 shadow-md">
        <div className="flex space-x-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-slate-400" />
            <input
              type="text"
              placeholder="输入股票代码，如 600519"
              value={searchSymbol}
              onChange={(e) => setSearchSymbol(e.target.value)}
              onKeyPress={handleKeyPress}
              className="w-full bg-slate-700 rounded-lg pl-10 pr-4 py-3 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <button
            onClick={handleSearch}
            className="px-6 py-3 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors"
          >
            查询
          </button>
        </div>
      </div>

      {/* 常用股票 */}
      <div className="bg-slate-800 rounded-2xl p-5 border border-slate-700 shadow-md">
        <h3 className="text-lg font-medium mb-4 flex items-center gap-2">
          <Activity className="w-5 h-5 text-primary-400" />
          全市场风向标
        </h3>
        <div className="grid grid-cols-4 gap-3">
          {hotStocks.map((stock) => (
            <button
              key={stock.symbol}
              onClick={() => setSelectedSymbol(stock.symbol)}
              className={`px-4 py-3 rounded-lg transition-colors text-left ${
                selectedSymbol === stock.symbol
                  ? 'bg-primary-600'
                  : 'bg-slate-700 hover:bg-slate-600'
              }`}
            >
              <div className="text-sm text-slate-400">{stock.symbol}</div>
              <div className="font-medium">{stock.name}</div>
            </button>
          ))}
        </div>
      </div>

      {/* 行情详情 */}
      {selectedSymbol && (
        <div className="bg-slate-800 rounded-2xl p-8 border border-slate-700 shadow-xl transition-all">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-xl font-semibold">行情详情</h3>
            <div className="flex items-center gap-3">
              <span className="text-xs text-slate-500">
                {isFetching ? '行情更新中...' : quote?.timestamp ? `更新于 ${new Date(quote.timestamp).toLocaleTimeString('zh-CN')}` : '等待查询'}
              </span>
              <button
                onClick={() => refetch()}
                disabled={isRefetching}
                className="flex items-center px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors disabled:opacity-60"
              >
                <RefreshCw className={`h-4 w-4 mr-2 ${isRefetching ? 'animate-spin' : ''}`} />
                刷新
              </button>
            </div>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="h-8 w-8 animate-spin text-primary-500" />
            </div>
          ) : isError ? (
            <div className="py-12 text-center">
              <AlertCircle className="h-8 w-8 mx-auto text-red-400 mb-3" />
              <div className="text-red-300 font-medium">获取实时行情失败</div>
              <div className="text-sm text-slate-400 mt-2">{formatQueryError(error)}</div>
              <div className="text-xs text-slate-500 mt-2">常见原因：后端未启动、端口代理错误，或行情源响应超时</div>
            </div>
          ) : quote ? (
            <div className="space-y-6">
              {/* 基本信息 */}
              <div className="flex items-baseline space-x-4">
                <span className="text-3xl font-bold">{quote.name}</span>
                <span className="text-slate-400">{quote.symbol}</span>
              </div>

              {/* 价格和涨跌 */}
              <div className="flex items-baseline space-x-6">
                <span className="text-4xl font-bold">
                  {quote.price?.toFixed(2)}
                </span>
                <div className={`flex items-center ${(quote.change_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {(quote.change_pct || 0) >= 0 ? <TrendingUp className="h-6 w-6 mr-1" /> : <TrendingDown className="h-6 w-6 mr-1" />}
                  <span className="text-2xl font-semibold">
                    {formatPercent(quote.change_pct || 0)}
                  </span>
                  <span className="ml-2 text-lg">
                    ({(quote.change || 0) >= 0 ? '+' : ''}{quote.change?.toFixed(2)})
                  </span>
                </div>
              </div>

              {/* 详细数据 */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6 pt-6 border-t border-slate-700/50">
                <div>
                  <div className="text-sm text-slate-400">开盘价</div>
                  <div className="text-lg font-medium">{quote.open?.toFixed(2) || '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">最高价</div>
                  <div className="text-lg font-medium text-green-400">{quote.high?.toFixed(2) || '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">最低价</div>
                  <div className="text-lg font-medium text-red-400">{quote.low?.toFixed(2) || '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">昨收价</div>
                  <div className="text-lg font-medium">{quote.prev_close?.toFixed(2) || '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">成交量</div>
                  <div className="text-lg font-medium">{quote.volume ? formatNumber(quote.volume) : '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">成交额</div>
                  <div className="text-lg font-medium">{quote.amount ? formatNumber(quote.amount) : '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">换手率</div>
                  <div className="text-lg font-medium">{quote.turnover ? `${quote.turnover.toFixed(2)}%` : '-'}</div>
                </div>
                <div>
                  <div className="text-sm text-slate-400">市盈率</div>
                  <div className="text-lg font-medium">{quote.pe ? quote.pe.toFixed(2) : '-'}</div>
                </div>
              </div>

              {/* 更新时间 */}
              {quote.timestamp && (
                <div className="text-sm text-slate-500 pt-6 border-t border-slate-700/50">
                  最后更新: {new Date(quote.timestamp).toLocaleString('zh-CN')}
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-12 text-slate-400">
              未找到股票信息
            </div>
          )}
        </div>
      )}

      {/* 空状态 */}
      {!selectedSymbol && (
        <div className="bg-slate-800/50 border border-slate-700/50 border-dashed rounded-2xl p-16 text-center transition-all hover:bg-slate-800/80">
          <div className="bg-slate-700/50 w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-6 shadow-inner">
            <Search className="h-10 w-10 text-slate-400" />
          </div>
          <h3 className="text-xl font-medium text-slate-300 mb-2">探索深度行情</h3>
          <p className="text-slate-500">输入任意股票代码，获取AI加持的实时市场数据与分析</p>
        </div>
      )}
    </div>
  )
}
