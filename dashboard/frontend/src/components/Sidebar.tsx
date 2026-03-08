import {
  MessageSquare,
  Wallet,
  Brain,
  TrendingUp,
  Settings,
  HelpCircle,
  BarChart2,
  LineChart
} from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { clsx } from 'clsx'

const navItems = [
  { icon: MessageSquare, label: '对话', path: '/' },
  { icon: Wallet, label: '持仓', path: '/portfolio' },
  { icon: LineChart, label: '跟踪', path: '/tracker' },
  { icon: TrendingUp, label: '行情', path: '/market' },
  { icon: Brain, label: '记忆', path: '/memory' },
  { icon: BarChart2, label: '回测', path: '/backtest' },
]

const bottomItems = [
  { icon: Settings, label: '设置', path: '/settings' },
  { icon: HelpCircle, label: '帮助', path: '/help' },
]

export default function Sidebar() {
  const location = useLocation()

  return (
    <aside className="flex w-64 flex-col bg-slate-800 border-r border-slate-700">
      {/* Logo */}
      <div className="flex h-16 items-center px-6 border-b border-slate-700">
        <TrendingUp className="h-8 w-8 text-primary-500" />
        <span className="ml-3 text-xl font-bold">交易助手</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-4 py-4 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon
          const isActive = location.pathname === item.path

          return (
            <Link
              key={item.path}
              to={item.path}
              className={clsx(
                'flex items-center px-4 py-3 rounded-lg transition-colors',
                isActive
                  ? 'bg-primary-600 text-white'
                  : 'text-slate-300 hover:bg-slate-700 hover:text-white'
              )}
            >
              <Icon className="h-5 w-5" />
              <span className="ml-3">{item.label}</span>
            </Link>
          )
        })}
      </nav>

      {/* Bottom Navigation */}
      <div className="border-t border-slate-700 px-4 py-4 space-y-1">
        {bottomItems.map((item) => {
          const Icon = item.icon
          const isActive = location.pathname === item.path

          return (
            <Link
              key={item.path}
              to={item.path}
              className={clsx(
                'flex items-center px-4 py-3 rounded-lg transition-colors',
                isActive
                  ? 'bg-primary-600 text-white'
                  : 'text-slate-300 hover:bg-slate-700 hover:text-white'
              )}
            >
              <Icon className="h-5 w-5" />
              <span className="ml-3">{item.label}</span>
            </Link>
          )
        })}
      </div>
    </aside>
  )
}
