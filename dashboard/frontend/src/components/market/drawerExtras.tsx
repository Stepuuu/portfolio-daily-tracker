// Sections other packages add to the stock drawer, under the position. Push one renderer; it returns null
// for a ticker it has nothing to say about. Renderers are components in all but name: they may use hooks
// only through a component they return. Registered here: the trade plans package's 交易计划 section (the
// listing's open plans, each linking to its drawer on the plans page; 没有进行中的交易计划 or 不可用 when
// there is none or the plans cannot be read).
//
//   DRAWER_EXTRAS.push(({ ticker }) => <PlanSummary ticker={ticker} />)
import { Fragment, type ReactNode } from 'react'
import { PlanDrawerSection } from '@/components/plans/PlanDrawerSection'
import { useAppMode } from '@/components/shell/useAppMode'

export interface DrawerContext {
  ticker: string
  name: string | null
  /** Writes are refused (demo, or the app mode is not known): disable write buttons with useAppMode().readOnlyReason. */
  readOnly: boolean
  /** Close the drawer (before navigating away, say). */
  close: () => void
}

export type DrawerExtraRenderer = (context: DrawerContext) => ReactNode

export const DRAWER_EXTRAS: DrawerExtraRenderer[] = []

/** One titled section of the drawer, as the drawer's own (持仓, 提醒). */
export function DrawerSection({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-col gap-2 border-t border-line pt-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-table font-medium text-fg-2">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

/**
 * The plans of this listing, in every account (the drawer is opened for an instrument, not an account).
 * The research mode has no plans: nothing is asked for there.
 */
function Plans({ ticker, close }: { ticker: string; close: () => void }) {
  const { mode, lab } = useAppMode()
  if (!mode || lab) return null
  return (
    <div className="border-t border-line pt-4">
      <PlanDrawerSection ticker={ticker} onNavigate={close} />
    </div>
  )
}

DRAWER_EXTRAS.push(({ ticker, close }) => (ticker.toUpperCase().startsWith('IDX:') ? null : <Plans ticker={ticker} close={close} />))

export function renderDrawerExtras(context: DrawerContext): ReactNode {
  const parts = DRAWER_EXTRAS.map((render, index) => [index, render(context)] as const)
    .filter(([, node]) => node !== null && node !== undefined && node !== false)
  if (!parts.length) return null
  return parts.map(([index, node]) => <Fragment key={index}>{node}</Fragment>)
}
