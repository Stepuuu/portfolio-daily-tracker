// Row edits without React. The edit form offers exactly what the backend lists in a row's `needs` (any
// other edit is refused with 422), plus the two edits every row accepts: 跳过 (kind "skip") and 撤销修改
// (reset). A need this page does not know is shown by name, so nothing the backend asks for is hidden.
// Pure TypeScript.
import type { ImportRow, RowEdit, Statement } from './types.ts'

/** Kinds a transactions row can be given (the PATCH contract); "skip" is its own button. */
export const EDIT_KINDS = ['buy', 'sell', 'dividend', 'fee', 'deposit', 'withdrawal', 'interest'] as const

export const EDIT_CURRENCIES = ['CNY', 'HKD', 'USD'] as const

export type EditControl =
  | 'kind' | 'ticker' | 'quantity' | 'price' | 'money' | 'currency' | 'rate' | 'date' | 'flag' | 'mapping' | 'account' | 'other'

export interface EditSpec {
  need: string
  control: EditControl
  label: string
  hint?: string
}

const SPECS: Record<string, Omit<EditSpec, 'need'>> = {
  kind: { control: 'kind', label: '类型' },
  ticker: { control: 'ticker', label: '代码', hint: '选择正式代码' },
  quantity: { control: 'quantity', label: '数量', hint: '按股数（股、张、份）填写，不是手' },
  price: { control: 'price', label: '价格', hint: '成交价，按成交回报填写' },
  fee: { control: 'money', label: '费用', hint: '成交单上的全部费用；确实没有费用填 0' },
  amount: { control: 'money', label: '金额', hint: '这一行的资金金额，填正数' },
  currency: { control: 'currency', label: '结算币种' },
  fx_rate: { control: 'rate', label: '汇率', hint: '1 单位外币折合多少人民币' },
  date: { control: 'date', label: '日期' },
  allow_margin: { control: 'flag', label: '允许融资', hint: '现金不足时记为负余额' },
  mapping: { control: 'mapping', label: '列对应' },
  account: { control: 'account', label: '账户识别' },
}

/** Controls that hold a value sent in the PATCH (the others are links to step 2 or a name). */
const VALUE_CONTROLS: EditControl[] = ['kind', 'ticker', 'quantity', 'price', 'money', 'currency', 'rate', 'date', 'flag']

export function editSpecs(row: Pick<ImportRow, 'needs'>): EditSpec[] {
  return [...new Set(row.needs ?? [])].map((need) => ({ need, ...(SPECS[need] ?? { control: 'other' as const, label: need }) }))
}

export function isValueSpec(spec: EditSpec): boolean {
  return VALUE_CONTROLS.includes(spec.control)
}

/** The kinds the form offers: only transactions rows take a kind other than skip. */
export function editKinds(statement: Statement): readonly string[] {
  return statement === 'transactions' ? EDIT_KINDS : []
}

export type EditValues = Record<string, string | boolean>

/** The form starts from the row's stored edits, never from the file's value the backend could not use. */
export function initialEditValues(row: Pick<ImportRow, 'needs' | 'edits'>): EditValues {
  const values: EditValues = {}
  for (const spec of editSpecs(row)) {
    if (!isValueSpec(spec)) continue
    const stored = row.edits?.[spec.need]
    if (spec.control === 'flag') values[spec.need] = stored === true
    else values[spec.need] = typeof stored === 'string' ? stored : ''
  }
  return values
}

/** The PATCH item for the form's values: only needed fields, only those filled in. */
export function rowEditPayload(row: Pick<ImportRow, 'row' | 'needs'>, values: EditValues): { edit: RowEdit } | { error: string } {
  const edit: Record<string, unknown> = { row: row.row }
  let filled = 0
  for (const spec of editSpecs(row)) {
    if (!isValueSpec(spec)) continue
    const value = values[spec.need]
    if (spec.control === 'flag') {
      if (value === true) {
        edit[spec.need] = true
        filled += 1
      }
    } else if (typeof value === 'string' && value.trim()) {
      edit[spec.need] = value.trim()
      filled += 1
    }
  }
  if (filled === 0) return { error: '请至少填写一项需要处理的内容，或把这一行设为跳过。' }
  return { edit: edit as unknown as RowEdit }
}

export function includeEdit(row: Pick<ImportRow, 'row'>, include: boolean): RowEdit {
  return { row: row.row, include }
}

export function skipEdit(row: Pick<ImportRow, 'row'>): RowEdit {
  return { row: row.row, kind: 'skip' }
}

export function resetEdit(row: Pick<ImportRow, 'row'>): RowEdit {
  return { row: row.row, reset: true }
}

export function hasEdits(row: Pick<ImportRow, 'edits'>): boolean {
  return Object.keys(row.edits ?? {}).length > 0
}

/**
 * 撤销修改 is offered while the edit still decides something. Once the edited row is in the ledger (已导入),
 * undoing it would not unbook anything; it would only turn the row back into 需处理, so it is not offered.
 */
export function canReset(row: Pick<ImportRow, 'edits' | 'status'>): boolean {
  return hasEdits(row) && row.status !== 'imported'
}

/** The row can be handled in the form (it books nothing until something is filled in or it is skipped). */
export function canEdit(row: Pick<ImportRow, 'status'>): boolean {
  return row.status === 'review'
}
