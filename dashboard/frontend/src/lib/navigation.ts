// Navigation rules over the route table in src/routes.tsx: which sections and pages a mode shows, which
// page a URL belongs to, and where redirects lead. Pure TypeScript with structural types, so node tests
// can check the real table.

/** full: everything. lab (TRACKER_LAB_ONLY=1): research and settings only. demo: everything, read-only. */
export type Mode = 'full' | 'lab' | 'demo'

export const MODES: Mode[] = ['full', 'lab', 'demo']

export interface PageLike {
  path: string
  label: string
  /** Modes that show the page; every mode when omitted. */
  modes?: Mode[]
}

export interface SectionLike<P extends PageLike = PageLike> {
  id: string
  label: string
  pages: P[]
  mobile: 'primary' | 'more'
}

export interface Redirect {
  from: string
  to: string
}

/** Why write controls are disabled in demo mode (the backend refuses every write there). */
export const DEMO_REASON = '演示模式只读'
/** Why the chat assistant's settings cannot be saved in research-only mode: that launcher starts no assistant. */
export const NO_ASSISTANT_REASON = '研究模式没有启动对话助手，对话模型请在完整模式下保存'
/** Why they are disabled while the backend's mode cannot be read (it may be a read-only demo). */
export const UNKNOWN_MODE_REASON = '暂时无法确认运行模式，修改操作已暂停'
const PENDING_REASON = '正在确认运行模式'

export interface ResolvedMode {
  /** The mode navigation follows; undefined until the backend has answered. */
  mode: Mode | undefined
  /** The status endpoint failed for another reason than being absent: the real mode is not known. */
  unknown: boolean
  /** Write controls are disabled (demo, unknown, not yet answered). */
  readOnly: boolean
  /** Shown on disabled write controls; undefined when writes are allowed. */
  readOnlyReason: string | undefined
}

/**
 * The mode from `GET /api/app/status`. Only an older backend of this app, confirmed to have no such
 * endpoint (`absent`, see `isMissingRoute`), means full; any other failure (timeout, 5xx, network, a 404
 * nobody confirmed, an answer without a known mode) is `unknown`: every page is shown (as in full mode)
 * but nothing can be written or sent, since a read-only demo server must not turn writable because its
 * status could not be read.
 */
export function resolveAppMode(input: { mode?: Mode; failed: boolean; status?: number; absent?: boolean }): ResolvedMode {
  if (input.mode) {
    const demo = input.mode === 'demo'
    return { mode: input.mode, unknown: false, readOnly: demo, readOnlyReason: demo ? DEMO_REASON : undefined }
  }
  if (input.failed && input.absent) return { mode: 'full', unknown: false, readOnly: false, readOnlyReason: undefined }
  if (input.failed) return { mode: 'full', unknown: true, readOnly: true, readOnlyReason: UNKNOWN_MODE_REASON }
  return { mode: undefined, unknown: false, readOnly: true, readOnlyReason: PENDING_REASON }
}

/** The mode in a `GET /api/app/status` answer: one the shell knows, else null (`{}`, junk, a new mode). */
export function statusMode(body: unknown): Mode | null {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null
  const mode = (body as { mode?: unknown }).mode
  return typeof mode === 'string' && (MODES as string[]).includes(mode) ? (mode as Mode) : null
}

/**
 * A 404 that FastAPI itself sends for a path the application has no route for (`{"detail": "Not Found"}`).
 * A proxy, a dev server or a static host answers a 404 differently, so its 404 says nothing about the
 * backend. The shell also asks the backend's settings endpoint before trusting it (useAppMode).
 */
export function isMissingRoute(status: number | undefined, body: unknown): boolean {
  return status === 404 && !!body && typeof body === 'object' && (body as { detail?: unknown }).detail === 'Not Found'
}

export function allowedIn(item: { modes?: Mode[] }, mode: Mode): boolean {
  return !item.modes || item.modes.includes(mode)
}

/** Sections with only the pages the mode shows; sections left empty are dropped. */
export function visibleSections<P extends PageLike, S extends SectionLike<P>>(sections: S[], mode: Mode): S[] {
  return sections
    .map((section) => ({ ...section, pages: section.pages.filter((page) => allowedIn(page, mode)) }))
    .filter((section) => section.pages.length > 0)
}

/** "/ledger/" → "/ledger"; the root stays "/". */
export function normalizePath(pathname: string): string {
  const path = pathname.split(/[?#]/, 1)[0] || '/'
  return path.length > 1 ? path.replace(/\/+$/, '') || '/' : path
}

function matches(pagePath: string, pathname: string): boolean {
  const base = pagePath.replace(/\/\*$/, '')
  if (base === '/') return pathname === '/'
  return pathname === base || pathname.startsWith(`${base}/`)
}

/** The section and page a URL belongs to (the longest matching page path), or null. */
export function findPage<P extends PageLike, S extends SectionLike<P>>(sections: S[], pathname: string): { section: S; page: P } | null {
  const path = normalizePath(pathname)
  let best: { section: S; page: P } | null = null
  for (const section of sections) {
    for (const page of section.pages) {
      if (matches(page.path, path) && (!best || page.path.length > best.page.path.length)) best = { section, page }
    }
  }
  return best
}

/**
 * Where a redirect sends `pathname`, or null. The query string and hash travel with it (an old link to a
 * proposal still opens that proposal): taken from `pathname` itself, or from `search` / `hash` (as a
 * router location gives them).
 */
export function resolveRedirect(redirects: Redirect[], pathname: string, search = '', hash = ''): string | null {
  const path = normalizePath(pathname)
  const target = redirects.find((redirect) => redirect.from === path)?.to
  if (target === undefined) return null
  const hashAt = pathname.indexOf('#')
  const ownHash = hashAt >= 0 ? pathname.slice(hashAt) : ''
  const beforeHash = hashAt >= 0 ? pathname.slice(0, hashAt) : pathname
  const queryAt = beforeHash.indexOf('?')
  const ownSearch = queryAt >= 0 ? beforeHash.slice(queryAt) : ''
  const query = search || (ownSearch === '?' ? '' : ownSearch)
  return `${target}${query}${hash || ownHash}`
}

/** The first page of the first section the mode shows: where `/` goes when 今日 is not available. */
export function homePath<P extends PageLike, S extends SectionLike<P>>(sections: S[], mode: Mode): string {
  return visibleSections(sections, mode)[0]?.pages[0]?.path ?? '/'
}

/** The bottom bar's own entries and the ones under 更多. With no primary section, all of them show. */
export function mobileSplit<S extends SectionLike>(sections: S[]): { primary: S[]; more: S[] } {
  const primary = sections.filter((section) => section.mobile === 'primary')
  if (!primary.length) return { primary: sections, more: [] }
  return { primary, more: sections.filter((section) => section.mobile !== 'primary') }
}

/** The browser tab title: "回测 · 研究", or just "今日" when page and section share a name. */
export function documentTitle<P extends PageLike, S extends SectionLike<P>>(match: { section: S; page: P } | null, app = '交易助手'): string {
  if (!match) return app
  const { section, page } = match
  return [page.label, section.label !== page.label ? section.label : '', app].filter(Boolean).join(' · ')
}
