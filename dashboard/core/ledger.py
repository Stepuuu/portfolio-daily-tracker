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
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

CURRENCIES = {"CNY", "HKD", "USD"}
MARKETS = {"SHA": "CNY", "SHE": "CNY", "HKG": "HKD", "NASDAQ": "USD", "NYSE": "USD"}
KINDS = {"opening", "buy", "sell", "deposit", "withdrawal", "dividend", "fee", "transfer", "fx", "split", "reverse", "fund_value", "fund_buy", "fund_sell"}


class LedgerError(ValueError):
    pass


class Conflict(LedgerError):
    pass


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
        event['ticker'] = instrument(event['ticker'])
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
            positions.append(_position(instrument(p.get('ticker')), p.get('quantity'), p.get('cost_price'),
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
        elif kind in {'deposit', 'withdrawal', 'dividend', 'fee'}:
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


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ledger_meta (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO ledger_meta VALUES (1,0);
                CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, proposal_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, receipt TEXT);
            ''')

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
            return {'revision': revision, **replay([e for e in events if as_of is None or e['date'] <= as_of])}

    def history(self):
        with self.connect() as db:
            return self._events(db)

    def proposals(self):
        with self.connect() as db:
            return [{'id': row['id'], 'revision': row['revision'], 'created_at': row['created_at'], **json.loads(row['payload'])}
                    for row in db.execute('SELECT * FROM proposals WHERE receipt IS NULL ORDER BY created_at DESC LIMIT 30')]

    def propose(self, raw_events):
        if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= 1000:
            raise LedgerError("Submit between 1 and 1000 events per proposal")
        proposed = [normalize(raw) for raw in raw_events]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self._events(db)
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
            seen = {e['id']: {k: v for k, v in e.items() if k != '_sequence'} for e in existing}
            events, duplicates = [], 0
            for event in proposed:
                if event['id'] in seen:
                    if encode(seen[event['id']]) != encode(event):
                        raise Conflict('An external ID already exists with different content')
                    duplicates += 1
                else:
                    seen[event['id']] = event
                    events.append(event)
            before = replay(existing)
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
            db.execute('INSERT INTO proposals VALUES (?,?,?,?,NULL)',
                       (proposal_id, revision, encode(payload), datetime.now(timezone.utc).isoformat()))
        return {'id': proposal_id, 'revision': revision, **payload}

    def confirm(self, proposal_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM proposals WHERE id=?', (proposal_id,)).fetchone()
            if row is None:
                raise LedgerError("Proposal not found")
            if row['receipt']:
                return json.loads(row['receipt'])
            revision = db.execute('SELECT revision FROM ledger_meta WHERE id=1').fetchone()[0]
            if revision != row['revision']:
                raise Conflict("The ledger changed after this preview. Create a new preview before confirming.")
            payload = json.loads(row['payload'])
            for event in payload['events']:
                db.execute('INSERT INTO events(id,payload,proposal_id) VALUES (?,?,?)', (event['id'], encode(event), proposal_id))
            revision += bool(payload['events'])
            db.execute('UPDATE ledger_meta SET revision=? WHERE id=1', (revision,))
            receipt = {'proposal_id': proposal_id, 'revision': revision, 'applied': len(payload['events']),
                       'duplicates': payload['duplicates'], 'state': replay(self._events(db))}
            db.execute('UPDATE proposals SET receipt=? WHERE id=?', (encode(receipt), proposal_id))
            return receipt

    def holdings(self, as_of=None):
        state = self.state(as_of)
        return {'date': as_of or state['date'], 'ledger_revision': state['revision'], 'groups': {
            name: {'cash': float(amount(account['cash_balances'].get('CNY', '0'))),
                   'cash_balances': {unit: float(amount(value)) for unit, value in account['cash_balances'].items()},
                   'fund': float(amount(account['fund_cny'])), 'cost_basis': float(amount(account['contributed_cny'])),
                   'positions': [{**p, 'quantity': float(amount(p['quantity'])), 'cost_price': float(amount(p['cost_price']))}
                                 for p in account['positions'].values()]}
            for name, account in state['accounts'].items()}}

    def backup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ledger.sqlite3'
            with self.connect() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            return target.read_bytes()


def portfolio_directory():
    root = Path(__file__).resolve().parents[1]
    default = root.parent / 'engine' / 'portfolio' if root.name == 'dashboard' else root / 'portfolio'
    return Path(os.environ.get('PORTFOLIO_DIR') or os.environ.get('QR_PORTFOLIO_DIR') or default)


def active_ledger(directory=None):
    path = Path(directory or portfolio_directory()) / 'ledger.sqlite3'
    if path.is_file():
        ledger = Ledger(path)
        if ledger.state()['revision']:
            return ledger
    return None


def opening_events(directory, as_of):
    """Preview a dated legacy holdings file; never migrate on application startup."""
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
    data = json.loads(selected.read_text())
    events = []
    for name, group in data.get('groups', {}).items():
        balances = group.get('cash_balances', {'CNY': group.get('cash', 0)})
        if 'cash' in group and amount(group['cash']) != amount(balances.get('CNY', 0)):
            raise LedgerError('Legacy cash disagrees with CNY cash_balances; reconcile it before migration')
        positions = [{k: p[k] for k in ('ticker', 'name', 'quantity', 'cost_price') if k in p}
                     for p in group.get('positions', []) if amount(p.get('quantity', 0)) != 0]
        events.append({'kind': 'opening', 'date': as_of, 'account': name, 'cash_balances': balances,
                       'positions': positions, 'contributed_cny': group.get('cost_basis', 0),
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
