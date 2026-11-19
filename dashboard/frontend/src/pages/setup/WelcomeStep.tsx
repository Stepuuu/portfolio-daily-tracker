// 1 欢迎: what the wizard sets up and how to start. The choice only decides what step 2 shows; 先看演示
// explains the demo, which is a separate start of the app (configured outside the UI).
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { Panel, buttonClass, cx, focusRing } from '@/components/ui'
import { START_CHOICES, type StartChoice } from '@/features/setup/steps'
import { CommandLine } from '@/components/setup/Checklist'

const WHAT = [
  ['账户与期初', '每个账户的现金、持仓和累计净投入；之后的买卖都从这里记。'],
  ['费率', '记一笔时按账户费率预填费用估算。'],
  ['行情与提醒', '行情源是否连得上，提醒监控和每日例行任务是否在运行。'],
  ['AI 助手（可选）', '对话助手和简报点评用的模型与密钥。'],
] as const

export function WelcomeStep({ start, onChoose, lab }: { start: StartChoice | null; onChoose: (choice: StartChoice) => void; lab: boolean }) {
  if (lab) {
    return (
      <Panel title="研究模式" description="后端以 TRACKER_LAB_ONLY=1 启动：只有研究工作台和设置。">
        <div className="flex flex-col gap-3 text-table text-fg-2">
          <p>这个模式不使用账本、费率、行情提醒和每日例行任务，向导只设置对话模型。研究工作台的模型连接在工作台里单独配置。</p>
          <Link to="/lab" className={cx('w-fit rounded-sm text-accent underline-offset-4 hover:underline', focusRing)}>打开研究工作台</Link>
        </div>
      </Panel>
    )
  }
  return (
    <div className="flex flex-col gap-6">
      <Panel title="几分钟完成首次设置" description="向导依次设置这几件事，从空白到今日页显示持仓。">
        <dl className="grid gap-3 sm:grid-cols-2">
          {WHAT.map(([title, text]) => (
            <div key={title} className="min-w-0">
              <dt className="text-body font-medium text-fg">{title}</dt>
              <dd className="text-table text-fg-2">{text}</dd>
            </div>
          ))}
        </dl>
      </Panel>
      <Panel title="从哪里开始">
        <div role="group" aria-label="从哪里开始" className="grid gap-3 sm:grid-cols-2">
          {START_CHOICES.map((choice) => (
            <button
              key={choice.value}
              type="button"
              aria-pressed={start === choice.value}
              onClick={() => onChoose(choice.value)}
              className={cx(
                'flex min-w-0 items-start justify-between gap-3 rounded-lg border px-4 py-3 text-left transition-colors duration-[120ms]',
                start === choice.value ? 'border-accent bg-accent/10' : 'border-line hover:border-line-input/70 hover:bg-surface-2',
                focusRing,
              )}
            >
              <span className="min-w-0">
                <span className="block text-body font-medium text-fg">{choice.label}</span>
                <span className="block text-table text-fg-2">{choice.description}</span>
              </span>
              <ArrowRight size={16} aria-hidden className="mt-1 shrink-0 text-fg-3" />
            </button>
          ))}
        </div>
      </Panel>
      {start === 'demo' && (
        <Panel title="演示模式" description="虚构的账户、行情和记录；页面都能看，任何修改都会被拒绝。">
          <div className="flex flex-col gap-3">
            <p className="text-table text-fg-2">演示是另一种启动方式：先停止现在的后端，再在仓库根目录运行下面这一行，然后打开今日页。</p>
            <CommandLine label="启动演示" text="make demo" />
            <p className="text-table text-fg-2">看完后用 make start 回到自己的数据；这里的设置不受影响。</p>
            <Link to="/" className={cx(buttonClass({ variant: 'secondary' }), 'w-fit')}>先去今日页看看</Link>
          </div>
        </Panel>
      )}
    </div>
  )
}
