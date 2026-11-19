// 1 选择文件: the ledger account, the kind of file (detected unless chosen), and the file by drag-and-drop or
// the picker. Files over the API's limit are refused before anything is sent; every other refusal is the
// backend's own Chinese text under a title for its status (413 too large, 415 unreadable, 422 invalid).
// Read-only (demo, unknown mode): the zone explains why and holds no file input, so nothing is uploaded.
import { useId, useRef, useState, type DragEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Download, FileUp, Loader2, Lock, Upload } from 'lucide-react'
import { Button, Field, InlineAlert, Input, Panel, Select, cx, numeric } from '@/components/ui'
import { getApiErrorMessage, httpStatus } from '@/services/api'
import { parseImport } from '@/services/imports'
import { TEMPLATE_URL } from '@/services/ledger'
import { fmtDateTime } from '@/lib/format'
import { ACCEPTED_FILES, MAX_IMPORT_BYTES, STATEMENT_LABELS, TOO_LARGE } from '@/features/import/flow'
import { NEXT_LABEL, uploadFailure, type Failure } from '@/features/import/errors'
import { FIRST_PAGE } from '@/features/import/rows'
import type { ImportSession, Statement } from '@/features/import/types'
import { download } from '@/components/ledger/useLedger'

const NEW_ACCOUNT = '\u0000new'

const STATEMENT_OPTIONS = [
  { value: 'auto', label: '自动识别' },
  ...(Object.entries(STATEMENT_LABELS) as [Statement, string][]).map(([value, label]) => ({ value, label })),
]

/** "12.3 KB": a file size, one decimal. */
function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const tenths = (value: number) => Math.round(value * 10) / 10
  return bytes < 1024 * 1024 ? `${tenths(bytes / 1024)} KB` : `${tenths(bytes / 1024 / 1024)} MB`
}

export interface UploadStepProps {
  /** Ledger accounts, in order. */
  accounts: string[]
  readOnlyReason?: string
  onUploaded: (session: ImportSession) => void
  /** The import in progress, if any: shown with 换一个文件. */
  current?: ImportSession
  onReplace?: () => void
}

export function UploadStep({ accounts, readOnlyReason, onUploaded, current, onReplace }: UploadStepProps) {
  const inputId = useId()
  // null until the person picks: the first account, once the ledger has answered.
  const [choice, setChoice] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [statement, setStatement] = useState<Statement | 'auto'>('auto')
  const [dragging, setDragging] = useState(false)
  const [failure, setFailure] = useState<Failure | null>(null)
  const [accountError, setAccountError] = useState<string | null>(null)
  const lastFile = useRef<File | null>(null)
  const selected = choice !== null && (choice === NEW_ACCOUNT || accounts.includes(choice)) ? choice : accounts[0] ?? NEW_ACCOUNT
  const account = selected === NEW_ACCOUNT ? newName.trim() : selected

  const upload = useMutation({
    mutationFn: (file: File) => parseImport(file, account, statement, FIRST_PAGE),
    onSuccess: onUploaded,
    onError: (error) => setFailure(uploadFailure(httpStatus(error), getApiErrorMessage(error))),
  })

  const send = (file: File | undefined) => {
    if (!file || readOnlyReason || upload.isPending) return
    setFailure(null)
    lastFile.current = file
    if (!account) {
      setAccountError('请选择账户或填写新账户名称')
      return
    }
    if (file.size > MAX_IMPORT_BYTES) {
      setFailure({ status: 413, title: '文件太大', message: TOO_LARGE, next: 'none' })
      return
    }
    upload.mutate(file)
  }

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setDragging(false)
    send(event.dataTransfer.files?.[0])
  }

  const zone = cx(
    'flex min-h-[148px] flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-8 text-center transition-colors duration-[120ms]',
    readOnlyReason ? 'border-line bg-surface-2 text-fg-3' : 'cursor-pointer border-line-input/70 hover:border-accent hover:bg-fg/[.05]',
    dragging && 'border-accent bg-fg/[.05]',
    'has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent',
  )

  return (
    <Panel title="选择文件" description="券商导出的交割单、资金对账单或持仓；先在这里核对，确认后才入账。">
      <div className="flex flex-col gap-4">
        {current && (
          <InlineAlert
            tone="info"
            title={`正在导入：${current.file.name}`}
            action={onReplace && <Button size="sm" onClick={onReplace} disabledReason={readOnlyReason}>换一个文件</Button>}
          >
            账户“{current.account}” · 上传于 {fmtDateTime(current.created_at)}。换一个文件会放弃这次导入（账本不受影响）。
          </InlineAlert>
        )}
        {readOnlyReason && (
          <InlineAlert tone="info" title="导入已停用">
            {readOnlyReason}：可以查看这个页面，但不能上传文件，也不会生成入账预览。
          </InlineAlert>
        )}

        {!current && (
          <>
            <div className="grid max-w-[720px] gap-4 sm:grid-cols-2">
              <Field
                label="导入到账户"
                hint={selected === NEW_ACCOUNT ? '新账户先导入持仓文件建立期初，之后才能导入交割单' : undefined}
              >
                <Select
                  value={selected}
                  disabled={Boolean(readOnlyReason)}
                  onChange={(event) => {
                    setChoice(event.target.value)
                    setAccountError(null)
                  }}
                  options={[...accounts.map((name) => ({ value: name, label: name })), { value: NEW_ACCOUNT, label: '新账户…' }]}
                />
              </Field>
              <Field label="文件类型" hint="一般不用改：按表头自动识别">
                <Select
                  value={statement}
                  disabled={Boolean(readOnlyReason)}
                  onChange={(event) => setStatement(event.target.value as Statement | 'auto')}
                  options={STATEMENT_OPTIONS}
                />
              </Field>
              {selected === NEW_ACCOUNT && (
                <Field label="新账户名称" required error={accountError ?? undefined}>
                  <Input
                    value={newName}
                    maxLength={240}
                    disabled={Boolean(readOnlyReason)}
                    placeholder="如 主账户"
                    onChange={(event) => {
                      setNewName(event.target.value)
                      setAccountError(null)
                    }}
                  />
                </Field>
              )}
            </div>

            {readOnlyReason ? (
              <div className={zone} aria-disabled="true">
                <Lock size={20} aria-hidden />
                <span className="text-body">拖入文件或点击选择</span>
                <span className="text-table">{readOnlyReason}</span>
              </div>
            ) : (
              <label
                htmlFor={inputId}
                className={zone}
                onDragEnter={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragOver={(event) => {
                  event.preventDefault()
                  event.dataTransfer.dropEffect = 'copy'
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                {upload.isPending ? (
                  <>
                    <Loader2 size={20} aria-hidden className="animate-spin text-accent motion-reduce:animate-none" />
                    <span role="status" className="text-body text-fg">正在读取 {lastFile.current?.name ?? '文件'}…</span>
                  </>
                ) : (
                  <>
                    <Upload size={20} aria-hidden className="text-accent" />
                    <span className="text-body font-medium text-fg">拖入文件或点击选择</span>
                    <span className={cx('text-table text-fg-3', numeric)}>CSV / XLS / XLSX / TXT · 不超过 5 MB · 一次一个文件</span>
                  </>
                )}
                <input
                  id={inputId}
                  type="file"
                  accept={ACCEPTED_FILES}
                  aria-label="选择券商文件"
                  className="sr-only"
                  disabled={upload.isPending}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    send(file)
                  }}
                />
              </label>
            )}

            {failure && (
              <InlineAlert
                tone="warn"
                title={failure.title}
                action={failure.next === 'retry' && lastFile.current ? (
                  <Button size="sm" onClick={() => send(lastFile.current ?? undefined)}>{NEXT_LABEL.retry}</Button>
                ) : undefined}
              >
                {failure.message}
                {lastFile.current && <span className="block text-table text-fg-3">{lastFile.current.name} · {sizeText(lastFile.current.size)}</span>}
              </InlineAlert>
            )}
          </>
        )}

        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-table text-fg-3">
          <FileUp size={14} aria-hidden />
          本系统的标准 CSV 模板（每行一个 external_id）也在这里导入。
          <Button size="sm" variant="ghost" icon={Download} onClick={() => download(TEMPLATE_URL)}>下载模板</Button>
        </p>
      </div>
    </Panel>
  )
}
