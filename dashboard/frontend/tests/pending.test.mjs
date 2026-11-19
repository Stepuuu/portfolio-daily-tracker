// Run from the repository root: node --experimental-strip-types --test "dashboard/frontend/tests/*.test.mjs"
// What waits for confirmation: the 待确认 count, the tray's groups and the one-line descriptions.
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  describeLedgerEvent, describeLedgerProposal, groupProposals, payloadFields, pendingCount, proposalGroup, sourceLabel,
  splitLedgerPending,
} from '../src/features/pending/proposals.ts';

const buy = { kind: 'buy', date: '2026-11-17', account: '主账户', ticker: 'SHA:600519', name: '贵州茅台', quantity: '100', price: '1680', currency: 'CNY' };
const ledger = {
  revision: 4,
  pending: [
    { id: 'a', revision: 4, events: [buy] },
    { id: 'b', revision: 3, events: [buy] },
    { id: 'c', revision: 4, events: [] },
  ],
};
const generic = (id, kind, status = 'pending') => ({
  id, kind, status, source: 'agent:chat', summary: `${kind} ${id}`, payload: { ticker: 'SHA:600519', threshold: '1500', note: '' },
  created_at: '2026-11-17T02:00:00Z', expires_at: '2026-11-18T02:00:00Z',
});

test('only proposals made against the current ledger revision can be confirmed', () => {
  const { current, outdated } = splitLedgerPending(ledger);
  assert.deepEqual(current.map((proposal) => proposal.id), ['a']);
  assert.deepEqual(outdated.map((proposal) => proposal.id), ['b']);
  assert.deepEqual(splitLedgerPending(undefined), { current: [], outdated: [] });
});

test('the 待确认 count: confirmable ledger proposals plus pending generic ones', () => {
  assert.equal(pendingCount(ledger, [generic('x', 'alert_rule'), generic('y', 'lesson', 'applied')]), 2);
  assert.equal(pendingCount(undefined, undefined), 0);
});

test('one-line descriptions', () => {
  assert.equal(describeLedgerEvent(buy), '11-17 买入 贵州茅台 100 股');
  assert.equal(describeLedgerEvent({ kind: 'sell', date: '2026-09-04', ticker: 'NASDAQ:EXAMPLE', quantity: '0.5' }), '09-04 卖出 NASDAQ:EXAMPLE 0.5 股');
  assert.equal(describeLedgerEvent({ kind: 'dividend', date: '2026-11-17', name: '招商银行', amount: '1936', currency: 'CNY' }), '11-17 分红 招商银行 ¥1,936.00');
  assert.equal(describeLedgerEvent({ kind: 'deposit', date: '2026-11-17', amount: '50', currency: 'USD' }), '11-17 入金 $50.00');
  assert.equal(describeLedgerEvent({ kind: 'dividend', date: '2026-11-17', ticker: 'NASDAQ:EXAMPLE', amount: '0.004', currency: 'USD' }), '11-17 分红 NASDAQ:EXAMPLE $0.004', 'as exact as the review');
  assert.equal(describeLedgerEvent({ kind: 'opening', date: '2026-09-01', account: '长期账户' }), '09-01 期初余额 长期账户');
  assert.equal(describeLedgerEvent({ kind: 'reverse', date: '2026-09-02' }), '09-02 冲销一笔记录');
  assert.equal(describeLedgerProposal({ id: 'p', revision: 1, events: [buy, { ...buy, quantity: '200' }] }), '11-17 买入 贵州茅台 100 股 等 2 笔');
  const opening = (account) => ({ kind: 'opening', date: '2026-09-01', account });
  assert.equal(describeLedgerProposal({ id: 'p', revision: 0, events: [opening('长期账户'), opening('研究账户')] }), '09-01 期初余额 · 2 个账户');
  assert.equal(describeLedgerProposal({ id: 'p', revision: 0, events: [] }), '空方案');
});

test('generic proposals are grouped 提醒, 计划, 教训, 自选, 其他', () => {
  assert.equal(proposalGroup('alert_rule'), 'alert');
  assert.equal(proposalGroup('trade_plan'), 'plan');
  assert.equal(proposalGroup('lesson'), 'lesson');
  assert.equal(proposalGroup('watchlist_add'), 'watchlist');
  assert.equal(proposalGroup('something'), 'other');
  const groups = groupProposals([generic('1', 'lesson'), generic('2', 'alert_rule'), generic('3', 'x'), generic('4', 'alert_rule')]);
  assert.deepEqual(groups.map(([group, items]) => [group, items.map((item) => item.id)]), [['alert', ['2', '4']], ['lesson', ['1']], ['other', ['3']]]);
});

test('source labels and payload fields', () => {
  assert.equal(sourceLabel('agent:chat'), '助手');
  assert.equal(sourceLabel('routine:postmarket'), '例行任务');
  assert.equal(sourceLabel('manual'), 'manual');
  assert.deepEqual(payloadFields({ ticker: 'SHA:1', note: '', n: 3, nested: { a: 1 }, empty: null }), [['ticker', 'SHA:1'], ['n', '3'], ['nested', '{"a":1}']]);
});

test('the tray says nothing is waiting only when both lists answered with nothing; a failed list is 不可用', async () => {
  const { trayView } = await import('../src/features/pending/proposals.ts');
  const ready = (count = 0) => ({ status: 'success', count });
  const loading = { status: 'pending', count: 0 };
  const failed = { status: 'error', count: 0 };
  // The empty ledger answered before the plan proposals: not "nothing waiting" yet.
  assert.deepEqual(trayView({ ledger: ready(), generic: loading }), { empty: false, loading: true, unavailable: [] });
  assert.deepEqual(trayView({ ledger: loading, generic: ready() }), { empty: false, loading: true, unavailable: [] });
  assert.deepEqual(trayView({ ledger: ready(), generic: ready() }), { empty: true, loading: false, unavailable: [] });
  // A failed list is unavailable, never part of "nothing waiting".
  assert.deepEqual(trayView({ ledger: ready(), generic: failed }), { empty: false, loading: false, unavailable: ['generic'] });
  assert.deepEqual(trayView({ ledger: failed, generic: failed }), { empty: false, loading: false, unavailable: ['ledger', 'generic'] });
  // Something waiting is shown while the other list still loads or failed.
  assert.deepEqual(trayView({ ledger: ready(1), generic: loading }), { empty: false, loading: true, unavailable: [] });
  assert.deepEqual(trayView({ ledger: failed, generic: ready(2) }), { empty: false, loading: false, unavailable: ['ledger'] });
});
