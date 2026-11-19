// 设置 › its sections (plan 2.6, `/settings/:section`): the list, which modes show each one, and where a URL
// leads. `/settings` and a section the mode does not show go to the mode's first section. Pure TypeScript
// (node tests).
import type { Mode } from '../../lib/navigation.ts'

export type SectionId = 'accounts' | 'models' | 'data' | 'automation' | 'preferences' | 'backup' | 'about'

export interface SettingsSection {
  id: SectionId
  label: string
  /** One sentence under the section title. */
  description: string
  /** Modes that show it; every mode when omitted. */
  modes?: Mode[]
}

/** The daily desk's sections: they read or write the ledger, the desk store or the assistant's memory. */
const DESK: Mode[] = ['full', 'demo']

export const SETTINGS_BASE = '/settings'

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: 'accounts', label: '账户与费率', description: '每个账户的费率，记一笔时按它预填费用估算。', modes: DESK },
  { id: 'models', label: '模型', description: '对话助手用的模型，和研究工作台的模型连接。' },
  { id: 'data', label: '数据与行情', description: '行情源、日线缓存和交易日历的现状。' },
  { id: 'automation', label: '自动化与通知', description: '价格提醒监控、每日例行任务和通知渠道。', modes: DESK },
  { id: 'preferences', label: '交易偏好', description: '助手记住的交易经验、风格、风险偏好和教训。', modes: DESK },
  { id: 'backup', label: '备份与导出', description: '下载账本和桌面数据的备份，导出流水。', modes: DESK },
  { id: 'about', label: '关于与帮助', description: '版本、外观、快捷键和使用说明。' },
]

export function settingsPath(id: SectionId): string {
  return `${SETTINGS_BASE}/${id}`
}

/** The sections a mode shows, in order; every section while the mode is not known yet. */
export function sectionsFor(mode: Mode | undefined): SettingsSection[] {
  if (!mode) return SETTINGS_SECTIONS
  return SETTINGS_SECTIONS.filter((section) => !section.modes || section.modes.includes(mode))
}

export interface SectionMatch {
  /** The section to show; null while the URL must first move (see `redirect`). */
  section: SettingsSection | null
  /** Where to go instead (replace the history entry), or null. */
  redirect: string | null
}

/**
 * What `/settings/<slug>` shows in `mode`. No slug, an unknown one or a section the mode does not show
 * redirects to the mode's first section; while the mode is unknown a known slug is shown as it is, and
 * `/settings` waits (no redirect yet, so research-only mode never passes through 账户与费率).
 */
export function resolveSection(slug: string | undefined, mode: Mode | undefined): SectionMatch {
  const visible = sectionsFor(mode)
  const found = visible.find((section) => section.id === slug)
  if (found) return { section: found, redirect: null }
  if (!mode) return { section: null, redirect: null }
  return { section: null, redirect: settingsPath(visible[0].id) }
}
