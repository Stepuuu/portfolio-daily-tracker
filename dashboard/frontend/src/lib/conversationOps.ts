// One shared "what is changing the conversation right now" state for sending a question,
// resuming a saved conversation and starting a new one. It lives in the store, so it survives
// the history modal closing and the chat panel unmounting. Every operation gets a token; a
// response that arrives after its operation was superseded must not touch the messages.

export type ConversationOpKind = 'send' | 'resume' | 'reset'

export interface ConversationOpState {
  // Last token handed out; tokens only grow.
  epoch: number
  active: { kind: ConversationOpKind; token: number } | null
}

export const initialConversationOp: ConversationOpState = { epoch: 0, active: null }

export interface BeginResult {
  state: ConversationOpState
  token: number
}

// Returns null when another operation is running (unless force is set, which supersedes it:
// the earlier operation's token stops being current).
export function beginOp(
  state: ConversationOpState,
  kind: ConversationOpKind,
  opts: { force?: boolean } = {},
): BeginResult | null {
  if (state.active && !opts.force) return null
  const token = state.epoch + 1
  return { state: { epoch: token, active: { kind, token } }, token }
}

export function isCurrentOp(state: ConversationOpState, token: number): boolean {
  return state.active !== null && state.active.token === token
}

// Call when the response arrives. apply=false means the operation was superseded: ignore the
// response and leave the state alone (it belongs to the newer operation).
export function finishOp(
  state: ConversationOpState,
  token: number,
): { state: ConversationOpState; apply: boolean } {
  if (!isCurrentOp(state, token)) return { state, apply: false }
  return { state: { epoch: state.epoch, active: null }, apply: true }
}

export function activeOpKind(state: ConversationOpState): ConversationOpKind | null {
  return state.active ? state.active.kind : null
}

// Plain words for the disabled input / buttons.
export function busyHint(state: ConversationOpState): string | null {
  switch (activeOpKind(state)) {
    case 'resume': return '正在加载历史对话，请稍候'
    case 'reset': return '正在开始新对话，请稍候'
    case 'send': return '回复生成中：请等它结束，或先点“停止”'
    default: return null
  }
}
