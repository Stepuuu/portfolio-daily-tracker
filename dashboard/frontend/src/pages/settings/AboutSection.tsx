// 设置 › 关于与帮助: version and mode, the theme (a preference of this browser), the setup wizard again, the
// keyboard shortcuts, how the app works in a few sentences (the old 帮助 page, rewritten for the ledger-based
// app) and links to the documentation in the repository.
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ExternalLink, RotateCcw } from 'lucide-react'
import { Field, Panel, SegmentedControl, buttonClass, cx, focusRing } from '@/components/ui'
import { SHORTCUTS } from '@/components/shell/ShortcutsDialog'
import { useAppMode } from '@/components/shell/useAppMode'
import { readThemePreference, setThemePreference, type ThemePreference } from '@/lib/theme'
import { ABOUT_TIPS, ASSISTANT_KEYS, DOC_LINKS, MODE_LABEL } from '@/features/settings/about'

function Keys({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-4 gap-y-2 text-body">
      {rows.map(([keys, action]) => (
        <div key={keys} className="contents">
          <dt><kbd className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-sans text-table text-fg">{keys}</kbd></dt>
          <dd className="text-fg-2">{action}</dd>
        </div>
      ))}
    </dl>
  )
}

export function AboutSection() {
  const { mode, status, lab } = useAppMode()
  const [theme, setTheme] = useState<ThemePreference>(() => readThemePreference())
  return (
    <>
      <Panel title="关于">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-body">
          <dt className="text-fg-3">版本</dt>
          <dd className="tabular-nums text-fg">{status?.version ?? '不可用'}</dd>
          <dt className="text-fg-3">运行模式</dt>
          <dd className="text-fg">{mode ? MODE_LABEL[mode] : '不可用'}</dd>
        </dl>
      </Panel>
      <Panel title="外观" description="只保存在这个浏览器里。">
        <Field label="主题">
          <SegmentedControl
            value={theme}
            onChange={(next) => {
              setTheme(next)
              setThemePreference(next)
            }}
            options={[
              { value: 'system', label: '跟随系统' },
              { value: 'light', label: '浅色' },
              { value: 'dark', label: '深色' },
            ]}
          />
        </Field>
      </Panel>
      <Panel title="设置向导" description="重新检查账户、费率、行情、提醒和模型；已经保存的内容不会丢失。">
        <Link to="/setup" className={buttonClass()}>
          <RotateCcw size={16} aria-hidden />
          重新运行设置向导
        </Link>
      </Panel>
      <Panel title="快捷键">
        <div className="flex flex-col gap-4">
          <Keys rows={SHORTCUTS.filter(([keys]) => keys !== 'N' || !lab)} />
          <div className="flex flex-col gap-2 border-t border-line pt-4">
            <h3 className="text-table font-medium text-fg-2">助手</h3>
            <Keys rows={ASSISTANT_KEYS} />
          </div>
        </div>
      </Panel>
      <Panel title="使用说明">
        <ul className="flex list-disc flex-col gap-2 pl-5 text-body text-fg-2 marker:text-fg-3">
          {ABOUT_TIPS.map((tip) => <li key={tip}>{tip}</li>)}
        </ul>
      </Panel>
      <Panel title="文档" description="在项目仓库中打开（新标签页）。">
        <ul className="flex flex-col gap-2">
          {DOC_LINKS.map((doc) => (
            <li key={doc.href}>
              <a
                href={doc.href}
                target="_blank"
                rel="noopener noreferrer"
                className={cx('inline-flex items-center gap-1 rounded-sm text-body text-accent underline-offset-4 hover:underline', focusRing)}
              >
                {doc.label}
                <ExternalLink size={14} aria-hidden />
              </a>
              <span className="ml-2 text-table text-fg-3">{doc.description}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  )
}

export default AboutSection
