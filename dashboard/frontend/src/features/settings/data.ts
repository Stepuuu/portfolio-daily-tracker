// 设置 › 数据与行情 without React: the bar cache's figures and the holiday table's coverage as lines. Unknown
// is 不可用. Pure TypeScript (node tests).
import { fmtQty } from '../../lib/format.ts'

export const UNAVAILABLE = '不可用'

export interface CacheFacts {
  /** False in the demo, which keeps no cache. */
  kept: boolean
  note: string | null
  rows: [string, string][]
}

const count = (value: unknown): string =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? fmtQty(String(value)) : UNAVAILABLE

const day = (value: unknown): string | null => (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : null)

/** `GET /api/backtest/data/cache` (`symbols`, `total_rows`, `earliest_date`, `latest_date`, `db_path`, `kept`). */
export function cacheFacts(stats: Record<string, unknown>): CacheFacts {
  if (stats.kept === false) return { kept: false, note: '演示模式不保存日线缓存，回测按需读取演示数据。', rows: [] }
  const first = day(stats.earliest_date)
  const last = day(stats.latest_date)
  const symbols = typeof stats.symbols === 'number' ? stats.symbols : null
  const range = first && last ? `${first} 至 ${last}` : symbols === 0 ? '还没有数据' : UNAVAILABLE
  const rows: [string, string][] = [
    ['标的', count(stats.symbols)],
    ['日线', count(stats.total_rows)],
    ['日期范围', range],
  ]
  if (typeof stats.db_path === 'string' && stats.db_path) rows.push(['文件', stats.db_path])
  return { kept: true, note: null, rows }
}

/**
 * "2026 年已收录" / "2026–2027 年已收录 · 2028 年起按工作日推算"; amber when this year is not in the table
 * (today's trading day is then a guess).
 */
export function calendarLine(years: number[], today: string | null | undefined): { text: string; warn: boolean } {
  const sorted = [...new Set(years.filter((year) => Number.isInteger(year)))].sort((a, b) => a - b)
  const current = today && /^\d{4}/.test(today) ? Number(today.slice(0, 4)) : null
  if (!sorted.length) return { text: '没有节假日表：全部按工作日推算', warn: true }
  const contiguous = sorted.every((year, index) => index === 0 || year === sorted[index - 1] + 1)
  const listed = contiguous && sorted.length > 1 ? `${sorted[0]}–${sorted[sorted.length - 1]}` : sorted.join('、')
  const after = `${sorted[sorted.length - 1] + 1} 年起按工作日推算`
  const missing = current !== null && !sorted.includes(current)
  return { text: `${listed} 年已收录 · ${missing ? `今年（${current}）按工作日推算` : after}`, warn: missing }
}
