// 策略文件: the Python strategies the service lists (GET /strategies): the built-in examples and, where the
// owner enabled them, custom files. Read-only: the source is shown as text, and 用它回测 opens 快速回测 with
// the strategy as the method. Adding one is a file on the server (described here), never an upload.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Code2, Play } from 'lucide-react'
import { Badge, Button, Dialog, EmptyState, InlineAlert, Skeleton, cx, numeric } from '@/components/ui'
import { fetchStrategySource, refusal, type StrategyInfo } from '@/services/backtest'
import { useStrategies } from './hooks'

function params(info: StrategyInfo): string {
  const entries = Object.entries(info.parameters ?? {})
  if (!entries.length) return '没有参数'
  return entries.map(([key, value]) => `${key} = ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`).join('，')
}

/** Built-in examples registered under two ids (rsi / rsi_mean_reversion) are listed once. */
function distinct(list: StrategyInfo[]): StrategyInfo[] {
  const seen = new Set<string>()
  return list.filter((item) => {
    const key = item.builtin ? `builtin:${item.class}` : `custom:${item.id}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function SourceDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const source = useQuery({ queryKey: ['backtest', 'strategy-source', id], queryFn: () => fetchStrategySource(id!), enabled: id !== null, retry: false })
  return (
    <Dialog open={id !== null} onClose={onClose} size="lg" title={source.data ? `${source.data.name} 的源码` : '策略源码'} description="只读；要修改请编辑服务器上的文件">
      {source.isPending ? <Skeleton lines={8} h={14} />
        : source.isError ? <InlineAlert tone="warn">{refusal(source.error).message}</InlineAlert>
        : (
          <pre className={cx('max-h-[60vh] overflow-auto whitespace-pre rounded-md bg-surface-2 p-3 text-table text-fg', numeric)}>
            {source.data?.source_code}
          </pre>
        )}
    </Dialog>
  )
}

function StrategyList({ items, onUse, onSource }: { items: StrategyInfo[]; onUse: (id: string) => void; onSource: (id: string) => void }) {
  return (
    <ul className="flex flex-col divide-y divide-line rounded-lg border border-line bg-surface">
      {items.map((item) => (
        <li key={item.id} className="flex flex-col gap-2 p-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-body font-medium text-fg">{item.name}</span>
              <code className="text-table text-fg-3">{item.id}</code>
              {!item.builtin && <Badge>自定义</Badge>}
            </div>
            {item.description && <p className="mt-1 whitespace-pre-line break-words text-table text-fg-2">{item.description.split('\n').slice(0, 3).join('\n')}</p>}
            <p className={cx('mt-1 break-words text-table text-fg-3', numeric)}>默认参数：{params(item)}</p>
            {item.file && <p className="mt-1 break-all text-caption text-fg-3">{item.file}</p>}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button size="sm" variant="ghost" icon={Code2} onClick={() => onSource(item.id)} aria-label={`查看 ${item.name} 的源码`}>源码</Button>
            <Button size="sm" icon={Play} onClick={() => onUse(item.id)} aria-label={`用 ${item.name} 回测`}>用它回测</Button>
          </div>
        </li>
      ))}
    </ul>
  )
}

export function StrategiesTab({ onUse }: { onUse: (id: string) => void }) {
  const strategies = useStrategies()
  const [viewing, setViewing] = useState<string | null>(null)
  const list = distinct(strategies.data?.strategies ?? [])
  const custom = list.filter((item) => !item.builtin)
  const builtin = list.filter((item) => item.builtin)
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <InlineAlert title="怎样加一个自己的策略">
        把继承 Strategy 的 Python 类写成一个 .py 文件，放进服务器的 dashboard/backtesting/strategies/custom/ 目录，
        并用 TRACKER_ALLOW_CUSTOM_STRATEGIES=1 启动服务（策略文件会执行 Python 代码，只在你信任的本机环境开启）。
        刷新后它会出现在下面，点“用它回测”在快速回测里运行；参数按策略里的名字填写。
      </InlineAlert>
      {strategies.isError && <InlineAlert tone="warn" title="读不到策略列表">{refusal(strategies.error).message}</InlineAlert>}
      {strategies.isPending && <Skeleton lines={4} h={14} />}
      {strategies.isSuccess && (
        <>
          <section aria-label="自定义策略文件" className="flex min-w-0 flex-col gap-2">
            <h2 className="text-title text-fg">自定义策略文件</h2>
            {custom.length ? <StrategyList items={custom} onUse={onUse} onSource={setViewing} /> : (
              <EmptyState title="没有自定义策略文件" description="服务没有加载任何自定义策略：目录里没有文件，或者没有开启 TRACKER_ALLOW_CUSTOM_STRATEGIES。" />
            )}
          </section>
          <section aria-label="内置示例策略" className="flex min-w-0 flex-col gap-2">
            <h2 className="text-title text-fg">内置示例策略</h2>
            <p className="text-table text-fg-3">写在代码里的示例，参数是英文名；日常检验更推荐快速回测里的方法。</p>
            <StrategyList items={builtin} onUse={onUse} onSource={setViewing} />
          </section>
        </>
      )}
      <SourceDialog id={viewing} onClose={() => setViewing(null)} />
    </div>
  )
}
