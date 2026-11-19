// The chip on a holding's row in Today: "计划 · 距止损 -11.76%". It opens the plan's drawer. The active
// plans and their market blocks are one request for the whole page (shared query, `?market=1`), so no
// request is made per row. The row's plan is the active plan of its listing in any spelling (HKG:700 /
// HKG:00700, BRK.B / BRK-B) and of its account (a plan for every account covers each). Reached and the
// distance are the backend's flags and digits; amber when reached, near or the quote is not verified. When
// the plans cannot be read the chip says 计划 不可用 (amber) and retries on click, instead of disappearing.
import { cx, focusRing, numeric } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { useActivePlans } from '@/features/plans/hooks'
import { usePlanUi } from '@/features/plans/store'
import { planChipView } from '@/features/plans/view'

export function PlanChip({ ticker, account }: { ticker: string; account?: string | null }) {
  const plans = useActivePlans()
  const openPlan = usePlanUi((state) => state.openPlan)
  const failed = plans.isError ? getApiErrorMessage(plans.error) : null
  const chip = planChipView(plans.data?.items, failed, ticker, account ?? null)
  if (!chip) return null
  const planId = chip.planId
  return (
    <button
      type="button"
      onClick={() => (planId === null ? void plans.refetch() : openPlan(planId))}
      title={chip.title}
      className={cx(
        'inline-flex h-5 max-w-full items-center whitespace-nowrap rounded-full px-2 text-caption font-medium transition-colors duration-[120ms]',
        numeric, focusRing,
        chip.warn ? 'bg-warn/10 text-warn hover:bg-warn/15' : 'bg-accent/10 text-accent hover:bg-accent/15',
      )}
    >
      {chip.text}
    </button>
  )
}
