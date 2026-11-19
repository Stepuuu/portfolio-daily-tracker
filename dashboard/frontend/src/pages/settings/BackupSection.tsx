// 设置 › 备份与导出: the two files that hold the desk's data, each downloaded whole (a consistent copy made
// with SQLite's backup API), the ledger's events as JSON, and a plain statement of what each file holds and
// what neither does. Downloads are reads: allowed in the demo too. A refusal (no ledger yet) is a toast with
// the backend's words, never a saved error page.
import { useState } from 'react'
import { Download } from 'lucide-react'
import { Button, Panel, toast } from '@/components/ui'
import { DESK_BACKUP_URL, LEDGER_BACKUP_URL, LEDGER_EXPORT_URL, downloadFile } from '@/services/settings'
import { BACKUP_CONTENTS, NOT_IN_BACKUPS } from '@/features/settings/backup'

function DownloadButton({ url, filename, label, primary = false }: { url: string; filename: string; label: string; primary?: boolean }) {
  const [busy, setBusy] = useState(false)
  const start = async () => {
    setBusy(true)
    try {
      await downloadFile(url, filename)
      toast.success(`已开始下载 ${filename}`)
    } catch (error) {
      toast.warn(`没有下载：${error instanceof Error ? error.message : '请稍后重试'}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button variant={primary ? 'primary' : 'secondary'} icon={Download} loading={busy} onClick={() => void start()}>
      {label}
    </Button>
  )
}

function Contents({ items }: { items: readonly string[] }) {
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-body text-fg-2 marker:text-fg-3">
      {items.map((item) => <li key={item}>{item}</li>)}
    </ul>
  )
}

export function BackupSection() {
  return (
    <>
      <Panel title="账本" description={`文件 ${BACKUP_CONTENTS.ledger.file}：全部持仓和现金都由它算出。`}>
        <div className="flex flex-col gap-4">
          <Contents items={BACKUP_CONTENTS.ledger.items} />
          <div className="flex flex-wrap gap-2">
            <DownloadButton url={LEDGER_BACKUP_URL} filename="ledger-backup.sqlite3" label="下载账本备份" primary />
            <DownloadButton url={LEDGER_EXPORT_URL} filename="transactions.json" label="导出流水（JSON）" />
          </div>
        </div>
      </Panel>
      <Panel title="桌面数据" description={`文件 ${BACKUP_CONTENTS.desk.file}：账本以外的日常数据，单独一个文件。`}>
        <div className="flex flex-col gap-4">
          <Contents items={BACKUP_CONTENTS.desk.items} />
          <div>
            <DownloadButton url={DESK_BACKUP_URL} filename="desk-backup.sqlite3" label="下载桌面数据备份" />
          </div>
        </div>
      </Panel>
      <Panel title="不在这两个备份里" description="需要时请另外备份这些文件或设置。">
        <div className="flex flex-col gap-3">
          <Contents items={NOT_IN_BACKUPS} />
          <p className="text-table text-fg-3">恢复时先停止后端，再用备份替换数据目录里的同名文件（ledger.sqlite3、desk.sqlite3），然后启动。</p>
        </div>
      </Panel>
    </>
  )
}

export default BackupSection
