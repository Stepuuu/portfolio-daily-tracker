// 数据: the daily bars kept in the local cache per instrument (GET /data/symbols: rows and dates) with where
// they came from (GET /data/cache symbol_details: source, download time, completeness, and the same per
// price adjustment; 不可用 only where the cache recorded nothing), 更新到今天 (POST /data/download with
// all_adjustments: every adjustment the cache holds, each reported) and 删除缓存 (DELETE
// /data/cache/{symbol}) — the actions the API offers. 导入 CSV
// (POST /data/import-csv) stores a file's bars under a code and an adjustment; the answer's
// provisional_note (bars from before that day's close, not kept) is shown as it is.
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Trash2, Upload } from 'lucide-react'
import { Button, DataTable, Dialog, EmptyState, Field, InlineAlert, Input, Select, cx, focusRing, numeric, toast, type Column } from '@/components/ui'
import { UNAVAILABLE, fmtQty } from '@/lib/format'
import { cacheProvenance, downloadReport, type DownloadReport } from '@/features/backtest/result'
import {
  CACHE_KEY, deleteCachedSymbol, downloadBars, fetchCacheStats, fetchCachedSymbols, importBarsCsv, refusal, type CacheDetail, type CachedSymbol,
  type CsvImportAnswer,
} from '@/services/backtest'

function CsvImport({ readOnlyReason, onDone }: { readOnlyReason: string | undefined; onDone: () => void }) {
  const [symbol, setSymbol] = useState('')
  const [adjust, setAdjust] = useState('qfq')
  const [file, setFile] = useState<File | null>(null)
  const [answer, setAnswer] = useState<CsvImportAnswer | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const submit = async () => {
    setProblem(null)
    setAnswer(null)
    if (!symbol.trim() || !file) {
      setProblem(!symbol.trim() ? '请填写这份行情对应的代码' : '请选择一个 CSV 文件')
      return
    }
    setBusy(true)
    try {
      setAnswer(await importBarsCsv(file, symbol.trim().toUpperCase(), adjust))
      if (input.current) input.current.value = ''
      setFile(null)
      onDone()
    } catch (exc) {
      setProblem(refusal(exc).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="导入 CSV" className="flex min-w-0 flex-col gap-3 rounded-lg border border-line bg-surface p-4">
      <h2 className="text-title text-fg">导入 CSV</h2>
      <p className="text-table text-fg-3">UTF-8 编码，列至少有 date、open、high、low、close、volume。回测按所选的复权方式读取数据，导入时要选对。</p>
      <div className="grid min-w-0 gap-3 sm:grid-cols-3">
        <Field label="代码">
          <Input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="如 SHA:600519" autoComplete="off" spellCheck={false} />
        </Field>
        <Field label="复权方式" hint="快速回测默认用前复权">
          <Select value={adjust} onChange={(event) => setAdjust(event.target.value)}
            options={[{ value: 'qfq', label: '前复权' }, { value: 'hfq', label: '后复权' }, { value: 'none', label: '不复权' }]} />
        </Field>
        <Field label="文件">
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className={cx('block w-full min-w-0 text-table text-fg-2 file:mr-3 file:rounded-md file:border file:border-line-input file:bg-surface file:px-3 file:py-1 file:text-table file:text-fg', focusRing)}
          />
        </Field>
      </div>
      <div>
        <Button icon={Upload} loading={busy} disabledReason={readOnlyReason} onClick={() => void submit()}>导入</Button>
      </div>
      {problem && <InlineAlert tone="warn">{problem}</InlineAlert>}
      {answer && (
        <InlineAlert title={`已导入 ${answer.rows_imported} 行`}>
          {answer.symbol} · {answer.filename ?? ''}{answer.sorted_by_date ? ' · 文件是新的在前，已按日期排序' : ''}
        </InlineAlert>
      )}
      {answer?.provisional_note && <InlineAlert tone="warn" title="有临时数据没有保存">{answer.provisional_note}</InlineAlert>}
    </section>
  )
}

export function DataTab({ readOnlyReason, today }: { readOnlyReason: string | undefined; today: string }) {
  const client = useQueryClient()
  const stats = useQuery({ queryKey: [...CACHE_KEY, 'stats'], queryFn: fetchCacheStats, retry: 1 })
  const symbols = useQuery({ queryKey: [...CACHE_KEY, 'symbols'], queryFn: fetchCachedSymbols, retry: 1 })
  const [clearing, setClearing] = useState<CachedSymbol | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [report, setReport] = useState<(DownloadReport & { symbol: string }) | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: CACHE_KEY })

  // Every adjustment the cache holds is refreshed (an index's 不复权 series is the one the comparison
  // reads), and each says how far it now reaches.
  const update = useMutation({
    mutationFn: (row: CachedSymbol) => downloadBars({ symbol: row.symbol, start_date: row.start_date ?? today, end_date: today, adjust: 'qfq', all_adjustments: true }),
    onSuccess: async (answer, row) => {
      const done = downloadReport(answer)
      setReport({ symbol: row.symbol, ...done })
      toast.success(`${row.symbol} 已更新`)
      await refresh()
    },
    onError: (exc) => setProblem(refusal(exc).message),
  })
  const clear = useMutation({
    mutationFn: (row: CachedSymbol) => deleteCachedSymbol(row.symbol),
    onSuccess: async (answer, row) => {
      setClearing(null)
      toast.success(`已删除 ${row.symbol} 的 ${answer.deleted_rows} 根缓存 K 线`)
      await refresh()
    },
    onError: (exc) => setProblem(refusal(exc).message),
  })

  if (stats.data?.kept === false) {
    return (
      <EmptyState
        title="演示模式不保存行情"
        description="演示回测用程序按固定种子生成的虚构 K 线，每次现算，不下载、不写入缓存，所以这里是空的。"
      />
    )
  }

  const details = new Map((stats.data?.symbol_details ?? []).map((detail: CacheDetail) => [detail.symbol, cacheProvenance(detail)]))
  const provenance = (row: CachedSymbol) => details.get(row.symbol) ?? cacheProvenance(null)
  const unknown = (text: string) => <span className={text === UNAVAILABLE ? 'text-fg-3' : undefined}>{text}</span>
  const columns: Column<CachedSymbol>[] = [
    { key: 'symbol', header: '标的', sortValue: (row) => row.symbol, render: (row) => <span className={cx('font-medium', numeric)}>{row.symbol}</span> },
    { key: 'rows', header: 'K 线数', numeric: true, sortValue: (row) => row.rows, render: (row) => fmtQty(row.rows) },
    { key: 'start', header: '最早', render: (row) => <span className={numeric}>{row.start_date ?? UNAVAILABLE}</span> },
    { key: 'end', header: '最晚', sortValue: (row) => row.end_date ?? null, render: (row) => <span className={numeric}>{row.end_date ?? UNAVAILABLE}</span> },
    { key: 'source', header: '来源', render: (row) => unknown(provenance(row).source) },
    { key: 'fetched', header: '最近下载', hideBelow: 'md', render: (row) => <span className={numeric}>{unknown(provenance(row).fetchedAt)}</span> },
    {
      key: 'complete', header: '完整性',
      render: (row) => {
        const complete = provenance(row).complete
        return <span className={complete.tone === 'warn' ? 'text-warn' : complete.text === UNAVAILABLE ? 'text-fg-3' : undefined}>{complete.text}</span>
      },
    },
  ]
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <p className="text-table text-fg-2">
        回测时缺的行情会自动下载并存在本地；这里列出已缓存的标的，以及每种复权方式存到哪一天、来源和最近一次下载。
        来源或下载时间是{UNAVAILABLE}：这份缓存写入时还没有记录；完整性是{UNAVAILABLE}：没有记录下载区间。
      </p>
      {(symbols.isError || stats.isError) && (
        <InlineAlert tone="warn" title="读不到行情缓存" action={<Button size="sm" onClick={() => void refresh()}>重试</Button>}>
          {refusal(symbols.error ?? stats.error).message}
        </InlineAlert>
      )}
      {problem && <InlineAlert tone="warn" action={<Button size="sm" variant="ghost" onClick={() => setProblem(null)}>知道了</Button>}>{problem}</InlineAlert>}
      {report && (
        <section aria-label="更新结果">
          <InlineAlert
            tone={report.tone === 'warn' ? 'warn' : 'info'}
            title={`${report.symbol} 更新后`}
            action={<Button size="sm" variant="ghost" onClick={() => setReport(null)}>知道了</Button>}
          >
            <ul className={cx('flex flex-col gap-0.5', numeric)}>
              {report.lines.map((line) => <li key={line.text} className={cx('whitespace-normal break-words', line.tone === 'warn' && 'text-warn')}>{line.text}</li>)}
            </ul>
          </InlineAlert>
        </section>
      )}
      <DataTable
        caption="已缓存的行情"
        columns={columns}
        rows={symbols.data?.symbols ?? []}
        rowKey={(row) => row.symbol}
        loading={symbols.isPending}
        mobile="cards"
        renderRowExtra={(row) => {
          const lines = provenance(row).adjustments
          return lines.length ? (
            <ul className={cx('flex flex-col gap-0.5 text-table text-fg-3', numeric)}>
              {lines.map((line) => <li key={line.adjust} className={cx('whitespace-normal break-words', line.tone === 'warn' && 'text-warn')}>{line.text}</li>)}
            </ul>
          ) : null
        }}
        rowActions={(row) => (
          <>
            <Button size="sm" variant="ghost" icon={RefreshCw} disabledReason={readOnlyReason}
              loading={update.isPending && update.variables?.symbol === row.symbol}
              aria-label={`更新 ${row.symbol} 到今天`} onClick={() => update.mutate(row)}>
              更新到今天
            </Button>
            <Button size="sm" variant="ghost" icon={Trash2} disabledReason={readOnlyReason} tipAlign="end"
              aria-label={`删除 ${row.symbol} 的缓存`} onClick={() => setClearing(row)}>
              删除缓存
            </Button>
          </>
        )}
        empty={<EmptyState title="还没有缓存的行情" description="跑一次回测，用到的日线会存在这里，下次直接用本地数据。" />}
      />
      <CsvImport readOnlyReason={readOnlyReason} onDone={() => void refresh()} />
      <Dialog
        open={clearing !== null}
        onClose={() => setClearing(null)}
        size="sm"
        title={clearing ? `删除 ${clearing.symbol} 的缓存？` : '删除缓存？'}
        footer={(
          <>
            <Button data-autofocus onClick={() => setClearing(null)}>取消</Button>
            <Button variant="warn-outline" loading={clear.isPending} onClick={() => clearing && clear.mutate(clearing)}>删除缓存</Button>
          </>
        )}
      >
        <p className="text-body text-fg-2">删除后下次回测会重新下载这只标的的行情（需要网络）；已保存的回测结果不受影响。</p>
      </Dialog>
    </div>
  )
}
