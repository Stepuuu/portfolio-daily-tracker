// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The bell's paging over GET /api/notifications: first page newest first, polls with after=<cursor>
// (oldest first), older pages with before=<oldest shown>. Nothing is skipped or shown twice.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  FIRST_PAGE, MAX_KEPT, POLL_MS, POLL_PAGE, addNewer, addOlder, badgeCount, firstPage, markAllRead, markRead, nextPollDelay,
  oldestSeq, rollbackRead,
} from '../src/features/notifications/inbox.ts';

const item = (seq, read = false) => ({
  seq, kind: 'alert', level: 'warning', title: `#${seq}`, body: '', data: null, created_at: '2026-11-17T02:00:00Z',
  read_at: read ? '2026-11-17T03:00:00Z' : null,
});

test('the first page sets the cursor to the newest seq in the inbox', () => {
  const inbox = firstPage({ items: [item(9), item(7), item(3)], unread: 3, last_seq: 9 });
  assert.deepEqual(inbox.items.map((entry) => entry.seq), [9, 7, 3]);
  assert.equal(inbox.cursor, 9);
  assert.equal(inbox.unread, 3);
  assert.equal(inbox.hasOlder, false);
  // An empty inbox polls from its last seq (rows may have been pruned).
  assert.equal(firstPage({ items: [], unread: 0, last_seq: 12 }).cursor, 12);
  assert.equal(firstPage({ items: Array.from({ length: FIRST_PAGE }, (_, i) => item(100 - i)), unread: 0, last_seq: 100 }).hasOlder, true);
});

test('newer pages arrive oldest first; the cursor moves to the last item; a full page asks again', () => {
  let inbox = firstPage({ items: [item(5)], unread: 1, last_seq: 5 });
  let result = addNewer(inbox, { items: [item(6), item(8)], unread: 3, last_seq: 8 });
  inbox = result.inbox;
  assert.deepEqual(inbox.items.map((entry) => entry.seq), [8, 6, 5]);
  assert.equal(inbox.cursor, 8);
  assert.equal(inbox.unread, 3);
  assert.equal(result.more, false);
  const full = Array.from({ length: POLL_PAGE }, (_, i) => item(9 + i));
  result = addNewer(inbox, { items: full, unread: 203, last_seq: 500 });
  assert.equal(result.more, true);
  assert.equal(result.inbox.cursor, 9 + POLL_PAGE - 1, 'continues from the last item, not from last_seq');
  // An empty poll keeps the cursor; a repeated item is not shown twice.
  assert.equal(addNewer(inbox, { items: [], unread: 3, last_seq: 8 }).inbox.cursor, 8);
  assert.equal(addNewer(inbox, { items: [item(8)], unread: 3, last_seq: 8 }).inbox.items.length, 3);
});

test('older pages go below, until a short page', () => {
  const inbox = firstPage({ items: Array.from({ length: FIRST_PAGE }, (_, i) => item(40 - i)), unread: 0, last_seq: 40 });
  assert.equal(oldestSeq(inbox), 21);
  const older = addOlder(inbox, { items: [item(20), item(18)], unread: 0, last_seq: 40 });
  assert.deepEqual(older.items.slice(-2).map((entry) => entry.seq), [20, 18]);
  assert.equal(older.hasOlder, false);
  assert.equal(oldestSeq(firstPage({ items: [], unread: 0, last_seq: 0 })), null);
});

test('marking read updates the item and the unread count', () => {
  const inbox = firstPage({ items: [item(3), item(2), item(1, true)], unread: 2, last_seq: 3 });
  const one = markRead(inbox, 3);
  assert.ok(one.items[0].read_at);
  assert.equal(one.unread, 1);
  assert.equal(markRead(one, 3).unread, 1, 'reading twice changes nothing');
  assert.equal(markRead(inbox, 3, 7).unread, 7, 'the server count wins when known');
  const all = markAllRead(inbox, 2);
  assert.deepEqual(all.items.map((entry) => Boolean(entry.read_at)), [false, true, true]);
  assert.equal(all.unread, 1);
});

test('badge count', () => {
  assert.equal(badgeCount(0), null);
  assert.equal(badgeCount(-1), null);
  assert.equal(badgeCount(7), '7');
  assert.equal(badgeCount(100), '99+');
});

test('a mark-read the server refused is undone: the rows are unread again, so a click retries', () => {
  const inbox = firstPage({ items: [item(3), item(2), item(1, true)], unread: 2, last_seq: 3 });
  const at = '2026-11-17T03:00:00.000Z';
  const marked = markRead(inbox, 3, undefined, at);
  assert.equal(marked.unread, 1);
  const undone = rollbackRead(marked, [3], at, marked);
  assert.equal(undone.items[0].read_at, null, 'unread again');
  assert.equal(undone.unread, 2, 'the badge counts it again');
  // Only the rows this attempt marked: one read earlier stays read.
  assert.ok(undone.items[2].read_at);
  // A server count that arrived meanwhile is already the truth (the server never marked it): kept as is.
  const polled = addNewer(marked, { items: [item(4)], unread: 3, last_seq: 4 }).inbox;
  const afterPoll = rollbackRead(polled, [3], at, marked);
  assert.equal(afterPoll.unread, 3);
  assert.equal(afterPoll.items.find((entry) => entry.seq === 3).read_at, null);
  // Read-all is undone the same way.
  const all = markAllRead(inbox, 3, undefined, at);
  const allUndone = rollbackRead(all, [3, 2, 1], at, all);
  assert.deepEqual(allUndone.items.map((entry) => Boolean(entry.read_at)), [false, false, true]);
  assert.equal(allUndone.unread, 2);
});

test('polling backs off on repeated failures, up to five minutes', () => {
  assert.equal(nextPollDelay(0), POLL_MS);
  assert.equal(POLL_MS, 15_000);
  assert.deepEqual([1, 2, 3, 4, 5, 20].map(nextPollDelay), [30_000, 60_000, 120_000, 240_000, 300_000, 300_000]);
});

test('polling keeps a bounded list: the oldest go, and can be loaded again', () => {
  let inbox = firstPage({ items: [item(1)], unread: 0, last_seq: 1 });
  for (let start = 2; start < 2 + 3 * POLL_PAGE; start += POLL_PAGE) {
    const page = Array.from({ length: POLL_PAGE }, (_, i) => item(start + i, true));
    inbox = addNewer(inbox, { items: page, unread: 0, last_seq: start + POLL_PAGE - 1 }).inbox;
  }
  assert.equal(inbox.items.length, MAX_KEPT);
  assert.equal(inbox.items[0].seq, 1 + 3 * POLL_PAGE, 'the newest are kept');
  assert.equal(inbox.hasOlder, true, 'what was dropped is reachable through 加载更早的通知');
  assert.equal(inbox.cursor, 1 + 3 * POLL_PAGE);
});

// Functions added with the reconciliation below; read through the module so the rest of this file runs
// even where they are missing.
const inboxModule = await import('../src/features/notifications/inbox.ts');

test('overlapping reads end as the server confirmed them: a mark-one failing after a mark-all leaves every row read', () => {
  const { confirmRead } = inboxModule;
  const inbox = firstPage({ items: [item(2), item(1)], unread: 2, last_seq: 2 });
  const T1 = '2026-11-17T03:00:00.001Z';
  const T2 = '2026-11-17T03:00:00.002Z';
  const one = markRead(inbox, 1, undefined, T1);            // click row 1
  const all = markAllRead(one, 2, undefined, T2);           // then 全部已读
  // Mark-all succeeds first, then the single read fails: the server has both rows read.
  const confirmed = confirmRead(all, { upTo: 2 }, 0);
  const failedLater = rollbackRead(confirmed, [1], T1, one);
  assert.deepEqual(failedLater.items.map((entry) => Boolean(entry.read_at)), [true, true]);
  assert.equal(failedLater.unread, 0, 'the badge agrees with the rows');
  // The other order: the single read fails first (row 1 unread again), then mark-all is confirmed.
  const failedFirst = rollbackRead(all, [1], T1, one);
  assert.equal(failedFirst.items.find((entry) => entry.seq === 1).read_at, null);
  const settled = confirmRead(failedFirst, { upTo: 2 }, 0);
  assert.deepEqual(settled.items.map((entry) => Boolean(entry.read_at)), [true, true]);
  assert.equal(settled.unread, 0);
  // A confirmed single read is not undone by a read-all that failed after it.
  const single = confirmRead(one, { seq: 1 }, 1);
  const allOnTop = markAllRead(single, 2, undefined, T2);
  const allFailed = rollbackRead(allOnTop, [2, 1], T2, allOnTop);
  assert.deepEqual(allFailed.items.map((entry) => [entry.seq, Boolean(entry.read_at)]), [[2, false], [1, true]]);
  assert.equal(allFailed.unread, 1);
  // Everything still unread when both fail.
  const bothFailed = rollbackRead(rollbackRead(all, [2], T2, all), [1], T1, one);
  assert.deepEqual(bothFailed.items.map((entry) => Boolean(entry.read_at)), [false, false]);
  assert.equal(bothFailed.unread, 2);
});

test('rows loaded with 加载更早的通知 stay through later polls until the panel closes; then the list is bounded again', () => {
  const { releaseOlder } = inboxModule;
  // The newest MAX_KEPT from polling (older ones exist on the server).
  let inbox = firstPage({ items: Array.from({ length: FIRST_PAGE }, (_, i) => item(300 - i, true)), unread: 0, last_seq: 300 });
  inbox = addOlder(inbox, { items: Array.from({ length: MAX_KEPT - FIRST_PAGE }, (_, i) => item(280 - i, true)), unread: 0, last_seq: 300 }, MAX_KEPT - FIRST_PAGE);
  assert.equal(inbox.items.length, MAX_KEPT);
  // The user asks for older ones: twenty more, below the bound.
  inbox = addOlder(inbox, { items: Array.from({ length: FIRST_PAGE }, (_, i) => item(100 - i, true)), unread: 0, last_seq: 300 });
  assert.equal(inbox.items.length, MAX_KEPT + FIRST_PAGE);
  // A poll brings one new notification: nothing the user loaded disappears.
  inbox = addNewer(inbox, { items: [item(301)], unread: 1, last_seq: 301 }).inbox;
  assert.equal(inbox.items.length, MAX_KEPT + FIRST_PAGE + 1);
  assert.equal(oldestSeq(inbox), 81, 'the oldest loaded row is still there');
  // Closing the panel bounds the list again; what was dropped can be loaded again.
  const closed = releaseOlder(inbox);
  assert.equal(closed.items.length, MAX_KEPT);
  assert.equal(closed.items[0].seq, 301);
  assert.equal(closed.hasOlder, true);
  // Later polls bound it as before.
  const polled = addNewer(closed, { items: [item(302)], unread: 2, last_seq: 302 }).inbox;
  assert.equal(polled.items.length, MAX_KEPT);
  // Releasing a list that was never extended changes nothing.
  const small = firstPage({ items: [item(3), item(2)], unread: 0, last_seq: 3 });
  assert.deepEqual(releaseOlder(small).items, small.items);
});
