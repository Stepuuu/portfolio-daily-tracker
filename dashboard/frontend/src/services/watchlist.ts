// The watchlist (backend/api/watchlist.py): list with quotes, add, note / order, remove. The person's
// own changes are saved directly; demo mode answers every write with 403.
import api from './api'
import type { WatchlistItem, WatchlistPayload } from '@/features/market/watchlist'

export type { WatchlistItem, WatchlistPayload }

/** Root key: invalidating it refreshes the page's list and every membership check. */
export const WATCHLIST_KEY = ['watchlist'] as const

const path = (ticker: string) => `/watchlist/${encodeURIComponent(ticker)}`

export async function fetchWatchlist(options: { quotes?: boolean; signal?: AbortSignal } = {}): Promise<WatchlistPayload> {
  const response = await api.get<WatchlistPayload>('/watchlist', { params: { quotes: options.quotes === false ? 0 : 1 }, signal: options.signal })
  return response.data
}

export async function addToWatchlist(ticker: string, note?: string | null): Promise<WatchlistItem> {
  return (await api.post<WatchlistItem>('/watchlist', note ? { ticker, note } : { ticker })).data
}

/** A new note (null clears it) and / or the new 0-based position. */
export async function updateWatchlistItem(ticker: string, changes: { note?: string | null; order?: number }): Promise<WatchlistItem> {
  return (await api.patch<WatchlistItem>(path(ticker), changes)).data
}

export async function removeFromWatchlist(ticker: string): Promise<{ deleted: true; item: WatchlistItem }> {
  return (await api.delete(path(ticker))).data
}
