// UI gallery (/__ui, development builds only): every kit component in every state, laid out the way
// real pages will use them. Reviewers judge the look here; `?theme=light|dark` picks the theme.
import { useEffect, useLayoutEffect, useState, type CSSProperties, type FormEvent, type ReactNode } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import {
  ChevronDown, Download, FileUp, Filter, Inbox, MessageSquare, MoreHorizontal, Plus, RefreshCw, Search, Upload,
} from 'lucide-react'
import {
  Badge, Button, Checkbox, DataTable, DateInput, Dialog, Drawer, EmptyState, Field, IconButton, InlineAlert, Input,
  LongValue, MoneyInput, NumberInput, PageHeader, Panel, Popover, SegmentedControl, Select, Skeleton, Stat, StatGroup, Tabs,
  TableSkeleton, Textarea, Toaster, cx, numeric, toast, useTheme, useTokens, type Column,
} from '@/components/ui'
import {
  UNAVAILABLE, fmtCompact, fmtLong, fmtMoney, fmtNumber, fmtPct, fmtPrice, fmtQty, toneOf, type Currency,
} from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'
import { applyTheme, followThemePreference, type Theme, type TokenName } from '@/lib/theme'
import { accountTotals, holdings, ledgerEvents, totals, type Holding, type LedgerEvent } from './galleryData'

// ------------------------------------------------------------------------------------------------
// Small helpers (pages will have their own domain components for these)

const na = <span className="text-fg-3">{UNAVAILABLE}</span>

/** Native currency in cells: CNY plain, other currencies with their symbol. */
function money(value: string | null, currency: Currency, signed = false) {
  if (value === null) return na
  return currency === 'CNY' ? fmtNumber(value, { signed }) : fmtMoney(value, currency, { signed })
}

function change(text: ReactNode, value: string | null) {
  if (value === null) return na
  return <span className={pnlTextClass(toneOf(value))}>{text}</span>
}

/** A page's own layout wrapper that brings an id: a custom component, not a kit control. */
function FieldRow({ id, children }: { id: string; children: ReactNode }) {
  return <div id={id} className="flex items-center gap-2">{children}</div>
}

function QuoteState({ holding }: { holding: Holding }) {
  if (holding.quote === 'stale') return <Badge tone="warn">已过期 {holding.quoteTime}</Badge>
  if (holding.quote === 'delayed') return <Badge>延迟</Badge>
  if (holding.quote === 'close') return <Badge>收盘价 {holding.quoteTime}</Badge>
  return null
}

function MenuItem({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-8 w-full items-center rounded-md px-2 text-left text-body text-fg hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
    >
      {children}
    </button>
  )
}

function GallerySection({ id, title, uses, children }: { id: string; title: string; uses: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex scroll-mt-16 flex-col gap-6 border-t border-line pt-6 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={`${id}-title`} className="text-caption font-medium text-fg-3">{title}</h2>
        <p className="text-caption text-fg-3">{uses}</p>
      </div>
      {children}
    </section>
  )
}

function useGalleryTheme(): [Theme, (theme: Theme) => void] {
  const [params, setParams] = useSearchParams()
  const theme = useTheme()
  const requested = params.get('theme')
  useLayoutEffect(() => {
    if (requested === 'light' || requested === 'dark') applyTheme(requested)
  }, [requested])
  // The gallery previews a theme without saving it; leaving restores the saved preference.
  useEffect(() => () => followThemePreference(), [])
  const choose = (next: Theme) => {
    applyTheme(next)
    setParams((current) => {
      current.set('theme', next)
      return current
    }, { replace: true })
  }
  return [theme, choose]
}

// ------------------------------------------------------------------------------------------------
// Example page: 今日

function holdingColumns(): Column<Holding>[] {
  return [
    {
      key: 'name', header: '名称 / 代码', label: '名称',
      render: (h) => (
        <div className="min-w-0 py-1">
          <div className="truncate font-medium text-fg">{h.name}</div>
          {h.ticker && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-fg-3">
              <span>{h.ticker}{h.quote === 'live' && ` · ${h.source} ${h.quoteTime}`}</span>
              <QuoteState holding={h} />
            </div>
          )}
        </div>
      ),
      sortValue: (h) => h.name,
    },
    {
      key: 'price', header: '现价', numeric: true,
      render: (h) => (h.kind === 'cash' ? '' : h.price === null ? na : fmtPrice(h.price, h.currency, h.kind, { symbol: h.currency !== 'CNY' })),
    },
    {
      key: 'change', header: '涨跌幅', numeric: true,
      render: (h) => (h.kind === 'cash' ? '' : change(fmtPct(h.changePct, { signed: true }), h.changePct)),
      sortValue: (h) => (h.changePct === null ? null : Number(h.changePct)),
    },
    {
      key: 'day', header: '今日盈亏', numeric: true,
      render: (h) => (h.kind === 'cash' ? '' : change(money(h.dayPnl, h.currency, true), h.dayPnl)),
    },
    { key: 'qty', header: '持仓', numeric: true, hideBelow: 'md', render: (h) => (h.quantity === null ? '' : fmtQty(h.quantity)) },
    {
      key: 'cost', header: '成本', numeric: true, hideBelow: 'lg',
      render: (h) => (h.cost === null ? '' : fmtPrice(h.cost, h.currency, h.kind === 'cash' ? 'stock' : h.kind)),
    },
    {
      key: 'value', header: '市值', numeric: true, render: (h) => money(h.marketValue, h.currency),
      sortValue: (h) => (h.marketValue === null ? null : Number(h.marketValue)),
    },
    {
      key: 'pnl', header: '总盈亏', numeric: true, hideBelow: 'sm',
      render: (h) => (h.kind === 'cash' ? '' : change(money(h.totalPnl, h.currency, true), h.totalPnl)),
    },
    {
      key: 'pnlPct', header: '收益率', numeric: true, hideBelow: 'lg',
      render: (h) => (h.kind === 'cash' ? '' : change(fmtPct(h.totalPnlPct, { signed: true }), h.totalPnlPct)),
    },
    { key: 'weight', header: '占比', numeric: true, hideBelow: 'md', render: (h) => (h.weightPct === null ? na : fmtPct(h.weightPct, { digits: 1 })) },
  ]
}

function RowMenu({ name }: { name: string }) {
  return (
    <Popover
      align="end"
      label={`${name} 更多操作`}
      className="w-40 p-1"
      trigger={<IconButton icon={MoreHorizontal} label="更多操作" size="sm" tipAlign="end" />}
    >
      {(close) => (
        <div className="flex flex-col">
          <MenuItem onClick={() => { toast.info(`已为 ${name} 打开提醒设置`); close() }}>设提醒</MenuItem>
          <MenuItem onClick={() => { toast.info(`已在助手中提问 ${name}`); close() }}>问助手</MenuItem>
          <MenuItem onClick={() => { toast.info('已加入自选'); close() }}>加入自选</MenuItem>
        </div>
      )}
    </Popover>
  )
}

function TodayExample({ onOpenEntry, onOpenStock }: { onOpenEntry: () => void; onOpenStock: (h: Holding) => void }) {
  const [account, setAccount] = useState('all')
  const rows = account === 'all' ? holdings : holdings.filter((h) => h.account === account)
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="今日"
        description={(
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-table">
            <span>A股 盘中 10:32:05</span><span aria-hidden className="text-fg-3">·</span>
            <span>东财 3 秒前</span><span aria-hidden className="text-fg-3">·</span>
            <span>港股 盘中</span><span aria-hidden className="text-fg-3">·</span>
            <span>美股 休市（收盘价）</span><span aria-hidden className="text-fg-3">·</span>
            <span>今日快照 未到时间</span>
          </span>
        )}
        actions={(
          <>
            <IconButton icon={RefreshCw} label="刷新" variant="secondary" />
            <Button variant="primary" icon={Plus} onClick={onOpenEntry}>记一笔</Button>
          </>
        )}
      />
      <StatGroup>
        <Stat size="lg" label="总资产" value={fmtMoney(totals.totalValue, 'CNY')} />
        <Stat size="lg" label="今日盈亏" tone="auto" value={fmtMoney(totals.dayPnl, 'CNY', { signed: true })}
          sub={change(fmtPct(totals.dayPnlPct, { signed: true }), totals.dayPnlPct)} />
        <Stat size="lg" label="总盈亏" tone="auto" value={fmtMoney(totals.totalPnl, 'CNY', { signed: true })}
          sub={change(fmtPct(totals.totalPnlPct, { signed: true }), totals.totalPnlPct)} />
        <Stat size="lg" label="现金" value={fmtMoney(totals.cash[0][1], totals.cash[0][0])}
          sub={<span className="text-fg-2">{totals.cash.slice(1).map(([ccy, v]) => fmtMoney(v, ccy)).join(' · ')}</span>} />
      </StatGroup>
      <InlineAlert
        title="待确认 2 笔"
        action={<Button size="sm" variant="secondary">核对方案</Button>}
      >
        11-17 买入 示例科技 100 股 · 分红 青山药业 ¥1,936.00
      </InlineAlert>
      <Panel
        padding="none"
        title="持仓"
        actions={(
          <>
            <Select
              size="sm"
              aria-label="账户"
              className="w-32"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              options={[{ value: 'all', label: '全部账户' }, { value: '主账户', label: '主账户' }, { value: '港美账户', label: '港美账户' }]}
            />
            <Button size="sm" icon={Upload}>导入</Button>
          </>
        )}
      >
        <DataTable
          caption="持仓"
          columns={holdingColumns()}
          rows={rows}
          rowKey={(h) => h.id}
          mobile="cards"
          onRowClick={(h) => (h.kind === 'cash' ? undefined : onOpenStock(h))}
          rowClassName={(h) => (h.kind === 'cash' ? 'text-fg-2' : undefined)}
          groupBy={{
            key: (h) => h.account,
            header: (key, list) => {
              const total = accountTotals[key]
              return (
                <span>
                  {key}
                  <span className="ml-2 font-normal text-fg-3">
                    {list.filter((h) => h.kind !== 'cash').length} 项持仓{total?.missing ? ` · ${total.missing} 项缺价` : ''}
                  </span>
                </span>
              )
            },
            summary: (key) => {
              const total = accountTotals[key]
              return {
                day: total?.dayPnl == null ? na : change(fmtMoney(total.dayPnl, 'CNY', { signed: true }), total.dayPnl),
                value: total?.marketValue == null ? na : fmtMoney(total.marketValue, 'CNY'),
                pnl: total?.totalPnl == null ? na : change(fmtMoney(total.totalPnl, 'CNY', { signed: true }), total.totalPnl),
              }
            },
          }}
          rowActions={(h) =>
            h.kind === 'cash' ? null : (
              <>
                <Button size="sm" variant="ghost" onClick={onOpenEntry}>买</Button>
                <Button size="sm" variant="ghost" onClick={onOpenEntry}>卖</Button>
                <RowMenu name={h.name} />
              </>
            )}
          footer="港股、美股按原币显示；分组合计为人民币，按今日汇率折算。"
        />
      </Panel>
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="今日事项">
          <ul className="-my-2 divide-y divide-line text-body">
            <li className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">复查到期：示例控股 研究笔记</span>
              <Button size="sm" variant="ghost">打开</Button>
            </li>
            <li className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">计划：示例科技 目标 180.00，距 <span className={numeric}>7.0%</span></span>
              <Button size="sm" variant="ghost">查看计划</Button>
            </li>
          </ul>
        </Panel>
        <Panel title="今日提醒" description="提醒监控 运行中 · 仅交易时段 · 后端关闭时不会提醒">
          <ul className="-my-2 divide-y divide-line text-body">
            <li className="flex items-center justify-between gap-3 py-2">
              <span className="min-w-0">
                <span className={cx('mr-2 text-fg-3', numeric)}>10:05</span>
                远航能源 跌破止损 23.50，现价 23.45（东财 10:05:10）
              </span>
              <Button size="sm" variant="ghost" onClick={onOpenEntry}>记一笔卖出</Button>
            </li>
          </ul>
        </Panel>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------
// Example page: 账本

const eventColumns: Column<LedgerEvent>[] = [
  { key: 'date', header: '日期', render: (e) => <span className={numeric}>{e.date}</span>, sortValue: (e) => e.date, width: 112 },
  { key: 'kind', header: '类型', render: (e) => e.kind, width: 64 },
  { key: 'account', header: '账户', hideBelow: 'md', render: (e) => <span className="text-fg-2">{e.account}</span> },
  {
    key: 'ticker', header: '标的',
    render: (e) => (e.ticker ? <span className="whitespace-nowrap">{e.name} <span className="text-fg-3">{e.ticker}</span></span> : <span className="text-fg-3">现金</span>),
  },
  { key: 'qty', header: '数量', numeric: true, render: (e) => (e.quantity === null ? '' : fmtQty(e.quantity)) },
  { key: 'price', header: '价格', numeric: true, render: (e) => (e.price === null ? '' : fmtPrice(e.price, e.currency, e.ticker === 'SHA:519901' ? 'etf' : 'stock')) },
  {
    key: 'amount', header: '金额（原币）', numeric: true, render: (e) => fmtNumber(e.amount, { signed: true }),
    sortValue: (e) => Number(e.amount),
  },
  { key: 'fee', header: '费用', numeric: true, render: (e) => (e.fee === null ? '' : fmtNumber(e.fee)) },
  { key: 'source', header: '来源', hideBelow: 'lg', render: (e) => <span className="text-fg-2">{e.source}</span> },
]

function LedgerExample({ onOpenEntry, onReverse }: { onOpenEntry: () => void; onReverse: (e: LedgerEvent) => void }) {
  const { pathname } = useLocation()
  const [range, setRange] = useState('month')
  const tab = pathname.endsWith('/positions') ? 'positions' : pathname.endsWith('/import') ? 'import' : 'events'
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="账本"
        tabs={[
          { id: 'events', label: '流水', href: '/__ui' },
          { id: 'positions', label: '持仓与成本', href: '/__ui/positions' },
          { id: 'import', label: '导入', href: '/__ui/import', count: 2 },
        ]}
        actions={(
          <>
            <Button icon={Download}>备份</Button>
            <Button>导出</Button>
            <Button variant="primary" icon={Plus} onClick={onOpenEntry}>记一笔</Button>
          </>
        )}
      />
      <div className={cx('flex flex-col gap-1 text-table text-fg-2', numeric)}>
        <p><span className="font-medium text-fg">主账户</span> · CNY 120,000.00 · 净投入 ¥381,000.00 · 4 项持仓</p>
        <p><span className="font-medium text-fg">港美账户</span> · HKD 5,000.00 · USD 0.00 · 2 项持仓</p>
      </div>
      {tab === 'events' && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Select size="sm" aria-label="账户" className="w-28" defaultValue="all"
              options={[{ value: 'all', label: '全部账户' }, { value: 'main', label: '主账户' }]} />
            <Select size="sm" aria-label="类型" className="w-28" defaultValue="all"
              options={[{ value: 'all', label: '全部类型' }, { value: 'buy', label: '买入' }, { value: 'sell', label: '卖出' }]} />
            <Input size="sm" aria-label="标的" placeholder="代码或名称" prefix={<Search size={14} aria-hidden />} className="w-40" />
            <SegmentedControl
              size="sm"
              ariaLabel="日期范围"
              value={range}
              onChange={setRange}
              options={[{ value: 'week', label: '本周' }, { value: 'month', label: '本月' }, { value: 'year', label: '今年' }, { value: 'all', label: '全部' }]}
            />
          </div>
          <Panel padding="none" aria-label="流水">
            <DataTable
              caption="账本流水"
              columns={eventColumns}
              rows={ledgerEvents}
              rowKey={(e) => e.id}
              defaultSort={{ key: 'date', direction: 'desc' }}
              rowActions={(e) => <Button size="sm" variant="ghost" onClick={() => onReverse(e)}>冲销</Button>}
              renderRowExtra={(e) => (e.note ? <span>备注：{e.note}</span> : null)}
              footer={`共 ${ledgerEvents.length} 笔 · 2026-11-06 至 2026-11-17`}
            />
          </Panel>
        </>
      )}
      {tab === 'positions' && (
        <Panel padding="none" aria-label="持仓与成本">
          <DataTable
            caption="持仓与成本"
            columns={[
              { key: 'name', header: '标的', render: (h: Holding) => <span className="whitespace-nowrap">{h.name} <span className="text-fg-3">{h.ticker}</span></span> },
              { key: 'qty', header: '数量', numeric: true, render: (h: Holding) => fmtQty(h.quantity) },
              { key: 'cost', header: '加权成本', numeric: true, render: (h: Holding) => fmtPrice(h.cost, h.currency, h.kind === 'cash' ? 'stock' : h.kind) },
              { key: 'ccy', header: '币种', render: (h: Holding) => h.currency },
            ]}
            rows={holdings.filter((h) => h.kind !== 'cash')}
            rowKey={(h) => h.id}
          />
        </Panel>
      )}
      {tab === 'import' && (
        <Panel>
          <EmptyState
            icon={FileUp}
            title="拖入文件或点击选择"
            description="支持券商导出的 CSV、XLS、XLSX、TXT 交割单或对账单，最大 5 MB。"
            action={<Button variant="primary" icon={Upload}>选择文件</Button>}
          />
        </Panel>
      )}
    </div>
  )
}

// ------------------------------------------------------------------------------------------------
// Example form: 记一笔

function EntryForm({ id, onPreview }: { id: string; onPreview?: () => void }) {
  const [kind, setKind] = useState('buy')
  const [account, setAccount] = useState('主账户')
  const [date, setDate] = useState('2026-11-17')
  const [ticker, setTicker] = useState('示例科技')
  const [quantity, setQuantity] = useState('100')
  const [price, setPrice] = useState('168.20')
  const [fee, setFee] = useState('')
  const [note, setNote] = useState('')
  const [margin, setMargin] = useState(false)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (price === '' || quantity === '') return
    onPreview ? onPreview() : toast.success('已生成入账预览（示例）')
  }
  return (
    <form id={id} onSubmit={submit} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl
          ariaLabel="类型"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'buy', label: '买入' }, { value: 'sell', label: '卖出' }, { value: 'dividend', label: '分红' },
            { value: 'deposit', label: '入金' }, { value: 'withdrawal', label: '出金' },
          ]}
        />
        <Popover label="更多类型" className="w-40 p-1" trigger={<Button variant="ghost" size="md">更多<ChevronDown size={14} aria-hidden /></Button>}>
          {(close) => (
            <div className="flex flex-col">
              {['换汇', '转账', '拆股/送转', '费用', '利息', '基金'].map((label) => (
                <MenuItem key={label} onClick={() => { setKind(label); close() }}>{label}</MenuItem>
              ))}
            </div>
          )}
        </Popover>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="账户">
          <Select value={account} onChange={(event) => setAccount(event.target.value)}
            options={[{ value: '主账户', label: '主账户' }, { value: '港美账户', label: '港美账户' }]} />
        </Field>
        <Field label="日期" hint="今天是交易日">
          <DateInput value={date} onChange={(event) => setDate(event.target.value)} />
        </Field>
      </div>
      <Field
        label="标的"
        hint={(
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={numeric}>示例科技 SHA:609001 · CNY · 现价 168.20 · 昨收 166.21 · 东财 10:32</span>
            <Button size="sm" variant="ghost" className="-my-1" onClick={() => setPrice('168.20')}>按现价</Button>
            <Button size="sm" variant="ghost" className="-my-1" onClick={() => setPrice('166.21')}>按昨收</Button>
          </span>
        )}
      >
        <Input value={ticker} onChange={(event) => setTicker(event.target.value)} prefix={<Search size={14} aria-hidden />} autoComplete="off" data-autofocus />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="数量" hint="可卖 300 股" required error={quantity === '' ? '请输入数量' : undefined}>
          <NumberInput value={quantity} onValueChange={setQuantity} step={100} stepButtons suffix="股" />
        </Field>
        <Field label="价格" required error={price === '' ? '请输入成交价格' : undefined}>
          <MoneyInput currency="CNY" value={price} onValueChange={setPrice} />
        </Field>
      </div>
      <Field label="费用" hint="按账户费率估算：佣金 5.00 · 过户费 0.17 · 印花税 0.00；留空即按估算入账">
        <MoneyInput currency="CNY" value={fee} onValueChange={setFee} placeholder="5.17" />
      </Field>
      <p className={cx('text-table text-fg-2', numeric)}>
        成交金额 ¥16,820.00 · CNY 现金 ¥120,000.00 → ¥103,174.83
      </p>
      <Field label="备注">
        <Textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="选填：为什么做这笔交易" rows={2} />
      </Field>
      <Checkbox checked={margin} onChange={(event) => setMargin(event.target.checked)} label="允许融资" description="现金不足时记为负现金余额" />
    </form>
  )
}

// ------------------------------------------------------------------------------------------------
// Reference: type and colour

const TYPE_SCALE: [string, string, string][] = [
  ['text-figure', '28 / 36 · 600', '¥616,954.45'],
  ['text-page', '20 / 28 · 600', '账本'],
  ['text-title', '16 / 24 · 600', '记一笔'],
  ['text-body', '14 / 22 · 400', '正文、输入框与按钮'],
  ['text-table', '13 / 20 · 400', '表格与说明 1,680.00'],
  ['text-caption', '12 / 16 · 400', '标签与时间 10:32:05'],
]

const ROLES: [TokenName, string, string][] = [
  ['bg', 'bg-bg', '页面背景'], ['surface', 'bg-surface', '面板、表格、对话框'], ['surface-2', 'bg-surface-2', '表头、悬停'],
  ['border', 'bg-line', '分隔线'], ['border-input', 'bg-line-input', '控件边框'], ['text', 'bg-fg', '主文字'],
  ['text-2', 'bg-fg-2', '标签、持平'], ['text-3', 'bg-fg-3', '说明、时间'], ['accent', 'bg-accent', '主按钮、链接、焦点'],
  ['up', 'bg-up', '上涨、盈利'], ['down', 'bg-down', '下跌、亏损'], ['warn', 'bg-warn', '错误、警告、过期'],
]

function Reference() {
  const tokens = useTokens()
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="字号" description="只用这六档；数字一律等宽（tabular-nums）。">
        <dl className="flex flex-col gap-3">
          {TYPE_SCALE.map(([name, spec, sample]) => (
            <div key={name} className="flex items-baseline justify-between gap-4">
              <dt className="shrink-0 text-caption text-fg-3"><span className="text-fg-2">{name}</span> · {spec}</dt>
              <dd className={cx(name, numeric, 'truncate text-right')}>{sample}</dd>
            </div>
          ))}
          <div className="flex items-baseline justify-between gap-4 border-t border-line pt-3">
            <dt className="shrink-0 text-caption text-fg-3"><span className="text-fg-2">LongValue</span> · 超过 8 位小数</dt>
            <dd className={cx('text-table', numeric, 'text-right')}>
              <LongValue value={fmtLong('32.016666666666666666666666667', (value) => fmtPrice(value, 'CNY', 'stock', { exact: true }))} />
              <span className="ml-2 text-fg-3">悬停看完整值</span>
            </dd>
          </div>
        </dl>
      </Panel>
      <Panel title="颜色" description="红只表示上涨，绿只表示下跌；错误和警告用琥珀色。">
        <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          {ROLES.map(([token, swatch, use]) => (
            <li key={token} className="flex items-center gap-3 text-table">
              <span aria-hidden className={cx('h-4 w-4 shrink-0 rounded border border-line', swatch)} />
              <span className="text-fg">{token}</span>
              <span className={cx('text-fg-3', numeric)}>{tokens[token]}</span>
              <span className="ml-auto truncate text-fg-2">{use}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------
// The gallery

const SECTIONS: [string, string][] = [
  ['today', '今日'], ['ledger', '账本'], ['form', '记一笔'], ['feedback', '反馈与状态'], ['controls', '控件'], ['reference', '字号与颜色'],
]

export default function UiGallery() {
  const [theme, setTheme] = useGalleryTheme()
  const [entryOpen, setEntryOpen] = useState(false)
  const [confirmEvent, setConfirmEvent] = useState<LedgerEvent | null>(null)
  const [stock, setStock] = useState<Holding | null>(null)
  const [tab, setTab] = useState('brief')
  const [checks, setChecks] = useState({ a: true, b: false })
  const [segment, setSegment] = useState('day')
  const [saving, setSaving] = useState(false)

  // ?open=entry|confirm|drawer|toasts|popover shows an overlay on load, for screenshots.
  const [params] = useSearchParams()
  const initialOverlay = params.get('open')
  useEffect(() => {
    if (initialOverlay === 'entry') setEntryOpen(true)
    if (initialOverlay === 'confirm') setConfirmEvent(ledgerEvents[0])
    if (initialOverlay === 'drawer') setStock(holdings[0])
    if (initialOverlay === 'toasts') {
      toast.success('已入账 1 笔 · 版本 12', { action: { label: '再记一笔', onClick: () => setEntryOpen(true) }, duration: 0 })
      toast.warn('行情连接中断，显示最后一次更新 10:30:12', { action: { label: '重试', onClick: () => {} }, duration: 0 })
      toast.info('今日快照将在 15:40 自动生成', { duration: 0 })
    }
  }, [initialOverlay])

  const style = { '--sticky-top': '48px' } as CSSProperties

  return (
    <div style={style} className="min-h-screen bg-bg font-sans text-body text-fg antialiased">
      <header className="sticky top-0 z-20 flex h-12 items-center gap-4 border-b border-line bg-surface px-4 sm:px-6">
        <h1 className="text-title">组件库</h1>
        <span className="hidden text-caption text-fg-3 sm:inline">仅开发环境</span>
        <nav aria-label="分区" className="mx-auto hidden items-center gap-4 text-table text-fg-2 lg:flex">
          {SECTIONS.map(([id, label]) => (
            <a key={id} href={`#${id}`} className="rounded-sm hover:text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">{label}</a>
          ))}
        </nav>
        <SegmentedControl
          size="sm"
          ariaLabel="主题"
          className="ml-auto lg:ml-0"
          value={theme}
          onChange={setTheme}
          options={[{ value: 'dark', label: '深色' }, { value: 'light', label: '浅色' }]}
        />
      </header>

      <main className="mx-auto flex max-w-[1280px] flex-col gap-8 px-4 pb-16 pt-6 sm:px-6">
        <GallerySection id="today" title="示例页面 · 今日" uses="PageHeader · StatGroup / Stat · InlineAlert · Panel · DataTable（分组、行操作、手机卡片） · Popover">
          <TodayExample onOpenEntry={() => setEntryOpen(true)} onOpenStock={setStock} />
        </GallerySection>

        <GallerySection id="ledger" title="示例页面 · 账本" uses="PageHeader（路由标签） · 筛选控件 · DataTable（排序、吸顶表头、行备注）">
          <LedgerExample onOpenEntry={() => setEntryOpen(true)} onReverse={setConfirmEvent} />
        </GallerySection>

        <GallerySection id="form" title="示例表单 · 记一笔" uses="Field · SegmentedControl · Select · DateInput · Input · NumberInput · MoneyInput · Textarea · Checkbox · Dialog">
          <Panel
            className="max-w-[720px]"
            title="记一笔"
            actions={<Button size="sm" variant="ghost" onClick={() => setEntryOpen(true)}>在对话框中打开</Button>}
          >
            <EntryForm id="entry-form-inline" />
            <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <div className="flex flex-wrap items-center gap-1 text-table text-fg-3">
                也可以：
                <Button size="sm" variant="ghost" icon={FileUp}>上传交割单</Button>
                <Button size="sm" variant="ghost" icon={Upload}>识别截图</Button>
                <Button size="sm" variant="ghost" icon={MessageSquare}>用一句话描述</Button>
              </div>
              <Button type="submit" form="entry-form-inline" variant="primary">预览入账</Button>
            </div>
          </Panel>
        </GallerySection>

        <GallerySection id="feedback" title="反馈与状态" uses="toast · InlineAlert · Badge · Stat（不可用、紧凑） · DataTable（加载、空） · Skeleton · EmptyState">
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => toast.success('已入账 1 笔 · 版本 12', { action: { label: '再记一笔', onClick: () => setEntryOpen(true) } })}>成功提示</Button>
            <Button onClick={() => toast.warn('行情连接中断，显示最后一次更新 10:30:12', { action: { label: '重试', onClick: () => toast.info('正在重新连接') } })}>警告提示</Button>
            <Button onClick={() => toast.info('今日快照将在 15:40 自动生成')}>普通提示</Button>
          </div>
          <div className="flex flex-col gap-3">
            <InlineAlert tone="warn" title="行情已过期" action={<Button size="sm" variant="secondary" icon={RefreshCw}>重试</Button>}>
              港股行情最后更新于 10:20，相关市值与盈亏显示为不可用。
            </InlineAlert>
            <InlineAlert tone="warn">导入失败：第 12 行缺少成交日期。</InlineAlert>
            <InlineAlert>演示模式 · 数据为虚构，只读。</InlineAlert>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge>实时</Badge>
            <Badge>延迟</Badge>
            <Badge tone="warn">已过期 10:20</Badge>
            <Badge>收盘价</Badge>
            <Badge>时间未知</Badge>
            <Badge tone="warn">不可用</Badge>
            <Badge tone="accent">待确认 2</Badge>
            <Badge tone="up">+2.10%</Badge>
            <Badge tone="down">-0.85%</Badge>
          </div>
          <StatGroup>
            <Stat label="总资产" value={null} unavailableReason="1 项缺价" size="lg" />
            <Stat label="持仓市值（紧凑写法）" value={fmtCompact('123100000', 'CNY')} exactTitle={fmtMoney('123100000', 'CNY')} size="lg" />
            <Stat label="今日盈亏" value={fmtMoney('0', 'CNY', { signed: true })} tone="auto" sub={<span className="text-fg-2">{fmtPct(0, { signed: true })}</span>} />
            <Stat label="单笔最大亏损" value={fmtMoney('-3120.5', 'CNY', { signed: true })} tone="auto" />
            <Stat label="胜率" value={fmtPct('0.612', { ratio: true, digits: 1 })} />
          </StatGroup>
          <div className="grid gap-6 lg:grid-cols-2">
            <Panel padding="none" title="加载中" description="骨架在 200 毫秒后才出现，快速加载不闪烁。">
              <DataTable caption="加载中的流水" columns={eventColumns.slice(0, 5)} rows={[]} rowKey={(e) => e.id} loading />
            </Panel>
            <Panel padding="none" title="空列表">
              <DataTable
                caption="账户"
                columns={eventColumns.slice(0, 4)}
                rows={[]}
                rowKey={(e) => e.id}
                empty={(
                  <EmptyState
                    icon={Inbox}
                    title="还没有账户"
                    description="从券商导出的持仓文件开始，或手动录入期初余额。"
                    action={(
                      <>
                        <Button variant="primary">开始设置</Button>
                        <Button icon={Upload}>导入券商文件</Button>
                      </>
                    )}
                  />
                )}
              />
            </Panel>
            <Panel title="正在生成简报">
              <Skeleton lines={3} />
            </Panel>
            <Panel padding="none" title="列未知时">
              <TableSkeleton rows={3} cols={4} />
            </Panel>
          </div>
        </GallerySection>

        <GallerySection id="controls" title="控件" uses="Button · IconButton · Tabs · Popover · 输入控件的各种状态">
          <Panel title="按钮" description="主按钮每个区域最多一个；禁用时说明原因（悬停或键盘聚焦可见）。">
            <div className="flex flex-col gap-4">
              {(['primary', 'secondary', 'ghost', 'warn-outline'] as const).map((variant) => (
                <div key={variant} className="flex flex-wrap items-center gap-3">
                  <span className="w-24 shrink-0 text-caption text-fg-3">{variant}</span>
                  <Button variant={variant} icon={Plus}>记一笔</Button>
                  <Button variant={variant}>预览入账</Button>
                  <Button variant={variant} size="sm">放弃</Button>
                  <Button variant={variant} loading={saving} onClick={() => { setSaving(true); window.setTimeout(() => setSaving(false), 1500) }}>
                    {saving ? '正在保存' : '保存'}
                  </Button>
                  <Button variant={variant} disabledReason="演示模式只读">确认入账</Button>
                  <Button variant={variant} disabled>导入</Button>
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-3">
                <span className="w-24 shrink-0 text-caption text-fg-3">IconButton</span>
                <IconButton icon={RefreshCw} label="刷新" />
                <IconButton icon={Filter} label="筛选" variant="secondary" />
                <IconButton icon={Download} label="导出" variant="secondary" size="sm" />
                <IconButton icon={Plus} label="记一笔" variant="primary" />
                <IconButton icon={Upload} label="导入" variant="secondary" disabledReason="演示模式只读" />
              </div>
            </div>
          </Panel>

          <div className="grid gap-6 lg:grid-cols-2">
            <Panel title="页内标签">
              <Tabs
                ariaLabel="计划与复盘"
                value={tab}
                onChange={setTab}
                items={[
                  { id: 'brief', label: '今日简报' }, { id: 'plans', label: '计划', count: 3 },
                  { id: 'history', label: '组合走势' }, { id: 'lessons', label: '教训', count: 1 },
                ]}
              />
              <p className="pt-4 text-body text-fg-2">
                {tab === 'brief' ? '盘前 08:45 自动生成；每条事实都注明来源和时间。'
                  : tab === 'plans' ? '3 个计划进行中，1 个已触发止损。'
                    : tab === 'history' ? '组合走势：净值、净投入与回撤。' : '1 条教训待确认。'}
              </p>
            </Panel>
            <Panel title="弹出层与分段">
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center gap-3">
                  <Popover
                    label="筛选"
                    className="w-64"
                    defaultOpen={initialOverlay === 'popover'}
                    trigger={<Button icon={Filter}>筛选</Button>}
                  >
                    {(close) => (
                      <div className="flex flex-col gap-3">
                        <Field label="类型">
                          <Select defaultValue="all" options={[{ value: 'all', label: '全部' }, { value: 'trade', label: '只看交易' }]} />
                        </Field>
                        <Checkbox label="只看持仓中的标的" defaultChecked />
                        <div className="flex justify-end gap-2">
                          <Button size="sm" variant="ghost" onClick={close}>取消</Button>
                          <Button size="sm" variant="primary" onClick={close}>应用</Button>
                        </div>
                      </div>
                    )}
                  </Popover>
                  <span className="text-table text-fg-3">Esc 关闭并回到按钮</span>
                </div>
                <SegmentedControl
                  ariaLabel="周期"
                  value={segment}
                  onChange={setSegment}
                  options={[{ value: 'day', label: '日' }, { value: 'week', label: '周' }, { value: 'month', label: '月' }, { value: 'year', label: '年', disabled: true }]}
                />
                <SegmentedControl
                  size="sm"
                  ariaLabel="周期（小）"
                  value={segment}
                  onChange={setSegment}
                  options={[{ value: 'day', label: '日' }, { value: 'week', label: '周' }, { value: 'month', label: '月' }]}
                />
              </div>
            </Panel>
          </div>

          <Panel title="输入控件" description="标签在上，限制写在提示行；错误用琥珀色并说明怎么改。">
            <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="账户名称"><Input defaultValue="主账户" /></Field>
              <Field label="导入来源" hint="例如券商名称"><Input placeholder="选填" /></Field>
              <Field label="成交价格" required error="请输入大于 0 的价格"><MoneyInput currency="HKD" value="" onValueChange={() => {}} /></Field>
              <Field label="累计净投入" hint="按成本估算，可修改"><MoneyInput currency="CNY" value="381000.00" onValueChange={() => {}} /></Field>
              <Field label="佣金率" hint="万 2.5 即 0.025%"><NumberInput value="0.025" onValueChange={() => {}} suffix="%" /></Field>
              <Field label="账户" hint="已有记录，不能修改"><Input value="主账户" readOnly /></Field>
              <Field label="外部编号" hint="控件自带 id，标签仍然指向它"><Input id="gallery-external-id" placeholder="选填" /></Field>
              <Field label="委托编号" hint="控件包在布局里，也自带 id"><div><Input id="gallery-nested-id" placeholder="选填" /></div></Field>
              <Field label="成交编号" hint="自定义包装和里面的控件各有 id"><FieldRow id="gallery-row-a"><Input id="gallery-wrapped-id" placeholder="选填" /></FieldRow></Field>
              <Field label="合同编号" hint="自定义包装有 id，控件没有"><FieldRow id="gallery-row-b"><Input placeholder="选填" /></FieldRow></Field>
              <Field label="券商"><Select defaultValue="" placeholder="请选择" options={[{ value: 'a', label: '示例证券' }, { value: 'b', label: '其他' }]} /></Field>
              <Field label="复查日期"><DateInput defaultValue="2026-12-01" /></Field>
              <Field label="禁用"><Input value="演示模式只读" disabled /></Field>
              <Field label="研究笔记" className="sm:col-span-2"><Textarea placeholder="论点、失效条件、复查时间" /></Field>
              <div className="flex flex-col gap-2 pt-1">
                <Checkbox label="免五" description="佣金不足 5 元按实际收取" checked={checks.a} onChange={(event) => setChecks({ ...checks, a: event.target.checked })} />
                <Checkbox label="港股通结算" checked={checks.b} onChange={(event) => setChecks({ ...checks, b: event.target.checked })} />
                <Checkbox label="全选（部分已选）" indeterminate />
                <Checkbox label="已入账（不可修改）" checked disabled readOnly />
              </div>
            </div>
          </Panel>
        </GallerySection>

        <GallerySection id="reference" title="字号与颜色" uses="tokens.css · tailwind.config.js">
          <Reference />
        </GallerySection>
      </main>

      <Dialog
        open={entryOpen}
        onClose={() => setEntryOpen(false)}
        title="记一笔"
        closeOnBackdrop={false}
        footer={(
          <>
            <Button onClick={() => setEntryOpen(false)}>取消</Button>
            <Button type="submit" form="entry-form-dialog" variant="primary">预览入账</Button>
          </>
        )}
      >
        <EntryForm id="entry-form-dialog" onPreview={() => { setEntryOpen(false); toast.success('已入账 1 笔 · 版本 12', { action: { label: '再记一笔', onClick: () => setEntryOpen(true) } }) }} />
      </Dialog>

      <Dialog
        open={confirmEvent !== null}
        onClose={() => setConfirmEvent(null)}
        size="sm"
        title="冲销这笔记录？"
        description="会新增一笔反向记录，原记录保留，可再次冲销。"
        footer={(
          <>
            <Button data-autofocus onClick={() => setConfirmEvent(null)}>取消</Button>
            <Button variant="primary" onClick={() => { setConfirmEvent(null); toast.success('已冲销 1 笔 · 版本 13') }}>确认冲销</Button>
          </>
        )}
      >
        {confirmEvent && (
          <p className={cx('text-body text-fg-2', numeric)}>
            {confirmEvent.date} {confirmEvent.kind} {confirmEvent.name || '现金'} {confirmEvent.quantity ? `${fmtQty(confirmEvent.quantity)} 股` : ''}
            {' '}金额 {fmtMoney(confirmEvent.amount, confirmEvent.currency, { signed: true })}
          </p>
        )}
      </Dialog>

      <Drawer
        open={stock !== null}
        onClose={() => setStock(null)}
        title={stock ? `${stock.name} ${stock.ticker}` : ''}
        description={stock ? (stock.quote === 'live' ? `${stock.source} ${stock.quoteTime} · 实时` : undefined) : undefined}
        footer={(
          <>
            <Button icon={MessageSquare}>问助手</Button>
            <Button>设提醒</Button>
            <Button variant="primary" icon={Plus} onClick={() => { setStock(null); setEntryOpen(true) }}>记一笔</Button>
          </>
        )}
      >
        {stock && (
          <div className="flex flex-col gap-6">
            {stock.quote === 'stale' && <InlineAlert tone="warn">行情已过期（最后更新 {stock.quoteTime}），以下数值不可用。</InlineAlert>}
            <div className="grid grid-cols-2 gap-x-6 gap-y-4">
              <Stat label="现价" value={stock.price === null ? null : fmtPrice(stock.price, stock.currency, stock.kind === 'cash' ? 'stock' : stock.kind, { symbol: true })} />
              <Stat label="涨跌幅" tone="auto" value={stock.changePct === null ? null : fmtPct(stock.changePct, { signed: true })} />
              <Stat label="持仓" value={stock.quantity === null ? null : `${fmtQty(stock.quantity)} 股`} />
              <Stat label="市值" value={stock.marketValue === null ? null : fmtMoney(stock.marketValue, stock.currency)} />
              <Stat label="成本" value={stock.cost === null ? null : fmtPrice(stock.cost, stock.currency, stock.kind === 'cash' ? 'stock' : stock.kind, { symbol: true })} />
              <Stat label="总盈亏" tone="auto" value={stock.totalPnl === null ? null : fmtMoney(stock.totalPnl, stock.currency, { signed: true })} />
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-body font-medium text-fg">计划</h3>
              <p className="text-body text-fg-2">目标仓位 15% · 止损 150.00 · 目标 180.00</p>
            </div>
            <div className="flex flex-col gap-2">
              <h3 className="text-body font-medium text-fg">笔记</h3>
              <p className="text-body text-fg-2">新产品放量前不加仓；季报后复查毛利率。</p>
            </div>
          </div>
        )}
      </Drawer>

      <Toaster />
    </div>
  )
}
