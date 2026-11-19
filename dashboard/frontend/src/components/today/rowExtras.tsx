// Chips after a holding's quote line on Today: `[计划 15%] [止损 1,520]`. Each later package (trade
// plans, alert rules) adds one renderer to `ROW_EXTRAS`; a renderer returns null for a ticker it has
// nothing to say about. Renderers run on every poll, so they read cached queries rather than fetch.
import { Fragment, type ReactNode } from 'react'
import { PlanChip } from '@/components/plans/PlanChip'
import { renderAlertChip } from '@/components/alerts/AlertChip'

export type RowExtraRenderer = (ticker: string, account?: string) => ReactNode

export const ROW_EXTRAS: RowExtraRenderer[] = []

// Trade plans: an active plan's distance to its stop, opening the plan's drawer.
ROW_EXTRAS.push((ticker, account) => <PlanChip ticker={ticker} account={account} />)
ROW_EXTRAS.push(renderAlertChip)   // 提醒 n (alerts package)

/** All contributions for one ticker, or null (the slot then renders nothing). */
export function renderRowExtra(ticker: string, account?: string): ReactNode {
  const parts = ROW_EXTRAS.map((render, index) => [index, render(ticker, account)] as const).filter(([, node]) => node !== null && node !== undefined && node !== false)
  if (!parts.length) return null
  return parts.map(([index, node]) => <Fragment key={index}>{node}</Fragment>)
}
