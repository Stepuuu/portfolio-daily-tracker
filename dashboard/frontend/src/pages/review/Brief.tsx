// 计划与复盘 › 今日简报 (/review): the pre-market and post-market brief of a trading day, as the backend
// generated them. Every row is a fact computed by code from the ledger and labelled quotes, with its
// source and time; unknown values say 不可用 and why; estimated and intraday values carry their own amber
// marks. Model commentary, when switched on, is a separate, labelled block. Data: GET /api/briefs (contract
// in backend/api/briefs.py and core/routines/briefs.py); 生成 / 重新生成 is the scheduler's manual run
// (POST /api/routines/{kind}/run), offered only when the backend says it would be accepted. `?date=` picks
// the day and stays in the URL once chosen; while another day loads, the shown day stays (and the date
// controls keep keyboard focus).
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, RefreshCw } from 'lucide-react'
import {
  Badge, Button, EmptyState, IconButton, InlineAlert, LongValue, Panel, Skeleton, TipBubble, cx, focusRing, numeric, toast,
} from '@/components/ui'
import { pnlTextClass } from '@/lib/pnlColor'
import { navPages } from '@/routes'
import { getApiErrorMessage } from '@/services/api'
import { BRIEFS_KEY, briefsKey, fetchBriefs, runBrief } from '@/services/briefs'
import { useAppMode } from '@/components/shell/useAppMode'
import type { Chip } from '@/features/today/quoteStatus'
import type { BriefDay, BriefItem, BriefKind, BriefSection, Commentary, Fact } from '@/features/briefs/types'
import {
  KINDS, calendarMark, columnState, dateLabel, demoJump, factChips, factDetail, factMeta, factValue, isWarning, itemText,
  pollInterval, qualityNote, refTarget, timeLabel, type Target,
} from '@/features/briefs/view'

const linkClass = cx('rounded-sm text-accent underline-offset-4 hover:underline', focusRing)
/** A fact's label that leads somewhere: quiet (dotted underline) so a list of links does not shout. */
const labelLinkClass = cx(
  'rounded-sm text-fg underline decoration-line decoration-dotted underline-offset-4 hover:text-accent hover:decoration-accent',
  focusRing,
)

/** A chip whose reason shows on hover and on keyboard focus. */
function ReasonChip({ chip }: { chip: Chip }) {
  const id = useId()
  if (!chip.title) return <Badge tone={chip.tone}>{chip.label}</Badge>
  return (
    <span className="group/tip relative inline-flex">
      <span tabIndex={0} aria-describedby={id} className={cx('inline-flex rounded-full', focusRing)}>
        <Badge tone={chip.tone}>{chip.label}</Badge>
      </span>
      <TipBubble id={id} align="end">{chip.title}</TipBubble>
    </span>
  )
}

function Label({ text, target }: { text: string; target: Target | null }) {
  if (!target) return <span className="text-fg">{text}</span>
  return <Link to={target.href} title={target.label} className={labelLinkClass}>{text}</Link>
}

function FactRow({ fact, day, generatedAt, routes }: { fact: Fact; day: string; generatedAt: string; routes: ReadonlySet<string> }) {
  const value = factValue(fact)
  const detail = factDetail(fact)
  const chips = factChips(fact, day)
  const meta = factMeta(fact, generatedAt, day)
  const warning = isWarning(fact)
  return (
    <li className="py-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex min-w-0 items-baseline gap-1.5">
          {warning && <AlertTriangle size={14} aria-label="需要注意" className="shrink-0 translate-y-0.5 self-start text-warn" />}
          <Label text={fact.label} target={refTarget(fact.ref, routes, fact.metric)} />
        </span>
        <span
          className={cx('shrink-0 text-right', numeric, value.missing ? 'text-fg-3' : value.tone ? pnlTextClass(value.tone) : 'text-fg',
            warning && !value.tone && 'text-warn')}
          title={value.missing ? detail ?? undefined : undefined}
        >
          <LongValue value={value} />
        </span>
      </div>
      {(detail || meta || chips.length > 0) && (
        <div className="mt-0.5 flex flex-wrap items-start justify-between gap-x-3 gap-y-0.5 text-caption">
          <span className={cx('min-w-0 flex-1 basis-48 break-words', value.missing ? 'text-fg-3' : 'text-fg-2')}>{detail}</span>
          {(meta || chips.length > 0) && (
            // A converted amount's line names its rate too: it gets a row of its own instead of squeezing the detail.
            <span className={cx('flex min-w-0 max-w-full flex-wrap items-center justify-end gap-1.5 text-fg-3', numeric,
              fact.fx?.rate && 'basis-full')}>
              {chips.map((chip) => <ReasonChip key={chip.label} chip={chip} />)}
              {meta && <span className="break-words">{meta}</span>}
            </span>
          )}
        </div>
      )}
      {fact.items && fact.items.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1 border-l border-line pl-3">
          {fact.items.map((item, index) => {
            const shown = itemText(item)
            const target = refTarget(item.ref, routes, fact.metric)
            const flagged = item.level === 'warning'
            return (
              <li key={index} className={cx('text-table', flagged ? 'text-fg' : 'text-fg-2')}>
                {flagged && <AlertTriangle size={12} aria-hidden className="mr-1 inline -translate-y-px text-warn" />}
                {shown.thirdParty && (
                  <span className="mr-1.5 text-caption text-fg-3" title="第三方网站的原文，仅供查阅">
                    {shown.when ? `${shown.when.slice(5, 16)} ` : ''}第三方
                  </span>
                )}
                <span className="break-words">{shown.text}</span>
                {target && target.href.startsWith('/plans') && (
                  <Link to={target.href} className={cx('ml-2 whitespace-nowrap text-caption', linkClass)}>{target.label}</Link>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
}

function SectionBlock({ section, brief, routes }: { section: BriefSection; brief: { trading_date: string; generated_at: string }; routes: ReadonlySet<string> }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="border-t border-line pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={id} className="text-table font-medium text-fg-2">{section.title}</h3>
        {section.status && (
          <ReasonChip chip={{ label: section.status.code === 'not_queried' ? '数据源不可用' : '不可用', tone: 'warn', title: section.status.reason }} />
        )}
      </div>
      {section.note && <p className="mt-0.5 text-caption text-fg-3">{section.note}</p>}
      <ul className="divide-y divide-line">
        {section.facts.map((fact, index) => (
          <FactRow key={`${fact.label}-${index}`} fact={fact} day={brief.trading_date} generatedAt={brief.generated_at} routes={routes} />
        ))}
      </ul>
    </section>
  )
}

function CommentaryBlock({ commentary }: { commentary: Commentary }) {
  const id = useId()
  return (
    <section aria-labelledby={id} className="border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={id} className="text-table font-medium text-fg-2">模型点评</h3>
        {commentary.model && <Badge>{commentary.model}</Badge>}
        <Badge tone="warn">不是事实</Badge>
      </div>
      <p className="mt-0.5 text-caption text-fg-3">由模型根据上方事实写成，仅供参考；数字以上方事实为准。</p>
      {commentary.status === 'ok' && commentary.text ? (
        <p className="mt-2 whitespace-pre-line text-body text-fg-2">{commentary.text}</p>
      ) : (
        <p className="mt-2 text-table text-fg-3">{commentary.error ?? '没有生成点评。'}</p>
      )}
      {commentary.grounding_note && <p className="mt-1 text-caption text-warn">{commentary.grounding_note}</p>}
    </section>
  )
}

function BriefColumn({ item, demo, readOnlyReason, routes, busy, onRun }: {
  item: BriefItem; demo: boolean; readOnlyReason: string | undefined; routes: ReadonlySet<string>; busy: boolean; onRun: (kind: BriefKind) => void
}) {
  const state = columnState(item, demo)
  const brief = item.brief
  const quality = brief ? qualityNote(brief) : null
  const working = state.tone === 'busy' || busy
  // Offered only when the backend would accept the run; otherwise its reason is said instead.
  const action = state.action && (
    <Button
      size="sm"
      variant={state.action === 'rerun' ? 'secondary' : 'primary'}
      icon={RefreshCw}
      loading={working}
      disabledReason={readOnlyReason}
      tipAlign="end"
      onClick={() => onRun(item.kind)}
    >
      {state.action === 'rerun' ? '重新生成' : '生成'}
    </Button>
  )
  // Without a brief, a quiet state (尚未生成, 非交易日 ...) is said once, in the empty state below.
  const quiet = !brief && state.tone === 'neutral'
  const description = !quiet && (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={cx(state.tone === 'warn' && 'text-warn', numeric)}>{state.status}</span>
      {quality && <ReasonChip chip={{ label: quality, tone: 'warn', title: '不可用的数字显示为「不可用」，估算的数字标有「估算」，盘中价（非收盘价）标有「盘中价」，悬停可看原因。' }} />}
    </span>
  )
  return (
    <Panel title={item.title} description={description || undefined} actions={action || undefined} className="min-w-0">
      <div className="flex flex-col gap-4">
        {state.actionBlocked && <p className="text-caption text-fg-3">不能手动生成：{state.actionBlocked}</p>}
        {state.reason && (state.tone === 'warn' ? (
          <InlineAlert tone="warn" title="生成失败">{state.reason}{brief ? '。下面是失败时已生成的内容。' : ''}</InlineAlert>
        ) : brief && <p className="text-table text-fg-3">{state.reason}</p>)}
        {!brief && state.tone === 'busy' && (
          <div role="status" aria-label="正在生成" className="flex flex-col gap-3 py-2"><Skeleton lines={4} /></div>
        )}
        {quiet && <EmptyState icon={FileText} title={state.status} description={state.reason ?? undefined} />}
        {brief && brief.notices.length > 0 && (
          <ul className="flex flex-col gap-1 text-table text-fg-2">
            {brief.notices.map((notice, index) => (
              <li key={index} className={cx(notice.status === 'failed' && 'text-warn')}>{notice.text}</li>
            ))}
          </ul>
        )}
        {brief && brief.sections.map((section) => (
          <SectionBlock key={section.id} section={section} brief={brief} routes={routes} />
        ))}
        {brief && brief.commentary && <CommentaryBlock commentary={brief.commentary} />}
      </div>
    </Panel>
  )
}

function DateControl({ day, loading, onGo }: { day: BriefDay; loading: boolean; onGo: (date: string) => void }) {
  const current = day.current_trading_date
  const mark = calendarMark(day, day.date)
  const around = (date: string | null) => (date && calendarMark(day, date) ? '（日历未核实）' : '')
  return (
    <div role="group" aria-label="选择交易日" aria-busy={loading || undefined} className="flex flex-wrap items-center gap-1">
      <IconButton icon={ChevronLeft} label={`上一个交易日 ${day.previous_date}${around(day.previous_date)}`}
                  onClick={() => onGo(day.previous_date)} />
      <span id="brief-date" tabIndex={-1} className={cx('min-w-[9.5rem] rounded-sm px-1 text-center text-title text-fg', numeric, focusRing)}
            aria-live="polite">
        {dateLabel(day.date)}
      </span>
      <IconButton
        icon={ChevronRight}
        label={day.next_date ? `下一个交易日 ${day.next_date}${around(day.next_date)}` : '下一个交易日'}
        disabledReason={day.next_date ? undefined : '已是最近的交易日'}
        onClick={() => day.next_date && onGo(day.next_date)}
      />
      {mark && <ReasonChip chip={{ label: mark, tone: 'warn', title: '该年的 A 股节假日表未收录：这一天按工作日当作交易日，可能是休市日。' }} />}
      {day.date !== current && (
        <Button size="sm" variant="ghost" onClick={() => onGo(current)}>回到最近交易日</Button>
      )}
    </div>
  )
}

export default function BriefPage() {
  const [params, setParams] = useSearchParams()
  const raw = params.get('date')
  const explicit = raw && /^\d{4}-\d{2}-\d{2}$/.test(raw) ? raw : null
  const { demo, readOnlyReason } = useAppMode()
  const client = useQueryClient()
  const [busy, setBusy] = useState<BriefKind | null>(null)
  const routes = useMemo(() => new Set(navPages.map((page) => page.path)), [])
  const query = useQuery({
    queryKey: briefsKey(explicit),
    queryFn: () => fetchBriefs(explicit),
    refetchInterval: (current) => pollInterval(current.state.data),
    // The day shown stays (and its date controls stay mounted, keeping focus) until the next one has loaded.
    placeholderData: keepPreviousData,
    retry: 1,
  })
  const day = query.data
  const loading = query.isPlaceholderData

  useEffect(() => {
    const jump = demoJump(query.isPlaceholderData ? undefined : day, explicit)
    if (jump) setParams({ date: jump }, { replace: true })
  }, [day, explicit, setParams, query.isPlaceholderData])

  // A date the person chose stays in the URL (going back to the latest day included), so nothing jumps away
  // from it. When the control used disappears on arrival (回到最近交易日), focus goes to the date itself.
  const moved = useRef(false)
  const go = (date: string) => {
    moved.current = true
    setParams({ date })
  }
  useEffect(() => {
    if (!moved.current || loading) return
    moved.current = false
    const active = document.activeElement
    if (!active || active === document.body) document.getElementById('brief-date')?.focus()
  }, [day?.date, loading])

  const run = async (kind: BriefKind) => {
    setBusy(kind)
    try {
      const result = await runBrief(kind)
      toast.success(result.accepted ? '已开始生成，完成后在这里显示。' : result.message)
      await client.invalidateQueries({ queryKey: BRIEFS_KEY })
    } catch (exc) {
      toast.warn(getApiErrorMessage(exc))
      await client.invalidateQueries({ queryKey: BRIEFS_KEY })
    } finally {
      setBusy(null)
    }
  }

  const empty = day ? KINDS.every((kind) => !day.items[kind].brief) : false
  const showLatest = day && empty && day.latest_date && day.latest_date !== day.date && !demo

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-4 text-body text-fg">
      <h1 className="sr-only">今日简报</h1>
      {day ? (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <DateControl day={day} loading={loading} onGo={go} />
          <p className="text-caption text-fg-3">
            {demo || day.mode === 'demo'
              ? <Badge tone="accent">演示数据，只读</Badge>
              : !day.scheduler_alive && day.date === day.current_trading_date
                ? '后台调度器未运行：简报只在后端运行期间自动生成。'
                : `每个交易日 ${day.items.premarket.time} 盘前、${day.items.postmarket.time} 盘后自动生成 · 刷新于 ${timeLabel(day.now, null, true)}`}
          </p>
        </div>
      ) : !query.isError && <Skeleton w={240} h={28} />}

      {query.isError && !day && (
        <InlineAlert tone="warn" title="无法读取简报" action={<Button size="sm" variant="secondary" onClick={() => void query.refetch()}>重试</Button>}>
          {getApiErrorMessage(query.error)}
        </InlineAlert>
      )}

      {showLatest && (
        <InlineAlert tone="info" action={<Button size="sm" variant="secondary" onClick={() => go(day.latest_date as string)}>查看</Button>}>
          {day.date} 没有简报，最近一期是 {day.latest_date}。
        </InlineAlert>
      )}

      {day ? (
        <div className={cx('grid items-start gap-6 lg:grid-cols-2', loading && 'opacity-60')} aria-busy={loading || undefined}>
          {KINDS.map((kind) => (
            <BriefColumn key={kind} item={day.items[kind]} demo={demo || day.mode === 'demo'} readOnlyReason={readOnlyReason} routes={routes}
                         busy={busy === kind} onRun={(which) => void run(which)} />
          ))}
        </div>
      ) : !query.isError && (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          {KINDS.map((kind) => (
            <Panel key={kind} title={kind === 'premarket' ? '盘前简报' : '盘后简报'}>
              <div role="status" aria-label="正在加载"><Skeleton lines={6} /></div>
            </Panel>
          ))}
        </div>
      )}
    </div>
  )
}
