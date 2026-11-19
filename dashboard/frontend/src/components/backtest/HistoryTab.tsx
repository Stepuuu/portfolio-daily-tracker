// 历史: the saved runs with their headline, period, method, result and date, a page of 100 at a time
// (加载更多 asks for the next one); open, rerun (by the saved manifest, not saved again), export and
// delete (after a confirmation). The demo keeps no history.
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, RotateCcw, Trash2 } from 'lucide-react'
import { Button, DataTable, Dialog, EmptyState, InlineAlert, buttonClass, cx, numeric, toast, type Column } from '@/components/ui'
import { pnlTextClass } from '@/lib/pnlColor'
import { backtestHref } from '@/features/backtest/model'
import { HISTORY_PAGE, historyRows, historyView, nextHistoryOffset, type HistoryView } from '@/features/backtest/result'
import { HISTORY_KEY, deleteHistory, exportUrl, fetchHistory, refusal, rerunHistory } from '@/services/backtest'

export function HistoryTab({ readOnlyReason }: { readOnlyReason: string | undefined }) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const history = useInfiniteQuery({
    queryKey: HISTORY_KEY,
    queryFn: ({ pageParam }) => fetchHistory(HISTORY_PAGE, pageParam),
    initialPageParam: 0,
    getNextPageParam: (_last, pages) => nextHistoryOffset(pages),
    retry: 1,
  })
  const [deleting, setDeleting] = useState<HistoryView | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const rows = historyRows(history.data?.pages).map(historyView)
  const stats = history.data?.pages[0]?.stats
  const kept = stats?.kept !== false
  const total = typeof stats?.total_runs === 'number' ? stats.total_runs : null

  const rerun = useMutation({
    mutationFn: (id: string) => rerunHistory(id, false),
    onSuccess: (answer) => navigate(backtestHref({ run: answer.run_id })),
    onError: (exc) => setProblem(refusal(exc).message),
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteHistory(id),
    onSuccess: async () => {
      setDeleting(null)
      toast.success('已删除这条回测记录')
      await client.invalidateQueries({ queryKey: HISTORY_KEY })
    },
    onError: (exc) => setProblem(refusal(exc).message),
  })

  const columns: Column<HistoryView>[] = [
    {
      key: 'instrument', header: '标的', sortValue: (row) => row.instrument,
      render: (row) => (
        <Link to={backtestHref({ run: row.id })} className="font-medium text-fg hover:underline">
          <span className={numeric}>{row.instrument}</span>
        </Link>
      ),
    },
    { key: 'strategy', header: '方法', render: (row) => row.strategy },
    { key: 'period', header: '区间', hideBelow: 'md', render: (row) => <span className={numeric}>{row.period}</span> },
    {
      key: 'result', header: '收益', numeric: true,
      render: (row) => (row.result === null ? <span className="text-fg-3">不可用</span> : <span className={pnlTextClass(row.resultTone)}>{row.result}</span>),
    },
    { key: 'date', header: '回测时间', hideBelow: 'sm', render: (row) => <span className={cx('text-fg-2', numeric)}>{row.date}</span>, sortValue: (row) => row.date },
  ]

  if (history.isSuccess && !kept) {
    return (
      <EmptyState
        title="演示模式不保存回测"
        description="演示用的是程序生成的虚构行情，每次回测只在打开它的页面里查看，不写入历史；正常模式下每次回测都会保存在这里。"
      />
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {history.isError && !history.isFetchNextPageError && (
        <InlineAlert tone="warn" title="读不到回测历史" action={<Button size="sm" onClick={() => void history.refetch()}>重试</Button>}>
          {refusal(history.error).message}
        </InlineAlert>
      )}
      {problem && <InlineAlert tone="warn" action={<Button size="sm" variant="ghost" onClick={() => setProblem(null)}>知道了</Button>}>{problem}</InlineAlert>}
      <DataTable
        caption="回测历史"
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        loading={history.isPending}
        mobile="cards"
        onRowClick={(row) => navigate(backtestHref({ run: row.id }))}
        renderRowExtra={(row) => (row.headline ? <p className="whitespace-normal break-words text-table text-fg-2">{row.headline}</p> : null)}
        rowActions={(row) => (
          <>
            <Button size="sm" variant="ghost" icon={RotateCcw} disabledReason={readOnlyReason ?? row.rerunBlocked ?? undefined} loading={rerun.isPending && rerun.variables === row.id}
              aria-label={`重跑 ${row.instrument} ${row.strategy}`} onClick={() => rerun.mutate(row.id)}>
              重跑
            </Button>
            <a href={exportUrl(row.id, 'csv')} download className={buttonClass({ size: 'sm', variant: 'ghost' })} aria-label={`导出 ${row.instrument} ${row.strategy} 的 CSV`}>
              <Download size={14} aria-hidden />导出
            </a>
            <Button size="sm" variant="ghost" icon={Trash2} disabledReason={readOnlyReason} tipAlign="end"
              aria-label={`删除 ${row.instrument} ${row.strategy} 的记录`} onClick={() => setDeleting(row)}>
              删除
            </Button>
          </>
        )}
        empty={<EmptyState title="还没有保存的回测" description="在快速回测里跑一次，结果会自动保存在这里，可以随时打开、重跑或导出。" />}
      />
      {rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 text-table text-fg-3">
          <span className={numeric}>已列出 {rows.length} 条{total !== null ? `，共 ${total} 条` : ''}</span>
          {history.hasNextPage && (
            <Button size="sm" loading={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>
              加载更多（每次 {HISTORY_PAGE} 条）
            </Button>
          )}
        </div>
      )}
      {history.isFetchNextPageError && (
        <InlineAlert tone="warn" title="读不到更早的回测" action={<Button size="sm" onClick={() => void history.fetchNextPage()}>重试</Button>}>
          {refusal(history.error).message}
        </InlineAlert>
      )}
      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        size="sm"
        title="删除这条回测记录？"
        description={deleting ? `${deleting.instrument} · ${deleting.strategy} · ${deleting.period}` : undefined}
        footer={(
          <>
            <Button data-autofocus onClick={() => setDeleting(null)}>取消</Button>
            <Button variant="warn-outline" loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting.id)}>删除</Button>
          </>
        )}
      >
        <p className="text-body text-fg-2">删除后不能恢复：这次回测的结果、成交和运行清单都会从历史里移除。</p>
      </Dialog>
    </div>
  )
}
