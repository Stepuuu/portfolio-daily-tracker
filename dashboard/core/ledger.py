"""Decimal accounting with immutable events and revision-checked confirmation.

Amounts are serialized as decimal strings. A proposal never changes the ledger;
confirmation atomically persists its events, state, revision and receipt in SQLite.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

CURRENCIES = {"CNY", "HKD", "USD"}
MARKETS = {"SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"}
KINDS = {"opening", "buy", "sell", "deposit", "withdrawal", "dividend", "fee", "transfer", "fx", "split", "reverse", "fund_value", "fund_buy", "fund_sell",
         "interest"}


class LedgerError(ValueError):
    pass


class Conflict(LedgerError):
    pass


class StaleProposal(Conflict):
    """A bound proposal's source changed, or the ledger changed while it was prepared (see "Bound proposals")."""


def amount(value, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or value is None or len(str(value)) > 128:
        raise LedgerError("An explicit numeric amount is required")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise LedgerError("Invalid numeric amount") from None
    if not number.is_finite() or abs(number) > Decimal('1e18'):
        raise LedgerError("Amount must be finite and within supported bounds")
    if number.as_tuple().exponent < -100:
        raise LedgerError('Amount has excessive decimal precision')
    if positive and number <= 0 or nonnegative and number < 0:
        raise LedgerError("Amount has an invalid sign")
    return number


def decimal_text(number):
    value = amount(number)
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def valid_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise LedgerError("Date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise LedgerError("Invalid calendar date") from None
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _stamp():
    """When a proposal row is written (UTC, ISO 8601): now, or inside ``desk_store.pinned_now`` the
    moment it pins, so the demo's seed books each entry on its own day."""
    from core import desk_store   # imports this module
    return (desk_store.pinned_moment() or datetime.now(timezone.utc)).isoformat()


def currency(value):
    if not isinstance(value, str) or value not in CURRENCIES:
        raise LedgerError("Currency must be CNY, HKD or USD")
    return value


def label(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise LedgerError(f"{field} is required (maximum 240 characters)")
    return value.strip()


def instrument(value):
    value = label(value, 'ticker').upper()
    if not re.fullmatch(r'(SHA|SHE|HKG|NASDAQ|NYSE):[A-Z0-9.\-]{1,20}', value):
        raise LedgerError("Use an exchange-qualified ticker, e.g. NASDAQ:AAPL")
    return value


# B shares (SHA:900xxx in USD, SHE:200xxx/201xxx in HKD) trade in foreign currency, while the ledger books every
# SHA/SHE listing in CNY, so a new event cannot record one honestly. Rows booked before this rule still replay
# (``replay`` uses ``instrument``); valuation marks them as a booking problem.
B_SHARE_REFUSAL = '{ticker} 是 B 股，以 {currency} 交易；账本按人民币记录沪深标的，暂不支持记入 B 股。'


def bookable(value):
    """``instrument(value)`` for a new event: refused for a listing the ledger cannot book in its own currency."""
    ticker = instrument(value)
    from core.market.instruments import InstrumentError, parse
    try:
        listing = parse(ticker)
    except InstrumentError:
        return ticker
    if listing.kind == 'equity' and listing.currency != MARKETS[listing.exchange]:
        raise LedgerError(B_SHARE_REFUSAL.format(ticker=ticker, currency=listing.currency))
    return ticker


def _identity(ticker):
    """What makes two tickers one listing, by the rule of ``core.market.search.identity_key``: ``HKG:700`` =
    ``HKG:00700``, ``NYSE:BRK-B`` = ``NYSE:BRK.B``. Written out here: the engine's ledger bridge ships this
    module without the market package, and every replay needs it."""
    text = ticker.strip().upper()
    exchange, _, code = text.partition(':')
    if exchange == 'HKG' and re.fullmatch(r'\d{1,5}', code) and int(code):
        return f'HKG:{int(code):05d}'
    if exchange in ('NASDAQ', 'NYSE'):
        return f'{exchange}:{code.replace("-", ".")}'
    return text


# One listing, one position, for what is booked from now on: a new event for a listing its account holds under
# another spelling is booked under the held one (``_booked``). Replay never merges spellings: a ledger booked
# before this rule may hold one listing under two spellings, and it replays to the figures it always had.

def _held(state):
    """Per (account, listing): the spellings the account holds it under, with their quantities."""
    held = {}
    for name, account in state['accounts'].items():
        for ticker, position in account['positions'].items():
            held.setdefault((name, _identity(ticker)), []).append((ticker, amount(position['quantity'])))
    return held


def _booked(events, existing):
    """New ``events`` spelled as their accounts held each listing on the event's own date (the ledger as a new
    event dated then is checked against it, ``replay_for_entry``): the held spelling; of two held, the one typed,
    else the larger position. Held under none on that date, but dated before an event of the account that uses
    the listing (an entry missed earlier): the one spelling the account's events use, when they use exactly one.
    Otherwise the spelling typed (a listing bought again after it was sold out keeps the spelling typed). A
    listing this proposal opens or buys counts as held from that event's date, so a proposal that opens a
    listing and trades it again uses one spelling."""
    used, last = {}, {}   # (account, listing) -> the spellings the account's events used (only those can be held),
    for event in existing:  # and the latest date of those events
        for ticker in [event.get('ticker')] + [p.get('ticker') for p in event.get('positions') or [] if isinstance(p, dict)]:
            if isinstance(ticker, str):
                key = (event.get('account'), _identity(ticker))
                used.setdefault(key, set()).add(ticker)
                last[key] = max(last.get(key, ''), event['date'])
    dated, added, out = {}, {}, []

    def held_on(day):
        if day not in dated:
            dated[day] = _held(replay_for_entry(existing, day))
        return dated[day]
    for event in events:
        account, day = event.get('account'), event['date']

        def spell(ticker):
            key = (account, _identity(ticker))
            spellings = (held_on(day).get(key) or []) if used.get(key, set()) - {ticker} else []
            if not spellings and key in added and added[key][0] <= day:
                spellings = [added[key][1]]
            if not spellings and len(used.get(key, ())) == 1 and last[key] > day:
                return next(iter(used[key]))
            if not spellings or any(spelling == ticker for spelling, _ in spellings):
                return ticker
            return max(spellings, key=lambda item: item[1])[0]
        event = dict(event)
        if isinstance(event.get('ticker'), str):
            event['ticker'] = spell(event['ticker'])
            if event['kind'] == 'buy':
                added.setdefault((account, _identity(event['ticker'])), (day, (event['ticker'], Decimal(0))))
        if event['kind'] == 'opening' and isinstance(event.get('positions'), list):
            positions = []
            for position in event['positions']:
                ticker = spell(position['ticker'])
                positions.append({**position, 'ticker': ticker,
                                  **({'name': ticker} if position.get('name') == position['ticker'] else {})})
                added.setdefault((account, _identity(ticker)), (day, (ticker, amount(position['quantity']))))
            event['positions'] = positions
        out.append(event)
    return out


def _by_listing(event):
    """``event`` with its ticker as the listing: a fill booked under the held spelling is the same fill."""
    return {**event, 'ticker': _identity(event['ticker'])} if isinstance(event.get('ticker'), str) else event


def normalize(raw):
    if not isinstance(raw, dict):
        raise LedgerError("Each event must be an object")
    allowed = {'kind', 'date', 'source', 'external_id', 'account', 'target_id', 'ticker', 'name', 'note',
               'quantity', 'price', 'fee', 'currency', 'amount', 'fx_rate', 'to_account', 'to_currency',
               'received_amount', 'ratio', 'allow_margin', 'cash_balances', 'positions', 'contributed_cny', 'fund_cny'}
    if set(raw) - allowed:
        raise LedgerError('Unknown event fields: ' + ', '.join(sorted(set(raw) - allowed)))
    event = copy.deepcopy(raw)
    if 'allow_margin' in event and not isinstance(event['allow_margin'], bool):
        raise LedgerError('allow_margin must be a boolean')
    event['kind'] = label(event.get('kind'), 'kind')
    if event['kind'] not in KINDS:
        raise LedgerError("Unsupported event kind")
    event['date'] = valid_date(event.get('date'))
    event['source'] = label(event.get('source', 'manual'), 'source')
    external = event.get('external_id')
    if external is not None:
        external = event['external_id'] = label(external, 'external_id')
    event['id'] = (hashlib.sha256(encode([event['source'], label(external, 'external_id')]).encode()).hexdigest()
                   if external is not None else uuid.uuid4().hex)
    if event['kind'] == 'reverse':
        event['target_id'] = label(event.get('target_id'), 'target_id')
    else:
        event['account'] = label(event.get('account'), 'account')
    if event.get('ticker') is not None:
        event['ticker'] = bookable(event['ticker'])
    if event.get('note') is not None:
        event['note'] = label(event['note'], 'note')
    if 'name' in event:
        event['name'] = label(event['name'], 'name')
    for key in ('quantity', 'price', 'fee', 'amount', 'fx_rate', 'received_amount', 'ratio', 'contributed_cny', 'fund_cny'):
        if key in event:
            event[key] = decimal_text(amount(event[key]))
    if 'cash_balances' in event:
        if not isinstance(event['cash_balances'], dict):
            raise LedgerError('cash_balances must be an object')
        event['cash_balances'] = {currency(k): decimal_text(amount(v)) for k, v in event['cash_balances'].items()}
    if 'positions' in event:
        if not isinstance(event['positions'], list) or len(event['positions']) > 1000:
            raise LedgerError('positions must be an array with at most 1000 entries')
        positions = []
        for p in event['positions']:
            if not isinstance(p, dict) or set(p) - {'ticker', 'name', 'quantity', 'cost_price'}:
                raise LedgerError('Invalid opening position fields')
            positions.append(_position(bookable(p.get('ticker')), p.get('quantity'), p.get('cost_price'),
                                       label(p['name'], 'name') if 'name' in p else None))
        event['positions'] = positions
    if len(encode(event)) > 100000:
        raise LedgerError("Event is too large")
    return event


def _account():
    return {'cash_balances': {}, 'positions': {}, 'contributed_cny': '0', 'fund_cny': '0', 'realized': {}}


def _cash(account, unit, delta):
    unit = currency(unit)
    balance = amount(account['cash_balances'].get(unit, '0')) + delta
    account['cash_balances'][unit] = decimal_text(balance)


def _position(ticker, quantity, price, name=None):
    return {'ticker': ticker, 'name': name or ticker, 'currency': MARKETS[ticker.split(':')[0]],
            'quantity': decimal_text(amount(quantity, positive=True)),
            'cost_price': decimal_text(amount(price, nonnegative=True))}


def replay(events):
    """Rebuild state, applying explicit reversals without deleting history."""
    identities = {event['id']: event for event in events}
    reversed_ids = set()
    for event in events:
        if event['kind'] == 'reverse':
            target = identities.get(event['target_id'])
            if not target or target['kind'] == 'reverse' or target['id'] in reversed_ids:
                raise LedgerError("Reversal target is missing, already reversed, or is itself a reversal")
            if event['date'] < target['date'] or event.get('_sequence', 0) <= target.get('_sequence', 0):
                raise LedgerError('Reversal must follow the original event')
            reversed_ids.add(target['id'])
    state = {'accounts': {}, 'date': max((e['date'] for e in events), default=None)}
    for event in sorted(events, key=lambda e: (e['date'], e.get('_sequence', 0))):
        if event['id'] in reversed_ids or event['kind'] == 'reverse':
            continue
        kind, name = event['kind'], event['account']
        accounts = state['accounts']
        if kind == 'opening':
            if name in accounts:
                raise LedgerError(f"Account already exists: {name}")
            account = _account()
            for unit, value in event.get('cash_balances', {}).items():
                _cash(account, unit, amount(value))
            account['contributed_cny'] = decimal_text(amount(event.get('contributed_cny', 0)))
            account['fund_cny'] = decimal_text(amount(event.get('fund_cny', 0), nonnegative=True))
            for raw in event.get('positions', []):
                ticker = instrument(raw.get('ticker'))
                if ticker in account['positions']:
                    raise LedgerError("Duplicate opening position")
                account['positions'][ticker] = _position(ticker, raw.get('quantity'), raw.get('cost_price'), raw.get('name'))
            accounts[name] = account
            continue
        if name not in accounts:
            raise LedgerError(f"Create an opening balance for account: {name}")
        account = accounts[name]
        if kind in {'buy', 'sell'}:
            ticker = instrument(event.get('ticker'))
            unit = currency(event.get('currency'))
            if unit != MARKETS[ticker.split(':')[0]]:
                raise LedgerError("Trade currency does not match the ticker's market")
            quantity, price = amount(event.get('quantity'), positive=True), amount(event.get('price'), positive=True)
            fee = amount(event.get('fee', 0), nonnegative=True)
            previous = account['positions'].get(ticker)
            old_quantity = amount(previous['quantity']) if previous else Decimal(0)
            old_cost = amount(previous['cost_price']) if previous else Decimal(0)
            if kind == 'buy':
                balance = amount(account['cash_balances'].get(unit, '0'))
                if balance < quantity * price + fee and not event.get('allow_margin', False):
                    raise LedgerError("Insufficient native cash; explicitly allow margin to borrow")
                _cash(account, unit, -quantity * price - fee)
                total_quantity = old_quantity + quantity
                average = (old_quantity * old_cost + quantity * price + fee) / total_quantity
                account['positions'][ticker] = _position(ticker, total_quantity, average, event.get('name') or (previous or {}).get('name'))
            else:
                if quantity > old_quantity:
                    raise LedgerError("Cannot sell more shares than held")
                _cash(account, unit, quantity * price - fee)
                realized = amount(account['realized'].get(unit, '0')) + quantity * (price - old_cost) - fee
                account['realized'][unit] = decimal_text(realized)
                if quantity == old_quantity:
                    del account['positions'][ticker]
                else:
                    previous['quantity'] = decimal_text(old_quantity - quantity)
        elif kind in {'deposit', 'withdrawal', 'dividend', 'fee', 'interest'}:
            # interest (利息归本, net 逆回购 income) is a cash credit like a dividend: no change to contributions.
            unit, value = currency(event.get('currency')), amount(event.get('amount'), positive=True)
            delta = -value if kind in {'withdrawal', 'fee'} else value
            _cash(account, unit, delta)
            if kind in {'deposit', 'withdrawal'}:
                rate = Decimal(1) if unit == 'CNY' else amount(event.get('fx_rate'), positive=True)
                account['contributed_cny'] = decimal_text(amount(account['contributed_cny']) + delta * rate)
        elif kind == 'transfer':
            destination = label(event.get('to_account'), 'to_account')
            if destination not in accounts or destination == name:
                raise LedgerError("Transfer requires a different existing destination account")
            unit, value = currency(event.get('currency')), amount(event.get('amount'), positive=True)
            _cash(account, unit, -value)
            _cash(accounts[destination], unit, value)
            rate = Decimal(1) if unit == 'CNY' else amount(event.get('fx_rate'), positive=True)
            account['contributed_cny'] = decimal_text(amount(account['contributed_cny']) - value * rate)
            accounts[destination]['contributed_cny'] = decimal_text(amount(accounts[destination]['contributed_cny']) + value * rate)
        elif kind == 'fx':
            unit, target = currency(event.get('currency')), currency(event.get('to_currency'))
            if unit == target:
                raise LedgerError("FX exchange requires two different currencies")
            # Record actual received amount; do not guess execution rate or fees.
            _cash(account, unit, -amount(event.get('amount'), positive=True))
            _cash(account, target, amount(event.get('received_amount'), positive=True))
        elif kind == 'fund_value':
            if event.get('currency', 'CNY') != 'CNY':
                raise LedgerError('Aggregate fund values use CNY')
            account['fund_cny'] = decimal_text(amount(event.get('amount'), nonnegative=True))
        elif kind in {'fund_buy', 'fund_sell'}:
            if event.get('currency', 'CNY') != 'CNY':
                raise LedgerError('Aggregate fund transactions use CNY')
            value = amount(event.get('amount'), positive=True)
            fee = amount(event.get('fee', 0), nonnegative=True)
            fund = amount(account['fund_cny'])
            if kind == 'fund_buy':
                if amount(account['cash_balances'].get('CNY', 0)) < value + fee and not event.get('allow_margin', False):
                    raise LedgerError('Insufficient CNY cash for fund purchase')
                account['fund_cny'] = decimal_text(fund + value)
                _cash(account, 'CNY', -value - fee)
            else:
                if value > fund:
                    raise LedgerError('Fund redemption exceeds recorded fund value; reconcile the valuation first')
                account['fund_cny'] = decimal_text(fund - value)
                _cash(account, 'CNY', value - fee)
        elif kind == 'split':
            ticker = instrument(event.get('ticker'))
            position = account['positions'].get(ticker)
            if not position:
                raise LedgerError("Split requires an existing position")
            ratio = amount(event.get('ratio'), positive=True)
            position['quantity'] = decimal_text(amount(position['quantity']) * ratio)
            position['cost_price'] = decimal_text(amount(position['cost_price']) / ratio)
    return state


def _account_groups(events, corrected):
    """Split events into groups of accounts that replay independently.

    Accounts share no cash. Only a transfer touches two accounts, and its two
    sides replay exactly as a withdrawal from the source and a deposit to the
    destination, so each account normally replays on its own. A transfer that a
    later reversal removes (one of ``corrected``) stays whole and ties its two
    accounts together: the corrected view must drop both sides or neither.
    """
    parent = {}

    def root(name):
        parent.setdefault(name, name)
        while parent[name] != name:
            parent[name] = name = parent[parent[name]]
        return name

    streams, placed = {}, {}
    for event in events:
        if event['kind'] == 'reverse':
            continue
        destination = event.get('to_account') if event['kind'] == 'transfer' else None
        if isinstance(destination, str) and event['id'] not in corrected:
            source = {key: value for key, value in event.items() if key != 'to_account'}
            sides = [(event['account'], {**source, 'kind': 'withdrawal'}),
                     (destination, {**source, 'kind': 'deposit', 'account': destination})]
        else:
            sides = [(event['account'], event)]
            if isinstance(destination, str):
                parent[root(event['account'])] = root(destination)
        for name, side in sides:
            streams.setdefault(name, []).append(side)
            placed.setdefault(event['id'], []).append(name)
    for event in events:
        if event['kind'] == 'reverse':
            # An unknown target goes to its own group, whose replay reports it.
            for name in placed.get(event['target_id'], [None]):
                streams.setdefault(name, []).append(event)
    groups = {}
    for name, stream in streams.items():
        members, part = groups.setdefault(root(name) if name is not None else None, ([], []))
        members.append(name)
        part.extend(stream)
    return list(groups.values())


def replay_as_of(events, as_of):
    """Rebuild the state at the end of ``as_of`` from events dated on or before it.

    A reversal takes effect on its own date, so an earlier ``as_of`` normally still
    shows the original event. That dated view can be impossible: an event back-dated
    after the reversal was only validated against the corrected ledger, e.g. a buy
    re-entered on its real, earlier date would spend the same cash as the mistaken
    buy it replaces. For the accounts whose own dated replay fails, the later
    reversals are then applied as well; that leaves a prefix of the full replay,
    which confirmation already validated, so it cannot fail. Every other account
    keeps its dated view. ``as_of_corrected_accounts`` lists the corrected accounts
    and is present only when there are any. See ``_account_groups`` for transfers.
    """
    dated = [event for event in events if event['date'] <= as_of]
    try:
        return replay(dated)
    except LedgerError:
        corrected = {event['target_id'] for event in events if event['kind'] == 'reverse' and event['date'] > as_of}
        if not corrected:
            raise
    accounts, days, adjusted = {}, [], set()
    for members, part in _account_groups(dated, corrected):
        try:
            result = replay(part)
        except LedgerError:
            if not any(event['id'] in corrected for event in part):
                raise
            result = replay([event for event in part if event['id'] not in corrected])
            adjusted.update(members)
        accounts.update(result['accounts'])
        days.append(result['date'])
    # Keep the account order of a plain replay: the order of the opening events.
    order = [event['account'] for event in sorted(dated, key=lambda e: (e['date'], e.get('_sequence', 0)))
             if event['kind'] == 'opening']
    return {'accounts': {name: accounts[name] for name in order if name in accounts},
            'date': max((day for day in days if day is not None), default=None),
            'as_of_corrected_accounts': [name for name in order if name in adjusted]}


def replay_for_entry(events, as_of):
    """The ledger at the end of ``as_of`` as a new event dated ``as_of`` is checked against it.

    ``propose`` replays every event that still counts in date order, the reversed ones and the reversals left
    out whatever date the reversal has, and a new event comes after those dated on or before its own date: so
    it sees exactly this view. Unlike ``replay_as_of`` a later reversal already counts here: after a sale is
    reversed, the corrected sale booked on the original date has the shares again. A prefix of the full
    replay, so it fails only when the ledger itself does not replay.
    """
    reversed_ids = {event.get('target_id') for event in events if event['kind'] == 'reverse'}
    return replay([event for event in events
                   if event['date'] <= as_of and event['kind'] != 'reverse' and event['id'] not in reversed_ids])


# Bound proposals
# ---------------
# A proposal prepared from another store's content (an import session in desk.sqlite3) carries a binding:
# (scope, subject, token) = (what kind of source, which one, a fingerprint of its content). The source's
# ``check(subject, token)`` (``register_binding``) runs inside the write transaction of ``propose`` and of
# ``confirm``: a bound proposal is confirmed only while its source still holds exactly that content, so a
# proposal whose source changed, expired or was deleted can never be confirmed, even when the source failed
# to withdraw it. A subject has at most one pending proposal (a unique index, not a read-then-write).
# A bound proposal whose scope has no check in this process is refused (fail closed).
#
# Events whose source starts with a prefix of ``BOUND_SOURCES`` come only from that bound source: an unbound
# proposal holding one (made before bindings existed) is refused and withdrawn by ``confirm``, and ``propose``
# refuses to create one. A proposal that can never be confirmed (its binding no longer holds, or the ledger
# changed since a bound proposal was prepared) is withdrawn by the confirmation that finds it; a withdrawn
# proposal keeps its row in ``proposal_bindings`` (an empty scope for one that had no binding), so confirming it
# later answers ``StaleProposal`` (409) instead of "not found".
#
# ``proposal_bindings`` is created by the first write that needs it (``writing``, ``propose``, ``confirm``):
# opening or reading a ledger never changes its schema. That first write is the upgrade from 3.2.x: the previews
# of the 3.2.x CSV import still pending then (every event with an ``external_id``, none an opening or a reversal)
# are marked with ``LEGACY_CSV_SCOPE``, and confirming one withdraws it with ``LEGACY_CSV`` (the importer prepares
# it again from the same file).
_BINDING_CHECKS = {}
BOUND_SOURCES = {'import:': 'import_session'}
# Bound proposals are the import page's 入账预览: the refusals use its words.
WITHDRAWN = ('这份入账预览已作废（导入的内容在生成它之后修改、删除或过期，或账本已有变化），不能确认。'
             '请重新生成入账预览。')
UNBOUND_IMPORT = ('这份入账预览来自旧版本的交割单导入，没有和导入记录绑定，无法核对它是否仍与导入的内容一致，'
                  '入账预览已作废，不能确认。请重新生成入账预览。')
LEDGER_CHANGED = '账本在生成这份入账预览之后有了变化，这份入账预览已作废，不能确认。请重新生成入账预览。'
LEGACY_CSV_SCOPE = 'csv-import-3.2'
LEGACY_CSV = ('这份入账预览是旧版本的 CSV 导入生成的，升级后已作废，不能确认。请重新导入：在“导入券商文件”里上传这个 CSV '
              '文件，按新的导入规则生成入账预览。')
_BINDINGS_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS proposal_bindings (proposal_id TEXT PRIMARY KEY, scope TEXT NOT NULL, '
    'subject TEXT NOT NULL, token TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL, info TEXT NOT NULL, '
    'created_at TEXT NOT NULL)',
    "CREATE UNIQUE INDEX IF NOT EXISTS proposal_bindings_one_pending ON proposal_bindings(scope, subject) "
    "WHERE state = 'pending'",
)


def _bound_source(events):
    """The binding scope an event's source belongs to (``BOUND_SOURCES``), or None."""
    for event in events:
        source = event.get('source') if isinstance(event, dict) else None
        for prefix, scope in BOUND_SOURCES.items():
            if isinstance(source, str) and source.startswith(prefix):
                return scope
    return None


def register_binding(scope, check):
    """``check(subject, token)`` returns None while a proposal bound to them may be confirmed, else the
    reason (Chinese, shown to the person). It may only read, and it runs while the ledger is locked."""
    if not isinstance(scope, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', scope) or not callable(check):
        raise ValueError('binding scope must be a short lowercase name and check a callable')
    _BINDING_CHECKS[scope] = check


def _binding_problem(scope, subject, token):
    """(reason, definitive): reason is None when the binding holds; definitive is False when it could not be checked."""
    check = _BINDING_CHECKS.get(scope)
    if check is None:
        return '这个提案来自本程序现在没有加载的功能，无法核对，不能确认。', False
    try:
        reason = check(subject, token)
    except Exception:
        return '暂时无法核对这个提案的来源，不能确认。请稍后重试。', False
    return reason, True


class Ledger:
    _TABLES = ('ledger_meta', 'events', 'proposals')

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # A ledger that already has its tables is only read here, so opening one for a read never writes to
            # the file or waits for its writers (``proposal_bindings``: see "Bound proposals").
            present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if set(self._TABLES) <= present and db.execute('SELECT 1 FROM ledger_meta WHERE id=1').fetchone():
                return
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ledger_meta (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO ledger_meta VALUES (1,0);
                CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, proposal_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, receipt TEXT);
            ''')

    @staticmethod
    def _bindings_table(db):
        """Inside a write transaction: create ``proposal_bindings`` when this ledger predates it, and mark the
        3.2.x CSV-import previews still pending then (see "Bound proposals")."""
        upgrade = not Ledger._has_bindings(db)
        for statement in _BINDINGS_SCHEMA:
            db.execute(statement)
        if not upgrade:
            return
        stamp = datetime.now(timezone.utc).isoformat()
        for row in db.execute('SELECT id, revision, payload FROM proposals WHERE receipt IS NULL').fetchall():
            try:
                events = json.loads(row[2]).get('events') or []
            except (ValueError, AttributeError):
                continue
            if events and _bound_source(events) is None and all(
                    isinstance(e, dict) and e.get('external_id') and e.get('kind') not in ('opening', 'reverse')
                    for e in events):
                db.execute("INSERT OR IGNORE INTO proposal_bindings(proposal_id, scope, subject, token, revision, state, "
                           "info, created_at) VALUES (?, ?, ?, '', ?, 'pending', '{}', ?)",
                           (row[0], LEGACY_CSV_SCOPE, row[0], row[1], stamp))

    @staticmethod
    def _has_bindings(db):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposal_bindings'").fetchone() is not None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _events(self, db):
        return [{**json.loads(row['payload']), '_sequence': row['sequence']} for row in db.execute('SELECT * FROM events ORDER BY sequence')]

    def state(self, as_of=None):
        if as_of is not None:
            valid_date(as_of)
        with self.connect() as db:
            db.execute('BEGIN')
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
            events = self._events(db)
            return {'revision': revision, **(replay(events) if as_of is None else replay_as_of(events, as_of))}

    def history(self):
        with self.connect() as db:
            return self._events(db)

    def proposals(self):
        with self.connect() as db:
            return [{'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'], **json.loads(row['payload'])}
                    for row in db.execute('SELECT * FROM proposals WHERE receipt IS NULL ORDER BY created_at DESC LIMIT 30')]

    def proposal(self, proposal_id):
        """Read the exact preview and durable receipt, including after a restart."""
        with self.connect() as db:
            row = db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone()
            if row is None:
                return None
            return {'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'],
                    **json.loads(row['payload']),
                    'receipt': json.loads(row['receipt']) if row['receipt'] else None}

    @contextmanager
    def writing(self):
        """The ledger's write lock (``BEGIN IMMEDIATE``) for a block; commits when it ends, rolls back on error.
        A commit that fails (a reader still holds the file) raises, and nothing of the block is kept."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._bindings_table(db)
            yield db

    @staticmethod
    def withdraw_bound(db, scope, subject):
        """Inside ``writing()``: delete every unconfirmed proposal bound to (scope, subject). Returns their ids."""
        ids = [row[0] for row in db.execute(
            "SELECT b.proposal_id FROM proposal_bindings b JOIN proposals p ON p.id = b.proposal_id "
            "WHERE b.scope=? AND b.subject=? AND p.receipt IS NULL", (scope, subject))]
        for proposal_id in ids:
            db.execute('DELETE FROM proposals WHERE id=? AND receipt IS NULL', (proposal_id,))
        db.execute("UPDATE proposal_bindings SET state='withdrawn' WHERE scope=? AND subject=? AND state='pending'",
                   (scope, subject))
        return ids

    @staticmethod
    def withdraw(db, proposal_id):
        """Inside ``writing()``: withdraw one unconfirmed proposal, bound or not, and remember that it was withdrawn
        (confirming it later answers ``StaleProposal``). True when it was pending."""
        row = db.execute('SELECT revision FROM proposals WHERE id=? AND receipt IS NULL', (proposal_id,)).fetchone()
        if row is None:
            return False
        db.execute('DELETE FROM proposals WHERE id=? AND receipt IS NULL', (proposal_id,))
        if db.execute("UPDATE proposal_bindings SET state='withdrawn' WHERE proposal_id=?", (proposal_id,)).rowcount == 0:
            db.execute("INSERT INTO proposal_bindings(proposal_id, scope, subject, token, revision, state, info, "
                       "created_at) VALUES (?, '', '', '', ?, 'withdrawn', '{}', ?)",
                       (proposal_id, row[0], _stamp()))
        return True

    @staticmethod
    def bound_subjects(db, scope):
        """Subjects of ``scope`` that still have an unconfirmed proposal."""
        return [row[0] for row in db.execute(
            "SELECT DISTINCT b.subject FROM proposal_bindings b JOIN proposals p ON p.id = b.proposal_id "
            "WHERE b.scope=? AND p.receipt IS NULL", (scope,))]

    def bindings(self, scope, subject=None):
        """Every proposal ever bound to (scope, subject) (every subject of the scope when ``subject`` is None),
        oldest first, with ``state``: confirmed, pending or discarded (withdrawn, or deleted from the ledger).
        A read: ``[]`` for a ledger without bindings, whose schema it leaves as it is."""
        with self.connect() as db:
            if not self._has_bindings(db):
                return []
            where, arguments = ('b.scope=?', (scope,)) if subject is None else ('b.scope=? AND b.subject=?', (scope, subject))
            rows = db.execute(
                "SELECT b.*, p.id AS live, p.receipt AS receipt, p.payload AS payload FROM proposal_bindings b "
                f"LEFT JOIN proposals p ON p.id = b.proposal_id WHERE {where} "
                "ORDER BY b.created_at, b.rowid", arguments).fetchall()
        out = []
        for row in rows:
            state = ('confirmed' if row['receipt'] else 'pending' if row['live'] and row['state'] == 'pending'
                     else 'discarded')
            out.append({'proposal_id': row['proposal_id'], 'subject': row['subject'], 'token': row['token'],
                        'revision': row['revision'], 'state': state, 'info': json.loads(row['info']),
                        'created_at': row['created_at'],
                        'events': json.loads(row['payload'])['events'] if state == 'pending' else None})
        return out

    def propose(self, raw_events, *, binding=None, expect_revision=None):
        """Store a preview. ``binding`` ``{scope, subject, token, info}`` binds it to the content of another
        store (see "Bound proposals"): the check must pass now, every other unconfirmed proposal of the subject
        is withdrawn, and an unconfirmed one with the same token, revision and events is returned instead of a
        copy (with ``reused: True``). ``expect_revision``: refuse (``Conflict``) unless the ledger is still at
        the revision the events were prepared against."""
        if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 1000:
            raise LedgerError("Submit between 1 and 1000 events per proposal")
        proposed = [normalize(raw) for raw in raw_events]
        owner = _bound_source(proposed)
        if owner is not None and (binding is None or binding.get('scope') != owner):
            raise LedgerError('来源以 import: 开头的记录只能由交割单导入生成（并与导入记录绑定）。请换一个来源名称。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._bindings_table(db)
            existing = self._events(db)
            before = replay(existing)
            proposed = _booked(proposed, existing)
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
            if expect_revision is not None and revision != expect_revision:
                raise StaleProposal('The ledger changed while this proposal was prepared. Prepare it again.')
            if binding is not None:
                reason, _definitive = _binding_problem(binding['scope'], binding['subject'], binding['token'])
                if reason:
                    raise StaleProposal(reason)
                reused = self._reusable(db, binding, revision, proposed, existing)
                if reused is not None:
                    return reused
                self.withdraw_bound(db, binding['scope'], binding['subject'])
            seen = {e['id']: {k: v for k, v in e.items() if k != '_sequence'} for e in existing}
            events, duplicates = [], 0
            for event in proposed:
                if event['id'] in seen:
                    if encode(_by_listing(seen[event['id']])) != encode(_by_listing(event)):
                        raise Conflict('An external ID already exists with different content')
                    duplicates += 1
                else:
                    seen[event['id']] = event
                    events.append(event)
            following = [{**event, '_sequence': len(existing) + index + 1} for index, event in enumerate(events)]
            after = replay(existing + following)
            warnings = [f"{name} {unit}: negative cash {balance}" for name, account in after['accounts'].items()
                        for unit, balance in account['cash_balances'].items() if amount(balance) < 0]
            if any(e['kind'] == 'opening' for e in events) and not before['accounts']:
                warnings.append('确认期初余额将启用交易账本。请先停用自动修改旧持仓文件的定时任务。')
            if any(e['kind'] in {'fund_buy', 'fund_sell', 'fund_value'} for e in events):
                warnings.append('基金按人民币合计估值记账，不记录单只基金份额或自动更新净值。请使用已核对的基金合计金额。')
            payload = {'events': events, 'before': before, 'after': after, 'duplicates': duplicates, 'warnings': warnings}
            proposal_id = uuid.uuid4().hex
            created = _stamp()
            db.execute('INSERT INTO proposals VALUES (?,?,?,?,NULL)', (proposal_id, revision, encode(payload), created))
            if binding is not None:
                db.execute("INSERT INTO proposal_bindings(proposal_id, scope, subject, token, revision, state, info, "
                           "created_at) VALUES (?,?,?,?,?,'pending',?,?)",
                           (proposal_id, binding['scope'], binding['subject'], binding['token'], revision,
                            encode(binding.get('info') or {}), created))
        return {'id': proposal_id, 'revision': revision, **payload}

    @staticmethod
    def _reusable(db, binding, revision, proposed, existing):
        """The subject's unconfirmed proposal when it has this token, revision and exactly these events."""
        row = db.execute(
            "SELECT p.* FROM proposal_bindings b JOIN proposals p ON p.id = b.proposal_id WHERE b.scope=? "
            "AND b.subject=? AND b.state='pending' AND p.receipt IS NULL AND b.token=? AND p.revision=?",
            (binding['scope'], binding['subject'], binding['token'], revision)).fetchone()
        if row is None:
            return None
        payload = json.loads(row['payload'])
        if payload['duplicates'] or encode(payload['events']) != encode(proposed):
            return None
        return {'id': row['id'], 'revision': row['revision'], **payload, 'reused': True}

    def confirm(self, proposal_id):
        """Book a proposal once (a repeat returns its receipt). ``StaleProposal`` (a ``Conflict``) for a proposal
        that was withdrawn or can never be confirmed: a bound one whose source changed or whose ledger revision
        passed, an unbound one of a bound source (see "Bound proposals"); such a proposal is withdrawn here."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._bindings_table(db)
            row = db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone()
            bound = db.execute('SELECT * FROM proposal_bindings WHERE proposal_id=?', (proposal_id,)).fetchone()
            if row is None:
                if bound is not None:
                    raise StaleProposal(LEGACY_CSV if bound['scope'] == LEGACY_CSV_SCOPE else WITHDRAWN)
                raise LedgerError("Proposal not found")
            if row['receipt']:
                return json.loads(row['receipt'])
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
            stale_revision = revision != row['revision']
            payload = json.loads(row['payload'])

            def withdrawn(reason):   # it can never be confirmed: withdraw it, keep that, and say why
                self.withdraw(db, proposal_id)
                db.commit()
                return StaleProposal(reason)
            if bound is not None and bound['scope'] == LEGACY_CSV_SCOPE:
                raise withdrawn(LEGACY_CSV)
            if bound is not None:
                reason, definitive = _binding_problem(bound['scope'], bound['subject'], bound['token'])
                if reason and definitive or stale_revision:
                    raise withdrawn(reason or LEDGER_CHANGED)
                if reason:
                    raise StaleProposal(reason)
                db.execute("UPDATE proposal_bindings SET state='confirmed' WHERE proposal_id=?", (proposal_id,))
            elif _bound_source(payload['events']) is not None:
                raise withdrawn(UNBOUND_IMPORT)
            elif stale_revision:
                raise Conflict("The ledger changed after this preview. Create a new preview before confirming.")
            # A proposal staged by an earlier release meets today's rules too: no B share is booked (``bookable``).
            for event in payload['events']:
                for ticker in [event.get('ticker')] + [p.get('ticker') for p in event.get('positions') or []]:
                    if ticker is not None:
                        bookable(ticker)
            for event in payload['events']:
                db.execute('INSERT INTO events(id,payload,proposal_id) VALUES (?,?,?)', (event['id'], encode(event), proposal_id))
            revision += bool(payload['events'])
            db.execute('UPDATE ledger_meta SET revision=? WHERE id=1', (revision,))
            receipt = {'proposal_id': proposal_id, 'revision': revision, 'applied': len(payload['events']),
                       'duplicates': payload['duplicates'], 'state': replay(self._events(db))}
            db.execute('UPDATE proposals SET receipt=? WHERE id=?', (encode(receipt), proposal_id))
            return receipt

    def holdings(self, as_of=None):
        return holdings_of(self.state(as_of), as_of)

    def backup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ledger.sqlite3'
            with self.connect() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            return target.read_bytes()


def holdings_of(state, as_of=None):
    """The legacy holdings shape (floats, as the engine scripts read them) of a ``state(as_of)``."""
    return {'date': as_of or state['date'], 'ledger_revision': state['revision'], 'groups': {
        name: {'cash': float(amount(account['cash_balances'].get('CNY', '0'))),
               'cash_balances': {unit: float(amount(value)) for unit, value in account['cash_balances'].items()},
               'fund': float(amount(account['fund_cny'])), 'cost_basis': float(amount(account['contributed_cny'])),
               'positions': [{**p, 'quantity': float(amount(p['quantity'])), 'cost_price': float(amount(p['cost_price']))}
                             for p in account['positions'].values()]}
        for name, account in state['accounts'].items()}}


def portfolio_directory():
    root = Path(__file__).resolve().parents[1]
    default = root.parent / 'engine' / 'portfolio' if root.name == 'dashboard' else root / 'portfolio'
    return Path(os.environ.get('PORTFOLIO_DIR') or os.environ.get('QR_PORTFOLIO_DIR') or default)


def active_ledger(directory=None):
    """The ledger of ``directory`` when it has a confirmed revision, else ``None``.

    Deciding never writes: a missing file, an empty one, one with only the
    research journal or with nothing confirmed is ``None`` without being
    created or initialised (``read_confirmed`` reads it read-only). Only a
    ledger that has its tables is opened as a ``Ledger``, whose opening then
    only reads. A file that cannot be read that way (damaged, busy) is opened
    as before, so it raises what ``Ledger`` raises.
    """
    path = Path(directory or portfolio_directory()) / 'ledger.sqlite3'
    if not path.is_file():
        return None
    try:
        if read_confirmed(directory, timeout=ACTIVE_LEDGER_WAIT) is None:
            return None
    except LedgerUnavailable:
        pass
    ledger = Ledger(path)
    if ledger.state()['revision']:
        return ledger
    return None


ACTIVE_LEDGER_WAIT = 15.0   # seconds; as long as ``Ledger.connect`` waits for a lock


# -- reading the confirmed ledger without writing --------------------------------
#
# ``active_ledger()`` opens the file for writing (``Ledger()`` initialises the
# schema), so it waits for a writer, up to 15 seconds, and creates tables. A
# reader with a time budget (a GET, the search) uses ``read_confirmed()``.

class LedgerUnavailable(Exception):
    """The ledger file is there but cannot be read now; ``reason`` is a fixed Chinese text for the person."""

    reason = '账本暂时无法读取'

    def __init__(self, reason=None):
        super().__init__(reason or self.reason)
        if reason:
            self.reason = reason


class LedgerBusy(LedgerUnavailable):
    """A lock wait (a commit in progress), or reading and replaying, ran past the time allowed."""

    reason = '账本正在写入或读取较慢，超过了本次读取的等待时间'


class LedgerDamaged(LedgerUnavailable):
    """The file is not a readable ledger: a damaged schema, or an interrupted write that needs recovery."""

    reason = '账本文件结构损坏（缺少必要的表或版本记录），需要检查或从备份恢复；不能当作空账本'


RECOVERY_NEEDED = ('账本上次写入中断，需要由应用以可写方式打开一次完成恢复（只读读取不会修复它）；'
                   '在此之前读不到持仓')
CORRUPT_FILE = '账本文件已损坏，无法读取；需要检查或从备份恢复，不能当作空账本'
OPEN_FAILED = '账本文件无法打开'
READ_FAILED = '账本文件无法读取'
# Every reason a reader is given (LedgerUnavailable.reason): fixed texts, nothing an exception said.
UNAVAILABLE_REASONS = (LedgerUnavailable.reason, LedgerBusy.reason, LedgerDamaged.reason, RECOVERY_NEEDED, CORRUPT_FILE,
                       OPEN_FAILED, READ_FAILED)


class ConfirmedLedger:
    """One consistent read of the confirmed ledger, for readers.

    ``state(as_of=None)`` and ``history()`` answer as ``Ledger``'s do, from
    this snapshot only, so code that reads a ``Ledger`` (``build_today``,
    ``read_ledger``) reads one revision. The events are shared with the cache:
    readers must not modify them.
    """

    def __init__(self, revision, events, identity):
        self.revision = revision
        self.identity = identity
        self._events = events
        self._state = None
        self._problem = None
        try:
            self._state = replay(events)
        except LedgerError as exc:
            self._problem = exc

    def state(self, as_of=None):
        if as_of is not None:
            valid_date(as_of)
            return {'revision': self.revision, **replay_as_of(self._events, as_of)}
        if self._problem is not None:
            raise LedgerError(str(self._problem))
        return {'revision': self.revision, **copy.deepcopy(self._state)}

    def history(self):
        return list(self._events)

    def holdings(self, as_of=None):
        return holdings_of(self.state(as_of), as_of)


# The latest snapshot read per ledger file and the read in progress per file.
_snapshots = {}
_reading = {}
_snapshot_lock = threading.Lock()
_IMMUTABLE_ATTEMPTS = 3
_MAX_SNAPSHOTS = 4          # ledger files whose latest snapshot is kept (one in normal use)


def _file_identity(path):
    status = path.stat()
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns


def _sidecars(path):
    return [Path(f'{path}{suffix}') for suffix in ('-wal', '-shm', '-journal')]


def _wal_header(path):
    """True when the file header says WAL (read and write versions 2)."""
    with open(path, 'rb') as handle:
        header = handle.read(20)
    return len(header) == 20 and header[18] == 2 and header[19] == 2


def _read_snapshot(path, lock_timeout):
    """``ConfirmedLedger`` or ``None`` from one read-only connection; never creates or changes a file.

    The connection is ``mode=ro``. A ledger in WAL mode with no ``-wal`` /
    ``-shm`` file is opened ``immutable=1`` as well: a read-only connection
    would create those files (and fail in a read-only directory), and their
    absence means no connection has the database open, so the main file holds
    every committed transaction. Should a writer open it during the read, its
    commits go to a new ``-wal`` file; only a checkpoint rewrites the main
    file, and that changes its size or modification time, which is checked
    after the read (the read is then repeated). A ledger in rollback-journal
    mode needs no extra files to be read.
    """
    key = str(path.absolute())
    for _ in range(_IMMUTABLE_ATTEMPTS):
        before = _file_identity(path)
        immutable = _wal_header(path) and not any(sidecar.exists() for sidecar in _sidecars(path))
        uri = path.absolute().as_uri() + ('?mode=ro&immutable=1' if immutable else '?mode=ro')
        try:
            db = sqlite3.connect(uri, uri=True, timeout=max(float(lock_timeout), 0.0), check_same_thread=False)
        except sqlite3.Error:
            raise LedgerUnavailable(OPEN_FAILED) from None
        try:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables & {'ledger_meta', 'events'}:
                return None                              # empty, or only the research journal: no ledger yet
            if not {'ledger_meta', 'events'} <= tables:
                raise LedgerDamaged()                    # one of the two without the other
            meta = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()
            count, last = db.execute('SELECT COUNT(*), MAX(sequence) FROM events').fetchone()
            if meta is None or (not meta[0] and count):
                raise LedgerDamaged()
            if not meta[0]:
                return None                              # initialised, nothing confirmed yet
            identity = (before[0], before[1], meta[0], count, last)
            with _snapshot_lock:
                cached = _snapshots.get(key)
            if cached is not None and cached.identity == identity:
                return cached
            events = [{**json.loads(row['payload']), '_sequence': row['sequence']}
                      for row in db.execute('SELECT sequence, payload FROM events ORDER BY sequence')]
        except sqlite3.OperationalError as exc:
            text = str(exc)
            if 'locked' in text or 'busy' in text:
                raise LedgerBusy() from None
            if 'readonly' in text or 'read-only' in text:
                raise LedgerDamaged(RECOVERY_NEEDED) from None   # a hot journal only a writer can roll back
            raise LedgerUnavailable(READ_FAILED) from None
        except sqlite3.DatabaseError:                        # not a database, or a malformed one
            raise LedgerDamaged(CORRUPT_FILE) from None
        except (ValueError, TypeError, KeyError):            # a stored event that is not JSON
            raise LedgerDamaged(CORRUPT_FILE) from None
        finally:
            db.close()
        if immutable and (_file_identity(path) != before or any(sidecar.exists() for sidecar in _sidecars(path))):
            continue                                     # a writer may have checkpointed during the read
        snapshot = ConfirmedLedger(meta[0], events, identity)
        with _snapshot_lock:
            _snapshots.pop(key, None)
            _snapshots[key] = snapshot
            while len(_snapshots) > _MAX_SNAPSHOTS:
                del _snapshots[next(iter(_snapshots))]
        return snapshot
    raise LedgerBusy()


class ReadOnlyOpen:
    """How to open an existing SQLite file for reading so that no file is created or changed.

    ``uri`` is ``mode=ro``, and also ``immutable=1`` for a WAL file with no
    ``-wal`` / ``-shm`` file (see ``_read_snapshot``: a read-only connection
    would create both and leave them behind). An immutable read takes no lock,
    so a writer that checkpoints meanwhile is seen afterwards: ``moved()`` is
    true when the main file's size or time changed or a sidecar appeared, and
    what was read must then be treated as busy (it may mix two states).
    """

    def __init__(self, path):
        self.path = Path(path)
        self.before = _file_identity(self.path)
        self.immutable = _wal_header(self.path) and not any(sidecar.exists() for sidecar in _sidecars(self.path))
        self.uri = self.path.absolute().as_uri() + ('?mode=ro&immutable=1' if self.immutable else '?mode=ro')

    def connect(self, timeout):
        return sqlite3.connect(self.uri, uri=True, timeout=max(float(timeout), 0.0), check_same_thread=False)

    def moved(self):
        return self.immutable and (_file_identity(self.path) != self.before
                                   or any(sidecar.exists() for sidecar in _sidecars(self.path)))


@contextmanager
def read_only_connection(path, *, timeout):
    """One read transaction on the existing SQLite file ``path`` that never creates or changes a file.

    Rows are ``sqlite3.Row``. Raises what ``sqlite3.connect`` and the reads
    raise, and ``LedgerBusy`` after the block when a writer checkpointed
    during an immutable read (``ReadOnlyOpen.moved``).
    """
    target = ReadOnlyOpen(path)
    db = target.connect(timeout)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()
    if target.moved():
        raise LedgerBusy()


class _Read:
    def __init__(self):
        self.done = threading.Event()
        self.result = None
        self.error = None


def read_confirmed(directory=None, *, timeout):
    """The active ledger as a ``ConfirmedLedger``, read without writing; ``None`` without one.

    Never creates or changes a file (no ``-wal`` / ``-shm`` either) and works in
    a read-only directory. ``timeout`` bounds the whole call: a lock wait (a
    commit in progress; a writer's open transaction does not block a reader),
    reading the events and replaying them. Past it ``LedgerBusy`` is raised and
    the read goes on in the background: its snapshot is kept per file and
    revision, so the next call returns at once. ``LedgerDamaged`` when the file
    is not a readable ledger (tables or the revision record missing, or an
    interrupted write that only a writer can recover): never mistaken for an
    empty ledger. ``None`` for an empty file, or one with no confirmed revision.
    """
    path = Path(directory or portfolio_directory()) / 'ledger.sqlite3'
    if not path.is_file():
        return None
    timeout = max(float(timeout), 0.0)
    key = str(path.absolute())
    with _snapshot_lock:
        job = _reading.get(key)
        start = job is None
        if start:
            job = _reading[key] = _Read()

    def work():
        try:
            job.result = _read_snapshot(path, timeout)
        except LedgerUnavailable as exc:
            job.error = (type(exc), exc.reason)
        except Exception:                                # nothing may escape a reader thread
            job.error = (LedgerDamaged, LedgerDamaged.reason)
        finally:
            with _snapshot_lock:
                if _reading.get(key) is job:
                    del _reading[key]
            job.done.set()
    if start:
        threading.Thread(target=work, name='ledger-read', daemon=True).start()
    if not job.done.wait(timeout):
        raise LedgerBusy()
    if job.error is not None:
        kind, reason = job.error
        raise kind(reason)
    return job.result


def confirmed_state(directory=None, *, timeout, as_of=None):
    """``read_confirmed(...).state(as_of)``: the confirmed state (as ``Ledger.state(as_of)``), or ``None``
    without a ledger.

    ``as_of`` (``YYYY-MM-DD``) replays only what is in force at the end of
    that day (``replay_as_of``, as the Today page does). Raises what
    ``read_confirmed`` raises, and ``LedgerError`` when the events do not
    replay or ``as_of`` is not a date.
    """
    if as_of is not None:
        valid_date(as_of)
    snapshot = read_confirmed(directory, timeout=timeout)
    return None if snapshot is None else snapshot.state(as_of)


MISSING_CONTRIBUTION = ('旧持仓文件没有记录部分账户的累计净投入（cost_basis）。请填写这些账户历年转入减转出的本金合计'
                        '（人民币）后再预览；不会按 0 记账。')


class MissingContribution(LedgerError):
    """Legacy holdings without ``cost_basis`` for ``accounts``: it is asked for, as the setup wizard does. Zero
    would make the whole balance look like a gain."""

    def __init__(self, accounts):
        super().__init__(MISSING_CONTRIBUTION)
        self.accounts = accounts


def opening_events(directory, as_of, contributions=None):
    """Preview a dated legacy holdings file; never migrate on application startup.

    ``contributions`` gives the 累计净投入 (CNY) of the accounts whose ``cost_basis`` the file lacks; without
    one for each of them, ``MissingContribution`` names them."""
    valid_date(as_of)
    paths = []
    for path in (Path(directory) / 'holdings').glob('*.json'):
        try:
            valid_date(path.stem)
        except LedgerError:
            continue
        if path.stem <= as_of:
            paths.append(path)
    if not paths:
        raise LedgerError('No dated holdings found. Enter an opening balance manually.')
    selected = max(paths)
    # Numbers are read as exact decimals: a binary float would change a quantity such as 1.1234567890123456789.
    try:
        data = json.loads(selected.read_text(encoding='utf-8'), parse_float=Decimal)
    except (ValueError, UnicodeDecodeError):
        raise LedgerError(f'旧持仓文件 {selected.name} 不是有效的 JSON，请修正后再预览。') from None
    groups = data.get('groups', {}) if isinstance(data, dict) else None
    shaped = isinstance(groups, dict) and all(
        isinstance(group, dict) and isinstance(group.get('cash_balances', {}), dict)
        and isinstance(group.get('positions', []), list) and all(isinstance(p, dict) for p in group.get('positions', []))
        for group in groups.values())
    if not shaped:
        raise LedgerError(f'旧持仓文件 {selected.name} 的格式不对：需要 groups 对象。')
    given = contributions or {}
    recorded = {name: group.get('cost_basis') for name, group in groups.items()}
    missing = [name for name, value in recorded.items()
               if value is None and (given.get(name) is None or not str(given[name]).strip())]
    if missing:
        raise MissingContribution(missing)
    events = []
    for name, group in groups.items():
        balances = group.get('cash_balances', {'CNY': group.get('cash', 0)})
        if 'cash' in group and amount(group['cash']) != amount(balances.get('CNY', 0)):
            raise LedgerError('Legacy cash disagrees with CNY cash_balances; reconcile it before migration')
        positions = [{k: p[k] for k in ('ticker', 'name', 'quantity', 'cost_price') if k in p}
                     for p in group.get('positions', []) if amount(p.get('quantity', 0)) != 0]
        events.append({'kind': 'opening', 'date': as_of, 'account': name, 'cash_balances': balances,
                       'positions': positions,
                       'contributed_cny': recorded[name] if recorded[name] is not None else given[name],
                       'fund_cny': group.get('fund', 0), 'source': 'legacy-opening',
                       'external_id': f'{as_of}:{name}', 'note': f'Opening balance from holdings dated {selected.stem}'})
    return events


CSV_COLUMNS = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee',
               'amount', 'fx_rate', 'to_account', 'to_currency', 'received_amount', 'ratio', 'note']


def csv_events(content, source):
    import csv
    import io
    label(source, 'source')
    if not isinstance(content, str) or len(content.encode()) > 2_000_000:
        raise LedgerError('CSV must be UTF-8 text smaller than 2 MB')
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise LedgerError('CSV requires unique column headers')
    if not {'external_id', 'date', 'kind', 'account'} <= set(reader.fieldnames) or set(reader.fieldnames) - set(CSV_COLUMNS):
        raise LedgerError('Use the standard CSV template and include an external_id for every row')
    events = []
    for line, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise LedgerError(f'CSV row {line} has the wrong number of columns')
        event = {key: value.strip() for key, value in row.items() if value.strip()}
        if not event.get('external_id'):
            raise LedgerError(f'CSV row {line} needs an external_id')
        event['source'] = source
        if event.get('kind') in {'opening', 'reverse'}:
            raise LedgerError('Use the opening or reversal preview for these event types')
        events.append(event)
        if len(events) > 1000:
            raise LedgerError('Import at most 1000 CSV rows at a time')
    return events
