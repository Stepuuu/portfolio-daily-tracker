import { useQuery } from '@tanstack/react-query'
import { useState, useMemo, Fragment } from 'react'
import {
  TrendingUp, Calendar, ChevronLeft, ChevronRight, ChevronDown, ChevronUp,
  RefreshCw, ArrowUpRight, ArrowDownRight, Minus, BarChart3, PieChart, Activity
} from 'lucide-react'
import { trackerService } from '@/services/tracker'
import type { TrackerSnapshot, TrackerGroup, HistoryRow } from '@/services/tracker'

// ============================================================
// Utility Helpers
// ============================================================

const fmt = (n: number, d = 2) =>
  new Intl.NumberFormat('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d }).format(n)

const fmtWan = (n: number) => fmt(n / 10000, 2) + '万'

const fmtPct = (n: number) => (n >= 0 ? '+' : '') + fmt(n, 2) + '%'

const colorClass = (n: number) =>
  n > 0 ? 'text-red-400' : n < 0 ? 'text-green-400' : 'text-slate-300'

const bgColor = (n: number) =>
  n > 0 ? 'bg-red-500/10 border-red-500/20' : n < 0 ? 'bg-green-500/10 border-green-500/20' : 'bg-slate-700/50 border-slate-600'

const Arrow = ({ n }: { n: number }) =>
  n > 0 ? <ArrowUpRight className="h-4 w-4 text-red-400" /> :
  n < 0 ? <ArrowDownRight className="h-4 w-4 text-green-400" /> :
  <Minus className="h-4 w-4 text-slate-400" />

// ============================================================
// Pie Chart: Per-group SVG donut (two pies: 进攻 + 稳健)
// ============================================================
const PIE_COLORS = [
  '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4',
  '#3b82f6', '#8b5cf6', '#ec4899', '#f43f5e', '#14b8a6',
  '#a855f7', '#6366f1', '#84cc16', '#f59e0b', '#10b981'
]

function GroupPie({ group, groupName }: { group: TrackerGroup; groupName: string }) {
  const items: { name: string; value: number; type: 'stock' | 'fund' | 'cash' }[] = []

  for (const pos of group.positions) {
    items.push({ name: pos.name, value: pos.market_value_cny, type: 'stock' })
  }
  if (group.fund !== undefined && group.fund !== 0) {
    items.push({ name: '基金', value: group.fund, type: 'fund' })
  }
  if (group.cash !== undefined && group.cash !== 0) {
    items.push({ name: '现金', value: group.cash, type: 'cash' })
  }

  // Use absolute values for weight calculation so they sum to 100%
  const absTotal = items.reduce((s, i) => s + Math.abs(i.value), 0)
  const positiveItems = items.filter(i => i.value > 0).sort((a, b) => b.value - a.value)
  const negativeItems = items.filter(i => i.value <= 0)
  const totalPositive = positiveItems.reduce((s, i) => s + i.value, 0)

  if (positiveItems.length === 0) return null

  const cx = 80, cy = 80, r = 65, innerR = 40
  let cumAngle = -Math.PI / 2

  const slices = positiveItems.map((item, idx) => {
    const pct = item.value / totalPositive
    const angle = pct * 2 * Math.PI
    const startAngle = cumAngle
    const endAngle = cumAngle + angle
    cumAngle = endAngle

    const x1 = cx + r * Math.cos(startAngle), y1 = cy + r * Math.sin(startAngle)
    const x2 = cx + r * Math.cos(endAngle), y2 = cy + r * Math.sin(endAngle)
    const ix1 = cx + innerR * Math.cos(endAngle), iy1 = cy + innerR * Math.sin(endAngle)
    const ix2 = cx + innerR * Math.cos(startAngle), iy2 = cy + innerR * Math.sin(startAngle)
    const largeArc = angle > Math.PI ? 1 : 0

    const d = [
      `M ${x1} ${y1}`, `A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`,
      `L ${ix1} ${iy1}`, `A ${innerR} ${innerR} 0 ${largeArc} 0 ${ix2} ${iy2}`, 'Z'
    ].join(' ')

    return { d, color: PIE_COLORS[idx % PIE_COLORS.length], item, pct, absPct: Math.abs(item.value) / absTotal }
  })

  return (
    <div>
      <div className="text-xs font-semibold text-slate-300 mb-2">{groupName} <span className="text-slate-500">({fmtWan(group.total_value)})</span></div>
      <div className="flex items-start gap-3">
        <svg viewBox="0 0 160 160" className="w-24 h-24 flex-shrink-0">
          {slices.map((s, i) => (
            <path key={i} d={s.d} fill={s.color} stroke="#1e293b" strokeWidth="1">
              <title>{s.item.name}: {fmtWan(s.item.value)} ({(s.absPct * 100).toFixed(1)}%)</title>
            </path>
          ))}
        </svg>
        <div className="flex-1 grid grid-cols-1 gap-y-0.5 text-[11px] max-h-32 overflow-y-auto">
          {slices.map((s, i) => (
            <div key={i} className="flex items-center space-x-1 min-w-0">
              <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: s.color }} />
              <span className="truncate text-slate-300">{s.item.name}</span>
              <span className="text-slate-500 ml-auto flex-shrink-0">{(s.absPct * 100).toFixed(1)}%</span>
            </div>
          ))}
          {negativeItems.map((item, i) => (
            <div key={`neg-${i}`} className="flex items-center space-x-1 min-w-0">
              <span className="w-2 h-2 rounded-sm flex-shrink-0 bg-slate-600" />
              <span className="truncate text-slate-500">{item.name}{item.value < 0 ? '(融资)' : ''}</span>
              <span className="text-green-400 ml-auto flex-shrink-0">{fmtWan(item.value)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function AllocationPies({ snapshot }: { snapshot: TrackerSnapshot }) {
  const entries = Object.entries(snapshot.groups)
  if (entries.length === 0) return null

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3 flex items-center space-x-2">
        <PieChart className="h-4 w-4 text-primary-400" />
        <span>资产配置</span>
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {entries.map(([name, group]) => (
          <GroupPie key={name} groupName={name} group={group} />
        ))}
      </div>
    </div>
  )
}

// ============================================================
// Quantitative Metrics Panel
// ============================================================
function QuantMetrics({ summary }: { summary: any }) {
  const s = summary
  if (!s.sharpe_ratio && s.sharpe_ratio !== 0) return null

  const metrics = [
    { label: '夏普比率', value: s.sharpe_ratio?.toFixed(2) ?? '-', good: (s.sharpe_ratio ?? 0) > 1 },
    { label: '年化波动率', value: s.volatility_annual ? s.volatility_annual.toFixed(1) + '%' : '-', good: (s.volatility_annual ?? 100) < 30 },
    { label: '胜率', value: s.win_rate ? s.win_rate.toFixed(1) + '%' : '-', good: (s.win_rate ?? 0) > 50 },
    { label: '盈亏比', value: s.profit_loss_ratio?.toFixed(2) ?? '-', good: (s.profit_loss_ratio ?? 0) > 1 },
    { label: '平均盈利', value: s.avg_win_pct ? '+' + s.avg_win_pct.toFixed(2) + '%' : '-', good: true },
    { label: '平均亏损', value: s.avg_loss_pct ? s.avg_loss_pct.toFixed(2) + '%' : '-', good: false },
    { label: '交易天数', value: s.trading_days?.toString() ?? '-', good: true },
  ]

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3 flex items-center space-x-2">
        <Activity className="h-4 w-4 text-primary-400" />
        <span>量化指标</span>
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
        {metrics.map(m => (
          <div key={m.label} className="text-center">
            <div className="text-xs text-slate-400 mb-1">{m.label}</div>
            <div className={`text-sm font-bold ${m.good ? 'text-emerald-400' : 'text-orange-400'}`}>
              {m.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ============================================================
// Bar chart for group comparison
// ============================================================
function GroupBar({ groups }: { groups: Record<string, TrackerGroup> }) {
  const entries = Object.entries(groups)
  const maxVal = Math.max(...entries.map(([, g]) => g.total_value))

  return (
    <div className="space-y-4">
      {entries.map(([name, g]) => {
        const pct = maxVal > 0 ? (g.total_value / maxVal) * 100 : 0
        const hasMargin = g.cash < 0
        const margin = hasMargin ? Math.abs(g.cash) : 0
        const grossAsset = g.positions_value + (g.fund || 0)
        const leverage = hasMargin ? grossAsset / g.total_value : 1
        return (
          <div key={name}>
            <div className="flex justify-between text-sm mb-1">
              <span className="font-medium">{name}</span>
              <span className={colorClass(g.profit)}>{fmtWan(g.total_value)} ({fmtPct(g.return_pct)})</span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-3">
              <div
                className={`h-3 rounded-full ${g.profit >= 0 ? 'bg-red-500/70' : 'bg-green-500/70'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="flex items-center space-x-3 text-xs text-slate-500 mt-1">
              <span>成本 <span className="text-slate-300">{fmtWan(g.cost_basis)}</span></span>
              <span>盈亏 <span className={colorClass(g.profit)}>{fmtWan(g.profit)}</span></span>
              {hasMargin && (
                <>
                  <span>融资 <span className="text-amber-400">{fmtWan(margin)}</span></span>
                  <span>杠杆 <span className="text-amber-400">{leverage.toFixed(2)}x</span></span>
                  <span>持仓 <span className="text-slate-400">{fmtWan(grossAsset)}</span></span>
                </>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ============================================================
// Position Table — includes fund + cash, daily P&L, fixed weights
// ============================================================
interface DisplayRow {
  name: string
  ticker: string
  quantity: number | null
  current_price: number | null
  currency: string
  market_value_cny: number
  profit_cny: number
  profit_pct: number
  weight_in_group: number
  rowType: 'stock' | 'fund' | 'cash'
}

function PositionTable({ group, groupName, prevGroup }: { group: TrackerGroup; groupName: string; prevGroup?: TrackerGroup }) {
  const [sortKey, setSortKey] = useState<string>('weight_in_group')
  const [sortAsc, setSortAsc] = useState(false)

  const rows: DisplayRow[] = useMemo(() => {
    const result: DisplayRow[] = []

    // Calculate absolute total for weight normalization (sums to 100%)
    let absTotal = 0
    for (const p of group.positions) absTotal += Math.abs(p.market_value_cny)
    if (group.fund !== undefined && group.fund !== 0) absTotal += Math.abs(group.fund)
    if (group.cash !== undefined && group.cash !== 0) absTotal += Math.abs(group.cash)
    if (absTotal === 0) absTotal = 1

    // Build a map from prev snapshot for daily P&L calculation
    const prevMap = new Map<string, number>()
    if (prevGroup) {
      for (const p of prevGroup.positions) {
        prevMap.set(p.name, p.market_value_cny)
      }
    }

    for (const p of group.positions) {
      // Daily P&L: compare market_value with previous day's market_value
      let dailyPnl = p.profit_cny  // fallback to total P&L
      let dailyPct = p.profit_pct
      if (prevGroup) {
        const prevVal = prevMap.get(p.name)
        if (prevVal !== undefined && prevVal !== 0) {
          dailyPnl = p.market_value_cny - prevVal
          dailyPct = (dailyPnl / prevVal) * 100
        }
      }

      result.push({
        name: p.name,
        ticker: p.ticker,
        quantity: p.quantity,
        current_price: p.current_price,
        currency: p.currency,
        market_value_cny: p.market_value_cny,
        profit_cny: dailyPnl,
        profit_pct: dailyPct,
        weight_in_group: (Math.abs(p.market_value_cny) / absTotal) * 100,
        rowType: 'stock',
      })
    }

    if (group.fund !== undefined && group.fund !== 0) {
      result.push({
        name: '基金',
        ticker: '-',
        quantity: null,
        current_price: null,
        currency: 'CNY',
        market_value_cny: group.fund,
        profit_cny: 0,
        profit_pct: 0,
        weight_in_group: (Math.abs(group.fund) / absTotal) * 100,
        rowType: 'fund',
      })
    }

    if (group.cash !== undefined && group.cash !== 0) {
      result.push({
        name: '现金',
        ticker: '-',
        quantity: null,
        current_price: null,
        currency: 'CNY',
        market_value_cny: group.cash,
        profit_cny: 0,
        profit_pct: 0,
        weight_in_group: (Math.abs(group.cash) / absTotal) * 100,
        rowType: 'cash',
      })
    }

    return result
  }, [group, prevGroup])

  const sorted = useMemo(() => {
    // Separate stocks from fund/cash, sort stocks only, pin fund/cash to bottom
    const stocks = rows.filter(r => r.rowType === 'stock')
    const fundRow = rows.find(r => r.rowType === 'fund')
    const cashRow = rows.find(r => r.rowType === 'cash')

    const sortedStocks = [...stocks].sort((a, b) => {
      const va = ((a as any)[sortKey] ?? 0) as number
      const vb = ((b as any)[sortKey] ?? 0) as number
      return sortAsc ? va - vb : vb - va
    })

    // Append: stocks first, then fund, then cash (always at bottom)
    const result = [...sortedStocks]
    if (fundRow) result.push(fundRow)
    if (cashRow) result.push(cashRow)
    return result
  }, [rows, sortKey, sortAsc])

  const handleSort = (key: string) => {
    if (sortKey === key) setSortAsc(!sortAsc)
    else { setSortKey(key); setSortAsc(false) }
  }

  const SortHeader = ({ k, label }: { k: string; label: string }) => (
    <th
      className="px-3 py-2 text-right text-xs font-medium text-slate-400 cursor-pointer hover:text-white select-none"
      onClick={() => handleSort(k)}
    >
      {label} {sortKey === k ? (sortAsc ? '↑' : '↓') : ''}
    </th>
  )

  const itemCount = rows.length

  return (
    <div className="bg-slate-800 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center space-x-2">
        <BarChart3 className="h-4 w-4 text-primary-400" />
        <span className="font-semibold text-sm">{groupName} 持仓明细</span>
        <span className="text-xs text-slate-500">({itemCount} 项)</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700">
              <th className="px-3 py-2 text-left text-xs font-medium text-slate-400">名称</th>
              <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">数量</th>
              <SortHeader k="current_price" label="现价" />
              <SortHeader k="market_value_cny" label="市值(¥)" />
              <SortHeader k="profit_cny" label="日盈亏(¥)" />
              <SortHeader k="profit_pct" label="日涨跌%" />
              <SortHeader k="weight_in_group" label="占比%" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((p) => {
              const isSpecial = p.rowType !== 'stock'
              const isCash = p.rowType === 'cash'
              const isFund = p.rowType === 'fund'
              const rowBg = isSpecial ? 'bg-slate-700/20' : ''

              return (
                <tr key={`${p.rowType}-${p.ticker}-${p.name}`} className={`border-b border-slate-700/50 hover:bg-slate-700/30 ${rowBg}`}>
                  <td className="px-3 py-2">
                    <div className="flex items-center space-x-2">
                      {isSpecial && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${isFund ? 'bg-blue-500/20 text-blue-400' : 'bg-amber-500/20 text-amber-400'}`}>
                          {isFund ? '基金' : '现金'}
                        </span>
                      )}
                      <div>
                        <div className="font-medium">{p.name}</div>
                        {!isSpecial && <div className="text-xs text-slate-500">{p.ticker}</div>}
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right text-slate-300">
                    {p.quantity !== null ? p.quantity.toLocaleString() : '-'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {p.current_price !== null ? (
                      <>
                        {fmt(p.current_price)}
                        {p.currency !== 'CNY' && <span className="text-xs text-slate-500 ml-1">{p.currency}</span>}
                      </>
                    ) : '-'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <span className={isCash && p.market_value_cny < 0 ? 'text-green-400' : ''}>
                      {fmtWan(p.market_value_cny)}
                    </span>
                  </td>
                  <td className={`px-3 py-2 text-right font-medium ${isSpecial ? 'text-slate-500' : colorClass(p.profit_cny)}`}>
                    {isSpecial ? '-' : `${p.profit_cny >= 0 ? '+' : ''}${fmtWan(p.profit_cny)}`}
                  </td>
                  <td className={`px-3 py-2 text-right font-medium ${isSpecial ? 'text-slate-500' : colorClass(p.profit_pct)}`}>
                    {isSpecial ? '-' : fmtPct(p.profit_pct)}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-300">{fmt(Math.abs(p.weight_in_group), 1)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ============================================================
// History Chart — SVG area chart (handles 100+ data points well)
// ============================================================
function HistoryChart({ history }: { history: HistoryRow[] }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  if (history.length === 0) return <div className="text-center text-slate-500 py-8">暂无历史数据</div>

  // Include cost values in Y range calculation
  const allVals = [...history.map(h => h.total_value), ...history.map(h => h.total_cost)]
  const maxVal = Math.max(...allVals)
  const minVal = Math.min(...allVals)
  const padding = (maxVal - minVal) * 0.08 || 1
  const yMin = minVal - padding
  const yMax = maxVal + padding
  const yRange = yMax - yMin

  const W = 600
  const H = 180
  const padL = 0
  const padR = 0

  const points = history.map((h, i) => {
    const x = padL + (i / Math.max(history.length - 1, 1)) * (W - padL - padR)
    const y = H - ((h.total_value - yMin) / yRange) * H
    return { x, y, h }
  })

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
  const areaPath = linePath + ` L ${points[points.length - 1].x.toFixed(1)} ${H} L ${points[0].x.toFixed(1)} ${H} Z`

  // Dynamic cost line (polyline tracking capital injections)
  const costPoints = history.map((h, i) => {
    const x = padL + (i / Math.max(history.length - 1, 1)) * (W - padL - padR)
    const y = H - ((h.total_cost - yMin) / yRange) * H
    return { x, y }
  })
  const costLinePath = costPoints.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')

  // Color: last value vs first — overall trend
  const isUp = history[history.length - 1].total_value >= history[0].total_value
  const lineColor = isUp ? '#ef4444' : '#22c55e'
  const fillId = 'history-gradient'

  const hoverPoint = hoverIdx !== null ? points[hoverIdx] : null

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h3 className="text-sm font-semibold mb-3 flex items-center space-x-2">
        <TrendingUp className="h-4 w-4 text-primary-400" />
        <span>资产走势</span>
        {hoverPoint && (
          <span className="ml-auto text-xs text-slate-400">
            {hoverPoint.h.date} · {fmtWan(hoverPoint.h.total_value)}
            <span className="ml-1 text-slate-500">成本{fmtWan(hoverPoint.h.total_cost)}</span>
            <span className={`ml-2 ${colorClass(hoverPoint.h.daily_change)}`}>{fmtPct(hoverPoint.h.daily_change_pct)}</span>
          </span>
        )}
      </h3>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ height: 180 }}
        preserveAspectRatio="none"
        onMouseLeave={() => setHoverIdx(null)}
      >
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.3" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Dynamic cost line (tracks capital injections) */}
        <path d={costLinePath} fill="none" stroke="#94a3b8" strokeWidth="1.2" strokeDasharray="4 3" opacity="0.5" />
        {/* Cost label at the end */}
        {costPoints.length > 0 && (
          <text x={costPoints[costPoints.length - 1].x - 2} y={Math.max(costPoints[costPoints.length - 1].y - 4, 10)} fill="#94a3b8" fontSize="9" textAnchor="end" opacity="0.6">成本线</text>
        )}

        {/* Area fill */}
        <path d={areaPath} fill={`url(#${fillId})`} />
        {/* Line */}
        <path d={linePath} fill="none" stroke={lineColor} strokeWidth="1.5" strokeLinejoin="round" />

        {/* Hover hitboxes (invisible rects) */}
        {points.map((p, i) => {
          const dx = (W - padL - padR) / Math.max(history.length - 1, 1)
          return (
            <rect
              key={i}
              x={p.x - dx / 2}
              y={0}
              width={dx}
              height={H}
              fill="transparent"
              onMouseEnter={() => setHoverIdx(i)}
            />
          )
        })}

        {/* Hover dot */}
        {hoverPoint && (
          <>
            <line x1={hoverPoint.x} y1={0} x2={hoverPoint.x} y2={H} stroke="#94a3b8" strokeWidth="0.5" strokeDasharray="2 2" />
            <circle cx={hoverPoint.x} cy={hoverPoint.y} r={3} fill={lineColor} stroke="#1e293b" strokeWidth="1.5" />
          </>
        )}
      </svg>

      <div className="flex justify-between text-xs text-slate-500 mt-1">
        <span>{history[0]?.date?.slice(5)}</span>
        <span className="text-slate-400">{fmtWan(maxVal)} / {fmtWan(minVal)}</span>
        <span>{history[history.length - 1]?.date?.slice(5)}</span>
      </div>
    </div>
  )
}

// ============================================================
// Main Page
// ============================================================
export default function PortfolioTracker() {
  const [selectedDate, setSelectedDate] = useState<string | undefined>(undefined)
  const [expandedMonths, setExpandedMonths] = useState<Set<string>>(new Set())

  const { data: datesData } = useQuery({
    queryKey: ['tracker-dates'],
    queryFn: trackerService.getDates,
  })

  const { data: snapshot, isLoading: snapshotLoading } = useQuery({
    queryKey: ['tracker-snapshot', selectedDate],
    queryFn: () => trackerService.getSnapshot(selectedDate),
    retry: 1,
  })

  const dates = datesData?.dates || []
  const currentIdx = selectedDate ? dates.indexOf(selectedDate) : 0

  // Fetch previous day's snapshot for daily P&L calculation
  const prevDate = currentIdx < dates.length - 1 ? dates[currentIdx + 1] : undefined
  const { data: prevSnapshot } = useQuery({
    queryKey: ['tracker-snapshot', prevDate],
    queryFn: () => trackerService.getSnapshot(prevDate),
    enabled: !!prevDate,
    retry: 1,
  })

  const { data: historyData } = useQuery({
    queryKey: ['tracker-history'],
    queryFn: () => trackerService.getHistory(365),
  })

  const history = historyData?.history || []
  const canPrev = currentIdx < dates.length - 1
  const canNext = currentIdx > 0

  const goDate = (offset: number) => {
    const newIdx = currentIdx + offset
    if (newIdx >= 0 && newIdx < dates.length) {
      setSelectedDate(dates[newIdx])
    }
  }

  if (snapshotLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw className="h-8 w-8 animate-spin text-primary-500" />
      </div>
    )
  }

  if (!snapshot) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-slate-400 space-y-4">
        <BarChart3 className="h-16 w-16 text-slate-600" />
        <p>暂无投资组合快照数据</p>
      </div>
    )
  }

  const s = snapshot.summary

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <Calendar className="h-5 w-5 text-primary-400" />
          <h1 className="text-xl font-bold">投资组合跟踪</h1>
        </div>

        <div className="flex items-center space-x-2 bg-slate-800 rounded-lg px-3 py-1.5">
          <button onClick={() => goDate(1)} disabled={!canPrev} className="p-1 rounded hover:bg-slate-700 disabled:opacity-30">
            <ChevronLeft className="h-4 w-4" />
          </button>
          <select
            value={selectedDate || dates[0] || ''}
            onChange={(e) => setSelectedDate(e.target.value || undefined)}
            className="bg-transparent text-sm font-medium border-none focus:outline-none cursor-pointer"
          >
            {dates.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <button onClick={() => goDate(-1)} disabled={!canNext} className="p-1 rounded hover:bg-slate-700 disabled:opacity-30">
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      {(() => {
        // Calculate leverage info from negative cash
        let totalMargin = 0
        let totalPositionsVal = 0
        for (const g of Object.values(snapshot.groups)) {
          if (g.cash < 0) totalMargin += Math.abs(g.cash)
          totalPositionsVal += g.positions_value + (g.fund || 0)
        }
        const leverageRatio = totalMargin > 0 ? (totalPositionsVal + totalMargin) / s.total_value : 1
        const netAsset = s.total_value
        // Use market_daily_change (excludes capital injections) for rate display
        const mdc = s.market_daily_change ?? s.daily_change
        const prevNetAsset = netAsset - mdc
        const netDailyPct = prevNetAsset !== 0 ? (mdc / prevNetAsset) * 100 : 0
        // Gross asset daily change rate (based on total positions incl. margin)
        const grossAsset = totalPositionsVal + totalMargin
        const prevGrossAsset = grossAsset - mdc
        const grossDailyPct = prevGrossAsset !== 0 ? (mdc / prevGrossAsset) * 100 : 0

        return (
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <div className={`rounded-lg p-4 border ${bgColor(s.total_profit)}`}>
          <div className="text-xs text-slate-400 mb-1">总资产</div>
          <div className="text-xl font-bold">{fmtWan(s.total_value)}</div>
          <div className={`text-sm mt-1 ${colorClass(s.total_profit)}`}>
            <Arrow n={s.total_profit} />
            <span className="ml-1">{fmtPct(s.total_return_pct)}</span>
          </div>
          {totalMargin > 0 && (
            <div className="text-xs text-slate-500 mt-1 space-y-0.5">
              <div>融资 {fmtWan(totalMargin)} · 杠杆 {leverageRatio.toFixed(2)}x</div>
            </div>
          )}
        </div>

        <div className={`rounded-lg p-4 border ${bgColor(s.total_profit)}`}>
          <div className="text-xs text-slate-400 mb-1">总盈亏</div>
          <div className={`text-xl font-bold ${colorClass(s.total_profit)}`}>
            {s.total_profit >= 0 ? '+' : ''}{fmtWan(s.total_profit)}
          </div>
          <div className="text-xs text-slate-500 mt-1">成本 {fmtWan(s.total_cost)}</div>
        </div>

        <div className={`rounded-lg p-4 border ${bgColor(mdc)}`}>
          <div className="text-xs text-slate-400 mb-1">今日盈亏</div>
          <div className={`text-xl font-bold ${colorClass(mdc)}`}>
            {mdc >= 0 ? '+' : ''}{fmtWan(mdc)}
          </div>
          <div className="text-xs mt-1 space-y-0.5">
            <div className={colorClass(netDailyPct)}>
              净资产 {fmtPct(netDailyPct)}
            </div>
            {totalMargin > 0 && (
              <div className={colorClass(grossDailyPct)}>
                总资产 {fmtPct(grossDailyPct)}
              </div>
            )}
            {!!s.capital_change && Math.abs(s.capital_change) > 100 && (
              <div className="text-slate-500">
                {s.capital_change > 0 ? '注资' : '取出'} {fmtWan(Math.abs(s.capital_change))}
              </div>
            )}
          </div>
        </div>

        <div className={`rounded-lg p-4 border ${bgColor(s.month_market_change ?? s.month_change)}`}>
          <div className="text-xs text-slate-400 mb-1">本月收益</div>
          <div className={`text-xl font-bold ${colorClass(s.month_market_change ?? s.month_change)}`}>
            {fmtWan(s.month_market_change ?? s.month_change)}
          </div>
          <div className={`text-sm mt-1 ${colorClass(s.month_return_pct)}`}>
            {fmtPct(s.month_return_pct)}
          </div>
        </div>

        <div className="rounded-lg p-4 border bg-slate-700/30 border-slate-600">
          <div className="text-xs text-slate-400 mb-1">最大回撤</div>
          <div className={`text-xl font-bold ${s.max_drawdown_pct < -10 ? 'text-red-400' : s.max_drawdown_pct < -5 ? 'text-orange-400' : 'text-slate-200'}`}>
            {fmt(s.max_drawdown_pct, 2)}%
          </div>
          <div className="text-xs text-slate-500 mt-1">
            TWR 调整
          </div>
        </div>
      </div>
        )
      })()}

      {/* Group + Chart + Pie — responsive: 1 col mobile, 2 col md, 3 col xl */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div className="bg-slate-800 rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-3">分组对比</h3>
          <GroupBar groups={snapshot.groups} />
        </div>
        <HistoryChart history={history} />
        <AllocationPies snapshot={snapshot} />
      </div>

      {/* Quant Metrics */}
      <QuantMetrics summary={s} />

      {/* FX Rates */}
      {snapshot.fx_rates && (
        <div className="flex items-center space-x-4 text-xs text-slate-500">
          <span>汇率:</span>
          {Object.entries(snapshot.fx_rates).map(([k, v]) => (
            k !== 'CNY' && <span key={k}>{k}/CNY = {Number(v).toFixed(4)}</span>
          ))}
          <span className="ml-auto">快照时间: {snapshot.generated_at?.slice(0, 19)}</span>
        </div>
      )}

      {/* Position Tables per Group */}
      <div className="space-y-4">
        {Object.entries(snapshot.groups).map(([name, group]) => (
          <PositionTable key={name} groupName={name} group={group} prevGroup={prevSnapshot?.groups?.[name]} />
        ))}
      </div>

      {/* Daily Returns Table — collapsible monthly sections */}
      {history.length > 0 && (() => {
        // Group history by month
        const monthGroups: { month: string; rows: HistoryRow[] }[] = []
        const monthMap = new Map<string, HistoryRow[]>()
        for (const h of history) {
          const m = h.date.slice(0, 7) // YYYY-MM
          if (!monthMap.has(m)) monthMap.set(m, [])
          monthMap.get(m)!.push(h)
        }
        const sortedMonths = [...monthMap.keys()].sort().reverse()
        const latestMonth = sortedMonths[0]

        // Initialize expandedMonths with current month on first render
        if (expandedMonths.size === 0 && latestMonth) {
          // Use a microtask to avoid setState during render
          queueMicrotask(() => setExpandedMonths(new Set([latestMonth])))
        }

        for (const m of sortedMonths) {
          const rows = monthMap.get(m)!.sort((a, b) => b.date.localeCompare(a.date))
          monthGroups.push({ month: m, rows })
        }

        const toggleMonth = (m: string) => {
          setExpandedMonths(prev => {
            const next = new Set(prev)
            if (next.has(m)) next.delete(m)
            else next.add(m)
            return next
          })
        }

        return (
          <div className="bg-slate-800 rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-700">
              <h3 className="text-sm font-semibold">每日收益记录</h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-700">
                    <th className="px-3 py-2 text-left text-xs font-medium text-slate-400">日期</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">净资产</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">成本</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">日盈亏</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">日收益率</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">累计收益率</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-slate-400">回撤</th>
                  </tr>
                </thead>
                <tbody>
                  {monthGroups.map(({ month, rows: monthRows }) => {
                    const isExpanded = expandedMonths.has(month)
                    const monthPnl = monthRows.reduce((s, r) => s + (r.market_daily_change ?? r.daily_change), 0)
                    return (
                      <Fragment key={month}>
                        <tr
                          className="border-b border-slate-600 bg-slate-700/40 cursor-pointer hover:bg-slate-700/60"
                          onClick={() => toggleMonth(month)}
                        >
                          <td className="px-3 py-2 font-medium text-slate-200 flex items-center space-x-1" colSpan={1}>
                            {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                            <span>{month}</span>
                            <span className="text-xs text-slate-500 ml-1">({monthRows.length}天)</span>
                          </td>
                          <td className="px-3 py-2 text-right text-slate-300">{fmtWan(monthRows[0].total_value)}</td>
                          <td className="px-3 py-2 text-right text-slate-400">{fmtWan(monthRows[0].total_cost)}</td>
                          <td className={`px-3 py-2 text-right font-medium ${colorClass(monthPnl)}`}>
                            {monthPnl >= 0 ? '+' : ''}{fmtWan(monthPnl)}
                          </td>
                          <td className="px-3 py-2" colSpan={3}></td>
                        </tr>
                        {isExpanded && monthRows.map((h) => (
                          <tr
                            key={h.date}
                            className="border-b border-slate-700/50 hover:bg-slate-700/30 cursor-pointer"
                            onClick={() => setSelectedDate(h.date)}
                          >
                            <td className="px-3 py-2 pl-6">{h.date}</td>
                            <td className="px-3 py-2 text-right">{fmtWan(h.total_value)}</td>
                            <td className="px-3 py-2 text-right text-slate-400">{fmtWan(h.total_cost)}</td>
                            <td className={`px-3 py-2 text-right ${colorClass(h.market_daily_change ?? h.daily_change)}`}>
                              {(h.market_daily_change ?? h.daily_change) >= 0 ? '+' : ''}{fmtWan(h.market_daily_change ?? h.daily_change)}
                              {!!h.capital_change && Math.abs(h.capital_change) > 100 && (
                                <span className="ml-1 text-xs text-slate-500">({h.capital_change > 0 ? '+' : ''}{fmtWan(h.capital_change)}注资)</span>
                              )}
                            </td>
                            <td className={`px-3 py-2 text-right font-medium ${colorClass(h.market_daily_change_pct ?? h.daily_change_pct)}`}>
                              {fmtPct(h.market_daily_change_pct ?? h.daily_change_pct)}
                            </td>
                            <td className={`px-3 py-2 text-right ${colorClass(h.return_pct)}`}>
                              {fmtPct(h.return_pct)}
                            </td>
                            <td className={`px-3 py-2 text-right ${h.max_drawdown_pct < -10 ? 'text-red-400' : h.max_drawdown_pct < -5 ? 'text-orange-400' : 'text-slate-400'}`}>
                              {fmt(h.max_drawdown_pct, 2)}%
                            </td>
                          </tr>
                        ))}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
