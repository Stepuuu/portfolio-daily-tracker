// The stock drawer's daily chart: closing prices over 1, 3 or 6 months as a plain SVG line (token
// colours, no chart library), the window's high and low, and the labels that keep it honest: the
// adjustment (前复权), the data source, and a last bar that is still moving during the session.
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge, SegmentedControl, Skeleton, cx, numeric } from '@/components/ui'
import { UNAVAILABLE, fmtNumber, fmtPct, toneOf } from '@/lib/format'
import { RANGES, chartGeometry, nearestPoint, type ChartGeometry } from '@/features/market/chart'
import { fetchBars } from '@/services/market'
import { errorText } from './hooks'

const HEIGHT = 148
const PAD_TOP = 18
const PAD_BOTTOM = 18

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

function Plot({ geometry, digits, provisional, width }: { geometry: ChartGeometry; digits: number; provisional: boolean; width: number }) {
  const [cursor, setCursor] = useState<number | null>(null)
  const tone = toneOf(geometry.changePct)
  const stroke = tone === 'up' ? 'stroke-up' : tone === 'down' ? 'stroke-down' : 'stroke-fg-2'
  const fill = tone === 'up' ? 'fill-up/10' : tone === 'down' ? 'fill-down/10' : 'fill-fg/5'
  const point = cursor === null ? null : geometry.points[cursor]
  const label = (value: number) => fmtNumber(value, { digits })
  const points = geometry.points
  const beforeLast = points[points.length - 2]
  // The provisional last segment is dashed; the rest of the line stops one bar earlier.
  const solid = provisional ? points.slice(0, -1).map((p, i) => `${i ? 'L' : 'M'}${p.x} ${p.y}`).join(' ') : geometry.line
  const anchor = (x: number) => (x < 40 ? 'start' : x > width - 40 ? 'end' : 'middle')
  const onKeyDown = (event: KeyboardEvent) => {
    const steps: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1 }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setCursor(event.key === 'Home' ? 0 : points.length - 1)
    } else if (steps[event.key]) {
      event.preventDefault()
      setCursor((current) => Math.max(0, Math.min(points.length - 1, (current ?? points.length - 1) + steps[event.key])))
    } else if (event.key === 'Escape' && cursor !== null) {
      event.stopPropagation()
      event.preventDefault()
      setCursor(null)
    }
  }
  return (
    <div className="relative">
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        tabIndex={0}
        aria-label={`收盘价走势：${geometry.first.date} ${label(geometry.first.value)}，${geometry.last.date} ${label(geometry.last.value)}，区间涨跌 ${geometry.changePct === null ? UNAVAILABLE : fmtPct(geometry.changePct, { signed: true })}；最高 ${label(geometry.high.value)}，最低 ${label(geometry.low.value)}。左右方向键逐日查看。`}
        onMouseMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect()
          setCursor(nearestPoint(geometry, event.clientX - box.left).index)
        }}
        onMouseLeave={() => setCursor(null)}
        onKeyDown={onKeyDown}
        onBlur={() => setCursor(null)}
        className="block overflow-visible rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <path d={geometry.area} className={fill} />
        <path d={solid} fill="none" strokeWidth={1.5} strokeLinejoin="round" className={stroke} />
        {provisional && beforeLast && (
          <path d={`M${beforeLast.x} ${beforeLast.y} L${geometry.last.x} ${geometry.last.y}`} fill="none" strokeWidth={1.5} strokeDasharray="3 3" className="stroke-warn" />
        )}
        <circle cx={geometry.last.x} cy={geometry.last.y} r={3} className={provisional ? 'fill-warn' : tone === 'down' ? 'fill-down' : tone === 'up' ? 'fill-up' : 'fill-fg-2'} />
        <text x={geometry.high.x} y={geometry.high.y - 6} textAnchor={anchor(geometry.high.x)} className={cx('fill-fg-3 text-caption', numeric)}>
          高 {label(geometry.high.value)}
        </text>
        <text x={geometry.low.x} y={geometry.low.y + 14} textAnchor={anchor(geometry.low.x)} className={cx('fill-fg-3 text-caption', numeric)}>
          低 {label(geometry.low.value)}
        </text>
        {point && (
          <>
            <line x1={point.x} x2={point.x} y1={0} y2={HEIGHT} strokeWidth={1} className="stroke-line-input" />
            <circle cx={point.x} cy={point.y} r={3.5} className="fill-surface stroke-fg" strokeWidth={1.5} />
          </>
        )}
      </svg>
      {point && (
        <div
          className={cx('pointer-events-none absolute top-0 rounded-md bg-fg px-2 py-0.5 text-caption text-bg shadow-overlay', numeric)}
          style={point.x > width / 2 ? { right: width - point.x + 8 } : { left: point.x + 8 }}
          aria-live="polite"
        >
          {point.date} 收 {label(point.value)}
          {provisional && point.index === points.length - 1 ? '（盘中）' : ''}
        </div>
      )}
    </div>
  )
}

export function MiniChart({ ticker, digits }: { ticker: string; digits: number }) {
  const [range, setRange] = useState('3m')
  const days = RANGES.find((item) => item.value === range)!.days
  const [box, width] = useWidth()
  const index = ticker.toUpperCase().startsWith('IDX:')
  const bars = useQuery({
    queryKey: ['bars', ticker, days],
    queryFn: ({ signal }) => fetchBars(ticker, days, signal),
    enabled: !index,
    staleTime: 5 * 60_000,
    // A moving last bar is refreshed now and then; a finished day is not.
    refetchInterval: (query) => (query.state.data?.last_bar_provisional ? 60_000 : false),
    retry: false,
  })
  const data = bars.data
  const geometry = data && width > 0 ? chartGeometry(data.bars, { width, height: HEIGHT, padTop: PAD_TOP, padBottom: PAD_BOTTOM }) : null
  return (
    <section aria-label="日线" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-table font-medium text-fg-2">日线</h3>
        {!index && <SegmentedControl size="sm" ariaLabel="日线区间" options={RANGES.map(({ value, label }) => ({ value, label }))} value={range} onChange={setRange} />}
      </div>
      <div ref={box} className="min-h-[148px]">
        {index ? (
          <p className="py-6 text-table text-fg-3">指数暂不提供日线。</p>
        ) : bars.isPending ? (
          <Skeleton h={HEIGHT} />
        ) : bars.isError ? (
          <p className="py-6 text-table text-fg-3">{errorText(bars.error)}</p>
        ) : geometry ? (
          <Plot geometry={geometry} digits={digits} provisional={Boolean(data?.last_bar_provisional)} width={width} />
        ) : (
          data && <p className="py-6 text-table text-fg-3">日线不足两天，画不出走势。</p>
        )}
      </div>
      {data && geometry && (
        <div className={cx('flex flex-col gap-1.5 text-caption text-fg-3', numeric)}>
          <div className="flex justify-between gap-3">
            <span>{geometry.first.date}</span>
            <span>{geometry.last.date}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1" title={data.adjust_note}>
            <span>{data.adjust_label}</span>
            <span aria-hidden>·</span>
            <span>{data.source}</span>
            {data.last_bar_provisional && <Badge tone="warn" title="最后一根是今天尚未收盘的数据，收盘前还会变化">最后一根为盘中数据</Badge>}
            {!data.coverage.complete && <Badge tone="warn" title="返回的交易日少于请求，或最新一根不够新">数据不完整</Badge>}
          </div>
        </div>
      )}
    </section>
  )
}
