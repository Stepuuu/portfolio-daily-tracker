// 上证 3,312.45 +0.52%   深成 10,812.30 -0.12%   恒生 19,880.00 +0.30%   纳指 收盘 …   东财 10:32:05
// The main indexes in one line of text (no cards), polled like Today. A missing price says 不可用.
import { useQuery } from '@tanstack/react-query'
import { cx, focusRing, numeric } from '@/components/ui'
import { UNAVAILABLE, fmtDate, fmtNumber, fmtPct, fmtTime, toneOf } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { INDEX_STRIP, stripItems } from '@/features/market/indices'
import { watchPollMs } from '@/features/market/watchlist'
import { DISPLAY_ZONE, sourceLabel } from '@/features/today/session'
import { QUOTES_KEY, fetchQuotes } from '@/services/market'
import { errorText, useSession } from './hooks'
import { openStockDrawer } from './stockDrawerStore'

const TICKERS = INDEX_STRIP.map((item) => item.ticker)

export function IndexStrip() {
  const session = useSession()
  const query = useQuery({
    queryKey: [...QUOTES_KEY, 'indices'],
    queryFn: ({ signal }) => fetchQuotes(TICKERS, signal),
    refetchInterval: () => watchPollMs(TICKERS, session),
    retry: 1,
  })
  const data = query.data
  const today = data ? fmtDate(data.as_of, { timeZone: DISPLAY_ZONE }) : null
  if (query.isError && !data) {
    return <p className="text-table text-fg-3">指数行情暂时无法读取：{errorText(query.error)}</p>
  }
  const items = stripItems(data?.quotes, today)
  return (
    <div className={cx('flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 text-table', numeric)}>
      <ul aria-label="主要指数" className="flex min-w-0 flex-wrap items-baseline gap-x-5 gap-y-1">
        {items.map((item) => (
          <li key={item.ticker} className="inline-flex items-baseline">
            <button
              type="button"
              onClick={() => openStockDrawer(item.ticker, item.label)}
              // This index's own source and exchange time, then the reason it is not live.
              title={[item.label, item.meta, item.title].filter(Boolean).join(' · ')}
              className={cx('inline-flex items-baseline gap-x-1.5 rounded-sm hover:underline', focusRing)}
            >
              <span className="text-fg-2">{item.label}</span>
              {!data ? (
                <span className="text-fg-3">…</span>
              ) : item.price === null ? (
                <span className="text-fg-3">{UNAVAILABLE}</span>
              ) : (
                <>
                  <span className="text-fg">{fmtNumber(item.price, { digits: 2, exact: true })}</span>
                  <span className={item.changePct === null ? 'text-fg-3' : pnlTextClass(toneOf(item.changePct))}>
                    {fmtPct(item.changePct, { signed: true, exact: true })}
                  </span>
                </>
              )}
              {item.state && item.price !== null && <span className={cx('text-caption', item.warn ? 'text-warn' : 'text-fg-3')}>{item.state}</span>}
            </button>
          </li>
        ))}
      </ul>
      {data && (
        <p className="shrink-0 text-caption text-fg-3" title="指数行情的来源和取得时间（北京时间）">
          {sourceLabel(data.source)} {fmtTime(data.as_of, { timeZone: DISPLAY_ZONE })}
        </p>
      )}
    </div>
  )
}
