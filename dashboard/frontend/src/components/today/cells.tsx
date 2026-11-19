// Small renderers shared by the Today components: native-currency money, signed colour, 不可用.
import type { ReactNode } from 'react'
import { UNAVAILABLE, fmtMoney, fmtNumber, toneOf } from '@/lib/format'
import { pnlTextClass } from '@/lib/pnlColor'

/** "不可用" in the muted colour: the value is unknown, never 0. */
export const Unavailable = ({ title }: { title?: string }) => <span title={title} className="text-fg-3">{UNAVAILABLE}</span>

/**
 * Native currency in cells: CNY as a plain number, other currencies with their symbol; every digit the
 * backend sent (at least two).
 */
export function money(value: string | null | undefined, currency: string, signed = false, title?: string): ReactNode {
  if (value === null || value === undefined) return <Unavailable title={title} />
  return currency === 'CNY' ? fmtNumber(value, { signed, exact: true }) : fmtMoney(value, currency, { signed, exact: true })
}

/** A signed figure coloured by direction (red up, green down); null stays 不可用. */
export function signed(text: ReactNode, value: string | null | undefined, title?: string): ReactNode {
  if (value === null || value === undefined) return <Unavailable title={title} />
  return <span className={pnlTextClass(toneOf(value))}>{text}</span>
}

/** Separator dot for one-line lists. */
export const Dot = () => <span aria-hidden className="text-fg-3">·</span>
