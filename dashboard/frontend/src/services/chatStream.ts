// Reads one streamed chat reply: POSTs the request, feeds the response body to the SSE parser,
// and guarantees that the caller gets an answer — text, a cancellation, or a timeout.
//
// Without this a stalled upstream (connection open, nothing arriving) left the chat in its
// loading state for good: fetch has no timeout of its own and nothing could abort it.
//
// - `signal` lets the caller stop the reply (the Stop button).
// - `inactivityTimeoutMs` bounds the silence between network reads, including the wait for the
//   response to start. It is not a limit on the total length of a reply: every read resets it.
//
// How the bytes are interpreted is entirely up to sseParser.ts.

import { createSseChunkParser } from './sseParser.ts'

/** Longest silence tolerated before the reply is given up on. */
export const CHAT_STREAM_INACTIVITY_TIMEOUT_MS = 120000

export class ChatStreamCancelledError extends Error {
  constructor() {
    super('已停止生成')
    this.name = 'ChatStreamCancelledError'
  }
}

export class ChatStreamTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`已经 ${Math.round(timeoutMs / 1000)} 秒没有收到新的内容，已停止等待`)
    this.name = 'ChatStreamTimeoutError'
  }
}

export interface ChatStreamOptions {
  signal?: AbortSignal
  inactivityTimeoutMs?: number
  /** For tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch
}

async function errorDetail(response: Response): Promise<string> {
  const rawText = await response.text()
  let detail: unknown = rawText
  try {
    const data = JSON.parse(rawText)
    detail = data.detail || data.message || rawText
  } catch {
    // keep raw text
  }
  return (typeof detail === 'string' && detail) || `Stream request failed (${response.status})`
}

/**
 * Resolves when the reply is complete ([DONE] seen, or the server closed the stream).
 * Rejects with ChatStreamCancelledError, ChatStreamTimeoutError, or the request's own error.
 * Chunks delivered to `onChunk` before a rejection stay delivered.
 */
export async function readChatStream(
  url: string,
  body: unknown,
  onChunk: (chunk: string) => void,
  options: ChatStreamOptions = {},
): Promise<void> {
  const timeoutMs = options.inactivityTimeoutMs ?? CHAT_STREAM_INACTIVITY_TIMEOUT_MS
  const doFetch = options.fetchImpl ?? fetch
  const controller = new AbortController()
  // Why the request was stopped, if it was. Set from event and timer callbacks.
  const state: { stopped: 'cancelled' | 'timeout' | null } = { stopped: null }
  let timer: ReturnType<typeof setTimeout> | undefined
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined

  // Rejects as soon as the request is stopped, so a fetch or a read that ignores the abort
  // signal (or simply never settles) cannot keep the caller waiting.
  const stop = new Promise<never>((_, reject) => {
    controller.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true })
  })
  stop.catch(() => {})

  const halt = (why: 'cancelled' | 'timeout') => {
    if (state.stopped === null) state.stopped = why
    controller.abort()
  }
  const onCallerAbort = () => halt('cancelled')
  const armTimer = () => {
    clearTimeout(timer)
    timer = setTimeout(() => halt('timeout'), timeoutMs)
  }
  // A step that had already settled can win a race against `stop`, so also check explicitly.
  const throwIfStopped = () => {
    if (state.stopped !== null) throw new Error('aborted')
  }

  try {
    if (options.signal?.aborted) halt('cancelled')
    else options.signal?.addEventListener('abort', onCallerAbort, { once: true })
    throwIfStopped()
    armTimer()

    const response = await Promise.race([
      stop,
      doFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      }),
    ])
    throwIfStopped()
    armTimer()

    if (!response.ok) throw new Error(await Promise.race([stop, errorDetail(response)]))

    reader = response.body?.getReader()
    if (!reader) return

    const parser = createSseChunkParser(onChunk)
    while (true) {
      const { done, value } = await Promise.race([stop, reader.read()])
      throwIfStopped()
      if (done) break
      armTimer()
      if (value && parser.push(value)) return
    }
    parser.end()
  } catch (error) {
    if (state.stopped === 'cancelled') throw new ChatStreamCancelledError()
    if (state.stopped === 'timeout') throw new ChatStreamTimeoutError(timeoutMs)
    throw error
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onCallerAbort)
    // Release the connection whether the reply finished, failed, or was stopped.
    void reader?.cancel().catch(() => {})
  }
}
