// The import page's messages: the backend's blockers in its own words, and a refused request with the one
// button that helps (重试, 重新载入, 重新生成入账预览, 回到预览, 重新开始).
import { AlertTriangle } from 'lucide-react'
import { Button, InlineAlert } from '@/components/ui'
import { NEXT_LABEL } from '@/features/import/errors'
import type { FlowFailure } from './useImport'

export function BlockerList({ blockers, title }: { blockers: string[]; title: string }) {
  if (!blockers.length) return null
  return (
    <section aria-label={title} className="flex flex-col gap-1 rounded-md bg-warn/10 px-3 py-2">
      <p className="text-body font-medium text-fg">{title}</p>
      <ul className="flex flex-col gap-1 text-table text-fg">
        {blockers.map((blocker) => (
          <li key={blocker} className="flex items-start gap-1.5">
            <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0 text-warn" />
            <span className="min-w-0 break-words">{blocker}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export interface FailureAlertProps {
  failure: FlowFailure
  onReload?: () => void
  onAgain?: () => void
  onBack?: () => void
  onRestart?: () => void
  className?: string
}

export function FailureAlert({ failure, onReload, onAgain, onBack, onRestart, className }: FailureAlertProps) {
  const handlers = { retry: failure.retry, reload: onReload, again: onAgain, back: onBack, restart: onRestart, none: undefined }
  const handler = handlers[failure.next]
  const label = NEXT_LABEL[failure.next]
  return (
    <InlineAlert
      tone="warn"
      title={failure.title}
      className={className}
      action={handler && label ? <Button size="sm" onClick={handler}>{label}</Button> : undefined}
    >
      {failure.message}
    </InlineAlert>
  )
}
