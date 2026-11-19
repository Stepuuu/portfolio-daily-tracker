// 设置 at `/settings/:section` (plan 2.6): the section list on the left from 768 px, a select above the
// section on phones. `/settings` and a section the mode does not show go to the mode's first section
// (features/settings/sections.ts), with the query and hash they came with. Each section is a component in
// this folder made of Panels; one 保存 per form, writes disabled with readOnlyReason.
import type { ComponentType } from 'react'
import { NavLink, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import { Field, PageHeader, Select, cx } from '@/components/ui'
import { focusRingInset } from '@/components/ui/cx'
import { useAppMode } from '@/components/shell/useAppMode'
import { resolveSection, sectionsFor, settingsPath, type SectionId } from '@/features/settings/sections'
import { AccountsSection } from './AccountsSection'
import { AboutSection } from './AboutSection'
import { AutomationSection } from './AutomationSection'
import { BackupSection } from './BackupSection'
import { DataSection } from './DataSection'
import { ModelSection } from './ModelSection'
import { PreferencesSection } from './PreferencesSection'

const BODY: Record<SectionId, ComponentType> = {
  accounts: AccountsSection,
  models: ModelSection,
  data: DataSection,
  automation: AutomationSection,
  preferences: PreferencesSection,
  backup: BackupSection,
  about: AboutSection,
}

export default function Settings() {
  const { section: slug } = useParams()
  const { mode } = useAppMode()
  const { search, hash } = useLocation()
  const navigate = useNavigate()
  const match = resolveSection(slug, mode)
  if (match.redirect) return <Navigate to={`${match.redirect}${search}${hash}`} replace />
  const sections = sectionsFor(mode)
  const current = match.section
  const Body = current ? BODY[current.id] : null
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <PageHeader title="设置" description={current?.description} />
      <div className="grid min-w-0 gap-6 md:grid-cols-[192px_minmax(0,1fr)] md:items-start">
        <nav aria-label="设置分区" className="hidden md:sticky md:top-0 md:block">
          <ul className="flex flex-col gap-0.5">
            {sections.map((section) => (
              <li key={section.id}>
                <NavLink
                  to={settingsPath(section.id)}
                  className={({ isActive }) => cx(
                    'flex h-9 items-center rounded-md px-3 text-body transition-colors duration-[120ms]',
                    isActive ? 'bg-accent/10 font-medium text-accent' : 'text-fg-2 hover:bg-surface-2 hover:text-fg',
                    focusRingInset,
                  )}
                >
                  {section.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="md:hidden">
          <Field label="设置分区">
            <Select
              value={current?.id ?? ''}
              onChange={(event) => navigate(settingsPath(event.target.value as SectionId))}
              options={sections.map((section) => ({ value: section.id, label: section.label }))}
            />
          </Field>
        </div>
        <div className="flex min-w-0 flex-col gap-6">{Body && <Body />}</div>
      </div>
    </div>
  )
}
