import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import api from '@/services/api'

type Proposal = {
  id: string
  revision: number
  events: { account?: string; date: string }[]
}

export default function LedgerProposals() {
  const query = useQuery({
    queryKey: ['ledger-chat-proposals'],
    queryFn: async () => (await api.get<{ revision: number; pending: Proposal[] }>('/ledger')).data,
    refetchInterval: 5000,
  })
  if (query.isError) return (
    <div className="border-b border-slate-700 px-4 py-3 text-sm text-amber-300" role="alert">
      暂时无法核对入账状态。
      <button className="ml-2 underline" onClick={() => void query.refetch()}>重新查询</button>
      <Link className="ml-3 underline" to="/ledger">查看账本</Link>
    </div>
  )
  if (!query.data) return null
  const { revision, pending } = query.data
  if (!revision && !pending.length) return (
    <div className="border-b border-slate-700 px-4 py-3 text-sm text-slate-300">
      开始记账前，请先核对账户的期初现金、持仓和本金。
      <Link className="ml-2 text-primary-300 underline" to="/ledger">设置期初余额</Link>
    </div>
  )
  if (!pending.length) return null
  return (
    <section aria-label="待确认账本方案" className="border-b border-slate-700 px-4 py-3 text-sm">
      <p className="text-slate-200">{pending.length} 个方案待确认，尚未入账</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {pending.slice(0, 3).map(proposal => (
          <Link key={proposal.id} to={`/ledger?proposal=${proposal.id}`}
            className="rounded-lg bg-slate-700 px-3 py-2 text-primary-300 hover:bg-slate-600 break-all">
            核对方案 · {proposal.events[0]?.date || '重复导入'} · {proposal.events.length} 笔
            {proposal.revision !== revision && '（已过期，需重新预览）'}
          </Link>
        ))}
        {pending.length > 3 && <Link className="px-3 py-2 underline" to="/ledger">查看全部方案</Link>}
      </div>
    </section>
  )
}
