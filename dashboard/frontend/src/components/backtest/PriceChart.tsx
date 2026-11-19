// The instrument's price over the run (the closes it traded on, adjusted as the run was) with a marker
// per fill: a buy below its price, a sale above it, the end mark hollow in amber (a valuation, not a
// sale), an order that did not fill as a hollow circle on that day's close. Hover or the arrow keys
// show a marker's date, side, price, quantity and reason. Plain SVG in token colours: buy and sell are
// not gains or losses, so they are told apart by shape, not by red and green.
import { useState, type KeyboardEvent } from 'react'
import { cx, numeric } from '@/components/ui'
import { markerAt, markerOffset, priceData, priceGeometry, type PriceMarker } from '@/features/backtest/chart'
import { adjustText, endMarkOf, markerCounts, markerLines, priceSummary, priceText } from '@/features/backtest/result'
import type { RunResult } from '@/features/backtest/types'
import { useWidth } from './hooks'

const HEIGHT = 240

/** A marker's shape around (x, y): buys point up from below, sales point down from above. */
function Shape({ marker, x, y, active }: { marker: PriceMarker; x: number; y: number; active: boolean }) {
  if (marker.kind === 'unfilled') {
    return <circle cx={x} cy={y} r={active ? 5 : 4} fill="none" strokeWidth={1.5} className="stroke-fg-3" />
  }
  const middle = y + markerOffset(marker)
  const tip = middle > y ? middle - 4.5 : middle + 4.5
  const base = middle > y ? middle + 4.5 : middle - 4.5
  const points = `${x.toFixed(1)},${tip.toFixed(1)} ${(x - 5).toFixed(1)},${base.toFixed(1)} ${(x + 5).toFixed(1)},${base.toFixed(1)}` // exact-ok: pixels
  if (marker.kind === 'end_mark') return <polygon points={points} fill="none" strokeWidth={active ? 2 : 1.5} className="stroke-warn" />
  return <polygon points={points} strokeWidth={active ? 1.5 : 0} className={marker.kind === 'buy' ? 'fill-accent stroke-fg' : 'fill-fg stroke-accent'} />
}

function LegendShape({ kind }: { kind: PriceMarker['kind'] }) {
  return (
    <svg width={12} height={12} aria-hidden className="shrink-0">
      {kind === 'unfilled' ? <circle cx={6} cy={6} r={4} fill="none" strokeWidth={1.5} className="stroke-fg-3" />
        : kind === 'buy' ? <polygon points="6,1 1,11 11,11" className="fill-accent" />
          : kind === 'sell' ? <polygon points="6,11 1,1 11,1" className="fill-fg" />
            : <polygon points="6,11 1,1 11,1" fill="none" strokeWidth={1.5} className="stroke-warn" />}
    </svg>
  )
}

export function PriceChart({ result }: { result: RunResult }) {
  const data = priceData(result)
  const [box, width] = useWidth()
  const [active, setActive] = useState<number | null>(null)
  const facts = result.facts
  const geometry = width > 0 ? priceGeometry(data, width, HEIGHT) : null
  const endMark = endMarkOf(result)
  const counts = markerCounts(data)
  const markers = data.markers
  const count = data.dates.length

  if (!result.prices?.length) {
    return (
      <section aria-label="价格与买卖点" data-chart="price" className="flex min-w-0 flex-col gap-2">
        <h3 className="text-title text-fg">价格与买卖点</h3>
        <p className="text-table text-fg-3">不可用：这条结果里没有价格数据（较早保存的记录没有这一项）。</p>
      </section>
    )
  }

  const onKeyDown = (event: KeyboardEvent) => {
    if (!markers.length) return
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const step = event.key === 'ArrowLeft' ? -1 : 1
      setActive((current) => Math.max(0, Math.min(markers.length - 1, current === null ? (step > 0 ? 0 : markers.length - 1) : current + step)))
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setActive(event.key === 'Home' ? 0 : markers.length - 1)
    } else if (event.key === 'Escape' && active !== null) {
      event.stopPropagation()
      setActive(null)
    }
  }
  const current = active === null ? null : markers[active] ?? null
  const reading = current ? markerLines(current, facts, endMark) : null
  const cx0 = current && geometry ? geometry.x(current.index) : 0

  return (
    <section aria-label="价格与买卖点" data-chart="price" className="flex min-w-0 flex-col gap-2">
      <h3 className="text-title text-fg">价格与买卖点</h3>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-table text-fg-2">
        <span className="inline-flex items-center gap-1.5"><span aria-hidden className="inline-block h-0.5 w-4 bg-fg-2" />收盘价</span>
        <span className="inline-flex items-center gap-1.5"><LegendShape kind="buy" />买入</span>
        <span className="inline-flex items-center gap-1.5"><LegendShape kind="sell" />卖出</span>
        {counts.endMark > 0 && <span className="inline-flex items-center gap-1.5"><LegendShape kind="end_mark" />期末估值（没有卖出）</span>}
        {counts.unfilled > 0 && <span className="inline-flex items-center gap-1.5"><LegendShape kind="unfilled" />没有成交</span>}
      </div>
      <div ref={box} className="relative min-w-0">
        {geometry ? (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            tabIndex={0}
            aria-label={priceSummary(data, facts)}
            onMouseMove={(event) => {
              const rect = event.currentTarget.getBoundingClientRect()
              setActive(markerAt(markers, geometry, event.clientX - rect.left, event.clientY - rect.top))
            }}
            onMouseLeave={() => setActive(null)}
            onBlur={() => setActive(null)}
            onKeyDown={onKeyDown}
            className="block overflow-visible rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <path d={geometry.path} fill="none" strokeWidth={1.5} strokeLinejoin="round" className="stroke-fg-2" />
            {current && <line x1={cx0} x2={cx0} y1={0} y2={HEIGHT} strokeWidth={1} className="stroke-line-input" />}
            {/* Orders that did not fill underneath, the fills on top of them. */}
            {[true, false].map((under) => markers.map((marker, index) => ((marker.kind === 'unfilled') === under ? (
              <g key={index} data-marker={marker.kind} data-side={marker.side} data-date={marker.date}>
                <Shape marker={marker} x={geometry.x(marker.index)} y={geometry.y(marker.value)} active={index === active} />
              </g>
            ) : null)))}
            <text x={2} y={11} className={cx('fill-fg-3 text-caption', numeric)}>{priceText(geometry.max, facts)}</text>
            <text x={2} y={HEIGHT - 2} className={cx('fill-fg-3 text-caption', numeric)}>{priceText(geometry.min, facts)}</text>
          </svg>
        ) : (
          <p className="py-8 text-table text-fg-3">{count < 2 ? '不足两个交易日，画不出价格走势。' : ' '}</p>
        )}
        {geometry && reading && (
          <div
            role="status"
            data-marker-reading
            className={cx('pointer-events-none absolute top-1 z-10 flex max-w-64 flex-col gap-0.5 rounded-md bg-fg px-2 py-1 text-caption text-bg shadow-overlay', numeric)}
            style={cx0 > width / 2 ? { right: width - cx0 + 8 } : { left: cx0 + 8 }}
          >
            <span className="font-medium">{reading.title}</span>
            {reading.lines.map((line) => <span key={line} className="whitespace-normal">{line}</span>)}
          </div>
        )}
      </div>
      {geometry && (
        <div className={cx('flex justify-between gap-3 text-caption text-fg-3', numeric)}>
          <span>{data.dates[0]}</span>
          <span>{data.dates[count - 1]}</span>
        </div>
      )}
      <p className={cx('text-caption text-fg-3', numeric)}>
        买入 {counts.buy} 次 · 卖出 {counts.sell} 次
        {counts.endMark > 0 && ` · 期末估值 ${counts.endMark} 次`}
        {counts.unfilled > 0 && ` · 没有成交 ${counts.unfilled} 个`}
        {counts.undrawn > 0 && ` · ${counts.undrawn} 个日期不在价格数据里，没有画出`}
        。价格为{adjustText(facts.adjust)}价，和成交价同一口径；悬停或用方向键查看每个点。
      </p>
    </section>
  )
}
