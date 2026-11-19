// 2 账户与期初: the first account and its opening balance, in one of three ways: typed here (从零开始),
// imported from a broker file (the 导入 page), or migrated from the v3.2 holdings file. The step reads as
// done as soon as the checklist says the ledger has an opening balance, whichever way it got there; a legacy
// source or account that is not in the ledger yet stays on offer (accountStepView).
import { Link } from 'react-router-dom'
import { BookOpen, FileUp, Plus, RefreshCw } from 'lucide-react'
import { Button, InlineAlert, Panel, SegmentedControl, buttonClass } from '@/components/ui'
import { accountStepView, type StartChoice } from '@/features/setup/steps'
import type { SetupItem } from '@/features/setup/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useShell } from '@/components/shell/shellStore'
import { LegacyMigration } from './LegacyMigration'
import { OpeningForm } from './OpeningForm'

type Way = 'zero' | 'import' | 'legacy'

const WAYS: { value: Way; label: string }[] = [
  { value: 'zero', label: '新建账户' },
  { value: 'import', label: '导入券商文件' },
  { value: 'legacy', label: '迁移旧版持仓' },
]

export interface AccountStepProps {
  start: StartChoice | null
  onStart: (way: Way) => void
  opening: SetupItem | undefined
  legacy: SetupItem | undefined
  refresh: () => void
  refreshing: boolean
}

export function AccountStep({ start, onStart, opening, legacy, refresh, refreshing }: AccountStepProps) {
  const { readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  // An old holdings file not migrated yet suggests migrating it, unless another way was chosen.
  const way: Way = start === 'import' || start === 'legacy' || start === 'zero' ? start
    : legacy?.state === 'todo' ? 'legacy' : 'zero'
  const done = opening?.state === 'done'
  const view = accountStepView(opening, legacy, start)

  return (
    <div className="flex flex-col gap-6">
      {done && opening && (
        <InlineAlert tone="info" title="这一步已完成">
          <p className="break-words">{opening.detail}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Link to="/ledger" className={buttonClass({ variant: 'secondary', size: 'sm' })}><BookOpen size={14} aria-hidden />查看账本</Link>
            <Button size="sm" variant="ghost" icon={Plus} disabledReason={readOnlyReason} onClick={() => openEntry({ kind: 'opening' })}>
              再建一个账户
            </Button>
          </div>
        </InlineAlert>
      )}
      {opening?.state === 'unavailable' && <InlineAlert tone="warn" title="读不到账本">{opening.detail}</InlineAlert>}
      {view.notice && legacy && (
        <InlineAlert tone="info" title="发现旧版持仓文件">
          <p className="break-words">{legacy.detail}</p>
          {view.offer && (
            <div className="mt-2">
              <Button size="sm" variant="secondary" onClick={() => onStart('legacy')}>迁移旧版持仓</Button>
            </div>
          )}
        </InlineAlert>
      )}
      {view.migrate && <LegacyMigration onConfirmed={refresh} />}

      {view.ways && (
        <>
          <SegmentedControl ariaLabel="建立期初的方式" options={WAYS} value={way} onChange={onStart} />
          {way === 'zero' && <OpeningForm onConfirmed={refresh} />}
          {way === 'legacy' && <LegacyMigration onConfirmed={refresh} />}
          {way === 'import' && (
            <Panel title="导入券商文件" description="用券商导出的持仓、交割单或对账单建立账本，逐行核对后入账。">
              <div className="flex flex-col gap-3 text-table text-fg-2">
                <p>导入页会识别文件格式、对应列、去掉重复，再生成入账预览。导入并确认后回到这里，这一步会显示为已完成。</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Link to="/ledger/import" className={buttonClass({ variant: 'primary' })}>
                    <FileUp size={16} aria-hidden />打开导入页
                  </Link>
                  <Button icon={RefreshCw} loading={refreshing} onClick={refresh}>重新检查</Button>
                </div>
              </div>
            </Panel>
          )}
        </>
      )}
    </div>
  )
}
