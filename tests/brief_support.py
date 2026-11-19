"""Shared helpers for the brief tests: a scripted portfolio and the provenance walker. Fictional data only.

``walk(brief, ctx)`` recomputes every numeric fact of a brief independently:
prices and rates from ``fake_quotes.json`` as written, positions and trades
from the ledger's own events (``core.ledger.replay`` for cash), plans, rules,
alerts, notifications and proposals by plain SQL on ``desk.sqlite3``. It does
not call the Today valuation, the alert view or the brief builders, so a
number the builders get wrong is caught here. A fact whose value is null must
say why. Every numeric fact must be covered by a check (an unknown metric
fails), and its ``source`` (and, for a quote, its time) must be the expected
one. Totals are checked against what the ledger says should be in them (every
position held or traded in its session, full exits included), never against
the brief's own rows, so a row left out together with its share of the total
is caught too.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Optional

from core import desk_store, notify
from core.alerts import events as alert_events, rules
from core.ledger import replay
from core.market.calendar import SHANGHAI, SessionClock
from core.market.quotes import QuoteService
from core.market.runtime import MarketRuntime
from core.market.sources_fake import FakeQuoteSource

CENT = Decimal('0.01')
NUMERIC = {'CNY', 'HKD', 'USD', 'pct', 'count'}


def shanghai(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=SHANGHAI)


def runtime_for(directory) -> MarketRuntime:
    """The file-driven fake source as a runtime; its clock follows the script's ``now``."""
    source = FakeQuoteSource(Path(directory))
    clock = SessionClock(source.now)
    return MarketRuntime(QuoteService([source], clock, ttl_seconds=0, fx_ttl_seconds=0), clock, 'fake')


def write_script(directory, now: str, quotes: Dict[str, dict], fx: Optional[Dict[str, dict]] = None) -> None:
    script = {'now': now, 'quotes': quotes, 'fx': fx if fx is not None else {
        'USD': {'rate': '7.1', 'age_seconds': 5}, 'HKD': {'rate': '0.91', 'age_seconds': 5}}}
    (Path(directory) / 'fake_quotes.json').write_text(json.dumps(script, ensure_ascii=False), encoding='utf-8')


def build(module, trading_date: str, directory, ledger, **options):
    return asyncio.run(module.build(trading_date, runtime=runtime_for(directory), ledger=ledger, **options))


def fire(ticker, kind, threshold, value, day, read=False, n=1):
    """A fired alert as the monitor records it: the log row and its notification."""
    rules.ensure()
    seq = notify.publish('alert', 'warning', f'{ticker} 提醒', f'示例：{ticker} 触发 {threshold}',
                         dedupe_key=f'test-alert:{ticker}:{day}:{n}')
    with desk_store.transaction() as db:
        row_id = alert_events.record(db, {
            'rule_id': 900 + n, 'ticker': ticker, 'name': None, 'account': None, 'kind': kind, 'threshold': threshold,
            'trading_date': day, 'n': n, 'value': value, 'price': value, 'prev_close': None, 'quote_source': 'fake',
            'quote_time': f'{day}T10:05:00+08:00', 'quote_status': 'live', 'fired_at': f'{day}T10:05:01+08:00',
            'fired_ts': shanghai(f'{day} 10:05:01').timestamp(), 'delivery': 'notified', 'notification_seq': seq,
            'title': f'{ticker} 提醒', 'body': f'示例：{ticker} 触发 {threshold}，现价 {value}'})
    if read:
        notify.mark_read(seq)
    return row_id


def journal(directory, entry_id, ticker, review_on):
    with sqlite3.connect(directory / 'ledger.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS journal (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO journal VALUES (?,?,?)', (entry_id, '2026-11-01T02:00:00+00:00', json.dumps(
            {'ticker': ticker, 'thesis': '示例研究：港股估值', 'invalidation': '示例条件', 'review_on': review_on},
            ensure_ascii=False)))


def r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def facts(brief, section_id: Optional[str] = None) -> List[dict]:
    return [item for part in brief['sections'] if section_id in (None, part['id']) for item in part['facts']]


def by_label(brief, section_id: str) -> Dict[str, dict]:
    return {item['label']: item for item in facts(brief, section_id)}


@dataclass
class Context:
    """What the walker needs to know besides the files."""
    directory: Path
    trading_date: str
    now: datetime
    # ticker -> the session (YYYY-MM-DD) its quote belongs to, for day profit
    sessions: Dict[str, str] = field(default_factory=dict)
    # ('notice' | 'news', ticker) -> the number of items the fake provider returns since the previous close
    news: Dict[tuple, int] = field(default_factory=dict)
    brief: dict = field(default_factory=dict)


def _script(ctx: Context) -> dict:
    return json.loads((ctx.directory / 'fake_quotes.json').read_text(encoding='utf-8'))


def _desk(ctx: Context):
    db = sqlite3.connect(ctx.directory / 'desk.sqlite3')
    db.row_factory = sqlite3.Row
    return db


def _table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _all_events(ctx: Context) -> List[dict]:
    path = ctx.directory / 'ledger.sqlite3'
    if not path.is_file():
        return []
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        if not _table(db, 'events'):
            return []
        return [{**json.loads(row[0]), '_sequence': row[1]}
                for row in db.execute('SELECT payload, sequence FROM events ORDER BY sequence')]


def _events(ctx: Context) -> List[dict]:
    """The events in force on the trading day: nothing dated later; a reversal dated on or before the day drops
    its target; when that dated view does not replay (a back-dated correction), later reversals apply too."""
    events = _all_events(ctx)
    dated = [event for event in events if event['date'] <= ctx.trading_date]
    reversed_ids = {event.get('target_id') for event in dated if event['kind'] == 'reverse'}
    kept = [event for event in dated if event['kind'] != 'reverse' and event.get('id') not in reversed_ids]
    try:
        replay(kept)
    except Exception:
        later = {event.get('target_id') for event in events if event['kind'] == 'reverse'}
        kept = [event for event in kept if event.get('id') not in later]
    return kept


def _revision(ctx: Context) -> int:
    with sqlite3.connect(f'file:{ctx.directory / "ledger.sqlite3"}?mode=ro', uri=True) as db:
        return db.execute('SELECT revision FROM ledger_meta').fetchone()[0]


def _price(ctx: Context, ticker: str):
    entry = _script(ctx)['quotes'][ticker]
    return Decimal(entry['price']), Decimal(entry['prev_close'])


def _quote_time(ctx: Context, ticker: str) -> Optional[str]:
    """The exchange time of the scripted quote as the brief states it (Shanghai, seconds)."""
    from datetime import timedelta
    entry = _script(ctx)['quotes'][ticker]
    if 'quote_time' in entry:
        moment = datetime.fromisoformat(entry['quote_time'])
    elif 'age_seconds' in entry:
        # Relative to the scripted clock, or to the brief's own moment when the script pins none (the demo).
        base = datetime.fromisoformat(_script(ctx)['now']) if _script(ctx).get('now') else ctx.now
        moment = base - timedelta(seconds=float(entry['age_seconds']))
    else:
        return None
    return moment.astimezone(SHANGHAI).isoformat(timespec='seconds')


def _quote_source(item, ctx: Context, ticker: str) -> None:
    """A quote fact names the scripted source and the quote's own exchange time."""
    assert item['source'] == 'fake', item
    expected = _quote_time(ctx, ticker)
    if expected is not None:
        assert item['as_of'] == expected, (item, expected)


def _rate(ctx: Context, currency: str) -> Decimal:
    return Decimal(1) if currency == 'CNY' else Decimal(_script(ctx)['fx'][currency]['rate'])


def _currency(ticker: str) -> str:
    return {'SHA': 'CNY', 'SHE': 'CNY', 'HKG': 'HKD', 'NASDAQ': 'USD', 'NYSE': 'USD'}[ticker.split(':')[0]]


def _quantity(events: List[dict], account: str, ticker: str, before: Optional[str] = None,
              upto: Optional[str] = None) -> Decimal:
    held = Decimal(0)
    for event in events:
        if before is not None and event['date'] >= before:
            continue
        if upto is not None and event['date'] > upto:
            continue
        if event.get('account') != account:
            continue
        if event['kind'] == 'opening':
            for position in event.get('positions') or []:
                if position['ticker'] == ticker:
                    held = Decimal(position['quantity'])
        elif event.get('ticker') == ticker and event['kind'] == 'buy':
            held += Decimal(event['quantity'])
        elif event.get('ticker') == ticker and event['kind'] == 'sell':
            held -= Decimal(event['quantity'])
    return held


def _day_pnl(ctx: Context, account: str, ticker: str) -> Decimal:
    """quantity now x price - quantity before x previous close - buys(q x p + fee) + sells(q x p - fee)."""
    events = _events(ctx)
    session = ctx.sessions[ticker]
    price, prev = _price(ctx, ticker)
    before = _quantity(events, account, ticker, before=session)
    now = _quantity(events, account, ticker, upto=ctx.trading_date)
    pnl = now * price - before * prev
    for event in events:
        if (event['kind'] in ('buy', 'sell') and event.get('account') == account and event.get('ticker') == ticker
                and session <= event['date'] <= ctx.trading_date):
            amount = Decimal(event['quantity']) * Decimal(event['price'])
            fee = Decimal(event.get('fee') or '0')
            if event['kind'] == 'buy':
                pnl -= amount + fee
            elif event['kind'] == 'sell':
                pnl += amount - fee
    return pnl


def _total_cny(ctx: Context) -> Decimal:
    state = replay([event for event in _events(ctx) if event['date'] <= ctx.trading_date])
    total = Decimal(0)
    for account in state['accounts'].values():
        for ticker, position in account['positions'].items():
            price, _ = _price(ctx, ticker)
            total += r2(Decimal(position['quantity']) * price * _rate(ctx, _currency(ticker)))
        for currency, balance in account['cash_balances'].items():
            total += r2(Decimal(balance) * _rate(ctx, currency))
        total += Decimal(account.get('fund_cny') or '0')
    return total


def _listing(ticker: str) -> str:
    """HK codes are numbers: HKG:700 and HKG:00700 are one listing."""
    exchange, code = ticker.split(':', 1)
    return f'HKG:{int(code):05d}' if exchange == 'HKG' and code.isdigit() else ticker


def _levels(ctx: Context, account: Optional[str] = None, any_account: bool = True) -> Dict[str, set]:
    """listing -> {('stop'|'target', threshold)} from active plans and enabled stop / target rules that cover
    ``account`` (a level without an account covers every one)."""
    found: Dict[str, set] = {}

    def covers(owner) -> bool:
        return any_account or owner is None or owner == account
    with _desk(ctx) as db:
        if _table(db, 'trade_plans'):
            for row in db.execute("SELECT ticker, account, stop_price, target_price FROM trade_plans WHERE status='active'"):
                for kind, value in (('stop', row['stop_price']), ('target', row['target_price'])):
                    if value is not None and covers(row['account']):
                        found.setdefault(_listing(row['ticker']), set()).add((kind, Decimal(value)))
        if _table(db, 'alert_rules'):
            for row in db.execute("SELECT ticker, account, kind, threshold FROM alert_rules WHERE enabled=1 "
                                  "AND kind IN ('stop_loss', 'target_price')"):
                if covers(row['account']):
                    kind = 'stop' if row['kind'] == 'stop_loss' else 'target'
                    found.setdefault(_listing(row['ticker']), set()).add((kind, Decimal(row['threshold'])))
    return found


def _holdings(ctx: Context) -> List[tuple]:
    """``(account, ticker)`` of every position open at the end of the trading day."""
    events = _events(ctx)
    state = replay(events) if events else {'accounts': {}}
    return [(name, ticker) for name, account in state['accounts'].items() for ticker in account['positions']]


def _held(ctx: Context) -> List[str]:
    return list(dict.fromkeys(ticker for _, ticker in _holdings(ctx)))


def _watched(ctx: Context) -> List[str]:
    with _desk(ctx) as db:
        if not _table(db, 'watchlist'):
            return []
        return [row[0] for row in db.execute('SELECT ticker FROM watchlist')]


def _previous_trading_day(ctx: Context) -> str:
    return SessionClock().previous_trading_day('CN', datetime.fromisoformat(ctx.trading_date).date()).isoformat()


def _recent_alerts(ctx: Context) -> int:
    """Alerts fired from the previous trading day on, plus enabled stop / target / weight rules still fired whose
    latest alert is older."""
    first = _previous_trading_day(ctx)
    with _desk(ctx) as db:
        if not _table(db, 'alert_events'):
            return 0
        count = db.execute('SELECT COUNT(*) FROM alert_events WHERE trading_date >= ? AND trading_date <= ?',
                           (first, ctx.trading_date)).fetchone()[0]
        for (rule_id,) in db.execute(
                "SELECT r.id FROM alert_rules r JOIN alert_state s ON s.rule_id = r.id WHERE r.enabled=1 AND s.armed=0 "
                "AND r.kind IN ('stop_loss', 'target_price', 'position_weight_max')").fetchall():
            latest = db.execute('SELECT MAX(trading_date) FROM alert_events WHERE rule_id=?', (rule_id,)).fetchone()[0]
            if latest is None or latest < first:
                count += 1
    return count


# -- the checks: one per metric -----------------------------------------------------

def _check_index(item, ctx):
    _quote_source(item, ctx, item['ref']['ticker'])
    price, prev = _price(ctx, item['ref']['ticker'])
    assert Decimal(item['value']) == r2((price - prev) / prev * 100), item
    assert Decimal(item['figures']['close']) == price and Decimal(item['figures']['prev_close']) == prev, item


def _check_distance(item, ctx):
    ticker = item['ref']['ticker']
    _quote_source(item, ctx, ticker)
    price, _ = _price(ctx, ticker)
    threshold = Decimal(item['figures']['threshold'])
    kind = 'stop' if '止损' in item['label'] else 'target'
    assert (kind, threshold) in _levels(ctx, item['ref'].get('account'), any_account=False).get(_listing(ticker), set()), item
    gap = price - threshold if kind == 'stop' else threshold - price
    assert Decimal(item['figures']['price']) == price and Decimal(item['figures']['gap']) == gap, item
    assert Decimal(item['value']) == r2(gap / price * 100), item


def _check_without_levels(item, ctx):
    assert item['source'] == '交易计划', item
    held = _holdings(ctx)
    listings = {_listing(ticker) for _, ticker in held}
    bare = [1 for account, ticker in dict.fromkeys(held)
            if not _levels(ctx, account, any_account=False).get(_listing(ticker))]
    bare += [1 for ticker in _watched(ctx) if _listing(ticker) not in listings and not _levels(ctx).get(_listing(ticker))]
    assert item['value'] == len(bare), item


def _review_rows(ctx: Context):
    from datetime import date, timedelta
    first = (date.fromisoformat(ctx.trading_date) - timedelta(days=30)).isoformat()
    plans = []
    with _desk(ctx) as db:
        if _table(db, 'trade_plans'):
            plans = [row['id'] for row in db.execute(
                "SELECT id FROM trade_plans WHERE status IN ('draft', 'active') AND review_on >= ? AND review_on <= ?",
                (first, ctx.trading_date))]
    journal = []
    path = ctx.directory / 'ledger.sqlite3'
    if path.is_file():
        with sqlite3.connect(path) as db:
            if _table(db, 'journal'):
                journal = [row[0] for row in db.execute('SELECT id, payload FROM journal')
                           if first <= json.loads(row[1])['review_on'] <= ctx.trading_date]
    return plans, journal


def _check_review_count(item, ctx):
    assert item['source'] == '交易计划、研究笔记', item
    plans, journal = _review_rows(ctx)
    assert item['value'] == len(plans) + len(journal), item


def _check_review_due(item, ctx):
    plans, journal = _review_rows(ctx)
    ref = item['ref']
    assert ref.get('plan_id') in plans or ref.get('journal_id') in journal, item
    assert item['source'] == ('交易计划' if ref.get('plan_id') is not None else '研究笔记'), item
    assert item['value'] <= ctx.trading_date, item


def _check_recent_alerts(item, ctx):
    assert item['source'] == '提醒记录', item
    assert item['value'] == _recent_alerts(ctx), item


def _check_alert(item, ctx):
    with _desk(ctx) as db:
        row = db.execute('SELECT * FROM alert_events WHERE id=?', (item['ref']['alert_event_id'],)).fetchone()
    assert row is not None and Decimal(item['value']) == Decimal(row['value']), item
    assert item['figures'].get('threshold') == row['threshold'] and item['figures'].get('price') == row['price'], item
    assert item['source'] == (row['quote_source'] or '提醒记录') and item['as_of'] == (row['quote_time'] or row['fired_at']), item


def _check_alert_threshold(item, ctx):
    with _desk(ctx) as db:
        row = db.execute('SELECT r.threshold, s.armed FROM alert_rules r JOIN alert_state s ON s.rule_id = r.id '
                         'WHERE r.id=?', (item['ref']['alert_rule_id'],)).fetchone()
    assert row is not None and row['armed'] == 0 and item['value'] == row['threshold'], item
    assert item['source'] == '提醒记录', item


def _check_alert_count(item, ctx):
    assert item['source'] == '提醒记录', item
    with _desk(ctx) as db:
        count = db.execute('SELECT COUNT(*) FROM alert_events WHERE trading_date=?', (ctx.trading_date,)).fetchone()[0] \
            if _table(db, 'alert_events') else 0
    assert item['value'] == count, item


def _pending(ctx: Context, kind: Optional[str] = None) -> int:
    with _desk(ctx) as db:
        if not _table(db, 'proposals'):
            return 0
        rows = db.execute("SELECT kind, created_at FROM proposals WHERE status='pending'").fetchall()
    return len([row for row in rows if (kind is None or row['kind'] == kind)
                and datetime.fromisoformat(row['created_at']) <= ctx.now])


def _check_pending(item, ctx):
    assert item['source'] == '待确认方案', item
    assert item['value'] == _pending(ctx), item


def _check_lessons(item, ctx):
    assert item['source'] == '待确认方案', item
    assert item['value'] == _pending(ctx, 'lesson'), item


def _check_ledger_pending(item, ctx):
    assert item['source'] == '账本', item
    path = ctx.directory / 'ledger.sqlite3'
    count = 0
    if path.is_file():
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            revision = db.execute('SELECT revision FROM ledger_meta').fetchone()[0]
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            # A manual preview the ledger API no longer lists: its lifetime ended (backend.api.ledger._expired).
            expired = {row[0] for row in db.execute('SELECT proposal_id, expires_at FROM proposal_expiry')
                       if datetime.fromisoformat(row[1]) <= ctx.now} if 'proposal_expiry' in tables else set()
            for row in db.execute('SELECT id, revision, payload, created_at FROM proposals WHERE receipt IS NULL'):
                if (row[1] == revision and json.loads(row[2])['events'] and row[0] not in expired
                        and datetime.fromisoformat(row[3]) <= ctx.now):
                    count += 1
    assert item['value'] == count, item


def _todays(ctx: Context, kinds=('buy', 'sell')) -> List[dict]:
    return [event for event in _events(ctx) if event['date'] == ctx.trading_date and event['kind'] in kinds]


def _ledger_source(item, ctx):
    assert item['source'] == f'账本（版本 {_revision(ctx)}）', item


def _check_trade_count(item, ctx):
    _ledger_source(item, ctx)
    assert item['value'] == len(_todays(ctx)), item


def _check_trade(item, ctx):
    _ledger_source(item, ctx)
    [event] = [event for event in _todays(ctx) if event['id'] == item['ref']['ledger_event_id']]
    assert Decimal(item['value']) == r2(Decimal(event['quantity']) * Decimal(event['price'])), item
    assert Decimal(item['figures']['quantity']) == Decimal(event['quantity']), item
    assert Decimal(item['figures']['price']) == Decimal(event['price']), item
    assert Decimal(item['figures']['fee']) == Decimal(event.get('fee') or '0'), item


def _check_other_events(item, ctx):
    _ledger_source(item, ctx)
    others = [event for event in _events(ctx) if event['date'] == ctx.trading_date and event['kind'] not in ('buy', 'sell')]
    assert item['value'] == len(others), item


def _links(ctx: Context) -> List[dict]:
    with _desk(ctx) as db:
        if not _table(db, 'plan_links'):
            return []
        rows = [dict(row) for row in db.execute('SELECT * FROM plan_links WHERE event_date=?', (ctx.trading_date,))]
    alive = {event['id'] for event in _todays(ctx)}
    return [{**row, 'deviations': json.loads(row['deviations'])} for row in rows if row['event_id'] in alive]


def _check_deviation_trades(item, ctx):
    assert item['source'] == '交易计划', item
    assert item['value'] == len([link for link in _links(ctx) if link['deviations']]), item


def _check_deviation(item, ctx):
    assert item['source'] == '交易计划', item
    codes = [deviation['code'] for link in _links(ctx) for deviation in link['deviations']
             if deviation['message'] in {row['text'].split('：', 1)[1] for row in item['items']}]
    assert item['value'] == len(item['items']) == len(codes), item


def _check_followed(item, ctx):
    assert item['source'] == '交易计划', item
    assert item['value'] == len([link for link in _links(ctx) if not link['deviations'] and link['plan_id'] is not None]), item


def _check_planless(item, ctx):
    assert item['source'] == '交易计划', item
    assert item['value'] == len([link for link in _links(ctx) if not link['deviations'] and link['plan_id'] is None]), item


def _check_unchecked(item, ctx):
    assert item['source'] == '交易计划', item
    linked = {link['event_id'] for link in _links(ctx)}
    assert item['value'] == len([event for event in _todays(ctx) if event['id'] not in linked]), item


def _contributors(ctx: Context) -> List[tuple]:
    """``(account, ticker)`` of every position in the day's profit, from the ledger alone: held before the session
    its quote belongs to, held at the end of the trading day, or bought or sold in that session (a full exit
    included)."""
    events = _events(ctx)
    found = []
    tickers = {position['ticker'] for event in events for position in event.get('positions') or []}
    tickers |= {event['ticker'] for event in events if event.get('ticker') and event['kind'] in ('buy', 'sell')}
    accounts = list(dict.fromkeys(event['account'] for event in events if event['kind'] == 'opening'))
    for account in accounts:
        for ticker in sorted(tickers):
            session = ctx.sessions.get(ticker)
            before = _quantity(events, account, ticker, before=session) if session else Decimal(0)
            after = _quantity(events, account, ticker, upto=ctx.trading_date)
            traded = session is not None and any(
                event['kind'] in ('buy', 'sell') and event.get('account') == account and event.get('ticker') == ticker
                and session <= event['date'] <= ctx.trading_date for event in events)
            if before or after or traded:
                assert session is not None, f'the context names no session for {ticker}'
                found.append((account, ticker))
    return found


def _check_day_pnl(item, ctx):
    ticker, account = item['ref']['ticker'], item['ref']['account']
    assert (account, ticker) in _contributors(ctx), item
    _quote_source(item, ctx, ticker)
    native = _day_pnl(ctx, account, ticker)
    assert Decimal(item['figures']['day_pnl']) == r2(native), (item, native)
    assert Decimal(item['value']) == r2(native * _rate(ctx, _currency(ticker))), item
    if _currency(ticker) != 'CNY':
        fx = item['fx']
        assert Decimal(fx['rate']) == _rate(ctx, _currency(ticker)) and fx['source'] == 'fake', item


def _check_day_total(item, ctx):
    assert item['source'] == 'fake', item
    contributors = _contributors(ctx)
    rows = {(fact['ref']['account'], fact['ref']['ticker']) for fact in facts(ctx.brief, 'pnl')
            if fact['metric'] == 'day_pnl_cny'}
    assert rows == set(contributors), (sorted(rows), contributors)   # no position left out, none added
    expected = sum((r2(_day_pnl(ctx, account, ticker) * _rate(ctx, _currency(ticker)))
                    for account, ticker in contributors), Decimal(0))
    assert Decimal(item['value']) == expected, (item, expected)
    pct = item.get('figures', {}).get('day_pnl_pct')
    if pct is not None:
        total = _total_cny(ctx)
        assert Decimal(pct) == r2(expected / (total - expected) * 100), (item, total)


def _check_levels_reached(item, ctx):
    assert item['source'] == '交易计划', item
    count = 0
    for account, ticker in dict.fromkeys(_holdings(ctx)):
        levels = _levels(ctx, account, any_account=False).get(_listing(ticker), set())
        if not levels:
            continue
        assert ticker in _script(ctx)['quotes'] and 'price' in _script(ctx)['quotes'][ticker], \
            f'a count of levels reached needs every price ({ticker}): {item}'
        price, _ = _price(ctx, ticker)
        count += len([1 for kind, threshold in levels if (price <= threshold if kind == 'stop' else price >= threshold)])
    assert item['value'] == count, item


def _check_news(item, ctx):
    from providers.news.cn_news_provider import SRC_NEWS, SRC_NOTICE
    kind = 'notice' if item['metric'] == 'announcement_count' else 'news'
    assert item['source'] == (SRC_NOTICE if kind == 'notice' else SRC_NEWS), item
    assert item['value'] == ctx.news[(kind, item['ref']['ticker'])], item


def _check_zero(item, ctx):
    assert item['source'] == '账本', item
    assert item['value'] == 0, item


CHECKS = {
    'index_change_pct': _check_index, 'distance_pct': _check_distance, 'without_levels_count': _check_without_levels,
    'review_due_count': _check_review_count, 'review_due': _check_review_due, 'recent_alert_count': _check_recent_alerts,
    'alert_value': _check_alert, 'alert_threshold': _check_alert_threshold, 'alert_count': _check_alert_count,
    'pending_count': _check_pending, 'planless_count': _check_planless,
    'ledger_pending_count': _check_ledger_pending, 'lesson_count': _check_lessons, 'trade_count': _check_trade_count,
    'trade_amount': _check_trade, 'other_event_count': _check_other_events,
    'deviation_trade_count': _check_deviation_trades, 'deviation_count': _check_deviation,
    'followed_count': _check_followed, 'unchecked_count': _check_unchecked, 'day_pnl_cny': _check_day_pnl,
    'day_pnl_total': _check_day_total, 'levels_reached_count': _check_levels_reached,
    'announcement_count': _check_news, 'news_count': _check_news, 'subject_count': _check_zero,
    'news_subject_count': _check_zero,
}


def walk(brief: dict, ctx: Context) -> Dict[str, int]:
    """Check every fact; returns how many numeric facts each metric had."""
    ctx.brief = brief
    counts: Dict[str, int] = {}
    for part in brief['sections']:
        for item in part['facts']:
            assert item['text'], item
            assert item['as_of'], item
            assert 'source' in item, item
            if item['value'] is None:
                # Unknown is null with the reason, never 0.
                assert ('不可用' in item['text'] or '未查询' in item['text']), item
                assert item['status'] and item['status']['code'] in ('unavailable', 'not_queried'), item
                continue
            if item['unit'] not in NUMERIC and item['metric'] not in CHECKS:
                continue   # a state word; dates are checked when their metric has a check
            assert item['metric'] in CHECKS, f'no provenance check for {item["metric"]}: {item}'
            CHECKS[item['metric']](item, ctx)
            counts[item['metric']] = counts.get(item['metric'], 0) + 1
            for name, figure in (item.get('figures') or {}).items():
                Decimal(figure)   # every figure is a plain decimal string
    return counts
