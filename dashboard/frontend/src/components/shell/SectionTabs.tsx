import { Tabs } from '@/components/ui'
import { useNavModel } from './useNavModel'

/** Tabs for the pages of the current section (研究: 工作台 · 回测), when it has more than one. */
export function SectionTabs() {
  const { current, sections } = useNavModel()
  const section = sections.find((item) => item.id === current?.section.id)
  if (!section || section.pages.length < 2) return null
  return (
    <div className="px-4 pt-2 text-body md:px-6">
      <Tabs
        ariaLabel={section.label}
        items={section.pages.map((page) => ({
          id: page.path, label: page.label, href: page.path,
          // Exact when another tab lives below this one (/ledger and /ledger/positions), else sub-paths count too.
          end: page.path === '/' || section.pages.some((other) => other.path.startsWith(`${page.path}/`)),
        }))}
      />
    </div>
  )
}
