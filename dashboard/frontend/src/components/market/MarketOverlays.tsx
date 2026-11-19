// The stock drawer and the alert dialog, mounted once for the whole app (by GlobalSearch, which the
// shell renders on every page). Their code loads the first time one of them opens.
import { Suspense, lazy, useEffect, useState } from 'react'
import { useAlertDialog } from '@/components/alerts/alertDialogStore'
import { useStockDrawer } from './stockDrawerStore'

const StockDrawer = lazy(() => import('./StockDrawer'))
const AlertRuleDialog = lazy(() => import('@/components/alerts/AlertRuleDialog'))

/** True from the first time `wanted` is true: the overlay then stays mounted (closing, focus return). */
function useOnce(wanted: boolean): boolean {
  const [mounted, setMounted] = useState(wanted)
  useEffect(() => {
    if (wanted) setMounted(true)
  }, [wanted])
  return mounted || wanted
}

export function MarketOverlays() {
  const drawer = useOnce(useStockDrawer((state) => state.ticker !== null))
  const dialog = useOnce(useAlertDialog((state) => state.draft !== null))
  // One boundary each: loading the dialog's code must not hide an open drawer (and lose its focus).
  return (
    <>
      <Suspense fallback={null}>{drawer && <StockDrawer />}</Suspense>
      <Suspense fallback={null}>{dialog && <AlertRuleDialog />}</Suspense>
    </>
  )
}
