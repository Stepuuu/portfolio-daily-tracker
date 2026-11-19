// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import test from 'node:test';
import { createSseChunkParser } from '../src/services/sseParser.ts';

const encoder = new TextEncoder();
const CHUNKS = ['建议减仓', '腾讯控股', '，止损 380。', 'line\nbreak "quoted" 📉', ''];

function frame(chunks, newline = '\n') {
  return chunks.map((chunk) => `data: ${JSON.stringify(chunk)}${newline}${newline}`).join('')
    + `data: [DONE]${newline}${newline}`;
}

function run(parts) {
  const received = [];
  const parser = createSseChunkParser((chunk) => received.push(chunk));
  let done = false;
  for (const part of parts) done = parser.push(part) || done;
  done = parser.end() || done;
  return { received, done };
}

test('a whole stream yields every chunk in order', () => {
  const { received, done } = run([encoder.encode(frame(CHUNKS))]);
  assert.deepEqual(received, CHUNKS);
  assert.equal(done, true);
});

test('splitting the stream at any byte offset does not change the text', () => {
  const bytes = encoder.encode(frame(CHUNKS));
  for (let offset = 0; offset <= bytes.length; offset += 1) {
    const { received, done } = run([bytes.slice(0, offset), bytes.slice(offset)]);
    assert.deepEqual(received, CHUNKS, `split at byte ${offset}`);
    assert.equal(done, true, `split at byte ${offset}`);
  }
});

test('one byte per read does not change the text', () => {
  const bytes = encoder.encode(frame(CHUNKS));
  const { received, done } = run(Array.from(bytes, (_, index) => bytes.slice(index, index + 1)));
  assert.deepEqual(received, CHUNKS);
  assert.equal(done, true);
});

test('CRLF line endings are accepted', () => {
  const bytes = encoder.encode(frame(CHUNKS, '\r\n'));
  for (let offset = 0; offset <= bytes.length; offset += 1) {
    const { received } = run([bytes.slice(0, offset), bytes.slice(offset)]);
    assert.deepEqual(received, CHUNKS, `split at byte ${offset}`);
  }
});

test('nothing after [DONE] is delivered', () => {
  const { received, done } = run([encoder.encode('data: "a"\n\ndata: [DONE]\n\ndata: "late"\n\n')]);
  assert.deepEqual(received, ['a']);
  assert.equal(done, true);
});

test('a stream that ends without [DONE] reports not done and keeps its last line', () => {
  const { received, done } = run([encoder.encode('data: "a"\n\ndata: "b"')]);
  assert.deepEqual(received, ['a', 'b']);
  assert.equal(done, false);
});

test('unencoded payloads, comment lines and a missing space after data: are accepted', () => {
  const { received } = run([encoder.encode(': keep-alive\n\nevent: message\ndata: plain text\n\ndata:"tight"\n\n')]);
  assert.deepEqual(received, ['plain text', 'tight']);
});

test('chunks are delivered and [DONE] is reported by the push that completes them', () => {
  const received = [];
  const parser = createSseChunkParser((chunk) => received.push(chunk));
  assert.equal(parser.push(encoder.encode('data: "a"\n\ndata: "b')), false);
  assert.deepEqual(received, ['a']);
  assert.equal(parser.push(encoder.encode('"\n')), false);
  assert.deepEqual(received, ['a', 'b']);
  assert.equal(parser.push(encoder.encode('\ndata: [DO')), false);
  assert.equal(parser.push(encoder.encode('NE]\n\ndata: "late"\n\n')), true);
  assert.deepEqual(received, ['a', 'b']);
  assert.equal(parser.end(), true);
  assert.deepEqual(received, ['a', 'b']);
});

test('a payload cut off by the end of the stream is dropped, not shown half-decoded', () => {
  const complete = encoder.encode('data: "ok"\n\n');
  const partial = encoder.encode('data: "建议减仓');
  for (const cut of [1, 2, 4, 5, partial.length - complete.length]) {
    // Cuts of 1, 2, 4 and 5 bytes end inside a three-byte character.
    const bytes = new Uint8Array([...complete, ...partial.slice(0, partial.length - cut)]);
    const { received, done } = run([bytes]);
    assert.deepEqual(received, ['ok'], `cut ${cut} bytes`);
    assert.equal(done, false);
  }
  assert.deepEqual(run([encoder.encode('data: "ok"\n\ndata: plain te')]).received, ['ok']);
});
