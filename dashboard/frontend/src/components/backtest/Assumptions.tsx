// 假设与数据: what the run assumed (lot rule, commission and the dated fee line as the service wrote it,
// slippage, adjustment, warm-up) and the bars it used (source, dates, completeness, hashes).
import type { ReactNode } from 'react'
import { Badge, cx, numeric } from '@/components/ui'
import { UNAVAILABLE, fmtQty } from '@/lib/format'
import { adjustFact, commissionText, coverageRows, lotText, moneyText, pctText } from '@/features/backtest/result'
import type { RunResult } from '@/features/backtest/types'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-x-4 gap-y-0.5 sm:grid-cols-[8rem_minmax(0,1fr)]">
      <dt className="text-table text-fg-3">{label}</dt>
      <dd className="min-w-0 break-words text-body text-fg">{children}</dd>
    </div>
  )
}

function Hash({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-fg-3">{UNAVAILABLE}</span>
  return <code title={value} className={cx('break-all text-table text-fg-2', numeric)}>{value}</code>
}

export function Assumptions({ result }: { result: RunResult }) {
  const facts = result.facts
  const manifest = result.manifest ?? {}
  const slippage = typeof facts.slippage_pct === 'number' ? facts.slippage_pct : result.assumptions?.slippage_pct
  const warmup = typeof facts.warmup_bars === 'number' ? `${fmtQty(facts.warmup_bars)} 根 K 线${facts.warmup_auto ? '（按方法自动取）' : ''}，在开始日期之前，不计入收益` : UNAVAILABLE
  const coverage = coverageRows(result)
  return (
    <section aria-label="假设与数据" className="flex min-w-0 flex-col gap-4">
      <h3 className="text-title text-fg">假设与数据</h3>
      <dl className="flex min-w-0 flex-col gap-3">
        <Row label="费用与成交">{result.assumptions?.line_zh ?? UNAVAILABLE}</Row>
        <Row label="佣金">{commissionText(facts)}</Row>
        <Row label="每手股数">{lotText(facts.lot)}</Row>
        <Row label="滑点">{typeof slippage === 'number' ? `每边 ${pctText(slippage, { signed: false })}` : UNAVAILABLE}</Row>
        <Row label="复权">{adjustFact(facts.adjust)}</Row>
        <Row label="预热">{warmup}</Row>
        <Row label="本金"><span className={numeric}>{moneyText(facts.capital, facts.currency)}</span></Row>
      </dl>
      <div className="flex min-w-0 flex-col gap-3">
        <h4 className="text-body font-medium text-fg-2">用到的行情</h4>
        {coverage.length === 0 && <p className="text-table text-fg-3">这次结果没有记录行情来源（{UNAVAILABLE}）。</p>}
        {coverage.map((item) => (
          <dl key={`${item.ticker}-${item.role}`} className="flex min-w-0 flex-col gap-2 border-l-2 border-line pl-3">
            <Row label={item.role}><span className={numeric}>{item.ticker}</span> · {item.adjust}</Row>
            <Row label="来源">{item.source}</Row>
            <Row label="日期">
              <span className={numeric}>{item.range}</span>
              <span className="text-fg-3"> · {item.rows}</span>
            </Row>
            <Row label="完整性">
              {item.completeTone === 'warn' ? <Badge tone="warn">{item.complete}</Badge> : item.complete}
              {item.problems.map((text) => <span key={text} className="mt-1 block text-table text-warn">{text}</span>)}
              {item.notes.map((text) => <span key={text} className="mt-1 block text-table text-fg-3">{text}</span>)}
            </Row>
            <Row label="下载时间"><span className={numeric}>{item.fetchedAt}</span></Row>
            <Row label="数据指纹"><Hash value={item.sha} /></Row>
          </dl>
        ))}
        <dl className="flex min-w-0 flex-col gap-2">
          <Row label="全部数据指纹"><Hash value={manifest.data_sha256} /></Row>
          <Row label="版本">
            <span className={cx('text-table text-fg-2', numeric)}>
              回测程序 {manifest.engine_version ?? UNAVAILABLE} · 税费表 {manifest.fee_schedule_version ?? UNAVAILABLE} · 指纹规则第 {manifest.data_hash_version ?? UNAVAILABLE} 版
            </span>
          </Row>
        </dl>
      </div>
    </section>
  )
}
