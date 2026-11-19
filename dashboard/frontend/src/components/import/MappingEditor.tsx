// 核对列's controls: a column conflict to settle (two columns that could feed one field and differ, including
// the action, market and 股/手 quantity columns, or a fee column listed twice), and the full column mapping.
// Every choice is a PATCH `mapping`, which the backend remembers for this account and header layout.
import { useId, useMemo, useState } from 'react'
import { Check } from 'lucide-react'
import { Badge, Button, Checkbox, Field, Select, cx, numeric } from '@/components/ui'
import {
  columnIndex, columnName, columnOptions, conflictText, feesChange, isMapped, mappedPairs, mappingChange,
} from '@/features/import/flow'
import type { ColumnConflict, ImportPatch, ImportRow, ImportSession } from '@/features/import/types'

type Patch = (body: ImportPatch) => void

/** Up to three of the conflict's rows that are on the loaded page, with each column's value. */
function examples(conflict: ColumnConflict, sample: ImportRow[]): ImportRow[] {
  const wanted = new Set(conflict.rows)
  return sample.filter((row) => wanted.has(row.row)).slice(0, 3)
}

export interface ConflictChoiceProps {
  conflict: ColumnConflict
  session: ImportSession
  sample: ImportRow[]
  onPatch: Patch
  busy: boolean
  disabledReason?: string
}

export function ConflictChoice({ conflict, session, sample, onPatch, busy, disabledReason }: ConflictChoiceProps) {
  const name = useId()
  const fees = Array.isArray(session.mapping.fees) ? session.mapping.fees : []
  const [column, setColumn] = useState<number>(conflict.chosen ?? conflict.columns[0])
  const [kept, setKept] = useState<number[]>(() => conflict.columns.filter((index) => fees.includes(index)))
  const rows = useMemo(() => examples(conflict, sample), [conflict, sample])
  const feeConflict = conflict.chosen === null

  return (
    <div
      role="group"
      aria-label={`列冲突：${conflict.label}`}
      className={cx('flex flex-col gap-3 rounded-md border px-3 py-3', conflict.resolved ? 'border-line' : 'border-warn/60')}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body font-medium text-fg">{conflict.label}</span>
        {conflict.resolved ? <Badge icon={Check}>已确认</Badge> : <Badge tone="warn">需要选择</Badge>}
      </div>
      <p className="text-table text-fg-2">{conflictText(conflict, session.columns)}</p>
      {feeConflict ? (
        <div className="flex flex-col gap-2">
          {conflict.columns.map((index) => (
            <Checkbox
              key={index}
              label={columnName(session.columns, index)}
              checked={kept.includes(index)}
              disabled={busy || Boolean(disabledReason)}
              onChange={(event) => setKept((current) => (event.target.checked ? [...current, index] : current.filter((item) => item !== index)))}
            />
          ))}
          <Button
            size="sm"
            variant={conflict.resolved ? 'secondary' : 'primary'}
            className="self-start"
            loading={busy}
            disabledReason={disabledReason}
            onClick={() => onPatch({ mapping: feesChange(session.mapping, [...fees.filter((index) => !conflict.columns.includes(index)), ...kept]) })}
          >
            确认费用列
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <div role="radiogroup" aria-label={`“${conflict.label}”用哪一列`} className="flex flex-col gap-1.5">
            {conflict.columns.map((index) => (
              <label key={index} className={cx('flex min-w-0 cursor-pointer items-start gap-2 text-body', (busy || disabledReason) && 'cursor-not-allowed')}>
                <input
                  type="radio"
                  name={name}
                  className="mt-[5px] h-4 w-4 shrink-0 accent-accent"
                  checked={column === index}
                  disabled={busy || Boolean(disabledReason)}
                  onChange={() => setColumn(index)}
                />
                <span className="min-w-0">
                  <span className="text-fg">{columnName(session.columns, index)}</span>
                  {conflict.chosen === index && <span className="ml-2 text-table text-fg-3">现在使用</span>}
                  {rows.length > 0 && (
                    <span className={cx('block text-table text-fg-3', numeric)}>
                      {rows.map((row) => `第 ${row.row} 行：${row.cells[index] ?? ''}`).join(' · ')}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </div>
          <Button
            size="sm"
            variant={conflict.resolved ? 'secondary' : 'primary'}
            className="self-start"
            loading={busy}
            disabledReason={disabledReason}
            onClick={() => onPatch({ mapping: mappingChange(session.mapping, conflict.field, column) })}
          >
            用这一列
          </Button>
        </div>
      )}
    </div>
  )
}

export interface MappingEditorProps {
  session: ImportSession
  sample: ImportRow[]
  onPatch: Patch
  busy: boolean
  disabledReason?: string
  /** Required fields the person said the file does not have. */
  acknowledged: string[]
  onAcknowledge: (field: string) => void
}

const NONE = ''

/** The mapping as "表头 → 字段" chips, the required fields without a column, and every field's column on request. */
export function MappingEditor({ session, sample, onPatch, busy, disabledReason, acknowledged, onAcknowledge }: MappingEditorProps) {
  const [open, setOpen] = useState(false)
  const pairs = mappedPairs(session)
  const options = useMemo(() => columnOptions(session.columns, sample[0]), [session.columns, sample])
  const missing = session.fields.filter((field) => field.required && !isMapped(session.mapping, field.field))
  const fees = Array.isArray(session.mapping.fees) ? session.mapping.fees : []
  const blocked = busy || Boolean(disabledReason)

  return (
    <div className="flex flex-col gap-3">
      <ul aria-label="列对应" className="flex flex-wrap gap-x-4 gap-y-1.5 text-table">
        {pairs.map((pair) => (
          <li key={pair.key} className="inline-flex items-center gap-1 text-fg-2">
            <span className="text-fg">{pair.from}</span>
            <span aria-hidden className="text-fg-3">→</span>
            <span>{pair.to}</span>
            <Check size={12} aria-label="已对应" className="text-accent" />
          </li>
        ))}
      </ul>

      {missing.map((field) => {
        const said = acknowledged.includes(field.field)
        return (
          <div key={field.field} className={cx('flex flex-wrap items-end gap-2 rounded-md px-3 py-2', said ? 'bg-fg/[.05]' : 'bg-warn/10')}>
            <Field label={`缺少必需的列：${field.label}`} className="min-w-[14rem] flex-1" hint={said ? '你已确认文件没有这一列；依赖它的行会留在需处理' : undefined}>
              <Select
                size="sm"
                value={NONE}
                disabled={blocked}
                onChange={(event) => onPatch({ mapping: mappingChange(session.mapping, field.field, columnIndex(event.target.value)) })}
                options={[{ value: NONE, label: '选择对应的列…' }, ...options]}
              />
            </Field>
            {!said && (
              <Button size="sm" variant="ghost" disabled={blocked} onClick={() => onAcknowledge(field.field)}>文件没有这一列</Button>
            )}
          </div>
        )
      })}

      <Button size="sm" variant="ghost" className="self-start" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {open ? '收起列对应' : '修改列对应'}
      </Button>
      {open && (
        <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          {session.fields.filter((field) => field.field !== 'fees').map((field) => {
            const value = session.mapping[field.field]
            return (
              <Field key={field.field} label={field.label} required={field.required}>
                <Select
                  size="sm"
                  value={typeof value === 'number' ? String(value) : NONE}
                  disabled={blocked}
                  onChange={(event) => onPatch({ mapping: mappingChange(session.mapping, field.field, columnIndex(event.target.value)) })}
                  options={[{ value: NONE, label: '（不使用）' }, ...options]}
                />
              </Field>
            )
          })}
          {session.fields.some((field) => field.field === 'fees') && (
            <fieldset className="flex min-w-0 flex-col gap-1.5 sm:col-span-2 lg:col-span-3">
              <legend className="mb-1 text-table text-fg-2">费用列（可多选，合计为费用）</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                {session.columns.map((column) => (
                  <Checkbox
                    key={column.index}
                    label={<span className="text-table">{column.header || `第 ${column.index + 1} 列`}</span>}
                    checked={fees.includes(column.index)}
                    disabled={blocked}
                    onChange={(event) => onPatch({
                      mapping: feesChange(session.mapping, event.target.checked ? [...fees, column.index] : fees.filter((index) => index !== column.index)),
                    })}
                  />
                ))}
              </div>
            </fieldset>
          )}
        </div>
      )}
    </div>
  )
}
