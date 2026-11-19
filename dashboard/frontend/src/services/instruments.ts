// `GET /api/instruments/search` and `POST /api/instruments/aliases` (backend/api/instruments.py):
// code / name search over holdings, the watchlist, remembered aliases and the cached name index.
// The result types and the display rules live in lib/instrument.ts (pure, tested in node).
import api from './api'
import type { InstrumentSearch } from '@/lib/instrument'

export type { InstrumentKind, InstrumentResult, InstrumentSearch } from '@/lib/instrument'

export async function searchInstruments(q: string, limit = 10, signal?: AbortSignal): Promise<InstrumentSearch> {
  return (await api.get<InstrumentSearch>('/instruments/search', { params: { q, limit }, signal })).data
}

/** Remember which exchange a letter symbol means (`symbol`), or a display name for a ticker (`name`). */
export async function rememberInstrument(body: { ticker: string; symbol?: string; name?: string }): Promise<void> {
  await api.post('/instruments/aliases', body)
}
