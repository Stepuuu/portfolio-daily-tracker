"""The briefs' release-gate findings, one test per defect. Fictional data only; no network, no model.

Scripted quotes (``fake_quotes.json``) drive every clock. Tuesday 2026-11-17 is the trading day.
"""
import sqlite3
from dataclasses import replace
from datetime import date

from core import plans
from core.alerts import rules
from core.market.quotes import CLOSE_NOT_FINAL, CLOSED, build_quote
from core.routines import brief_inputs as inputs
from core.routines import postmarket, premarket
from tests.brief_support import build, by_label, facts, shanghai
from tests.fakes import opening, position, trade, build_ledger
from tests.plan_support import payload
from tests.test_briefs_review import A, B, CLOSE, TUESDAY, US1, home, portfolio, quotes, script  # noqa: F401


def levels_section(brief):
    return next(part for part in brief['sections'] if part['id'] == 'levels')


def pending_close(ticker, price, name):
    """A New York close seen before it settled (``close_pending``): closed, not final, an estimate."""
    quote = build_quote(ticker, 'fake', shanghai('2026-11-18 05:10'), price=price, prev_close='120', name=name,
                        currency='USD', quote_time=shanghai('2026-11-18 05:00'))
    return replace(quote, status=CLOSED, reason=CLOSE_NOT_FINAL.format(settled='16:30'), close_pending=True,
                   session_date=date(2026, 11, 17), calendar_confidence='calendar')


# ── 1. A count that rests on an unverified price says so ────────────────────────

def test_a_zero_that_rests_on_a_stale_price_says_it_could_not_be_checked(home):
    ledger = portfolio(home)
    rules.create({'ticker': A, 'kind': 'stop_loss', 'threshold': '10'})
    script(home, '2026-11-17T15:45:00+08:00', quotes(**{A: {'name': '示例一号', 'price': '10.50', 'prev_close': '10.6',
                                                           'quote_time': '2026-11-17T14:10:00+08:00'}}))
    brief, _ = build(postmarket, TUESDAY, home, ledger)
    [pnl] = [item for item in facts(brief, 'pnl') if (item['ref'] or {}).get('ticker') == A]
    assert pnl['status']['code'] == 'stale'                  # as Today marks it
    [count] = facts(brief, 'levels')
    assert count['value'] == 0 and (count['status'] or {}).get('estimated') is True, count
    assert '没有持仓越过' not in count['text']
    assert '已过期' in count['text'] and '未能核对' in count['text'] and '示例一号' in count['text'], count['text']
    assert brief['quality']['level'] != 'complete'


def test_a_verified_close_keeps_the_count_plain(home):
    ledger = portfolio(home)
    rules.create({'ticker': A, 'kind': 'stop_loss', 'threshold': '10'})
    script(home, '2026-11-17T15:45:00+08:00')
    [count] = facts(build(postmarket, TUESDAY, home, ledger)[0], 'levels')
    assert count['value'] == 0 and count['status'] is None and count['text'] == '没有持仓越过止损或目标'


def test_a_new_york_close_not_yet_final_is_not_a_verified_count(home):
    portfolio(home, positions=[position(US1, '10', '100', '示例美股')])
    rules.create({'ticker': US1, 'kind': 'stop_loss', 'threshold': '120'})
    levels = inputs.active_levels([US1])
    subjects = [(US1, '示例美股', 'Main')]
    section = postmarket._levels_at_close(object(), subjects, levels, None,
                                          {US1: pending_close(US1, '121', '示例美股')}, '2026-11-18T05:10:00+08:00')
    [count] = section['facts']
    assert count['value'] == 0 and (count['status'] or {}).get('estimated') is True and '未能核对' in count['text'], count


# ── 2. One holding crossing one level is one breach ─────────────────────────────

def test_one_holding_crossing_one_level_named_by_a_rule_and_a_plan_is_one_breach(home):
    ledger = portfolio(home)
    rule = rules.create({'ticker': A, 'kind': 'stop_loss', 'threshold': '10'})          # every account
    plan = plans.activate(plans.create(payload(A, account='Main', stop_price='10', planned_quantity='1000'))['id'])
    found = quotes(**{A: {'name': '示例一号', 'price': '9.80', 'prev_close': '10.2', 'quote_time': CLOSE}})
    script(home, '2026-11-17T15:45:00+08:00', found)
    brief, _ = build(postmarket, TUESDAY, home, ledger)
    count, *rows = facts(brief, 'levels')
    assert count['value'] == 1 and count['status'] is None, count
    [row] = rows
    assert f'计划 #{plan["id"]}' in row['text'] and f'提醒 #{rule["id"]}' in row['text'], row['text']
    script(home, '2026-11-17T08:45:00+08:00', found)
    rows = [item for item in facts(build(premarket, TUESDAY, home, ledger)[0], 'levels') if '止损 10' in item['label']]
    assert len(rows) == 1, rows


def test_a_close_not_yet_final_that_crosses_one_level_is_one_estimated_breach(home):
    portfolio(home, positions=[position(US1, '10', '100', '示例美股')])
    rule = rules.create({'ticker': US1, 'kind': 'stop_loss', 'threshold': '120'})
    plan = plans.activate(plans.create(payload(US1, account='Main', stop_price='120', planned_quantity='10'))['id'])
    levels = inputs.active_levels([US1])
    section = postmarket._levels_at_close(object(), [(US1, '示例美股', 'Main')], levels, None,
                                          {US1: pending_close(US1, '119', '示例美股')}, '2026-11-18T05:10:00+08:00')
    count, *rows = section['facts']
    assert count['value'] == 1 and count['status']['estimated'] is True, count
    [row] = rows
    assert row['status']['estimated'] is True and '尚未最终确定' in row['text']
    assert f'计划 #{plan["id"]}' in row['text'] and f'提醒 #{rule["id"]}' in row['text'], row['text']


# ── 3. Quantities as the ledger holds them ─────────────────────────────────────

def test_a_fractional_quantity_is_stored_as_the_ledger_holds_it(home):
    ledger = build_ledger(home, [opening('Main', cash={'CNY': '1000', 'USD': '1000'},
                                         positions=[position(US1, '10.000000001', '100', '示例美股')],
                                         date='2026-11-02', contributed_cny='8000')])
    plans.activate(plans.create(payload(US1, account='Main', stop_price='100', planned_quantity='0.1'))['id'])
    proposal = ledger.propose([trade('buy', US1, '0.12345678', '185', currency='USD', account='Main', date=TUESDAY)])
    ledger.confirm(proposal['id'])
    plans.record_links(ledger.proposal(proposal['id']), ledger=ledger)
    script(home, '2026-11-17T23:00:00+08:00', quotes(**{US1: {
        'name': '示例美股', 'price': '186', 'prev_close': '185', 'quote_time': '2026-11-17T22:59:30+08:00'}}))
    brief, _ = build(postmarket, TUESDAY, home, ledger)
    [bought] = [item for item in facts(brief, 'trades') if item['metric'] == 'trade_amount']
    assert bought['figures']['quantity'] == '0.12345678' and '0.12345678 股' in bought['text'], bought
    [pnl] = [item for item in facts(brief, 'pnl') if (item['ref'] or {}).get('ticker') == US1]
    assert pnl['figures']['quantity'] == '10.123456781', pnl['figures']
    shown = [item['text'] for part in brief['sections'] for item in part['facts']]
    shown += [entry.get('text') or '' for part in brief['sections'] for item in part['facts']
              for entry in item.get('items') or []]
    assert not any('0.123457' in text for text in shown), shown


# ── 4. Pending ledger previews: what the ledger's list shows at that instant ────

def test_a_manual_preview_that_expired_overnight_is_not_waiting(home, monkeypatch):
    import backend.api.ledger as ledger_api
    ledger = portfolio(home)
    path = home / 'ledger.sqlite3'
    manual = ledger.propose([trade('buy', B, '100', '11', account='Main', date='2026-11-16')])
    ledger_api.mark_transient(ledger, manual['id'])
    with sqlite3.connect(path) as db:        # made Monday 22:10, so it expired at 00:10 Tuesday
        db.execute('UPDATE proposals SET created_at=? WHERE id=?', ('2026-11-16T14:10:00+00:00', manual['id']))
        db.execute('UPDATE proposal_expiry SET expires_at=? WHERE proposal_id=?',
                   ('2026-11-16T16:10:00+00:00', manual['id']))

    def listed_by_the_ledger(now):
        monkeypatch.setattr(ledger_api, 'utc_now', lambda: now)
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            return len(ledger_api._pending(db, ledger_api._tables(db)))

    script(home, '2026-11-17T08:45:00+08:00')
    pending = by_label(build(premarket, TUESDAY, home, ledger)[0], 'pending')['待确认账本记录']
    assert listed_by_the_ledger(shanghai('2026-11-17 08:45')) == 0
    assert pending['value'] == 0 and pending['text'] == '没有', pending

    # A manual preview made at 07:30 is still open at 08:45; a genuine preview has no lifetime.
    early = ledger.propose([trade('buy', B, '100', '11.1', account='Main', date='2026-11-16')])
    ledger_api.mark_transient(ledger, early['id'])
    genuine = ledger.propose([{'kind': 'deposit', 'date': '2026-11-16', 'account': 'Main', 'currency': 'CNY',
                               'amount': '100'}])
    with sqlite3.connect(path) as db:
        db.execute('UPDATE proposals SET created_at=? WHERE id=?', ('2026-11-16T23:30:00+00:00', early['id']))
        db.execute('UPDATE proposal_expiry SET expires_at=? WHERE proposal_id=?',
                   ('2026-11-17T01:30:00+00:00', early['id']))
        db.execute('UPDATE proposals SET created_at=? WHERE id=?', ('2026-11-16T12:00:00+00:00', genuine['id']))
    pending = by_label(build(premarket, TUESDAY, home, ledger)[0], 'pending')['待确认账本记录']
    assert listed_by_the_ledger(shanghai('2026-11-17 08:45')) == 2
    assert pending['value'] == 2, pending


# ── 5. The pre-market note says what the prices are ────────────────────────────

def test_a_catch_up_premarket_note_matches_its_intraday_rows(home):
    ledger = portfolio(home, positions=[position(A, '1000', '10', '示例一号'), position(US1, '10', '100', '示例美股')])
    rules.create({'ticker': A, 'kind': 'stop_loss', 'threshold': '9'})
    rules.create({'ticker': US1, 'kind': 'stop_loss', 'threshold': '100'})
    script(home, '2026-11-17T08:45:00+08:00', quotes(**{A: {'name': '示例一号', 'price': '10.5', 'prev_close': '10.4',
                                                           'quote_time': '2026-11-16T15:00:00+08:00'}}))
    note = levels_section(build(premarket, TUESDAY, home, ledger)[0])['note']
    assert '上一交易时段的收盘价' in note and '盘中' not in note

    only_a = portfolio(home / 'cn', positions=[position(A, '1000', '10', '示例一号')])
    live = quotes(**{A: {'name': '示例一号', 'price': '10.6', 'prev_close': '10.5',
                         'quote_time': '2026-11-17T09:59:50+08:00'}})
    script(home, '2026-11-17T10:00:00+08:00', live)
    section = levels_section(build(premarket, TUESDAY, home, only_a)[0])
    [row] = [item for item in section['facts'] if '止损 9' in item['label']]
    assert '盘中价' in row['text']
    assert '上一交易时段的收盘价' not in section['note'] and '盘中价' in section['note'], section['note']

    section = levels_section(build(premarket, TUESDAY, home, ledger)[0])     # A trading, New York closed
    texts = {item['ref']['ticker']: item['text'] for item in section['facts'] if item['metric'] == 'distance_pct'}
    assert '盘中价' in texts[A] and '收盘价' in texts[US1], texts
    assert '盘中价' in section['note'] and '收盘价' in section['note'], section['note']
