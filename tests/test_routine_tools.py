"""Agent tools of the routines package: the stored brief, the ledger's trades and the journal. Read-only.

Fictional data only.
"""
import asyncio
import json
import sqlite3

import pytest

from core import agent_tools
from core.market.runtime import set_market_runtime
from core.routines import briefs, premarket
from tests.brief_support import build, runtime_for, shanghai, write_script
from tests.fakes import book, build_ledger, opening, position, trade

TUESDAY = '2026-11-17'
A, B = 'SHA:600001', 'SHA:600002'


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_DIR', str(tmp_path))
    monkeypatch.delenv('TRACKER_DEMO_MODE', raising=False)
    write_script(tmp_path, '2026-11-17T16:00:00+08:00', {
        A: {'name': '示例一号', 'price': '10.5', 'prev_close': '10.2', 'quote_time': '2026-11-17T15:00:00+08:00'}})
    set_market_runtime(runtime_for(tmp_path))
    built = {tool.name: tool for tool in agent_tools.build_all()}
    yield tmp_path, built
    set_market_runtime(None)


def call(tool, **arguments):
    return asyncio.run(tool.function(**arguments))


def test_the_three_tools_are_registered_and_read_only(tools):
    _, built = tools
    assert {'get_routine_report', 'get_trades', 'get_journal'} <= set(built)
    for name in ('get_routine_report', 'get_trades', 'get_journal'):
        assert '只读' in built[name].description


def test_get_routine_report_returns_the_stored_brief_or_says_it_is_missing(tools):
    directory, built = tools
    missing = call(built['get_routine_report'], kind='premarket')
    assert missing['brief'] is None and missing['trading_date'] == TUESDAY and missing['notes']
    assert not (directory / 'desk.sqlite3').exists()
    ledger = build_ledger(directory, [opening('Main', cash={'CNY': '1000'}, positions=[position(A, '100', '10', '示例一号')],
                                              date='2026-11-02')])
    brief, _ = build(premarket, TUESDAY, directory, ledger)
    briefs.save(brief)
    report = call(built['get_routine_report'], kind='premarket', date=TUESDAY)
    assert report['generated_at'] == brief['generated_at'] and report['quality'] == brief['quality']
    assert [part['id'] for part in report['sections']] == [part['id'] for part in brief['sections']]
    assert report['source'].startswith('盘前/盘后简报') and 'null 表示不可用' in report['note']
    assert len(json.dumps(report, ensure_ascii=False)) <= 14000
    assert call(built['get_routine_report'], kind='weekly')['error'].startswith('kind 只能是')
    assert call(built['get_routine_report'], kind='premarket', date='2026-02-30')['error'] == '日期不存在。'


def test_get_trades_reads_confirmed_buys_and_sells_in_a_range(tools):
    directory, built = tools
    ledger = build_ledger(directory, [opening('Main', cash={'CNY': '100000'}, date='2026-11-02')])
    book(ledger, [trade('buy', A, '100', '10', account='Main', date='2026-11-10'),
                  trade('buy', B, '50', '20', account='Main', date='2026-11-16')])
    receipt = book(ledger, [trade('sell', A, '40', '11', account='Main', date=TUESDAY)])
    sold = next(event for event in ledger.history() if event['kind'] == 'sell')
    book(ledger, [{'kind': 'reverse', 'date': TUESDAY, 'target_id': sold['id']}])
    result = call(built['get_trades'], date_from='2026-11-01', date_to=TUESDAY)
    assert [(item['date'], item['kind'], item['ticker'], item['quantity']) for item in result['items']] == [
        ('2026-11-10', 'buy', A, '100'), ('2026-11-16', 'buy', B, '50')]
    assert result['ledger_revision'] == receipt['revision'] + 1 and result['count'] == 2
    only = call(built['get_trades'], date_from='2026-11-01', date_to=TUESDAY, ticker='sha:600002')
    assert [item['ticker'] for item in only['items']] == [B]
    assert call(built['get_trades'], date_from='2026-11-17', date_to='2026-11-01')['error'] == 'date_to 不能早于 date_from'
    assert call(built['get_trades'], date_from='2025-01-01', date_to=TUESDAY)['error'] == '日期范围最多 366 天'
    assert '代码格式不正确' in call(built['get_trades'], date_from='2026-11-01', date_to=TUESDAY, ticker='600001')['error']


def test_get_journal_reads_the_ledger_file_without_creating_anything(tools):
    directory, built = tools
    empty = call(built['get_journal'])
    assert empty['items'] == [] and not (directory / 'ledger.sqlite3').exists()
    build_ledger(directory, [opening('Main', cash={'CNY': '1000'}, date='2026-11-02')])
    with sqlite3.connect(directory / 'ledger.sqlite3') as db:
        db.execute('CREATE TABLE journal (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
        for entry_id, ticker, review_on, created in (('j1', A, '2026-11-20', '2026-11-01'), ('j2', B, '2026-12-20', '2026-11-05')):
            db.execute('INSERT INTO journal VALUES (?,?,?)', (entry_id, created, json.dumps(
                {'ticker': ticker, 'thesis': '示例论点', 'invalidation': '示例条件', 'review_on': review_on}, ensure_ascii=False)))
    result = call(built['get_journal'])
    assert [item['id'] for item in result['items']] == ['j2', 'j1']
    assert [item['id'] for item in call(built['get_journal'], due_by='2026-11-30')['items']] == ['j1']
    assert [item['id'] for item in call(built['get_journal'], ticker=B)['items']] == ['j2']
    assert call(built['get_journal'], limit=0)['error'] == 'limit 参数必须不小于 1'
