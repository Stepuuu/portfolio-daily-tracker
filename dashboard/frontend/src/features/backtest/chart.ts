// The charts of a result: the method's net value against holding the same stock and the index, each
// relative to the start (1 = the capital), on the run's trading days; the drawdown under it (the
// service's daily `equity[].drawdown`, not recomputed); and the instrument's price (`prices`, the bars
// the run traded on) with a marker per fill and per order that did not fill. Geometry only (SVG paths,
// marker positions, the point under the cursor); the numbers shown come from the result as they are.
// Pure TypeScript so node tests can load it.
import type { Fill, RunResult, Unfilled } from './types.ts'

export interface Series {
  key: 'strategy' | 'same_stock' | 'index'
  label: string
  /** One value per date of `dates` (null: no value that day). */
  values: (number | null)[]
}

export interface ChartData {
  dates: string[]
  series: Series[]
  /** The method's drawdown per date (0 or negative). */
  drawdown: (number | null)[]
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

/** The lines to draw: the method always; a comparison only when the service computed it. */
export function chartData(result: Pick<RunResult, 'equity' | 'benchmark' | 'facts'>): ChartData {
  const equity = result.equity ?? []
  const dates = equity.map((row) => row.date)
  const position = new Map(dates.map((date, index) => [date, index]))
  const align = (curve: { date: string; value: number }[] | undefined) => {
    const values: (number | null)[] = dates.map(() => null)
    for (const point of curve ?? []) {
      const index = position.get(point.date)
      if (index !== undefined && finite(point.value)) values[index] = point.value
    }
    return values
  }
  const series: Series[] = [{ key: 'strategy', label: '这个方法', values: equity.map((row) => (finite(row.net_value) ? row.net_value : null)) }]
  const same = result.benchmark?.same_stock
  if (same && finite(same.total_return) && same.curve?.length) series.push({ key: 'same_stock', label: '一直持有', values: align(same.curve) })
  const index = result.benchmark?.index
  if (index && finite(index.total_return) && index.curve?.length) {
    series.push({ key: 'index', label: index.name || result.facts.index_name || index.ticker, values: align(index.curve) })
  }
  return { dates, series, drawdown: equity.map((row) => (finite(row.drawdown) ? row.drawdown : null)) }
}

export interface Geometry {
  width: number
  height: number
  /** SVG path per series key. */
  paths: Record<string, string>
  /** y of the start line (value 1). */
  baseY: number
  min: number
  max: number
  x: (index: number) => number
  y: (value: number) => number
}

function linePath(values: (number | null)[], x: (index: number) => number, y: (value: number) => number): string {
  let path = ''
  let pen = false
  values.forEach((value, index) => {
    if (value === null) {
      pen = false
      return
    }
    path += `${pen ? 'L' : 'M'}${x(index).toFixed(1)} ${y(value).toFixed(1)}` // exact-ok: pixel coordinates
    pen = true
  })
  return path
}

/** Paths for the lines in a `width` × `height` box with `pad` pixels above and below. */
export function equityGeometry(data: ChartData, width: number, height: number, pad = 8): Geometry | null {
  const count = data.dates.length
  if (count < 2 || width <= 0) return null
  const all = data.series.flatMap((series) => series.values.filter(finite))
  if (!all.length) return null
  let min = Math.min(1, ...all)
  let max = Math.max(1, ...all)
  if (max - min < 1e-9) {
    min -= 0.01
    max += 0.01
  }
  const x = (index: number) => (index / (count - 1)) * width
  const y = (value: number) => pad + (1 - (value - min) / (max - min)) * (height - 2 * pad)
  const paths: Record<string, string> = {}
  for (const series of data.series) paths[series.key] = linePath(series.values, x, y)
  return { width, height, paths, baseY: y(1), min, max, x, y }
}

/** The drawdown as an area hanging from 0 (top) down to the deepest point (bottom). */
export function drawdownPath(drawdown: (number | null)[], width: number, height: number): string | null {
  const count = drawdown.length
  const values = drawdown.filter(finite)
  if (count < 2 || !values.length) return null
  const deepest = Math.min(...values)
  if (deepest >= 0) return null
  const x = (index: number) => (index / (count - 1)) * width
  const y = (value: number) => (value / deepest) * height
  let path = `M0 0`
  drawdown.forEach((value, index) => {
    path += `L${x(index).toFixed(1)} ${y(finite(value) ? Math.min(0, value) : 0).toFixed(1)}` // exact-ok: pixels
  })
  return `${path}L${width} 0Z`
}

/** The date index under a horizontal position. */
export function indexAt(count: number, width: number, offsetX: number): number {
  if (count < 2 || width <= 0) return 0
  return Math.max(0, Math.min(count - 1, Math.round((offsetX / width) * (count - 1)))) // exact-ok: an index
}

// -------------------------------------------------------------------------------------------------
// The price chart: the closes the run traded on, a marker per fill, a hollow one per unfilled order.

/** buy / sell: a fill; end_mark: the valuation of what was still held (not a sale); unfilled: an order. */
export type MarkerKind = 'buy' | 'sell' | 'end_mark' | 'unfilled'

export interface PriceMarker {
  kind: MarkerKind
  /** The fill's or the order's direction (an end mark is a sell-side valuation). */
  side: 'buy' | 'sell' | 'other'
  /** Index into `dates`. */
  index: number
  date: string
  /** Where it is drawn: the fill price; an order that did not fill sits on that day's close. */
  value: number
  fill?: Fill
  unfilled?: Unfilled
}

export interface PriceData {
  dates: string[]
  closes: (number | null)[]
  /** In date order; on one day buys, then sells, then orders that did not fill. */
  markers: PriceMarker[]
  /** Fills and orders whose day is not among the price rows: not drawn, and said so. */
  undrawn: number
}

const ORDER: Record<MarkerKind, number> = { buy: 0, sell: 1, end_mark: 2, unfilled: 3 }

const sideOf = (direction: string | null | undefined): PriceMarker['side'] =>
  direction === 'buy' || direction === 'sell' ? direction : 'other'

/** The price rows and the markers placed on them. */
export function priceData(result: Pick<RunResult, 'prices' | 'fills' | 'unfilled'>): PriceData {
  const rows = (result.prices ?? []).filter((row) => typeof row?.date === 'string')
  const dates = rows.map((row) => row.date)
  const closes = rows.map((row) => (finite(row.close) ? row.close : null))
  const position = new Map(dates.map((date, index) => [date, index]))
  const markers: PriceMarker[] = []
  let undrawn = 0
  for (const fill of result.fills ?? []) {
    const index = position.get(fill.date)
    if (index === undefined || !finite(fill.price)) {
      undrawn += 1
      continue
    }
    const mark = Boolean(fill.end_mark) || fill.reason === 'end_mark'
    const side = sideOf(fill.direction)
    markers.push({ kind: mark ? 'end_mark' : side === 'buy' ? 'buy' : 'sell', side, index, date: fill.date, value: fill.price, fill })
  }
  for (const entry of result.unfilled ?? []) {
    const date = typeof entry.date === 'string' ? entry.date : typeof entry.signal_date === 'string' ? entry.signal_date : null
    const index = date === null ? undefined : position.get(date)
    const close = index === undefined ? null : closes[index]
    if (index === undefined || close === null || date === null) {
      undrawn += 1
      continue
    }
    markers.push({ kind: 'unfilled', side: sideOf(entry.direction), index, date, value: close, unfilled: entry })
  }
  markers.sort((a, b) => a.index - b.index || ORDER[a.kind] - ORDER[b.kind])
  return { dates, closes, markers, undrawn }
}

export interface PriceGeometry {
  path: string
  /** The lowest and highest price drawn (a close or a fill price, as the result has it). */
  min: number
  max: number
  x: (index: number) => number
  y: (value: number) => number
}

/** The close line and the scale, in a `width` × `height` box with `pad` pixels above and below. */
export function priceGeometry(data: PriceData, width: number, height: number, pad = 14): PriceGeometry | null {
  const count = data.dates.length
  if (count < 2 || width <= 0) return null
  const all = [...data.closes.filter(finite), ...data.markers.map((marker) => marker.value)]
  if (!all.length) return null
  const min = Math.min(...all)
  const max = Math.max(...all)
  const x = (index: number) => (index / (count - 1)) * width
  // A flat price sits in the middle (the labels stay prices the result has, nothing is widened).
  const y = (value: number) => (max === min ? height / 2 : pad + (1 - (value - min) / (max - min)) * (height - 2 * pad))
  return { path: linePath(data.closes, x, y), min, max, x, y }
}

/** Pixels between a marker's price and the middle of its shape: buys hang below the price, sales above. */
export function markerOffset(marker: Pick<PriceMarker, 'kind' | 'side'>): number {
  if (marker.kind === 'unfilled') return 0
  return marker.kind === 'buy' ? 7.5 : -7.5
}

/** The marker nearest to a pointer position (by the middle of its shape), within `reach` pixels (null: none). */
export function markerAt(markers: PriceMarker[], geometry: Pick<PriceGeometry, 'x' | 'y'>, px: number, py: number, reach = 14): number | null {
  let best: number | null = null
  let bestDistance = reach * reach
  markers.forEach((marker, index) => {
    const dx = geometry.x(marker.index) - px
    const dy = geometry.y(marker.value) + markerOffset(marker) - py
    const distance = dx * dx + dy * dy
    if (distance <= bestDistance) {
      best = index
      bestDistance = distance
    }
  })
  return best
}
