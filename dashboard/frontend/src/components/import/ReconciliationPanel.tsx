// 对账: cash and positions after the import (by the ledger, as if this preview were confirmed) against the
// broker's own balances in the file. Each line is 一致, 不一致 with both figures and the difference, or 未核对
// with the reason. A mismatch is amber and must be acknowledged before a batch can be proposed.
import { Check } from 'lucide-react'
import { Badge, Checkbox, InlineAlert, cx, numeric } from '@/components/ui'
import { reconLines, reconNeedsAck, reconTally, type ReconLine } from '@/features/import/reconcile'
import type { Reconciliation } from '@/features/import/types'

function State({ line }: { line: ReconLine }) {
  if (line.state === 'ok') return <Badge icon={Check}>一致</Badge>
  if (line.state === 'unchecked') return <Badge>未核对</Badge>
  return <Badge tone="warn">{line.stateText}</Badge>
}

export interface ReconciliationPanelProps {
  recon: Reconciliation | null
  acknowledged: boolean
  onAcknowledge: (value: boolean) => void
  disabled?: boolean
}

export function ReconciliationPanel({ recon, acknowledged, onAcknowledge, disabled }: ReconciliationPanelProps) {
  if (!recon) return null
  if (!recon.available) {
    return (
      <section aria-label="对账" className="flex flex-col gap-1">
        <h3 className="text-body font-medium text-fg">对账</h3>
        <p className="text-table text-fg-2"><Badge>未核对</Badge> <span className="ml-1">{recon.reason}</span></p>
      </section>
    )
  }
  const lines = reconLines(recon)
  const mismatch = reconNeedsAck(recon)
  return (
    <section aria-label="对账" className={cx('flex flex-col gap-3 rounded-md border px-3 py-3', mismatch ? 'border-warn/60' : 'border-line')}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className={cx('text-body font-medium', mismatch ? 'text-warn' : 'text-fg')}>对账{mismatch ? '：有不一致' : ''}</h3>
        <span className={cx('text-table text-fg-3', numeric)}>
          {recon.as_of ? `按文件最后一天 ${recon.as_of} · ` : ''}{reconTally(lines)}
        </span>
      </div>
      <p className={cx('break-words text-table text-fg-2', numeric)}>{recon.summary}</p>
      {/* A phone gets one block per line: four columns of 12-digit figures do not fit 390 px. */}
      <ul className="flex flex-col sm:hidden">
        {lines.map((line) => (
          <li key={line.key} className={cx('flex flex-col gap-1 border-b border-line py-2 text-table', line.state === 'mismatch' && 'text-warn')}>
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 break-words text-fg">{line.label}</span>
              <State line={line} />
            </div>
            <dl className={cx('grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5', numeric)}>
              <dt className="text-fg-3">导入后（账本）</dt>
              <dd className="break-words text-right">{line.ours ?? '不可用'}</dd>
              <dt className="text-fg-3">券商余额（文件）</dt>
              <dd className="break-words text-right">{line.theirs ?? '不可用'}</dd>
            </dl>
            {line.detail && <p className={cx('break-words', numeric, line.state === 'mismatch' ? 'text-warn' : 'text-fg-3')}>{line.detail}</p>}
          </li>
        ))}
      </ul>
      <div className="min-w-0 overflow-x-auto max-sm:hidden">
        <table className="w-full min-w-[30rem] border-separate border-spacing-0 text-table">
          <caption className="sr-only">对账</caption>
          <thead>
            <tr className="text-caption text-fg-3">
              <th scope="col" className="border-b border-line py-1.5 pr-3 text-left font-medium">项目</th>
              <th scope="col" className="border-b border-line px-3 py-1.5 text-right font-medium">导入后（账本）</th>
              <th scope="col" className="border-b border-line px-3 py-1.5 text-right font-medium">券商余额（文件）</th>
              <th scope="col" className="border-b border-line py-1.5 pl-3 text-left font-medium">结果</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.key} className={cx(line.state === 'mismatch' && 'text-warn')}>
                <td className="border-b border-line py-1.5 pr-3 text-fg">{line.label}</td>
                <td className={cx('border-b border-line px-3 py-1.5 text-right', numeric)}>{line.ours ?? '不可用'}</td>
                <td className={cx('border-b border-line px-3 py-1.5 text-right', numeric)}>{line.theirs ?? '不可用'}</td>
                <td className="border-b border-line py-1.5 pl-3">
                  <span className="inline-flex flex-wrap items-center gap-1.5">
                    <State line={line} />
                    {line.detail && <span className={cx(numeric, line.state === 'mismatch' ? 'text-warn' : 'text-fg-3')}>{line.detail}</span>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mismatch && recon.causes.length > 0 && (
        <div className="flex flex-col gap-1 text-table">
          <p className="text-fg-2">可能的原因</p>
          <ul className="flex list-disc flex-col gap-0.5 pl-4 text-fg">
            {recon.causes.map((cause) => <li key={cause} className="break-words">{cause}</li>)}
          </ul>
        </div>
      )}
      {mismatch && (
        <InlineAlert tone="warn">
          <Checkbox
            label="我已核对这些差异，仍要生成入账预览"
            description="不一致说明导入后账本与券商余额对不上；先处理需处理的行或核对期初，通常就能一致。"
            checked={acknowledged}
            disabled={disabled}
            onChange={(event) => onAcknowledge(event.target.checked)}
          />
        </InlineAlert>
      )}
    </section>
  )
}
