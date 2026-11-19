// The method's net value against holding the same stock and the index (1 = the capital), and under it,
// as its own chart, the method's drawdown: the service's daily `equity[].drawdown`, drawn as it is (a loss,
// so green). Plain SVG in token colours; the values under the cursor are the result's own numbers.
import { useState, type KeyboardEvent } from 'react'
import { cx, numeric } from '@/components/ui'
import { UNAVAILABLE, fmtNumber } from '@/lib/format'
import { chartData, drawdownPath, equityGeometry, indexAt, type Series } from '@/features/backtest/chart'
import { pctText } from '@/features/backtest/result'
import type { RunResult } from '@/features/backtest/types'
import { useWidth } from './hooks'

const HEIGHT = 220
const DD_HEIGHT = 56

const STROKE: Record<Series['key'], string> = { strategy: 'stroke-accent', same_stock: 'stroke-fg-2', index: 'stroke-fg-3' }
const DASH: Record<Series['key'], string | undefined> = { strategy: undefined, same_stock: '5 3', index: '2 3' }
const SWATCH: Record<Series['key'], string> = { strategy: 'bg-accent', same_stock: 'bg-fg-2', index: 'bg-fg-3' }

const value = (v: number | null) => (v === null ? UNAVAILABLE : fmtNumber(v, { exact: true, digits: 2 }))

export function EquityChart({ result }: { result: RunResult }) {
  const data = chartData(result)
  const [box, width] = useWidth()
  const [cursor, setCursor] = useState<number | null>(null)
  const geometry = width > 0 ? equityGeometry(data, width, HEIGHT) : null
  const drawdown = width > 0 ? drawdownPath(data.drawdown, width, DD_HEIGHT) : null
  const count = data.dates.length
  const facts = result.facts
  const summary = `净值走势，${data.dates[0] ?? ''} 至 ${data.dates[count - 1] ?? ''}，1 表示本金：${data.series.map((series) => {
    const last = series.values[count - 1]
    return `${series.label} 期末 ${value(last ?? null)}`
  }).join('，')}；最大回撤 ${pctText(facts.max_drawdown)}。左右方向键逐日查看。`
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault()
      const step = event.key === 'ArrowLeft' ? -1 : 1
      setCursor((current) => Math.max(0, Math.min(count - 1, (current ?? count - 1) + step)))
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      setCursor(event.key === 'Home' ? 0 : count - 1)
    } else if (event.key === 'Escape' && cursor !== null) {
      event.stopPropagation()
      setCursor(null)
    }
  }
  const at = cursor === null ? null : cursor
  return (
    <section aria-label="净值走势" data-chart="equity" className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-table text-fg-2">
        {data.series.map((series) => (
          <span key={series.key} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={cx('inline-block h-0.5 w-4', SWATCH[series.key])} />
            {series.label}
          </span>
        ))}
        <span className="text-fg-3">1 = 本金</span>
      </div>
      <div ref={box} className="relative min-w-0">
        {geometry ? (
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            tabIndex={0}
            aria-label={summary}
            onMouseMove={(event) => setCursor(indexAt(count, width, event.clientX - event.currentTarget.getBoundingClientRect().left))}
            onMouseLeave={() => setCursor(null)}
            onBlur={() => setCursor(null)}
            onKeyDown={onKeyDown}
            className="block overflow-visible rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <line x1={0} x2={width} y1={geometry.baseY} y2={geometry.baseY} strokeDasharray="2 4" strokeWidth={1} className="stroke-line-input" />
            {data.series.slice().reverse().map((series) => (
              <path
                key={series.key}
                d={geometry.paths[series.key]}
                fill="none"
                strokeWidth={series.key === 'strategy' ? 2 : 1.5}
                strokeDasharray={DASH[series.key]}
                strokeLinejoin="round"
                className={STROKE[series.key]}
              />
            ))}
            <text x={2} y={12} className={cx('fill-fg-3 text-caption', numeric)}>{value(geometry.max)}</text>
            <text x={2} y={HEIGHT - 2} className={cx('fill-fg-3 text-caption', numeric)}>{value(geometry.min)}</text>
            {at !== null && <line x1={geometry.x(at)} x2={geometry.x(at)} y1={0} y2={HEIGHT} strokeWidth={1} className="stroke-line-input" />}
          </svg>
        ) : (
          <p className="py-8 text-table text-fg-3">{count < 2 ? '不足两个交易日，画不出走势。' : ' '}</p>
        )}
        {geometry && at !== null && (
          <div
            aria-live="polite"
            className={cx('pointer-events-none absolute top-1 z-10 flex flex-col gap-0.5 rounded-md bg-fg px-2 py-1 text-caption text-bg shadow-overlay', numeric)}
            style={geometry.x(at) > width / 2 ? { right: width - geometry.x(at) + 8 } : { left: geometry.x(at) + 8 }}
          >
            <span>{data.dates[at]}</span>
            {data.series.map((series) => <span key={series.key}>{series.label} {value(series.values[at])}</span>)}
            {data.drawdown[at] !== null && <span>回撤 {pctText(data.drawdown[at])}</span>}
          </div>
        )}
      </div>
      {geometry && (
        <div className={cx('flex justify-between gap-3 text-caption text-fg-3', numeric)}>
          <span>{data.dates[0]}</span>
          <span>{data.dates[count - 1]}</span>
        </div>
      )}
      <DrawdownStrip result={result} drawdown={drawdown} width={width} dates={data.dates} />
    </section>
  )
}

/** The drawdown under the equity chart, on the same days: the service's figures, a loss (green). */
function DrawdownStrip({ result, drawdown, width, dates }: { result: RunResult; drawdown: string | null; width: number; dates: string[] }) {
  const facts = result.facts
  const deepest = pctText(facts.max_drawdown)
  const when = facts.max_drawdown_peak && facts.max_drawdown_trough ? `（${facts.max_drawdown_peak} → ${facts.max_drawdown_trough}）` : ''
  if (!width || dates.length < 2) return null
  const summary = `回撤：这个方法每天的净值离此前最高点跌了多少，${dates[0]} 至 ${dates[dates.length - 1]}；最深 ${deepest}${when}。数字来自回测服务的每日回撤。`
  return (
    <section aria-label="回撤" data-chart="drawdown" className="flex min-w-0 flex-col gap-1">
      <span className="text-table text-fg-2">回撤（这个方法，从此前最高点下跌的幅度）</span>
      {drawdown ? (
        <svg width={width} height={DD_HEIGHT} role="img" aria-label={summary} className="block">
          <path d={drawdown} className="fill-down/20 stroke-down" strokeWidth={1} />
        </svg>
      ) : (
        <p className="text-caption text-fg-3">期间净值没有低于此前的最高点：回撤为 0。</p>
      )}
      <span className={cx('text-caption text-fg-3', numeric)}>
        最深 <span className={facts.max_drawdown ? 'text-down' : undefined}>{deepest}</span>{when}
      </span>
    </section>
  )
}
