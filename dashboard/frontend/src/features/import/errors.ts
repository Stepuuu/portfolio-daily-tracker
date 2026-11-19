// What a refused import request means for the person: a title of its own per status code, the backend's
// Chinese `detail` as the text, and the one next step that helps (retry, reload the session, refresh the
// preview, go back to the preview, start over). The status codes are the ones the API contract lists
// (backend/api/imports.py and the ledger's confirm). Pure TypeScript.

export type NextStep =
  /** Send the same request again (the ledger or store was busy, or no answer came). */
  | 'retry'
  /** Read the session again and drop what this page held (it was changed elsewhere). */
  | 'reload'
  /** The preview was refreshed; ask again. */
  | 'again'
  /** The proposal was withdrawn: back to 预览与去重 with the session as it is now. */
  | 'back'
  /** The session is gone (expired or deleted): start a new import. */
  | 'restart'
  /** Nothing to do but read the message (an edit or a file refused as invalid). */
  | 'none'

export interface Failure {
  status: number | undefined
  title: string
  /** The backend's words. */
  message: string
  next: NextStep
}

const GONE = '这次导入已过期或已删除'

function gone(status: number | undefined): boolean {
  return status === 404
}

function busy(status: number | undefined): boolean {
  return status === undefined || status === 503 || status === 502 || status === 504
}

/** POST /parse: 413 too large, 415 unreadable type, 422 invalid, 503 busy. */
export function uploadFailure(status: number | undefined, message: string): Failure {
  if (status === 413) return { status, title: '文件太大', message, next: 'none' }
  if (status === 415) return { status, title: '无法读取这种文件', message, next: 'none' }
  if (status === 422) return { status, title: '这个文件不能导入', message, next: 'none' }
  if (status === 403) return { status, title: '现在不能导入', message, next: 'none' }
  if (status === 409) return { status, title: '导入数据的版本不匹配', message, next: 'none' }
  if (busy(status)) return { status, title: '没有上传成功', message, next: 'retry' }
  return { status, title: '没有上传成功', message, next: 'none' }
}

/** GET /{id}: 404 expired or deleted. */
export function readFailure(status: number | undefined, message: string): Failure {
  if (gone(status)) return { status, title: GONE, message, next: 'restart' }
  return { status, title: '读取导入记录失败', message, next: 'retry' }
}

/** PATCH /{id}: 422 refused (nothing changed), 409 edited elsewhere (reload), 503 ledger busy (retry), 404 gone. */
export function patchFailure(status: number | undefined, message: string): Failure {
  if (status === 422) return { status, title: '没有保存这项修改', message, next: 'none' }
  if (status === 409) return { status, title: '导入记录在别处被修改了', message, next: 'reload' }
  if (gone(status)) return { status, title: GONE, message, next: 'restart' }
  if (status === 503) return { status, title: '账本正忙，修改没有保存', message, next: 'retry' }
  if (busy(status)) return { status, title: '修改没有保存', message, next: 'retry' }
  return { status, title: '修改没有保存', message, next: 'reload' }
}

/**
 * POST /{id}/propose: 409 nothing to propose, a blocker, or the ledger or session changed while the batch was
 * prepared (the preview is refreshed; ask again); 422 the ledger refused the events; 503 busy; 404 gone.
 */
export function proposeFailure(status: number | undefined, message: string): Failure {
  if (status === 409) return { status, title: '没有生成入账预览', message, next: 'again' }
  if (status === 422) return { status, title: '账本不接受这批记录', message, next: 'none' }
  if (gone(status)) return { status, title: GONE, message, next: 'restart' }
  if (busy(status)) return { status, title: '没有生成入账预览', message, next: 'retry' }
  return { status, title: '没有生成入账预览', message, next: 'again' }
}

/** What the ledger review (`useProposalReview`) learned from the last 确认入账 of a preview. */
export interface ConfirmOutcomeLike {
  state: 'refused' | 'not_booked' | 'checking' | 'unknown'
  message?: string
}

/**
 * POST /api/ledger/proposals/{id}/confirm of an import batch, as the ledger review reports it. Refused (the
 * backend answered 4xx: the session changed, expired or was deleted, the ledger changed, the proposal was
 * withdrawn) or read back as gone: nothing was booked and this preview can never be; back to 预览与去重 with
 * the session as it is now. A lost answer (no answer, a gateway's 5xx) is not a failure here: it may have
 * been booked, so the review reads the proposal back and says 已入账, 仍未入账 or 结果未知 itself.
 * `unconfirmable` is the review's own verdict (a 仍未入账 read-back can be confirmed again).
 */
export function confirmFailure(outcome: ConfirmOutcomeLike | null | undefined, unconfirmable: boolean): Failure | null {
  if (!outcome) return null
  const final = outcome.state === 'refused' || (outcome.state === 'not_booked' && unconfirmable)
  return final ? { status: undefined, title: '没有入账：这份入账预览已作废', message: outcome.message ?? '', next: 'back' } : null
}

/** DELETE /{id}: 404 gone already, 409 changed by another request meanwhile (discard again). */
export function deleteFailure(status: number | undefined, message: string): Failure {
  if (gone(status)) return { status, title: GONE, message, next: 'restart' }
  if (status === 409) return { status, title: '导入记录刚刚被修改了，没有放弃', message, next: 'retry' }
  return { status, title: '没有放弃这次导入', message, next: busy(status) ? 'retry' : 'none' }
}

export const NEXT_LABEL: Record<NextStep, string | null> = {
  retry: '重试',
  reload: '重新载入',
  again: '重新生成入账预览',
  back: '回到预览',
  restart: '重新开始',
  none: null,
}
