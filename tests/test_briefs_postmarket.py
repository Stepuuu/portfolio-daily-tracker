"""The post-market brief: trades, the plan check, profit attribution, alerts and levels, all recomputed.

The acceptance case of M5/M6: a buy that breaks its plan appears in the post-market brief with the right
deviation. Scripted quotes pin the clock to Tuesday 2026-11-17 15:45 (A-shares closed, Hong Kong still
trading, New York's last session Monday's). Fictional data only.
"""
import asyncio
import copy
import json
from datetime import datetime, time
from decimal import Decimal

import pytest

from core import desk_store, lessons, plans
from core.alerts import rules
from core.market.calendar import SHANGHAI
from core.market.runtime import set_market_runtime
from core.routines import postmarket, scheduler
from core.routines.scheduler import Routine, Scheduler
from tests.brief_support import Context, build, by_label, facts, fire, shanghai, walk, write_script
from tests.fakes import FakeClock, build_ledger, opening, position, trade
from tests.plan_support import at, payload, pin

TUESDAY = '2026-11-17'
NOW = '2026-11-17T15:45:00+08:00'
A, B, C = 'SHA:600001', 'SHA:600002', 'SHA:600003'
HK1, US1 = 'HKG:00001', 'NASDAQ:EXMP'
SESSIONS = {A: TUESDAY, B: TUESDAY, C: TUESDAY, HK1: TUESDAY, US1: '2026-11-16'}


def post_quotes(**changes):
    quotes = {
        A: {'name': '示例一号', 'price': '10.6', 'prev_close': '10.5', 'quote_time': '2026-11-17T15:00:00+08:00'},
        B: {'name': '示例二号', 'price': '11.2', 'prev_close': '10.9', 'quote_time': '2026-11-17T15:00:00+08:00'},
        C: {'name': '示例三号', 'price': '19.5', 'prev_close': '20.2', 'quote_time': '2026-11-17T15:00:00+08:00'},
        HK1: {'name': '示例港股', 'price': '52.5', 'prev_close': '52', 'quote_time': '2026-11-17T15:44:50+08:00'},
        US1: {'name': '示例美股', 'price': '121', 'prev_close': '120', 'quote_time': '2026-11-17T05:00:00+08:00'},
    }
    quotes.update(changes)
    return {ticker: entry for ticker, entry in quotes.items() if entry is not None}


def book(directory, ledger, events, link=True):
    """Preview and confirm; with ``link`` also store the plan links, as the ledger API does."""
    proposal = ledger.propose(list(events))
    ledger.confirm(proposal['id'])
    if link:
        # As the ledger API's confirm: no quotes are requested (fix/plans-review); these plans have no planned weight.
        plans.record_links(ledger.proposal(proposal['id']), ledger=ledger)
    return proposal['events']


@pytest.fixture
def day(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    for name in ('TRACKER_DEMO_MODE', 'TRACKER_QUOTE_SOURCE', 'TRACKER_POSTMARKET', 'TRACKER_POSTMARKET_TIME'):
        monkeypatch.delenv(name, raising=False)
    pin(monkeypatch, at('2026-11-10'))
    write_script(tmp_path, NOW, post_quotes())
    set_market_runtime(None)
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '200000', 'HKD': '5000', 'USD': '1000'}, positions=[
        position(A, '1000', '10', '示例一号'), position(HK1, '200', '50', '示例港股'), position(US1, '10', '100', '示例美股')],
        date='2026-11-02', contributed_cny='230000')])
    own = plans.activate(plans.create(payload(A, entry_low='10', entry_high='11', stop_price='9.8', target_price='12',
                                              planned_quantity='1500'))['id'])
    broken = plans.activate(plans.create(payload(B, entry_low='9.5', entry_high='10.5', stop_price='9', target_price='12',
                                                 planned_quantity='1000'))['id'])
    rules.create({'ticker': C, 'kind': 'target_price', 'threshold': '19'})
    traded = {}
    [traded['followed']] = book(tmp_path, ledger, [trade('buy', A, '200', '10.4', fee='5', account='Main', date=TUESDAY)])
    [traded['broken']] = book(tmp_path, ledger, [trade('buy', B, '1500', '11', fee='5', account='Main', date=TUESDAY)])
    [traded['unplanned']] = book(tmp_path, ledger, [trade('buy', C, '100', '20', fee='5', account='Main', date=TUESDAY)])
    # Confirmed before plans were in use: no link, so it was never checked.
    [traded['unchecked']] = book(tmp_path, ledger, [trade('sell', HK1, '50', '51.5', currency='HKD', fee='2',
                                                          account='Main', date=TUESDAY)], link=False)
    book(tmp_path, ledger, [{'kind': 'dividend', 'date': TUESDAY, 'account': 'Main', 'currency': 'CNY',
                             'amount': '50', 'ticker': A}])
    fire(A, 'day_change_pct', '-3', '-3.2', TUESDAY)
    with desk_store.pinned_now(shanghai('2026-11-17 14:00')):
        lessons.propose({'description': '示例教训：没有按计划仓位买入', 'type': 'failure'})
    yield tmp_path, ledger, own, broken, traded
    set_market_runtime(None)


def context(directory):
    return Context(directory=directory, trading_date=TUESDAY, now=shanghai('2026-11-17 15:45'), sessions=SESSIONS)


def test_every_number_of_the_post_market_brief_is_recomputed_from_the_sources(day):
    directory, ledger, *_ = day
    brief, blockers = build(postmarket, TUESDAY, directory, ledger)
    assert blockers == []
    assert [part['id'] for part in brief['sections']] == ['trades', 'plan_check', 'pnl', 'alerts', 'levels', 'lessons']
    counts = walk(brief, context(directory))
    assert counts == {'trade_count': 1, 'trade_amount': 4, 'other_event_count': 1, 'deviation_trade_count': 1,
                      'deviation_count': 3, 'followed_count': 1, 'unchecked_count': 1, 'day_pnl_total': 1,
                      'day_pnl_cny': 5, 'alert_count': 1, 'alert_value': 1, 'levels_reached_count': 1,
                      'distance_pct': 1, 'lesson_count': 1}
    pnl = {(item['ref'] or {}).get('ticker', 'total'): item for item in facts(brief, 'pnl')}
    assert {ticker: item['value'] for ticker, item in pnl.items()} == {
        'total': '489.68', A: '135.00', B: '295.00', C: '-55.00', HK1: '43.68', US1: '71.00'}
    assert pnl[US1]['figures']['day_pnl'] == '10.00' and pnl[HK1]['figures']['day_pnl'] == '48.00'
    assert pnl[A]['text'] == '+¥135.00（A股 11-17 收盘）' and pnl['total']['signed'] is True
    trades = by_label(brief, 'trades')
    assert trades['今日成交']['text'] == '4 笔（买入 3，卖出 1）'
    assert trades['买入 示例二号']['value'] == '16500.00'
    assert trades['买入 示例二号']['text'] == '1500 股 × 11.00 = ¥16,500.00，费用 ¥5.00'


def test_a_buy_that_breaks_its_plan_appears_with_the_right_deviation(day):
    directory, ledger, own, broken, traded = day
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    check = by_label(brief, 'plan_check')
    summary = check['偏离计划的成交']
    assert summary['value'] == 2 and summary['level'] == 'warning'
    assert summary['text'] == '成交 4 笔：偏离计划 2 笔，按计划 1 笔，未核对 1 笔'
    outside = check['超出计划入场区间']
    assert outside['value'] == 1 and outside['level'] == 'warning'
    [item] = outside['items']
    assert item['text'] == '买入 示例二号 1500 股 @ 11.00：买入价 11 高于计划入场区间 9.5–10.5。'
    assert item['ref'] == {'plan_id': broken['id'], 'ledger_event_id': traded['broken']['id'], 'ticker': B}
    [quantity] = check['超过计划股数']['items']
    assert quantity['text'].endswith('买入后持有 1500 股（全部账户），超过计划的 1000 股。')
    [unplanned] = check['没有交易计划']['items']
    assert unplanned['ref']['ledger_event_id'] == traded['unplanned']['id'] and unplanned['ref'].get('plan_id') is None
    [followed] = check['按计划执行']['items']
    assert followed['text'] == f'买入 示例一号 200 股 @ 10.40：加仓，计划 #{own["id"]}'
    [unchecked] = check['未核对']['items']
    assert unchecked['ref'] == {'ledger_event_id': traded['unchecked']['id'], 'ticker': HK1}


def test_each_market_says_which_session_its_numbers_are(day):
    directory, ledger, *_ = day
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    pnl = {item['ref']['ticker']: item for item in facts(brief, 'pnl') if item['ref']}
    # Words true at 15:45: New York's last session closed on Monday; Hong Kong is still trading.
    assert pnl[US1]['text'] == '+$10.00 ≈ +¥71.00（纽约 11-16 收盘；汇率 7.1，11-17 15:44）'
    assert pnl[HK1]['text'] == '+HK$48.00 ≈ +¥43.68（香港 11-17 盘中价 15:44，非收盘价，16:00 收盘；汇率 0.91，11-17 15:44）'
    assert pnl[US1]['status'] == {'code': 'closed', 'reason': None, 'estimated': False}
    assert pnl[HK1]['status'] == {'code': 'live', 'reason': '香港 11-17 盘中价 15:44，非收盘价，16:00 收盘，收盘后会变',
                                  'estimated': False, 'provisional': True}
    assert pnl[US1]['fx'] == {'currency': 'USD', 'rate': '7.1', 'source': 'fake', 'as_of': '2026-11-17T15:44:55+08:00',
                              'status': 'live', 'reason': None, 'estimated': False}
    note = next(part for part in brief['sections'] if part['id'] == 'pnl')['note']
    assert '不含分红、单独费用、汇率变动和基金估值' in note
    assert '港股在生成时（北京时间 15:45）仍在交易' in note and '美股按纽约 11-16 的收盘价计算' in note
    total = by_label(brief, 'pnl')['今日盈亏合计']
    assert total['status'] == {'code': 'provisional', 'reason': '含港股盘中价（非收盘价）', 'estimated': False,
                               'provisional': True}


def test_a_new_york_close_that_has_not_settled_is_not_called_the_close(day):
    # Monday's brief written 12 minutes after New York's Monday close (16:00 there is 05:00 on Tuesday in
    # Beijing): the official closing prices come out minutes after the bell, so the row is not the close yet.
    directory, ledger, *_ = day
    write_script(directory, '2026-11-17T05:12:00+08:00', post_quotes(**{
        A: {'name': '示例一号', 'price': '10.5', 'prev_close': '10.4', 'quote_time': '2026-11-16T15:00:00+08:00'},
        B: None, C: None,
        HK1: {'name': '示例港股', 'price': '52', 'prev_close': '51.5', 'quote_time': '2026-11-16T16:08:03+08:00'}}))
    brief, _ = build(postmarket, '2026-11-16', directory, ledger)
    pnl = {item['ref']['ticker']: item for item in facts(brief, 'pnl') if item['ref']}
    assert '纽约 11-16 收盘价尚未最终确定' in pnl[US1]['text'], pnl[US1]['text']
    status = pnl[US1]['status']
    assert status['code'] == 'closed' and status['estimated'] is True and '尚未最终确定' in status['reason']
    note = next(part for part in brief['sections'] if part['id'] == 'pnl')['note']
    assert '美股按纽约' not in note and '美股的收盘价在生成时尚未最终确定' in note, note
    assert by_label(brief, 'pnl')['今日盈亏合计']['status']['estimated'] is True


def test_a_price_phrase_does_not_call_an_unsettled_new_york_close_the_close():
    from dataclasses import replace
    from datetime import date
    from core.routines.brief_inputs import price_phrase
    from tests.fakes import quote
    settled = replace(quote(US1, '121', at=shanghai('2026-11-17 05:00')), status='closed', session_date=date(2026, 11, 16))
    assert price_phrase(settled, 'US') == '纽约11-16 收盘价'
    assert price_phrase(replace(settled, close_pending=True), 'US') == '纽约11-16 收盘价（尚未最终确定）'


def test_a_missing_rate_is_null_with_the_reason_never_zero(day):
    directory, ledger, *_ = day
    write_script(directory, NOW, post_quotes(), fx={'HKD': {'rate': '0.91', 'age_seconds': 5}})
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    pnl = {(item['ref'] or {}).get('ticker', 'total'): item for item in facts(brief, 'pnl')}
    assert pnl[US1]['value'] is None and pnl[US1]['status']['code'] == 'unavailable'
    assert pnl[US1]['text'] == '+$10.00；人民币金额不可用：缺少 USD 汇率（fake：fake_quotes.json 中没有该币种）'
    assert pnl['total']['value'] is None and pnl['total']['text'].startswith('不可用：')
    assert pnl[A]['value'] == '135.00'
    assert brief['quality']['level'] == 'incomplete'
    walk(brief, context(directory))


def test_a_stale_quote_is_marked_estimated(day):
    directory, ledger, *_ = day
    write_script(directory, NOW, post_quotes(**{A: {'name': '示例一号', 'price': '10.6', 'prev_close': '10.5',
                                                  'quote_time': '2026-11-17T14:10:00+08:00'}}))
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    row = next(item for item in facts(brief, 'pnl') if (item['ref'] or {}).get('ticker') == A)
    assert row['status']['code'] == 'stale' and row['status']['estimated'] is True and row['status']['reason']


def test_levels_at_the_close_alerts_today_and_lessons(day):
    directory, ledger, *_ = day
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    levels = facts(brief, 'levels')
    assert levels[0]['value'] == 1 and levels[0]['level'] == 'warning'
    assert levels[1]['label'] == '示例三号 目标 19.00' and levels[1]['value'] == '-2.56'
    alerts = facts(brief, 'alerts')
    assert alerts[0]['value'] == 1 and alerts[1]['unit'] == 'pct' and alerts[1]['value'] == '-3.2'
    assert by_label(brief, 'lessons')['待确认教训']['value'] == 1


def test_the_close_snapshot_s_state_is_mentioned_but_never_required(day):
    directory, ledger, *_ = day
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    assert brief['notices'][0] == {'kind': 'close_snapshot', 'text': '收盘快照：今天没有运行记录'}

    def due(clock):
        return datetime.combine(clock.local_date(), time(15, 40), tzinfo=SHANGHAI)

    async def snapshot(trading_date):
        return scheduler.failed('部分持仓没有取得有效价格，未生成快照（不会按 0 计算）', 'missing_price')

    clock = FakeClock('2026-11-17 15:40')
    runner = Scheduler([Routine('close_snapshot', due, snapshot, '收盘快照')], now=clock)

    async def go():
        await runner.tick()
        await runner.settle()
    asyncio.run(go())
    brief, blockers = build(postmarket, TUESDAY, directory, ledger)
    assert brief['notices'][0]['text'] == '收盘快照：失败（部分持仓没有取得有效价格，未生成快照（不会按 0 计算））'
    assert brief['notices'][0]['status'] == 'failed' and blockers == []


def test_a_day_without_trades(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    write_script(tmp_path, NOW, post_quotes())
    ledger = build_ledger(tmp_path, [opening('Main', cash={'CNY': '1000'}, positions=[position(A, '100', '10', '示例一号')],
                                             date='2026-11-02')])
    brief, blockers = build(postmarket, TUESDAY, tmp_path, ledger)
    assert blockers == [] and by_label(brief, 'trades')['今日成交']['text'] == '今日没有已确认的成交'
    assert by_label(brief, 'plan_check')['计划核对']['text'] == '今日没有成交，无需核对'
    assert not (tmp_path / 'desk.sqlite3').exists()
    walk(brief, Context(directory=tmp_path, trading_date=TUESDAY, now=shanghai('2026-11-17 15:45'), sessions=SESSIONS))
    assert json.dumps(brief, ensure_ascii=False).count('"value": null') == 0


def test_the_walker_catches_a_position_left_out_with_its_share_and_a_fact_without_its_source(day):
    directory, ledger, *_ = day
    brief, _ = build(postmarket, TUESDAY, directory, ledger)
    walk(copy.deepcopy(brief), context(directory))
    omitted = copy.deepcopy(brief)
    part = next(part for part in omitted['sections'] if part['id'] == 'pnl')
    row = next(item for item in part['facts'] if (item['ref'] or {}).get('ticker') == US1)
    part['facts'].remove(row)
    total = part['facts'][0]
    total['value'] = str(Decimal(total['value']) - Decimal(row['value']))
    total.get('figures', {}).pop('day_pnl_pct', None)
    with pytest.raises(AssertionError):
        walk(omitted, context(directory))
    unsourced = copy.deepcopy(brief)
    next(item for item in facts(unsourced, 'pnl') if (item['ref'] or {}).get('ticker') == A)['source'] = None
    with pytest.raises(AssertionError):
        walk(unsourced, context(directory))
