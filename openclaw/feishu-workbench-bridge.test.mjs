import assert from 'node:assert/strict';
import { once } from 'node:events';
import { chmod, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import http from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { dispatchWorkbenchEvent, matchesCardAction } from './feishu-workbench-bridge.mjs';

const TOKEN = 'synthetic-bridge-test-token-for-local-tests-only';
const CARD = {
  accountId: 'qr',
  eventType: 'card.action.trigger',
  event: { action: { value: { qr_workbench: 1, action: 'home' } } },
};

async function fixture(t, handler) {
  const directory = await mkdtemp(path.join(tmpdir(), 'qr-bridge-test-'));
  const tokenFile = path.join(directory, 'bridge-token');
  await writeFile(tokenFile, TOKEN, { mode: 0o600 });
  const server = http.createServer(handler);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  });
  return { url: `http://127.0.0.1:${server.address().port}/bridge/event`, tokenFile };
}

test('unrelated accounts, authorization cards and arbitrary text never contact the bridge', async () => {
  for (const envelope of [
    { ...CARD, accountId: 'another-account' },
    { ...CARD, event: { action: { value: { action: 'app_auth_done' } } } },
    { ...CARD, event: { action: { value: { qr_workbench: '1' } } } },
    { ...CARD, eventType: 'application.bot.menu_v6', event: { event_key: 'authorize' } },
    { ...CARD, eventType: 'unexpected.event' },
    { ...CARD, eventType: 'im.message.receive_v1', event: { message: {
      chat_type: 'p2p', message_type: 'text', content: JSON.stringify({ text: '买入一些股票' }),
    } } },
  ]) assert.equal(await dispatchWorkbenchEvent(envelope, { url: 'invalid' }), null);
  assert.equal(matchesCardAction({ action: { name: 'qrw_daily_submit' } }), true);
  assert.equal(matchesCardAction({ schema: '2.0', event: CARD.event }), true);
});

test('forwards the authenticated event and returns an official card/toast response', async (t) => {
  const expected = { toast: { type: 'success', content: '已打开' }, card: { type: 'raw', data: { schema: '2.0' } } };
  const settings = await fixture(t, async (request, response) => {
    assert.equal(request.method, 'POST');
    assert.equal(request.url, '/bridge/event');
    assert.equal(request.headers.authorization, `Bearer ${TOKEN}`);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    assert.deepEqual(JSON.parse(Buffer.concat(chunks).toString()), {
      account_id: CARD.accountId, event_type: CARD.eventType, event: CARD.event,
    });
    response.end(JSON.stringify(expected));
  });
  assert.deepEqual(await dispatchWorkbenchEvent(CARD, settings), expected);
});

test('menu, direct chat entry and exact direct-message commands reach the companion', async (t) => {
  let calls = 0;
  const settings = await fixture(t, (_request, response) => { calls += 1; response.end('{}'); });
  const events = [
    ['application.bot.menu_v6', { event_key: 'qrw_home' }],
    ['im.chat.access_event.bot_p2p_chat_entered_v1', { chat_id: 'synthetic-chat' }],
    ['im.message.receive_v1', { message: { chat_type: 'p2p', message_type: 'text', content: '{"text":"工作台"}' } }],
    ['im.message.receive_v1', { message: { chat_type: 'p2p', message_type: 'text', content: '{"text":"/workbench"}' } }],
  ];
  for (const [eventType, event] of events) {
    assert.deepEqual(await dispatchWorkbenchEvent({ accountId: 'qr', eventType, event }, settings), {});
  }
  assert.equal(calls, events.length);
  assert.equal(await dispatchWorkbenchEvent({ accountId: 'qr', eventType: 'im.message.receive_v1', event: {
    message: { chat_type: 'group', message_type: 'text', content: '{"text":"工作台"}' },
  } }, settings), null);
  assert.equal(calls, events.length);
});

test('rejects remote endpoints, credentials in URLs and unexpected paths', async (t) => {
  let calls = 0;
  const settings = await fixture(t, (_request, response) => { calls += 1; response.end('{}'); });
  for (const url of [
    'https://example.com/bridge/event',
    settings.url.replace('127.0.0.1', 'user:password@127.0.0.1'),
    settings.url.replace('/bridge/event', '/other'),
    `${settings.url}?debug=1`,
    `file://${settings.tokenFile}`,
  ]) {
    const result = await dispatchWorkbenchEvent(CARD, { ...settings, url });
    assert.equal(result.toast.type, 'error');
    assert.equal(JSON.stringify(result).includes(TOKEN), false);
  }
  assert.equal(calls, 0);
});

test('rejects readable-by-others token files and symlinks before sending', { skip: process.platform === 'win32' }, async (t) => {
  let calls = 0;
  const settings = await fixture(t, (_request, response) => { calls += 1; response.end('{}'); });
  await chmod(settings.tokenFile, 0o644);
  assert.equal((await dispatchWorkbenchEvent(CARD, settings)).toast.type, 'error');
  await chmod(settings.tokenFile, 0o600);
  const link = `${settings.tokenFile}-link`;
  await symlink(settings.tokenFile, link);
  assert.equal((await dispatchWorkbenchEvent(CARD, { ...settings, tokenFile: link })).toast.type, 'error');
  assert.equal(calls, 0);
});

test('timeouts return a safe uncertain-outcome message within the callback budget', async (t) => {
  const settings = await fixture(t, () => {});
  const start = performance.now();
  const result = await dispatchWorkbenchEvent(CARD, { ...settings, timeoutMs: 40 });
  assert.ok(performance.now() - start < 1500);
  assert.equal(result.toast.type, 'error');
  assert.match(result.toast.content, /避免重复提交/);
  assert.equal(JSON.stringify(result).includes(settings.tokenFile), false);
});

test('never follows redirects or reveals service error details', async (t) => {
  const settings = await fixture(t, (_request, response) => {
    response.writeHead(302, { Location: 'https://example.com' });
    response.end(`private detail ${TOKEN}`);
  });
  const result = await dispatchWorkbenchEvent(CARD, settings);
  assert.equal(result.toast.type, 'error');
  assert.equal(JSON.stringify(result).includes(TOKEN), false);
});

test('oversized events are rejected before dispatch', async (t) => {
  let calls = 0;
  const settings = await fixture(t, (_request, response) => { calls += 1; response.end('{}'); });
  const result = await dispatchWorkbenchEvent({ ...CARD, event: { ...CARD.event, extra: 'x'.repeat(256 * 1024) } }, settings);
  assert.equal(result.toast.type, 'error');
  assert.equal(calls, 0);
});

test('oversized or malformed companion responses are contained', async (t) => {
  let reply = 'x'.repeat(256 * 1024 + 1);
  const settings = await fixture(t, (_request, response) => response.end(reply));
  for (const value of [reply, '{', '[]', '{"toast":"bad"}', '{"debug":"private detail"}']) {
    reply = value;
    const result = await dispatchWorkbenchEvent(CARD, settings);
    assert.equal(result.toast.type, 'error');
    assert.equal(JSON.stringify(result).includes('private detail'), false);
  }
});
