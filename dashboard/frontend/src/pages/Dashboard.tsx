import ChatPanel from '@/components/ChatPanel'
import PortfolioPanel from '@/components/PortfolioPanel'
import SuggestionsPanel from '@/components/SuggestionsPanel'

export default function Dashboard() {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 relative min-h-full">
      {/* 左侧：对话区域 */}
      <div className="col-span-1 lg:col-span-7 h-full">
        <ChatPanel />
      </div>

      {/* 右侧：持仓和建议 */}
      <div className="col-span-1 lg:col-span-5 flex flex-col gap-6 sticky top-0 self-start max-h-[calc(100vh-6rem)] overflow-y-auto pb-4 custom-scrollbar">
        <PortfolioPanel />
        <SuggestionsPanel />
      </div>
    </div>
  )
}
