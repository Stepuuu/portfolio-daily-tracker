// Shell-wide UI state: the 记一笔 dialog, the assistant dock and the 待确认 tray. Any page opens them
// through these actions (e.g. a holdings row's 买 / 问助手), so they live outside the React tree.
import { create } from 'zustand'
import type { EntryDraft } from '@/components/ledger/EntryDialog'
import { parseTicker } from '@/features/ledger/entry'

/**
 * What 问助手 puts in the assistant's input for an instrument: "关于 SHA:600519：", with the exchange code
 * only, checked against the ledger's ticker rule. Never a name: names come from imports, the ledger or a
 * quote provider, and typed into the input they would reach the model as the person's own words.
 * Undefined when `ticker` is not a valid code (the assistant then opens empty).
 * Use: `openAssistant(assistantPrefillFor(ticker))`.
 */
export function assistantPrefillFor(ticker: string | null | undefined): string | undefined {
  const code = parseTicker(ticker)
  return code ? `关于 ${code}：` : undefined
}

interface ShellState {
  entry: EntryDraft | null
  assistantOpen: boolean
  /** Text to put in the assistant's input when it opens; `prefillId` changes on every request. */
  assistantPrefill: string | null
  prefillId: number
  trayOpen: boolean
  moreOpen: boolean
  shortcutsOpen: boolean
  openEntry: (draft?: EntryDraft) => void
  closeEntry: () => void
  openAssistant: (prefill?: string) => void
  closeAssistant: () => void
  toggleAssistant: () => void
  setTrayOpen: (open: boolean) => void
  setMoreOpen: (open: boolean) => void
  setShortcutsOpen: (open: boolean) => void
}

export const useShell = create<ShellState>((set) => ({
  entry: null,
  assistantOpen: false,
  assistantPrefill: null,
  prefillId: 0,
  trayOpen: false,
  moreOpen: false,
  shortcutsOpen: false,
  openEntry: (draft = {}) => set({ entry: draft, trayOpen: false, moreOpen: false }),
  closeEntry: () => set({ entry: null }),
  openAssistant: (prefill) =>
    set((state) => ({ assistantOpen: true, assistantPrefill: prefill ?? null, prefillId: state.prefillId + 1, moreOpen: false })),
  closeAssistant: () => set({ assistantOpen: false }),
  toggleAssistant: () => set((state) => ({ assistantOpen: !state.assistantOpen, assistantPrefill: null })),
  setTrayOpen: (open) => set({ trayOpen: open }),
  setMoreOpen: (open) => set({ moreOpen: open }),
  setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
}))
