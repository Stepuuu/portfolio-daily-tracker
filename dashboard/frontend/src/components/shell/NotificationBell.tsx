// The bell: unread count, a popover list (newest first, older pages on request), mark read. Polls with
// `after=<cursor>` so nothing is skipped (backend/api/notifications.py; rules in features/notifications):
// every 15 s while the tab is visible, not at all while it is hidden (also not when it opens hidden; one
// poll on return), backing off to 5 min after failures, keeping the newest 200 (and, while the panel is
// open, everything loaded with 加载更早的通知). Marking read is shown at once; each row then follows what
// the server confirmed: a refused read is undone (the row stays clickable to retry) unless another read
// the server accepted covers it, and after overlapping reads the count is read again from the server.
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Bell, Info } from 'lucide-react'
import { Button, Popover, cx, focusRing, numeric } from '@/components/ui'
import { fmtRelative } from '@/lib/format'
import { getApiErrorMessage } from '@/services/api'
import { fetchInbox, markNotificationRead, markNotificationsRead } from '@/services/notifications'
import {
  FIRST_PAGE, POLL_PAGE, addNewer, addOlder, badgeCount, confirmRead, firstPage, markAllRead, markRead, nextPollDelay,
  oldestSeq, releaseOlder, rollbackRead, type Inbox, type Notification, type ReadOperation,
} from '@/features/notifications/inbox'
import { useAppMode } from './useAppMode'

function useInbox(enabled: boolean) {
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [markError, setMarkError] = useState<string | null>(null)
  const current = useRef<Inbox | null>(null)
  // Asks the poller for an immediate poll (after overlapping reads, for the server's count).
  const pollNow = useRef<() => void>(() => {})
  const reads = useRef({ inFlight: 0, overlapped: false })

  /** Applies `change` to the latest inbox, in the ref (read by async work) and in state. */
  const update = useCallback((change: (inbox: Inbox) => Inbox) => {
    if (!current.current) return
    current.current = change(current.current)
    setInbox(current.current)
  }, [])

  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let running = false
    let again = false
    let failures = 0
    let timer: number | undefined
    const hidden = () => document.visibilityState === 'hidden'
    const schedule = () => {
      window.clearTimeout(timer)
      if (!stopped && !hidden()) timer = window.setTimeout(() => void poll(), nextPollDelay(failures))
    }
    const poll = async () => {
      if (stopped) return
      if (running) {
        again = true
        return
      }
      running = true
      try {
        if (!current.current) {
          const page = await fetchInbox({ limit: FIRST_PAGE })
          if (stopped) return
          current.current = firstPage(page)
          setInbox(current.current)
        } else {
          // A full page means more are waiting: ask again at once, from the last item received.
          for (let more = true; more && !stopped;) {
            const page = await fetchInbox({ after: current.current!.cursor, limit: POLL_PAGE })
            if (stopped) return
            const result = addNewer(current.current!, page)
            more = result.more
            current.current = result.inbox
            setInbox(current.current)
          }
        }
        failures = 0
        setError(null)
      } catch (exc) {
        failures += 1
        if (!stopped) setError(getApiErrorMessage(exc))
      } finally {
        running = false
      }
      if (again && !stopped) {
        again = false
        void poll()
        return
      }
      schedule()
    }
    pollNow.current = () => {
      if (!hidden()) void poll()
    }
    // A hidden tab does not poll, also not when it opens hidden; coming back polls at once.
    const onVisibility = () => {
      window.clearTimeout(timer)
      if (!hidden()) void poll()
    }
    document.addEventListener('visibilitychange', onVisibility)
    if (!hidden()) void poll()
    return () => {
      stopped = true
      pollNow.current = () => {}
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [enabled])

  const loadOlder = useCallback(async () => {
    const before = current.current && oldestSeq(current.current)
    if (!before) return
    try {
      const page = await fetchInbox({ before, limit: FIRST_PAGE })
      update((inbox) => addOlder(inbox, page))
    } catch (exc) {
      setError(getApiErrorMessage(exc))
    }
  }, [update])

  /** The panel closed: rows loaded on request go, the list is bounded again. */
  const release = useCallback(() => update(releaseOlder), [update])

  /**
   * Shows the rows read at once, then follows the server: confirmed, they stay read (with its count);
   * refused, the rows this read marked are unread again unless another confirmed read covers them.
   */
  const optimistic = useCallback(async (operation: ReadOperation, send: () => Promise<{ unread: number }>) => {
    const before = current.current
    if (!before) return
    const at = new Date().toISOString()
    const mark = (inbox: Inbox) => ('seq' in operation ? markRead(inbox, operation.seq, undefined, at) : markAllRead(inbox, operation.upTo, undefined, at))
    const seqs = before.items.filter((item) => !item.read_at && ('seq' in operation ? item.seq === operation.seq : item.seq <= operation.upTo)).map((item) => item.seq)
    update(mark)
    const marked = current.current!
    setMarkError(null)
    reads.current.inFlight += 1
    if (reads.current.inFlight > 1) reads.current.overlapped = true
    try {
      const result = await send()
      update((inbox) => confirmRead(inbox, operation, result.unread))
    } catch (exc) {
      update((inbox) => rollbackRead(inbox, seqs, at, marked))
      setMarkError(`标记已读失败：${getApiErrorMessage(exc)}。请再点一次。`)
    } finally {
      reads.current.inFlight -= 1
      // Answers to overlapping reads may arrive in another order than the server applied them: the count
      // of the last one to arrive is not necessarily the latest. Ask the server once they are all done.
      if (reads.current.inFlight === 0 && reads.current.overlapped) {
        reads.current.overlapped = false
        pollNow.current()
      }
    }
  }, [update])

  const read = useCallback((seq: number) => optimistic({ seq }, () => markNotificationRead(seq)), [optimistic])

  const readAll = useCallback(() => {
    const upTo = current.current?.items[0]?.seq
    if (!upTo) return Promise.resolve()
    return optimistic({ upTo }, () => markNotificationsRead(upTo))
  }, [optimistic])

  return { inbox, error, markError, loadOlder, release, read, readAll }
}

function Item({ item, onRead, readOnly }: { item: Notification; onRead: (seq: number) => void; readOnly: boolean }) {
  const unread = !item.read_at
  const urgent = item.level === 'warning' || item.level === 'critical'
  const Icon = urgent ? AlertTriangle : Info
  return (
    <li>
      <button
        type="button"
        onClick={() => unread && !readOnly && onRead(item.seq)}
        className={cx('flex w-full gap-3 px-4 py-3 text-left transition-colors duration-[120ms] hover:bg-surface-2', focusRing, 'focus-visible:-outline-offset-2')}
      >
        <Icon size={16} aria-hidden className={cx('mt-0.5 shrink-0', urgent ? 'text-warn' : 'text-fg-3')} />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-3">
            <span className={cx('min-w-0 truncate text-body', unread ? 'font-medium text-fg' : 'text-fg-2')}>{item.title}</span>
            <span className={cx('shrink-0 text-caption text-fg-3', numeric)}>{fmtRelative(item.created_at)}</span>
          </span>
          {item.body && <span className="mt-0.5 line-clamp-2 block text-table text-fg-2">{item.body}</span>}
        </span>
        {unread && (
          <>
            <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            <span className="sr-only">未读</span>
          </>
        )}
      </button>
    </li>
  )
}

export function NotificationBell() {
  const { mode, readOnly, readOnlyReason } = useAppMode()
  const { inbox, error, markError, loadOlder, release, read, readAll } = useInbox(mode !== undefined && mode !== 'lab')
  const count = badgeCount(inbox?.unread ?? 0)
  const label = count ? `通知，${count} 条未读` : '通知'
  return (
    <Popover
      align="end"
      label="通知列表"
      className="w-[360px] p-0"
      onOpenChange={(open) => {
        if (!open) release()
      }}
      trigger={(
        <button
          type="button"
          aria-label={label}
          title="通知"
          className={cx(
            'relative inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-2 transition-colors duration-[120ms] hover:bg-fg/[.06] hover:text-fg coarse:h-10 coarse:w-10',
            focusRing,
          )}
        >
          <Bell size={16} aria-hidden />
          {count && (
            <span className={cx('absolute -right-0.5 -top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-accent px-1 text-caption font-medium leading-none text-accent-fg', numeric)}>
              {count}
            </span>
          )}
        </button>
      )}
    >
      <div className="flex flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
          <h2 className="text-title text-fg">通知</h2>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void readAll()}
            disabled={!inbox?.unread}
            disabledReason={readOnlyReason}
            tipAlign="end"
          >
            全部已读
          </Button>
        </div>
        {markError && <p role="alert" className="border-b border-line px-4 py-2 text-table text-warn">{markError}</p>}
        {error && <p className="border-b border-line px-4 py-2 text-table text-warn">暂时无法读取通知：{error}</p>}
        {inbox && inbox.items.length === 0 ? (
          <p className="px-4 py-8 text-center text-table text-fg-3">暂无通知。提醒触发、快照完成或失败时会出现在这里。</p>
        ) : (
          <ul className="max-h-[min(420px,60vh)] divide-y divide-line overflow-y-auto">
            {inbox?.items.map((item) => <Item key={item.seq} item={item} onRead={read} readOnly={readOnly} />)}
          </ul>
        )}
        {inbox?.hasOlder && (
          <div className="border-t border-line p-2">
            <Button size="sm" variant="ghost" className="w-full" onClick={() => void loadOlder()}>加载更早的通知</Button>
          </div>
        )}
      </div>
    </Popover>
  )
}
