// Geometry of the stock drawer's daily mini chart (`GET /api/market/bars`): a closing-price line with
// its area, the high and low of the window and the last bar. Coordinates only; the component draws them
// with token colours. The window's return is exact decimal arithmetic on the closes as the API wrote
// them, never binary floating point. Pure TypeScript so node tests can load it.
import { percentChange } from './decimal.ts'

export interface Bar {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number | null
}

export interface BarsPayload {
  ticker: string
  currency: string
  source: string
  provider: string
  adjust: string
  adjust_label: string
  adjust_note: string
  fetched_at: string
  data_date: string
  last_bar_provisional: boolean
  calendar: string
  coverage: { rows: number; complete: boolean; first: string | null; last: string | null }
  bars: Bar[]
}

export interface Point {
  x: number
  y: number
  value: number
  date: string
  index: number
}

export interface ChartGeometry {
  /** SVG path of the closing prices. */
  line: string
  /** The same path closed down to the bottom edge, for a faint fill. */
  area: string
  first: Point
  last: Point
  high: Point
  low: Point
  /**
   * Change over the window in percent of the first close, a decimal string rounded half away from zero
   * to two places ("0.01" for 200 → 200.01); null when the first close is 0 (不可用, never 0.00%).
   */
  changePct: string | null
  points: Point[]
}

export interface ChartBox {
  width: number
  height: number
  /** Space kept free above and below the line (labels). */
  padTop?: number
  padBottom?: number
}

const round = (value: number) => Math.round(value * 10) / 10

/** The chart's shapes, or null when fewer than two bars have a close. */
export function chartGeometry(bars: Bar[], box: ChartBox): ChartGeometry | null {
  const usable = bars
    .map((bar, index) => ({ bar, index }))
    .filter(({ bar }) => typeof bar.close === 'number' && Number.isFinite(bar.close))
  if (usable.length < 2) return null
  const closes = usable.map(({ bar }) => bar.close as number)
  let min = Math.min(...closes)
  let max = Math.max(...closes)
  if (max === min) {
    max += Math.abs(max) * 0.01 || 1
    min -= Math.abs(min) * 0.01 || 1
  }
  const top = box.padTop ?? 8
  const bottom = box.height - (box.padBottom ?? 8)
  const step = box.width / (usable.length - 1)
  const points: Point[] = usable.map(({ bar }, position) => ({
    x: round(position * step),
    y: round(bottom - ((bar.close as number) - min) / (max - min) * (bottom - top)),
    value: bar.close as number,
    date: bar.date,
    index: position,
  }))
  const line = points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ')
  const area = `${line} L${points[points.length - 1].x} ${box.height} L${points[0].x} ${box.height} Z`
  const high = points.reduce((best, point) => (point.value > best.value ? point : best))
  const low = points.reduce((best, point) => (point.value < best.value ? point : best))
  const first = points[0]
  const last = points[points.length - 1]
  // The API's closes are JSON numbers; String() gives the shortest decimal that reads back as the same
  // number, i.e. the digits the backend wrote. Anything that prints with an exponent is not read.
  return { line, area, first, last, high, low, changePct: percentChange(String(first.value), String(last.value), 2), points }
}

/** The bar nearest to an x position (hover / keyboard read-out). */
export function nearestPoint(geometry: ChartGeometry, x: number): Point {
  let best = geometry.points[0]
  for (const point of geometry.points) if (Math.abs(point.x - x) < Math.abs(best.x - x)) best = point
  return best
}

export const RANGES: { value: string; label: string; days: number }[] = [
  { value: '1m', label: '1个月', days: 22 },
  { value: '3m', label: '3个月', days: 66 },
  { value: '6m', label: '6个月', days: 132 },
]
