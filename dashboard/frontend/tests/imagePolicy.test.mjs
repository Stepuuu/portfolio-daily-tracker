// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { classifyImageSrc, isTrustedImageSrc } from '../src/lib/imagePolicy.ts';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const ORIGIN = 'http://localhost:3000';

test('stored chat images and other same-origin API paths are loaded', () => {
  for (const src of [
    '/api/chat/conversation-images/data/conversation_images/20260803_101500_0.jpg',
    '/api/chat/conversation-images/example.png',
    '/api/chat/conversation-images/a.png?v=2',
  ]) {
    assert.deepEqual(classifyImageSrc(src), { kind: 'image', src });
    assert.equal(new URL(src, ORIGIN).origin, ORIGIN, src);
  }
});

test('inline data: images are loaded', () => {
  for (const src of ['data:image/png;base64,iVBORw0KGgo=', 'data:image/jpeg;base64,/9j/4AAQ', 'DATA:image/webp;base64,AAAA']) {
    assert.deepEqual(classifyImageSrc(src), { kind: 'image', src });
  }
});

test('remote images are never loaded; they become a link the user has to click', () => {
  for (const src of [
    'https://attacker.example/pixel.png?d=600519-1000-shares',
    'http://attacker.example/a.gif',
    'HTTPS://attacker.example/a.gif',
    'https://localhost:3000/api/chat/conversation-images/a.png',
  ]) {
    assert.equal(isTrustedImageSrc(src), false, src);
    assert.deepEqual(classifyImageSrc(src), { kind: 'link', href: src });
  }
});

test('every form that could reach another host or run script is refused outright', () => {
  for (const src of [
    '//attacker.example/a.png',                 // protocol-relative
    '/\\attacker.example/a.png',                // browsers read "\" as "/"
    '/api/\\attacker.example/a.png',
    '\\\\attacker.example\\a.png',
    ' https://attacker.example/a.png',          // leading whitespace is stripped by URL parsers
    '/api/chat/a.png\n',
    '\t//attacker.example/a.png',
    '/api/../../attacker',                      // leaves /api/
    '/api/chat/../../secret.png',
    '/api/%2e%2e/x.png',
    '/api/%2E%2e/x.png',
    '/apix/a.png',
    '/api',
    'api/a.png',
    'a.png',
    './a.png',
    '/static/a.png',
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'data:image',
    'data:,image/png',
    'blob:http://localhost:3000/6f1e',
    'file:///etc/passwd',
    'ftp://attacker.example/a.png',
    'https://',
    'https:///attacker.example',
    'https://attacker.example/a b.png',
    '',
  ]) {
    assert.equal(isTrustedImageSrc(src), false, JSON.stringify(src));
    assert.deepEqual(classifyImageSrc(src), { kind: 'text' }, JSON.stringify(src));
  }
});

test('every accepted path really stays on this origin under /api/', () => {
  // Cross-check the policy against the URL parser a browser uses.
  const candidates = ['/api/a.png', '/api/chat/conversation-images/x.jpg', '/api/a/./b.png', '/api/a.png#frag', '/api/a.png?u=//x'];
  for (const src of candidates) {
    assert.equal(isTrustedImageSrc(src), true, src);
    const resolved = new URL(src, ORIGIN);
    assert.equal(resolved.origin, ORIGIN, src);
    assert.ok(resolved.pathname.startsWith('/api/'), src);
  }
});

test('values that are not strings are not images', () => {
  for (const src of [undefined, null, 42, {}, ['/api/a.png']]) {
    assert.equal(isTrustedImageSrc(src), false);
    assert.deepEqual(classifyImageSrc(src), { kind: 'text' });
  }
});

// Model output must go through SafeMarkdown, which applies this policy. A bare <ReactMarkdown>
// anywhere else would load remote images again.
test('react-markdown is only used through SafeMarkdown', () => {
  const offenders = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(entry.name) && entry.name !== 'SafeMarkdown.tsx'
        && /from ['"]react-markdown['"]/.test(readFileSync(path, 'utf8'))) offenders.push(path);
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, []);
  const safe = readFileSync(join(SRC, 'components', 'SafeMarkdown.tsx'), 'utf8');
  assert.match(safe, /classifyImageSrc/);
});
