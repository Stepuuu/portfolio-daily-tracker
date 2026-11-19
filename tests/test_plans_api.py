"""HTTP contract of /api/plans: plans with links, alerts and live distance, Chinese errors, never a 500, demo mode."""
import re
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import features
from backend.api import ledger as ledger_api
from backend.api import plans as api
from core import plans
from core.alerts import rules
from core.ledger import Ledger
from core.market.runtime import set_market_runtime
from tests.fakes import build_ledger, opening, position, trade, write_fake_quotes
from tests.plan_support import CN, HK, OTHER, payload, pin
from tests.test_alert_monitor import Market

CHINESE = re.compile(r'[一-鿿]')
PLAN_KEYS = {'id', 'ticker', 'account', 'direction', 'thesis', 'invalidation', 'entry_low', 'entry_high', 'stop_price',
             'target_price', 'planned_weight_pct', 'planned_quantity', 'max_loss_cny', 'max_loss', 'review_on',
             'status', 'status_label', 'close_reason', 'created_at', 'updated_at', 'activated_at', 'ended_at',
             'source', 'currency', 'conflicts', 'version', 'identity'}
DETAIL_KEYS = PLAN_KEYS | {'links', 'links_error', 'alerts', 'alerts_error', 'alert_notes', 'market'}
MARKET_KEYS = {'name', 'price', 'prev_close', 'change_pct', 'quote', 'price_reason', 'price_estimated', 'price_estimated_reason',
               'position_quantity', 'planned_quantity',
               'quantity_vs_plan', 'weight_pct', 'weight_estimated', 'weight_reason', 'planned_weight_pct',
               'weight_vs_plan_pct', 'over_planned_weight', 'stop_price', 'distance_to_stop', 'distance_to_stop_pct',
               'stop_reached', 'target_price', 'distance_to_target', 'distance_to_target_pct', 'target_reached', 'as_of'}


@pytest.fixture(autouse=True)
def desk(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    pin(monkeypatch)
    set_market_runtime(None)
    yield tmp_path
    set_market_runtime(None)


@pytest.fixture
def market():
    scripted = Market()
    scripted.price(CN, '10.5')
    set_market_runtime(scripted.runtime)
    return scripted


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ledger_api.router, prefix='/api/ledger')
    for router, prefix, tags in features.routers():
        app.include_router(router, prefix=prefix, tags=tags)
    return TestClient(app)


def create(client, **fields):
    return client.post('/api/plans', json=payload(**fields))


def held(desk):
    """One account: 9 000 CNY and 100 SHA:600001 bought at 10."""
    return build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, positions=[position(CN, '100', '10')],
                                       date='2026-11-13')])


# -- the plan's life ----------------------------------------------------------

def test_create_get_list_and_edit(client, market):
    response = create(client, account='Main', entry_low='10', entry_high='10.8', stop_price='9.5',
                      target_price='12', planned_quantity='1000')
    assert response.status_code == 201
    plan = response.json()
    assert set(plan) == DETAIL_KEYS and set(plan['market']) == MARKET_KEYS
    assert (plan['status'], plan['source'], plan['ticker'], plan['max_loss_cny']) == ('draft', 'manual', CN, '1300.00')
    assert plan['links'] == [] and plan['alerts'] == [] and plan['alert_notes'] == []
    assert client.get(f"/api/plans/{plan['id']}").json() == plan
    listing = client.get('/api/plans').json()
    assert listing['as_of'] == '2026-11-17T10:00:00+08:00'
    assert listing['items'] == [{key: plan[key] for key in PLAN_KEYS}]
    edited = client.patch(f"/api/plans/{plan['id']}", json={'stop_price': '9.8', 'review_on': '2026-12-01'})
    assert edited.status_code == 200
    assert (edited.json()['stop_price'], edited.json()['review_on'], edited.json()['max_loss_cny']) == (
        '9.8', '2026-12-01', '1000.00')


def test_activate_close_and_the_alerts_shown_with_the_plan(client, market):
    plan = create(client, stop_price='9', target_price='12', planned_weight_pct='10').json()
    activated = client.post(f"/api/plans/{plan['id']}/activate")
    assert activated.status_code == 200 and activated.json()['status'] == 'active'
    alerts = activated.json()['alerts']
    assert sorted((rule['kind'], rule['threshold'], rule['enabled'], rule['source']) for rule in alerts) == [
        ('position_weight_max', '10', True, f"plan:{plan['id']}"), ('stop_loss', '9', True, f"plan:{plan['id']}"),
        ('target_price', '12', True, f"plan:{plan['id']}")]
    assert activated.json()['alert_notes'] == []
    assert client.post(f"/api/plans/{plan['id']}/activate").status_code == 200
    assert len(rules.list_rules()) == 3
    closed = client.post(f"/api/plans/{plan['id']}/close", json={'reason': '已到目标价卖出'})
    assert closed.status_code == 200
    assert (closed.json()['status'], closed.json()['close_reason']) == ('closed', '已到目标价卖出')
    assert [rule['enabled'] for rule in closed.json()['alerts']] == [False, False, False]


def test_cancel_with_and_without_a_reason(client, market):
    first = create(client).json()
    assert client.post(f"/api/plans/{first['id']}/cancel").json()['status'] == 'cancelled'
    second = create(client, ticker=OTHER).json()
    cancelled = client.post(f"/api/plans/{second['id']}/cancel", json={'reason': '放弃'}).json()
    assert (cancelled['status'], cancelled['close_reason']) == ('cancelled', '放弃')
    assert [item['id'] for item in client.get('/api/plans', params={'status': 'cancelled'}).json()['items']] == [
        second['id'], first['id']]
    assert client.get('/api/plans', params={'ticker': 'sha:600002'}).json()['items'][0]['id'] == second['id']


# -- the market part ----------------------------------------------------------

def test_detail_shows_price_and_weight_against_the_plan(client, market, desk):
    held(desk)
    plan = create(client, stop_price='9', target_price='12', planned_weight_pct='10').json()
    view = plan['market']
    # 100 x 10.5 = 1 050 of 9 000 + 1 050 = 10 050 -> 10.447...%
    assert (view['price'], view['position_quantity'], view['weight_pct'], view['weight_estimated']) == (
        '10.5', '100', '10.45', False)
    assert (view['weight_vs_plan_pct'], view['over_planned_weight'], view['weight_reason']) == ('0.45', True, None)
    assert (view['distance_to_stop'], view['distance_to_stop_pct'], view['stop_reached']) == ('1.5', '14.29', False)
    assert (view['distance_to_target'], view['distance_to_target_pct'], view['target_reached']) == ('1.5', '14.29', False)
    assert view['quote']['source'] == 'scripted' and view['quote']['status'] == 'live' and view['price_reason'] is None
    assert view['as_of'] == '2026-11-17T10:00:00+08:00'


def test_a_ticker_not_held_has_a_price_and_zero_weight(client, market, desk):
    held(desk)
    market.price(OTHER, '20')
    plan = create(client, ticker=OTHER, stop_price='19.5', planned_quantity='100').json()
    view = plan['market']
    assert (view['price'], view['weight_pct'], view['position_quantity'], view['quantity_vs_plan']) == (
        '20', '0.00', '0', '-100')
    assert (view['distance_to_stop'], view['stop_reached']) == ('0.5', False)


def test_a_plan_in_an_account_that_does_not_hold_the_listing_is_priced_by_the_listing_s_quote(client, market, desk):
    # Main holds it at 10.5; a funded second account plans its first buy of the same stock.
    build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, positions=[position(CN, '100', '10')], date='2026-11-13'),
                        opening('Second', cash={'CNY': '20000'}, date='2026-11-13')])
    main = create(client, account='Main', stop_price='9').json()['market']
    second = create(client, account='Second', stop_price='9').json()['market']
    assert (main['price'], main['distance_to_stop'], main['distance_to_stop_pct']) == ('10.5', '1.5', '14.29')
    assert (second['price'], second['distance_to_stop'], second['distance_to_stop_pct'], second['stop_reached']) == (
        '10.5', '1.5', '14.29', False)
    assert second['price_reason'] is None and second['quote']['status'] == 'live'
    assert (second['position_quantity'], second['weight_pct']) == ('0', '0.00'), 'the second account holds none'
    listed = client.get('/api/plans', params={'market': 1}).json()['items']
    assert {item['account']: item['market']['price'] for item in listed} == {'Main': '10.5', 'Second': '10.5'}


def test_the_weight_over_several_accounts_is_computed_once_from_the_amounts(client, market, desk):
    # 1 000 of the stock in each of two accounts in a 30 000 portfolio: 2 000 / 30 000 = 6.666...% -> 6.67. Each
    # account alone rounds to 3.33; adding those gives 6.66 and hides the breach of a 6.66 cap.
    market.price(OTHER, '10')
    build_ledger(desk, [opening('Main', cash={'CNY': '14000'}, positions=[position(OTHER, '100', '10')], date='2026-11-13'),
                        opening('Second', cash={'CNY': '14000'}, positions=[position(OTHER, '100', '10')],
                                date='2026-11-13')])
    plan = create(client, ticker=OTHER, planned_weight_pct='6.66').json()
    view = plan['market']
    assert (view['weight_pct'], view['over_planned_weight'], view['weight_vs_plan_pct']) == ('6.67', True, '0.01')
    # The buy check divides the same amounts the same way: one more share is 2 010 / 30 000 = 6.70%.
    plans.activate(plan['id'])
    ledger = Ledger(desk / 'ledger.sqlite3')
    [item] = plans.check_proposal(ledger.propose([trade('buy', OTHER, '1', '10', account='Main', date='2026-11-16')]))
    [deviation] = [entry for entry in item['deviations'] if entry['code'] == 'over_planned_weight']
    assert deviation['data']['weight_pct'] == '6.70'


def test_a_provisional_us_close_marks_the_plan_s_price_as_an_estimate(client, desk):
    scripted = Market(at='2026-11-18 05:12')           # 16:12 New York on Tuesday Nov 17: the close is not final yet
    scripted.price('NASDAQ:EXAMPLE', '201.70', prev_close='200', age=12 * 60)     # stamped 16:00:00
    set_market_runtime(scripted.runtime)
    view = create(client, ticker='NASDAQ:EXAMPLE', stop_price='190').json()['market']
    assert view['quote']['status'] == 'closed' and view['price'] is not None
    assert view['price_estimated'] is True and '收盘价' in view['price_estimated_reason'], view
    scripted.clock.advance(seconds=40 * 60)            # 16:52: settled, the verified close
    later = client.get('/api/plans').json()['items'][0]['id']
    settled = client.get(f'/api/plans/{later}').json()['market']
    assert (settled['price_estimated'], settled['price_estimated_reason']) == (False, None), settled


def test_unknown_values_are_null_with_reasons(client, market, desk):
    plan = create(client, stop_price='9', planned_weight_pct='10').json()
    view = plan['market']
    assert view['weight_pct'] is None and '期初余额' in view['weight_reason'], 'no ledger yet'
    assert view['position_quantity'] is None and view['price'] == '10.5'
    held(desk)
    del market.source.quotes[CN]
    view = client.get(f"/api/plans/{plan['id']}").json()['market']
    assert view['price'] is None and view['price_reason'] and view['distance_to_stop'] is None
    assert view['weight_pct'] is None and view['weight_reason'] and view['over_planned_weight'] is None
    other = create(client, ticker=OTHER, account='Elsewhere', planned_weight_pct='5').json()['market']
    assert other['weight_pct'] is None and other['weight_reason'] == '账本中没有账户“Elsewhere”'


def test_a_market_failure_does_not_fail_the_request(client, market, monkeypatch):
    plan = create(client).json()
    monkeypatch.setattr('core.market.today.build_today', lambda *args, **kwargs: 1 / 0)
    response = client.get(f"/api/plans/{plan['id']}")
    assert response.status_code == 200
    assert response.json()['market']['price'] is None and '暂时不可用' in response.json()['market']['price_reason']


def test_a_foreign_plan_s_loss_is_converted_at_the_current_rate(client, market):
    from tests.fakes import fx_rate
    market.source.rates['HKD'] = fx_rate('HKD', '0.92', at=market.clock.now())
    plan = create(client, ticker=HK, entry_high='40', stop_price='36', planned_quantity='1000').json()
    assert (plan['max_loss']['amount'], plan['max_loss']['amount_cny'], plan['max_loss_cny']) == (
        '4000.00', '3680.00', '3680.00')
    assert client.get('/api/plans').json()['items'][0]['max_loss_cny'] == '3680.00'


def test_linked_ledger_events_are_shown(client, market, desk):
    build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, date='2026-11-13')])
    plan = create(client, account='Main', entry_high='10', stop_price='9').json()
    client.post(f"/api/plans/{plan['id']}/activate")
    preview = client.post('/api/ledger/proposals', json={'events': [
        trade('buy', CN, '100', '10.2', account='Main', date='2026-11-16')]}).json()
    assert [item['deviations'][0]['code'] for item in preview['plan_check']] == ['outside_entry_range']
    client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    [link] = client.get(f"/api/plans/{plan['id']}").json()['links']
    assert (link['event_id'], link['relation'], link['reversed']) == (preview['events'][0]['id'], 'entry', False)
    assert [deviation['code'] for deviation in link['deviations']] == ['outside_entry_range']


# -- errors -------------------------------------------------------------------

@pytest.mark.parametrize('body, words', [
    ({'thesis': 'x', 'invalidation': 'y'}, '请填写代码'),
    ({'ticker': '600001', 'thesis': 'x', 'invalidation': 'y'}, '交易所:代码'),
    (payload(stop_price='11', entry_low='10'), '止损价必须低于入场价'),
    (payload(planned_weight_pct='150'), '不超过 100'),
    (payload(status='active'), '计划状态'),
    ([1, 2], 'JSON 对象'),
    ('text', 'JSON 对象'),
    (None, 'JSON 对象'),
])
def test_invalid_plans_are_refused_in_chinese(client, market, body, words):
    response = client.post('/api/plans', json=body)
    assert response.status_code == 422
    detail = response.json()['detail']
    assert isinstance(detail, str) and CHINESE.search(detail) and words in detail
    assert plans.list() == []


def test_unknown_plans_are_404_in_chinese(client, market):
    for path in ('/api/plans/1', '/api/plans/abc', '/api/plans/0', '/api/plans/-1', f'/api/plans/{2 ** 60}'):
        response = client.get(path)
        assert response.status_code == 404 and response.json()['detail'] == '交易计划不存在。请刷新列表。', path
    for response in (client.patch('/api/plans/9', json={'thesis': 'x'}), client.post('/api/plans/9/activate'),
                     client.post('/api/plans/9/close', json={'reason': 'x'}), client.post('/api/plans/9/cancel')):
        assert response.status_code == 404


@pytest.mark.parametrize('text', ['%C2%B2', '%EF%BC%91', '%D9%A1', '%E0%A5%A7', '1%20', '%201', '+1', '1.0', '1_0'])
def test_an_id_that_is_not_ascii_decimal_is_404_on_every_route(client, market, text):
    plan = create(client).json()                           # plan 1: "１" (full-width) or "١" must not reach it
    assert plan['id'] == 1
    for method, suffix, options in (('get', '', {}), ('patch', '', {'json': {'thesis': '改过'}}),
                                    ('post', '/activate', {}), ('post', '/close', {'json': {'reason': 'x'}}),
                                    ('post', '/cancel', {})):
        response = getattr(client, method)(f'/api/plans/{text}{suffix}', **options)
        assert response.status_code == 404, (text, method, suffix, response.status_code)
        assert response.json()['detail'] == '交易计划不存在。请刷新列表。'
    assert (plans.get(1)['status'], plans.get(1)['thesis']) == ('draft', plan['thesis'])


def test_conflicts_are_409(client, market):
    first = create(client, account='Main').json()
    second = create(client, account='Main').json()
    assert client.post(f"/api/plans/{first['id']}/activate").status_code == 200
    refused = client.post(f"/api/plans/{second['id']}/activate")
    assert refused.status_code == 409 and f"#{first['id']}" in refused.json()['detail']
    assert client.post(f"/api/plans/{second['id']}/close", json={'reason': 'x'}).status_code == 409
    assert client.patch(f"/api/plans/{first['id']}", json={'ticker': OTHER}).status_code == 409
    client.post(f"/api/plans/{first['id']}/close", json={'reason': '已离场'})
    for response in (client.patch(f"/api/plans/{first['id']}", json={'thesis': '新'}),
                     client.post(f"/api/plans/{first['id']}/activate"),
                     client.post(f"/api/plans/{first['id']}/cancel")):
        assert response.status_code == 409 and CHINESE.search(response.json()['detail'])


@pytest.mark.parametrize('body, words', [(None, '关闭原因'), ({}, '关闭原因'), ({'reason': ' '}, '关闭原因'),
                                         ({'reason': 'x', 'why': 'y'}, '不支持的字段：why'), (['x'], 'JSON 对象')])
def test_closing_needs_a_reason_object(client, market, body, words):
    plan = create(client).json()
    client.post(f"/api/plans/{plan['id']}/activate")
    response = client.post(f"/api/plans/{plan['id']}/close", json=body)
    assert response.status_code == 422 and words in response.json()['detail']
    assert plans.get(plan['id'])['status'] == 'active'


def test_no_request_is_a_server_error(client, market):
    plan = create(client).json()
    requests = [
        ('get', '/api/plans', {'params': {'status': 'open'}}), ('get', '/api/plans', {'params': {'ticker': '123'}}),
        ('post', '/api/plans', {'content': b'not json', 'headers': {'content-type': 'application/json'}}),
        ('post', '/api/plans', {'json': {'ticker': CN, 'thesis': 5, 'invalidation': []}}),
        ('patch', f"/api/plans/{plan['id']}", {'json': None}), ('patch', f"/api/plans/{plan['id']}", {'json': 'x'}),
        ('patch', f"/api/plans/{plan['id']}", {'json': {'stop_price': {'a': 1}}}),
        ('patch', f"/api/plans/{plan['id']}", {'json': {'planned_quantity': '1e400'}}),
        ('post', f"/api/plans/{plan['id']}/cancel", {'json': {'reason': 7}}),
        ('post', f"/api/plans/{plan['id']}/cancel", {'json': 'x'}),
        ('get', '/api/plans/%F0%9F%98%80', {}),
    ]
    for method, url, options in requests:
        response = getattr(client, method)(url, **options)
        assert 400 <= response.status_code < 500, (method, url, options, response.status_code, response.text)


def test_the_router_is_registered_as_a_feature():
    mounted = {prefix: router for router, prefix, _tags in features.routers()}
    assert mounted.get('/api/plans') is api.router


@pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
def test_demo_mode_reads_work_and_writes_are_rejected(desk, monkeypatch):
    monkeypatch.chdir(desk)   # importing the application creates data directories in the working directory
    from backend.main import app

    write_fake_quotes(desk, {CN: {'name': '示例甲', 'price': '10.5', 'prev_close': '10', 'age_seconds': 3}})
    plan = plans.activate(plans.create(payload(stop_price='9'))['id'])
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    set_market_runtime(None)
    client = TestClient(app, base_url='http://127.0.0.1')
    [item] = client.get('/api/plans').json()['items']
    assert item['id'] == plan['id'] and item['status'] == 'active'
    detail = client.get(f"/api/plans/{plan['id']}").json()
    assert detail['market']['quote']['source'] == 'fake' and detail['market']['price'] == '10.5'
    for response in (client.post('/api/plans', json=payload(ticker=OTHER)),
                     client.patch(f"/api/plans/{plan['id']}", json={'stop_price': '8'}),
                     client.post(f"/api/plans/{plan['id']}/activate"),
                     client.post(f"/api/plans/{plan['id']}/close", json={'reason': 'x'}),
                     client.post(f"/api/plans/{plan['id']}/cancel")):
        assert response.status_code == 403 and response.json() == {'detail': 'Demo mode is read-only'}
    assert [(item['id'], item['status'], item['stop_price']) for item in plans.list()] == [(plan['id'], 'active', '9')]


@pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
def test_demo_reads_of_an_empty_portfolio_create_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from backend.main import app

    portfolio = tmp_path / 'portfolio'
    portfolio.mkdir()
    monkeypatch.setenv('PORTFOLIO_DIR', str(portfolio))
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    set_market_runtime(None)
    client = TestClient(app, base_url='http://127.0.0.1')
    assert client.get('/api/plans').json()['items'] == []
    assert client.get('/api/plans/1').status_code == 404
    assert list(portfolio.iterdir()) == []


# -- converted losses say how good their rate is ------------------------------------

def test_a_loss_converted_at_a_stale_rate_is_marked_estimated_with_the_rate_s_source_and_time(client, market):
    from datetime import timedelta
    from tests.fakes import fx_rate
    market.source.rates['HKD'] = fx_rate('HKD', '0.92', at=market.clock.now() - timedelta(hours=3))
    plan = create(client, ticker=HK, entry_high='40', stop_price='36', planned_quantity='1000').json()
    for loss in (plan['max_loss'], client.get('/api/plans').json()['items'][0]['max_loss'],
                 client.get(f"/api/plans/{plan['id']}").json()['max_loss']):
        assert (loss['amount'], loss['amount_cny'], loss['fx_rate'], loss['fx_status']) == (
            '4000.00', '3680.00', '0.92', 'stale')
        assert loss['estimated'] is True and '汇率' in loss['estimated_reason']
        assert (loss['fx_source'], loss['fx_quote_time']) == ('scripted', '2026-11-17T07:00:00+08:00')
        assert '估算' in loss['formula']


def test_a_loss_converted_at_a_live_rate_is_not_an_estimate(client, market):
    from tests.fakes import fx_rate
    market.source.rates['HKD'] = fx_rate('HKD', '0.92', at=market.clock.now())
    loss = create(client, ticker=HK, entry_high='40', stop_price='36', planned_quantity='1000').json()['max_loss']
    assert (loss['amount_cny'], loss['fx_status'], loss['estimated'], loss['estimated_reason']) == (
        '3680.00', 'live', False, None)
    assert (loss['fx_source'], loss['fx_quote_time']) == ('scripted', '2026-11-17T10:00:00+08:00')
    cny = create(client, ticker=OTHER, entry_high='10', stop_price='9', planned_quantity='100').json()['max_loss']
    assert (cny['amount_cny'], cny['estimated'], cny['fx_source'], cny['fx_quote_time']) == ('100.00', False, None, None)


# -- JSON numbers are read from their text, not through a float ------------------------

@pytest.mark.parametrize('field, text, words', [
    ('planned_quantity', '1000000000000.00000001', '超出'),
    ('planned_weight_pct', '100.0000000000000001', '不超过 100'),
    ('entry_low', '100000000.000000001', '超出'),
    ('entry_high', '100000000.000000001', '超出'),
    ('stop_price', '100000000.000000001', '超出'),
    ('target_price', '100000000.000000001', '超出'),
    ('planned_quantity', '10.0000000000000000001', '最多保留 8 位小数'),   # a float would read 10
])
def test_a_raw_json_number_is_exact_and_cannot_round_into_range(client, market, field, text, words):
    raw = {'content-type': 'application/json'}
    body = '{"ticker": "%s", "thesis": "x", "invalidation": "y", "%s": %s}' % (CN, field, text)
    response = client.post('/api/plans', content=body.encode(), headers=raw)
    assert response.status_code == 422 and words in response.json()['detail'], response.text
    assert plans.list() == []
    plan = create(client).json()
    response = client.patch(f"/api/plans/{plan['id']}", content=('{"%s": %s}' % (field, text)).encode(), headers=raw)
    assert response.status_code == 422 and words in response.json()['detail'], response.text
    assert plans.get(plan['id'])[field] is None


def test_raw_json_numbers_within_range_are_stored_as_written(client, market):
    raw = {'content-type': 'application/json'}
    body = ('{"ticker": "%s", "thesis": "x", "invalidation": "y", "entry_low": 9.50, "entry_high": 10.25, '
            '"stop_price": 0.1, "target_price": 100000000, "planned_quantity": 1000000000000.00000000}' % CN)
    response = client.post('/api/plans', content=body.encode(), headers=raw)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert (plan['entry_low'], plan['entry_high'], plan['stop_price'], plan['target_price'], plan['planned_quantity']) == (
        '9.5', '10.25', '0.1', '100000000', '1000000000000')
    for text in ('NaN', 'Infinity', '{"planned_quantity": 1', '[1, 2]'):
        response = client.patch(f"/api/plans/{plan['id']}", content=text.encode(), headers=raw)
        assert response.status_code == 422 and CHINESE.search(response.json()['detail']), (text, response.text)


# -- reads never create or grow the ledger file ---------------------------------------

def test_reading_a_plan_never_creates_or_grows_the_ledger_file(client, market, desk):
    plan = create(client, stop_price='9', planned_weight_pct='10').json()
    (desk / 'ledger.sqlite3').write_bytes(b'')             # an empty file is no ledger
    detail = client.get(f"/api/plans/{plan['id']}")
    assert detail.status_code == 200 and client.get('/api/plans').status_code == 200
    assert (desk / 'ledger.sqlite3').stat().st_size == 0
    view = detail.json()['market']
    assert view['position_quantity'] is None and view['weight_pct'] is None and view['weight_reason']
    assert view['price'] == '10.5', 'the quote does not depend on the ledger'


@pytest.mark.skipif(sys.version_info < (3, 10), reason='backend.main needs Python 3.10+')
def test_demo_reads_of_a_plan_leave_an_empty_ledger_file_as_it_is(desk, monkeypatch):
    monkeypatch.chdir(desk)
    from backend.main import app

    write_fake_quotes(desk, {CN: {'name': '示例甲', 'price': '10.5', 'prev_close': '10', 'age_seconds': 3}})
    plan = plans.activate(plans.create(payload(stop_price='9', planned_weight_pct='10'))['id'])
    (desk / 'ledger.sqlite3').write_bytes(b'')
    monkeypatch.setenv('TRACKER_DEMO_MODE', '1')
    set_market_runtime(None)
    client = TestClient(app, base_url='http://127.0.0.1')
    detail = client.get(f"/api/plans/{plan['id']}")
    assert detail.status_code == 200 and detail.json()['market']['position_quantity'] is None
    assert client.get('/api/plans').status_code == 200
    assert (desk / 'ledger.sqlite3').stat().st_size == 0


# -- the market block names the instrument ------------------------------------------------

def test_the_market_block_names_the_instrument_held_or_not(client, market, desk):
    build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, positions=[position(CN, '100', '10', name='示例持仓名')],
                                date='2026-11-13')])
    market.price(OTHER, '20', name='示例乙')
    held = create(client, stop_price='9').json()['market']
    assert held['name'] == '示例持仓名', 'the ledger\'s name for a holding, as on the Today page'
    quoted = create(client, ticker=OTHER, stop_price='19').json()['market']
    assert quoted['name'] == '示例乙', 'the quote\'s name for a ticker not held'
    del market.source.quotes[OTHER]
    unknown = client.get(f"/api/plans/{create(client, ticker='SHA:600003').json()['id']}").json()['market']
    assert unknown['name'] is None


def test_a_held_b_share_plan_is_priced_by_its_quote_in_its_trading_currency(client, market, desk, monkeypatch):
    b_share = 'SHA:900901'
    import core.ledger   # booked before the ledger refused B shares (core.ledger.bookable)
    monkeypatch.setattr(core.ledger, 'bookable', core.ledger.instrument)
    build_ledger(desk, [opening('Main', cash={'CNY': '9000'}, positions=[position(b_share, '1000', '3.5')],
                                date='2026-11-13')])
    market.price(b_share, '0.5', prev_close='0.49', name='示例B股')
    view = create(client, ticker=b_share, stop_price='0.4', target_price='0.6').json()['market']
    # Today cannot value the CNY-booked holding at a USD price; the plan's USD stop compares with the USD quote.
    assert (view['price'], view['distance_to_stop'], view['distance_to_target']) == ('0.5', '0.1', '0.1')
    assert view['position_quantity'] == '1000'
    assert view['weight_pct'] is None and view['weight_reason']


# -- an edit never overwrites a newer version ----------------------------------------------

def test_the_stale_edit_message_names_the_time_in_beijing_time(client, market, monkeypatch):
    plan = create(client, stop_price='9', target_price='12').json()
    monkeypatch.setattr(plans, '_now', lambda: '2026-11-18T19:42:25+00:00')   # stored in UTC
    plans.update(plan['id'], {'stop_price': '9.5'})                               # another tab
    stale = client.patch(f"/api/plans/{plan['id']}", json={'thesis': '新理由', 'if_version': plan['version']})
    assert stale.status_code == 409
    detail = stale.json()['detail']
    assert '2026-11-19 03:42' in detail and '北京时间' in detail and '+00:00' not in detail, detail


def test_an_edit_made_against_an_older_version_is_refused_with_409(client, market):
    plan = create(client, stop_price='9', target_price='12').json()
    assert isinstance(plan['version'], str) and plan['version']
    elsewhere = client.patch(f"/api/plans/{plan['id']}", json={'stop_price': '9.5'}).json()   # another tab
    assert elsewhere['version'] != plan['version']
    stale = client.patch(f"/api/plans/{plan['id']}", json={'thesis': '新理由', 'if_version': plan['version']})
    assert stale.status_code == 409 and '已在别处修改' in stale.json()['detail']
    kept = plans.get(plan['id'])
    assert (kept['stop_price'], kept['thesis']) == ('9.5', plan['thesis']), 'nothing of the stale edit is saved'
    fresh = client.patch(f"/api/plans/{plan['id']}", json={'thesis': '新理由', 'if_version': elsewhere['version']})
    assert fresh.status_code == 200 and (fresh.json()['stop_price'], fresh.json()['thesis']) == ('9.5', '新理由')
    assert client.patch(f"/api/plans/{plan['id']}", json={'thesis': 'x', 'if_version': 5}).status_code == 422
    # Without if_version an edit is applied as before; the version follows every write.
    activated = client.post(f"/api/plans/{plan['id']}/activate").json()
    assert activated['version'] != fresh.json()['version']


# -- what cannot be read is unavailable, not empty -----------------------------------------------

def test_unreadable_rules_and_links_are_unavailable_not_an_empty_list(client, market, monkeypatch):
    plan = create(client).json()
    assert (plan['links'], plan['links_error'], plan['alerts'], plan['alerts_error']) == ([], None, [], None)

    def broken(*args, **kwargs):
        raise RuntimeError('simulated failure')

    monkeypatch.setattr(plans, 'links_for_plan', broken)
    monkeypatch.setattr(plans, 'alert_rules', broken)
    detail = client.get(f"/api/plans/{plan['id']}").json()
    assert detail['links'] is None and '关联成交' in detail['links_error'] and '不可用' in detail['links_error']
    assert detail['alerts'] is None and '提醒规则' in detail['alerts_error'] and detail['alert_notes'] == []


# -- the list can carry the market block (the Today chip) ------------------------------------------

def test_the_list_carries_the_market_block_of_open_plans_on_request(client, market, desk):
    held(desk)
    active_plan = create(client, account='Main', stop_price='9', target_price='12').json()
    client.post(f"/api/plans/{active_plan['id']}/activate")
    ended = create(client, ticker=OTHER).json()
    client.post(f"/api/plans/{ended['id']}/cancel")
    plain = client.get('/api/plans', params={'status': 'all'}).json()['items']
    assert all('market' not in item for item in plain), 'no quotes are asked for by default'
    items = {item['id']: item for item in client.get('/api/plans', params={'status': 'all', 'market': '1'}).json()['items']}
    view = items[active_plan['id']]['market']
    assert (view['price'], view['stop_reached'], view['distance_to_stop_pct'], view['quote']['source']) == (
        '10.5', False, '14.29', 'scripted')
    assert items[ended['id']]['market'] is None, 'an ended plan is not compared with the market'
    assert client.get('/api/plans', params={'market': 'yes'}).status_code == 422


# -- the listing, as the other pages match it -----------------------------------

def test_plans_carry_the_listing_identity_as_alerts_and_watchlist_do(client, market):
    from core.market.search import identity_key
    dashed = create(client, ticker='NYSE:BRK-B').json()
    assert dashed['identity'] == identity_key('NYSE:BRK.B') == identity_key('NYSE:BRK-B')
    padded = create(client, ticker='HKG:700').json()
    assert padded['identity'] == identity_key('HKG:00700')
    listed = {item['id']: item['identity'] for item in client.get('/api/plans').json()['items']}
    assert listed == {dashed['id']: dashed['identity'], padded['id']: padded['identity']}
    assert client.get(f"/api/plans/{dashed['id']}").json()['identity'] == dashed['identity']
    with_market = client.get('/api/plans', params={'market': '1'}).json()['items']
    assert {item['id']: item['identity'] for item in with_market} == listed
