// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// The shell's mode follows the backend that is running now: a tab left open on the demo while the backend
// restarts in full mode (`make demo`, then `make start`) must not keep 记一笔 disabled, and the reverse must not
// keep the write controls. The mode is read again periodically and as soon as the backend answers again.
import assert from 'node:assert/strict';
import { register } from 'node:module';
import test from 'node:test';
import { QueryClient } from '@tanstack/react-query';

register('./tsxHooks.mjs', import.meta.url);
const mode = await import('../src/components/shell/useAppMode.ts');

test('the mode is read again periodically, and sooner while it is unknown', () => {
  const { statusRefetchInterval } = mode;
  const full = { ok: true, status: { mode: 'full', ledger: { active: true, revision: 3 }, version: '4.0.0' } };
  assert.equal(typeof statusRefetchInterval(full), 'number', 'a known mode is read again too');
  assert.ok(statusRefetchInterval(full) <= 60_000);
  assert.equal(statusRefetchInterval({ ok: false, httpStatus: 404, absent: true }), statusRefetchInterval(full));
  assert.equal(statusRefetchInterval({ ok: false, httpStatus: 503, absent: false }), 15_000);
  assert.equal(statusRefetchInterval(undefined), false, 'nothing to repeat before the first answer');
});

const down = () => Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' });
const status = (code) => Object.assign(new Error(`HTTP ${code}`), { response: { status: code, data: {} } });

test('the mode is read again as soon as the backend answers after it could not be reached', async () => {
  const { watchBackendReturn } = mode;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  let returns = 0;
  const stop = watchBackendReturn(client, () => { returns += 1; });
  let answer = 'ok';
  const queryFn = async () => {
    if (answer === 'down') throw down();
    if (typeof answer === 'number') throw status(answer);
    return { answer };
  };
  const fetch = () => client.fetchQuery({ queryKey: ['today'], queryFn }).catch(() => {});
  await fetch();
  assert.equal(returns, 0, 'answers all along: nothing to re-read');
  answer = 'down';
  await fetch();
  await fetch();
  assert.equal(returns, 0, 'still down');
  answer = 'ok';
  await fetch();
  assert.equal(returns, 1, 'back: the mode is read again, once');
  await fetch();
  assert.equal(returns, 1);
  // A proxy in front of a stopped backend answers 502 / 503 / 504: the same.
  answer = 502;
  await fetch();
  answer = 'ok';
  await fetch();
  assert.equal(returns, 2);
  // An answer from the backend (a 404 of an endpoint it does not have, a refusal) is not an outage.
  for (const code of [404, 409, 422]) {
    answer = code;
    await fetch();
    answer = 'ok';
    await fetch();
  }
  assert.equal(returns, 2);
  stop();
  answer = 'down';
  await fetch();
  answer = 'ok';
  await fetch();
  assert.equal(returns, 2, 'stopped');
  client.clear();
});
