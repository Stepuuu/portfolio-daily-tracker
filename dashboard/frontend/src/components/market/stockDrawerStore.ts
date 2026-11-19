// Which stock the drawer shows. Any page opens it with `openStockDrawer(ticker, name)`: the watchlist,
// the global search, Today's holdings. The drawer itself is mounted once by the shell (GlobalSearch).
import { create } from 'zustand'

interface StockDrawerState {
  ticker: string | null
  /** The display name when the opener knows it; the drawer looks it up otherwise. */
  name: string | null
  open: (ticker: string, name?: string | null) => void
  close: () => void
}

export const useStockDrawer = create<StockDrawerState>((set) => ({
  ticker: null,
  name: null,
  open: (ticker, name = null) => set({ ticker, name }),
  close: () => set({ ticker: null, name: null }),
}))

/** Opens the stock drawer for `ticker` (an `EXCH:CODE` ledger ticker or `IDX:` index). */
export function openStockDrawer(ticker: string, name?: string | null) {
  useStockDrawer.getState().open(ticker, name ?? null)
}
