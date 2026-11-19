// 设置向导 (/setup, plan EN-5): from a fresh checkout (or the v3.2 holdings file) to a working Today page.
// Six steps (研究模式: 欢迎, AI 助手, 完成), each showing its real state from GET /api/setup/status; every
// step can be skipped and opened again, and none blocks the app. `?step=` is the step shown (without it a
// visit resumes where setup stands), `?start=` the way chosen on 欢迎. 稍后再说 dismisses the wizard, so
// Today never sends the person here again. In the demo every step is viewable and nothing can be saved.
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button, InlineAlert, PageHeader, Skeleton, buttonClass, toast } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { dismissSetup } from '@/services/setup'
import {
  STEP_LABELS, itemsOf, neighbours, parseStart, parseStep, resumeStep, stepState, stepsFor, type StartChoice, type StepId,
} from '@/features/setup/steps'
import { useAppMode } from '@/components/shell/useAppMode'
import { AccountStep } from './AccountStep'
import { AiStep } from './AiStep'
import { DoneStep } from './DoneStep'
import { FeesStep } from './FeesStep'
import { MarketStep } from './MarketStep'
import { Stepper } from './Stepper'
import { WelcomeStep } from './WelcomeStep'
import { useRefreshSetup, useSetupStatus } from './useSetup'

export default function Setup() {
  const { mode, lab, demo, readOnly, readOnlyReason } = useAppMode()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const status = useSetupStatus()
  const refresh = useRefreshSetup()
  const steps = stepsFor(mode)
  const items = status.data?.items
  const requested = parseStep(params.get('step'), steps)
  const step: StepId | null = requested ?? (status.isPending ? null : resumeStep(steps, items))
  const start = parseStart(params.get('start'))
  const { previous, next } = step ? neighbours(steps, step) : { previous: null, next: null }
  const state = step ? stepState(step, items) : 'unknown'

  const go = (target: StepId, choice: StartChoice | null = start) => {
    const query: Record<string, string> = { step: target }
    if (choice) query.start = choice
    setParams(query)
    document.getElementById('main')?.scrollTo(0, 0)
  }
  const dismiss = useMutation({
    mutationFn: dismissSetup,
    onSuccess: async () => {
      await refresh()
      toast.info('已关闭设置向导：今日页不会再自动打开它，可在设置中重新运行。')
      navigate(lab ? '/lab' : '/')
    },
    onError: (error) => toast.warn(`没有关闭：${getApiErrorMessage(error)}`),
  })

  const later = readOnly ? (
    <Link to={lab ? '/lab' : '/'} className={buttonClass({ variant: 'ghost' })}>返回{lab ? '研究工作台' : '今日'}</Link>
  ) : (
    <Button variant="ghost" loading={dismiss.isPending} onClick={() => dismiss.mutate()} title="以后不再自动打开向导；可在设置中重新运行">
      稍后再说
    </Button>
  )

  const nextLabel = step === 'welcome' ? '开始' : state === 'todo' ? '跳过这一步' : '下一步'
  return (
    <div className="mx-auto flex max-w-[960px] flex-col gap-6 text-body text-fg">
      <PageHeader title="设置向导" description="每一步都显示当前状态，都可以跳过；之后可在设置中重新打开。" actions={later} />
      {demo && (
        <InlineAlert tone="info" title="演示模式：只能浏览">
          每一步都可以查看，但不能保存任何设置（{readOnlyReason}）。
        </InlineAlert>
      )}
      {status.isError && (
        <InlineAlert tone="warn" title="读不到设置状态" action={<Button size="sm" onClick={() => void status.refetch()}>重试</Button>}>
          {getApiErrorMessage(status.error)}；各步骤仍可浏览，状态显示为不可用。
        </InlineAlert>
      )}
      <Stepper steps={steps} current={step} items={items} onSelect={(target) => go(target)} />

      <section aria-label={step ? STEP_LABELS[step] : '正在读取'} className="flex min-w-0 flex-col gap-6">
        {step === null && <Skeleton lines={5} />}
        {step === 'welcome' && (
          <WelcomeStep
            lab={lab}
            start={start}
            onChoose={(choice) => (choice === 'demo' ? go('welcome', 'demo') : go('account', choice))}
          />
        )}
        {step === 'account' && (
          <AccountStep
            start={start}
            onStart={(way) => go('account', way)}
            opening={items?.find((item) => item.id === 'opening')}
            legacy={items?.find((item) => item.id === 'legacy')}
            refresh={() => void refresh()}
            refreshing={status.isFetching}
          />
        )}
        {step === 'fees' && <FeesStep item={items?.find((item) => item.id === 'fees')} refresh={() => void refresh()} />}
        {step === 'market' && <MarketStep items={itemsOf('market', items)} refresh={() => void status.refetch()} refreshing={status.isFetching} />}
        {step === 'ai' && <AiStep refresh={refresh} />}
        {step === 'done' && <DoneStep items={items ?? []} complete={status.data?.setup_complete ?? null} refresh={refresh} />}
      </section>

      {step && step !== 'done' && (
        <nav aria-label="向导翻页" className="flex items-center justify-between gap-3 border-t border-line pt-4">
          {previous ? (
            <Button variant="ghost" icon={ChevronLeft} onClick={() => go(previous)}>上一步</Button>
          ) : <span />}
          {next && (
            <Button variant={state === 'todo' ? 'secondary' : 'primary'} onClick={() => go(next)}>
              {nextLabel}
              <ChevronRight size={16} aria-hidden />
            </Button>
          )}
        </nav>
      )}
    </div>
  )
}
