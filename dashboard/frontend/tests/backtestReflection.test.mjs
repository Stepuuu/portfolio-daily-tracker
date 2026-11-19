// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { freshestReflection, pollReflection, POLL_MAX_ATTEMPTS } from '../src/features/backtest/reflection.ts';

const snapshots = (...states) => {
  const calls = [];
  const fetchRun = async (runId) => {
    calls.push(runId);
    const next = states.length > 1 ? states.shift() : states[0];
    if (next instanceof Error) throw next;
    return { run_id: runId, status: 'completed', reflection: next };
  };
  return { calls, fetchRun };
};
const noSleep = { sleeps: [], sleep(ms) { this.sleeps.push(ms); return Promise.resolve(); } };
const pending = { status: 'pending', result: null, message: null };

test('polls the run until the reading is done and reports progress', async () => {
  const done = { status: 'completed', result: { analysis: 'ok', lessons: ['a'], improvements: [] }, message: null };
  const { calls, fetchRun } = snapshots(pending, pending, done);
  const updates = [];
  const sleeper = { ...noSleep, sleeps: [] };
  const state = await pollReflection('r1', fetchRun, {
    intervalMs: 1500, sleep: sleeper.sleep.bind(sleeper), onUpdate: (s) => updates.push(s.status),
  });
  assert.equal(state.status, 'completed');
  assert.deepEqual(state.result, done.result);
  assert.deepEqual(calls, ['r1', 'r1', 'r1']);
  assert.deepEqual(sleeper.sleeps, [1500, 1500, 1500]);
  assert.deepEqual(updates, ['pending', 'pending', 'completed']);
});

test('a failed reading ends the polling with its message', async () => {
  const { fetchRun } = snapshots(pending, { status: 'failed', result: null, message: '解读没有生成（TimeoutError）' });
  const state = await pollReflection('r1', fetchRun, { sleep: () => Promise.resolve() });
  assert.equal(state.status, 'failed');
  assert.match(state.message, /TimeoutError/);
});

test('polling is bounded: it gives up after the last attempt and says where to look', async () => {
  const { calls, fetchRun } = snapshots(pending);
  const state = await pollReflection('r1', fetchRun, { maxAttempts: 4, sleep: () => Promise.resolve() });
  assert.equal(state.status, 'timeout');
  assert.equal(calls.length, 4);
  assert.match(state.message, /历史记录/);
  assert.ok(POLL_MAX_ATTEMPTS > 0 && Number.isInteger(POLL_MAX_ATTEMPTS));
});

test('a failed request counts as an attempt and is retried', async () => {
  const done = { status: 'completed', result: { analysis: 'ok' }, message: null };
  const { calls, fetchRun } = snapshots(new Error('网络错误'), done);
  const state = await pollReflection('r1', fetchRun, { sleep: () => Promise.resolve() });
  assert.equal(state.status, 'completed');
  assert.equal(calls.length, 2);
});

test('nothing being written (the server forgot the run) stops at once', async () => {
  const { calls, fetchRun } = snapshots({ status: 'none', result: null, message: null });
  const state = await pollReflection('r1', fetchRun, { sleep: () => Promise.resolve() });
  assert.equal(state.status, 'failed');
  assert.equal(calls.length, 1);
});

test('a newer run cancels the polling of an older one', async () => {
  let cancelled = false;
  const { calls, fetchRun } = snapshots(pending);
  const state = await pollReflection('r1', fetchRun, {
    sleep: () => Promise.resolve(), onUpdate: () => { cancelled = true; }, isCancelled: () => cancelled,
  });
  assert.equal(state.status, 'cancelled');
  assert.equal(calls.length, 1);
});

test('the backtest page asks for a reading only on request, polls the state without the result, and says it is pending', () => {
  const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
  const hooks = readFileSync(join(src, 'components', 'backtest', 'hooks.ts'), 'utf8');
  const service = readFileSync(join(src, 'services', 'backtest.ts'), 'utf8');
  const view = readFileSync(join(src, 'components', 'backtest', 'Reflection.tsx'), 'utf8');
  assert.match(hooks, /pollReflection\(/);
  assert.match(hooks, /fetchRun\(run, false\)/);                     // the result itself is not re-sent
  assert.match(service, /include_result=false/);
  assert.match(hooks, /status === 'pending'\) poll\(runId\)/);        // a reading already pending is followed
  assert.match(view, /解读生成中/);
  assert.match(view, /模型的看法，不是事实/);
  assert.doesNotMatch(view, /dangerouslySetInnerHTML|Markdown/);
});

// Gate review: a reading generated, then the run reopened from 历史, offered 「生成 AI 解读」 again: the full
// result cached when the run finished (reflection none) won over the state read afterwards (completed).
test('a reopened run shows the reading the server holds, not the one cached with the full result', () => {
  const done = { status: 'completed', result: { analysis: 'ok' }, message: null };
  const none = { status: 'none', result: null, message: null };
  const state = { data: { status: 'completed', reflection: done }, updatedAt: 2000 };
  const full = { data: { status: 'completed', reflection: none }, updatedAt: 1000 };
  assert.deepEqual(freshestReflection(state, full), done);
  // The newer of the two wins either way; a missing snapshot is skipped.
  assert.deepEqual(freshestReflection({ data: { reflection: none }, updatedAt: 1000 }, { data: { reflection: done }, updatedAt: 3000 }), done);
  assert.deepEqual(freshestReflection({ data: undefined, updatedAt: 0 }, full), none);
  assert.equal(freshestReflection({ data: undefined, updatedAt: 0 }, { data: undefined, updatedAt: 0 }), undefined);
  // The run's state is read again whenever the run is shown, and a finished reading is kept in the cache.
  const hooks = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'components', 'backtest', 'hooks.ts'), 'utf8');
  assert.match(hooks, /refetchOnMount: 'always'/);
  assert.match(hooks, /freshestReflection\(/);
  assert.match(hooks, /setQueryData/);
});
