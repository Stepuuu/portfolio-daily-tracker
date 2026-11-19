import { Button } from '@/components/ui'
import { useAppMode } from './useAppMode'

/**
 * A thin line under the header: in demo mode the data is invented and nothing can be saved; while the
 * backend's mode cannot be read, a neutral notice that writing is paused (it may be a read-only demo).
 */
export function DemoBanner() {
  const { demo, unknown, retry } = useAppMode()
  if (unknown) {
    return (
      <p role="status" className="flex min-h-7 shrink-0 flex-wrap items-center justify-center gap-x-2 border-b border-line bg-surface-2 px-4 text-caption text-fg-2">
        <span className="font-medium text-fg">无法确认运行模式</span>
        <span aria-hidden className="text-fg-3">·</span>
        修改操作已暂停，可以继续查看
        <Button size="sm" variant="ghost" className="h-6 coarse:h-8" onClick={retry}>重试</Button>
      </p>
    )
  }
  if (!demo) return null
  return (
    <p className="flex h-7 shrink-0 items-center justify-center gap-2 border-b border-line bg-surface-2 px-4 text-caption text-fg-2">
      <span className="font-medium text-fg">演示模式</span>
      <span aria-hidden className="text-fg-3">·</span>
      数据为虚构，只读
    </p>
  )
}
