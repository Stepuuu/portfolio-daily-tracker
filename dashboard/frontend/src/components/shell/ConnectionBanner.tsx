// Says so when the figures on screen stop updating: the last `/api/today` answer is older than twice the
// poll interval, or the request failed (rule in features/today/connection.ts). Not a live region with
// role=alert: it stays up across pages and must not compete with a page's own alerts.
import { useQueryClient } from '@tanstack/react-query'
import { WifiOff } from 'lucide-react'
import { Button, cx, numeric } from '@/components/ui'
import { fmtRelative, fmtTime } from '@/lib/format'
import { connectionState } from '@/features/today/connection'
import { DISPLAY_ZONE, pollIntervalMs } from '@/features/today/session'
import { TODAY_KEY } from '@/services/today'
import { getApiErrorMessage } from '@/services/api'
import { useNow } from './useNow'
import { snapshotReceivedAt, useTodayFeed } from './useTodayFeed'

export function ConnectionBanner() {
  const query = useTodayFeed()
  const client = useQueryClient()
  const now = useNow(1000)
  // The age of the snapshot on screen, from when it arrived; not the query's last update.
  const receivedAt = snapshotReceivedAt(query.data)
  const state = connectionState({
    receivedAt,
    intervalMs: pollIntervalMs(query.data),
    failed: query.isError,
    now,
  })
  const visible = state !== 'ok'
  return (
    <div aria-live="polite" className="shrink-0">
      {visible && (
        <div className={cx('flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1 border-b border-warn/30 bg-warn/10 px-4 py-1 text-table text-warn md:px-6', numeric)}>
          <WifiOff size={14} aria-hidden className="shrink-0" />
          <span className="font-medium">{state === 'failed' ? '连接中断' : '数据未更新'}</span>
          <span className="text-fg-2">
            {query.data
              ? `显示的是 ${fmtTime(query.data.as_of, { timeZone: DISPLAY_ZONE })} 的数据（${receivedAt === null ? '接收时间不可用' : fmtRelative(receivedAt, now)}）`
              : '还没有取得今日数据'}
            {state === 'failed' && query.error ? ` · ${getApiErrorMessage(query.error)}` : ''}
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto text-warn hover:text-warn"
            loading={query.isFetching}
            onClick={() => void client.refetchQueries({ queryKey: TODAY_KEY })}
          >
            重试
          </Button>
        </div>
      )}
    </div>
  )
}
