// The notification bell's list, kept in step with `GET /api/notifications` (backend/api/notifications.py):
// the first page comes newest first; polling asks `after=<cursor>` and gets the newer ones oldest first;
// older pages come with `before=<smallest seq shown>`. Pure functions, so the paging rules are tested.

export type NotificationLevel = 'info' | 'warning' | 'critical'

export interface Notification {
  seq: number
  kind: string
  level: NotificationLevel | string
  title: string
  body: string
  data: Record<string, unknown> | null
  created_at: string
  read_at: string | null
}

export interface InboxPage {
  items: Notification[]
  unread: number
  last_seq: number
}

export interface Inbox {
  /** Newest first, no duplicates. */
  items: Notification[]
  /** The highest seq seen; the next poll asks for `after=cursor`. */
  cursor: number
  /** Unread in the whole inbox (from the server). */
  unread: number
  /** Whether older notifications may exist beyond the last item shown. */
  hasOlder: boolean
  /** Counts the server's unread counts received; an optimistic change leaves it alone. */
  synced: number
  /**
   * Reads the server confirmed: everything up to `upTo` (全部已读) and single rows. A read that fails
   * later never makes these rows unread again, and a confirmation marks them read whatever came before.
   */
  confirmed: { upTo: number; seqs: number[] }
  /**
   * The oldest seq the person loaded with 加载更早的通知 while the panel is open: polling keeps every row
   * from it on, beyond the usual bound, until the panel closes (`releaseOlder`). Null otherwise.
   */
  keepFrom: number | null
}

/** A mark-read: one row, or everything up to a seq (全部已读). */
export type ReadOperation = { seq: number } | { upTo: number }

function covers(operation: ReadOperation, seq: number): boolean {
  return 'seq' in operation ? operation.seq === seq : seq <= operation.upTo
}

function confirmedRead(inbox: Inbox, seq: number): boolean {
  return seq <= inbox.confirmed.upTo || inbox.confirmed.seqs.includes(seq)
}

export const FIRST_PAGE = 20
export const POLL_PAGE = 200
/** At most this many notifications are kept from polling; older ones can be loaded again on request. */
export const MAX_KEPT = 200
export const POLL_MS = 15_000
const MAX_POLL_MS = 300_000

/** The wait before the next poll after `failures` failed polls in a row: 15 s, then doubling, at most 5 min. */
export function nextPollDelay(failures: number): number {
  return Math.min(POLL_MS * 2 ** Math.max(0, failures), MAX_POLL_MS)
}

function byNewest(items: Notification[]): Notification[] {
  const seen = new Map<number, Notification>()
  for (const item of items) seen.set(item.seq, item)
  return [...seen.values()].sort((a, b) => b.seq - a.seq)
}

/** The inbox after the first page (no cursor, newest first). */
export function firstPage(page: InboxPage, limit = FIRST_PAGE): Inbox {
  return {
    items: byNewest(page.items),
    cursor: Math.max(page.last_seq, ...page.items.map((item) => item.seq), 0),
    unread: page.unread,
    hasOlder: page.items.length >= limit,
    synced: 1,
    confirmed: { upTo: 0, seqs: [] },
    keepFrom: null,
  }
}

/** How many of the newest rows a list keeps: MAX_KEPT, or more while rows loaded on request are open. */
function bound(items: Notification[], keepFrom: number | null): number {
  if (keepFrom === null) return MAX_KEPT
  return Math.max(MAX_KEPT, items.filter((item) => item.seq >= keepFrom).length)
}

/**
 * Adds a page of newer notifications (`after=cursor`, oldest first). `more` is true when the page was
 * full: more are waiting and the caller asks again right away.
 */
export function addNewer(inbox: Inbox, page: InboxPage, limit = POLL_PAGE): { inbox: Inbox; more: boolean } {
  const last = page.items.length ? page.items[page.items.length - 1].seq : inbox.cursor
  const items = byNewest([...page.items, ...inbox.items])
  // A long-lived page keeps only the newest MAX_KEPT; the rest stay on the server (加载更早的通知). What the
  // person loaded on request stays while the panel is open.
  const kept = items.slice(0, bound(items, inbox.keepFrom))
  return {
    inbox: {
      ...inbox, items: kept, cursor: Math.max(inbox.cursor, last), unread: page.unread,
      hasOlder: inbox.hasOlder || kept.length < items.length, synced: inbox.synced + 1,
    },
    more: page.items.length >= limit,
  }
}

/**
 * Adds a page of older notifications (`before=<oldest shown>`, newest first), asked for by the person:
 * they stay, through polls, until the panel closes.
 */
export function addOlder(inbox: Inbox, page: InboxPage, limit = FIRST_PAGE): Inbox {
  const items = byNewest([...inbox.items, ...page.items])
  return {
    ...inbox, items, unread: page.unread, hasOlder: page.items.length >= limit, synced: inbox.synced + 1,
    keepFrom: items.length ? items[items.length - 1].seq : inbox.keepFrom,
  }
}

/** The panel closed: the list is bounded to the newest MAX_KEPT again; the rest can be loaded again. */
export function releaseOlder(inbox: Inbox): Inbox {
  if (inbox.keepFrom === null) return inbox
  const items = inbox.items.slice(0, MAX_KEPT)
  return { ...inbox, items, keepFrom: null, hasOlder: inbox.hasOlder || items.length < inbox.items.length }
}

/**
 * A mark-read the server accepted, with its unread count: the rows it covers are read (also one that a
 * failed read had made unread again), and stay read whatever fails later.
 */
export function confirmRead(inbox: Inbox, operation: ReadOperation, unread: number, at = new Date().toISOString()): Inbox {
  const items = inbox.items.map((item) => (covers(operation, item.seq) && !item.read_at ? { ...item, read_at: at } : item))
  const confirmed = 'seq' in operation
    ? { ...inbox.confirmed, seqs: [...inbox.confirmed.seqs.filter((seq) => seq !== operation.seq), operation.seq] }
    : { ...inbox.confirmed, upTo: Math.max(inbox.confirmed.upTo, operation.upTo) }
  return { ...inbox, items, unread, synced: inbox.synced + 1, confirmed }
}

/** The `before` cursor for the next older page; null when nothing is shown. */
export function oldestSeq(inbox: Inbox): number | null {
  return inbox.items.length ? inbox.items[inbox.items.length - 1].seq : null
}

/** One notification marked read locally (the server's unread count follows when known). */
export function markRead(inbox: Inbox, seq: number, unread?: number, at = new Date().toISOString()): Inbox {
  let changed = 0
  const items = inbox.items.map((item) => {
    if (item.seq !== seq || item.read_at) return item
    changed += 1
    return { ...item, read_at: at }
  })
  return { ...inbox, items, unread: unread ?? Math.max(0, inbox.unread - changed) }
}

/** Everything up to `upTo` marked read (what the user has been shown). */
export function markAllRead(inbox: Inbox, upTo: number, unread?: number, at = new Date().toISOString()): Inbox {
  let changed = 0
  const items = inbox.items.map((item) => {
    if (item.seq > upTo || item.read_at) return item
    changed += 1
    return { ...item, read_at: at }
  })
  return { ...inbox, items, unread: unread ?? Math.max(0, inbox.unread - changed) }
}

/** "99+" above 99, nothing at zero. */
export function badgeCount(unread: number): string | null {
  if (!unread || unread < 0) return null
  return unread > 99 ? '99+' : String(unread)
}

/**
 * Undoes an optimistic `markRead` / `markAllRead` (stamped `at`) that the server refused: those rows are
 * unread again, so clicking one retries, and the count goes back up by them, unless a server count
 * arrived since `marked` (it is the truth then: the server never marked them). A row another read has
 * confirmed in the meantime stays read: the server has it read.
 */
export function rollbackRead(inbox: Inbox, seqs: number[], at: string, marked: Pick<Inbox, 'synced'>): Inbox {
  const wanted = new Set(seqs)
  let restored = 0
  const items = inbox.items.map((item) => {
    if (!wanted.has(item.seq) || item.read_at !== at || confirmedRead(inbox, item.seq)) return item
    restored += 1
    return { ...item, read_at: null }
  })
  return { ...inbox, items, unread: inbox.synced === marked.synced ? inbox.unread + restored : inbox.unread }
}
