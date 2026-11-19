// The wizard's steps without React: which steps a mode shows, each step's state from the checklist, where a
// returning visitor resumes, and whether Today sends a first visitor here. Pure TypeScript (node tests).
import type { Mode } from '../../lib/navigation.ts'
import type { ItemId, ItemState, SetupItem, SetupStatus } from './types.ts'

export type StepId = 'welcome' | 'account' | 'fees' | 'market' | 'ai' | 'done'
export type StartChoice = 'zero' | 'import' | 'legacy' | 'demo'
export type StepState = 'done' | 'todo' | 'optional' | 'unknown'

export const STEP_LABELS: Record<StepId, string> = {
  welcome: '欢迎', account: '账户与期初', fees: '费率', market: '行情与提醒', ai: 'AI 助手', done: '完成',
}

/** The checklist items each step settles. */
export const STEP_ITEMS: Record<StepId, ItemId[]> = {
  welcome: [], account: ['opening', 'legacy'], fees: ['fees'], market: ['quotes', 'alerts', 'routines', 'notifications'],
  ai: ['llm'], done: [],
}

const DESK_STEPS: StepId[] = ['welcome', 'account', 'fees', 'market', 'ai', 'done']
/** Research-only mode has no ledger, fees, quotes or alerts: only the model makes sense. */
const LAB_STEPS: StepId[] = ['welcome', 'ai', 'done']

export function stepsFor(mode: Mode | undefined): StepId[] {
  return mode === 'lab' ? LAB_STEPS : DESK_STEPS
}

export const START_CHOICES: { value: StartChoice; label: string; description: string }[] = [
  { value: 'zero', label: '从零开始', description: '新建账户，填写现金、持仓和累计净投入。' },
  { value: 'import', label: '导入券商文件', description: '用券商导出的持仓或交割单建立账本。' },
  { value: 'legacy', label: '迁移旧版持仓', description: '把 v3.2 的持仓文件转成期初余额，核对后入账。' },
  { value: 'demo', label: '先看演示', description: '用虚构数据浏览全部页面，什么都不会保存。' },
]

export const STATE_LABELS: Record<ItemState, string> = {
  done: '已完成', todo: '待处理', optional: '可选', unavailable: '不可用',
}

export function parseStep(value: string | null | undefined, steps: StepId[]): StepId | null {
  return steps.find((step) => step === value) ?? null
}

export function parseStart(value: string | null | undefined): StartChoice | null {
  return START_CHOICES.find((choice) => choice.value === value)?.value ?? null
}

export function itemsOf(step: StepId, items: SetupItem[] | undefined): SetupItem[] {
  const wanted = STEP_ITEMS[step]
  return (items ?? []).filter((item) => wanted.includes(item.id))
}

/**
 * A step is `todo` while one of its items is; `done` once every item that applies is done (an optional
 * item does not hold it back); `optional` when all it has is optional; `unknown` when nothing applies or
 * nothing could be checked. Welcome and 完成 have no items (`unknown`).
 */
export function stepState(step: StepId, items: SetupItem[] | undefined): StepState {
  const own = itemsOf(step, items)
  if (own.some((item) => item.state === 'todo')) return 'todo'
  const required = own.filter((item) => item.state === 'done' || item.state === 'todo')
  if (required.length && required.every((item) => item.state === 'done')) return 'done'
  if (own.some((item) => item.state === 'optional')) return 'optional'
  return 'unknown'
}

/**
 * Where a visit without `?step=` starts: welcome while nothing is set up (no opening balance), otherwise the
 * first step still to do, otherwise 完成. Research-only mode starts at welcome.
 */
export function resumeStep(steps: StepId[], items: SetupItem[] | undefined): StepId {
  if (!items || !steps.includes('account')) return 'welcome'
  const opening = items.find((item) => item.id === 'opening')
  if (!opening || opening.state === 'todo') return 'welcome'
  return steps.find((step) => step !== 'welcome' && step !== 'done' && stepState(step, items) === 'todo') ?? 'done'
}

/**
 * What 账户与期初 shows. Before the ledger has an opening: the three ways (`ways`). After it, only while a legacy
 * source still holds accounts that are not in the ledger (the checklist's legacy item is then to do or
 * optional): the migration on its own when asked for (`?start=legacy`, also the checklist's link), else an
 * offer (`offer`). A file migrated in full stays closed, also on that link. `notice`: the legacy item's text.
 */
export function accountStepView(opening: SetupItem | undefined, legacy: SetupItem | undefined, start: StartChoice | null): {
  ways: boolean; migrate: boolean; offer: boolean; notice: boolean
} {
  const done = opening?.state === 'done'
  const left = legacy?.state === 'todo' || legacy?.state === 'optional'
  return {
    ways: !done,
    migrate: done && start === 'legacy' && left,
    offer: done && start !== 'legacy' && left,
    notice: Boolean(legacy) && legacy!.state !== 'done',
  }
}

export function neighbours(steps: StepId[], step: StepId): { previous: StepId | null; next: StepId | null } {
  const index = steps.indexOf(step)
  return { previous: index > 0 ? steps[index - 1] : null, next: index >= 0 && index < steps.length - 1 ? steps[index + 1] : null }
}

/** Checklist items by state, in the order the 完成 step lists them. */
export function groupItems(items: SetupItem[] | undefined): { state: ItemState; items: SetupItem[] }[] {
  const order: ItemState[] = ['todo', 'optional', 'done', 'unavailable']
  return order
    .map((state) => ({ state, items: (items ?? []).filter((item) => item.state === state) }))
    .filter((group) => group.items.length > 0)
}

/** Where a checklist action leads; only paths inside the app (never a link built from other data). */
export function actionHref(href: string | undefined): string | null {
  return href && /^\/(?!\/)/.test(href) ? href : null
}

// -- the first visit -------------------------------------------------------------------------

/**
 * Today sends a visitor to /setup once: in full mode, while the ledger has no opening balance, the wizard
 * was neither completed nor dismissed, and it has not sent them in this browser session yet. An unknown
 * value (null, an unread status) never redirects: Today stays usable.
 */
export function shouldRedirect(input: {
  mode: Mode | undefined
  unknown?: boolean
  status: SetupStatus | undefined
  alreadyRedirected: boolean
}): boolean {
  const { mode, status } = input
  if (mode !== 'full' || input.unknown || !status || input.alreadyRedirected) return false
  return status.ledger_ready === false && status.setup_complete === false && status.dismissed === false
}

export const REDIRECT_KEY = 'pdt.setup.redirected'

export interface SessionLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

let redirectedThisLoad = false

/** The tab's sessionStorage, or null where it cannot be used. */
export function sessionStore(): SessionLike | null {
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

/** Whether Today already sent this tab to /setup (this page load, or earlier in the session). */
export function alreadyRedirected(storage: SessionLike | null): boolean {
  if (redirectedThisLoad) return true
  try {
    return storage?.getItem(REDIRECT_KEY) === '1'
  } catch {
    return false
  }
}

export function rememberRedirect(storage: SessionLike | null): void {
  redirectedThisLoad = true
  try {
    storage?.setItem(REDIRECT_KEY, '1')
  } catch {
    // Without storage the page-load flag still prevents a second redirect.
  }
}

/** For tests: forget the page-load flag. */
export function resetRedirectMemory(): void {
  redirectedThisLoad = false
}
