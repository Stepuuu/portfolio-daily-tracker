// What the instrument picker says besides its rows (relative `.ts` imports only, so node tests can load it).
// An answer of GET /api/instruments/search carries `omitted[]` (the person's own sources the search could not
// read in time; each reason names its source, e.g. 持仓暂时读取不到…) and `index_reason` (the name index's
// state, also while a stale index still answers with `index_available: true`). Both are shown for every
// query, the empty one included, and for cached answers; an empty list never claims there are no holdings
// when they could not be read.
import type { InstrumentSearch } from '../../lib/instrument.ts'

/** One source the search left out (`omitted[]` of the answer). */
export interface SearchOmission {
  source: string
  reason: string
}

export interface PickerNotices {
  /** The reasons of the sources left out, as the backend wrote them. */
  omitted: string[]
  /** The name index's state, or null. */
  indexReason: string | null
  /** What an empty list says. */
  emptyText: string
}

export function pickerNotices(data: InstrumentSearch | null, query: string): PickerNotices {
  const entries = (data as (InstrumentSearch & { omitted?: SearchOmission[] | null }) | null)?.omitted ?? []
  const omitted = entries.map((entry) => entry?.reason).filter((reason): reason is string => Boolean(reason))
  const emptyText = query
    ? `没有找到“${query}”。可以直接输入代码，如 600519、00700、AAPL。`
    : omitted.length
      ? '持仓或自选暂时读不到，见下方说明。输入代码或名称仍可搜索。'
      : '还没有持仓或自选。输入代码或名称搜索。'
  return { omitted, indexReason: data?.index_reason || null, emptyText }
}
