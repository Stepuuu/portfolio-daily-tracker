// 设置 › 关于与帮助: the text of the help (the v3.2 帮助 page, rewritten for the ledger-based app: nothing is
// edited by hand in a file) and the documentation links, fixed addresses in the project's repository.
import type { Mode } from '../../lib/navigation.ts'

export const MODE_LABEL: Record<Mode, string> = {
  full: '完整模式',
  lab: '研究模式（只有研究和设置）',
  demo: '演示模式（虚构数据，只读）',
}

/** The chat box's keys (components/ChatPanel.tsx). */
export const ASSISTANT_KEYS: [string, string][] = [
  ['Enter', '发送消息'],
  ['Ctrl + V', '粘贴图片'],
  ['拖入', '把图片拖到对话框上传'],
]

export const ABOUT_TIPS: string[] = [
  '持仓和现金只来自账本：记一笔、导入券商文件，或确认助手提出的方案；确认前都会先预览变化。',
  '行情可能延迟或中断：每个价格都标着来源和时间，取不到时显示不可用，成交价以券商的成交回报为准。',
  '价格提醒和每日例行任务只在后端运行时工作，关掉后端就不会提醒。',
  '助手可以查看持仓和行情，提出记账、提醒、计划和教训的方案；它不会直接修改任何数据，方案要你确认后才生效。',
  '助手的回答只供参考，不构成投资建议。',
]

const REPO = 'https://github.com/Stepuuu/portfolio-daily-tracker/blob/main'

export const DOC_LINKS: { label: string; href: string; description: string }[] = [
  { label: '中文说明', href: `${REPO}/README_CN.md`, description: '安装、启动和日常使用' },
  { label: '完整使用指南', href: `${REPO}/docs/FULL_GUIDE_CN.md`, description: '各页面的用法' },
  { label: '账本', href: `${REPO}/docs/LEDGER.md`, description: '流水、方案和确认' },
  { label: '研究工作台', href: `${REPO}/docs/RESEARCH_CN.md`, description: '实验、数据和模型连接' },
  { label: '飞书工作台', href: `${REPO}/docs/FEISHU_WORKBENCH.md`, description: '推送和卡片' },
  { label: '安全说明', href: `${REPO}/docs/SECURITY.md`, description: '密钥、访问和数据' },
]
