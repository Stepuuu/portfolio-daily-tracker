// A plan in detail, in a drawer: why and when it is wrong, the numbers (with the backend's loss
// sentence as it is), the market block, the alert rules and the linked trades, and the actions that fit
// the status. Opened with `openPlanDrawer(id)` (features/plans/store). Rules or trades that cannot be read
// are 不可用 with the reason, never an empty list; long words in the person's texts wrap at 390 px.
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { Badge, Button, Drawer, InlineAlert, LongValue, Skeleton, buttonClass, cx, numeric, toast } from '@/components/ui'
import { fmtDate, fmtDateTime, fmtPct, fmtQty, longLine, longLineOrNull, toneOf, type Shown } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { useAppMode } from '@/components/shell/useAppMode'
import { useShell } from '@/components/shell/shellStore'
import { usePlanDetail, useRefreshPlans, useTodayLookup } from '@/features/plans/hooks'
import { usePlanUi } from '@/features/plans/store'
import {
  UNFILLED, alertRuleText, buyDraft, deviationTone, entryRange, isEnded, marketQuoteLine, maxLossView, planName, planPrice, plannedSize,
  quantityLine, relationLabel, statusChip, stopDistance, targetDistance, tradeText, weightLine, type DistanceText,
} from '@/features/plans/view'
import type { PlanDetail, PlanLink } from '@/features/plans/types'
import { planLink } from '@/features/backtest/model'
import { getApiErrorMessage } from '@/services/api'
import { activatePlan } from '@/services/plans'
import { EndPlanDialog } from './EndPlanDialog'
import { PlanName } from './PlanName'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-table font-medium text-fg-2">{title}</h3>
      {children}
    </section>
  )
}

function Facts({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-body">
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="text-caption text-fg-3">{item.label}</dt>
          <dd className={cx('break-words', numeric)}>{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

const blank = <span className="text-fg-3">{UNFILLED}</span>

/** A plan figure or line through lib/format.ts `fmtLong` (past 8 places rounded, the full value on hover), or 未填写. */
const shownOr = (value: Shown | null) => (value ? <LongValue value={value} /> : blank)

/** Long unbroken text (a pasted URL, a code) wraps inside the drawer instead of widening it. */
const WRAP = 'min-w-0 [overflow-wrap:anywhere]'

/** The loss at the stop: the CNY figure, 估算 (amber) on an unverified rate, and the rate it was converted at. */
function MaxLoss({ plan }: { plan: PlanDetail }) {
  const loss = maxLossView(plan)
  return (
    <span>
      <span className={loss.muted ? 'text-fg-3' : undefined}>{loss.text}</span>
      {loss.estimated && <Badge tone="warn" title={loss.note ?? undefined} className="ml-2 align-middle">估算</Badge>}
      {/* The rate a converted figure rests on; a missing figure's reason is in the sentence below. */}
      {loss.note && plan.max_loss.amount_cny !== null && <span className="mt-0.5 block text-caption text-fg-3">{loss.note}</span>}
    </span>
  )
}

function Distance({ value, level }: { value: DistanceText; level: string | null }) {
  return (
    <span>
      <LongValue value={value} className={value.tone === 'warn' ? 'text-warn' : value.reason ? 'text-fg-3' : undefined} />
      {level && <span className="ml-2 text-caption text-fg-3">{level}</span>}
      {value.reason && <span className="mt-0.5 block text-caption text-fg-3">{value.reason}</span>}
    </span>
  )
}

function MarketSection({ plan }: { plan: PlanDetail }) {
  const { market } = plan
  const stop = stopDistance(plan, market)
  const target = targetDistance(plan, market)
  const quantity = quantityLine(market, plan)
  const weight = weightLine(market, plan)
  // As Today's row: amber when the backend counts the price as an estimate (a New York close before it settles).
  const line = marketQuoteLine(market, null)
  return (
    <Section title="当前行情">
      <Facts
        items={[
          {
            label: '现价',
            value: market.price === null ? (
              <span><span className="text-fg-3">不可用</span><span className="mt-0.5 block text-caption text-fg-3">{market.price_reason ?? '行情不可用'}</span></span>
            ) : (
              <span>
                {shownOr(longLineOrNull((pick) => planPrice(market.price, plan.currency, pick)))}
                {market.change_pct !== null
                  ? <span className={cx('ml-2 text-table', pnlTextClass(toneOf(market.change_pct)))}>{fmtPct(market.change_pct, { signed: true, exact: true })}</span>
                  : <span className="ml-2 text-table text-fg-3" title="缺少昨收，涨跌幅无法计算">涨跌幅 不可用</span>}
                {line && (line.meta || line.chip || line.estimate) && (
                  <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-caption text-fg-3">
                    {line.meta}
                    {line.chip && <Badge tone={line.chip.tone} title={line.chip.title}>{line.chip.label}</Badge>}
                    {line.estimate && <Badge tone={line.estimate.tone} title={line.estimate.title}>{line.estimate.label}</Badge>}
                  </span>
                )}
              </span>
            ),
          },
          { label: '距止损', value: <Distance value={stop} level={plan.stop_price === null ? null : `止损 ${fmtQty(plan.stop_price)}`} /> },
          { label: '持仓 / 计划', value: <span>{quantity.text}{quantity.reason && <span className="mt-0.5 block text-caption text-fg-3">{quantity.reason}</span>}</span> },
          { label: '距目标', value: <Distance value={target} level={plan.target_price === null ? null : `目标 ${fmtQty(plan.target_price)}`} /> },
          {
            label: '仓位',
            value: (
              <span>
                {weight.text}
                {weight.estimated && <Badge tone="warn" title={weight.reason ?? undefined} className="ml-2 align-middle">估算</Badge>}
                {weight.reason && <span className="mt-0.5 block text-caption text-fg-3">{weight.reason}</span>}
              </span>
            ),
          },
        ]}
      />
      {market.over_planned_weight && <p className="text-table text-warn">当前仓位已超过计划仓位。</p>}
    </Section>
  )
}

function Deviations({ link }: { link: PlanLink }) {
  if (!link.deviations.length) return <p className="text-caption text-fg-3">与计划一致</p>
  return (
    <ul className="flex flex-col gap-1">
      {link.deviations.map((deviation, index) => (
        <li key={`${deviation.code}:${index}`} className={cx('flex items-start gap-1 text-table', deviationTone(deviation) === 'warn' ? 'text-warn' : 'text-fg-2')}>
          {deviationTone(deviation) === 'warn' && <AlertTriangle size={14} className="mt-[3px] shrink-0" aria-hidden />}
          <span className={WRAP}>{deviation.message}</span>
        </li>
      ))}
    </ul>
  )
}

export function PlanDrawer() {
  const id = usePlanUi((state) => state.viewId)
  const close = usePlanUi((state) => state.closePlan)
  const openForm = usePlanUi((state) => state.openForm)
  const openEntry = useShell((state) => state.openEntry)
  const { readOnlyReason } = useAppMode()
  const lookup = useTodayLookup()
  const query = usePlanDetail(id)
  const refresh = useRefreshPlans()
  const [ending, setEnding] = useState<'close' | 'cancel' | null>(null)
  const [activating, setActivating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const plan = query.data && query.data.id === id ? query.data : undefined
  const reason = readOnlyReason

  const activate = async () => {
    if (!plan) return
    setActivating(true)
    setError(null)
    try {
      const result = await activatePlan(plan.id)
      await refresh()
      toast.success(result.alerts === null ? '计划已启用；提醒规则暂时无法读取' : `计划已启用，当前有 ${result.alerts.filter((rule) => rule.enabled).length} 条提醒`)
    } catch (exc) {
      setError(getApiErrorMessage(exc))
    } finally {
      setActivating(false)
    }
  }

  const name = plan ? planName(plan, lookup.names, plan.market) : undefined
  const buy = () => {
    if (!plan) return
    close()
    // The plan's id goes along: the entry says which plan it follows and its preview is checked against it.
    openEntry(buyDraft(plan, name))
  }

  const chip = plan ? statusChip(plan) : null
  const ended = plan ? isEnded(plan.status) : false

  // The plan's stop and target on its own day, with the 止损止盈 preset (the page shows the conversion to %).
  const backtest = plan ? (
    <Link to={planLink({ ...plan, entry_day: fmtDate(plan.activated_at ?? plan.created_at, { timeZone: 'Asia/Shanghai' }) })} onClick={close} className={buttonClass({ variant: 'ghost' })}>回测这个计划</Link>
  ) : null
  const footer = plan && !ended ? (
    <>
      {backtest}
      {plan.status === 'draft' && <Button variant="ghost" disabledReason={reason} onClick={() => setEnding('cancel')}>取消计划</Button>}
      {plan.status === 'active' && (
        <>
          <Button variant="ghost" disabledReason={reason} onClick={() => setEnding('cancel')}>取消计划</Button>
          <Button disabledReason={reason} onClick={() => setEnding('close')}>结束</Button>
        </>
      )}
      <Button disabledReason={reason} onClick={() => openForm({ mode: 'edit', id: plan.id })}>编辑</Button>
      {plan.status === 'draft' && <Button variant="primary" loading={activating} disabledReason={reason} onClick={() => void activate()}>启用</Button>}
      {plan.status === 'active' && <Button variant="primary" disabledReason={reason} onClick={buy}>按计划买入</Button>}
    </>
  ) : backtest ?? undefined

  return (
    <>
      <Drawer
        open={id !== null}
        onClose={close}
        title={plan ? <PlanName ticker={plan.ticker} name={name} className="inline-block max-w-full align-bottom" /> : '交易计划'}
        description={plan && chip ? (
          <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
            <Badge tone={chip.tone}>{chip.label}</Badge>
            <span>{plan.account ? `账户 ${plan.account}` : '全部账户'}</span>
            <span aria-hidden>·</span>
            <span>创建于 {fmtDate(plan.created_at, { timeZone: 'Asia/Shanghai' })}</span>
          </span>
        ) : undefined}
        footer={footer}
      >
        {!plan && query.isError ? (
          <InlineAlert tone="warn" title="无法读取这个计划" action={<Button size="sm" variant="secondary" onClick={() => void query.refetch()}>重试</Button>}>
            {getApiErrorMessage(query.error)}
          </InlineAlert>
        ) : !plan ? (
          <div role="status" aria-label="正在加载计划"><Skeleton lines={5} h={14} /></div>
        ) : (
          <div className="flex flex-col gap-4 text-body">
            {error && <InlineAlert tone="warn" title="操作没有完成">{error}</InlineAlert>}
            {plan.conflicts && plan.conflicts.length > 0 && (
              <InlineAlert tone="warn" title="同一标的有多个启用中的计划">
                {`计划 ${plan.conflicts.map((other) => `#${other}`).join('、')} 也在启用中，覆盖同一只股票和账户。成交按最近启用的计划对照；请关闭或取消多余的计划。`}
              </InlineAlert>
            )}
            {ended && (
              <InlineAlert tone="info" title={`${plan.status_label}${plan.ended_at ? ` · ${fmtDateTime(plan.ended_at, { timeZone: 'Asia/Shanghai' })}` : ''}`}>
                <span className={WRAP}>{plan.close_reason ?? '没有写原因。'}</span>
              </InlineAlert>
            )}
            <Section title="为什么买">
              <p className={cx('whitespace-pre-wrap', WRAP)}>{plan.thesis}</p>
            </Section>
            <Section title="什么情况说明我错了">
              <p className={cx('whitespace-pre-wrap', WRAP)}>{plan.invalidation}</p>
            </Section>
            <Section title="计划数字">
              <Facts
                items={[
                  { label: '入场区间', value: shownOr(longLineOrNull((pick) => entryRange(plan, pick))) },
                  { label: '计划仓位', value: shownOr(longLineOrNull((pick) => plannedSize(plan, pick))) },
                  { label: '止损', value: shownOr(longLineOrNull((pick) => planPrice(plan.stop_price, plan.currency, pick))) },
                  { label: '目标', value: shownOr(longLineOrNull((pick) => planPrice(plan.target_price, plan.currency, pick))) },
                  { label: '复查日', value: plan.review_on ?? blank },
                  { label: '最大亏损', value: <MaxLoss plan={plan} /> },
                ]}
              />
              <p className={cx('text-table text-fg-2', WRAP)}>{plan.max_loss.formula ?? plan.max_loss.reason}</p>
              {plan.max_loss.formula && plan.max_loss.reason && <p className="text-table text-warn">{plan.max_loss.reason}</p>}
            </Section>
            {!ended && query.isError && (
              <InlineAlert tone="warn" title="行情可能不是最新">
                {`显示的是 ${fmtDateTime(query.dataUpdatedAt, { timeZone: 'Asia/Shanghai' })} 读到的数据，之后刷新失败：${getApiErrorMessage(query.error)}`}
              </InlineAlert>
            )}
            {!ended && <MarketSection plan={plan} />}
            <Section title="提醒规则">
              {plan.alerts === null ? (
                <p className="text-table"><span className="text-fg-3">不可用</span><span className="ml-2 text-warn">{plan.alerts_error ?? '提醒规则暂时无法读取。'}</span></p>
              ) : plan.alerts.length > 0 ? (
                <ul className="flex flex-col gap-1 text-table">
                  {plan.alerts.map((rule) => (
                    <li key={rule.id} className={rule.enabled ? 'text-fg' : 'text-fg-3'}><LongValue value={longLine((pick) => alertRuleText(rule, pick))} /></li>
                  ))}
                </ul>
              ) : (
                <p className="text-table text-fg-3">{plan.status === 'draft' ? '启用后会按止损、目标和计划仓位自动设置提醒。' : '没有提醒规则。'}</p>
              )}
              {plan.alert_notes.map((note) => <InlineAlert key={note} tone="info"><span className={WRAP}>{note}</span></InlineAlert>)}
            </Section>
            <Section title="关联成交">
              {plan.links === null ? (
                <p className="text-table"><span className="text-fg-3">不可用</span><span className="ml-2 text-warn">{plan.links_error ?? '关联成交暂时无法读取。'}</span></p>
              ) : plan.links.length === 0 ? (
                <p className="text-table text-fg-3">还没有对应的成交。在账本记录买入后，这里会显示它是否按计划执行。</p>
              ) : (
                <ul className="flex flex-col gap-3">
                  {plan.links.map((link) => (
                    <li key={link.event_id} className={cx('flex flex-col gap-1', link.reversed && 'text-fg-3')}>
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className={cx('text-table', numeric)}>{link.event_date}</span>
                        <LongValue value={longLine((pick) => tradeText(link, pick))} className={numeric} />
                        <Badge>{relationLabel(link.relation)}</Badge>
                        {link.reversed && <Badge tone="warn">已冲销</Badge>}
                      </div>
                      <Deviations link={link} />
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        )}
      </Drawer>
      {ending && plan && (
        <EndPlanDialog
          open
          mode={ending}
          planId={plan.id}
          onClose={() => setEnding(null)}
          onDone={() => setEnding(null)}
        />
      )}
    </>
  )
}
