"""The observer follows unfinished research only; finished card state is pruned."""
import asyncio
import json
import time

import pytest

from feishu_workbench.drafts import Drafts
from feishu_workbench.portfolio import PortfolioAdapter, PortfolioError
from feishu_workbench.service import ACTIONS, Workbench
from feishu_workbench.store import RETENTION, Store


DAY = 86400


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'cards.sqlite3')


def card(store, view='home', model=None, actor='owner', chat='chat', age=0.0):
    """Deliver one card; ``age`` moves its expiry that many seconds into the past."""
    identifier = store.open(actor, chat)
    command = store.claim()
    key = command['id'] + ':' + view
    store.prepare_render(key, identifier, view, model or {}, ACTIONS)
    store.delivered(key, 'om_' + identifier, chat)
    store.finish(command)
    if age:
        with store.db() as db:
            db.execute('UPDATE sessions SET expires=expires-? WHERE id=?', (age, identifier))
            db.execute('UPDATE commands SET created=created-? WHERE session=?', (age, identifier))
    return identifier


def research(status, number=0):
    return {'status': status, '_run_id': 'run_' + f'{number:032x}'}


def count(store, table):
    with store.db() as db:
        return db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def sessions(store):
    with store.db() as db:
        return {row['id'] for row in db.execute('SELECT id FROM sessions')}


# Leaves a card expired for longer than the retention period.
OLD = 7 * DAY + RETENTION + DAY


def test_observer_reaches_a_live_run_behind_many_finished_ones(store):
    for number in range(60):
        card(store, 'research_status', research(('failed', 'cancelled', 'completed')[number % 3], number))
    live = card(store, 'research_status', research('running', 99))
    queued = card(store, 'research_status', research('queued', 100))
    assert [session['id'] for session in store.observed_sessions()] == [queued, live]


def test_observer_order_is_newest_card_first_and_stable(store):
    cards = [card(store, 'research_status', research('running', number)) for number in range(8)]
    with store.db() as db:
        for position, identifier in enumerate(cards):
            db.execute('UPDATE sessions SET expires=? WHERE id=?', (time.time() + DAY + position, identifier))
    newest_first = list(reversed(cards))
    assert [session['id'] for session in store.observed_sessions()] == newest_first
    assert [session['id'] for session in store.observed_sessions(limit=3)] == newest_first[:3]
    assert store.observed_sessions(limit=3) == store.observed_sessions(limit=3)


def test_observer_ignores_expired_busy_and_other_cards(store):
    card(store, 'research_status', research('running', 1), age=8 * DAY)
    card(store, 'research_result', research('completed', 2))
    card(store, 'home')
    busy = card(store, 'research_status', research('running', 3))
    with store.db() as db:
        db.execute('UPDATE sessions SET busy=1 WHERE id=?', (busy,))
    current = card(store, 'research_status', research('running', 4))
    assert [session['id'] for session in store.observed_sessions()] == [current]


def test_prune_removes_long_expired_cards_with_their_commands_and_deliveries(store):
    old = [card(store, age=OLD) for _ in range(3)]
    recently_expired = card(store, age=7 * DAY + RETENTION - DAY)
    current = card(store)
    assert store.prune() == 3
    assert sessions(store) == {recently_expired, current}
    assert count(store, 'commands') == 2 and count(store, 'deliveries') == 2
    assert not set(old) & sessions(store)
    assert store.prune() == 0


def test_prune_is_bounded_per_pass_and_takes_the_oldest_first(store):
    cards = [card(store, age=OLD + number * DAY) for number in range(5)]
    card(store)
    assert store.prune(limit=2) == 2
    assert sessions(store) >= set(cards[:3]) and not set(cards[3:]) & sessions(store)
    assert store.prune(limit=2) == 2
    assert store.prune(limit=2) == 1
    assert store.prune(limit=2) == 0


def test_prune_keeps_idempotency_keys_of_valid_cards(store):
    identifier = card(store)
    session = store.session(identifier)
    nonce = session['nonces']['assets']
    assert store.accept('owner', 'chat', session['message'], nonce, {})
    command = store.claim()
    store.finish(command, 'simulated failure before the card was replaced')
    with store.db() as db:
        db.execute('UPDATE commands SET created=created-?', (OLD,))
    assert store.prune() == 0
    # The card still shows this button: its nonce must stay spent.
    assert store.accept('owner', 'chat', session['message'], nonce, {}) is False


def test_prune_keeps_unfinished_and_recent_commands(store):
    queued = card(store, age=OLD)
    store.enqueue_report(queued, {'operation_id': 'example'}, 'example')
    with store.db() as db:
        db.execute("UPDATE commands SET created=created-? WHERE id='example_report'", (OLD,))
    running = card(store, age=OLD)
    with store.db() as db:
        db.execute("UPDATE commands SET status='running' WHERE session=?", (running,))
    undelivered = store.open('owner', 'chat', request_id='event_example')
    store.finish(store.claim(), 'simulated delivery failure')
    card(store)
    assert store.prune() == 0
    assert {queued, running, undelivered} <= sessions(store)
    # A redelivered event is still recognised as the same request.
    assert store.open('owner', 'chat', request_id='event_example') == undelivered


def test_prune_keeps_the_reports_listed_on_the_reports_card(store):
    cards = []
    for number in range(7):
        identifier = card(store, age=OLD + (7 - number) * DAY)
        cards.append(identifier)
        store.enqueue_report(identifier, {'operation_id': f'example_{number}'}, f'example_{number}')
        with store.db() as db:
            db.execute("UPDATE commands SET status='failed',created=? WHERE id=?",
                       (time.time() - OLD - (7 - number) * DAY, f'example_{number}_report'))
    card(store)
    listed = [report['id'] for report in store.recent_reports('owner')]
    assert store.prune() == 2
    assert not set(cards[:2]) & sessions(store) and set(cards[2:]) <= sessions(store)
    assert [report['id'] for report in store.recent_reports('owner')] == listed
    store.retry_report('owner', listed[0])


def _failed_report(store, number, age):
    identifier = card(store, age=age)
    store.enqueue_report(identifier, {'operation_id': f'ref_{number}'}, f'ref_{number}')
    with store.db() as db:
        db.execute("UPDATE commands SET status='failed',created=? WHERE id=?",
                   (time.time() - age, f'ref_{number}_report'))
    return identifier


def test_prune_keeps_the_report_a_valid_card_can_still_retry(store):
    old = _failed_report(store, 0, OLD)
    card(store, view='reports', model={'can_retry_report': True, '_report_command': 'ref_0_report'})
    for number in range(1, 6):
        _failed_report(store, number, DAY * number)
    store.prune()
    assert old in sessions(store)
    store.retry_report('owner', 'ref_0_report')
    with store.db() as db:
        assert db.execute("SELECT status FROM commands WHERE id='ref_0_report'").fetchone()[0] == 'queued'


def test_prune_still_removes_old_reports_nothing_references(store):
    old = _failed_report(store, 0, OLD)
    card(store, view='reports', model={'can_retry_report': False})
    for number in range(1, 6):
        _failed_report(store, number, DAY * number)
    assert store.prune() >= 1
    assert old not in sessions(store)


def test_prune_forgets_a_report_once_the_referencing_card_expired(store):
    old = _failed_report(store, 0, OLD)
    card(store, view='reports', model={'_report_command': 'ref_0_report'}, age=OLD)
    for number in range(1, 6):
        _failed_report(store, number, DAY * number)
    store.prune()
    assert old not in sessions(store)


def test_prune_keeps_the_latest_card_of_each_chat(store):
    first = card(store, chat='chat_one', age=OLD + DAY)
    latest = card(store, chat='chat_one', age=OLD)
    other = card(store, chat='chat_two', actor='second', age=OLD)
    assert store.prune() == 1
    assert sessions(store) == {latest, other} and first not in sessions(store)


def test_entering_a_chat_after_pruning_does_not_send_another_card(tmp_path, store):
    controller = Workbench({'allowed_users': ['owner'], 'account_id': 'qr'}, store, None, None, None)
    card(store, age=OLD + DAY)
    card(store, age=OLD)
    assert store.prune() == 1
    entered = {'account_id': 'qr', 'event_type': 'im.chat.access_event.bot_p2p_chat_entered_v1',
               'event': {'event_id': 'entered_example', 'operator_id': {'open_id': 'owner'}, 'chat_id': 'chat'}}
    before = count(store, 'commands')
    assert controller.accept(entered) == {}
    assert count(store, 'commands') == before


def test_edit_results_go_once_their_command_is_gone(store):
    drafts = Drafts(store)
    old, current = card(store, age=OLD), card(store)
    with store.db() as db:
        keys = {session: db.execute('SELECT id FROM commands WHERE session=?', (session,)).fetchone()['id']
                for session in (old, current)}
    draft = drafts.get('owner', '2026-01-02')
    draft = drafts.save('owner', draft['revision'], keys[old], '2026-01-02', [])
    draft = drafts.save('owner', draft['revision'], keys[old] + '_date', '2026-01-02', [])
    kept = drafts.save('owner', draft['revision'], keys[current], '2026-01-02', [])
    drafts.begin('owner', kept['revision'], keys[old], {'id': 'a' * 32})
    assert drafts.prune() == 0
    assert store.prune() == 1
    assert drafts.prune() == 2
    assert drafts.edited('owner', keys[old]) is None and drafts.edited('owner', keys[old] + '_date') is None
    assert drafts.edited('owner', keys[current]) == kept
    # The commit record is recovery state for a confirmed update and stays.
    assert drafts.commit('owner', keys[old])['status'] == 'pending'


@pytest.fixture
def adapter(tmp_path):
    project = tmp_path / 'example'
    holdings = project / 'portfolio' / 'holdings'
    holdings.mkdir(parents=True)
    (project / 'scripts').mkdir()
    (holdings / '2026-01-02.json').write_text(json.dumps({'date': '2026-01-02', 'groups': {'Example': {
        'cash': 1000, 'cash_balances': {'CNY': 1000}, 'fund': 50, 'positions': []}}}))
    return PortfolioAdapter(project, tmp_path / 'state')


def balance(amount):
    return {'operation': 'set_cash', 'date': '2026-01-02', 'account': 'Example', 'currency': 'CNY', 'amount': amount}


def test_expired_previews_are_dropped_and_valid_or_confirmed_ones_survive(adapter):
    expired = adapter.preview(balance('1'))
    confirmed = adapter.preview(balance('2'))
    receipt = adapter.confirm(confirmed, 'confirmed_update')
    with adapter._db() as db:
        db.execute('UPDATE previews SET expires=? WHERE id IN (?,?)', (time.time() - 1, expired['id'], confirmed['id']))
    valid = adapter.preview(balance('3'))
    newest = adapter.preview(balance('4'))
    with adapter._db() as db:
        assert {row['id'] for row in db.execute('SELECT id FROM previews')} == {valid['id'], newest['id']}
    with pytest.raises(PortfolioError, match='预览已过期'):
        adapter.confirm(expired, 'expired_update')
    # The confirmed update no longer needs its preview row.
    assert adapter.confirm(confirmed, 'confirmed_update') == receipt
    assert adapter.confirm(valid, 'valid_update')['operation_id'] == 'valid_update'


def test_service_prunes_while_observing(tmp_path, store, adapter):
    controller = Workbench({'allowed_users': ['owner'], 'account_id': 'qr'}, store, adapter, None, None)
    old = card(store, age=OLD)
    current = card(store)

    def observed():
        controller.stopping = True
        return []

    controller.store.observed_sessions = observed
    asyncio.run(asyncio.wait_for(controller.observe(), timeout=8))
    assert sessions(store) == {current} and old not in sessions(store)
