// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CHAT_STREAM_INACTIVITY_TIMEOUT_MS, ChatStreamCancelledError, ChatStreamTimeoutError, readChatStream,
} from '../src/services/chatStream.ts';

const encoder = new TextEncoder();
const frame = (text) => encoder.encode(`data: ${JSON.stringify(text)}\n\n`);
const DONE = encoder.encode('data: [DONE]\n\n');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A fake server. `script` is a list of steps: a Uint8Array to send, a number of milliseconds to
// wait, 'close' to end the body, or 'hang' to keep the connection open and silent forever.
// `honourAbort: false` models a transport that ignores the abort signal.
function fakeFetch(script, { status = 200, honourAbort = true, body } = {}) {
  const state = { calls: 0, request: null, cancelled: false, aborted: false };
  const fetchImpl = async (url, init) => {
    state.calls += 1;
    state.request = { url, init };
    init.signal.addEventListener('abort', () => { state.aborted = true; });
    if (body !== undefined) return new Response(body, { status });
    const stream = new ReadableStream({
      async start(controller) {
        if (honourAbort) {
          init.signal.addEventListener('abort', () => {
            try { controller.error(new DOMException('aborted', 'AbortError')); } catch { /* already closed */ }
          });
        }
        for (const step of script) {
          if (step === 'hang') return;
          if (step === 'close') break;
          if (typeof step === 'number') await sleep(step);
          else {
            try { controller.enqueue(step); } catch { return; }
          }
        }
        try { controller.close(); } catch { /* errored by abort */ }
      },
      cancel() { state.cancelled = true; },
    });
    return new Response(stream, { status });
  };
  return { fetchImpl, state };
}

async function run(script, options = {}, fetchOptions = {}) {
  const { fetchImpl, state } = fakeFetch(script, fetchOptions);
  const chunks = [];
  let error = null;
  const started = Date.now();
  try {
    await readChatStream('/api/chat/message', { message: 'hi', stream: true }, (chunk) => chunks.push(chunk), { fetchImpl, ...options });
  } catch (caught) {
    error = caught;
  }
  return { chunks, error, state, elapsed: Date.now() - started };
}

test('a complete reply is delivered and the request is the chat POST', async () => {
  const { chunks, error, state } = await run([frame('建议'), frame('减仓'), DONE]);
  assert.equal(error, null);
  assert.deepEqual(chunks, ['建议', '减仓']);
  assert.equal(state.calls, 1);
  assert.equal(state.request.url, '/api/chat/message');
  assert.equal(state.request.init.method, 'POST');
  assert.deepEqual(JSON.parse(state.request.init.body), { message: 'hi', stream: true });
  assert.ok(state.request.init.signal instanceof AbortSignal);
});

test('[DONE] ends the reply even if the server keeps the connection open', async () => {
  const { chunks, error, state, elapsed } = await run([frame('a'), DONE, 'hang'], { inactivityTimeoutMs: 5000 });
  assert.equal(error, null);
  assert.deepEqual(chunks, ['a']);
  assert.ok(elapsed < 1000, `took ${elapsed}ms`);
  await sleep(5);
  assert.equal(state.cancelled, true, 'the connection is released');
});

test('a stream closed without [DONE] still completes with what arrived', async () => {
  const { chunks, error } = await run([frame('a'), frame('b'), 'close']);
  assert.equal(error, null);
  assert.deepEqual(chunks, ['a', 'b']);
});

test('a silent upstream times out instead of hanging forever', async () => {
  const { chunks, error, state, elapsed } = await run([frame('部分'), 'hang'], { inactivityTimeoutMs: 40 });
  assert.ok(error instanceof ChatStreamTimeoutError, String(error));
  assert.match(error.message, /没有收到新的内容/);
  assert.deepEqual(chunks, ['部分'], 'text received before the stall is kept');
  assert.ok(elapsed >= 35 && elapsed < 1500, `took ${elapsed}ms`);
  assert.equal(state.aborted, true, 'the request is aborted');
});

test('the timeout also covers a response that never starts, even if fetch ignores abort', async () => {
  const neverFetch = () => new Promise(() => {});
  const started = Date.now();
  await assert.rejects(
    readChatStream('/api/chat/message', {}, () => {}, { fetchImpl: neverFetch, inactivityTimeoutMs: 40 }),
    ChatStreamTimeoutError,
  );
  assert.ok(Date.now() - started < 1500);
});

test('a read that ignores the abort signal still times out', async () => {
  const { error, elapsed } = await run([frame('a'), 'hang'], { inactivityTimeoutMs: 40 }, { honourAbort: false });
  assert.ok(error instanceof ChatStreamTimeoutError, String(error));
  assert.ok(elapsed < 1500, `took ${elapsed}ms`);
});

test('the timeout measures silence, not total duration', async () => {
  const script = [];
  for (let index = 0; index < 8; index += 1) script.push(frame(`c${index}`), 25);
  script.push(DONE);
  const { chunks, error, elapsed } = await run(script, { inactivityTimeoutMs: 120 });
  assert.equal(error, null);
  assert.equal(chunks.length, 8);
  assert.ok(elapsed >= 150, `a reply longer than the timeout (${elapsed}ms) completed`);
});

test('the caller can cancel a reply in progress and keeps the text so far', async () => {
  const controller = new AbortController();
  const pending = run([frame('已经'), frame('收到'), 'hang'], { signal: controller.signal, inactivityTimeoutMs: 5000 });
  await sleep(30);
  controller.abort();
  const { chunks, error, state, elapsed } = await pending;
  assert.ok(error instanceof ChatStreamCancelledError, String(error));
  assert.deepEqual(chunks, ['已经', '收到']);
  assert.ok(elapsed < 1500, `took ${elapsed}ms`);
  assert.equal(state.aborted, true);
});

test('cancelling works when the transport ignores the abort signal', async () => {
  const controller = new AbortController();
  const pending = run([frame('a'), 'hang'], { signal: controller.signal, inactivityTimeoutMs: 5000 }, { honourAbort: false });
  await sleep(30);
  controller.abort();
  const { error, elapsed } = await pending;
  assert.ok(error instanceof ChatStreamCancelledError, String(error));
  assert.ok(elapsed < 1500, `took ${elapsed}ms`);
});

test('a signal that is already aborted cancels at once', async () => {
  const controller = new AbortController();
  controller.abort();
  const { chunks, error } = await run([frame('a'), DONE], { signal: controller.signal });
  assert.ok(error instanceof ChatStreamCancelledError, String(error));
  assert.deepEqual(chunks, []);
});

test('cancelling after the reply finished does nothing', async () => {
  const controller = new AbortController();
  const { chunks, error } = await run([frame('a'), DONE], { signal: controller.signal });
  controller.abort();
  await sleep(5);
  assert.equal(error, null);
  assert.deepEqual(chunks, ['a']);
});

test('an HTTP error reports the server detail', async () => {
  const withDetail = await run([], {}, { status: 502, body: JSON.stringify({ detail: '上游模型服务不可用' }) });
  assert.equal(withDetail.error.message, '上游模型服务不可用');
  assert.deepEqual(withDetail.chunks, []);
  const plain = await run([], {}, { status: 500, body: 'Internal Server Error' });
  assert.equal(plain.error.message, 'Internal Server Error');
  const empty = await run([], {}, { status: 503, body: '' });
  assert.equal(empty.error.message, 'Stream request failed (503)');
});

test('a network failure is passed through as is', async () => {
  const failing = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(readChatStream('/api/chat/message', {}, () => {}, { fetchImpl: failing }), /Failed to fetch/);
});

test('the default timeout is generous but finite, and no timer outlives a reply', async () => {
  assert.ok(CHAT_STREAM_INACTIVITY_TIMEOUT_MS >= 60000 && CHAT_STREAM_INACTIVITY_TIMEOUT_MS <= 300000);
  // Uses the default timeout: a leaked timer would keep this test file alive for two minutes.
  const { error } = await run([frame('a'), DONE]);
  assert.equal(error, null);
});
