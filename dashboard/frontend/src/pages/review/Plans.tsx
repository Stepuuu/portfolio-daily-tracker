// 计划与复盘 › 计划 (/plans): the plan written before buying (why, when it is wrong, entry range, stop,
// target, size), whether the market is near its stop or target, and whether trades followed it.
// Data: GET /api/plans (contract in backend/api/plans.py). The list carries no market block, so the
// details of the open plans (drafts and active ones, a handful) are fetched for the instrument's name, the
// price, the distances and the position; ended plans show their own numbers only. A field the user left
// empty reads 未填写. Prices keep the backend's digits and name their source and time; a quote that is not
// verified, or a detail whose latest refresh failed, is amber, so an old price never looks current.
// `?plan=<id>` opens that plan's drawer; `?ticker=EXCH:CODE` (and `&account=`) shows that listing's plans.
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ClipboardList, Plus, X } from 'lucide-react'
import {
  Badge, LongValue, Button, DataTable, EmptyState, InlineAlert, Panel, SegmentedControl, Skeleton, cx, toast, type Column,
} from '@/components/ui'
import { fmtDate, fmtTime, longLine, longLineOrNull, type Shown } from '@/lib/format'
import { getApiErrorMessage } from '@/services/api'
import { activatePlan } from '@/services/plans'
import { useShell } from '@/components/shell/shellStore'
import { useAppMode } from '@/components/shell/useAppMode'
import { PlanHost } from '@/components/plans/PlanHost'
import { PlanName } from '@/components/plans/PlanName'
import { useOpenDetails, usePlanList, useRefreshPlans, useTodayLookup } from '@/features/plans/hooks'
import { usePlanUi } from '@/features/plans/store'
import type { PlanDetail, PlanItem } from '@/features/plans/types'
import {
  FILTERS, UNFILLED, buyDraft, defaultFilter, entryRange, isEnded, marketState, matchesFilter, maxLossView, planName, plansOfListing,
  planSummary, planPrice, plannedSize, priceView, quantityLine, statusChip, stopDistance, targetDistance, type DistanceText,
  type PlanFilter, type PriceView,
} from '@/features/plans/view'
import { DISPLAY_ZONE } from '@/features/today/session'

const Unavailable = ({ title }: { title?: string }) => <span title={title} className="text-fg-3">不可用</span>
const Unfilled = ({ title }: { title?: string }) => <span title={title} className="text-fg-3">{UNFILLED}</span>
/** A plan figure through lib/format.ts `fmtLong` (every digit up to 8 places), or 未填写. */
const shownOr = (value: Shown | null) => (value ? <LongValue value={value} /> : <Unfilled />)
/** Market cells of an ended plan: it is no longer compared with the market. */
const NotCompared = () => <span title="计划已结束，不再对照行情" className="text-fg-3">—</span>

function LossCell({ plan }: { plan: PlanItem }) {
  const loss = maxLossView(plan)
  if (loss.muted) return <span title={loss.note ?? undefined} className="text-fg-3">{loss.text}</span>
  return (
    <span title={loss.note ?? undefined}>
      {loss.text}
      {loss.estimated && <span className="ml-1 text-caption text-warn">估算</span>}
    </span>
  )
}

/** A distance; amber with the price's warning when the price it is measured from is not verified or may be old. */
function DistanceCell({ value, price }: { value: DistanceText; price?: PriceView }) {
  if (value.reason || value.text === '不可用') return <Unavailable title={value.reason ?? undefined} />
  const doubtful = price?.tone === 'warn' && !value.text.startsWith('未设')
  return (
    <span title={doubtful ? `按${price?.label ?? '未核实'}的现价计算：${price?.title ?? ''}` : undefined}
      className={value.tone === 'warn' || doubtful ? 'text-warn' : value.text.startsWith('未设') ? 'text-fg-3' : undefined}>
      <LongValue value={value} />
    </span>
  )
}

/** The backend's price, its source and time on hover, and an amber 已过期 hh:mm / 可能不是最新 when it applies. */
function PriceCell({ view }: { view: PriceView }) {
  if (view.text === '不可用') return <Unavailable title={view.title} />
  return (
    <span title={view.title}>
      {view.text}
      {view.label && <span className={cx('ml-1 text-caption', view.tone === 'warn' ? 'text-warn' : 'text-fg-3')}>{view.label}</span>}
    </span>
  )
}

export default function Plans() {
  const list = usePlanList()
  const items = list.data?.items
  const { details, errors, outdated } = useOpenDetails(items)
  const lookup = useTodayLookup()
  const { readOnlyReason } = useAppMode()
  const openPlan = usePlanUi((state) => state.openPlan)
  const openForm = usePlanUi((state) => state.openForm)
  const openEntry = useShell((state) => state.openEntry)
  const refresh = useRefreshPlans()
  const [params, setParams] = useSearchParams()
  const [chosen, setChosen] = useState<PlanFilter | null>(null)
  const [activating, setActivating] = useState<number | null>(null)
  const listing = params.get('ticker')
  const listingAccount = params.get('account')
  // `?ticker=` narrows the list to that listing (any spelling) and `&account=` to that account's plans.
  const listed = useMemo(() => plansOfListing(items ?? [], listing, listingAccount), [items, listing, listingAccount])
  const filter = chosen ?? defaultFilter(listed)
  const reason = readOnlyReason
  const today = fmtDate(new Date(), { timeZone: DISPLAY_ZONE })

  const clearListing = () => {
    const next = new URLSearchParams(params)
    next.delete('ticker')
    next.delete('account')
    setParams(next, { replace: true })
  }

  useEffect(() => {
    const raw = params.get('plan') ?? ''
    if (/^[0-9]{1,15}$/.test(raw) && raw !== '0') openPlan(Number.parseInt(raw, 10))
    if (params.has('plan')) {
      const next = new URLSearchParams(params)
      next.delete('plan')
      setParams(next, { replace: true })
    }
  }, [params, openPlan, setParams])

  const rows = useMemo(() => listed.filter((plan) => matchesFilter(plan, filter)), [listed, filter])
  const filterLabel = FILTERS.find((item) => item.value === filter)?.label ?? ''
  const emptyTitle = !listing ? `没有${filterLabel}的计划`
    : filter === 'all' ? `${listing} 还没有交易计划` : `${listing} 没有${filterLabel}的计划`
  const marketOf = (plan: PlanItem): { detail: PlanDetail | undefined; pending: boolean; failed: string | null } => {
    const detail = details.get(plan.id)
    const state = marketState(plan, detail, errors.get(plan.id))
    return { detail, pending: state.kind === 'pending', failed: state.kind === 'failed' ? state.reason : null }
  }

  const nameOf = (plan: PlanItem) => planName(plan, lookup.names, details.get(plan.id)?.market)
  const priceOf = (plan: PlanItem, detail: PlanDetail | undefined) => priceView(detail?.market, plan.currency, outdated.get(plan.id) ?? null, today)

  const activate = async (plan: PlanItem) => {
    setActivating(plan.id)
    try {
      const result = await activatePlan(plan.id)
      await refresh()
      toast.success(result.alerts === null ? '计划已启用；提醒规则暂时无法读取' : `计划已启用，当前有 ${result.alerts.filter((rule) => rule.enabled).length} 条提醒`)
    } catch (exc) {
      toast.warn(getApiErrorMessage(exc))
    } finally {
      setActivating(null)
    }
  }

  const columns: Column<PlanItem>[] = [
    {
      key: 'ticker', header: '标的', label: '标的',
      render: (plan) => (
        <div className="flex min-w-0 flex-col py-1">
          <PlanName ticker={plan.ticker} name={nameOf(plan)} />
          {plan.account && <span className="truncate text-caption text-fg-3">{plan.account}</span>}
          {plan.conflicts && plan.conflicts.length > 0 && (
            <span className="text-caption text-warn" title="同一标的同一账户只能有一个启用中的计划；成交按最近启用的计划对照">
              {`与计划 ${plan.conflicts.map((other) => `#${other}`).join('、')} 重复启用`}
            </span>
          )}
        </div>
      ),
      sortValue: (plan) => plan.ticker,
    },
    {
      key: 'status', header: '状态',
      render: (plan) => { const chip = statusChip(plan); return <Badge tone={chip.tone}>{chip.label}</Badge> },
    },
    { key: 'entry', header: '入场区间', numeric: true, hideBelow: 'md', render: (plan) => shownOr(longLineOrNull((pick) => entryRange(plan, pick))) },
    { key: 'stop', header: '止损', numeric: true, render: (plan) => shownOr(longLineOrNull((pick) => planPrice(plan.stop_price, plan.currency, pick))) },
    { key: 'target', header: '目标', numeric: true, hideBelow: 'sm', render: (plan) => shownOr(longLineOrNull((pick) => planPrice(plan.target_price, plan.currency, pick))) },
    { key: 'size', header: '计划仓位', numeric: true, hideBelow: 'md', render: (plan) => shownOr(longLineOrNull((pick) => plannedSize(plan, pick))) },
    {
      key: 'price', header: '现价', numeric: true, hideBelow: 'md',
      render: (plan) => {
        if (isEnded(plan.status)) return <NotCompared />
        const { detail, pending, failed } = marketOf(plan)
        if (pending) return <Skeleton w={48} className="ml-auto" />
        if (failed) return <Unavailable title={failed} />
        return <PriceCell view={priceOf(plan, detail)} />
      },
    },
    {
      key: 'toStop', header: '距止损', numeric: true, hideBelow: 'md',
      render: (plan) => {
        if (isEnded(plan.status)) return <NotCompared />
        const { detail, pending, failed } = marketOf(plan)
        if (pending) return <Skeleton w={48} className="ml-auto" />
        return failed ? <Unavailable title={failed} /> : <DistanceCell value={stopDistance(plan, detail?.market)} price={priceOf(plan, detail)} />
      },
    },
    {
      key: 'toTarget', header: '距目标', numeric: true, hideBelow: 'lg',
      render: (plan) => {
        if (isEnded(plan.status)) return <NotCompared />
        const { detail, pending, failed } = marketOf(plan)
        if (pending) return <Skeleton w={48} className="ml-auto" />
        return failed ? <Unavailable title={failed} /> : <DistanceCell value={targetDistance(plan, detail?.market)} price={priceOf(plan, detail)} />
      },
    },
    {
      key: 'held', header: '持仓 / 计划', numeric: true, hideBelow: 'lg',
      render: (plan) => {
        if (isEnded(plan.status)) return <NotCompared />
        const { detail, pending, failed } = marketOf(plan)
        if (pending) return <Skeleton w={48} className="ml-auto" />
        if (failed) return <Unavailable title={failed} />
        const line = quantityLine(detail?.market, plan)
        return line.text === '不可用' ? <Unavailable title={line.reason ?? undefined} /> : <span title={line.reason ?? undefined}>{line.text}</span>
      },
    },
    { key: 'loss', header: '最大亏损', numeric: true, hideBelow: 'lg', render: (plan) => <LossCell plan={plan} /> },
    {
      key: 'review', header: '复查日', numeric: true, hideBelow: 'md',
      render: (plan) => {
        if (!plan.review_on) return <Unfilled />
        const due = plan.status === 'active' && plan.review_on <= today
        return <span className={due ? 'text-warn' : undefined} title={due ? '已到复查日' : undefined}>{plan.review_on}</span>
      },
      sortValue: (plan) => plan.review_on,
    },
  ]

  const card = (plan: PlanItem) => {
    const chip = statusChip(plan)
    const { detail, pending, failed } = marketOf(plan)
    const open = !isEnded(plan.status)
    const unavailable: DistanceText = { text: '不可用', reason: failed, tone: 'neutral' }
    const market = detail?.market
    const stop = failed ? unavailable : stopDistance(plan, market)
    const target = failed ? unavailable : targetDistance(plan, market)
    const held = quantityLine(market, plan)
    const loss = maxLossView(plan)
    const price = priceOf(plan, detail)
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between gap-3">
          <PlanName ticker={plan.ticker} name={nameOf(plan)} />
          <Badge tone={chip.tone}>{chip.label}</Badge>
        </div>
        <p className="text-table text-fg-2"><LongValue value={longLine((pick) => planSummary(plan, pick))} /></p>
        <p className="text-table text-fg-3">
          最大亏损 <span className={loss.muted ? undefined : 'text-fg-2'} title={loss.note ?? undefined}>{loss.text}</span>
          {loss.estimated && <span className="ml-1 text-warn">估算</span>}
        </p>
        {open && (
          <div aria-label="当前行情" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-table text-fg-3">
            {pending ? <Skeleton w={160} /> : (
              <>
                <span>现价 {failed ? <Unavailable title={failed} /> : <span className="text-fg-2"><PriceCell view={price} /></span>}</span>
                <span className="inline-flex items-center gap-1">距止损 <DistanceCell value={stop} price={price} /></span>
                <span className="inline-flex items-center gap-1">距目标 <DistanceCell value={target} price={price} /></span>
                {!failed && held.text !== '不可用' && <span>{held.text}</span>}
              </>
            )}
          </div>
        )}
        {plan.conflicts && plan.conflicts.length > 0 && (
          <p className="text-table text-warn">{`与计划 ${plan.conflicts.map((other) => `#${other}`).join('、')} 重复启用`}</p>
        )}
      </div>
    )
  }

  const total = items?.length ?? 0
  const noPlans = items !== undefined && total === 0

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 text-body text-fg">
      <h1 className="sr-only">计划</h1>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl<PlanFilter> ariaLabel="计划筛选" options={FILTERS} value={filter} onChange={setChosen} />
          {listing && (
            <Button size="sm" variant="secondary" icon={X} onClick={clearListing} title="显示所有标的的计划">
              {`只看 ${listing}${listingAccount ? `（${listingAccount}）` : ''}`}
            </Button>
          )}
        </div>
        <Button variant="primary" icon={Plus} disabledReason={reason} tipAlign="end" onClick={() => openForm({ mode: 'new' })}>新建计划</Button>
      </div>

      {list.isError && !items && (
        <InlineAlert tone="warn" title="无法读取交易计划" action={<Button size="sm" variant="secondary" onClick={() => void list.refetch()}>重试</Button>}>
          {getApiErrorMessage(list.error)}
        </InlineAlert>
      )}
      {list.isError && items && (
        <InlineAlert tone="warn" title="计划列表可能不是最新" action={<Button size="sm" variant="secondary" onClick={() => void list.refetch()}>重试</Button>}>
          {`显示的是 ${fmtTime(list.dataUpdatedAt, { timeZone: DISPLAY_ZONE, seconds: false })} 读到的列表，之后刷新失败：${getApiErrorMessage(list.error)}`}
        </InlineAlert>
      )}

      {noPlans ? (
        <Panel>
          <EmptyState
            icon={ClipboardList}
            title="还没有交易计划"
            description="买入前写下理由、什么情况说明判断错了，以及入场区间、止损、目标和仓位；启用后会自动设提醒，并在账本里对照你的成交。"
            action={<Button variant="primary" icon={Plus} disabledReason={reason} onClick={() => openForm({ mode: 'new' })}>新建计划</Button>}
          />
        </Panel>
      ) : !list.isError || items ? (
        <Panel padding="none" title="交易计划" description="计划只用于记录和提醒，不会下单。">
          <DataTable
            caption="交易计划"
            columns={columns}
            rows={rows}
            rowKey={(plan) => String(plan.id)}
            loading={list.isLoading}
            mobile="cards"
            card={card}
            onRowClick={(plan) => openPlan(plan.id)}
            rowClassName={(plan) => cx(plan.status === 'closed' || plan.status === 'cancelled' ? 'text-fg-2' : undefined)}
            rowActions={(plan) => (
              <>
                {plan.status === 'draft' && (
                  <Button size="sm" variant="ghost" loading={activating === plan.id} disabledReason={reason} onClick={() => void activate(plan)}>启用</Button>
                )}
                {plan.status === 'active' && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabledReason={reason}
                    onClick={() => openEntry(buyDraft(plan, nameOf(plan)))}
                  >
                    买入
                  </Button>
                )}
              </>
            )}
            empty={<EmptyState title={emptyTitle} description={listing ? '换一个分类，或显示所有标的的计划。' : '换一个分类，或新建计划。'} />}
            footer={items ? `${listing ? `${listing} 有 ${listed.length} 个计划（共 ${total} 个）` : `共 ${total} 个计划`}；距止损、距目标是价格要走多远才到，不是盈亏。` : undefined}
          />
        </Panel>
      ) : null}
      <PlanHost />
    </div>
  )
}
