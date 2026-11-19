// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The shared Today query (services/today.ts) over a stand-in for GET /api/today: which answer is shown,
// how old the page says it is, and what 刷新 asks the backend for. The HTTP layer is replaced by a
// scripted adapter, so every request is held until the test answers it.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';
import { QueryClient, QueryObserver } from '@tanstack/react-query';

register('./tsxHooks.mjs', import.meta.url);
const { default: api } = await import('../src/services/api.ts');
const today = await import('../src/services/today.ts');

/** Every request the Today query sends, held until `answer` (or aborted by the query). */
let requests = [];
api.defaults.adapter = (config) => new Promise((resolve, reject) => {
  const request = {
    refresh: config.params?.refresh === 1,
    signal: config.signal,
    answer: (data) => resolve({ data, status: 200, statusText: 'OK', headers: {}, config, request: {} }),
  };
  config.signal?.addEventListener?.('abort', () => reject(Object.assign(new Error('canceled'), { __CANCEL__: true })));
  requests.push(request);
});

const snapshot = (time, revision, extra = {}) => ({
  as_of: time.includes('T') ? time : `2026-11-17T${time}+08:00`, ledger_revision: revision, accounts: [], ...extra,
});

async function until(condition, what) {
  for (let i = 0; i < 200; i += 1) {
    if (condition()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail(`timed out waiting for ${what}`);
}

function start() {
  requests = [];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const observer = new QueryObserver(client, { ...today.todayQuery, staleTime: 0 });
  const stop = observer.subscribe(() => {});
  const shown = () => client.getQueryData(today.TODAY_KEY);
  return { client, observer, stop, shown };
}

test('the answer to the later request is shown, whatever its own time or ledger revision says', async () => {
  const { client, stop, shown } = start();
  await until(() => requests.length === 1, 'the first load');
  requests[0].answer(snapshot('10:33:00', 10));
  await until(() => shown(), 'the first answer');
  // The server's clock was corrected back three minutes while the ledger moved on to revision 11.
  let next = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 2, 'the second request');
  requests[1].answer(snapshot('10:30:00', 11));
  await next;
  assert.equal(shown().ledger_revision, 11, 'the confirmed revision 11 is shown, not the older answer');
  // A backup was restored: a lower revision, at the same moment.
  next = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 3, 'the third request');
  requests[2].answer(snapshot('10:30:00', 9));
  await next;
  assert.equal(shown().ledger_revision, 9);
  // An answer whose time cannot be read does not freeze the page on the previous one, nor after it.
  next = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 4, 'the fourth request');
  requests[3].answer(snapshot('not a time', 12));
  await next;
  assert.equal(shown().ledger_revision, 12);
  next = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 5, 'the fifth request');
  requests[4].answer(snapshot('10:29:00', 13));
  await next;
  assert.equal(shown().ledger_revision, 13);
  stop();
  client.clear();
});

test('the age the page shows is the age of the snapshot it shows', async () => {
  const { client, stop, shown } = start();
  await until(() => requests.length === 1, 'the first load');
  const before = Date.now();
  requests[0].answer(snapshot('10:33:00', 10));
  await until(() => shown(), 'the first answer');
  const received = today.todayReceivedAt(shown());
  assert.ok(received >= before && received <= Date.now(), 'stamped when it arrived');
  assert.equal(today.todayReceivedAt(undefined), null);
  assert.equal(today.todayReceivedAt({ ...shown() }), null, 'a payload that never arrived has no age');
  stop();
  client.clear();
});

test('an older request that answers after a newer one never replaces it, and keeps its own age', async () => {
  const { createSnapshotOrder } = await import('../src/features/today/feed.ts');
  let clock = 1000;
  const order = createSnapshotOrder(() => clock);
  const first = order.issue();
  const second = order.issue();
  const newer = order.received(snapshot('10:30:00', 11), second);
  clock = 2000;
  const older = order.received(snapshot('10:33:00', 10), first);
  assert.equal(order.pick(newer, older), newer);
  assert.equal(order.pick(older, newer), newer);
  assert.equal(order.pick(undefined, older), older);
  assert.equal(order.receivedAt(newer), 1000, 'the shown snapshot is as old as it is, not renewed by the late answer');
});

test('刷新 during the first load asks the backend again with refresh=1, and only that answer lands', async () => {
  const { client, stop, shown } = start();
  await until(() => requests.length === 1, 'the first load');
  assert.equal(requests[0].refresh, false);
  const refreshed = today.refreshToday(client);
  await until(() => requests.length === 2, 'the refresh request');
  assert.equal(requests[1].refresh, true, 'the refresh skips the quote cache');
  assert.equal(requests[0].signal.aborted, true, 'the load without refresh was replaced');
  requests[1].answer(snapshot('10:33:05', 4, { source: 'refreshed' }));
  requests[0].answer(snapshot('10:33:00', 4, { source: 'cached' }));
  await refreshed;
  assert.equal(shown().source, 'refreshed');
  // With data on screen a poll in flight is replaced the same way; the next poll asks without refresh.
  const poll = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 3, 'a poll');
  const again = today.refreshToday(client);
  await until(() => requests.length === 4, 'the second refresh');
  assert.deepEqual([requests[2].refresh, requests[3].refresh, requests[2].signal.aborted], [false, true, true]);
  requests[3].answer(snapshot('10:33:09', 4, { source: 'refreshed again' }));
  await again;
  await poll.catch(() => {});
  assert.equal(shown().source, 'refreshed again');
  const later = client.refetchQueries({ queryKey: today.TODAY_KEY });
  await until(() => requests.length === 5, 'the next poll');
  assert.equal(requests[4].refresh, false);
  requests[4].answer(snapshot('10:33:14', 4));
  await later;
  stop();
  client.clear();
});
