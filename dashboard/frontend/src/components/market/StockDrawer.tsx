// The stock drawer: one instrument at a glance, opened from the watchlist, the global search and Today
// (`openStockDrawer`). Quote with source, time and state; the daily chart; the position when held
// (from /api/today, with the backend's digits, each holding's quote source and time and its 估算 mark);
// sections other packages add (DRAWER_EXTRAS: the plan); this listing's alert rules; and the actions
// 记一笔 · 设提醒 · 问助手 · 加入 / 移出自选. Holdings, rules and the watchlist entry are matched by the
// backend's listing identity. Read-only in the demo or an unknown mode, with the reason.
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BellPlus, FlaskConical, MessageSquare, Plus, Star, StarOff } from 'lucide-react'
import { Badge, Button, Drawer, LongValue, cx, focusRing, numeric, toast } from '@/components/ui'
import { UNAVAILABLE, fmtDate, fmtPct, toneOf } from '@/lib/format'
import { exchangeLabel, identityKey, identityOf } from '@/lib/instrument'
import { pnlTextClass } from '@/lib/pnlColor'
import { changeAmount, hasPrice } from '@/features/market/watchlist'
import { currencyOf, fmtTickerPrice, tickerPriceDigits } from '@/features/market/format'
import { positionView } from '@/features/market/drawer'
import { conditionText, distanceView, kindLabel, ruleState, sourceInfo } from '@/features/alerts/rules'
import { DISPLAY_ZONE } from '@/features/today/session'
import { watchLink } from '@/features/backtest/model'
import type { Quality, TodayRow } from '@/features/today/types'
import { money, signed } from '@/components/today/cells'
import { assistantPrefillFor, useShell } from '@/components/shell/shellStore'
import { useAppMode } from '@/components/shell/useAppMode'
import { useTodayFeed } from '@/components/shell/useTodayFeed'
import { openNewAlert, openEditAlert } from '@/components/alerts/alertDialogStore'
import { addToWatchlist, removeFromWatchlist, updateWatchlistItem, WATCHLIST_KEY } from '@/services/watchlist'
import { clearInstrumentCache } from '@/components/inputs/useInstrumentSearch'
import { DrawerSection as Section, renderDrawerExtras } from './drawerExtras'
import { errorText, nameOf, useAlertRules, useNames, useQuote, useWatchlist } from './hooks'
import { MiniChart } from './MiniChart'
import { QuoteMeta } from './QuoteMeta'
import { useStockDrawer } from './stockDrawerStore'


function signedClass(value: string | null | undefined) {
  return value === null || value === undefined ? 'text-fg-3' : pnlTextClass(toneOf(value))
}

function PositionRows({
  rows, ticker, today, quality, qualityReason,
}: { rows: { account: string; row: TodayRow }[]; ticker: string; today: string | null; quality: Quality | undefined; qualityReason: string | null }) {
  return (
    <ul className="flex flex-col divide-y divide-line">
      {rows.map(({ account, row }) => {
        const currency = row.currency
        const view = positionView({ ...row, ticker: row.ticker || ticker }, quality, qualityReason)
        return (
          <li key={account} className="py-2 first:pt-0 last:pb-0">
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-2">
              <span>{account}</span>
              {/* The holding's own quote: source, time, state, and 估算 when the row is estimated. */}
              <QuoteMeta ticker={row.ticker} quote={row} today={today} showTicker={false} />
            </p>
            <dl className={cx('mt-1 grid grid-cols-3 gap-x-3 gap-y-1 text-table', numeric)}>
              <div><dt className="text-caption text-fg-3">持仓</dt><dd>{view.quantity}</dd></div>
              <div className="min-w-0"><dt className="text-caption text-fg-3">成本</dt><dd className="break-all"><LongValue value={view.cost} /></dd></div>
              <div className="text-right"><dt className="text-caption text-fg-3">市值</dt><dd>{money(row.market_value, currency)}</dd></div>
              <div><dt className="text-caption text-fg-3">持仓盈亏</dt><dd>{signed(money(row.total_pnl, currency, true), row.total_pnl)}</dd></div>
              <div><dt className="text-caption text-fg-3">收益率</dt><dd>{signed(fmtPct(row.total_pnl_pct, { signed: true, exact: true }), row.total_pnl_pct)}</dd></div>
              <div className="text-right">
                <dt className="text-caption text-fg-3">占比</dt>
                <dd className="flex flex-wrap items-center justify-end gap-1">
                  <span className={row.weight_pct === null ? 'text-fg-3' : undefined}>{view.weight}</span>
                  {view.weightEstimated && <Badge tone="warn" title={view.weightTitle}>估算</Badge>}
                </dd>
              </div>
            </dl>
          </li>
        )
      })}
    </ul>
  )
}

function DrawerBody({ ticker, name, close }: { ticker: string; name: string | null; close: () => void }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { lab, readOnlyReason } = useAppMode()
  const openEntry = useShell((state) => state.openEntry)
  const openAssistant = useShell((state) => state.openAssistant)
  const quote = useQuote(ticker)
  const today = useTodayFeed().data
  const watch = useWatchlist(false)
  const rules = useAlertRules(true)
  // The drawer's own ticker has no payload of its own: the backend's rule applied here (lib/instrument.ts).
  const key = identityKey(ticker)
  const index = ticker.toUpperCase().startsWith('IDX:')
  const object = quote.data?.quote ?? null
  const currency = object?.currency ?? currencyOf(ticker)
  const date = today ? fmtDate(today.as_of, { timeZone: DISPLAY_ZONE }) : null
  const held = (today?.accounts ?? []).flatMap((account) => account.positions
    .filter((row) => identityOf(row) === key)
    .map((row) => ({ account: account.name, row })))
  const watched = watch.data?.items.find((item) => identityOf(item) === key) ?? null
  const ownRules = (rules.data?.items ?? []).filter((rule) => identityOf(rule) === key)
  const estimate = today?.totals.estimated?.[0]
  const qualityReason = estimate ? `${estimate.ticker ?? (estimate.currency ? `汇率 ${estimate.currency}` : '部分持仓')}：${estimate.reason}` : null
  const label = name || object?.name || held[0]?.row.name || watched?.name || ticker
  const reason = readOnlyReason
  const change = changeAmount(object)

  const toggleWatch = useMutation({
    mutationFn: async () => {
      if (watched) return { removed: (await removeFromWatchlist(watched.ticker)).item }
      return { added: await addToWatchlist(ticker) }
    },
    onSuccess: (result) => {
      clearInstrumentCache()
      void client.invalidateQueries({ queryKey: WATCHLIST_KEY })
      if ('removed' in result && result.removed) {
        const removed = result.removed
        toast.info(`已移出自选：${label}`, {
          action: {
            label: '撤销',
            onClick: () => {
              void addToWatchlist(removed.ticker, removed.note)
                .then(() => updateWatchlistItem(removed.ticker, { order: removed.order }))
                .then(() => client.invalidateQueries({ queryKey: WATCHLIST_KEY }), (exc) => toast.warn(`撤销失败：${errorText(exc)}`))
            },
          },
        })
      } else {
        toast.success(`已加入自选：${label}`)
      }
    },
    onError: (exc) => toast.warn(errorText(exc)),
  })

  return (
    <div className="flex flex-col gap-4">
      <section aria-label="行情" className="flex flex-col gap-1">
        {quote.isPending ? (
          <p className="text-body text-fg-3">正在读取行情…</p>
        ) : hasPrice(object) ? (
          <>
            <p className={cx('flex flex-wrap items-baseline gap-x-3', numeric)}>
              <span className="text-figure text-fg">{fmtTickerPrice(object.price, ticker, currency, { symbol: true, exact: true })}</span>
              <span className={cx('text-body', signedClass(object.change_pct))}>{fmtPct(object.change_pct, { signed: true, exact: true })}</span>
              <span className={cx('text-body', signedClass(change))} title={change === null ? '缺少昨收价，涨跌额不可用' : undefined}>
                {change === null ? UNAVAILABLE : fmtTickerPrice(change, ticker, currency, { signed: true, exact: true })}
              </span>
            </p>
            <QuoteMeta ticker={ticker} quote={object} today={date} showTicker={false} extra={object.prev_close !== null && (
              <span className={numeric}>· 昨收 {fmtTickerPrice(object.prev_close, ticker, currency, { exact: true })}</span>
            )} />
          </>
        ) : (
          <>
            <p className="text-page font-normal text-fg-3">{UNAVAILABLE}</p>
            <QuoteMeta ticker={ticker} quote={object} today={date} showTicker={false} extra={quote.isError ? <span>{errorText(quote.error)}</span> : null} />
          </>
        )}
      </section>

      <MiniChart ticker={ticker} digits={tickerPriceDigits(ticker, currency)} />

      {held.length > 0 && (
        <Section title="持仓">
          <PositionRows rows={held} ticker={ticker} today={date} quality={today?.totals.quality} qualityReason={qualityReason} />
        </Section>
      )}

      {renderDrawerExtras({ ticker, name: label, readOnly: Boolean(reason), close })}

      {!lab && (
        <Section
          title="提醒"
          action={(
            <Link to="/alerts" onClick={close} className={cx('rounded-sm text-table text-accent hover:underline', focusRing)}>全部提醒</Link>
          )}
        >
          {rules.isError ? (
            <p className="text-table text-fg-3">提醒规则暂时无法读取：{errorText(rules.error)}</p>
          ) : ownRules.length === 0 ? (
            <p className="text-table text-fg-3">{rules.isPending ? '正在读取…' : '还没有提醒。设一个止损或目标价，到价时会通知你。'}</p>
          ) : (
            <ul className="flex flex-col divide-y divide-line">
              {ownRules.map((rule) => {
                const source = sourceInfo(rule.source)
                const distance = distanceView(rule)
                const state = ruleState(rule, false, date)
                return (
                  <li key={rule.id} className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
                    <span className="min-w-0">
                      <span className={cx('flex flex-wrap items-baseline gap-x-2 text-body', !rule.enabled && 'text-fg-3')}>
                        <span className="font-medium">{kindLabel(rule.kind)}</span>
                        <span className={numeric}>{conditionText(rule)}</span>
                        {distance.gap && <span className={cx('text-table', distance.tone === 'warn' ? 'text-warn' : 'text-fg-3', numeric)}>{distance.gap}</span>}
                      </span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-1">
                        <Badge>{source.label}</Badge>
                        {state.label !== '启用' && <Badge tone={state.tone === 'warn' ? 'warn' : 'neutral'} title={state.detail ?? undefined}>{state.label}</Badge>}
                      </span>
                      {state.tone === 'warn' && state.detail && <span className="mt-0.5 block text-caption text-warn">{state.detail}</span>}
                    </span>
                    {!source.readOnly && (
                      <Button size="sm" variant="ghost" disabledReason={reason} tipAlign="end" onClick={() => openEditAlert(rule, label)}>编辑</Button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
          {ownRules.some((rule) => sourceInfo(rule.source).readOnly) && (
            <p className="text-caption text-fg-3">来自交易计划的规则在计划中修改。</p>
          )}
        </Section>
      )}

      {watched?.note && (
        <Section title="自选备注">
          <p className="whitespace-pre-wrap text-body text-fg">{watched.note}</p>
        </Section>
      )}

      {!lab && (
        <div className="flex flex-wrap gap-2 border-t border-line pt-4">
          {!index && (
            <Button
              variant="primary"
              icon={Plus}
              disabledReason={reason}
              tipAlign="start"
              onClick={() => openEntry({ ticker, name: label, currency, account: held.length === 1 ? held[0].account : undefined })}
            >
              记一笔
            </Button>
          )}
          <Button icon={BellPlus} disabledReason={reason} onClick={() => openNewAlert({ ticker, name: label })}>设提醒</Button>
          {!index && <Button variant="ghost" icon={FlaskConical} onClick={() => { close(); navigate(watchLink(ticker)) }}>回测</Button>}
          <Button
            variant="ghost"
            icon={MessageSquare}
            // A chat is a write (its history), refused like any other in the demo or an unknown mode.
            disabledReason={reason}
            onClick={() => {
              close()
              // Only the validated ticker: names come from providers and must not become the user's words.
              openAssistant(assistantPrefillFor(ticker))
            }}
          >
            问助手
          </Button>
          <Button
            variant="ghost"
            icon={watched ? StarOff : Star}
            loading={toggleWatch.isPending}
            disabled={watch.isPending}
            disabledReason={reason}
            tipAlign="end"
            onClick={() => toggleWatch.mutate()}
          >
            {watched ? '移出自选' : '加入自选'}
          </Button>
        </div>
      )}
      {lab && (
        <p className="border-t border-line pt-4 text-table text-fg-3">
          研究模式只看行情和日线。
          <button type="button" onClick={() => { close(); navigate(index ? '/backtest' : watchLink(ticker)) }} className={cx('ml-1 rounded-sm text-accent hover:underline', focusRing)}>去回测</button>
        </p>
      )}
    </div>
  )
}

/** The drawer, mounted once by the shell; `openStockDrawer(ticker, name)` shows it. */
export default function StockDrawer() {
  const ticker = useStockDrawer((state) => state.ticker)
  const given = useStockDrawer((state) => state.name)
  const close = useStockDrawer((state) => state.close)
  const names = useNames(ticker ? [ticker] : [])
  const name = given || (ticker ? nameOf(names, ticker) : null)
  const exchange = ticker ? ticker.split(':')[0] : ''
  return (
    <Drawer
      open={ticker !== null}
      onClose={close}
      title={name ?? ticker ?? ''}
      description={ticker ? [ticker, exchangeLabel(exchange), currencyOf(ticker)].filter(Boolean).join(' · ') : undefined}
    >
      {ticker && <DrawerBody key={ticker} ticker={ticker} name={name} close={close} />}
    </Drawer>
  )
}
