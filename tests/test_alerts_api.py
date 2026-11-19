"""HTTP contract of /api/alerts: rules with live distance, Chinese errors, events, status and demo mode."""
import asyncio
import re
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import alerts as api
from core import notify
from core.alerts import monitor, rules
from core.market.runtime import set_market_runtime
from tests.fakes import build_ledger, opening, position, quote, write_fake_quotes
from tests.test_alert_monitor import Market

CN = 'SHA:600001'
CHINESE = re.compile(r'[一-鿿]')
# ``identity`` (the listing, read-only) was added by the market UI review (an addition; the rest as before).
RULE_KEYS = {'id', 'ticker', 'account', 'kind', 'kind_label', 'condition', 'threshold', 'enabled',
             'cooldown_seconds', 'note', 'source', 'created_at', 'updated_at', 'state', 'market', 'disabled_reason',
             'identity'}
STATE_KEYS = {'armed', 'last_fired_at', 'last_trading_date', 'fire_count_today', 'fires_left_today'}
# ``condition_met`` was added by the release gate (an addition; the rest as before).
MARKET_KEYS = {'price', 'prev_close', 'change_pct', 'weight_pct', 'value', 'distance', 'distance_pct', 'unit',
               'condition_met', 'checked_now', 'skip_reason', 'skip_code', 'skipped_since', 'quote'}


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    monkeypatch.delenv('TRACKER_ALERTS_ENABLED', raising=False)
    monkeypatch.setattr(notify, '_deliverers', [])
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)


@pytest.fixture
def market():
    scripted = Market()
    set_market_runtime(scripted.runtime)
    return scripted


@pytest.fixture
def client():
    app = FastAPI()
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def create(client, **body):
    return client.post('/api/alerts/rules', json={'ticker': CN, 'kind': 'stop_loss', 'threshold': '10', **body})


def fire(market, *tickers):
    for ticker in tickers:
        market.price(ticker, '9.9')
    return asyncio.run(market.monitor().run_cycle())


# -- rules --------------------------------------------------------------------

def test_a_created_rule_comes_back_with_its_price_distance_and_state(client, market):
    market.price(CN, '10.5')
    response = create(client, ticker='sha:600001', threshold='9.50', note='跌破年线')
    assert response.status_code == 201
    rule = response.json()
    assert set(rule) == RULE_KEYS and set(rule['state']) == STATE_KEYS and set(rule['market']) == MARKET_KEYS
    assert (rule['ticker'], rule['threshold'], rule['source'], rule['enabled']) == (CN, '9.5', 'manual', True)
    assert rule['kind_label'] == '止损' and rule['condition'] == '价格跌到 9.5 或以下' and rule['note'] == '跌破年线'
    assert rule['cooldown_seconds'] == 300
    assert rule['state'] == {'armed': True, 'last_fired_at': None, 'last_trading_date': None, 'fire_count_today': 0,
                             'fires_left_today': 3}
    assert rule['market'] == {
        'price': '10.5', 'prev_close': '10', 'change_pct': '5.00', 'weight_pct': None, 'value': '10.5',
        'distance': '1', 'distance_pct': '9.52', 'unit': 'price', 'condition_met': False, 'checked_now': True,
        'skip_reason': None,
        'skip_code': None, 'skipped_since': None, 'quote': {'source': 'scripted', 'quote_time': '2026-11-17T09:59:57+08:00',
                  'fetched_at': '2026-11-17T09:59:57+08:00', 'status': 'live', 'reason': None}}
    listing = client.get('/api/alerts/rules').json()
    assert listing['items'] == [rule] and listing['as_of'] == '2026-11-17T10:00:00+08:00'
    assert client.get(f"/api/alerts/rules/{rule['id']}").json() == rule
    assert market.source.calls == [(CN,), (CN,), (CN,)], 'one batched request per response'


def test_without_quotes_the_market_fields_are_null(client, market):
    create(client)
    [item] = client.get('/api/alerts/rules', params={'quotes': 0}).json()['items']
    assert item['market']['price'] is None and item['market']['distance'] is None
    assert market.source.calls == [(CN,)], 'only the create response asked for a quote'


def test_untrusted_quotes_show_no_distance(client, market):
    stale = 'SHA:600002'
    market.price(stale, '10.5', age=30 * 60)
    create(client, ticker=stale)
    create(client, ticker='SHA:600003')                     # the source has no quote for it
    items = {item['ticker']: item['market'] for item in client.get('/api/alerts/rules').json()['items']}
    assert items[stale]['price'] == '10.5' and items[stale]['quote']['status'] == 'stale'
    assert items[stale]['distance'] is None and items[stale]['checked_now'] is False
    assert '过期' in items[stale]['skip_reason']
    missing = items['SHA:600003']
    assert missing['price'] is None and missing['distance'] is None and missing['quote']['status'] == 'unavailable'
    assert '不可用' in missing['skip_reason']


def test_outside_the_session_rules_are_shown_but_not_checked(client):
    evening = Market('2026-11-17 20:00')
    set_market_runtime(evening.runtime)
    evening.source.quotes[CN] = quote(CN, '10.5', prev_close='10', at=evening.clock.now() - timedelta(hours=5))
    item = create(client).json()
    assert item['market']['quote']['status'] == 'closed' and item['market']['distance'] == '0.5'
    assert item['market']['checked_now'] is False and item['market']['skip_reason'] == '不在交易时段，不检查提醒'
    assert item['market']['skip_code'] == 'session_closed' and item['market']['skipped_since'] is None


@pytest.mark.parametrize('ticker', ['SHA:000001', 'SHE:399001', 'sha:000300'])
def test_a_stock_code_in_an_index_range_is_refused_as_the_picker_refuses_it(client, market, ticker):
    """The gate review's case: a direct POST of SHA:000001 was stored, though no source quotes it as a stock
    (it is the 上证指数's code), so the rule could never be checked. The agent's proposal is refused alike."""
    from core.alerts import proposal
    response = create(client, ticker=ticker)
    assert response.status_code == 422, response.text
    assert '指数编号段' in response.json()['detail'] and CHINESE.search(response.json()['detail'])
    with pytest.raises(rules.AlertRuleError, match='指数编号段'):
        proposal.validate({'ticker': ticker, 'kind': 'target_price', 'threshold': '10'})
    assert rules.list_rules() == []
    assert create(client, ticker='IDX:' + ticker.split(':')[1]).status_code == 201, 'the index itself is fine'


def test_a_rule_stored_on_such_a_code_before_can_still_be_edited_and_deleted(client, market):
    from core import desk_store
    rules.ensure()
    with desk_store.transaction() as db:
        db.execute("INSERT INTO alert_rules(ticker, kind, threshold, enabled, cooldown_seconds, source, "
                   "created_at, updated_at) VALUES ('SHA:000001', 'stop_loss', '10', 1, 0, 'manual', 'x', 'x')")
    [rule] = client.get('/api/alerts/rules').json()['items']
    assert rule['market']['checked_now'] is False and '指数编号段' in rule['market']['skip_reason']
    assert client.patch(f"/api/alerts/rules/{rule['id']}", json={'enabled': False}).status_code == 200
    assert client.delete(f"/api/alerts/rules/{rule['id']}").status_code == 200


def test_a_kept_rule_can_be_edited_after_its_duplicate_was_disabled_by_the_upgrade(client, market):
    """The gate review's case over HTTP: PATCH of the kept HKG:700 rule returned 409 citing HKG:00700."""
    from core import desk_store
    desk_store.ensure_schema(rules.SCHEMA, rules.MIGRATIONS[:4])
    with desk_store.transaction() as db:
        for ticker in ('HKG:700', 'HKG:00700'):
            db.execute("INSERT INTO alert_rules(ticker, kind, threshold, enabled, cooldown_seconds, source, "
                       "created_at, updated_at) VALUES (?, 'stop_loss', '50', 1, 0, 'manual', 'x', 'x')", (ticker,))
    rules.ensure()
    for change in ({'enabled': False}, {'note': '跌破年线'}, {'enabled': True}):
        response = client.patch('/api/alerts/rules/1', json=change)
        assert response.status_code == 200, response.text
    assert client.patch('/api/alerts/rules/2', json={'enabled': True}).status_code == 409


def test_a_change_just_short_of_the_threshold_is_not_reached_and_its_distance_is_not_zero(client, market):
    """The gate review's case: 20.01 -> 21.01 is +4.9975 %, short of a 5 % rule; the engine waits, so the
    page must not read 已达到. ``condition_met`` is the engine's comparison; the distance keeps its digits."""
    market.price(CN, '21.01', prev_close='20.01')
    rule = create(client, kind='day_change_pct', threshold='5').json()
    rise = rule['market']
    assert rise['condition_met'] is False
    assert rise['distance'] == '0.0025' and rise['distance_pct'] == '0.0024'
    assert rise['change_pct'] == '5.00', 'the shown change stays rounded as before'
    assert asyncio.run(market.monitor().run_cycle()).actions == {rule['id']: 'none'}, 'the engine waits'
    market.price(CN, '21.0105', prev_close='20.01')     # exactly +5 %
    [reached] = client.get('/api/alerts/rules').json()['items']
    assert reached['market']['condition_met'] is True and reached['market']['distance'] == '0.00'
    # A price rule: a gap below the price's six places is shown, not rounded to 0.
    stop = create(client, threshold='21.01049999').json()['market']
    assert stop['condition_met'] is False
    assert (stop['distance'], stop['distance_pct']) == ('0.00000001', '0.000000048')
    unquoted = client.get('/api/alerts/rules', params={'quotes': 0}).json()['items']
    assert [item['market']['condition_met'] for item in unquoted] == [None, None], 'unknown without a quote'


@pytest.mark.parametrize('quantity, cash, shown, exact, met', [
    ('50000', '50000', '50.00', '50', True),                       # B3 (round 5): exactly 50 % meets a 50 % cap
    ('49996', '50004', '50.00', '49.996', False),                  # shown as 50.00, below the cap
    ('1', '2', '33.33', '33.33333333333333333333', False),         # cut at 20 places, never rounded up
])
def test_today_hands_the_dialog_the_weight_the_monitor_compares(client, market, desk, quantity, cash, shown, exact,
                                                                  met):
    """The new-alert dialog reads the weight from /api/today: ``weight_pct`` is rounded for display,
    ``weight_pct_exact`` is what the monitor compares with a cap (``condition_met`` of the rule's view)."""
    from core.market.today import build_today
    ledger = build_ledger(desk, [opening('Main', cash={'CNY': cash}, positions=[position(CN, quantity, '1')],
                                         date='2026-11-13')])
    market.price(CN, '1', prev_close='1')
    [row] = build_today(ledger, market.runtime)['accounts'][0]['positions']
    assert (row['weight_pct'], row['weight_pct_exact']) == (shown, exact)
    rule = create(client, kind='position_weight_max', threshold='50').json()
    assert rule['market']['weight_pct'] == shown and rule['market']['condition_met'] is met


@pytest.mark.parametrize('holdings, cap, rows, met', [
    # cr_v6b N4: ¥1,000 of one stock in each of three accounts with ¥9,000 cash each, a 10 % cap.
    ([('A', '1000', '9000'), ('B', '1000', '9000'), ('C', '1000', '9000')], '10',
     [('3.33', '3.33333333333333333333')] * 3, False),
    # cr_v6bs B3: ¥100, ¥300 and ¥1,000 of one stock in a ¥2,800 portfolio, a 50 % cap.
    ([('A', '100', '1400'), ('B', '300', '0'), ('C', '1000', '0')], '50',
     [('3.57', '3.57142857142857142857'), ('10.71', '10.71428571428571428571'), ('35.71', '35.71428571428571428571')],
     False),
    # Both ways: 25 % in each of two accounts meets a 50 % cap.
    ([('A', '1000', '1000'), ('B', '1000', '1000')], '50', [('25.00', '25')] * 2, True),
])
def test_one_stock_in_several_accounts_is_judged_on_the_monitors_sum(client, market, desk, holdings, cap, rows, met):
    """Round 6: the figures /api/today gives the new-alert dialog for these portfolios (its fixtures in
    dashboard/frontend/tests/alerts.test.mjs), and what the API and the monitor decide on them."""
    from core.market.today import build_today
    ledger = build_ledger(desk, [opening(name, cash={'CNY': cash}, positions=[position(CN, quantity, '1')],
                                         date='2026-11-13') for name, quantity, cash in holdings])
    market.price(CN, '1', prev_close='1')
    today = build_today(ledger, market.runtime)
    assert [(row['weight_pct'], row['weight_pct_exact']) for account in today['accounts']
            for row in account['positions']] == rows
    rule = create(client, kind='position_weight_max', threshold=cap).json()
    assert rule['market']['condition_met'] is met
    assert asyncio.run(market.monitor().run_cycle()).actions == {rule['id']: 'fire' if met else 'none'}


def test_percentage_rules_measure_in_points(client, market, desk):
    build_ledger(desk, [opening('Main', cash={'CNY': '1000'}, positions=[position(CN, '100', '8')], date='2026-11-13')])
    market.price(CN, '10')
    fall = create(client, kind='day_change_pct', threshold='-5').json()['market']
    assert (fall['value'], fall['distance'], fall['unit']) == ('0.00', '5.00', 'pct_points')
    assert fall['distance_pct'] == '5.00'                   # 10 -> 9.5
    weight = create(client, kind='position_weight_max', threshold='60').json()['market']
    assert (weight['weight_pct'], weight['value'], weight['distance'], weight['distance_pct']) == (
        '50.00', '50.00', '10.00', None)


@pytest.mark.parametrize('body, words', [
    ({'kind': 'stop_loss', 'threshold': '10'}, '代码'),
    ({'ticker': '600001', 'kind': 'stop_loss', 'threshold': '10'}, '交易所:代码'),
    ({'ticker': CN, 'kind': 'trailing', 'threshold': '10'}, '提醒类型'),
    ({'ticker': CN, 'kind': 'stop_loss', 'threshold': 'abc'}, '阈值必须是数字'),
    ({'ticker': CN, 'kind': 'stop_loss', 'threshold': '0'}, '大于 0'),
    ({'ticker': CN, 'kind': 'day_change_pct', 'threshold': '0'}, '不能为 0'),
    ({'ticker': CN, 'kind': 'position_weight_max', 'threshold': '150'}, '不超过 100'),
    ({'ticker': 'IDX:000300', 'kind': 'position_weight_max', 'threshold': '10'}, '指数'),
    ({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10', 'cooldown_seconds': -1}, '冷却时间'),
    ({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10', 'enabled': 'yes'}, 'enabled'),
    ({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10', 'source': 'plan:1'}, '不支持的字段：source'),
    ([1, 2], 'JSON 对象'),
])
def test_invalid_rules_are_refused_in_chinese(client, market, body, words):
    response = client.post('/api/alerts/rules', json=body)
    assert response.status_code == 422
    detail = response.json()['detail']
    assert isinstance(detail, str) and CHINESE.search(detail) and words in detail
    assert rules.list_rules() == []


@pytest.mark.parametrize('threshold, words', [
    ('1e22', '1 亿'), ('1e999999', '1 亿'), ('1e1000000', '1 亿'), (1e22, '1 亿'), (10 ** 30, '1 亿'),
    ('1E-999', '8 位小数'), ('NaN', '数字'), ('-Infinity', '数字'), ('٩', '数字'), ('1_0', '数字'),
    ('0x10', '数字'), ('9.5' + '0' * 40, '过长'),
])
def test_unsupported_thresholds_are_refused_before_anything_is_stored(client, market, threshold, words):
    response = create(client, threshold=threshold)
    assert response.status_code == 422, response.text
    assert CHINESE.search(response.json()['detail']) and words in response.json()['detail']
    assert rules.list_rules() == []
    rule = create(client).json()
    assert client.patch(f"/api/alerts/rules/{rule['id']}", json={'threshold': threshold}).status_code == 422
    assert rules.get(rule['id'])['threshold'] == '10'


@pytest.mark.parametrize('threshold, stored', [
    ('9.5E0', '9.5'), ('95e-1', '9.5'), ('1E+2', '100'), (9.5, '9.5'), ('1e-05', '0.00001'), ('00010.500', '10.5'),
    ('100000000', '100000000'),
])
def test_thresholds_are_stored_as_plain_numbers(client, market, threshold, stored):
    response = create(client, threshold=threshold)
    assert response.status_code == 201 and response.json()['threshold'] == stored


def test_an_identical_rule_is_a_conflict(client, market):
    first = create(client).json()
    response = create(client, threshold='10.0')
    assert response.status_code == 409 and f"#{first['id']}" in response.json()['detail']
    assert create(client, threshold='9').status_code == 201


def test_patch_changes_what_may_change_and_rearms(client, market):
    rule = create(client).json()
    fire(market, CN)
    fired = client.get(f"/api/alerts/rules/{rule['id']}").json()['state']
    assert (fired['armed'], fired['fire_count_today'], fired['fires_left_today']) == (False, 1, 2)
    response = client.patch(f"/api/alerts/rules/{rule['id']}", json={'threshold': '9.5', 'note': '下移'})
    assert response.status_code == 200
    changed = response.json()
    assert (changed['threshold'], changed['note'], changed['state']['armed'], changed['state']['fire_count_today']) == (
        '9.5', '下移', True, 1)
    assert client.patch(f"/api/alerts/rules/{rule['id']}", json={'enabled': False}).json()['enabled'] is False
    for body, words in (({'ticker': 'SHA:600002'}, '删除这条规则后重新创建'), ({'kind': 'target_price'}, '删除'),
                        ({}, '没有要修改的内容'), ({'threshold': '-1'}, '大于 0'), ({'colour': 'red'}, '不支持的字段')):
        refused = client.patch(f"/api/alerts/rules/{rule['id']}", json=body)
        assert refused.status_code == 422 and words in refused.json()['detail'], body
    assert client.patch('/api/alerts/rules/999', json={'note': 'x'}).status_code == 404
    assert client.patch('/api/alerts/rules/0', json={'note': 'x'}).status_code == 422


def test_delete_removes_the_rule_but_keeps_its_events(client, market):
    rule = create(client).json()
    fire(market, CN)
    response = client.delete(f"/api/alerts/rules/{rule['id']}")
    assert response.status_code == 200 and response.json()['deleted'] is True
    assert response.json()['rule']['id'] == rule['id']
    assert client.delete(f"/api/alerts/rules/{rule['id']}").status_code == 404
    assert client.get(f"/api/alerts/rules/{rule['id']}").json()['detail'] == '提醒规则不存在或已被删除。请刷新列表。'
    assert [event['rule_id'] for event in client.get('/api/alerts/events').json()['items']] == [rule['id']]


# -- events -------------------------------------------------------------------

def test_events_are_newest_first_and_page_backwards(client, market):
    tickers = ['SHA:600001', 'SHA:600002', 'SHA:600003']
    ids = [create(client, ticker=ticker).json()['id'] for ticker in tickers]
    fire(market, *tickers)
    first = client.get('/api/alerts/events', params={'limit': 2}).json()
    assert [event['rule_id'] for event in first['items']] == ids[::-1][:2]
    event = first['items'][0]
    assert event['delivery'] == 'notified' and event['notification_seq'] and event['price'] == '9.9'
    assert event['quote_source'] == 'scripted' and event['quote_status'] == 'live' and event['trading_date'] == '2026-11-17'
    assert event['title'].startswith('止损提醒') and '不是委托单' in event['body']
    rest = client.get('/api/alerts/events', params={'limit': 2, 'before': first['next_before']}).json()
    assert [event['rule_id'] for event in rest['items']] == [ids[0]] and rest['next_before'] is None
    only = client.get('/api/alerts/events', params={'rule_id': ids[1]}).json()['items']
    assert [event['rule_id'] for event in only] == [ids[1]]
    assert client.get('/api/alerts/events', params={'limit': 0}).status_code == 422


def test_one_listing_under_two_spellings_is_one_rule_and_one_filter(client, market):
    hk = {'ticker': 'HKG:700', 'kind': 'stop_loss', 'threshold': '50'}
    first = client.post('/api/alerts/rules', json=hk).json()
    again = client.post('/api/alerts/rules', json={**hk, 'ticker': 'HKG:00700'})
    assert again.status_code == 409 and f"#{first['id']}" in again.json()['detail']
    other = create(client).json()
    for spelling in ('HKG:700', 'HKG:0700', 'hkg:00700'):
        listed = client.get('/api/alerts/rules', params={'ticker': spelling, 'quotes': 0}).json()['items']
        assert [item['id'] for item in listed] == [first['id']], spelling
    assert [item['id'] for item in client.get('/api/alerts/rules', params={'ticker': CN, 'quotes': 0}).json()['items']] \
        == [other['id']]
    assert client.get('/api/alerts/rules', params={'ticker': 'not a ticker'}).status_code == 422
    from core.alerts import proposal
    with pytest.raises(rules.RuleConflict):
        proposal.validate({**hk, 'ticker': 'HKG:0700'})


def test_us_class_share_spellings_are_one_rule_and_one_filter(client, market):
    first = client.post('/api/alerts/rules', json={'ticker': 'NYSE:BRK.B', 'kind': 'stop_loss', 'threshold': '400'})
    assert first.status_code == 201
    again = client.post('/api/alerts/rules', json={'ticker': 'NYSE:BRK-B', 'kind': 'stop_loss', 'threshold': '400'})
    assert again.status_code == 409 and f"#{first.json()['id']}" in again.json()['detail']
    listed = client.get('/api/alerts/rules', params={'ticker': 'NYSE:BRK-B', 'quotes': 0}).json()['items']
    assert [item['id'] for item in listed] == [first.json()['id']]


def test_reading_rules_without_quotes_never_opens_the_ledger_for_writing(client, market, desk, monkeypatch):
    """The reviewer's case: in the demo, GET /rules?quotes=0 grew an empty ledger file to 40,960 bytes."""
    create(client)
    create(client, kind='position_weight_max', threshold='60')
    ledger_file = desk / 'ledger.sqlite3'
    ledger_file.write_bytes(b'')
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    for params in ({'quotes': 0}, {}):
        assert client.get('/api/alerts/rules', params=params).status_code == 200
    assert client.get('/api/alerts/rules/1').status_code == 200
    assert ledger_file.stat().st_size == 0
    assert sorted(path.name for path in desk.iterdir() if path.name.startswith('ledger')) == ['ledger.sqlite3']


# -- status -------------------------------------------------------------------

def test_status_before_and_after_the_first_cycle(client, market):
    before = client.get('/api/alerts/status').json()
    assert before['status'] == 'not_started' and before['running'] is False and before['in_process'] is False
    assert '止损委托' in before['notice'] and CHINESE.search(before['message'])
    create(client)
    market.price(CN, '10.5')
    asyncio.run(monitor.AlertMonitor(market.runtime).run_cycle())    # heartbeat in real time
    after = client.get('/api/alerts/status').json()
    assert after['status'] == 'ok' and after['running'] is True and after['paused'] is False
    assert after['rules_enabled'] == 1 and after['rules_active'] == 1 and after['interval_seconds'] == 5
    assert after['markets']['CN'] == {'phase': 'morning', 'trading_day': '2026-11-17', 'is_trading_day': True,
                                      'calendar_confidence': 'calendar', 'next_open': '2026-11-17T13:00:00+08:00',
                                      'rules': 1, 'monitoring': True, 'fail_open': False}
    assert after['markets']['US']['monitoring'] is False and after['quote_source'] == 'scripted'


def test_reading_alerts_on_an_empty_portfolio_creates_no_store(client, market, desk):
    """GETs and the agent's listing read; only a write may create desk.sqlite3."""
    from core.agent_tools import alert_tools
    from core.tools import ToolExecutor

    for path in ('/api/alerts/rules', '/api/alerts/rules?quotes=0', '/api/alerts/events', '/api/alerts/status',
                 '/api/alerts/rules/1', '/api/alerts/events?rule_id=1'):
        assert client.get(path).status_code in (200, 404), path
    listing = asyncio.run(ToolExecutor(alert_tools.alert_tools(None)).execute_tool('list_alert_rules', {}))
    assert listing['rules'] == [] and listing['monitor']['status'] == 'not_started'
    assert rules.counts() == {'total': 0, 'enabled': 0}
    assert not (desk / 'desk.sqlite3').exists()


def test_the_router_and_the_monitor_are_registered_as_features():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get('/api/alerts') is api.router
    assert monitor.background in features.background_tasks()


@pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
def test_demo_mode_reads_work_and_writes_are_rejected(desk, monkeypatch):
    monkeypatch.chdir(desk)   # importing the application creates data directories in the working directory
    from backend.main import app

    write_fake_quotes(desk, {CN: {'name': '示例甲', 'price': '10.5', 'prev_close': '10', 'age_seconds': 3}})
    rule = rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10'})
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    set_market_runtime(None)
    # No lifespan; the real application only answers loopback Host headers.
    client = TestClient(app, base_url='http://127.0.0.1')
    [item] = client.get('/api/alerts/rules').json()['items']
    assert item['id'] == rule['id'] and item['market']['quote']['source'] == 'fake'
    assert client.get('/api/alerts/status').status_code == 200
    assert client.get('/api/alerts/events').json() == {'items': [], 'next_before': None}
    for response in (client.post('/api/alerts/rules', json={'ticker': CN, 'kind': 'stop_loss', 'threshold': '9'}),
                     client.patch(f"/api/alerts/rules/{rule['id']}", json={'enabled': False}),
                     client.delete(f"/api/alerts/rules/{rule['id']}")):
        assert response.status_code == 403 and response.json() == {'detail': 'Demo mode is read-only'}
    assert [item['enabled'] for item in rules.list_rules()] == [True]


def test_the_application_runs_the_monitor_for_its_lifetime(desk, monkeypatch):
    import time

    # The scripted source pins the clock inside a session, below the stop. It is asked for on its own:
    # the read-only demo (TRACKER_DEMO_MODE=1) would never start the monitor, see tests/test_demo_seed.py.
    write_fake_quotes(desk, {CN: {'name': '示例甲', 'price': '9.9', 'prev_close': '10',
                                  'quote_time': '2026-11-17T09:59:57+08:00'}}, now='2026-11-17T10:00:00+08:00')
    rules.create({'ticker': CN, 'kind': 'stop_loss', 'threshold': '10'})
    monkeypatch.setenv('TRACKER_QUOTE_SOURCE', 'fake')
    set_market_runtime(None)
    @asynccontextmanager
    async def lifespan(app):   # the task the application registers (see the test above)
        async with monitor.background(app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(api.router, prefix='/api/alerts')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        for _ in range(100):
            if [item for item in notify.list() if item['kind'] == 'alert']:
                break
            time.sleep(0.05)
        status = client.get('/api/alerts/status').json()
    [alert] = [item for item in notify.list() if item['kind'] == 'alert']
    assert alert['data']['quote_source'] == 'fake' and '不是委托单' in alert['body']
    assert status['in_process'] is True and status['running'] is True and status['quote_mode'] == 'fake'
    after = monitor.status()
    assert after['status'] == 'stopped' and after['in_process'] is False
