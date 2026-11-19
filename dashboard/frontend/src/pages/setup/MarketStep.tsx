// 4 行情与提醒: whether the quote source answers, whether the alert monitor and the daily routines run, and
// the push channels. These are configured outside the UI (environment variables read when the backend
// starts), so each item shows the exact line to use; the checklist is read again after a restart.
import { RefreshCw } from 'lucide-react'
import { Button, Panel } from '@/components/ui'
import type { SetupItem } from '@/features/setup/types'
import { Checklist, EnvHowTo } from '@/components/setup/Checklist'

export function MarketStep({ items, refresh, refreshing }: { items: SetupItem[]; refresh: () => void; refreshing: boolean }) {
  return (
    <div className="flex flex-col gap-6">
      <Panel
        title="行情与提醒"
        description="提醒和每日任务只在后端运行时工作；关掉后端就不会提醒。"
        actions={<Button size="sm" icon={RefreshCw} loading={refreshing} onClick={refresh}>重新检查</Button>}
      >
        <Checklist items={items} label="行情与提醒的检查结果" />
      </Panel>
      <Panel title="怎样修改" description="这些开关和时间在后端启动时读取，不在页面里保存。">
        <EnvHowTo />
      </Panel>
    </div>
  )
}
