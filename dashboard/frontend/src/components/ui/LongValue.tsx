import type { Shown } from '@/lib/format'

export interface LongValueProps {
  /** A figure or a line from `fmtLong` / `longLine` (lib/format.ts), or plain text. */
  value: Shown | string
  className?: string
}

/**
 * A figure that may have been rounded for display: the rounded text ("≈32.01666667") on the page, the full value
 * on hover (title) and for screen readers ("约 32.01666667，完整值 32.016666666666666666666666667"). A figure that
 * was not rounded is plain text.
 */
export function LongValue({ value, className }: LongValueProps) {
  const shown = typeof value === 'string' ? { text: value } : value
  if (!shown.full) return className ? <span className={className}>{shown.text}</span> : <>{shown.text}</>
  return (
    <span title={shown.full} className={className}>
      <span aria-hidden="true">{shown.text}</span>
      <span className="sr-only">{shown.label ?? shown.full}</span>
    </span>
  )
}
