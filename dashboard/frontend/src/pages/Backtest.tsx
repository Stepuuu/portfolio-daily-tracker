import { useState, useEffect, useCallback } from 'react'
import {
  BarChart2, Database, BookOpen, Play, History, TrendingUp,
  AlertCircle, CheckCircle, Loader2, ChevronDown, ChevronUp, Brain,
  Download, Upload, Trash2, Eye, Star, RefreshCw, Code, Copy, FileText,
  ArrowUpDown, Settings2, Zap
} from 'lucide-react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RTTooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

// ================================================================
// Types
// ================================================================

interface StrategyParam { [key: string]: number | string }

interface StrategyInfo {
  id: string; name: string; class: string; parameters: StrategyParam
  description: string; builtin: boolean; file: string
}

interface SymbolInfo {
  symbol: string; rows: number; start_date: string; end_date: string
}

interface EquityPoint { date: string; net_value: number; drawdown: number }

interface TradeRecord {
  date: string; symbol: string; direction: string
  quantity: number; price: number; pnl: number; commission: number
}

interface BacktestResult {
  run_id?: string; run_date?: string; strategy_name?: string; primary_symbol?: string
  initial_cash: number; final_value: number; total_return: number
  annualized_return: number; sharpe_ratio: number; sortino_ratio: number
  calmar_ratio: number; max_drawdown: number; volatility: number
  total_trades: number; win_rate: number; profit_factor: number
  avg_profit: number; avg_loss: number
  max_single_profit: number; max_single_loss: number
  benchmark_return?: number
  summary?: string; equity_curve?: EquityPoint[]; trade_records?: TradeRecord[]
  reflection?: { analysis: string; lessons: string[]; improvements: string[] }
  saved_to_history?: boolean
}

interface HistoryRun {
  run_id: string; run_date: string; strategy_name: string; primary_symbol: string
  total_return: number; sharpe_ratio: number; max_drawdown: number
  total_trades: number; win_rate: number; starred: boolean; notes: string
  tags: string
}

// ================================================================
// API helpers
// ================================================================

const api = async (path: string, opts?: RequestInit) => {
  const resp = await fetch(`/api/backtest${path}`, opts)
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || 'API 请求失败')
  }
  return resp.json()
}

const apiPost = (path: string, body: any) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

const apiDelete = (path: string, body?: any) =>
  api(path, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })

// ================================================================
// Shared Components
// ================================================================

function MetricCard({ label, value, color = 'text-slate-900', sub = '' }: {
  label: string; value: string; color?: string; sub?: string
}) {
  return (
    <div className="bg-white rounded-xl p-3.5 border border-slate-100 shadow-sm">
      <div className="text-[10px] text-slate-500 uppercase tracking-wide mb-0.5">{label}</div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-400 mt-0.5">{sub}</div>}
    </div>
  )
}

function Badge({ children, variant = 'default' }: { children: React.ReactNode; variant?: 'default' | 'green' | 'red' | 'blue' | 'yellow' }) {
  const colors = {
    default: 'bg-slate-100 text-slate-600',
    green: 'bg-green-50 text-green-700',
    red: 'bg-red-50 text-red-700',
    blue: 'bg-blue-50 text-blue-700',
    yellow: 'bg-yellow-50 text-yellow-700'
  }
  return <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium ${colors[variant]}`}>{children}</span>
}

function EquityCurve({ data }: { data: EquityPoint[] }) {
  if (!data || data.length < 2) return (
    <div className="h-48 flex items-center justify-center text-slate-400 text-sm">暂无净值曲线数据</div>
  )
  const lastVal = data[data.length - 1].net_value
  const isPos = lastVal >= 1.0
  const color = isPos ? '#10b981' : '#ef4444'
  
  return (
    <div className="w-full h-48">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis dataKey="date" tick={{fontSize: 10, fill: '#94a3b8'}} tickFormatter={(v) => v.substring(5)} minTickGap={30} />
          <YAxis domain={['auto', 'auto']} tick={{fontSize: 10, fill: '#94a3b8'}} tickFormatter={(v) => v.toFixed(2)} />
          <RTTooltip 
            contentStyle={{ borderRadius: '8px', fontSize: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
            labelStyle={{ color: '#64748b', marginBottom: '4px' }}
            formatter={(val: any) => [Number(val).toFixed(4), '净值']}
          />
          <ReferenceLine y={1} stroke="#94a3b8" strokeDasharray="3 3" />
          <Line type="monotone" dataKey="net_value" stroke={color} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

const pct = (v: number) => v != null ? `${(v * 100).toFixed(2)}%` : '—'
const money = (v: number) => v != null ? v.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—'

// ================================================================
// Tab 1: 数据管理
// ================================================================

function DataTab() {
  const [symbols, setSymbols] = useState<SymbolInfo[]>([])
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [dlSymbol, setDlSymbol] = useState('600519')
  const [dlStart, setDlStart] = useState('2020-01-01')
  const [dlEnd, setDlEnd] = useState('2024-12-31')
  const [dlAdjust, setDlAdjust] = useState('qfq')
  const [dlLoading, setDlLoading] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [preview, setPreview] = useState<{ symbol: string; data: any[]; total: number } | null>(null)
  const [csvFile, setCsvFile] = useState<File | null>(null)
  const [csvSymbol, setCsvSymbol] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [s, c] = await Promise.all([api('/data/symbols'), api('/data/cache')])
      setSymbols(s.symbols || [])
      setStats(c)
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleDownload = async () => {
    setDlLoading(true); setMsg(null)
    try {
      const r = await apiPost('/data/download', { symbol: dlSymbol, start_date: dlStart, end_date: dlEnd, adjust: dlAdjust })
      setMsg({ type: 'ok', text: `✓ ${dlSymbol} 下载完成, ${r.rows_saved} 条数据` })
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
    setDlLoading(false)
  }

  const handleDelete = async (sym: string) => {
    if (!confirm(`确认删除 ${sym} 的缓存数据?`)) return
    try {
      await apiDelete(`/data/cache/${sym}`)
      setMsg({ type: 'ok', text: `已删除 ${sym}` })
      if (preview?.symbol === sym) setPreview(null)
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handlePreview = async (sym: string) => {
    try {
      const r = await api(`/data/preview/${sym}?limit=30`)
      setPreview({ symbol: sym, data: r.data, total: r.total_rows })
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleCsvUpload = async () => {
    if (!csvFile || !csvSymbol) return
    setDlLoading(true); setMsg(null)
    try {
      const fd = new FormData()
      fd.append('file', csvFile)
      fd.append('symbol', csvSymbol)
      fd.append('adjust', 'none')
      const resp = await fetch('/api/backtest/data/import-csv', { method: 'POST', body: fd })
      if (!resp.ok) throw new Error((await resp.json()).detail)
      const r = await resp.json()
      setMsg({ type: 'ok', text: `✓ CSV 导入成功: ${r.rows_imported} 条 (${r.filename})` })
      setCsvFile(null); setCsvSymbol('')
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
    setDlLoading(false)
  }

  return (
    <div className="space-y-5">
      {/* Stats overview */}
      {stats && (
        <div className="grid grid-cols-3 gap-3">
          <MetricCard label="标的数量" value={String(stats.total_symbols ?? symbols.length)} />
          <MetricCard label="数据行数" value={money(stats.total_rows ?? 0)} />
          <MetricCard label="数据库大小" value={stats.db_size_mb ? `${stats.db_size_mb} MB` : '—'} />
        </div>
      )}

      {/* Download panel */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
          <Download className="w-4 h-4 text-blue-500" /> 下载数据 (AKShare)
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">股票代码</label>
            <input value={dlSymbol} onChange={e => setDlSymbol(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="600519" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">开始日期</label>
            <input type="date" value={dlStart} onChange={e => setDlStart(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">结束日期</label>
            <input type="date" value={dlEnd} onChange={e => setDlEnd(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">复权</label>
            <select value={dlAdjust} onChange={e => setDlAdjust(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
              <option value="qfq">前复权</option>
              <option value="hfq">后复权</option>
              <option value="">不复权</option>
            </select>
          </div>
          <div className="flex items-end">
            <button onClick={handleDownload} disabled={dlLoading}
              className="w-full flex items-center justify-center gap-1.5 bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50">
              {dlLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              下载
            </button>
          </div>
        </div>
      </div>

      {/* CSV Import */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
          <Upload className="w-4 h-4 text-green-500" /> CSV 导入
        </h3>
        <p className="text-xs text-slate-400 mb-3">CSV 需含列: date, open, high, low, close, volume</p>
        <div className="grid grid-cols-3 gap-3 items-end">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">标的代码</label>
            <input value={csvSymbol} onChange={e => setCsvSymbol(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm" placeholder="如 BTC-USD" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">选择文件</label>
            <input type="file" accept=".csv" onChange={e => setCsvFile(e.target.files?.[0] ?? null)}
              className="w-full text-sm text-slate-600" />
          </div>
          <button onClick={handleCsvUpload} disabled={!csvFile || !csvSymbol || dlLoading}
            className="flex items-center justify-center gap-1.5 bg-green-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
            <Upload className="w-3.5 h-3.5" /> 导入
          </button>
        </div>
      </div>

      {/* Message */}
      {msg && (
        <div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      {/* Symbol table */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
            <Database className="w-4 h-4 text-indigo-500" /> 已缓存数据 ({symbols.length} 只标的)
          </h3>
          <button onClick={refresh} disabled={loading}
            className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
          </button>
        </div>
        {symbols.length === 0 ? (
          <div className="text-center py-8 text-slate-400 text-sm">暂无缓存数据，请先下载或导入</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-400 border-b border-slate-100">
                  <th className="py-2 text-left">标的</th>
                  <th className="py-2 text-right">数据行数</th>
                  <th className="py-2 text-right">起始日期</th>
                  <th className="py-2 text-right">截止日期</th>
                  <th className="py-2 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {symbols.map(s => (
                  <tr key={s.symbol} className="border-b border-slate-50 hover:bg-slate-50">
                    <td className="py-2 font-medium text-slate-700">{s.symbol}</td>
                    <td className="py-2 text-right text-slate-600">{s.rows?.toLocaleString()}</td>
                    <td className="py-2 text-right text-slate-500">{s.start_date}</td>
                    <td className="py-2 text-right text-slate-500">{s.end_date}</td>
                    <td className="py-2 text-right">
                      <button onClick={() => handlePreview(s.symbol)} className="text-blue-500 hover:text-blue-700 mr-3" title="预览">
                        <Eye className="w-3.5 h-3.5 inline" />
                      </button>
                      <button onClick={() => handleDelete(s.symbol)} className="text-red-400 hover:text-red-600" title="删除">
                        <Trash2 className="w-3.5 h-3.5 inline" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Preview */}
      {preview && (
        <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-700">
              {preview.symbol} K线预览 (最近 {preview.data.length} / {preview.total} 条)
            </h3>
            <button onClick={() => setPreview(null)} className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
          </div>
          <div className="overflow-x-auto max-h-60 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="py-1.5 text-left">日期</th>
                  <th className="py-1.5 text-right">开盘</th>
                  <th className="py-1.5 text-right">最高</th>
                  <th className="py-1.5 text-right">最低</th>
                  <th className="py-1.5 text-right">收盘</th>
                  <th className="py-1.5 text-right">成交量</th>
                  <th className="py-1.5 text-right">涨跌幅</th>
                </tr>
              </thead>
              <tbody>
                {preview.data.map((d: any, i: number) => (
                  <tr key={i} className="border-b border-slate-50">
                    <td className="py-1 text-slate-600">{d.date}</td>
                    <td className="py-1 text-right">{Number(d.open).toFixed(2)}</td>
                    <td className="py-1 text-right text-red-500">{Number(d.high).toFixed(2)}</td>
                    <td className="py-1 text-right text-green-600">{Number(d.low).toFixed(2)}</td>
                    <td className="py-1 text-right font-medium">{Number(d.close).toFixed(2)}</td>
                    <td className="py-1 text-right text-slate-500">{Number(d.volume).toLocaleString()}</td>
                    <td className={`py-1 text-right font-medium ${Number(d.pct_change) >= 0 ? 'text-red-500' : 'text-green-600'}`}>
                      {d.pct_change != null ? `${Number(d.pct_change).toFixed(2)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ================================================================
// Tab 2: 策略库
// ================================================================

function StrategyTab() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedSource, setSelectedSource] = useState<{ id: string; code: string; params: StrategyParam; desc: string } | null>(null)
  const [uploadName, setUploadName] = useState('')
  const [uploadCode, setUploadCode] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await api('/strategies')
      setStrategies(r.strategies || [])
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
    setLoading(false)
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const viewSource = async (id: string) => {
    try {
      const r = await api(`/strategies/${encodeURIComponent(id)}/source`)
      setSelectedSource({ id, code: r.source_code, params: r.parameters, desc: r.description })
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleUpload = async () => {
    setMsg(null)
    try {
      const r = await apiPost('/strategies/upload', { filename: uploadName, code: uploadCode })
      setMsg({ type: 'ok', text: `策略上传成功: ${r.strategy_classes.join(', ')} → ${r.strategy_id}` })
      setShowUpload(false); setUploadName(''); setUploadCode('')
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleDeleteCustom = async (id: string, file: string) => {
    if (!confirm(`确认删除自定义策略 ${id}?`)) return
    const filename = file.split('/').pop() || ''
    try {
      await apiDelete(`/strategies/custom/${filename}`)
      setMsg({ type: 'ok', text: `已删除 ${id}` })
      if (selectedSource?.id === id) setSelectedSource(null)
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const STRATEGY_TEMPLATE = `"""
自定义策略示例
继承 Strategy 基类, 实现 on_bar() 方法
"""
from backtesting.strategies.base import Strategy, Order


class MyCustomStrategy(Strategy):
    """我的自定义策略"""

    name = "my_custom"
    parameters = {
        "lookback": 20,
        "threshold": 0.02,
    }

    def on_bar(self):
        # self.data  -> DataFeed 对象, 可通过 self.data.close(n) 获取前 n 天收盘价
        # self.buy(quantity=xxx)  -> 提交买单
        # self.sell(quantity=xxx) -> 提交卖单
        # self.get_position()    -> 获取当前持仓对象

        # 示例: 取过去 lookback 天的收盘价
        closes = [self.data.close(i) for i in range(self.parameters["lookback"])]
        if len(closes) < self.parameters["lookback"]:
            return

        avg = sum(closes) / len(closes)
        current_close = self.data.close()
        
        pos = self.get_position()
        holding = pos and pos.quantity > 0

        if current_close < avg * (1 - self.parameters["threshold"]) and not holding:
            self.buy(quantity=100)
        elif current_close > avg * (1 + self.parameters["threshold"]) and holding:
            self.close_position()
`

  return (
    <div className="space-y-5">
      {/* Header + actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={refresh} disabled={loading}
            className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
          </button>
          <Badge variant="blue">{strategies.filter(s => s.builtin).length} 内置</Badge>
          <Badge variant="green">{strategies.filter(s => !s.builtin).length} 自定义</Badge>
        </div>
        <button onClick={() => { setShowUpload(!showUpload); if (!uploadCode) setUploadCode(STRATEGY_TEMPLATE) }}
          className="flex items-center gap-1.5 bg-green-600 text-white px-3 py-1.5 rounded-lg text-xs hover:bg-green-700">
          <Upload className="w-3 h-3" /> 上传策略
        </button>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      {/* Strategy list */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {strategies.map(s => (
          <div key={s.id} className={`bg-white rounded-xl border shadow-sm p-4 ${selectedSource?.id === s.id ? 'ring-2 ring-blue-300 border-blue-200' : 'border-slate-100'}`}>
            <div className="flex items-start justify-between">
              <div>
                <div className="font-semibold text-slate-800 text-sm flex items-center gap-2">
                  {s.name}
                  {s.builtin ? <Badge variant="blue">内置</Badge> : <Badge variant="green">自定义</Badge>}
                </div>
                <div className="text-[10px] text-slate-400 mt-0.5">{s.class} · {s.id}</div>
                {s.description && <p className="text-xs text-slate-500 mt-1.5 line-clamp-2">{s.description}</p>}
              </div>
              <div className="flex items-center gap-1.5">
                <button onClick={() => viewSource(s.id)} className="text-blue-500 hover:text-blue-700 p-1" title="查看源码">
                  <Code className="w-4 h-4" />
                </button>
                {!s.builtin && (
                  <button onClick={() => handleDeleteCustom(s.id, s.file)} className="text-red-400 hover:text-red-600 p-1" title="删除">
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
            {/* Params */}
            {Object.keys(s.parameters).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(s.parameters).map(([k, v]) => (
                  <span key={k} className="bg-slate-50 text-slate-600 px-2 py-0.5 rounded text-[10px]">{k}={String(v)}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Source code viewer */}
      {selectedSource && (
        <div className="bg-slate-900 rounded-xl border border-slate-700 p-5 text-green-300">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-green-400 flex items-center gap-2">
              <Code className="w-4 h-4" /> {selectedSource.id} 源码
            </h3>
            <div className="flex items-center gap-2">
              <button onClick={() => navigator.clipboard.writeText(selectedSource.code)}
                className="text-xs text-slate-400 hover:text-white flex items-center gap-1">
                <Copy className="w-3 h-3" /> 复制
              </button>
              <button onClick={() => setSelectedSource(null)} className="text-xs text-slate-400 hover:text-white">关闭</button>
            </div>
          </div>
          <pre className="text-xs overflow-auto max-h-96 font-mono whitespace-pre leading-5">{selectedSource.code}</pre>
        </div>
      )}

      {/* Upload panel */}
      {showUpload && (
        <div className="bg-white rounded-xl border border-green-200 shadow-sm p-5">
          <h3 className="text-sm font-semibold text-green-700 mb-3 flex items-center gap-2">
            <Upload className="w-4 h-4" /> 上传自定义策略
          </h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-slate-500 mb-1 block">文件名</label>
              <input value={uploadName} onChange={e => setUploadName(e.target.value)}
                className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm" placeholder="my_strategy.py" />
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block">Python 代码</label>
              <textarea value={uploadCode} onChange={e => setUploadCode(e.target.value)}
                className="w-full border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono h-64 resize-y"
                spellCheck={false} />
            </div>
            <div className="flex gap-3">
              <button onClick={handleUpload} disabled={!uploadName || !uploadCode}
                className="flex items-center gap-1.5 bg-green-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
                <Upload className="w-3.5 h-3.5" /> 提交
              </button>
              <button onClick={() => setShowUpload(false)} className="text-sm text-slate-500 hover:text-slate-700">取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ================================================================
// Tab 3: 回测执行
// ================================================================

function BacktestRunTab() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [symbol, setSymbol] = useState('600519')
  const [startDate, setStartDate] = useState('2020-01-01')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [strategy, setStrategy] = useState('sma_cross')
  const [initialCash, setInitialCash] = useState(1000000)
  const [params, setParams] = useState<StrategyParam>({})
  const [withReflection, setWithReflection] = useState(true)
  // Broker params
  const [commBuy, setCommBuy] = useState(0.0003)
  const [commSell, setCommSell] = useState(0.0013)
  const [minComm, setMinComm] = useState(5.0)
  const [slippage, setSlippage] = useState(0.0002)
  const [lotSize, setLotSize] = useState(100)
  const [adjust, setAdjust] = useState('qfq')
  const [warmup, setWarmup] = useState(60)
  const [tags, setTags] = useState('')
  const [notes, setNotes] = useState('')
  const [showBroker, setShowBroker] = useState(false)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [showTrades, setShowTrades] = useState(false)
  const [showReflection, setShowReflection] = useState(true)

  // Load strategies
  useEffect(() => {
    api('/strategies').then(r => {
      setStrategies(r.strategies || [])
      // Initialize params for first strategy
      const first = (r.strategies || [])[0]
      if (first) setParams(first.parameters || {})
    }).catch(() => {})
  }, [])

  const handleStrategyChange = (id: string) => {
    setStrategy(id)
    const s = strategies.find(s => s.id === id)
    if (s) setParams({ ...s.parameters })
  }

  const handleParamChange = (key: string, value: string) => {
    const num = parseFloat(value)
    setParams(prev => ({ ...prev, [key]: isNaN(num) ? value : num }))
  }

  const handleRun = async () => {
    setLoading(true); setError(null); setResult(null)
    try {
      const data = await apiPost('/run-sync', {
        symbol, start_date: startDate, end_date: endDate, strategy,
        initial_cash: initialCash, params, with_reflection: withReflection,
        commission_buy: commBuy, commission_sell: commSell,
        min_commission: minComm, slippage_pct: slippage, lot_size: lotSize,
        adjust, warmup,
        tags: tags.split(',').map(t => t.trim()).filter(Boolean),
        notes,
      })
      setResult(data)
    } catch (e: any) { setError(e.message || '回测失败') }
    setLoading(false)
  }

  const handleQuickTest = async () => {
    setLoading(true); setError(null); setResult(null)
    try {
      const data = await apiPost('/quick-test', {})
      setResult({ ...data.stats, summary: data.summary, trade_records: [], equity_curve: [] } as any)
    } catch (e: any) { setError(e.message) }
    setLoading(false)
  }

  return (
    <div className="space-y-5">
      {/* Config panel */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
            <Settings2 className="w-4 h-4 text-blue-500" /> 回测配置
          </h2>
          <button onClick={handleQuickTest} disabled={loading}
            className="text-xs px-3 py-1 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1">
            <Zap className="w-3 h-3 text-yellow-500" /> 合成数据快速测试
          </button>
        </div>

        {/* Basic params */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">股票代码</label>
            <input value={symbol} onChange={e => setSymbol(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">开始日期</label>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">结束日期</label>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="text-[10px] text-slate-500 mb-1 block">策略</label>
            <select value={strategy} onChange={e => handleStrategyChange(e.target.value)}
              className="w-full border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
              {strategies.map(s => (
                <option key={s.id} value={s.id}>{s.name} {s.builtin ? '' : '(自定义)'}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Strategy params */}
        {Object.keys(params).length > 0 && (
          <div className="mt-3 pt-3 border-t border-slate-100">
            <div className="text-[10px] text-slate-500 mb-2">策略参数</div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {Object.entries(params).map(([key, val]) => (
                <div key={key}>
                  <label className="text-[10px] text-slate-400 mb-0.5 block">{key}</label>
                  <input type="number" value={val as number} step="any"
                    onChange={e => handleParamChange(key, e.target.value)}
                    className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Broker params (collapsible) */}
        <div className="mt-3 pt-3 border-t border-slate-100">
          <button onClick={() => setShowBroker(!showBroker)}
            className="text-[10px] text-slate-500 flex items-center gap-1 hover:text-slate-700">
            <Settings2 className="w-3 h-3" /> 经纪商 / 高级参数
            {showBroker ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showBroker && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">初始资金</label>
                <input type="number" value={initialCash} onChange={e => setInitialCash(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">买入佣金率</label>
                <input type="number" value={commBuy} step="0.0001" onChange={e => setCommBuy(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">卖出佣金率 (含印花税)</label>
                <input type="number" value={commSell} step="0.0001" onChange={e => setCommSell(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">最低佣金 (元)</label>
                <input type="number" value={minComm} onChange={e => setMinComm(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">滑点比例</label>
                <input type="number" value={slippage} step="0.0001" onChange={e => setSlippage(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">最小交易单位 (股)</label>
                <input type="number" value={lotSize} onChange={e => setLotSize(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">复权方式</label>
                <select value={adjust} onChange={e => setAdjust(e.target.value)}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm">
                  <option value="qfq">前复权</option>
                  <option value="hfq">后复权</option>
                  <option value="">不复权</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-400 mb-0.5 block">预热期 (bar数)</label>
                <input type="number" value={warmup} onChange={e => setWarmup(Number(e.target.value))}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" />
              </div>
              <div className="col-span-2">
                <label className="text-[10px] text-slate-400 mb-0.5 block">标签 (逗号分隔)</label>
                <input value={tags} onChange={e => setTags(e.target.value)}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" placeholder="测试,均线策略" />
              </div>
              <div className="col-span-2">
                <label className="text-[10px] text-slate-400 mb-0.5 block">备注</label>
                <input value={notes} onChange={e => setNotes(e.target.value)}
                  className="w-full border border-slate-200 rounded-lg px-2 py-1 text-sm" placeholder="可选备注..." />
              </div>
            </div>
          )}
        </div>

        {/* Run button row */}
        <div className="flex items-center gap-3 mt-5">
          <button onClick={handleRun} disabled={loading}
            className="flex items-center gap-2 bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {loading ? '回测中...' : '开始回测'}
          </button>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={withReflection} onChange={e => setWithReflection(e.target.checked)} className="rounded" />
            <span className="text-sm text-slate-600 flex items-center gap-1">
              <Brain className="w-3.5 h-3.5 text-purple-500" /> LLM 反思
            </span>
          </label>
          {result && (
            <span className="flex items-center gap-1.5 text-green-600 text-sm">
              <CheckCircle className="w-4 h-4" />
              回测完成
              {result.saved_to_history && <span className="text-slate-400 text-xs ml-1">(已保存到历史)</span>}
            </span>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-start gap-3">
          <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
          <div>
            <div className="text-sm font-semibold text-red-700">回测失败</div>
            <div className="text-sm text-red-600 mt-0.5">{error}</div>
          </div>
        </div>
      )}

      {/* Results */}
      {result && <ResultsDisplay result={result} showTrades={showTrades} setShowTrades={setShowTrades}
        showReflection={showReflection} setShowReflection={setShowReflection} />}
    </div>
  )
}

// Shared results display component
function ResultsDisplay({ result, showTrades, setShowTrades, showReflection, setShowReflection }: {
  result: BacktestResult; showTrades: boolean; setShowTrades: (v: boolean) => void
  showReflection: boolean; setShowReflection: (v: boolean) => void
}) {
  return (
    <div className="space-y-4">
      {/* Key metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="总收益率" value={pct(result.total_return)}
          color={result.total_return >= 0 ? 'text-green-600' : 'text-red-500'} />
        <MetricCard label="年化收益" value={pct(result.annualized_return)}
          color={result.annualized_return >= 0 ? 'text-green-600' : 'text-red-500'} />
        <MetricCard label="最大回撤" value={pct(result.max_drawdown)} color="text-red-500" />
        <MetricCard label="夏普比率" value={result.sharpe_ratio?.toFixed(3) || '—'}
          color={result.sharpe_ratio > 1 ? 'text-green-600' : result.sharpe_ratio > 0 ? 'text-yellow-600' : 'text-red-500'} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard label="交易次数" value={String(result.total_trades ?? 0)} />
        <MetricCard label="胜率" value={pct(result.win_rate)}
          color={result.win_rate > 0.5 ? 'text-green-600' : 'text-orange-500'} />
        <MetricCard label="盈亏比" value={result.profit_factor?.toFixed(3) || '—'}
          color={result.profit_factor > 1 ? 'text-green-600' : 'text-red-500'} />
        <MetricCard label="基准收益" value={result.benchmark_return != null ? pct(result.benchmark_return) : '—'} />
      </div>

      {/* Equity curve */}
      {result.equity_curve && result.equity_curve.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3 flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-blue-500" /> 净值曲线
          </h3>
          <EquityCurve data={result.equity_curve} />
        </div>
      )}

      {/* Stats table */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">详细统计</h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-8 gap-y-2 text-sm">
          {[
            ['初始资金', `¥${money(result.initial_cash)}`],
            ['最终净值', `¥${money(result.final_value)}`],
            ['年化波动率', pct(result.volatility || 0)],
            ['卡玛比率', result.calmar_ratio?.toFixed(3)],
            ['索提诺比率', result.sortino_ratio?.toFixed(3)],
            ['平均盈利', `¥${result.avg_profit?.toFixed(0)}`],
            ['平均亏损', `¥${result.avg_loss?.toFixed(0)}`],
            ['最大单笔盈利', `¥${result.max_single_profit?.toFixed(0)}`],
            ['最大单笔亏损', `¥${result.max_single_loss?.toFixed(0)}`],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between py-1 border-b border-slate-50">
              <span className="text-slate-500">{k}</span>
              <span className="font-medium text-slate-800">{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* LLM Reflection */}
      {result.reflection && (
        <div className="bg-gradient-to-br from-purple-50 to-indigo-50 rounded-xl border border-purple-100 shadow-sm p-5">
          <button onClick={() => setShowReflection(!showReflection)} className="w-full flex items-center justify-between">
            <h3 className="text-sm font-semibold text-purple-800 flex items-center gap-2">
              <Brain className="w-4 h-4 text-purple-600" /> AI 反思分析
            </h3>
            {showReflection ? <ChevronUp className="w-4 h-4 text-purple-500" /> : <ChevronDown className="w-4 h-4 text-purple-500" />}
          </button>
          {showReflection && (
            <div className="mt-4 space-y-4">
              {result.reflection.lessons?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-purple-700 mb-2">📚 教训提取</div>
                  <ul className="space-y-1.5">
                    {result.reflection.lessons.map((l, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-purple-800">
                        <span className="mt-0.5 h-1.5 w-1.5 rounded-full bg-purple-400 shrink-0" />{l}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {result.reflection.improvements?.length > 0 && (
                <div>
                  <div className="text-xs font-semibold text-indigo-700 mb-2">🛠 改进建议</div>
                  <ul className="space-y-1.5">
                    {result.reflection.improvements.map((imp, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-indigo-800">
                        <span className="mt-0.5 text-indigo-400 shrink-0">{i + 1}.</span>{imp}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {result.reflection.analysis && (
                <div>
                  <div className="text-xs font-semibold text-slate-600 mb-2">📝 完整分析</div>
                  <div className="text-xs text-slate-600 whitespace-pre-wrap bg-white/60 rounded-lg p-3 max-h-60 overflow-y-auto">
                    {result.reflection.analysis}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Trades */}
      {result.trade_records && result.trade_records.length > 0 && (
        <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
          <button onClick={() => setShowTrades(!showTrades)} className="w-full flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
              <ArrowUpDown className="w-4 h-4 text-slate-500" /> 成交记录 ({result.trade_records.length} 笔)
            </h3>
            {showTrades ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>
          {showTrades && (
            <div className="mt-3 overflow-x-auto max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white">
                  <tr className="text-slate-400 border-b border-slate-100">
                    <th className="py-2 text-left">日期</th>
                    <th className="py-2 text-left">标的</th>
                    <th className="py-2 text-left">方向</th>
                    <th className="py-2 text-right">数量</th>
                    <th className="py-2 text-right">价格</th>
                    <th className="py-2 text-right">盈亏</th>
                    <th className="py-2 text-right">佣金</th>
                  </tr>
                </thead>
                <tbody>
                  {result.trade_records.map((t, i) => (
                    <tr key={i} className="border-b border-slate-50 hover:bg-slate-50">
                      <td className="py-1.5 text-slate-600">{t.date}</td>
                      <td className="py-1.5 text-slate-600">{t.symbol}</td>
                      <td className={`py-1.5 font-medium ${t.direction === 'buy' ? 'text-red-500' : 'text-green-600'}`}>
                        {t.direction === 'buy' ? '买入' : '卖出'}
                      </td>
                      <td className="py-1.5 text-right text-slate-700">{t.quantity}</td>
                      <td className="py-1.5 text-right text-slate-700">{t.price.toFixed(2)}</td>
                      <td className={`py-1.5 text-right font-medium ${t.pnl > 0 ? 'text-red-500' : t.pnl < 0 ? 'text-green-600' : 'text-slate-400'}`}>
                        {t.pnl !== 0 ? (t.pnl > 0 ? '+' : '') + t.pnl.toFixed(0) : '—'}
                      </td>
                      <td className="py-1.5 text-right text-slate-400">{t.commission.toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ================================================================
// Tab 4: 历史记录
// ================================================================

function HistoryTab() {
  const [runs, setRuns] = useState<HistoryRun[]>([])
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [selectedRun, setSelectedRun] = useState<any>(null)
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set())
  const [comparison, setComparison] = useState<any>(null)
  const [lessons, setLessons] = useState<any[]>([])
  const [showLessons, setShowLessons] = useState(false)
  const [filter, setFilter] = useState({ strategy: '', symbol: '' })
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (filter.strategy) params.set('strategy', filter.strategy)
      if (filter.symbol) params.set('symbol', filter.symbol)
      params.set('limit', '50')
      const r = await api(`/history?${params}`)
      setRuns(r.runs || [])
      setStats(r.stats || {})
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
    setLoading(false)
  }, [filter])

  useEffect(() => { refresh() }, [refresh])

  const loadDetail = async (runId: string) => {
    try {
      const r = await api(`/history/${runId}`)
      setSelectedRun(r)
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleDelete = async (runId: string) => {
    if (!confirm('确认删除此回测记录?')) return
    try {
      await apiDelete(`/history/${runId}`)
      setMsg({ type: 'ok', text: '已删除' })
      if (selectedRun?.run_id === runId) setSelectedRun(null)
      refresh()
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleStar = async (runId: string) => {
    try {
      const r = await apiPost(`/history/${runId}/star`, {})
      setRuns(prev => prev.map(run => run.run_id === runId ? { ...run, starred: r.starred } : run))
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const toggleCompare = (runId: string) => {
    setCompareIds(prev => {
      const next = new Set(prev)
      next.has(runId) ? next.delete(runId) : next.add(runId)
      return next
    })
  }

  const handleCompare = async () => {
    if (compareIds.size < 2) { setMsg({ type: 'err', text: '请至少选择 2 次回测进行对比' }); return }
    try {
      const r = await apiPost('/history/compare', { run_ids: Array.from(compareIds) })
      setComparison(r.comparison)
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  const handleLoadLessons = async () => {
    try {
      const r = await api('/history/lessons?limit=50')
      setLessons(r.lessons || [])
      setShowLessons(true)
    } catch (e: any) { setMsg({ type: 'err', text: e.message }) }
  }

  return (
    <div className="space-y-5">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-4 gap-3">
          <MetricCard label="总回测次数" value={String(stats.total_runs ?? 0)} />
          <MetricCard label="策略种类" value={String(stats.distinct_strategies ?? 0)} />
          <MetricCard label="标的种类" value={String(stats.distinct_symbols ?? 0)} />
          <MetricCard label="收藏数" value={String(stats.starred_count ?? 0)} />
        </div>
      )}

      {/* Filters & actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <input value={filter.strategy} onChange={e => setFilter(f => ({ ...f, strategy: e.target.value }))}
          className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-36" placeholder="筛选策略..." />
        <input value={filter.symbol} onChange={e => setFilter(f => ({ ...f, symbol: e.target.value }))}
          className="border border-slate-200 rounded-lg px-3 py-1.5 text-sm w-36" placeholder="筛选标的..." />
        <button onClick={refresh} disabled={loading}
          className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1">
          <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> 刷新
        </button>
        <div className="flex-1" />
        {compareIds.size >= 2 && (
          <button onClick={handleCompare}
            className="flex items-center gap-1 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-xs hover:bg-indigo-700">
            <ArrowUpDown className="w-3 h-3" /> 对比已选 ({compareIds.size})
          </button>
        )}
        <button onClick={handleLoadLessons}
          className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 border border-purple-200 px-3 py-1.5 rounded-lg">
          <BookOpen className="w-3 h-3" /> 全部教训
        </button>
      </div>

      {msg && (
        <div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm ${msg.type === 'ok' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
          {msg.type === 'ok' ? <CheckCircle className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {msg.text}
        </div>
      )}

      {/* Runs table */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-sm p-5">
        {runs.length === 0 ? (
          <div className="text-center py-12 text-slate-400 text-sm">暂无回测记录，去「回测执行」跑一次吧</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="py-2 text-left w-8"></th>
                  <th className="py-2 text-left">日期</th>
                  <th className="py-2 text-left">策略</th>
                  <th className="py-2 text-left">标的</th>
                  <th className="py-2 text-right">收益率</th>
                  <th className="py-2 text-right">夏普</th>
                  <th className="py-2 text-right">最大回撤</th>
                  <th className="py-2 text-right">胜率</th>
                  <th className="py-2 text-right">交易数</th>
                  <th className="py-2 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.run_id} className={`border-b border-slate-50 hover:bg-slate-50 ${compareIds.has(r.run_id) ? 'bg-indigo-50' : ''}`}>
                    <td className="py-2">
                      <input type="checkbox" checked={compareIds.has(r.run_id)}
                        onChange={() => toggleCompare(r.run_id)} className="rounded" />
                    </td>
                    <td className="py-2 text-slate-500">{r.run_date?.slice(0, 10)}</td>
                    <td className="py-2 text-slate-700 font-medium">{r.strategy_name}</td>
                    <td className="py-2 text-slate-600">{r.primary_symbol}</td>
                    <td className={`py-2 text-right font-medium ${r.total_return >= 0 ? 'text-red-500' : 'text-green-600'}`}>
                      {pct(r.total_return)}
                    </td>
                    <td className="py-2 text-right text-slate-700">{r.sharpe_ratio?.toFixed(2)}</td>
                    <td className="py-2 text-right text-red-400">{pct(r.max_drawdown)}</td>
                    <td className="py-2 text-right text-slate-600">{pct(r.win_rate)}</td>
                    <td className="py-2 text-right text-slate-500">{r.total_trades}</td>
                    <td className="py-2 text-center">
                      <button onClick={() => handleStar(r.run_id)}
                        className={`mr-1 ${r.starred ? 'text-yellow-500' : 'text-slate-300 hover:text-yellow-400'}`} title="收藏">
                        <Star className="w-3.5 h-3.5 inline" fill={r.starred ? 'currentColor' : 'none'} />
                      </button>
                      <button onClick={() => loadDetail(r.run_id)} className="text-blue-500 hover:text-blue-700 mr-1" title="详情">
                        <Eye className="w-3.5 h-3.5 inline" />
                      </button>
                      <button onClick={() => handleDelete(r.run_id)} className="text-red-400 hover:text-red-600" title="删除">
                        <Trash2 className="w-3.5 h-3.5 inline" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Comparison */}
      {comparison && Array.isArray(comparison) && comparison.length > 0 && (
        <div className="bg-white rounded-xl border border-indigo-200 shadow-sm p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-indigo-700 flex items-center gap-2">
              <ArrowUpDown className="w-4 h-4" /> 回测对比 ({comparison.length} 次)
            </h3>
            <button onClick={() => { setComparison(null); setCompareIds(new Set()) }}
              className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="py-2 text-left">策略</th>
                  <th className="py-2 text-left">标的</th>
                  <th className="py-2 text-right">收益率</th>
                  <th className="py-2 text-right">夏普</th>
                  <th className="py-2 text-right">最大回撤</th>
                  <th className="py-2 text-right">胜率</th>
                  <th className="py-2 text-right">盈亏比</th>
                </tr>
              </thead>
              <tbody>
                {comparison.map((c: any, i: number) => (
                  <tr key={i} className="border-b border-slate-50">
                    <td className="py-1.5 text-slate-700 font-medium">{c.strategy_name}</td>
                    <td className="py-1.5 text-slate-600">{c.primary_symbol}</td>
                    <td className={`py-1.5 text-right font-medium ${c.total_return >= 0 ? 'text-red-500' : 'text-green-600'}`}>
                      {pct(c.total_return)}
                    </td>
                    <td className="py-1.5 text-right">{c.sharpe_ratio?.toFixed(3)}</td>
                    <td className="py-1.5 text-right text-red-400">{pct(c.max_drawdown)}</td>
                    <td className="py-1.5 text-right">{pct(c.win_rate)}</td>
                    <td className="py-1.5 text-right">{c.profit_factor?.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Selected run detail */}
      {selectedRun && (
        <div className="bg-white rounded-xl border border-blue-200 shadow-sm p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-blue-700 flex items-center gap-2">
              <FileText className="w-4 h-4" /> 回测详情 #{selectedRun.run_id}
            </h3>
            <button onClick={() => setSelectedRun(null)} className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
          </div>
          <div className="text-xs text-slate-600 space-y-1 mb-4">
            <div>策略: <strong>{selectedRun.strategy_name}</strong> | 标的: <strong>{selectedRun.primary_symbol}</strong></div>
            <div>日期: {selectedRun.run_date} | 备注: {selectedRun.notes || '无'}</div>
            {selectedRun.tags && <div>标签: {selectedRun.tags}</div>}
          </div>
          {selectedRun.equity_curve && (
            <EquityCurve data={typeof selectedRun.equity_curve === 'string' ? JSON.parse(selectedRun.equity_curve) : selectedRun.equity_curve} />
          )}
          {selectedRun.reflection && (
            <div className="mt-4 bg-purple-50 rounded-lg p-3 text-xs text-purple-800 whitespace-pre-wrap max-h-40 overflow-y-auto">
              {typeof selectedRun.reflection === 'string' ? selectedRun.reflection : JSON.stringify(selectedRun.reflection, null, 2)}
            </div>
          )}
        </div>
      )}

      {/* Lessons */}
      {showLessons && (
        <div className="bg-gradient-to-br from-purple-50 to-indigo-50 rounded-xl border border-purple-100 shadow-sm p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-purple-800 flex items-center gap-2">
              <BookOpen className="w-4 h-4" /> 全部回测教训 ({lessons.length})
            </h3>
            <button onClick={() => setShowLessons(false)} className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
          </div>
          {lessons.length === 0 ? (
            <div className="text-center py-6 text-purple-400 text-sm">暂无教训记录</div>
          ) : (
            <div className="space-y-2 max-h-60 overflow-y-auto">
              {lessons.map((l, i) => (
                <div key={i} className="bg-white/70 rounded-lg p-3 text-xs">
                  <div className="flex items-center gap-2 mb-1 text-purple-700">
                    <span className="font-medium">{l.strategy_name}</span>
                    <span className="text-purple-400">·</span>
                    <span>{l.primary_symbol}</span>
                    <span className="text-purple-400">·</span>
                    <span className="text-purple-500">{l.run_date?.slice(0, 10)}</span>
                  </div>
                  <div className="text-slate-700">{l.lesson}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ================================================================
// Main Component with Tabs
// ================================================================

const TABS = [
  { id: 'data', label: '数据管理', icon: Database },
  { id: 'strategy', label: '策略库', icon: BookOpen },
  { id: 'run', label: '回测执行', icon: Play },
  { id: 'history', label: '历史记录', icon: History },
] as const

type TabId = typeof TABS[number]['id']

export default function Backtest() {
  const [activeTab, setActiveTab] = useState<TabId>('run')

  return (
    <div className="max-w-6xl mx-auto p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
            <BarChart2 className="w-6 h-6 text-blue-600" />
            量化回测框架
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            数据管理 → 策略编辑 → 回测执行 → 历史分析 · 专业级模拟交易系统
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-slate-100 rounded-xl p-1">
        {TABS.map(tab => {
          const Icon = tab.icon
          const active = activeTab === tab.id
          return (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                active ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}>
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'data' && <DataTab />}
        {activeTab === 'strategy' && <StrategyTab />}
        {activeTab === 'run' && <BacktestRunTab />}
        {activeTab === 'history' && <HistoryTab />}
      </div>
    </div>
  )
}
