// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeOpKind, beginOp, busyHint, finishOp, initialConversationOp, isCurrentOp,
} from '../src/lib/conversationOps.ts';

test('a second operation cannot start while one is running', () => {
  const a = beginOp(initialConversationOp, 'resume');
  assert.ok(a);
  assert.equal(activeOpKind(a.state), 'resume');
  assert.equal(beginOp(a.state, 'send'), null);
  assert.equal(beginOp(a.state, 'reset'), null);
  assert.equal(beginOp(a.state, 'resume'), null);
});

test('delayed resume response, new question sent meanwhile: the question is refused, then the response applies', () => {
  let state = initialConversationOp;
  const resume = beginOp(state, 'resume'); state = resume.state;
  // user closes the modal and types a question: refused while resume is pending
  assert.equal(beginOp(state, 'send'), null);
  const done = finishOp(state, resume.token); state = done.state;
  assert.equal(done.apply, true);
  const send = beginOp(state, 'send');
  assert.ok(send, 'sending works once the resume finished');
});

test('delayed reset, navigate away and back (state outlives the component), send is refused until reset finishes', () => {
  let state = initialConversationOp;
  const reset = beginOp(state, 'reset'); state = reset.state;
  // component unmounted and mounted again: only the store state is left
  assert.equal(activeOpKind(state), 'reset');
  assert.equal(beginOp(state, 'send'), null);
  const done = finishOp(state, reset.token); state = done.state;
  assert.equal(done.apply, true);
  assert.equal(activeOpKind(state), null);
  assert.ok(beginOp(state, 'send'));
});

test('superseded operation: its late response is ignored in either completion order', () => {
  const first = beginOp(initialConversationOp, 'resume');
  const second = beginOp(first.state, 'reset', { force: true });
  assert.ok(second);
  assert.equal(isCurrentOp(second.state, first.token), false);

  // order 1: old response arrives while the new operation is still running
  const late = finishOp(second.state, first.token);
  assert.equal(late.apply, false);
  assert.equal(late.state, second.state);
  assert.equal(activeOpKind(late.state), 'reset', 'old response must not clear the new operation');
  const fin = finishOp(late.state, second.token);
  assert.equal(fin.apply, true);

  // order 2: new operation finishes first, then the old response arrives
  const fin2 = finishOp(second.state, second.token);
  assert.equal(fin2.apply, true);
  const late2 = finishOp(fin2.state, first.token);
  assert.equal(late2.apply, false);
  assert.equal(late2.state.active, null);
});

test('finishing twice applies only once', () => {
  const a = beginOp(initialConversationOp, 'send');
  const one = finishOp(a.state, a.token);
  assert.equal(one.apply, true);
  assert.equal(finishOp(one.state, a.token).apply, false);
});

test('tokens never repeat', () => {
  let state = initialConversationOp;
  const seen = new Set();
  for (let i = 0; i < 5; i++) {
    const b = beginOp(state, 'send');
    assert.ok(!seen.has(b.token)); seen.add(b.token);
    state = finishOp(b.state, b.token).state;
  }
});

test('hints are plain words and null when idle', () => {
  assert.equal(busyHint(initialConversationOp), null);
  assert.match(busyHint(beginOp(initialConversationOp, 'resume').state), /历史/);
  assert.match(busyHint(beginOp(initialConversationOp, 'reset').state), /新对话/);
  assert.match(busyHint(beginOp(initialConversationOp, 'send').state), /停止/);
});
