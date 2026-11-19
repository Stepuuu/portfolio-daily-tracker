"""Ledger previews carry a plan check, confirmation links once, and plans never change the ledger's answer."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import ledger as ledger_api
from core import desk_store, plans
from core.ledger import Ledger
from core.market.runtime import set_market_runtime
from tests.fakes import build_ledger, opening, position, trade
from tests.plan_support import CN, OTHER, active, at, payload, pin
from tests.test_alert_monitor import Market

LEDGER_KEYS = {'id', 'revision', 'events', 'before', 'after', 'duplicates', 'warnings'}
RECEIPT_KEYS = {'proposal_id', 'revision', 'applied', 'duplicates', 'state'}
ITEM_KEYS = {'event_ref', 'index', 'kind', 'date', 'ticker', 'account', 'quantity', 'price', 'plan_id', 'relation',
             'deviations'}
DAY = '2026-11-16'


@pytest.fixture(autouse=True)
def market(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    pin(monkeypatch)                                       # plans change on Monday 2026-11-16
    scripted = Market()                                    # quotes at Tuesday 2026-11-17 10:00, in session
    scripted.price(CN, '10')
    scripted.price(OTHER, '20')
    set_market_runtime(scripted.runtime)
    yield scripted
    set_market_runtime(None)


@pytest.fixture
def ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'CNY': '10000'}, date='2026-11-13'),
                                   opening('Other', cash={'CNY': '10000'}, date='2026-11-13')])


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ledger_api.router, prefix='/api/ledger')
    return TestClient(app)


def buy(quantity='100', price='10', ticker=CN, **fields):
    return trade('buy', ticker, quantity, price, account=fields.pop('account', 'Main'), date=fields.pop('date', DAY),
                 **fields)


def sell(quantity='100', price='10', ticker=CN, **fields):
    return trade('sell', ticker, quantity, price, account=fields.pop('account', 'Main'), date=fields.pop('date', DAY),
                 **fields)


def check(ledger, *events):
    """The plan check of a preview of ``events`` (nothing is booked)."""
    return plans.check_proposal(ledger.propose(list(events)))


def codes(item):
    return [deviation['code'] for deviation in item['deviations']]


def book(ledger, *events):
    proposal = ledger.propose(list(events))
    ledger.confirm(proposal['id'])
    plans.record_links(ledger.proposal(proposal['id']))
    return proposal


def link_rows():
    with desk_store.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM plan_links ORDER BY rowid")]


# -- the HTTP hook ------------------------------------------------------------

def test_without_plans_the_ledger_answers_exactly_as_before(client, ledger, tmp_path):
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]})
    assert preview.status_code == 200 and set(preview.json()) == LEDGER_KEYS
    reread = client.get(f"/api/ledger/proposals/{preview.json()['id']}").json()
    assert set(reread) == LEDGER_KEYS | {'created_at', 'receipt'}
    confirmed = client.post(f"/api/ledger/proposals/{preview.json()['id']}/confirm")
    assert confirmed.status_code == 200 and set(confirmed.json()) == RECEIPT_KEYS
    assert not (tmp_path / 'desk.sqlite3').exists(), 'the ledger must not create the desk store'


def test_an_empty_plan_table_is_still_plans_unused(client, ledger):
    plans.ensure()                                         # the desk store and the plan tables exist, no plan
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]}).json()
    assert set(preview) == LEDGER_KEYS
    client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    assert link_rows() == []


def test_a_preview_carries_the_plan_check_and_confirming_links_once(client, ledger):
    plan = active(account='Main', entry_low='9.5', entry_high='10.5', stop_price='9', target_price='12',
                  planned_quantity='1000')
    preview = client.post('/api/ledger/proposals', json={'events': [buy(), {'kind': 'deposit', 'date': DAY,
                                                                           'account': 'Main', 'currency': 'CNY',
                                                                           'amount': '1'}]}).json()
    assert set(preview) == LEDGER_KEYS | {'plan_check'}
    event = preview['events'][0]
    assert preview['plan_check'] == [{
        'event_ref': event['id'], 'index': 0, 'kind': 'buy', 'date': DAY, 'ticker': CN, 'account': 'Main',
        'quantity': '100', 'price': '10', 'plan_id': plan['id'], 'relation': 'entry', 'deviations': []}]
    # The page reloads the preview from here; it carries the same check.
    reread = client.get(f"/api/ledger/proposals/{preview['id']}").json()
    assert reread['plan_check'] == preview['plan_check']

    first = client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    second = client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    assert first.status_code == second.status_code == 200 and first.json() == second.json()
    assert set(first.json()) == RECEIPT_KEYS
    assert first.json() == Ledger(ledger.path).proposal(preview['id'])['receipt']
    rows = link_rows()
    assert [(row['event_id'], row['plan_id'], row['relation'], row['proposal_id']) for row in rows] == [
        (event['id'], plan['id'], 'entry', preview['id'])]
    assert plans.record_links(ledger.proposal(preview['id'])) == 0
    assert len(link_rows()) == 1
    assert 'plan_check' not in client.get(f"/api/ledger/proposals/{preview['id']}").json(), 'confirmed: as stored'


def test_csv_and_opening_previews_are_checked_too(client, ledger, tmp_path):
    active(OTHER)
    content = 'external_id,date,kind,account,ticker,currency,quantity,price\nx1,2026-11-16,buy,Main,SHA:600001,CNY,10,10\n'
    imported = client.post('/api/ledger/csv-preview', json={'content': content, 'source': 'broker-csv'}).json()
    assert [codes(item) for item in imported['plan_check']] == [['no_plan']]
    (tmp_path / 'holdings').mkdir()
    (tmp_path / 'holdings' / '2026-11-12.json').write_text('{"groups": {"Third": {"cash": 1, "cost_basis": 1}}}')
    assert client.post('/api/ledger/opening-preview', json={'date': '2026-11-13'}).json()['plan_check'] == []


def test_a_failing_plan_check_never_changes_the_ledger_answer(client, ledger, monkeypatch):
    active()

    def broken(*args, **kwargs):
        raise RuntimeError('simulated failure')

    monkeypatch.setattr(plans, 'check_proposal', broken)
    monkeypatch.setattr(plans, 'record_links', broken)
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]})
    assert preview.status_code == 200
    body = preview.json()
    assert set(body) == LEDGER_KEYS | {'plan_check_error'} and '入账不受影响' in body['plan_check_error']
    assert body['events'][0]['ticker'] == CN and body['after']['accounts']['Main']['positions'][CN]['quantity'] == '100'
    assert client.get(f"/api/ledger/proposals/{body['id']}").status_code == 200
    confirmed = client.post(f"/api/ledger/proposals/{body['id']}/confirm")
    assert confirmed.status_code == 200 and set(confirmed.json()) == RECEIPT_KEYS
    assert confirmed.json() == ledger.proposal(body['id'])['receipt'] and ledger.state()['revision'] == 2
    # A ledger error is still the ledger's error, unchanged.
    refused = client.post('/api/ledger/proposals', json={'events': [buy(quantity='-1')]})
    assert refused.status_code == 422 and 'plan_check_error' not in refused.json()
    assert client.post('/api/ledger/proposals/unknown/confirm').status_code == 422


def test_a_broken_store_does_not_block_the_ledger(client, ledger, tmp_path):
    active()
    desk_store.ensure_schema(plans.SCHEMA, plans.MIGRATIONS + [['CREATE TABLE later (id INTEGER PRIMARY KEY)']])
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]})
    assert preview.status_code == 200 and 'plan_check_error' in preview.json()
    assert client.post(f"/api/ledger/proposals/{preview.json()['id']}/confirm").status_code == 200
    assert ledger.state()['accounts']['Main']['positions'][CN]['quantity'] == '100'


def test_two_confirmations_at_once_write_one_link(ledger, monkeypatch):
    import threading
    active()
    proposal = ledger.propose([buy()])
    ledger.confirm(proposal['id'])
    confirmed = ledger.proposal(proposal['id'])
    real, barrier = plans.check_proposal, threading.Barrier(2, timeout=10)

    def both_checked(*args, **kwargs):
        result = real(*args, **kwargs)
        barrier.wait()                                     # both saw no link yet; both will insert
        return result

    monkeypatch.setattr(plans, 'check_proposal', both_checked)
    written = []
    threads = [threading.Thread(target=lambda: written.append(plans.record_links(confirmed))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(written) == [0, 1] and len(link_rows()) == 1


def test_an_interrupted_link_write_is_repaired_by_confirming_again(client, ledger, monkeypatch):
    active()
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]}).json()
    real = plans.record_links
    monkeypatch.setattr(plans, 'record_links', lambda proposal: (_ for _ in ()).throw(RuntimeError('crash')))
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    assert link_rows() == []
    monkeypatch.setattr(plans, 'record_links', real)
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    assert [row['relation'] for row in link_rows()] == ['entry']


# -- relations ----------------------------------------------------------------

def test_relations_follow_the_position_in_the_plan_scope(ledger):
    plan = active(account='Main', target_price='12')
    [first] = check(ledger, buy())
    assert (first['plan_id'], first['relation']) == (plan['id'], 'entry')
    book(ledger, buy())
    [more] = check(ledger, buy('50'))
    assert more['relation'] == 'add'
    [part] = check(ledger, sell('40', '12'))
    assert part['relation'] == 'reduce' and part['deviations'] == []
    [whole] = check(ledger, sell('100', '12'))
    assert whole['relation'] == 'exit'
    # Shares in another account are outside an account plan's scope.
    [elsewhere] = check(ledger, buy(account='Other'))
    assert (elsewhere['plan_id'], elsewhere['relation'], codes(elsewhere)) == (None, 'none', ['no_plan'])


def test_a_plan_for_every_account_sees_every_account(ledger):
    plan = active(planned_quantity='150')
    book(ledger, buy(account='Other'))
    [item] = check(ledger, buy(account='Main'))
    assert (item['plan_id'], item['relation']) == (plan['id'], 'add')
    assert codes(item) == ['over_planned_quantity'] and '全部账户' in item['deviations'][0]['message']


def test_events_in_one_proposal_are_walked_in_order(ledger):
    active(account='Main', planned_quantity='150')
    items = check(ledger, buy(), buy(), {'kind': 'deposit', 'date': DAY, 'account': 'Main', 'currency': 'CNY',
                                         'amount': '5'}, sell('200'))
    assert [(item['index'], item['relation'], codes(item)) for item in items] == [
        (0, 'entry', []), (1, 'add', ['over_planned_quantity']), (3, 'exit', [])]
    assert items[1]['deviations'][0]['data'] == {'quantity_after': '200', 'planned_quantity': '150'}


# -- deviation codes ----------------------------------------------------------

def test_no_plan(ledger):
    active(OTHER)                                          # plans are in use, but not for this ticker
    [item] = check(ledger, buy())
    assert (item['plan_id'], item['relation']) == (None, 'none')
    [deviation] = item['deviations']
    assert (deviation['code'], deviation['level']) == ('no_plan', 'warning')
    assert '没有启用中的交易计划' in deviation['message']
    book(ledger, buy())
    [sale] = check(ledger, sell())
    assert (sale['plan_id'], sale['relation'], sale['deviations']) == (None, 'none', []), 'a sale needs no plan'


def test_no_plan_names_a_draft_or_a_plan_activated_after_the_trade(ledger, monkeypatch):
    draft = plans.create(payload())
    [item] = check(ledger, buy())
    assert codes(item) == ['no_plan'] and f'计划 #{draft["id"]} 仍是草稿' in item['deviations'][0]['message']
    pin(monkeypatch, at('2026-11-17'))
    plans.activate(draft['id'])
    [late] = check(ledger, buy(date='2026-11-16'))
    assert codes(late) == ['no_plan'] and late['plan_id'] is None
    assert '于 2026-11-17 启用，晚于这笔交易的日期 2026-11-16' in late['deviations'][0]['message']
    [same_day] = check(ledger, buy(date='2026-11-17'))
    assert (same_day['plan_id'], codes(same_day)) == (draft['id'], [])


def test_after_invalidation(ledger, monkeypatch):
    pin(monkeypatch, at('2026-11-13'))
    plan = active(target_price='12')
    book(ledger, buy(date='2026-11-13'))
    pin(monkeypatch, at('2026-11-16', 14))
    plans.close(plan['id'], '跌破年线')
    [again] = check(ledger, buy(date='2026-11-16'))
    assert (again['plan_id'], again['relation']) == (plan['id'], 'none')
    [deviation] = again['deviations']
    assert (deviation['code'], deviation['level']) == ('after_invalidation', 'warning')
    assert deviation['message'] == f'计划 #{plan["id"]} 已于 2026-11-16 关闭（跌破年线），之后仍买入 {CN}。'
    assert deviation['data'] == {'plan_status': 'closed', 'ended_on': '2026-11-16', 'close_reason': '跌破年线'}
    # A buy dated while the plan was in force still belongs to it (booked late).
    [backfilled] = check(ledger, buy(date='2026-11-14'))
    assert (backfilled['plan_id'], backfilled['relation'], codes(backfilled)) == (plan['id'], 'add', [])
    # The sale that ends a plan, booked after closing it on the same day, is its exit.
    [exit_] = check(ledger, sell(date='2026-11-16', price='12'))
    assert (exit_['plan_id'], exit_['relation'], codes(exit_)) == (plan['id'], 'exit', [])
    [later_sale] = check(ledger, sell(date='2026-11-17'))
    assert (later_sale['plan_id'], later_sale['relation'], codes(later_sale)) == (None, 'none', [])


# -- a plan's dates are the trading days of its own market --------------------------------
# Trades are dated in the market's own calendar (a New York buy at 01:30 Beijing is dated the day before), so a
# plan's activation and end count in that market's local day too.

US = 'NASDAQ:EXAMPLE'


@pytest.fixture
def us_ledger(tmp_path):
    return build_ledger(tmp_path, [opening('Main', cash={'USD': '10000'}, date='2026-11-13')])


def us_buy(quantity, date='2026-11-17', price='200'):
    return buy(quantity, price, ticker=US, currency='USD', date=date)


def us_sell(quantity, date='2026-11-17', price='200'):
    return sell(quantity, price, ticker=US, currency='USD', date=date)


def test_a_us_plan_activated_after_midnight_beijing_covers_that_new_york_day(us_ledger, monkeypatch):
    pin(monkeypatch, '2026-11-17T17:30:00+00:00')         # 01:30 Beijing on Nov 18 = 12:30 New York on Nov 17
    plan = active(US, account='Main', planned_quantity='10')
    [item] = check(us_ledger, us_buy('12'))
    assert (item['plan_id'], item['relation'], codes(item)) == (plan['id'], 'entry', ['over_planned_quantity'])
    book(us_ledger, us_buy('12'))
    [row] = link_rows()
    assert (row['plan_id'], row['relation']) == (plan['id'], 'entry')
    [link] = plans.links_for_plan(plan['id'])
    [excess] = link['deviations']                         # 12 bought against a plan of 10: two shares over
    assert excess['code'] == 'over_planned_quantity'
    assert (excess['data']['quantity_after'], excess['data']['planned_quantity']) == ('12', '10'), excess


def test_a_us_buy_on_the_new_york_day_a_plan_was_closed_comes_after_it(us_ledger, monkeypatch):
    pin(monkeypatch, '2026-11-16T15:00:00+00:00')         # 10:00 New York on Nov 16
    plan = active(US, account='Main')
    book(us_ledger, us_buy('10', date='2026-11-16'))
    pin(monkeypatch, '2026-11-17T17:30:00+00:00')         # closed at 12:30 New York on Nov 17 (01:30 Beijing, Nov 18)
    plans.close(plan['id'], '跌破年线')
    [again] = check(us_ledger, us_buy('1'))
    assert (again['plan_id'], codes(again)) == (plan['id'], ['after_invalidation'])
    assert '已于 2026-11-17 关闭' in again['deviations'][0]['message']
    [exit_] = check(us_ledger, us_sell('10'))                 # the sale that ends it, the same New York day
    assert (exit_['plan_id'], exit_['relation']) == (plan['id'], 'exit')
    [later] = check(us_ledger, us_sell('10', date='2026-11-18'))
    assert later['plan_id'] is None


def test_a_us_plan_activated_on_the_next_new_york_day_says_so(us_ledger, monkeypatch):
    pin(monkeypatch, '2026-11-18T15:00:00+00:00')         # 10:00 New York on Nov 18 (23:00 Beijing)
    plan = active(US, account='Main')
    [late] = check(us_ledger, us_buy('1'))
    assert (late['plan_id'], codes(late)) == (None, ['no_plan'])
    assert f'计划 #{plan["id"]} 于 2026-11-18 启用，晚于这笔交易的日期 2026-11-17' in late['deviations'][0]['message']


@pytest.mark.parametrize('ticker, currency, stamp', [
    (CN, 'CNY', '2026-11-17T06:30:00+00:00'),               # 14:30 Shanghai
    ('HKG:00001', 'HKD', '2026-11-17T07:30:00+00:00'),      # 15:30 Hong Kong
    ('NASDAQ:EXAMPLE', 'USD', '2026-11-17T20:30:00+00:00'),  # 15:30 New York (04:30 Beijing on Nov 18)
])
def test_a_plan_activated_and_traded_on_the_same_local_day_covers_the_trade(tmp_path, monkeypatch, ticker, currency,
                                                                               stamp):
    ledger = build_ledger(tmp_path, [opening('Main', cash={currency: '100000'}, date='2026-11-13')])
    pin(monkeypatch, stamp)
    plan = active(ticker, account='Main')
    [item] = check(ledger, buy('100', '10', ticker=ticker, currency=currency, date='2026-11-17'))
    assert (item['plan_id'], item['relation'], codes(item)) == (plan['id'], 'entry', [])


def test_after_a_cancelled_plan(ledger):
    plan = plans.cancel(plans.create(payload())['id'], '想法放弃')
    [item] = check(ledger, buy())
    assert (item['plan_id'], codes(item)) == (plan['id'], ['after_invalidation'])
    assert '取消（想法放弃）' in item['deviations'][0]['message']
    replacement = active()
    [now] = check(ledger, buy())
    assert (now['plan_id'], codes(now)) == (replacement['id'], [])


@pytest.mark.parametrize('price, found', [('10.6', '高于计划入场区间 9.5–10.5'), ('9.4', '低于计划入场区间 9.5–10.5'),
                                          ('10.5', None), ('9.5', None)])
def test_outside_entry_range(ledger, price, found):
    active(entry_low='9.5', entry_high='10.5')
    [item] = check(ledger, buy(price=price))
    if found is None:
        assert item['deviations'] == []
    else:
        [deviation] = item['deviations']
        assert (deviation['code'], deviation['level']) == ('outside_entry_range', 'warning')
        assert found in deviation['message']
        assert deviation['data'] == {'price': price, 'entry_low': '9.5', 'entry_high': '10.5'}


def test_outside_a_one_sided_entry_range(ledger):
    active(entry_high='10')
    [above] = check(ledger, buy(price='10.01'))
    assert codes(above) == ['outside_entry_range'] and '高于计划入场价上限 10' in above['deviations'][0]['message']
    assert check(ledger, buy(price='1'))[0]['deviations'] == []


@pytest.mark.parametrize('price, expected', [('9', ['below_stop_buy']), ('8.5', ['below_stop_buy']),
                                             ('9.01', [])])
def test_below_stop_buy(ledger, price, expected):
    active(stop_price='9', target_price='12')
    [item] = check(ledger, buy(price=price))
    assert codes(item) == expected
    if expected:
        assert item['deviations'][0]['level'] == 'warning' and '止损价 9' in item['deviations'][0]['message']


def test_a_buy_below_the_stop_is_also_outside_the_entry_range(ledger):
    active(entry_low='9.5', stop_price='9')
    assert codes(check(ledger, buy(price='9'))[0]) == ['outside_entry_range', 'below_stop_buy']


@pytest.mark.parametrize('quantity, expected', [('1000', []), ('1000.5', ['over_planned_quantity'])])
def test_over_planned_quantity(ledger, quantity, expected):
    active(account='Main', planned_quantity='1000')
    [item] = check(ledger, buy(quantity, price='1'))
    assert codes(item) == expected


@pytest.mark.parametrize('quantity, expected', [('100', []), ('101', ['over_planned_weight'])])
def test_over_planned_weight(ledger, quantity, expected):
    # 10 000 CNY in Main and 10 000 in Other; 100 shares at 10 are 1 000 of 20 000, i.e. 5 %.
    active(account='Main', planned_weight_pct='5')
    [item] = check(ledger, buy(quantity))
    assert codes(item) == expected
    if expected:
        [deviation] = item['deviations']
        assert deviation['level'] == 'warning'
        assert deviation['data'] == {'weight_pct': '5.05', 'planned_weight_pct': '5', 'estimated': False,
                                     'price_basis': 'current_quotes', 'quotes_as_of': '2026-11-17T10:00:00+08:00'}
        assert '约占组合 5.05%' in deviation['message'] and '超过计划的 5%' in deviation['message']


def test_the_weight_uses_current_quotes_and_says_when_they_are_estimates(ledger, market):
    active(planned_weight_pct='5')
    market.price(CN, '11', age=30 * 60)                    # a stale quote: valued, but not verified
    [item] = check(ledger, buy(price='10'))
    [deviation] = item['deviations']
    assert deviation['code'] == 'over_planned_weight' and deviation['data']['estimated'] is True
    assert deviation['data']['weight_pct'] == '5.47'       # 100 x 11 = 1 100 of 9 000 + 1 100 + 10 000
    assert '估算' in deviation['message']


def test_weight_unknown_when_the_position_cannot_be_valued(ledger, market):
    active(planned_weight_pct='1')
    del market.source.quotes[CN]                           # no quote at all for the ticker
    [item] = check(ledger, buy('900'))                     # far above 1 % whatever the price
    [deviation] = item['deviations']
    assert (deviation['code'], deviation['level']) == ('weight_unknown', 'info')
    assert '无法计算买入后的仓位比例' in deviation['message'] and '未检查计划仓位 1%' in deviation['message']
    assert set(deviation['data']) == {'planned_weight_pct', 'reason'}


def test_weight_unknown_when_the_quote_service_is_down(ledger, market):
    active(planned_weight_pct='50')
    market.source.down = 'simulated outage'
    [item] = check(ledger, buy())
    assert codes(item) == ['weight_unknown']


def test_weight_unknown_when_the_quotes_cannot_be_read(ledger, monkeypatch):
    active(planned_weight_pct='50')
    monkeypatch.setattr(plans, '_fetch_market', lambda runtime, tickers: (_ for _ in ()).throw(RuntimeError('boom')))
    [item] = check(ledger, buy())
    assert codes(item) == ['weight_unknown'] and '暂时无法取得行情' in item['deviations'][0]['message']


def test_weight_unknown_when_the_valuation_fails(ledger, monkeypatch):
    active(planned_weight_pct='50')
    monkeypatch.setattr(plans, '_valued', lambda state, quotes, fx: (_ for _ in ()).throw(RuntimeError('boom')))
    [item] = check(ledger, buy())
    assert codes(item) == ['weight_unknown'] and '暂时无法计算组合市值' in item['deviations'][0]['message']


def test_quotes_are_asked_for_only_when_a_plan_has_a_weight(ledger, market):
    active(planned_quantity='1000')
    calls = len(market.source.calls)
    check(ledger, buy())
    assert len(market.source.calls) == calls


@pytest.mark.parametrize('price, expected', [('8.9', ['sold_below_stop']), ('9', []), ('9.01', ['sold_before_target']),
                                             ('11.99', ['sold_before_target']), ('12', []), ('13', [])])
def test_sales_against_stop_and_target(ledger, price, expected):
    active(stop_price='9', target_price='12')
    book(ledger, buy())
    [item] = check(ledger, sell('50', price))
    assert codes(item) == expected
    assert all(deviation['level'] == 'info' for deviation in item['deviations'])


def test_a_sale_without_a_stop_is_before_target_below_it(ledger):
    active(target_price='12')
    book(ledger, buy())
    assert codes(check(ledger, sell('50', '1'))[0]) == ['sold_before_target']


# A ledger state as ``Ledger.propose`` returns it: Main with 10 000 CNY and nothing held.
EMPTY_MAIN = {'accounts': {'Main': {'cash_balances': {'CNY': '10000'}, 'positions': {}, 'contributed_cny': '0',
                                    'fund_cny': '0', 'realized': {}}}}


def test_a_position_without_a_value_is_never_counted_as_zero_weight():
    from core.market.quotes import unavailable
    from tests.fakes import shanghai
    plan = {'id': 7, 'ticker': CN, 'account': None, 'status': 'active', 'activated_at': at('2026-11-01'),
            'ended_at': None, 'close_reason': None, 'entry_low': None, 'entry_high': None, 'stop_price': None,
            'target_price': None, 'planned_weight_pct': '10', 'planned_quantity': None}
    asked = []

    def market(tickers):
        asked.append(tickers)
        return {CN: unavailable(CN, 'test', shanghai('2026-11-17 10:00'), '测试：没有价格')}, {}, None

    [item] = plans.check_events([{'id': 'e1', **buy()}], EMPTY_MAIN, market, plans=[plan])
    assert codes(item) == ['weight_unknown'] and '测试：没有价格' in item['deviations'][0]['message']
    assert asked == [[CN]], 'one read for the tickers held right after the buy'


def test_check_events_works_on_given_plans_without_a_store(tmp_path):
    plan = {'id': 7, 'ticker': CN, 'account': None, 'status': 'active', 'activated_at': at('2026-11-01'),
            'ended_at': None, 'close_reason': None, 'entry_low': None, 'entry_high': None, 'stop_price': '9',
            'target_price': None, 'planned_weight_pct': '10', 'planned_quantity': None}
    events = [{'id': 'e1', **buy(price='8')}]
    [item] = plans.check_events(events, EMPTY_MAIN, None, plans=[plan], market_reason='测试：没有估值')
    assert (item['plan_id'], item['relation'], codes(item)) == (7, 'entry', ['below_stop_buy', 'weight_unknown'])
    assert '测试：没有估值' in item['deviations'][1]['message']
    assert not (tmp_path / 'desk.sqlite3').exists()


# -- confirming never waits for quotes ------------------------------------------------

def blocked_quotes(market):
    """Make every quote and rate request hang until the returned event is set; returns (gate, asked)."""
    import threading
    from core.market.quotes import SourceError
    gate, asked = threading.Event(), []

    def hang(wanted):
        asked.append(wanted)
        gate.wait(10)
        raise SourceError('测试：行情服务无响应')

    market.source.fetch = hang
    market.source.fetch_fx = hang
    return gate, asked


def confirm_in_time(proposal_id, seconds=5):
    """Confirm through the route in a thread; returns (receipt or None if it did not return in time, thread)."""
    import threading
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(receipt=ledger_api.confirm(proposal_id)), daemon=True)
    worker.start()
    worker.join(seconds)
    return answer.get('receipt'), worker


def test_the_receipt_does_not_wait_for_quotes_and_the_link_keeps_the_preview_s_weight(client, ledger, market):
    plan = active(account='Main', planned_weight_pct='5')
    preview = client.post('/api/ledger/proposals', json={'events': [buy('150')]}).json()   # 1 500 of 20 000
    assert [codes(item) for item in preview['plan_check']] == [['over_planned_weight']]
    gate, asked = blocked_quotes(market)
    try:
        receipt, worker = confirm_in_time(preview['id'])
    finally:
        gate.set()
    worker.join()
    assert receipt is not None, 'the confirmation waited for the quote service'
    assert set(receipt) == RECEIPT_KEYS and receipt == ledger.proposal(preview['id'])['receipt']
    assert asked == [], 'confirming asked for quotes'
    [link] = plans.links_for_plan(plan['id'])
    [deviation] = link['deviations']
    assert deviation == preview['plan_check'][0]['deviations'][0], 'the weight the preview showed'
    assert deviation['data']['weight_pct'] == '7.50' and deviation['data']['quotes_as_of']


def test_without_the_preview_s_quotes_the_link_leaves_the_weight_unknown(ledger, market):
    plan = active(account='Main', planned_weight_pct='5')
    proposal = ledger.propose([buy('150')])                # previewed elsewhere (another process, or before a restart)
    gate, asked = blocked_quotes(market)
    try:
        receipt, worker = confirm_in_time(proposal['id'])
    finally:
        gate.set()
    worker.join()
    assert receipt is not None and asked == []
    [link] = plans.links_for_plan(plan['id'])
    [deviation] = link['deviations']
    assert (deviation['code'], deviation['level']) == ('weight_unknown', 'info')
    assert '不等待行情' in deviation['message']


def test_quotes_older_than_the_limit_are_not_reused(client, ledger, market, monkeypatch):
    plan = active(account='Main', planned_weight_pct='5')
    preview = client.post('/api/ledger/proposals', json={'events': [buy('150')]}).json()
    later = plans._clock() + plans.PREVIEW_QUOTES_MAX_AGE + 1
    monkeypatch.setattr(plans, '_clock', lambda: later)
    client.post(f"/api/ledger/proposals/{preview['id']}/confirm")
    [link] = plans.links_for_plan(plan['id'])
    assert [deviation['code'] for deviation in link['deviations']] == ['weight_unknown']
    assert '超过' in link['deviations'][0]['message']


# -- the link is the check of the moment of confirmation ------------------------------

def crash(*args, **kwargs):
    raise RuntimeError('simulated crash while writing the links')


def test_a_repaired_link_keeps_the_check_made_when_the_trade_was_confirmed(client, ledger, monkeypatch):
    plan = active(account='Main', planned_quantity='150')
    preview = client.post('/api/ledger/proposals', json={'events': [buy('200')]}).json()
    assert [codes(item) for item in preview['plan_check']] == [['over_planned_quantity']]
    real = plans.record_links
    monkeypatch.setattr(plans, 'record_links', crash)      # the confirmation's link write is interrupted
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    assert link_rows() == []
    monkeypatch.setattr(plans, 'record_links', real)
    plans.update(plan['id'], {'planned_quantity': '250'})  # edited afterwards: the trade was booked against 150
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    [link] = plans.links_for_plan(plan['id'])
    assert [deviation['code'] for deviation in link['deviations']] == ['over_planned_quantity']
    assert link['deviations'][0]['data'] == {'quantity_after': '200', 'planned_quantity': '150'}
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    assert len(link_rows()) == 1


def test_a_plan_edited_before_the_confirmation_is_the_plan_the_link_uses(client, ledger):
    plan = active(account='Main', planned_quantity='150')
    preview = client.post('/api/ledger/proposals', json={'events': [buy('200')]}).json()
    plans.update(plan['id'], {'planned_quantity': '250'})  # changed between preview and confirmation
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    [link] = plans.links_for_plan(plan['id'])
    assert link['deviations'] == []


def test_a_reloaded_preview_is_checked_at_its_own_ledger_revision(client, ledger):
    active(account='Main', planned_quantity='150')
    preview = client.post('/api/ledger/proposals', json={'events': [buy()]}).json()
    book(ledger, buy())                                    # another 100 confirmed independently
    reread = client.get(f"/api/ledger/proposals/{preview['id']}").json()
    assert reread['revision'] == preview['revision'] < ledger.state()['revision']
    assert reread['after']['accounts']['Main']['positions'][CN]['quantity'] == '100'
    [item] = reread['plan_check']
    assert (item['relation'], codes(item)) == ('entry', []), 'the same ledger as its saved before and after'
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 409


def test_a_confirmation_in_another_portfolio_never_uses_this_portfolio_s_preview_quotes(client, ledger, market,
                                                                                        tmp_path, monkeypatch):
    import shutil
    plan = active(account='Main', planned_weight_pct='5')
    market.price(CN, '50')
    preview = client.post('/api/ledger/proposals', json={'events': [buy('150')]}).json()
    assert [codes(item) for item in preview['plan_check']] == [['over_planned_weight']]
    copy = tmp_path.parent / f'{tmp_path.name}-copy'
    shutil.copytree(tmp_path, copy)                        # portfolio B: the same pending proposal, no preview there
    monkeypatch.setenv('PORTFOLIO_DIR', str(copy))
    assert client.post(f"/api/ledger/proposals/{preview['id']}/confirm").status_code == 200
    [link] = plans.links_for_plan(plan['id'])
    [deviation] = link['deviations']
    assert (deviation['code'], deviation['level']) == ('weight_unknown', 'info')
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    assert link_rows() == [] and ledger.proposal(preview['id'])['receipt'] is None, 'portfolio A is untouched'
