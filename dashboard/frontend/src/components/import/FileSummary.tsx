// "识别为 同花顺交割单 · GBK 制表符分隔 · 214 行 · 2026-10-01 至 2026-11-14 · 预设未经你的券商文件验证": what the
// backend recognised the file as, in its words. Compact on the preview step.
import { AlertTriangle } from 'lucide-react'
import { Badge, Button, cx, numeric } from '@/components/ui'
import type { ImportSession } from '@/features/import/types'

const DELIMITERS: Record<string, string> = { tab: '制表符', comma: '逗号', semicolon: '分号', pipe: '竖线', whitespace: '空格' }

export interface FileSummaryProps {
  session: ImportSession
  compact?: boolean
  /** 修改列对应 (the preview step links back to 核对列). */
  onEditMapping?: () => void
  className?: string
}

export function FileSummary({ session, compact = false, onEditMapping, className }: FileSummaryProps) {
  const { format, preset, counts, date_range: range } = session
  const facts = [
    format.encoding && `编码 ${format.encoding}`,
    format.delimiter && `分隔符 ${DELIMITERS[format.delimiter] ?? format.delimiter}`,
    format.sheet && `工作表“${format.sheet}”`,
    `${counts.total ?? 0} 行`,
    range ? `${range.from} 至 ${range.to}` : '没有日期',
  ].filter(Boolean) as string[]
  const unverified = preset.note ? (
    <Badge tone="warn" icon={AlertTriangle} title="预设按常见列名编写，没有用你的券商的真实文件核对过；请逐列核对">{preset.note}</Badge>
  ) : null

  if (compact) {
    return (
      <div className={cx('flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-2', numeric, className)}>
        <span>识别为 <span className="font-medium text-fg">{preset.label}</span>（{session.statement_label}）</span>
        <span className="text-fg-3">·</span>
        <span>{facts.join(' · ')}</span>
        {unverified}
        {onEditMapping && <Button size="sm" variant="ghost" onClick={onEditMapping}>修改列对应</Button>}
      </div>
    )
  }
  return (
    <dl className={cx('grid gap-x-6 gap-y-2 text-table sm:grid-cols-[auto_1fr]', numeric, className)}>
      <dt className="text-fg-3">识别为</dt>
      <dd className="flex min-w-0 flex-wrap items-center gap-2 text-fg">
        <span className="text-body font-medium">{preset.label}</span>
        <span className="text-fg-2">{session.statement_label}</span>
        {unverified}
      </dd>
      <dt className="text-fg-3">格式</dt>
      <dd className="min-w-0 break-words text-fg-2">{format.label} · {facts.join(' · ')}</dd>
      <dt className="text-fg-3">文件</dt>
      <dd className="min-w-0 break-words text-fg-2">
        {session.file.name || '（无文件名）'} · 导入到账户“{session.account}”{session.account_exists ? '' : '（新账户）'}
      </dd>
      {format.title_lines.length > 0 && (
        <>
          <dt className="text-fg-3">表头上方</dt>
          <dd className="min-w-0 break-words text-fg-3">{format.title_lines.slice(0, 3).join(' / ')}</dd>
        </>
      )}
    </dl>
  )
}
