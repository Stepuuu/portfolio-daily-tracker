/**
 * Forward QR workbench events through the Feishu plugin's existing connection.
 * The companion owns operator authorization, card ownership and action deduplication.
 * This module never opens another Feishu WebSocket or executes user-provided commands.
 */
import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import http from 'node:http';
import https from 'node:https';

const CARD_EVENT = 'card.action.trigger';
const MENU_EVENT = 'application.bot.menu_v6';
const ENTER_EVENT = 'im.chat.access_event.bot_p2p_chat_entered_v1';
const MESSAGE_EVENT = 'im.message.receive_v1';
const MAX_BODY_BYTES = 256 * 1024;
const MAX_TOKEN_BYTES = 4096;
const DEADLINE_MS = 1800;

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function eventBody(event) {
  return isObject(event?.event) ? event.event : event;
}

/** Accept SDK-normalized events and original v2 event envelopes. */
export function matchesCardAction(event) {
  const action = eventBody(event)?.action;
  return isObject(action) && (
    action.value?.qr_workbench === 1 ||
    (typeof action.name === 'string' && action.name.startsWith('qrw_'))
  );
}

function matchesEvent(eventType, event) {
  const body = eventBody(event);
  if (!isObject(body)) return false;
  if (eventType === CARD_EVENT) return matchesCardAction(event);
  if (eventType === MENU_EVENT) {
    return typeof body.event_key === 'string' && body.event_key.startsWith('qrw_');
  }
  if (eventType === ENTER_EVENT) return true;
  if (eventType !== MESSAGE_EVENT || body.message?.chat_type !== 'p2p') return false;
  if (body.message?.message_type !== 'text') return false;
  try {
    const content = typeof body.message.content === 'string'
      ? JSON.parse(body.message.content) : body.message.content;
    return typeof content?.text === 'string' &&
      /^(?:工作台|QR工作台|\/workbench)$/iu.test(content.text.trim());
  } catch {
    return false;
  }
}

function unavailable() {
  // A timeout may happen after the companion accepts the action. Never claim
  // that nothing happened or tell users to resubmit a financial mutation.
  return {
    toast: {
      type: 'error',
      content: '工作台暂时无法确认处理结果，请稍后查看任务状态，避免重复提交。',
    },
  };
}

function bridgeUrl(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) ||
      !['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash) {
    throw new Error('Invalid bridge endpoint');
  }
  // Pin localhost to loopback without relying on hosts-file or DNS resolution.
  if (url.hostname === 'localhost') url.hostname = '127.0.0.1';
  if (url.pathname === '/') url.pathname = '/bridge/event';
  if (url.pathname !== '/bridge/event') throw new Error('Invalid bridge path');
  return url;
}

async function readToken(tokenFile) {
  if (typeof tokenFile !== 'string' || !tokenFile) throw new Error('Missing bridge token');
  const handle = await open(tokenFile, constants.O_RDONLY |
    (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size < 32 || stat.size > MAX_TOKEN_BYTES ||
        (process.platform !== 'win32' && (stat.mode & 0o077) !== 0) ||
        (typeof process.getuid === 'function' && stat.uid !== process.getuid())) {
      throw new Error('Invalid bridge token file');
    }
    const buffer = Buffer.alloc(MAX_TOKEN_BYTES + 1);
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    if (bytesRead > MAX_TOKEN_BYTES) throw new Error('Invalid bridge token size');
    const token = buffer.subarray(0, bytesRead).toString('utf8').trim();
    if (!/^[A-Za-z0-9._~+\/-]{32,}={0,2}$/.test(token)) throw new Error('Invalid bridge token');
    return token;
  } finally {
    await handle.close();
  }
}

function postEvent(url, token, body, signal) {
  return new Promise((resolve, reject) => {
    const transport = url.protocol === 'https:' ? https : http;
    const request = transport.request(url, {
      method: 'POST',
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'Content-Length': body.length,
      },
    }, (response) => {
      // Redirects are not followed: the bearer credential must stay local.
      if (response.statusCode !== 200) {
        response.destroy();
        reject(new Error('Bridge request rejected'));
        return;
      }
      let length = 0;
      const chunks = [];
      response.on('error', reject);
      response.on('data', (chunk) => {
        length += chunk.length;
        if (length > MAX_BODY_BYTES) {
          response.destroy(new Error('Bridge response too large'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try {
          const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (!isObject(result)) throw new Error('Invalid bridge response');
          if (result.toast !== undefined && !isObject(result.toast)) throw new Error('Invalid toast');
          if (result.card !== undefined && !isObject(result.card)) throw new Error('Invalid card');
          if (Object.keys(result).some((key) => !['toast', 'card'].includes(key))) {
            throw new Error('Invalid callback response');
          }
          resolve(result);
        } catch (error) {
          reject(error);
        }
      });
    });
    request.on('error', reject);
    request.end(body);
  });
}

/**
 * Return null when the event belongs to another handler; otherwise return the
 * Feishu callback response (possibly {} for an asynchronously handled event).
 * settings.url/tokenFile override QR_WORKBENCH_BRIDGE_URL/TOKEN_FILE, allowing
 * the host to inject private settings without putting them in this module.
 */
export async function dispatchWorkbenchEvent({ accountId, eventType, event }, settings = {}) {
  const expectedAccount = settings.accountId ?? process.env.QR_WORKBENCH_ACCOUNT_ID ?? 'qr';
  if (accountId !== expectedAccount || !matchesEvent(eventType, event)) return null;
  const controller = new AbortController();
  const timeoutMs = Number.isFinite(settings.timeoutMs)
    ? Math.min(DEADLINE_MS, Math.max(1, settings.timeoutMs)) : DEADLINE_MS;
  let timer;
  try {
    const deadline = new Promise((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Error('Bridge deadline exceeded'));
      }, timeoutMs);
    });
    const operation = (async () => {
      const url = bridgeUrl(settings.url ?? process.env.QR_WORKBENCH_BRIDGE_URL ??
        'http://127.0.0.1:18200/bridge/event');
      const body = Buffer.from(JSON.stringify({ account_id: accountId, event_type: eventType, event }));
      if (body.length > MAX_BODY_BYTES) throw new Error('Event too large');
      const token = await readToken(settings.tokenFile ?? process.env.QR_WORKBENCH_BRIDGE_TOKEN_FILE);
      controller.signal.throwIfAborted();
      return postEvent(url, token, body, controller.signal);
    })();
    return await Promise.race([operation, deadline]);
  } catch {
    return unavailable();
  } finally {
    clearTimeout(timer);
  }
}
