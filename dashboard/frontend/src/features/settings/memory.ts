// 设置 › 交易偏好 without React: the assistant's memory as `GET /api/memory` sends it (core/memory/models.py),
// the closed choices of the profile, and what a save has to send. The choices mirror the backend's enums
// and `MemoryExtractor.SECTORS`; tests/test_settings_memory_choices.py keeps them equal. Pure TypeScript.

export interface MemoryProfile {
  name?: string
  experience_level: string
  trading_style: string
  risk_tolerance: string
  preferred_sectors: string[]
  notes: string
}

export interface MemoryLesson {
  date: string
  description: string
  /** failure | success | insight (proposals), win | loss | mistake (older entries). */
  type: string
  symbol: string | null
  lesson: string
  /** The confirmed proposal that wrote it; absent for a lesson written by hand. */
  source_id?: string
}

export interface MemoryState {
  profile: MemoryProfile
  preferences: { emotional_triggers?: string[] }
  history: { lessons: MemoryLesson[] }
}

export interface Choice {
  value: string
  label: string
}

// Labels as the backend writes them into the assistant's context (UserMemory._translate_*).
export const EXPERIENCE_CHOICES: Choice[] = [
  { value: 'beginner', label: '新手（<1年）' },
  { value: 'intermediate', label: '中级（1-5年）' },
  { value: 'expert', label: '专家（>5年）' },
]

export const STYLE_CHOICES: Choice[] = [
  { value: 'day', label: '日内交易' },
  { value: 'swing', label: '波段交易' },
  { value: 'position', label: '中线持股' },
  { value: 'value', label: '价值投资' },
]

export const RISK_CHOICES: Choice[] = [
  { value: 'conservative', label: '保守（追求稳定）' },
  { value: 'moderate', label: '稳健（平衡收益与风险）' },
  { value: 'aggressive', label: '激进（追求高收益）' },
]

/** The sectors the assistant recognises (MemoryExtractor.SECTORS), in its order. */
export const SECTORS: string[] = [
  '科技', '新能源', '半导体', '芯片', '医药', '消费', '金融',
  '银行', '地产', '汽车', '军工', '农业', '白酒', '光伏',
  '锂电', 'AI', '人工智能', '机器人', '传媒', '游戏',
]

export const LESSON_TYPE_LABEL: Record<string, string> = {
  failure: '失败教训', success: '成功经验', insight: '交易心得', win: '成功经验', loss: '失败教训', mistake: '失败教训',
}

/** The proposal kinds this section decides (core/lessons.py). */
export const MEMORY_PROPOSAL_KINDS = ['lesson', 'profile'] as const

export interface ProfileForm {
  experience_level: string
  trading_style: string
  risk_tolerance: string
  sectors: string[]
  notes: string
}

export function profileForm(memory: MemoryState | undefined): ProfileForm {
  const profile = memory?.profile
  return {
    experience_level: profile?.experience_level ?? '',
    trading_style: profile?.trading_style ?? '',
    risk_tolerance: profile?.risk_tolerance ?? '',
    sectors: [...(profile?.preferred_sectors ?? [])],
    notes: profile?.notes ?? '',
  }
}

/**
 * The sector checkboxes: the closed set, then any saved sector outside it (written by an older version or by
 * hand), so it can still be unticked.
 */
export function sectorChoices(saved: string[]): { value: string; extra: boolean }[] {
  const extra = saved.filter((sector) => !SECTORS.includes(sector))
  return [...SECTORS.map((value) => ({ value, extra: false })), ...extra.map((value) => ({ value, extra: true }))]
}

export function toggleSector(sectors: string[], sector: string, on: boolean): string[] {
  if (on) return sectors.includes(sector) ? sectors : [...sectors, sector]
  return sectors.filter((item) => item !== sector)
}

export interface ProfileChanges {
  /** Fields for `PUT /api/memory/profile`; empty when none changed. */
  profile: Partial<Pick<ProfileForm, 'experience_level' | 'trading_style' | 'risk_tolerance' | 'notes'>>
  add: string[]
  remove: string[]
  empty: boolean
}

/** What a save sends: the changed choices and notes, and the sectors to add and to remove. */
export function profileChanges(saved: ProfileForm, form: ProfileForm): ProfileChanges {
  const profile: ProfileChanges['profile'] = {}
  for (const key of ['experience_level', 'trading_style', 'risk_tolerance'] as const) {
    if (form[key] && form[key] !== saved[key]) profile[key] = form[key]
  }
  if (form.notes.trim() !== saved.notes.trim()) profile.notes = form.notes.trim()
  const add = form.sectors.filter((sector) => !saved.sectors.includes(sector))
  const remove = saved.sectors.filter((sector) => !form.sectors.includes(sector))
  return { profile, add, remove, empty: !Object.keys(profile).length && !add.length && !remove.length }
}

/** A lesson as one line plus the event behind it (when that says something else). */
export function lessonText(lesson: MemoryLesson): { text: string; event: string | null } {
  const text = (lesson.lesson || lesson.description || '').trim()
  const event = (lesson.description || '').trim()
  return { text, event: event && event !== text ? event : null }
}

export function choiceLabel(choices: Choice[], value: string | undefined): string {
  return choices.find((choice) => choice.value === value)?.label ?? (value || '未设置')
}

export const NOTES_MAX = 500
export const ITEM_MAX = 200

/** Why a typed lesson or trigger cannot be added, or null. */
export function itemProblem(text: string, existing: string[]): string | null {
  const value = text.trim()
  if (!value) return '请先填写内容'
  if (value.length > ITEM_MAX) return `最多 ${ITEM_MAX} 个字`
  if (existing.some((item) => item.trim() === value)) return '已经记录过了'
  return null
}
