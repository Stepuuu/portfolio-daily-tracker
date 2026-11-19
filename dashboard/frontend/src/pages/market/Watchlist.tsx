// 行情 › 自选 (/market): the main indexes in one line, then the watchlist with live quotes. Add by search
// (type 茅台 or 600519, pick or Enter on a row on screen: leaving the field never adds anything), notes
// edited in place, reorder with ↑ ↓, remove with 撤销 (back where it was on screen). Polls like Today:
// every 5 s while a watched market trades, every 60 s otherwise; the quote age is the age of the last
// answer, not of a local edit. Data: GET /api/watchlist, GET /api/quotes (indexes); writes are refused in
// the demo, with the reason.
import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Star } from 'lucide-react'
import { Badge, Button, EmptyState, InlineAlert, Panel, cx, numeric, toast } from '@/components/ui'
import { fmtDate, fmtRelative, fmtTime } from '@/lib/format'
import { connectionState } from '@/features/today/connection'
import { DISPLAY_ZONE, sourceLabel } from '@/features/today/session'
import { displayName, moveItem, restoreItem, ruleCounts, watchPollMs, type WatchlistItem, type WatchlistPayload } from '@/features/market/watchlist'
import { TickerInput } from '@/components/inputs/TickerInput'
import { clearInstrumentCache } from '@/components/inputs/useInstrumentSearch'
import { useAppMode } from '@/components/shell/useAppMode'
import { useNow } from '@/components/shell/useNow'
import { IndexStrip } from '@/components/market/IndexStrip'
import { WatchlistTable } from '@/components/market/WatchlistTable'
import { errorText, useAlertRules, useSession, useWatchlist } from '@/components/market/hooks'
import { addToWatchlist, removeFromWatchlist, updateWatchlistItem, WATCHLIST_KEY } from '@/services/watchlist'
import type { InstrumentResult } from '@/lib/instrument'

const QUOTES_KEY = [...WATCHLIST_KEY, 'quotes']

function Freshness({ payload, receivedAt, failed }: { payload: WatchlistPayload | undefined; receivedAt: number; failed: boolean }) {
  const now = useNow(1000)
  const session = useSession()
  if (!payload?.as_of) return null
  const state = connectionState({
    receivedAt: receivedAt || null, intervalMs: watchPollMs(payload.items.map((item) => item.ticker), session), failed, now,
  })
  const time = fmtTime(payload.as_of, { timeZone: DISPLAY_ZONE })
  if (state !== 'ok') {
    return <Badge tone="warn" title={`显示的是 ${time} 的行情`}>{state === 'failed' ? '连接中断' : '行情未更新'} · {time}</Badge>
  }
  return <span className={numeric}>{sourceLabel(payload.source)} {fmtRelative(receivedAt || payload.as_of, now)}</span>
}

export default function Watchlist() {
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const watch = useWatchlist(true)
  const rules = useAlertRules(false)
  const [adding, setAdding] = useState('')
  const addInput = useRef<HTMLInputElement>(null)
  const readOnly = readOnlyReason
  const data = watch.data
  const items = data?.items ?? []
  const today = data?.as_of ? fmtDate(data.as_of, { timeZone: DISPLAY_ZONE }) : null
  // Unknown while the rules have not been read (or could not be): 不可用, never 0.
  const counts = rules.data ? ruleCounts(rules.data.items) : null

  const refresh = () => {
    clearInstrumentCache()
    return client.invalidateQueries({ queryKey: WATCHLIST_KEY })
  }
  /** A local change of the list; it keeps the time of the last answer, so the quote age stays true. */
  const setItems = (next: (items: WatchlistItem[]) => WatchlistItem[]) => {
    const received = client.getQueryState(QUOTES_KEY)?.dataUpdatedAt
    client.setQueryData<WatchlistPayload>(
      QUOTES_KEY,
      (old) => {
        if (!old) return old
        const items = next(old.items)
        return { ...old, items, count: items.length }
      },
      received ? { updatedAt: received } : undefined,
    )
  }

  const add = useMutation({
    mutationFn: (choice: InstrumentResult) => addToWatchlist(choice.ticker!),
    onSuccess: (item, choice) => {
      toast.success(`已加入自选：${choice.name ?? item.name ?? item.ticker}`)
      setAdding('')
      void refresh()
    },
    onError: (exc) => toast.warn(`没有加入：${errorText(exc)}`),
  })

  const move = useMutation({
    mutationFn: ({ ticker, order }: { ticker: string; order: number }) => updateWatchlistItem(ticker, { order }),
    onError: (exc) => {
      toast.warn(`顺序没有保存：${errorText(exc)}`)
      void refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (item: WatchlistItem) => removeFromWatchlist(item.ticker),
    // Where it was on screen (after any move not yet polled back): that is where undo puts it.
    onMutate: (item) => {
      const position = Math.max(0, items.findIndex((entry) => entry.ticker === item.ticker))
      setItems((list) => list.filter((entry) => entry.ticker !== item.ticker))
      return { position }
    },
    onSuccess: (_result, item, context) => {
      clearInstrumentCache()
      const position = context?.position ?? item.order
      toast.info(`已移出自选：${displayName(item)}`, {
        action: {
          label: '撤销',
          onClick: () => {
            setItems((list) => restoreItem(list, { ...item, order: position }, position))
            void addToWatchlist(item.ticker, item.note)
              .then(() => updateWatchlistItem(item.ticker, { order: position }))
              .catch((exc) => toast.warn(`撤销失败：${errorText(exc)}`))
              .finally(() => void refresh())
          },
        },
      })
    },
    onError: (exc) => {
      toast.warn(`没有移出：${errorText(exc)}`)
      void refresh()
    },
  })

  const onMove = (ticker: string, delta: -1 | 1) => {
    const result = moveItem(items, ticker, delta)
    if (!result) return
    setItems(() => result.items)
    move.mutate({ ticker, order: result.order })
  }

  const onNote = async (ticker: string, note: string | null) => {
    try {
      const item = await updateWatchlistItem(ticker, { note })
      setItems((list) => list.map((entry) => (entry.ticker === ticker ? { ...entry, note: item.note } : entry)))
      toast.success(note ? '备注已保存' : '备注已清除')
    } catch (exc) {
      toast.warn(`备注没有保存：${errorText(exc)}`)
      throw exc
    }
  }

  const addField = readOnly ? (
    <Button size="sm" disabledReason={readOnly} tipAlign="end">添加自选</Button>
  ) : (
    <TickerInput
      ref={addInput}
      value={adding}
      onChange={setAdding}
      // Adding is a write: only a pick or Enter on a row on screen adds, never leaving the field.
      resolveOnBlur={false}
      onSelect={(choice) => {
        if (choice.in_watchlist) {
          toast.info(`${choice.name ?? choice.ticker} 已在自选中`)
          setAdding('')
          return
        }
        add.mutate(choice)
      }}
      placeholder="添加自选：代码或名称"
      aria-label="添加自选"
      size="sm"
      disabled={add.isPending}
      className="w-full sm:w-64"
    />
  )

  return (
    <div className="mx-auto flex max-w-[1600px] flex-col gap-6 text-body text-fg">
      <h1 className="sr-only">自选</h1>
      <IndexStrip />

      {watch.isError && !data && (
        <InlineAlert tone="warn" title="无法读取自选" action={<Button size="sm" onClick={() => void watch.refetch()}>重试</Button>}>
          {errorText(watch.error)}
        </InlineAlert>
      )}

      <Panel
        padding="none"
        title="自选"
        description={data ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={numeric}>{data.count} 只</span>
            {data.count > 0 && <Freshness payload={data} receivedAt={watch.dataUpdatedAt} failed={watch.isError} />}
          </span>
        ) : undefined}
        actions={addField}
        className={cx(watch.isError && !data && 'hidden')}
      >
        <WatchlistTable
          items={items}
          loading={watch.isPending}
          today={today}
          ruleCounts={counts}
          rulesState={{ loading: rules.isPending, error: rules.isError ? errorText(rules.error) : null }}
          readOnly={readOnly}
          onMove={onMove}
          onRemove={(item) => remove.mutate(item)}
          onNote={onNote}
          empty={(
            <EmptyState
              icon={Star}
              title="还没有自选"
              description="关注但还没买的标的放在这里，行情会自动刷新。"
              action={readOnly ? undefined : <Button variant="primary" onClick={() => addInput.current?.focus()}>添加第一只</Button>}
            />
          )}
        />
      </Panel>
    </div>
  )
}
