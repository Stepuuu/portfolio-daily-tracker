// One checklist item as the backend states it: its label, a state chip, the one-sentence detail and what to
// do: a link inside the app, or the exact line to put in the environment (things configured outside the
// UI). The detail is rendered as text. Used by the setup wizard (行情与提醒, 完成) and by 设置 (数据与行情,
// 自动化与通知), which show the same items. `EnvHowTo` says where such lines go.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Copy } from 'lucide-react'
import { Badge, IconButton, cx, focusRing, type BadgeTone } from '@/components/ui'
import { STATE_LABELS, actionHref } from '@/features/setup/steps'
import type { ItemState, SetupAction, SetupItem } from '@/features/setup/types'

const TONE: Record<ItemState, BadgeTone> = { done: 'accent', todo: 'warn', optional: 'neutral', unavailable: 'neutral' }

export function StateChip({ state }: { state: ItemState }) {
  return <Badge tone={TONE[state]}>{STATE_LABELS[state]}</Badge>
}

/** A line to copy into `.env` or a shell, with a copy button. */
export function CommandLine({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-table text-fg-3">{label}</span>
      <div className="flex min-w-0 items-start gap-1 rounded-md bg-surface-2 px-2 py-1">
        <code className="min-w-0 flex-1 whitespace-pre-wrap break-words py-0.5 text-table text-fg">{text}</code>
        <IconButton icon={copied ? Check : Copy} label={copied ? '已复制' : '复制这一行'} size="sm" onClick={() => void copy()} />
      </div>
    </div>
  )
}

export function ActionLine({ action }: { action: SetupAction }) {
  if (action.command) return <CommandLine text={action.command} label={action.label} />
  const href = actionHref(action.href)
  if (!href) return null
  return (
    <Link to={href} className={cx('w-fit rounded-sm text-table text-accent underline-offset-4 hover:underline', focusRing)}>
      {action.label}
    </Link>
  )
}

export interface ChecklistProps {
  items: SetupItem[]
  /** The list's accessible name. */
  label: string
  /** Hide links that lead to the step already shown. */
  hideActions?: (item: SetupItem) => boolean
  /** Inside a panel titled after the item: no item heading, the state chip starts the detail. */
  compact?: boolean
  className?: string
}

export function Checklist({ items, label, hideActions, compact = false, className }: ChecklistProps) {
  return (
    <ul aria-label={label} className={cx('flex flex-col divide-y divide-line', className)}>
      {items.map((item) => (
        <li key={item.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0">
          {compact ? (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-2">
              <StateChip state={item.state} />
              <span className="min-w-0 break-words">{item.detail}</span>
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                <span className="font-medium text-fg">{item.label}</span>
                <StateChip state={item.state} />
              </div>
              <p className="break-words text-table text-fg-2">{item.detail}</p>
            </>
          )}
          {item.action && !hideActions?.(item) && <ActionLine action={item.action} />}
        </li>
      ))}
    </ul>
  )
}

/** Where the environment lines above go and how they take effect (they are read when the backend starts). */
export function EnvHowTo() {
  return (
    <div className="flex flex-col gap-3 text-table text-fg-2">
      <p>把需要的行写进仓库根目录的 .env 文件（每行一个，没有就新建），然后重启后端：</p>
      <CommandLine label="重启后端" text="make stop && make start" />
      <p>或者只在这一次启动时生效：把这些行放在启动命令前面，例如 TRACKER_PREMARKET_TIME=08:30 make start。</p>
    </div>
  )
}
