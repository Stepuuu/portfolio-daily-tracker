// The assistant's memory (`/api/memory`, backend/api/memory.py), as 设置 › 交易偏好 reads and edits it. Every
// write is refused (409, with the reason) in the demo and while the memory file cannot be read; without the
// assistant service (the demo, research-only mode) every request is a 503.
import api from './api'
import type { MemoryLesson, MemoryState } from '@/features/settings/memory'

export const MEMORY_KEY = ['memory'] as const

export async function fetchMemory(): Promise<MemoryState> {
  return (await api.get<MemoryState>('/memory')).data
}

export async function updateProfile(fields: Partial<Record<'experience_level' | 'trading_style' | 'risk_tolerance' | 'notes', string>>): Promise<void> {
  await api.put('/memory/profile', fields)
}

export async function addSector(sector: string): Promise<void> {
  await api.post('/memory/preferences/sector', { sector })
}

export async function removeSector(sector: string): Promise<void> {
  await api.delete('/memory/preferences/sector', { data: { sector } })
}

export async function addTrigger(trigger: string): Promise<void> {
  await api.post('/memory/preferences/trigger', { trigger })
}

export async function removeTrigger(trigger: string): Promise<void> {
  await api.delete('/memory/preferences/trigger', { data: { trigger } })
}

/** A lesson written by hand (type 交易心得). */
export async function addLesson(text: string): Promise<void> {
  await api.post('/memory/history/lesson', { description: text, lesson_type: 'insight', lesson: text })
}

/**
 * The lesson at `index` in the list as last read, named by `lesson` itself: the backend deletes it only if
 * that place still holds it, and answers 409 (a fixed text, nothing deleted) when the list has changed
 * meanwhile. The caller reads the list again right after.
 */
export async function removeLesson(index: number, lesson: MemoryLesson): Promise<void> {
  await api.delete(`/memory/history/lesson/${index}`, { data: { expect: lesson } })
}

/** Everything the assistant remembers about the person: profile, sectors, triggers, lessons. */
export async function resetMemory(): Promise<void> {
  await api.delete('/memory/reset')
}
