// What a rerun of a saved run found: the same data? the same strategy? (相同 / 不同 / 无法核对), whether the
// figures came out identical, and the reasons in plain words with the service's own sentence under each.
import { Link } from 'react-router-dom'
import { Badge, cx, focusRing, numeric } from '@/components/ui'
import { backtestHref } from '@/features/backtest/model'
import { rerunFindings, triState } from '@/features/backtest/result'
import type { RunResult } from '@/features/backtest/types'

export function RerunPanel({ result }: { result: RunResult }) {
  const rerun = result.rerun
  if (!rerun) return null
  const data = triState(rerun.same_data)
  const strategy = triState(rerun.same_strategy)
  const changed = Object.keys(rerun.changed_metrics ?? {}).length
  const findings = rerunFindings(result)
  return (
    <section aria-label="重跑核对" className="flex min-w-0 flex-col gap-3 rounded-lg border border-line p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-title text-fg">重跑核对</h3>
        {rerun.of && (
          <Link to={backtestHref({ run: rerun.of })} className={cx('rounded-sm text-table text-accent hover:underline', focusRing)}>
            打开原来的记录
          </Link>
        )}
      </div>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-table text-fg-3">行情数据和保存时</dt>
          <dd className="mt-1" data-check="same-data"><Badge tone={data.tone}>{data.text}</Badge></dd>
        </div>
        <div>
          <dt className="text-table text-fg-3">策略和保存时</dt>
          <dd className="mt-1" data-check="same-strategy"><Badge tone={strategy.tone}>{strategy.text}</Badge></dd>
        </div>
        <div>
          <dt className="text-table text-fg-3">结果数字</dt>
          <dd className={cx('mt-1', numeric)}>
            {rerun.metrics_identical ? <Badge>和原记录完全相同</Badge> : <Badge tone="warn">{changed ? `${changed} 项和原记录不同` : '无法比较'}</Badge>}
          </dd>
        </div>
      </dl>
      {findings.length > 0 && (
        <ul className="flex flex-col gap-2">
          {findings.map((item) => (
            <li key={`${item.code}-${item.text}`} className="flex flex-col gap-0.5">
              <span className={cx('text-body font-medium', item.tone === 'warn' ? 'text-warn' : 'text-fg')}>{item.title}</span>
              <span className="text-table text-fg-2">{item.text}</span>
            </li>
          ))}
        </ul>
      )}
      {findings.length === 0 && rerun.same_data === true && rerun.same_strategy === true && (
        <p className="text-table text-fg-2">数据、策略和设置都和保存时一样。</p>
      )}
    </section>
  )
}
