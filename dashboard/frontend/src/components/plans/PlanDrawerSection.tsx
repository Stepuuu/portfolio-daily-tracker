// "交易计划" in the stock drawer: the open plans (draft or active) of one listing (any spelling), and of one
// account when given (a plan for every account covers it). Register it from the market package:
//
//   DRAWER_EXTRAS.push(({ ticker, close }) => <PlanDrawerSection ticker={ticker} onNavigate={close} />)
//
// It reads the plans list the plans page uses (one shared query); each plan links to its drawer on the plans
// page (`/plans?plan=<id>`), and 全部计划 to `/plans?ticker=EXCH:CODE`. A list that cannot be read is 不可用,
// never "no plan".
import { Link } from 'react-router-dom'
import { Badge, LongValue, Skeleton, cx, focusRing, numeric } from '@/components/ui'
import { longLine } from '@/lib/format'
import { getApiErrorMessage } from '@/services/api'
import { usePlanList } from '@/features/plans/hooks'
import { isEnded, planSummary, plansOfListing, statusChip } from '@/features/plans/view'

const LINK = cx('shrink-0 rounded-sm text-table text-accent underline-offset-4 hover:underline', focusRing)

export function PlanDrawerSection({ ticker, account, onNavigate }: { ticker: string; account?: string | null; onNavigate?: () => void }) {
  const list = usePlanList()
  const items = list.data ? plansOfListing(list.data.items, ticker, account).filter((plan) => !isEnded(plan.status)) : undefined
  const all = `/plans?ticker=${encodeURIComponent(ticker)}${account ? `&account=${encodeURIComponent(account)}` : ''}`
  return (
    <section aria-label="交易计划" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-table font-medium text-fg-2">交易计划</h3>
        <Link to={all} onClick={onNavigate} className={LINK}>全部计划</Link>
      </div>
      {items === undefined ? (
        list.isError ? (
          <p className="text-table"><span className="text-fg-3">不可用</span><span className="ml-2 text-warn">{`交易计划暂时无法读取：${getApiErrorMessage(list.error)}`}</span></p>
        ) : (
          <div role="status" aria-label="正在读取交易计划"><Skeleton lines={2} h={12} /></div>
        )
      ) : items.length === 0 ? (
        <p className="text-table text-fg-3">没有进行中的交易计划。</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((plan) => {
            const chip = statusChip(plan)
            return (
              <li key={plan.id} className="flex items-start justify-between gap-3">
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone={chip.tone}>{chip.label}</Badge>
                    <span className="text-caption text-fg-3">{plan.account ? `账户 ${plan.account}` : '全部账户'}</span>
                  </span>
                  <span className={cx('mt-0.5 block text-table text-fg-2 [overflow-wrap:anywhere]', numeric)}><LongValue value={longLine((pick) => planSummary(plan, pick))} /></span>
                </span>
                <Link to={`/plans?plan=${plan.id}`} onClick={onNavigate} className={LINK}>打开</Link>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
