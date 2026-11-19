// 研究 › 回测 (/backtest): "would this simple rule have worked on the stock I hold, and how sure can I be?"
// Tabs 快速回测 / 历史 / 数据 / 策略文件. 快速回测 is the form on the left and the run on the right; the run id
// is in the URL (?run=), so a run can be left and opened again, and saved runs open the same way. Links
// from a holding, a plan or the stock drawer prefill the form (contract in features/backtest/model.ts)
// and never run by themselves. Running is not a ledger write: it works in the demo (on its synthetic
// bars) and in research-only mode, where the page calls no desk endpoint (no holdings search).
import { useEffect, useRef, useState, type RefObject } from 'react'
import { useSearchParams } from 'react-router-dom'
import { History } from 'lucide-react'
import { Button, EmptyState, InlineAlert, PageHeader, Skeleton } from '@/components/ui'
import { useAppMode } from '@/components/shell/useAppMode'
import {
  EMPTY_FORM, TABS, buildRunBody, defaultMethod, formFromSpec, hasPrefill, methodParams, parseLink,
  prefillFromLink, refusalErrors, type FieldErrors, type FormState, type TabId,
} from '@/features/backtest/model'
import { isFinished } from '@/features/backtest/result'
import { refusal, startRun } from '@/services/backtest'
import { DataTab } from '@/components/backtest/DataTab'
import { HistoryTab } from '@/components/backtest/HistoryTab'
import { QuickForm } from '@/components/backtest/QuickForm'
import { ResultView } from '@/components/backtest/ResultView'
import { RunProgress } from '@/components/backtest/RunProgress'
import { StrategiesTab } from '@/components/backtest/StrategiesTab'
import { usePresets, useReflection, useRun, useStrategies, useToday } from '@/components/backtest/hooks'

const LAST_RUN = 'backtest:last-run'

function rememberRun(id: string) {
  try {
    sessionStorage.setItem(LAST_RUN, id)
  } catch {
    // Storage can be unavailable (private windows); the URL still holds the run.
  }
}

function lastRun(): string | null {
  try {
    return sessionStorage.getItem(LAST_RUN)
  } catch {
    return null
  }
}

function RunPane({ runId, onResult, onRetry, progressRef }: {
  runId: string | null
  onResult: (runId: string, spec: Record<string, any>) => void
  onRetry: () => void
  progressRef: RefObject<HTMLDivElement>
}) {
  const run = useRun(runId)
  const reflection = useReflection(runId, run.reflection)
  const [, setParams] = useSearchParams()
  const spec = run.result?.manifest?.spec
  // When a result arrives (not on later edits of the form, which are the person's).
  useEffect(() => {
    if (spec && runId) onResult(runId, spec)
  }, [spec])

  if (!runId) {
    const previous = lastRun()
    return (
      <EmptyState
        icon={History}
        title="还没有结果"
        description="选好标的和方法，点“开始回测”。结果会说明收益、和一直持有及大盘的比较，以及这次结果有多可靠。"
        action={previous ? <Button onClick={() => setParams({ run: previous })}>打开上一次回测</Button> : undefined}
      />
    )
  }
  if (run.error && !run.snapshot) {
    return (
      <InlineAlert tone="warn" title="打不开这次回测" action={<Button size="sm" onClick={run.refetch}>重试</Button>}>
        {run.error.message}
      </InlineAlert>
    )
  }
  if (!run.snapshot || run.loading) {
    return <div role="status" aria-label="正在读取回测"><Skeleton lines={6} h={16} /></div>
  }
  if (run.snapshot.status !== 'completed' || !run.result) {
    return <RunProgress ref={progressRef} snapshot={run.snapshot} onRetry={isFinished(run.snapshot.status) ? onRetry : undefined} />
  }
  return <ResultView snapshot={run.snapshot} result={run.result} reflection={reflection} />
}

export default function Backtest() {
  const [params, setParams] = useSearchParams()
  const { mode, demo, lab, unknown, readOnlyReason } = useAppMode()
  const today = useToday()
  const presets = usePresets()
  const link = parseLink(params)
  const tab: TabId = link.tab ?? 'quick'
  const runId = link.run
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [errors, setErrors] = useState<FieldErrors>({})
  const [notes, setNotes] = useState<string[]>([])
  const [conversions, setConversions] = useState<Record<string, string>>({})
  const [starting, setStarting] = useState(false)
  const needStrategies = form.method?.kind === 'strategy' || Boolean(link.strategy) || tab === 'strategies'
  const strategies = useStrategies(needStrategies)
  const presetList = presets.data?.presets ?? []
  const strategyList = strategies.data?.strategies ?? []
  const applied = useRef<string | null>(null)
  const progressRef = useRef<HTMLDivElement>(null)
  const focusProgress = useRef(false)
  const desk = mode === 'full' || mode === 'demo'

  // A link's prefill, once the presets (and the strategies it names) are known; a new link applies again.
  const search = params.toString()
  useEffect(() => {
    if (!presets.data || !hasPrefill(link) || applied.current === search) return
    if (link.strategy && !strategies.data) return
    applied.current = search
    const prefill = prefillFromLink(link, { presets: presetList, strategies: strategyList })
    setForm(prefill.form)
    setNotes(prefill.notes)
    setConversions(prefill.conversions)
    setErrors({})
  }, [search, presets.data, strategies.data]) // the link is read from `search`

  // A fresh form starts with the default method once the presets are known.
  useEffect(() => {
    if (presets.data && !form.method) setForm((current) => (current.method ? current : { ...current, method: defaultMethod(presetList) }))
  }, [presets.data]) // only when the list arrives

  // A refusal moves focus to the first field it is about (scrolling it into view), never a toast.
  useEffect(() => {
    if (!Object.keys(errors).length) return
    const field = document.querySelector<HTMLElement>('form[aria-label="快速回测"] [aria-invalid="true"]')
    if (field) field.focus()
    else document.querySelector<HTMLElement>('form[aria-label="快速回测"] [role="alert"]')?.scrollIntoView({ block: 'center' })
  }, [errors])

  useEffect(() => {
    if (runId) rememberRun(runId)
    if (runId && focusProgress.current) {
      focusProgress.current = false
      window.setTimeout(() => progressRef.current?.focus(), 50)
    }
  }, [runId])

  const setTab = (id: string) => {
    const next = new URLSearchParams()
    if (id !== 'quick') next.set('tab', id)
    else if (runId) next.set('run', runId)
    setParams(next)
  }

  // The form shows the run on the right. A run this form started already matches it (and may have been
  // edited since); a run opened from history, a rerun or a reload fills it with that run's settings, once.
  const startedHere = useRef<string | null>(null)
  const filledFrom = useRef<string | null>(null)
  const onResult = (id: string, spec: Record<string, any>) => {
    if (filledFrom.current === id || startedHere.current === id) return
    filledFrom.current = id
    if (hasPrefill(link)) return   // a link's prefill wins
    setForm((current) => formFromSpec(spec, { ...EMPTY_FORM, method: current.method }))
    setNotes([])
    setConversions({})
    setErrors({})
  }
  const onRetry = () => document.querySelector<HTMLElement>('form[aria-label="快速回测"] input')?.focus()

  const run = async () => {
    const context = { presets: presetList, strategies: strategyList }
    const built = buildRunBody(form, context)
    if (!built.ok) {
      setErrors(built.errors)
      return
    }
    setErrors({})
    setStarting(true)
    try {
      // While the mode cannot be read (it may be a demo) the run is made but nothing is saved to history.
      const answer = await startRun(unknown ? { ...built.body, save: false } : built.body)
      startedHere.current = answer.run_id
      focusProgress.current = true
      setParams(new URLSearchParams({ run: answer.run_id }))
    } catch (exc) {
      setErrors(refusalErrors(refusal(exc), methodParams(form.method, context)))
    } finally {
      setStarting(false)
    }
  }

  const presetsProblem = presets.isError
    ? `读不到方法列表：${refusal(presets.error).message}`
    : presets.data && !presets.data.available ? presets.data.detail ?? '预设方法不可用' : null

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageHeader
        title="回测"
        description="用历史行情检验一个简单规则在一只股票上的表现，并说明这个结果有多可靠。"
        tabs={TABS.map((item) => ({ id: item.id, label: item.label }))}
        tab={tab}
        onTabChange={setTab}
        tabsLabel="回测"
      />
      {demo && (
        <InlineAlert title="演示模式">
          回测用程序生成的虚构行情，只能选演示持仓里的标的；结果不代表任何真实股票，也不会保存到历史。
        </InlineAlert>
      )}

      {tab === 'quick' && (
        <div className="grid min-w-0 gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
          <div className="min-w-0">
            <QuickForm
              form={form}
              onChange={(next) => {
                setForm(next)
                if (Object.keys(errors).length) setErrors({})
              }}
              presets={presetList}
              presetsProblem={presetsProblem}
              strategies={strategyList}
              errors={errors}
              notes={notes}
              conversions={conversions}
              lab={!desk}
              today={today}
              running={starting}
              onRun={() => void run()}
            />
          </div>
          <div className="min-w-0">
            <RunPane runId={runId} onResult={onResult} onRetry={onRetry} progressRef={progressRef} />
          </div>
        </div>
      )}
      {tab === 'history' && <HistoryTab readOnlyReason={readOnlyReason} />}
      {tab === 'data' && <DataTab readOnlyReason={readOnlyReason} today={today} />}
      {tab === 'strategies' && (
        <StrategiesTab
          onUse={(id) => {
            setForm((current) => ({ ...current, method: { kind: 'strategy', id }, params: {} }))
            setNotes([])
            setConversions({})
            setParams(new URLSearchParams())
          }}
        />
      )}
      {lab && tab === 'quick' && (
        <p className="text-table text-fg-3">研究模式不读取持仓和自选：标的请直接填代码。</p>
      )}
    </div>
  )
}
