// The kind of entry: 买入 · 卖出 · 分红 · 入金 · 出金 as segments, everything else the ledger accepts under
// 更多 (换汇, 转账, 拆股/送转, 费用, 利息, the fund entries and a new account). A kind chosen from 更多 is shown
// on that button, so the choice is always visible.
import { ChevronDown } from 'lucide-react'
import { Button, Popover, SegmentedControl, cx, focusRing } from '@/components/ui'
import { LEDGER_KIND_LABEL, MENU_LABEL, MORE_KINDS, PRIMARY_KINDS, type EntryKind } from '@/features/ledger/kinds'

export interface KindPickerProps {
  value: EntryKind
  onChange: (kind: EntryKind) => void
  /** Kinds that cannot be chosen now (everything but 新账户 while there is no account). */
  disabled?: (kind: EntryKind) => boolean
}

export function KindPicker({ value, onChange, disabled }: KindPickerProps) {
  const primary = (PRIMARY_KINDS as string[]).includes(value)
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      <SegmentedControl<EntryKind | ''>
        ariaLabel="类型"
        value={primary ? value : ''}
        onChange={(kind) => kind && onChange(kind)}
        options={PRIMARY_KINDS.map((kind) => ({ value: kind, label: LEDGER_KIND_LABEL[kind], disabled: disabled?.(kind) }))}
      />
      <Popover
        label="更多类型"
        className="w-48 p-1"
        trigger={(
          <Button
            variant={primary ? 'ghost' : 'secondary'}
            className={cx(!primary && 'font-medium text-fg')}
            aria-label={primary ? '更多类型' : `类型：${LEDGER_KIND_LABEL[value]}，更多类型`}
          >
            {primary ? '更多' : LEDGER_KIND_LABEL[value]}
            <ChevronDown size={14} aria-hidden />
          </Button>
        )}
      >
        {(close) => (
          <div role="menu" aria-label="更多类型" className="flex flex-col">
            {MORE_KINDS.map((group, index) => (
              <div key={group.title ?? index} className={cx(index > 0 && 'mt-1 border-t border-line pt-1')}>
                {group.title && <p className="px-2 pb-0.5 pt-1 text-caption text-fg-3">{group.title}</p>}
                {group.kinds.map((kind) => {
                  const off = disabled?.(kind) ?? false
                  return (
                    <button
                      key={kind}
                      type="button"
                      role="menuitemradio"
                      aria-checked={kind === value}
                      disabled={off}
                      onClick={() => {
                        onChange(kind)
                        close()
                      }}
                      className={cx(
                        'flex h-8 w-full items-center rounded-md px-2 text-left text-body coarse:h-10',
                        kind === value ? 'bg-accent/10 font-medium text-accent' : 'text-fg hover:bg-surface-2',
                        'disabled:cursor-not-allowed disabled:text-fg-3 disabled:hover:bg-transparent',
                        focusRing, 'focus-visible:outline-offset-[-2px]',
                      )}
                    >
                      {MENU_LABEL[kind] ?? LEDGER_KIND_LABEL[kind]}
                    </button>
                  )
                })}
              </div>
            ))}
          </div>
        )}
      </Popover>
    </div>
  )
}
