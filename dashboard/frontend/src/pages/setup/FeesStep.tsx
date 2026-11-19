// 3 费率: each account's fee profile, with the same form as 设置 › 账户与费率 (AccountsSection). The market
// defaults are explained first; saving a profile (even the preset unchanged) marks it reviewed.
import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Panel } from '@/components/ui'
import { PROFILES_KEY, fetchProfiles } from '@/services/ledger'
import type { SetupItem } from '@/features/setup/types'
import { AccountsSection } from '@/pages/settings/AccountsSection'
import { StateChip } from '@/components/setup/Checklist'

export function FeesStep({ item, refresh }: { item: SetupItem | undefined; refresh: () => void }) {
  // A saved profile changes the profiles query: read the checklist again.
  const profiles = useQuery({ queryKey: PROFILES_KEY, queryFn: fetchProfiles })
  const seen = useRef(profiles.dataUpdatedAt)
  useEffect(() => {
    if (profiles.dataUpdatedAt && profiles.dataUpdatedAt !== seen.current) {
      seen.current = profiles.dataUpdatedAt
      refresh()
    }
  }, [profiles.dataUpdatedAt, refresh])

  return (
    <div className="flex flex-col gap-6">
      <Panel title="费率预设" description="记一笔时按账户费率预填费用估算，标为“估算”；实际费用以成交单为准。">
        <ul className="flex list-disc flex-col gap-1 pl-5 text-table text-fg-2">
          <li>A 股（股票、ETF）：佣金万分之 2.5，最低 5 元；过户费 0.001%、卖出印花税 0.05% 按法定费率另计。</li>
          <li>港股、美股没有预设：不设置时，这两个市场的费用按成交单逐笔填写。</li>
          <li>券商给的佣金不同（如万 1、免五）时在下面改好再保存；预设合适也请保存一次，表示已核对。</li>
        </ul>
      </Panel>
      {item && (
        <div className="flex flex-wrap items-center gap-2 text-table text-fg-2">
          <StateChip state={item.state} />
          <span className="min-w-0 break-words">{item.detail}</span>
        </div>
      )}
      <AccountsSection />
    </div>
  )
}
