import { Dialog } from '@/components/ui'
import { useShell } from './shellStore'
import { useAppMode } from './useAppMode'

/** The app's keys (also listed in 设置 › 关于与帮助). */
export const SHORTCUTS: [string, string][] = [
  ['N', '记一笔'],
  ['⌘K 或 /', '搜索代码 / 名称'],
  ['?', '快捷键'],
  ['Esc', '关闭对话框、抽屉或助手'],
]

export function ShortcutsDialog() {
  const open = useShell((state) => state.shortcutsOpen)
  const setOpen = useShell((state) => state.setShortcutsOpen)
  const { lab, readOnly } = useAppMode()
  // 记一笔 does not exist in research-only mode and is read-only in the demo (or while the mode is unknown).
  const shown = SHORTCUTS.filter(([keys]) => keys !== 'N' || (!lab && !readOnly))
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="快捷键" size="sm">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-body">
        {shown.map(([keys, action]) => (
          <div key={keys} className="contents">
            <dt><kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-sans text-table text-fg">{keys}</kbd></dt>
            <dd className="text-fg-2">{action}</dd>
          </div>
        ))}
      </dl>
    </Dialog>
  )
}
