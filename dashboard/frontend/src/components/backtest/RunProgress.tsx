// A run in progress: 准备数据（本地缓存 / 正在下载）→ 模拟交易 45% → 统计, with 取消. The run id is in the URL,
// so the page can be left and opened again; a finished run that failed says why, as the service wrote it.
import { forwardRef, useState } from 'react'
import { Check, Circle, Loader2, X } from 'lucide-react'
import { Badge, Button, InlineAlert, cx, numeric } from '@/components/ui'
import { progressSteps, statusChip } from '@/features/backtest/result'
import { cancelRun, refusal, type RunSnapshot } from '@/services/backtest'

const ICONS = { done: Check, active: Loader2, pending: Circle, stopped: X } as const

export const RunProgress = forwardRef<HTMLDivElement, { snapshot: RunSnapshot; onRetry?: () => void }>(function RunProgress(
  { snapshot, onRetry }, ref,
) {
  const [cancelling, setCancelling] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const steps = progressSteps(snapshot)
  const chip = statusChip(snapshot.status)
  const live = snapshot.status === 'queued' || snapshot.status === 'running'
  const cancel = async () => {
    setCancelling(true)
    setProblem(null)
    try {
      await cancelRun(snapshot.run_id)
    } catch (exc) {
      setProblem(refusal(exc).message)
    } finally {
      setCancelling(false)
    }
  }
  return (
    <div ref={ref} tabIndex={-1} role="status" aria-label="回测进度" className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-4 outline-none">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-title text-fg">{live ? '正在回测' : '回测没有完成'}</h2>
          <Badge tone={chip.tone}>{chip.text}</Badge>
        </div>
        {live && <Button size="sm" variant="ghost" icon={X} loading={cancelling} onClick={() => void cancel()}>取消</Button>}
      </div>
      <ol className="flex flex-col gap-2 sm:flex-row sm:gap-6">
        {steps.map((step) => {
          const Icon = ICONS[step.state]
          return (
            <li key={step.id} className={cx('flex items-center gap-2 text-body', step.state === 'pending' ? 'text-fg-3' : 'text-fg')}>
              <Icon
                size={16}
                aria-hidden
                className={cx('shrink-0', step.state === 'active' && 'animate-spin motion-reduce:animate-none text-accent', step.state === 'stopped' && 'text-warn')}
              />
              <span>{step.label}</span>
              {step.detail && <span className={cx('text-table text-fg-2', numeric)}>{step.detail}</span>}
            </li>
          )
        })}
      </ol>
      {live && <p className="text-table text-fg-3">第一次回测一只股票要下载行情，约 5–20 秒；之后用本地缓存。可以离开这一页，回来时进度还在。</p>}
      {snapshot.status === 'failed' && (
        <InlineAlert tone="warn" title="回测失败" action={onRetry && <Button size="sm" onClick={onRetry}>修改后重试</Button>}>
          {snapshot.error?.message ?? '服务没有给出原因'}
        </InlineAlert>
      )}
      {snapshot.status === 'cancelled' && <InlineAlert tone="warn">回测已取消，没有结果。</InlineAlert>}
      {problem && <InlineAlert tone="warn">{problem}</InlineAlert>}
    </div>
  )
})
