// 处理第 N 行: the fields the backend lists in the row's `needs`, and nothing else (any other edit is refused
// with 422). The file's own values and the backend's reason are shown above; a refused edit keeps the
// dialog open with the backend's words (422), 重试 (503) or 重新载入 (409).
import { useState } from 'react'
import { ArrowRight, SkipForward } from 'lucide-react'
import {
  Button, Checkbox, DateInput, Dialog, Field, InlineAlert, LongValue, MoneyInput, NumberInput, Select, cx, numeric, toast,
} from '@/components/ui'
import { longLine } from '@/lib/format'
import { TickerInput } from '@/components/inputs/TickerInput'
import { kindLabel } from '@/features/ledger/kinds'
import {
  EDIT_CURRENCIES, editKinds, editSpecs, initialEditValues, rowEditPayload, skipEdit, type EditSpec, type EditValues,
} from '@/features/import/edits'
import { rowExplanation, rowLines } from '@/features/import/rows'
import type { ImportColumn, ImportPatch, ImportRow, Statement } from '@/features/import/types'
import { FailureAlert } from './Notices'
import type { FlowFailure } from './useImport'

export interface RowEditDialogProps {
  row: ImportRow | null
  statement: Statement
  /** The file's columns: a template row is shown as its cells (the backend reads them only as a whole). */
  columns?: ImportColumn[]
  onClose: () => void
  /** PATCH that returns its refusal instead of showing it on the page. */
  patch: (body: ImportPatch) => Promise<FlowFailure | null>
  busy: boolean
  disabledReason?: string
  /** 409: read the session again (the dialog closes). */
  onReload: () => void
  /** A need settled on 核对列 (mapping, account). */
  onGoToMapping: () => void
}

export function RowEditDialog(props: RowEditDialogProps) {
  // One editor per row: its values and refusal start fresh, and closing it unmounts the dialog (focus returns).
  return props.row ? <RowEditor key={props.row.row} {...props} row={props.row} /> : null
}

function RowEditor({ row, statement, columns = [], onClose, patch, busy, disabledReason, onReload, onGoToMapping }: RowEditDialogProps & { row: ImportRow }) {
  const [values, setValues] = useState<EditValues>(() => initialEditValues(row))
  const [failure, setFailure] = useState<FlowFailure | null>(null)
  const [error, setError] = useState<string | null>(null)
  const specs = editSpecs(row)
  const { reasons, notes } = rowExplanation(row)
  const line = rowLines(row, statement)[0]
  const fillable = specs.some((spec) => !['mapping', 'account', 'other'].includes(spec.control))
  const toMapping = specs.some((spec) => spec.control === 'mapping' || spec.control === 'account')
  const update = (need: string, value: string | boolean) => {
    setError(null)
    setValues((current) => ({ ...current, [need]: value }))
  }
  const send = async (body: ImportPatch, done: string) => {
    setFailure(null)
    const refused = await patch(body)
    if (!refused) {
      toast.success(done)
      onClose()
    } else if (refused.next === 'reload') {
      onClose()
      onReload()
    } else {
      setFailure(refused)
    }
  }
  const save = () => {
    const result = rowEditPayload(row, values)
    if ('error' in result) setError(result.error)
    else void send({ rows: [result.edit] }, `已保存第 ${row.row} 行的修改`)
  }
  // A template row is the system's own CSV: its cells under their headers say exactly what the file holds.
  const cells = statement === 'template'
    ? columns.flatMap((column) => {
      const cell = row.cells?.[column.index]?.trim()
      if (!cell) return []
      return [`${column.field_label ?? column.header} ${column.field === 'kind' ? kindLabel(cell) : cell}`]
    })
    : []
  const facts = cells.length ? { text: cells.join(' · ') } : longLine((pick) => [
    line.date && `日期 ${line.date}`,
    row.action && `业务 ${statement === 'template' ? kindLabel(row.action) : row.action}`,
    (row.name || row.code) && `${row.name} ${row.code}`.trim(),
    line.quantity.text && `数量 ${pick(line.quantity)}`,
    line.price.text && `价格 ${pick(line.price)}${line.priceNote ? `（${line.priceNote}）` : ''}`,
    line.fee.text && `费用 ${pick(line.fee)}${line.feeNote ? `（${line.feeNote}）` : ''}`,
    line.amount.text && `金额 ${pick(line.amount)}`,
  ].filter(Boolean).join(' · '))

  const footer = (
    <div className="flex w-full flex-wrap items-center justify-between gap-2">
      <Button variant="ghost" icon={SkipForward} disabled={busy} disabledReason={disabledReason}
        onClick={() => void send({ rows: [skipEdit(row)] }, `第 ${row.row} 行已设为跳过`)}>
        跳过这一行
      </Button>
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={onClose}>取消</Button>
        {toMapping && (
          <Button icon={ArrowRight} onClick={() => {
            onClose()
            onGoToMapping()
          }}>去核对列</Button>
        )}
        {fillable && <Button variant="primary" loading={busy} disabledReason={disabledReason} onClick={save}>保存修改</Button>}
      </div>
    </div>
  )

  return (
    <Dialog open onClose={onClose} title={`处理第 ${row.row} 行`} size="md" closeOnBackdrop={false} footer={footer}>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1 text-table">
          <p className="text-fg-3">文件里的这一行</p>
          <p className={cx('break-words text-fg', numeric)}>{facts.text ? <LongValue value={facts} /> : '（空行）'}</p>
        </div>
        {reasons.length > 0 && (
          <InlineAlert tone="warn" title="为什么需要处理">
            <ul className="flex flex-col gap-0.5 text-table">{reasons.map((reason) => <li key={reason} className="break-words">{reason}</li>)}</ul>
          </InlineAlert>
        )}
        {notes.length > 0 && <p className="text-table text-fg-3">{notes.join('；')}</p>}
        <div className="grid gap-4 sm:grid-cols-2">
          {specs.map((spec) => (
            <EditField key={spec.need} spec={spec} row={row} statement={statement} values={values} update={update} />
          ))}
        </div>
        {error && <p role="alert" className="text-table text-warn">{error}</p>}
        {failure && <FailureAlert failure={failure} />}
      </div>
    </Dialog>
  )
}

function EditField({ spec, row, statement, values, update }: {
  spec: EditSpec; row: ImportRow; statement: Statement; values: EditValues; update: (need: string, value: string | boolean) => void
}) {
  const text = typeof values[spec.need] === 'string' ? (values[spec.need] as string) : ''
  const currency = row.currency ?? 'CNY'
  switch (spec.control) {
    case 'kind': {
      const kinds = editKinds(statement)
      if (!kinds.length) return <p className="text-table text-fg-2 sm:col-span-2">这种文件的行只能跳过：用下面的“跳过这一行”。</p>
      return (
        <Field label={spec.label} hint={row.category_label ? `文件里是“${row.category_label}”` : undefined}>
          <Select value={text} placeholder="选择类型" onChange={(event) => update(spec.need, event.target.value)}
            options={kinds.map((kind) => ({ value: kind, label: kindLabel(kind) }))} />
        </Field>
      )
    }
    case 'ticker': {
      const choices = row.choices?.ticker ?? []
      return (
        <div className="flex min-w-0 flex-col gap-1.5 sm:col-span-2">
          <Field label={spec.label} hint={row.code ? `文件里的代码：${row.code}${row.name ? `（${row.name}）` : ''}` : spec.hint}>
            <TickerInput value={text} onChange={(value) => update(spec.need, value)} showName />
          </Field>
          {choices.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 text-table text-fg-3">
              可能是：
              {choices.map((choice) => (
                <Button key={choice} size="sm" variant={text === choice ? 'primary' : 'secondary'} onClick={() => update(spec.need, choice)}>{choice}</Button>
              ))}
            </div>
          )}
        </div>
      )
    }
    case 'quantity':
      return (
        <Field label={spec.label} hint={spec.hint}>
          <NumberInput value={text} onValueChange={(value) => update(spec.need, value)} suffix="股" />
        </Field>
      )
    case 'price':
    case 'rate':
      return (
        <Field label={spec.label} hint={spec.hint}>
          <NumberInput value={text} onValueChange={(value) => update(spec.need, value)} />
        </Field>
      )
    case 'money':
      return (
        <Field label={spec.label} hint={spec.hint}>
          <MoneyInput currency={currency} value={text} onValueChange={(value) => update(spec.need, value)} />
        </Field>
      )
    case 'currency':
      return (
        <Field label={spec.label}>
          <Select value={text} placeholder="选择币种" onChange={(event) => update(spec.need, event.target.value)}
            options={EDIT_CURRENCIES.map((unit) => ({ value: unit, label: unit }))} />
        </Field>
      )
    case 'date':
      return (
        <Field label={spec.label}>
          <DateInput value={text} onChange={(event) => update(spec.need, event.target.value)} />
        </Field>
      )
    case 'flag':
      return (
        <Checkbox label={spec.label} description={spec.hint} checked={values[spec.need] === true}
          onChange={(event) => update(spec.need, event.target.checked)} />
      )
    case 'mapping':
    case 'account':
      return (
        <p className="text-table text-fg-2 sm:col-span-2">
          {spec.control === 'mapping' ? '这一行要先改正列对应。' : '这一行的资金账号或股东代码不在所选范围内。'}
          在“核对列”里处理。
        </p>
      )
    default:
      return (
        <p className="text-table text-fg-2 sm:col-span-2">
          还需要：{spec.label}。这个页面还不能填写这一项；请改正文件后重新导入，或把这一行设为跳过。
        </p>
      )
  }
}
