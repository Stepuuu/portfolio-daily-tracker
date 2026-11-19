// Desktop navigation: a 224 px sidebar from 1280 px, a 56 px icon rail with tooltips from 768 px.
import { NavLink } from 'react-router-dom'
import { TrendingUp } from 'lucide-react'
import { cx, focusRing } from '@/components/ui'
import type { NavSection } from '@/routes'
import { useAppMode } from './useAppMode'
import { useNavModel } from './useNavModel'

function NavItem({ section, active }: { section: NavSection; active: boolean }) {
  const Icon = section.icon
  return (
    <li className="group/tip relative">
      <NavLink
        to={section.pages[0].path}
        aria-current={active ? 'page' : undefined}
        className={cx(
          'flex h-9 items-center gap-3 rounded-md px-2.5 text-body transition-colors duration-[120ms] max-xl:justify-center max-xl:px-0',
          active ? 'bg-accent/10 font-medium text-accent' : 'text-fg-2 hover:bg-fg/[.06] hover:text-fg',
          focusRing,
        )}
      >
        <Icon size={18} aria-hidden className="shrink-0" />
        <span className="truncate max-xl:sr-only">{section.label}</span>
      </NavLink>
      {/* The icon rail names each entry on hover and keyboard focus. */}
      <span
        aria-hidden
        className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 hidden w-max -translate-y-1/2 rounded-md bg-fg px-2 py-1 text-caption text-bg shadow-overlay group-hover/tip:block group-has-[:focus-visible]/tip:block xl:!hidden"
      >
        {section.label}
      </span>
    </li>
  )
}

export function Sidebar() {
  const { sections, current, ready } = useNavModel()
  const { lab, demo, unknown } = useAppMode()
  const main = sections.filter((section) => section.id !== 'settings')
  const settings = sections.find((section) => section.id === 'settings')
  const modeLabel = lab ? '研究模式' : demo ? '演示模式' : unknown ? '运行模式未确认' : null
  return (
    <aside className="hidden w-14 shrink-0 flex-col border-r border-line bg-surface text-fg md:flex xl:w-56">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-4 max-xl:justify-center max-xl:px-0">
        <TrendingUp size={20} aria-hidden className="shrink-0 text-accent" />
        <span className="text-title max-xl:sr-only">交易助手</span>
      </div>
      <nav aria-label="主导航" className="flex min-h-0 flex-1 flex-col justify-between overflow-y-auto p-2">
        <ul className="flex flex-col gap-0.5">
          {ready && main.map((section) => <NavItem key={section.id} section={section} active={current?.section.id === section.id} />)}
        </ul>
        <div className="flex flex-col gap-2 border-t border-line pt-2">
          {settings && (
            <ul>
              <NavItem section={settings} active={current?.section.id === 'settings'} />
            </ul>
          )}
          {modeLabel && <p className="px-2.5 pb-1 text-caption text-fg-3 max-xl:sr-only">{modeLabel}</p>}
        </div>
      </nav>
    </aside>
  )
}
