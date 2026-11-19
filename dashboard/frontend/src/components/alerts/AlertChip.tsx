// The alert chip on a Today holding: `提醒 2` (enabled rules for the listing, matched by the rules'
// `identity` from the backend: NYSE:BRK-B held and a rule on NYSE:BRK.B are one listing; and for the
// row's account or every account, as the plan chip). It opens the stock drawer, which lists the rules.
// All chips read one shared, cheap query (rules without quotes).
import { cx, focusRing, numeric } from '@/components/ui'
import { holdingRules } from '@/features/alerts/rules'
import { useAlertRules } from '@/components/market/hooks'
import { openStockDrawer } from '@/components/market/stockDrawerStore'

export function AlertChip({ ticker, account }: { ticker: string; account?: string | null }) {
  const rules = useAlertRules(false)
  // Today's row extras pass the ticker: its identity is the same rule as the backend's (lib/instrument.ts).
  const count = holdingRules(rules.data?.items, ticker, account).length
  if (!count) return null
  return (
    <button
      type="button"
      onClick={() => openStockDrawer(ticker)}
      title={`${count} 条启用的提醒，点开查看`}
      className={cx('inline-flex h-5 items-center rounded-full bg-fg/[.08] px-2 text-caption font-medium text-fg-2 hover:bg-fg/[.12] hover:text-fg', numeric, focusRing)}
    >
      提醒 {count}
    </button>
  )
}

/** For components/today/rowExtras.tsx: `(ticker, account?)`. */
export const renderAlertChip = (ticker: string, account?: string) => <AlertChip ticker={ticker} account={account} />
