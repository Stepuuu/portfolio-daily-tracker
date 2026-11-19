// 2 核对列: what the file was recognised as, then everything that must be settled before the preview, each
// with its control: column conflicts (action, market and 股/手 included), a workbook's sheet, required
// columns, the account identities of a mixed file, how short codes read, and the kind of file.
// 继续 stays disabled, with the reason, until nothing is left.
import { useRef, type ReactNode } from 'react'
import { ArrowRight, Trash2 } from 'lucide-react'
import { Button, Field, InlineAlert, Panel, SegmentedControl, Select, cx } from '@/components/ui'
import { MAPPING_SOURCE_LABEL, STATEMENT_LABELS, mappingProblems, needsSheetChoice } from '@/features/import/flow'
import type { ImportPatch, ImportRow, ImportSession, Statement } from '@/features/import/types'
import { AccountIdentity } from './AccountIdentity'
import { BlockerList, FailureAlert } from './Notices'
import { FileSummary } from './FileSummary'
import { ConflictChoice, MappingEditor } from './MappingEditor'
import type { FlowFailure } from './useImport'

const SHORT_CODES = [
  { value: '', label: '自动' },
  { value: 'HKG', label: '港股' },
  { value: 'A', label: 'A 股' },
]

function Section({ title, description, children, warn = false }: { title: string; description?: ReactNode; children: ReactNode; warn?: boolean }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-col gap-2 border-t border-line pt-4">
      <div>
        <h3 className={cx('text-body font-medium', warn ? 'text-warn' : 'text-fg')}>{title}</h3>
        {description && <p className="text-table text-fg-3">{description}</p>}
      </div>
      {children}
    </section>
  )
}

export interface MappingStepProps {
  session: ImportSession
  /** The loaded rows (first page), for example values. */
  sample: ImportRow[]
  /** The first page of 需处理 rows, for the backend's reasons behind the rows the account choice keeps out. */
  reviewRows: ImportRow[] | undefined
  onPatch: (body: ImportPatch) => void
  busy: boolean
  failure: FlowFailure | null
  onReload: () => void
  disabledReason?: string
  acknowledged: string[]
  onAcknowledge: (field: string) => void
  onContinue: () => void
  onDiscard: () => void
}

export function MappingStep({
  session, sample, reviewRows, onPatch, busy, failure, onReload, disabledReason, acknowledged, onAcknowledge, onContinue, onDiscard,
}: MappingStepProps) {
  const problems = mappingProblems(session, acknowledged)
  const continueRef = useRef<HTMLButtonElement>(null)
  const sheetOptions = session.format.sheet_options ?? []
  const conflicts = session.column_conflicts ?? []
  const blocked = busy || Boolean(disabledReason)

  return (
    <Panel
      title="核对列"
      description={MAPPING_SOURCE_LABEL[session.mapping_source ?? ''] ?? undefined}
    >
      <div className="flex flex-col gap-4">
        <FileSummary session={session} />
        {failure && <FailureAlert failure={failure} onReload={onReload} />}

        {problems.length > 0 ? (
          <InlineAlert tone="warn" title={`继续之前需要处理 ${problems.length} 项`}>
            <ul className="flex list-disc flex-col gap-0.5 pl-4 text-table">
              {problems.map((problem) => <li key={problem.key} className="break-words">{problem.text}</li>)}
            </ul>
          </InlineAlert>
        ) : (
          <BlockerList blockers={session.blockers} title="生成入账预览前还需要处理" />
        )}

        {conflicts.length > 0 && (
          <Section title="列冲突" warn={conflicts.some((conflict) => !conflict.resolved)} description="两列都可以作为同一项而且数值不同：选定一列，下次同样的表头会自动套用。">
            <div className="grid gap-3 lg:grid-cols-2">
              {conflicts.map((conflict) => (
                <ConflictChoice
                  key={`${conflict.field}:${conflict.columns.join(',')}:${conflict.chosen}`}
                  conflict={conflict}
                  session={session}
                  sample={sample}
                  onPatch={onPatch}
                  busy={busy}
                  disabledReason={disabledReason}
                />
              ))}
            </div>
          </Section>
        )}

        {sheetOptions.length > 1 && (
          <Section title="工作表" warn={needsSheetChoice(session.format)} description="工作簿里有多个含数据的表。">
            <div className="flex flex-wrap items-end gap-2">
              <Field label="导入的工作表" className="w-72 max-w-full">
                <Select
                  value={session.format.sheet ?? ''}
                  disabled={blocked}
                  onChange={(event) => onPatch({ sheet: event.target.value })}
                  options={sheetOptions.map((option) => ({
                    value: option.name,
                    label: `${option.name}（${option.data_rows} 行${option.hidden ? '，隐藏' : ''}）`,
                  }))}
                />
              </Field>
              {needsSheetChoice(session.format) && session.format.sheet && (
                <Button loading={busy} disabledReason={disabledReason} onClick={() => onPatch({ sheet: session.format.sheet! })}>
                  就用“{session.format.sheet}”
                </Button>
              )}
            </div>
          </Section>
        )}

        <Section title="列对应" description="每一列对应到账本的哪一项；改过的会为这个账户和表头记住。">
          <MappingEditor
            session={session}
            sample={sample}
            onPatch={onPatch}
            busy={busy}
            disabledReason={disabledReason}
            acknowledged={acknowledged}
            onAcknowledge={onAcknowledge}
          />
        </Section>

        {session.accounts && (
          <Section title="账户识别" warn={problems.some((problem) => problem.kind === 'account')} description={`文件里的资金账号 / 股东代码，哪些属于账户“${session.account}”。`}>
            <AccountIdentity
              account={session.account}
              accounts={session.accounts}
              reviewRows={reviewRows}
              onPatch={onPatch}
              busy={busy}
              disabledReason={disabledReason}
            />
          </Section>
        )}

        <Section title="代码与文件类型">
          <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
            <Field label="5 位及以下的数字代码" hint="如 00700：按港股还是 A 股读">
              <SegmentedControl
                size="sm"
                options={SHORT_CODES.map((option) => ({ ...option, disabled: blocked }))}
                value={session.short_code_market ?? ''}
                onChange={(value) => onPatch({ short_code_market: value || null })}
              />
            </Field>
            <Field label="文件类型" hint="切换会重新识别列对应，并清除对行的修改" className="w-80 max-w-full">
              <Select
                value={session.statement}
                disabled={blocked}
                onChange={(event) => onPatch({ statement: event.target.value as Statement })}
                options={(Object.entries(STATEMENT_LABELS) as [Statement, string][]).map(([value, label]) => ({ value, label }))}
              />
            </Field>
          </div>
        </Section>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
          <Button variant="ghost" icon={Trash2} disabled={busy} disabledReason={disabledReason} onClick={onDiscard}>放弃这次导入</Button>
          <Button
            ref={continueRef}
            variant="primary"
            icon={ArrowRight}
            disabled={problems.length > 0 || busy}
            title={problems[0]?.text}
            onClick={onContinue}
          >
            继续：预览与去重
          </Button>
        </div>
        {problems.length > 0 && <p className="-mt-2 text-right text-table text-fg-3">{problems[0].text}</p>}
      </div>
    </Panel>
  )
}
