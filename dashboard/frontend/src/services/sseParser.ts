// Incremental parser for the chat stream ("data: <json string>" lines, ended by "data: [DONE]").
// Network reads can end anywhere, including inside a line or a multi-byte character,
// so bytes are decoded as a stream and only complete lines are interpreted.

export interface SseChunkParser {
  /** Feed one network read. Returns true once the [DONE] marker has been seen. */
  push(bytes: Uint8Array): boolean
  /** Call when the stream closes. Returns true if [DONE] was seen. */
  end(): boolean
}

export function createSseChunkParser(onChunk: (chunk: string) => void): SseChunkParser {
  const decoder = new TextDecoder()
  let buffer = ''
  let finished = false

  const handleLine = (rawLine: string, terminated: boolean) => {
    if (finished) return
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (!line.startsWith('data:')) return
    const data = line.startsWith('data: ') ? line.slice(6) : line.slice(5)
    if (data === '[DONE]') {
      finished = true
      return
    }
    let chunk: string | null = null
    try {
      const parsed: unknown = JSON.parse(data)
      if (typeof parsed === 'string') chunk = parsed
    } catch {
      // Not JSON: handled below.
    }
    if (chunk === null) {
      // A line cut off by the end of the stream is incomplete; drop it instead of
      // showing half a payload. A terminated line that is not JSON is raw text.
      if (!terminated) return
      chunk = data
    }
    onChunk(chunk)
  }

  const drain = (streamClosed: boolean) => {
    let newline = buffer.indexOf('\n')
    while (newline !== -1) {
      handleLine(buffer.slice(0, newline), true)
      buffer = buffer.slice(newline + 1)
      newline = buffer.indexOf('\n')
    }
    if (streamClosed && buffer) {
      handleLine(buffer, false)
      buffer = ''
    }
    return finished
  }

  return {
    push(bytes: Uint8Array) {
      buffer += decoder.decode(bytes, { stream: true })
      return drain(false)
    },
    end() {
      buffer += decoder.decode()
      return drain(true)
    },
  }
}
