// 6 完成: the whole checklist, what is still to do first, then optional, done and not applicable; 完成 sets
// the persisted flag and opens Today (研究工作台 in research-only mode). In the demo nothing is saved, so
// the button only leads to Today.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { Button, InlineAlert, Panel, buttonClass } from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { completeSetup } from '@/services/setup'
import { STATE_LABELS, groupItems } from '@/features/setup/steps'
import type { SetupItem } from '@/features/setup/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { Checklist } from '@/components/setup/Checklist'

export function DoneStep({ items, complete, refresh }: { items: SetupItem[]; complete: boolean | null; refresh: () => Promise<unknown> }) {
  const { lab, readOnly, readOnlyReason } = useAppMode()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const home = lab ? '/lab' : '/'
  const homeLabel = lab ? '打开研究工作台' : '打开今日'
  const groups = groupItems(lab ? items.filter((item) => item.id === 'llm') : items)
  const todo = groups.find((group) => group.state === 'todo')?.items.length ?? 0

  const finish = async () => {
    setBusy(true)
    setFailure(null)
    try {
      await completeSetup()
      await refresh()
      navigate(home)
    } catch (exc) {
      setFailure(getApiErrorMessage(exc))
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Panel
        title={todo ? `还有 ${todo} 项待处理` : '设置完成'}
        description={todo ? '待处理的项目不影响使用，可以之后再回来；向导会一直在设置页里。' : '每一项都已检查；之后可在设置中重新运行向导。'}
      >
        <div className="flex flex-col gap-6">
          {groups.map((group) => (
            <section key={group.state} aria-label={STATE_LABELS[group.state]} className="flex flex-col gap-2">
              <h3 className="text-table font-medium text-fg-2">{STATE_LABELS[group.state]}（{group.items.length}）</h3>
              <Checklist items={group.items} label={`${STATE_LABELS[group.state]}的项目`} />
            </section>
          ))}
        </div>
      </Panel>
      {failure && <InlineAlert tone="warn" title="没有保存完成状态">{failure}</InlineAlert>}
      <div className="flex flex-wrap items-center gap-3">
        {readOnly ? (
          <>
            <Link to={home} className={buttonClass({ variant: 'primary' })}>{homeLabel}<ArrowRight size={16} aria-hidden /></Link>
            {readOnlyReason && <span className="text-table text-fg-3">{readOnlyReason}：完成状态不会保存。</span>}
          </>
        ) : (
          <Button variant="primary" icon={ArrowRight} loading={busy} onClick={() => void finish()}>
            {complete ? `${homeLabel}` : `完成，${homeLabel}`}
          </Button>
        )}
      </div>
    </div>
  )
}
