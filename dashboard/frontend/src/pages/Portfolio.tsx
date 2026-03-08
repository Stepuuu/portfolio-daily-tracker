import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useRef } from 'react'
import { TrendingUp, TrendingDown, RefreshCw, Plus, Edit2, Trash2, X, Save } from 'lucide-react'
import { portfolioService } from '@/services'
import type { Position } from '@/types'

// 编辑持仓对话框组件
interface EditPositionDialogProps {
  position: Position
  onClose: () => void
  onSave: (symbol: string, data: { quantity: number; cost_price: number }) => void
}

function EditPositionDialog({ position, onClose, onSave }: EditPositionDialogProps) {
  const [quantity, setQuantity] = useState(position.quantity)
  const [costPrice, setCostPrice] = useState(position.cost_price)

  const handleSave = () => {
    onSave(position.symbol, { quantity, cost_price: costPrice })
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-slate-800 rounded-lg p-6 w-full max-w-md">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-lg font-semibold">编辑持仓 - {position.name}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm text-slate-400 mb-2">股票代码</label>
            <input
              type="text"
              value={position.symbol}
              disabled
              className="w-full bg-slate-700/50 rounded-lg px-4 py-2 text-slate-500"
            />
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">持仓数量</label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(parseInt(e.target.value) || 0)}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-2">成本价（元）</label>
            <input
              type="number"
              step="0.01"
              value={costPrice}
              onChange={(e) => setCostPrice(parseFloat(e.target.value) || 0)}
              className="w-full bg-slate-700 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div className="flex space-x-3 pt-4">
            <button
              onClick={onClose}
              className="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              className="flex-1 px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors flex items-center justify-center"
            >
              <Save className="h-4 w-4 mr-2" />
              保存
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function Portfolio() {
  const queryClient = useQueryClient()
  const [editingPosition, setEditingPosition] = useState<Position | null>(null)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const hasRefreshedOnMount = useRef(false)

  const { data: portfolio, isLoading } = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioService.getPortfolio,
    refetchInterval: autoRefresh ? 60000 : false, // 每60秒自动刷新
  })

  const refreshMutation = useMutation({
    mutationFn: portfolioService.refresh,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolio'] })
    }
  })

  // 页面加载时自动刷新一次行情
  useEffect(() => {
    if (!hasRefreshedOnMount.current && portfolio?.positions?.length > 0) {
      hasRefreshedOnMount.current = true
      refreshMutation.mutate()
    }
  }, [portfolio?.positions?.length])

  const updatePositionMutation = useMutation({
    mutationFn: ({ symbol, data }: { symbol: string; data: { quantity: number; cost_price: number } }) =>
      portfolioService.updatePosition(symbol, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolio'] })
      queryClient.refetchQueries({ queryKey: ['portfolio'] })
      setEditingPosition(null)
    }
  })

  const deletePositionMutation = useMutation({
    mutationFn: portfolioService.removePosition,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolio'] })
      queryClient.refetchQueries({ queryKey: ['portfolio'] })
    }
  })

  const handleDeletePosition = (symbol: string, name: string) => {
    if (confirm(`确定要删除持仓 ${name}(${symbol}) 吗？`)) {
      deletePositionMutation.mutate(symbol)
    }
  }

  const formatNumber = (num: number) => {
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }).format(num)
  }

  const formatPercent = (num: number) => {
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      signDisplay: 'exceptZero'
    }).format(num)
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw className="h-8 w-8 animate-spin text-primary-500" />
      </div>
    )
  }

  const positions = portfolio?.positions || []
  const totalAssets = portfolio?.total_assets || 0
  const totalMarketValue = portfolio?.total_market_value || 0
  const cash = portfolio?.cash || 0
  const totalProfit = portfolio?.total_profit || 0
  const profitPercent = totalMarketValue > 0 ? (totalProfit / (totalMarketValue - totalProfit)) * 100 : 0

  return (
    <div className="p-6 space-y-6">
      {/* 资产概览 */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-slate-800 rounded-lg p-6">
          <div className="text-sm text-slate-400 mb-2">总资产</div>
          <div className="text-2xl font-bold">¥{formatNumber(totalAssets)}</div>
        </div>
        <div className="bg-slate-800 rounded-lg p-6">
          <div className="text-sm text-slate-400 mb-2">持仓市值</div>
          <div className="text-2xl font-bold">¥{formatNumber(totalMarketValue)}</div>
        </div>
        <div className="bg-slate-800 rounded-lg p-6">
          <div className="text-sm text-slate-400 mb-2">可用现金</div>
          <div className="text-2xl font-bold text-green-400">¥{formatNumber(cash)}</div>
        </div>
        <div className="bg-slate-800 rounded-lg p-6">
          <div className="text-sm text-slate-400 mb-2">总盈亏</div>
          <div className={`text-2xl font-bold ${totalProfit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {totalProfit >= 0 ? '+' : ''}¥{formatNumber(totalProfit)}
            <span className="text-sm ml-2">({formatPercent(profitPercent)}%)</span>
          </div>
        </div>
      </div>

      {/* 持仓明细 */}
      <div className="bg-slate-800 rounded-lg">
        <div className="p-6 border-b border-slate-700 flex justify-between items-center">
          <div className="flex items-center space-x-4">
            <h2 className="text-xl font-semibold">持仓明细</h2>
            <label className="flex items-center text-sm text-slate-400 cursor-pointer">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
                className="mr-2 rounded"
              />
              自动刷新(60s)
            </label>
          </div>
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            className="flex items-center px-4 py-2 bg-primary-600 hover:bg-primary-500 rounded-lg transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshMutation.isPending ? 'animate-spin' : ''}`} />
            刷新行情
          </button>
        </div>

        {positions.length === 0 ? (
          <div className="p-12 text-center text-slate-400">
            暂无持仓，点击对话框上传截图或添加持仓
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-700">
                  <th className="px-6 py-4 text-left text-sm font-medium text-slate-400">股票代码</th>
                  <th className="px-6 py-4 text-left text-sm font-medium text-slate-400">股票名称</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-slate-400">盈亏</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-slate-400">持仓/可用</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-slate-400">成本/现价</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-slate-400">市值</th>
                  <th className="px-6 py-4 text-right text-sm font-medium text-slate-400">盈亏比例</th>
                  <th className="px-6 py-4 text-center text-sm font-medium text-slate-400">操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => {
                  const isProfit = position.profit >= 0
                  const profitClass = isProfit ? 'text-green-400' : 'text-red-400'

                  return (
                    <tr key={position.symbol} className="border-b border-slate-700/50 hover:bg-slate-700/30">
                      <td className="px-6 py-4 font-mono text-primary-400">{position.symbol}</td>
                      <td className="px-6 py-4">{position.name}</td>
                      <td className={`px-6 py-4 text-right font-medium ${profitClass}`}>
                        <div className="flex items-center justify-end">
                          {isProfit ? <TrendingUp className="h-4 w-4 mr-1" /> : <TrendingDown className="h-4 w-4 mr-1" />}
                          {isProfit ? '+' : ''}¥{formatNumber(position.profit)}
                        </div>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="text-sm">{position.quantity}</div>
                        <div className="text-xs text-slate-500">{position.available_qty}</div>
                      </td>
                      <td className="px-6 py-4 text-right">
                        <div className="text-sm text-slate-400">¥{formatNumber(position.cost_price)}</div>
                        <div className={`text-sm font-medium ${profitClass}`}>¥{formatNumber(position.current_price)}</div>
                      </td>
                      <td className="px-6 py-4 text-right font-medium">
                        ¥{formatNumber(position.market_value)}
                      </td>
                      <td className={`px-6 py-4 text-right font-bold ${profitClass}`}>
                        {formatPercent(position.profit_pct)}%
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center justify-center space-x-2">
                          <button
                            onClick={() => setEditingPosition(position)}
                            className="p-1.5 text-slate-400 hover:text-primary-400 hover:bg-slate-700 rounded transition-colors"
                            title="编辑持仓"
                          >
                            <Edit2 className="h-4 w-4" />
                          </button>
                          <button
                            onClick={() => handleDeletePosition(position.symbol, position.name)}
                            className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded transition-colors"
                            title="删除持仓"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 编辑对话框 */}
      {editingPosition && (
        <EditPositionDialog
          position={editingPosition}
          onClose={() => setEditingPosition(null)}
          onSave={(symbol, data) => updatePositionMutation.mutate({ symbol, data })}
        />
      )}
    </div>
  )
}
