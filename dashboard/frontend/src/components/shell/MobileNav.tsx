// Phones (< 768 px): a bottom bar 今日 · 行情 · 记一笔 · 助手 · 更多; 更多 opens a sheet with the other
// sections. Research-only mode shows its two sections and nothing else.
import { NavLink } from 'react-router-dom'
import { MessageSquare, MoreHorizontal, Plus, type LucideIcon } from 'lucide-react'
import { Drawer, cx, focusRing, toast } from '@/components/ui'
import { mobileSplit } from '@/lib/navigation'
import type { NavSection } from '@/routes'
import { useShell } from './shellStore'
import { useAppMode } from './useAppMode'
import { useNavModel } from './useNavModel'

const ITEM =
  'flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-caption transition-colors duration-[120ms]'

function BarButton({
  icon: Icon, label, onClick, active = false, accent = false, pressed, disabledReason,
}: { icon: LucideIcon; label: string; onClick: () => void; active?: boolean; accent?: boolean; pressed?: boolean; disabledReason?: string }) {
  return (
    <button
      type="button"
      aria-disabled={disabledReason ? true : undefined}
      aria-pressed={pressed}
      onClick={() => (disabledReason ? toast.info(disabledReason) : onClick())}
      className={cx(ITEM, accent || active ? 'text-accent' : 'text-fg-2', disabledReason && 'opacity-50', focusRing, 'focus-visible:-outline-offset-2')}
    >
      <Icon size={20} aria-hidden />
      {label}
      {disabledReason && <span className="sr-only">：{disabledReason}</span>}
    </button>
  )
}

function SectionLink({ section, active }: { section: NavSection; active: boolean }) {
  const Icon = section.icon
  return (
    <NavLink
      to={section.pages[0].path}
      aria-current={active ? 'page' : undefined}
      className={cx(ITEM, active ? 'text-accent' : 'text-fg-2', focusRing, 'focus-visible:-outline-offset-2')}
    >
      <Icon size={20} aria-hidden />
      {section.label}
    </NavLink>
  )
}

export function MobileNav() {
  const { sections, current, ready } = useNavModel()
  const { lab, readOnlyReason } = useAppMode()
  const shell = useShell()
  if (!ready) return <nav aria-label="主导航" className="h-14 shrink-0 border-t border-line bg-surface md:hidden" />
  const { primary, more } = mobileSplit(sections)
  const inMore = more.some((section) => section.id === current?.section.id)
  return (
    <>
      <nav aria-label="主导航" className="flex h-14 shrink-0 items-stretch border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden">
        {primary.map((section) => <SectionLink key={section.id} section={section} active={current?.section.id === section.id} />)}
        {!lab && (
          <>
            <BarButton icon={Plus} label="记一笔" accent onClick={() => shell.openEntry()} disabledReason={readOnlyReason} />
            <BarButton icon={MessageSquare} label="助手" active={shell.assistantOpen} pressed={shell.assistantOpen} onClick={shell.toggleAssistant} />
          </>
        )}
        {more.length > 0 && <BarButton icon={MoreHorizontal} label="更多" active={inMore || shell.moreOpen} onClick={() => shell.setMoreOpen(true)} />}
      </nav>
      <Drawer open={shell.moreOpen} onClose={() => shell.setMoreOpen(false)} title="更多">
        <nav aria-label="更多页面" className="-mx-2 flex flex-col gap-4">
          {more.map((section) => {
            const Icon = section.icon
            return (
              <section key={section.id}>
                <h3 className="flex items-center gap-2 px-2 pb-1 text-caption font-medium text-fg-3">
                  <Icon size={14} aria-hidden />
                  {section.label}
                </h3>
                <ul>
                  {section.pages.map((page) => (
                    <li key={page.path}>
                      <NavLink
                        to={page.path}
                        end
                        onClick={() => shell.setMoreOpen(false)}
                        className={({ isActive }) => cx(
                          'flex h-11 items-center rounded-md px-2 text-body',
                          isActive ? 'bg-accent/10 font-medium text-accent' : 'text-fg hover:bg-surface-2',
                          focusRing,
                        )}
                      >
                        {page.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </section>
            )
          })}
        </nav>
      </Drawer>
    </>
  )
}
