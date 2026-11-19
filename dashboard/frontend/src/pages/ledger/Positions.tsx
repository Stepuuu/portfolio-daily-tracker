// 账本 › 持仓与成本 (/ledger/positions): each account's holdings at weighted average cost, by the confirmed
// ledger. Market value and profit at live prices are on 今日.
import { Panel } from '@/components/ui'
import { useAppMode } from '@/components/shell/useAppMode'
import { PositionsTable } from '@/components/ledger/PositionsTable'
import { LedgerFrame } from './LedgerFrame'

export default function LedgerPositions() {
  const { readOnlyReason } = useAppMode()
  return (
    <LedgerFrame title="账本 · 持仓与成本">
      {(ledger) => (
        <Panel
          padding="none"
          title="持仓与成本"
          description="加权平均成本，买入费用计入成本；不是实时市值，实时盈亏见今日。"
        >
          <PositionsTable accounts={ledger.accounts} writeBlocked={readOnlyReason} />
        </Panel>
      )}
    </LedgerFrame>
  )
}
