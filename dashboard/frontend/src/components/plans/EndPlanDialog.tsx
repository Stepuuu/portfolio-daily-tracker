// 结束 / 取消计划: a short dialog. Closing needs a reason (the backend refuses without one); cancelling
// may leave it empty. The backend's message is shown as it is.
import { useState } from 'react'
import { Button, Dialog, Field, InlineAlert, Textarea, toast } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { cancelPlan, closePlan } from '@/services/plans'
import { useRefreshPlans } from '@/features/plans/hooks'

export interface EndPlanDialogProps {
  open: boolean
  mode: 'close' | 'cancel'
  planId: number
  onClose: () => void
  onDone: () => void
}

export function EndPlanDialog({ open, mode, planId, onClose, onDone }: EndPlanDialogProps) {
  const closing = mode === 'close'
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const refresh = useRefreshPlans()

  const submit = async () => {
    if (closing && !reason.trim()) {
      setError('请写明结束原因，例如“已到目标价卖出”或“跌破止损已离场”。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (closing) await closePlan(planId, reason.trim())
      else await cancelPlan(planId, reason.trim() || undefined)
      await refresh()
      toast.success(closing ? '计划已结束，相关提醒已停用' : '计划已取消，相关提醒已停用')
      setReason('')
      onDone()
    } catch (exc) {
      setError(getApiErrorMessage(exc))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="sm"
      title={closing ? '结束计划' : '取消计划'}
      description={closing ? '计划结束后不能再修改，它的提醒会停用。' : '取消后不能再启用，它的提醒会停用。需要时请新建计划。'}
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>返回</Button>
          <Button variant="primary" loading={busy} onClick={() => void submit()}>{closing ? '结束计划' : '取消计划'}</Button>
        </>
      )}
    >
      <div className="flex flex-col gap-3">
        <Field label={closing ? '结束原因' : '取消原因（可不填）'} required={closing} error={error ?? undefined}>
          <Textarea rows={3} value={reason} onChange={(event) => setReason(event.target.value)} placeholder={closing ? '例如：已到目标价卖出' : '例如：不再看好'} />
        </Field>
        {!closing && <InlineAlert tone="info">取消只改变这个计划，不会改动账本里的任何交易。</InlineAlert>}
      </div>
    </Dialog>
  )
}
