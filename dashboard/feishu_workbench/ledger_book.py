"""The daily card on the confirmed ledger: list items become ledger events, a preview is a ledger proposal.

``PortfolioAdapter`` hands its work here while ``ledger.sqlite3`` has a confirmed revision; without one the card
keeps editing the dated holdings files. The design is in docs/FEISHU_WORKBENCH.md ("With the transaction
ledger"). In short:

* Everything runs in this process on ``core.ledger``: reads through ``read_confirmed`` (never creates or changes
  a file), writes through ``Ledger.propose`` and ``Ledger.confirm``. The card works while the backend is
  stopped, and the ledger's own rules guard every booking: a proposal is confirmed only at the revision it was
  previewed against, every later event is replayed, a repeated confirmation returns the first receipt.
* ``plan`` turns the list into events in this order: trades and transfers in list order (a 港股通 trade is the
  importer's ``fx`` + HKD trade, ``core.importers.transactions._connect``), then 本金更新, 基金估值 and last
  现金对账 (the difference to the ledger's balance after everything else, booked as ``interest`` or ``fee``).
  Every event has the source ``feishu-card`` and an external id made from its list item, so a list item that
  was booked once is never booked again.
* ``preview`` withdraws the same person's earlier card proposals (so do edits of the list, ``withdraw_pending``),
  stores the new ledger proposal when there is something new to book and marks it to expire like a 记一笔
  preview; its card text goes to the adapter's operations store under the proposal id. ``confirm`` keys the
  operation on that id, so every tap on one proposal meets one operation and receipt. Only the card confirms
  its proposals: the web app lists them but its confirm endpoint refuses them (``backend.api.ledger``), since
  only the card's confirmation completes the list and queues the day's report.
* ``run_report`` runs the adapter's pipeline child for the list's date. That child shares the portfolio write
  lock and the ``pipeline-state`` checkpoint with the backend's close snapshot and, like it, pushes the report
  only when ``feishu_chat_id`` is configured. Before the day's closes are final (``closes_final_at``) the job
  waits instead, with its time stored; the card service runs it then (``due_reports``).

Messages are Chinese and safe to show; log lines name exception types only.
"""
from __future__ import annotations

from datetime import date, datetime, time as Time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import NamedTuple
import uuid
from zoneinfo import ZoneInfo

from core.importers.transactions import CENT, HKD_CNY_BAND, exact_price, ledger_product, ledger_reason
from core.ledger import (Conflict, Ledger, LedgerError, LedgerUnavailable, StaleProposal, amount, decimal_text, encode,
                         normalize, portfolio_directory, read_confirmed, replay)
# The spelling ``Ledger.propose`` books a ticker under, and how it compares a fill sent again: the card's own
# balances must be the ones the proposal will show.
from core.ledger import _booked, _by_listing

from .portfolio import PortfolioError, StalePreview, _MARKETS, _decimal, _encode

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo('Asia/Shanghai')
SOURCE = 'feishu-card'
READ_TIMEOUT = 15.0
# The card confirms its own preview for 30 minutes (as the holdings previews); in the web's 待确认 it stays as
# long as a 记一笔 preview does (backend/api/ledger.py MANUAL_PREVIEW_LIFETIME, the same proposal_expiry table).
CARD_PREVIEW_SECONDS = 1800
PREVIEW_LIFETIME = timedelta(hours=2)
# A day's snapshot and report are made again until 09:00 the next morning, the close snapshot's own limit
# (core/routines/close_snapshot.RETRY_UNTIL): a later run could value the next session's prices.
REPORT_UNTIL = Time(9, 0)
MAX_ITEMS = 30
MAX_NOTE = 240

# What the card books, by list operation. ``correction`` is the menu entry that only explains the refusal.
OPERATIONS = ('buy', 'sell', 'deposit', 'withdraw', 'set_cash', 'set_fund', 'set_capital')
CORRECTIONS = ('correction', 'set_cost_basis', 'set_quantity', 'set_cost_price')
ITEM_FIELDS = {'id', 'account', 'operation', 'ticker', 'quantity', 'price', 'fee', 'currency', 'amount', 'name',
               'principal_cny', 'settlement_cny', 'fee_currency', 'reconciliation_note', 'allow_margin',
               'capital_mode', 'principal_only_confirmed'}

CORRECTION = ('账本不直接改写持仓数量、成本价或累计净投入（本金）。请在网页 账本 › 流水 找到原来的记录点“冲销”，'
              '再用 记一笔 录入正确的记录；累计净投入只随资金转入、转出改变。')
STALE = '账本在预览之后有了变化（例如网页里记了一笔），这份预览已作废，没有入账。'
UNREADABLE = '无法确认交易账本状态，已暂停旧持仓更新。'
OTHER_DIRECTORY = ('工作台的组合目录与 PORTFOLIO_DIR 不是同一个目录，账户费率和交易计划会从别处读取，因此暂停记账。'
                   '请让伴随服务与后端使用同一个组合目录。')
BOOKED_DIFFERENTLY = '这一项已经入账，但内容与账本中的记录不同；请删除这一项。如需更正，请在网页 流水 冲销后重新录入。'
REVERSED = '这一项入账后已在网页 流水 中冲销，同一项不会再次入账；如需重新记录，请删除这一项后重新添加。'
CONNECT_CASH = ('人民币现金 {balance} CNY 不足以支付这笔港股通买入的 {needed} CNY（含手续费）；'
                '如确实使用融资，请在“现金不足时”选择允许融资。')
BACKDATED = '这是 {day} 的补记：按账本规则，补记不会自动重做当日以前的快照和日报。'
UNBOOKED_OLD = '账本没有新增记录；{day} 的快照和日报只在次日 09:00 前重做，这次不会重新生成。'
# A report that must wait for the day's closes: the card service runs the job itself at that time (``due_reports``,
# ``Workbench.release_due_reports``), and the close snapshot may make it first; either way it is made once.
# ``reason`` is the close snapshot's own words for the wait (``close_snapshot.wait_reason``): 等待港股收盘价确定，16:10
# 后记入; 等待美股收盘价确定，次日 05:30 后记入 (New York's evening session, which a snapshot made then waits for).
WAITING = ('{booked}{reason}；{day} 的日报会在那之后自动生成（同一持仓不会重复生成或推送），'
           '也可以届时在“日报”里点“重试日报”。')
EXPIRED = '{day} 的日报没能在次日 09:00 前生成，之后不再自动重试；账本记录不受影响。'
PENDING = '账本已入账，日报尚未完成；可以在“日报”里重试，不会重复入账。'
SAVED = '日报已按入账后的持仓生成并保存；未配置飞书推送目标（feishu_chat_id），按收盘流程的规则不推送。'
UNCHANGED = '账本没有新增记录，{day} 的日报已经完成，不会重做或再次推送。'
REVERSED_LATER = '{day} · 已入账 {applied} 笔账本记录，其中 {count} 笔后来已在网页 流水 中冲销'
CLOSE_WAIT = re.compile(r'CLOSE_NOT_FINAL_UNTIL=(\S+)(?: MARKETS=([A-Z,]+))?(?: CHECK_AT=(\S+))?')
# A wait the engine names is never shorter than this, so a job can never be run again at once.
RECHECK = timedelta(minutes=1)
CHINESE = re.compile(r'[一-鿿]')
# The ledger's general notes the card already says in the item's own line ("基金估值，不是申购或赎回").
_SAID_ELSEWHERE = ('基金按人民币合计估值记账',)


def _number(value, *, positive=False, nonnegative=False):
    """A typed number as an exact ``Decimal`` (the card's own rules and messages)."""
    return _decimal(value.strip() if isinstance(value, str) else value, positive=positive, nonnegative=nonnegative)


def _text(value):
    return decimal_text(value)


def _shown(value):
    """Decimal text as the web shows it: more than 8 decimal places read ``≈`` with 8 places."""
    number = amount(value)
    exponent = number.normalize().as_tuple().exponent
    if isinstance(exponent, int) and exponent < -8:
        return '≈' + decimal_text(number.quantize(Decimal('1e-8'), rounding=ROUND_HALF_UP))
    return decimal_text(number)


def _signed(value):
    return ('+' if value > 0 else '') + _shown(value)


def ledger_ticker(value):
    """A code typed for a new purchase: exchange-qualified, Hong Kong in the ledger's five digits."""
    if not isinstance(value, str):
        raise PortfolioError('请选择含交易所前缀的证券代码。')
    value = value.strip().upper()
    match = re.fullmatch(r'(SHA|SHE|HKG|NASDAQ|NYSE):([A-Z0-9][A-Z0-9.\-]{0,19})', value)
    if not match:
        raise PortfolioError('证券代码必须明确，例如 NASDAQ:AAPL；不接受名称或模糊匹配。')
    market, code = match.groups()
    if market in {'SHA', 'SHE'} and not re.fullmatch(r'\d{6}', code):
        raise PortfolioError('A 股代码需要交易所前缀和六位数字。')
    if market == 'HKG':
        if not re.fullmatch(r'\d{4,5}', code) or int(code) == 0:
            raise PortfolioError('港股代码需要 HKG 前缀和四至五位数字。')
        code = f'{int(code):05d}'
    return market + ':' + code


def _entry_prefix(history, day):
    """The events a new event dated ``day`` is checked after (``core.ledger.replay_for_entry``)."""
    reversed_ids = {event.get('target_id') for event in history if event['kind'] == 'reverse'}
    return [event for event in history
            if event['date'] <= day and event['kind'] != 'reverse' and event['id'] not in reversed_ids]


def _groups(state):
    """A ledger state in the holdings shape the card's lists read (positions per account, exact text)."""
    return {'groups': {name: {
        'cash_balances': dict(account['cash_balances']), 'fund': account['fund_cny'],
        'cost_basis': account['contributed_cny'],
        'positions': [{'ticker': ticker, 'name': position.get('name') or ticker, 'quantity': position['quantity'],
                       'cost_price': position['cost_price']} for ticker, position in account['positions'].items()]}
        for name, account in state['accounts'].items()}}


def _identifier(event):
    """The id ``core.ledger.normalize`` gives a card event: from its source and external id."""
    return hashlib.sha256(encode([SOURCE, event['external_id']]).encode()).hexdigest()


def _now():
    """The card service's clock (aware, Shanghai); the one place the report step reads the time."""
    return datetime.now(SHANGHAI)


def report_window_open(day, now=None):
    """Whether the day's snapshot and report may be made again now (until 09:00 the next morning)."""
    now = now or _now()
    return now < datetime.combine(date.fromisoformat(day) + timedelta(days=1), REPORT_UNTIL, tzinfo=SHANGHAI)


class Wait(NamedTuple):
    """A report that waits: until when, for the closes of which markets (calendar markets: CN, HK, US)."""
    until: datetime
    markets: tuple


def _reason(wait, day):
    """The close snapshot's words for ``wait`` (``等待港股收盘价确定，16:10 后记入``)."""
    from core.routines.close_snapshot import wait_reason
    return wait_reason(wait.markets, wait.until, day)


def closes_final_at(state, day, moment):
    """When a snapshot of ``day`` made at ``moment`` can book the closes of the markets ``state`` holds, if that
    is still to come (a ``Wait``); else None.

    The snapshot engine's own rule decides, from its one definition (``core.market.calendar.snapshot_wait``, which
    ``portfolio_snapshot._close_verdict`` asks too): per market a position is held in (``instruments.parse``, as
    the engine reads a holding), the day books the session that ends on that Shanghai date (New York's of the
    night before), and a session of that market still under way at ``moment`` (New York's evening session) is
    waited for as well; each until its close is final (``close_settled``, early closes included). A snapshot made
    earlier would record the previous session's prices as the day's close.
    """
    from core.market.calendar import SessionClock, snapshot_wait
    from core.market.instruments import InstrumentError, parse
    markets = set()
    for account in state['accounts'].values():
        for ticker in account['positions']:
            try:
                markets.add(parse(ticker).market)
            except InstrumentError:
                continue
    clock, finals = SessionClock(), {}
    for market in sorted(markets):
        waiting = snapshot_wait(market, day, moment)
        final = clock.close_settled(market, waiting[0]) if waiting is not None else None
        if final is not None:
            finals[market] = final.astimezone(SHANGHAI)
    return Wait(max(finals.values()), tuple(sorted(finals))) if finals else None


def waits_until(adapter, day, now=None):
    """The ``Wait`` of the day's report, if its closes are still to come (``closes_final_at``); else None."""
    book = adapter._book()
    if book is None:
        return None
    now = now or _now()
    try:
        until = closes_final_at(book.state_on(day), day, now)
    except PortfolioError:
        raise
    except Exception as exc:   # without the calendar the snapshot engine's own close check still applies
        logger.warning('the market calendar could not be read: %s', type(exc).__name__)
        return None
    return until if until is not None and now < until.until else None


_PREVIEWS_TABLE = ('CREATE TABLE IF NOT EXISTS ledger_previews (proposal_id TEXT PRIMARY KEY, owner TEXT NOT NULL, '
                   'created REAL NOT NULL)')


def _owner_key(owner):
    return hashlib.sha256(owner.encode()).hexdigest()[:24] if isinstance(owner, str) and owner else None


def withdraw_pending(adapter, owner, keep=None):
    """Withdraw ``owner``'s card proposals that are still pending, except ``keep``.

    A list that is edited, cleared or previewed again no longer stands behind its earlier proposal. One whose
    confirmation already started is left to that confirmation; a withdrawn proposal can only be refused, never
    booked. Raises ``PortfolioError`` when the ledger cannot be written, so an edit never goes ahead with the
    earlier proposal still confirmable.
    """
    key = _owner_key(owner)
    if key is None:
        return
    with adapter._db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_previews'").fetchone():
            return   # the card never previewed into a ledger here
        older = [row[0] for row in db.execute(
            'SELECT proposal_id FROM ledger_previews WHERE owner=? AND proposal_id!=? '
            'AND proposal_id NOT IN (SELECT preview_id FROM operations)', (key, keep or ''))]
    if not older:
        return
    path = adapter.portfolio_dir / 'ledger.sqlite3'
    if path.is_file():
        try:
            with Ledger(path).writing() as db:
                for old in older:
                    Ledger.withdraw(db, old)
        except Exception as exc:
            logger.warning('an earlier card proposal could not be withdrawn: %s', type(exc).__name__)
            raise PortfolioError('暂时无法撤回这份清单之前的预览，清单没有修改；请稍后再试。') from None
    with adapter._db() as db:
        db.executemany('DELETE FROM ledger_previews WHERE proposal_id=?', [(old,) for old in older])


def receipt_notice(receipt, report, wait=None):
    """What a ledger receipt says under its summary: whether anything was booked and whether the report follows
    now or, when the day's closes are still to come (``wait``), automatically after them."""
    booked = bool(receipt.get('applied'))
    if report and wait is not None:
        return WAITING.format(booked='账本已入账。' if booked else '账本没有新增记录。', day=receipt['date'],
                              reason=_reason(wait, receipt['date']))
    if report:
        return ('账本已入账。' if booked else '账本没有新增记录。') + '日报正在更新，可从“日报”查看进度。'
    return (BACKDATED if booked else UNBOOKED_OLD).format(day=receipt['date'])


class _Item:
    """One list item while it is planned: its number, its events and the line the preview shows."""

    def __init__(self, number, item):
        self.number, self.item = number, item
        known = item if isinstance(item, dict) else {}
        self.account, self.operation = known.get('account'), known.get('operation')
        self.events, self.line, self.estimated = [], '', False
        # The CNY a 港股通 buy without margin must find in cash before its ``fx`` (``LedgerBook._covered``).
        self.needs_cny = None

    def refuse(self, message):
        return PortfolioError(f'第 {self.number} 项：{message}')

    def event(self, part, **fields):
        raw = {'date': None, 'account': self.account, 'source': SOURCE,
               'external_id': f"{self.item['id']}:{part}", **{k: v for k, v in fields.items() if v is not None}}
        self.events.append(raw)
        return raw


class LedgerBook:
    """The card's view of one confirmed ledger, read once when it is opened (``open``)."""

    def __init__(self, adapter, snapshot):
        self.adapter = adapter
        self.snapshot = snapshot
        self.path = adapter.portfolio_dir / 'ledger.sqlite3'

    @classmethod
    def open(cls, adapter):
        """The book while ``adapter``'s portfolio has a confirmed ledger, else None; never writes."""
        try:
            snapshot = read_confirmed(adapter.portfolio_dir, timeout=READ_TIMEOUT)
        except LedgerUnavailable:
            raise PortfolioError(UNREADABLE) from None
        if snapshot is None:
            return None
        if Path(portfolio_directory()).resolve() != Path(adapter.portfolio_dir).resolve():
            raise PortfolioError(OTHER_DIRECTORY)
        return cls(adapter, snapshot)

    def _reload(self):
        fresh = LedgerBook.open(self.adapter)
        if fresh is None:
            raise PortfolioError(UNREADABLE)
        self.snapshot = fresh.snapshot

    # -- reading -------------------------------------------------------------------------------

    def state(self):
        try:
            return self.snapshot.state()
        except LedgerError:
            raise PortfolioError('交易账本的记录无法回放，请在网页中检查账本。') from None

    def overview(self):
        state = self.state()
        accounts, positions = [], []
        for name, account in state['accounts'].items():
            accounts.append({'label': name, 'value': name, 'cash_balances': dict(account['cash_balances']),
                             'fund': account['fund_cny'], 'cost_basis': account['contributed_cny']})
            for ticker, position in account['positions'].items():
                positions.append({'label': position.get('name') or ticker, 'value': ticker, 'account': name,
                                  'ticker': ticker, 'quantity': position['quantity'],
                                  'cost_price': position['cost_price'], 'currency': position['currency']})
        result = {'accounts': accounts, 'groups': accounts, 'positions': positions, 'ledger': True,
                  'date': state['date'], 'as_of': state['date'],
                  'summary': f'{len(accounts)} 个账户，{len(positions)} 项持仓；来自已确认的交易账本。',
                  'total': '等待日报估值', 'cash': '见账户余额', 'change': '暂无当日估值'}
        self._valuation(result, state)
        return result

    def _valuation(self, result, state):
        """Market values from the latest snapshot, only when it was made from exactly this ledger revision."""
        folder = self.adapter.portfolio_dir / 'snapshots'
        try:
            files = sorted(path for path in folder.glob('*.json') if re.fullmatch(r'\d{4}-\d{2}-\d{2}', path.stem))
            if not files or files[-1].is_symlink() or files[-1].stat().st_size > 10_000_000:
                return
            snapshot = json.loads(files[-1].read_text())
            if (snapshot.get('ledger_revision') != self.snapshot.revision or not state['date']
                    or str(snapshot.get('date')) < state['date']
                    or set(snapshot.get('groups', {})) != set(state['accounts'])):
                return
            money = lambda value: f"¥{_decimal(value):,.2f}"
            summary = snapshot['summary']
            result['total'] = money(summary['total_value'])
            change = summary.get('market_daily_change')
            result['change'] = money(change) if change is not None else '暂无当日变动'
            result['as_of'] = snapshot.get('generated_at', snapshot['date'])
            result['notice'] = '人民币估值来自已保存的日报快照；打开本页不会刷新行情。'
            for position in result['positions']:
                rows = snapshot['groups'][position['account']].get('positions', [])
                match = next((p for p in rows if p.get('ticker') == position['ticker']), None)
                if match and match.get('market_value_cny') is not None:
                    position['market_value'] = money(match['market_value_cny'])
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            # Balances stay readable when a snapshot cannot give a trustworthy valuation.
            return

    # -- planning ------------------------------------------------------------------------------

    def state_on(self, day):
        """The ledger as a new entry dated ``day`` is checked against it (``replay_for_entry``)."""
        try:
            return replay(_entry_prefix(self.snapshot.history(), day))
        except LedgerError:
            raise PortfolioError('交易账本的记录无法回放，请在网页中检查账本。') from None

    def holdings_after(self, day, items):
        """Holdings on ``day`` once ``items`` are applied, in the holdings shape (``groups``)."""
        if items:
            return _groups(self.plan(day, items)['state'])
        try:
            return _groups(replay(_entry_prefix(self.snapshot.history(), day)))
        except LedgerError:
            raise PortfolioError('交易账本的记录无法回放，请在网页中检查账本。') from None

    def check(self, day, items):
        if items:
            self.plan(day, items)
        else:
            self.holdings_after(day, [])

    def plan_batch(self, day, items):
        plan = self.plan(day, items)
        return {'after': _groups(plan['state']), 'day': day, 'saved_day': day, 'changes': plan['lines'],
                'items': json.loads(_encode(items)), 'events': plan['events']}

    def plan(self, day, items):
        """The ledger events of ``items`` dated ``day`` and what the preview says of each; nothing is written.

        Returns ``{'events', 'lines', 'estimated', 'state'}``: the raw events in booking order, one line per
        item, the numbers of the items whose fee was estimated, and the state after every event (``replay``
        of the events a new event dated ``day`` is checked after, plus these).
        """
        if not isinstance(items, list) or not items or len(items) > MAX_ITEMS:
            raise PortfolioError(f'每次更新请添加 1–{MAX_ITEMS} 项变动。')
        history = self.snapshot.history()
        prefix = _entry_prefix(history, day)
        try:
            base = replay(prefix)
        except LedgerError:
            raise PortfolioError('交易账本的记录无法回放，请在网页中检查账本。') from None
        planned = [_Item(number, item) for number, item in enumerate(items, 1)]
        for entry in planned:
            self._validate(entry, base)
        self._exclusive(planned)
        trades = [entry for entry in planned if entry.operation in {'buy', 'sell', 'deposit', 'withdraw'}]
        for entry in trades:
            (self._trade if entry.operation in {'buy', 'sell'} else self._flow)(entry, day, base)
        state = self._settle(history, prefix, trades, day)
        self._covered(history, prefix, trades, day)
        capital = [entry for entry in planned if entry.operation == 'set_capital']
        for entry in capital:
            self._capital(entry, state)
        if capital:
            state = self._settle(history, prefix, trades + capital, day)
        funds = [entry for entry in planned if entry.operation == 'set_fund']
        for entry in funds:
            self._fund(entry, state)
        cash = [entry for entry in planned if entry.operation == 'set_cash']
        funded = {(entry.account, entry.item.get('currency')) for entry in trades
                  if entry.operation in {'deposit', 'withdraw'}}
        funded |= {(entry.account, 'CNY') for entry in capital if entry.events}
        for entry in cash:
            self._cash(entry, state, funded)
        ordered = trades + capital + funds + cash
        state = self._settle(history, prefix, ordered, day)
        booked = {event['id'] for event in history}
        for entry in ordered:
            ids = [_identifier(event) for event in entry.events]
            if ids and all(identifier in booked for identifier in ids):
                entry.line = entry.line.rstrip('。') + '（已经入账，不会重复记录）。'
        events = [{**event, 'date': day} for entry in ordered for event in entry.events]
        return {'events': events, 'lines': [entry.line for entry in planned],
                'estimated': [entry.number for entry in planned if entry.estimated], 'state': state}

    def _validate(self, entry, base):
        item = entry.item
        if not isinstance(item, dict):
            raise entry.refuse('无效的变动。')
        if entry.operation in CORRECTIONS or (entry.operation == 'set_capital'
                                              and item.get('capital_mode') not in (None, '', 'sync_cash')):
            raise entry.refuse(CORRECTION)
        if entry.operation not in OPERATIONS:
            raise entry.refuse('无效的变动类型，请删除这一项后重新添加。')
        if set(item) - ITEM_FIELDS:
            raise entry.refuse('包含不支持的字段，请删除这一项后重新添加。')
        if not isinstance(item.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', item['id']):
            raise entry.refuse('记录标识无效，请删除这一项后重新添加。')
        if not isinstance(entry.account, str) or entry.account not in base['accounts']:
            raise entry.refuse('账户不存在（或在记账日期还没有期初余额），请从当前账户列表中选择。')
        if item.get('currency') not in {'CNY', 'HKD', 'USD'}:
            raise entry.refuse('请明确选择 CNY、HKD 或 USD 币种。')

    @staticmethod
    def _exclusive(planned):
        """One final balance per account and currency, one fund value and one principal target per account."""
        seen = set()
        for entry in planned:
            if entry.operation in {'set_cash', 'set_fund', 'set_capital'}:
                key = (entry.account, entry.operation, entry.item.get('currency'))
                if key in seen:
                    raise entry.refuse('同一账户的同币种现金、基金估值或本金只需核对一次，请修改已有记录。')
                seen.add(key)
        targets = {entry.account for entry in planned if entry.operation == 'set_capital'}
        for entry in planned:
            if entry.operation in {'deposit', 'withdraw'} and entry.account in targets:
                raise entry.refuse('同一账户请在资金转入转出与本金更新中选一种，避免重复计算。')

    def _settle(self, history, prefix, entries, day):
        """Replay the events of ``entries`` after ``prefix`` as ``Ledger.propose`` will book them.

        An event already in the ledger with the same content is left out (the ledger counts it as a duplicate);
        one with other content is refused, and so is one that was booked and then reversed: the ledger would
        count it as a duplicate too, so the item would read as booked while nothing of it counts. A refusal of
        the ledger names the item that caused it.
        """
        reversed_ids = {event.get('target_id') for event in history if event['kind'] == 'reverse'}
        for entry in entries:
            if any(_identifier(event) in reversed_ids for event in entry.events):
                raise entry.refuse(REVERSED)

        def replayed(subset):
            raws = [{**event, 'date': day} for entry in subset for event in entry.events]
            events = _booked([normalize(raw) for raw in raws], history)
            known = {event['id']: event for event in history}
            fresh = []
            for event in events:
                old = known.get(event['id'])
                if old is None:
                    fresh.append(event)
                    known[event['id']] = event
                elif encode(_by_listing({k: v for k, v in old.items() if k != '_sequence'})) != encode(_by_listing(event)):
                    raise Conflict('An external ID already exists with different content')
            start = max((event.get('_sequence', 0) for event in history), default=0) + 1
            return replay(prefix + [{**event, '_sequence': start + index} for index, event in enumerate(fresh)])
        try:
            return replayed(entries)
        except LedgerError:
            pass
        for count in range(1, len(entries) + 1):
            try:
                replayed(entries[:count])
            except Conflict:
                raise entries[count - 1].refuse(BOOKED_DIFFERENTLY) from None
            except LedgerError as exc:
                raise entries[count - 1].refuse(ledger_reason(exc) + '。') from None
        raise PortfolioError('账本拒绝了这批变动，请在网页中检查账本。')

    def _covered(self, history, prefix, entries, day):
        """A 港股通 buy pays its CNY through an ``fx``, which the ledger books whatever the cash. Unless margin
        was chosen, the CNY must be there before it (after the earlier items), as the ledger asks of any buy."""
        booked = {event['id'] for event in history}
        for index, entry in enumerate(entries):
            if entry.needs_cny is None or _identifier(entry.events[0]) in booked:
                continue
            state = self._settle(history, prefix, entries[:index], day)
            balance = amount(state['accounts'][entry.account]['cash_balances'].get('CNY', '0'))
            if balance < entry.needs_cny:
                raise entry.refuse(CONNECT_CASH.format(balance=_shown(balance), needed=_shown(entry.needs_cny)))

    def _held(self, base, account):
        return base['accounts'][account]['positions']

    def _trade(self, entry, day, base):
        item, buy = entry.item, entry.operation == 'buy'
        held = self._held(base, entry.account)
        raw_ticker = item.get('ticker')
        ticker = raw_ticker if isinstance(raw_ticker, str) and raw_ticker in held else None
        try:
            ticker = ticker or ledger_ticker(raw_ticker)
            if item.get('amount') not in (None, ''):
                raise PortfolioError('买卖请填写数量、成交价和费用，不要同时填写余额。')
            market = ticker.split(':')[0]
            unit = _MARKETS[market]
            if item['currency'] != unit:
                raise PortfolioError('成交币种与证券市场不一致。')
            quantity = _number(item.get('quantity'), positive=True)
            price = _number(item.get('price'), positive=True)
            if market in {'SHA', 'SHE', 'HKG'} and quantity != quantity.to_integral_value():
                raise PortfolioError('该市场的股票数量必须为整数股。')
            margin = True if buy and item.get('allow_margin') in ('yes', True) else None
            if not buy and item.get('allow_margin') in ('yes', True):
                raise PortfolioError('只有买入可以选择允许融资。')
            settlement = item.get('settlement_cny')
            if settlement not in (None, ''):
                if market != 'HKG':
                    raise PortfolioError('人民币成交金额只用于港股通；其他市场请留空。')
                return self._connect(entry, ticker, quantity, price, _number(settlement, positive=True), margin)
            if item.get('fee_currency') not in (None, '', unit):
                raise PortfolioError('港股通请填写人民币成交金额；港币交收的手续费按港币填写。')
            word = '买入' if buy else '卖出'
            fee_text = item.get('fee')
            if fee_text in (None, ''):
                fee, basis = self._estimate(entry.account, ticker, entry.operation, quantity, price, day)
                entry.estimated = True
                note = f'〔费用为估算：{basis}〕'
                fee_line = f'费用 {_shown(fee)} {unit}（估算：{basis}）'
            else:
                fee, note = _number(fee_text, nonnegative=True), None
                fee_line = f'费用 {_shown(fee)} {unit}'
        except PortfolioError as exc:
            raise entry.refuse(str(exc)) from None
        entry.event('trade', kind=entry.operation, ticker=ticker, currency=unit, quantity=_text(quantity),
                    price=_text(price), fee=_text(fee), allow_margin=margin, note=note)
        entry.line = (f'{entry.account}：{word} {ticker} {_shown(quantity)} 股 × {_shown(price)} {unit}，{fee_line}'
                      + ('；允许融资（现金可为负）' if margin else '') + '。')

    def _estimate(self, account, ticker, side, quantity, price, day):
        """The fee 记一笔 would book for an empty fee field: the account's fee profile (``core.fees``)."""
        from core import account_profiles, fees
        try:
            profile = account_profiles.get(account)
            estimate = fees.estimate(profile, ticker, side, quantity, price, day)
        except ValueError as exc:   # a profile or number the estimator refuses: its own Chinese message
            raise PortfolioError(f'无法估算费用（{str(exc).rstrip("。")}），请填写成交单上的费用。') from None
        except Exception as exc:
            logger.warning('the fee profile could not be read: %s', type(exc).__name__)
            raise PortfolioError('暂时无法读取账户费率，请填写成交单上的费用。') from None
        if estimate.total is None:
            raise PortfolioError(f'{(estimate.reason or "无法估算费用").rstrip("。")}，请填写成交单上的费用。')
        return estimate.total, estimate.basis or '按费率估算'

    def _connect(self, entry, ticker, quantity, price, settlement, margin):
        """港股通 settled in CNY, as the broker-file importer books it (``transactions._connect``).

        ``settlement`` is the CNY amount of the shares (without the fee) and the fee is in CNY. The rate is
        settlement ÷ HKD turnover; the fee is converted at it to the cent. A buy is ``fx`` CNY→HKD of the CNY
        paid, receiving the turnover plus the converted fee, then the HKD buy; a sell is the HKD sell, then
        ``fx`` of what it brought, receiving the CNY. A buy without margin needs the CNY in cash (``_covered``);
        its HKD leg, paid exactly by the ``fx``, is booked with margin allowed (the importer leaves that flag to
        the person), so HKD owed before never blocks it.
        """
        item, buy = entry.item, entry.operation == 'buy'
        if item.get('fee') in (None, ''):
            raise PortfolioError('港股通请填写人民币手续费（成交单上的金额，没有费用填 0）。')
        if item.get('fee_currency') not in (None, '', 'CNY'):
            raise PortfolioError('港股通的手续费按人民币填写。')
        fee_cny = _number(item.get('fee'), nonnegative=True)
        turnover = ledger_product(quantity, price)
        rate = settlement / turnover
        if not HKD_CNY_BAND[0] < rate < HKD_CNY_BAND[1]:
            raise PortfolioError(f'人民币成交金额 ÷ 港币成交金额 = {_shown(rate.quantize(Decimal("1e-6")))}，'
                                 '不在合理的港币汇率范围内；请核对人民币成交金额（不含手续费）。')
        fee_hkd = (fee_cny / rate).quantize(CENT, rounding=ROUND_HALF_UP)
        cash = settlement + fee_cny if buy else settlement - fee_cny
        if cash <= 0 or (not buy and turnover - fee_hkd <= 0):
            raise PortfolioError('港股通卖出金额不足以支付费用，请核对费用。')
        rate_text = _shown(rate.quantize(Decimal('1e-6')) if rate.as_tuple().exponent < -6 else rate)
        note = f'港股通：人民币成交 {_text(settlement)}，费用 {_text(fee_cny)} CNY 按汇率 {rate_text} 折合 {_text(fee_hkd)} HKD'
        if buy:
            entry.needs_cny = None if margin else cash
            entry.event('fx', kind='fx', currency='CNY', amount=_text(cash), to_currency='HKD',
                        received_amount=_text(turnover + fee_hkd), note=f'港股通买入 {ticker} 的人民币交收')
            # The ``fx`` pays the HKD leg exactly, so the leg borrows no HKD; it is booked with margin allowed so
            # that an unrelated negative HKD balance does not refuse it. The CNY the trade needs is checked instead
            # (``_covered``), unless 允许融资 was chosen.
            entry.event('trade', kind='buy', ticker=ticker, currency='HKD', quantity=_text(quantity), price=_text(price),
                        fee=_text(fee_hkd), allow_margin=True, note=note)
            exchange = f'换汇 {_shown(cash)} CNY → {_shown(turnover + fee_hkd)} HKD，再港币买入'
        else:
            entry.event('trade', kind='sell', ticker=ticker, currency='HKD', quantity=_text(quantity), price=_text(price),
                        fee=_text(fee_hkd), note=note)
            entry.event('fx', kind='fx', currency='HKD', amount=_text(turnover - fee_hkd), to_currency='CNY',
                        received_amount=_text(cash), note=f'港股通卖出 {ticker} 的人民币交收')
            exchange = f'港币卖出，再换汇 {_shown(turnover - fee_hkd)} HKD → {_shown(cash)} CNY'
        entry.line = (f"{entry.account}：港股通{'买入' if buy else '卖出'} {ticker} {_shown(quantity)} 股 × "
                      f"{_shown(price)} HKD；人民币成交 {_shown(settlement)}、手续费 {_shown(fee_cny)} CNY，"
                      f"汇率 {rate_text}，手续费折合 {_shown(fee_hkd)} HKD；记为{exchange}"
                      + ('；允许融资（现金可为负）' if margin else '') + '。')

    def _flow(self, entry, day, base):
        item, deposit = entry.item, entry.operation == 'deposit'
        unit = item['currency']
        try:
            if any(item.get(key) not in (None, '') for key in ('ticker', 'quantity', 'price', 'fee', 'settlement_cny')):
                raise PortfolioError('资金转入转出不能同时包含证券交易字段。')
            value = _number(item.get('amount'), positive=True)
            principal = item.get('principal_cny')
            rate = None
            if unit == 'CNY':
                if principal not in (None, '') and _number(principal, positive=True) != value:
                    raise PortfolioError('人民币转入或转出的本金必须与金额一致。')
                principal = value
            else:
                if principal in (None, ''):
                    raise PortfolioError('外币转入或转出请填写本次折合人民币本金；不会自动套用当前汇率。')
                principal = _number(principal, positive=True)
                rate, _places = exact_price(principal, value)
                if rate is None:
                    raise PortfolioError('折合人民币本金 ÷ 金额 无法得到账本能精确记录的汇率，请核对金额。')
        except PortfolioError as exc:
            raise entry.refuse(str(exc)) from None
        word = '转入' if deposit else '转出'
        entry.event('flow', kind='deposit' if deposit else 'withdrawal', currency=unit, amount=_text(value),
                    fx_rate=None if rate is None else _text(rate),
                    note=None if rate is None else f'汇率按折合人民币本金 {_text(principal)} CNY 推算')
        entry.line = f'{entry.account}：资金{word} {_shown(value)} {unit}，累计净投入{"增加" if deposit else "减少"} {_shown(principal)} CNY'
        if rate is not None:
            entry.line += f'（汇率 {_shown(rate)} 按折合人民币本金推算）'
        entry.line += '。'

    def _capital(self, entry, state):
        item = entry.item
        try:
            if item['currency'] != 'CNY':
                raise PortfolioError('累计净投入以人民币登记，请填写 CNY 金额。')
            target = _number(item.get('amount'))
        except PortfolioError as exc:
            raise entry.refuse(str(exc)) from None
        current = amount(state['accounts'][entry.account]['contributed_cny'])
        difference = target - current
        if difference == 0:
            entry.line = f'{entry.account}：本金更新，累计净投入已是 {_shown(target)} CNY，不记录。'
            return
        deposit = difference > 0
        entry.event('capital', kind='deposit' if deposit else 'withdrawal', currency='CNY', amount=_text(abs(difference)),
                    note=f'本金更新：目标累计净投入 {_text(target)}，账本为 {_text(current)} CNY')
        entry.line = (f"{entry.account}：本金更新，累计净投入 {_shown(current)} → {_shown(target)} CNY，记一笔人民币资金"
                      f"{'转入' if deposit else '转出'} {_shown(abs(difference))}（现金同时{'增加' if deposit else '减少'}）。")

    def _fund(self, entry, state):
        item = entry.item
        try:
            if item['currency'] != 'CNY':
                raise PortfolioError('基金合计估值仅支持 CNY。')
            if any(item.get(key) not in (None, '') for key in ('ticker', 'quantity', 'price', 'fee')):
                raise PortfolioError('余额对账不能同时包含交易字段。')
            value = _number(item.get('amount'), nonnegative=True)
        except PortfolioError as exc:
            raise entry.refuse(str(exc)) from None
        before = state['accounts'][entry.account]['fund_cny']
        if amount(before) == value:
            # Nothing changes; an event would only move the revision and make the day's report again.
            entry.line = f'{entry.account}：基金合计估值已是 {_shown(value)} CNY，不记录。'
            return
        entry.event('fund', kind='fund_value', currency='CNY', amount=_text(value))
        entry.line = f'{entry.account}：基金合计估值 {_shown(before)} → {_shown(value)} CNY（基金估值，不是申购或赎回）。'

    def _cash(self, entry, state, funded):
        item = entry.item
        unit = item['currency']
        try:
            if any(item.get(key) not in (None, '') for key in ('ticker', 'quantity', 'price', 'fee')):
                raise PortfolioError('余额对账不能同时包含交易字段。')
            target = _number(item.get('amount'))
        except PortfolioError as exc:
            raise entry.refuse(str(exc)) from None
        explanation = item.get('reconciliation_note')
        explanation = explanation.strip() if isinstance(explanation, str) else ''
        balance = amount(state['accounts'][entry.account]['cash_balances'].get(unit, '0'))
        difference = target - balance
        if difference == 0:
            entry.line = f'{entry.account}：{unit} 现金对账，账本余额已是 {_shown(target)}，不记录。'
            return
        if (entry.account, unit) in funded and not explanation:
            raise entry.refuse(f'现金对账 {_shown(target)} {unit} 与本次资金及交易计算的余额 {_shown(balance)} {unit} '
                               '不一致，请核对并填写差额说明。')
        kind, word = ('interest', '利息') if difference > 0 else ('fee', '费用')
        note = f'现金对账调整：卡片填写 {unit} 余额 {_text(target)}，账本为 {_text(balance)}，差额 {_signed(difference)}'
        if explanation:
            room = MAX_NOTE - len(note) - 4
            note += '；说明：' + (explanation if len(explanation) <= room else explanation[:max(room - 1, 0)] + '…')
        entry.event('adjust', kind=kind, currency=unit, amount=_text(abs(difference)), note=note)
        entry.line = (f'{entry.account}：{unit} 现金对账，账本 {_shown(balance)} → 填写 {_shown(target)}，差额 '
                      f'{_signed(difference)}，记一笔「{word}」现金调整（{kind}），不改变累计净投入'
                      + (f'；说明：{explanation}' if explanation else '') + '。')

    # -- previews ------------------------------------------------------------------------------

    def preview_single(self, day, fields, owner=None):
        """The one-item form (``daily.preview``): ``no_change`` books nothing, anything else is a list of one."""
        operation = fields.get('action', fields.get('operation'))
        if operation == 'no_change':
            if any(fields.get(key) not in (None, '') for key in fields if key not in {'action', 'operation', 'date'}):
                raise PortfolioError('无变化操作不能同时包含金额、证券或账户变更。')
            return self._save(day, None, ['确认今日账本无新增记录；日报按当前账本生成。'], f'{day} · 组合无变化',
                              'no_change', None, owner)
        item = {key: value for key, value in fields.items() if key not in {'action', 'date'} and value is not None}
        item['operation'] = operation
        item['id'] = uuid.uuid4().hex[:24]
        return self.preview(day, [item], owner=owner)

    def preview(self, day, items, owner=None):
        """A ledger proposal for the list, with the card text that describes it.

        The same person's earlier card proposals are withdrawn first, whatever this preview turns out to be (one
        that books nothing, or a refusal): a replaced preview never stays confirmable. A list whose events are all
        booked already, or that has none, gets no proposal; so every card proposal holds a card event, which is how
        the web app knows to leave it to the card.
        """
        withdraw_pending(self.adapter, owner)
        for _attempt in range(3):
            plan = self.plan(day, items)
            booked = {event['id'] for event in self.snapshot.history()}
            if all(_identifier(event) in booked for event in plan['events']):
                if plan['events']:
                    return self._save(day, None, plan['lines'] + ['清单中的变动已经入账，不会重复记录；日报按当前账本生成。'],
                                      f'{day} · 清单中的变动已经入账，没有新的记录', 'batch', items, owner)
                return self._save(day, None, plan['lines'] + ['账本无需新增记录；日报按当前账本生成。'],
                                  f'{day} · 账本无需新增记录', 'batch', items, owner)
            try:
                proposal = Ledger(self.path).propose(plan['events'], expect_revision=self.snapshot.revision)
            except StaleProposal:
                self._reload()   # another entry was booked meanwhile: plan again on the new ledger
                continue
            except Conflict:
                raise PortfolioError(BOOKED_DIFFERENTLY) from None
            except LedgerError as exc:
                raise PortfolioError('账本拒绝了这批变动：' + ledger_reason(exc) + '。') from None
            break
        else:
            raise PortfolioError('账本正在频繁变化，请稍后重新核对。')
        self._mark(proposal['id'], owner)
        changes = plan['lines'] + self._describe(proposal, items, plan)
        summary = f"{day} · 账本入账预览：{len(proposal['events'])} 笔记录待确认"
        return self._save(day, proposal, changes, summary, 'batch', items, owner)

    def _describe(self, proposal, items, plan):
        """What the list changes in each account it touches (only the figures that move), and the warnings that
        concern this list. The list's own lines (``plan['lines']``) already say each item's before → after, so
        an account's fund value is not repeated here when the list sets it."""
        before, after = proposal['before']['accounts'], proposal['after']['accounts']
        touched = list(dict.fromkeys(item['account'] for item in items))
        fund_set = {item['account'] for item in items if item.get('operation') == 'set_fund'}
        lines, moved_cash = [], set()
        for name in touched:
            old, new = before.get(name), after.get(name)
            if old is None or new is None:
                continue
            parts = []
            for unit in sorted(set(old['cash_balances']) | set(new['cash_balances'])):
                was, now = old['cash_balances'].get(unit, '0'), new['cash_balances'].get(unit, '0')
                if amount(was) != amount(now):
                    moved_cash.add((name, unit))
                    parts.append(f"{unit} 现金 {_shown(was)} → {_shown(now)}")
            if name not in fund_set and amount(old['fund_cny']) != amount(new['fund_cny']):
                parts.append(f"CNY 基金估值 {_shown(old['fund_cny'])} → {_shown(new['fund_cny'])}")
            if amount(old['contributed_cny']) != amount(new['contributed_cny']):
                parts.append(f"CNY 累计净投入 {_shown(old['contributed_cny'])} → {_shown(new['contributed_cny'])}")
            for ticker in sorted(set(old['positions']) | set(new['positions'])):
                a, b = old['positions'].get(ticker), new['positions'].get(ticker)
                if a == b:
                    continue
                quantity = f"{_shown(a['quantity']) if a else '0'} → {_shown(b['quantity']) if b else '0'} 股"
                cost = f"成本价 {_shown(a['cost_price']) if a else '—'} → {_shown(b['cost_price']) if b else '—'}"
                parts.append(f'{ticker} {quantity}（{cost}）')
            if parts:
                lines.append(f'{name}：' + '；'.join(parts))
        # A negative balance is pointed out when this list moves it; one the account already had is not news.
        for name, unit in sorted(moved_cash):
            value = after[name]['cash_balances'].get(unit, '0')
            if amount(value) < 0:
                lines.append(f'{name} {unit} 现金为负（{_shown(value)}），请核对是否与实际账户一致；可在清单里补充现金对账。')
        lines += [warning for warning in proposal.get('warnings', []) if CHINESE.search(warning)
                  and not any(warning.startswith(prefix) for prefix in _SAID_ELSEWHERE)
                  and 'negative cash' not in warning]
        if plan['estimated']:
            lines.append('第 ' + '、'.join(map(str, plan['estimated'])) + ' 项的费用为估算，入账后在流水中标为估算；'
                         '与成交单不同时请在清单里改填实际费用。')
        if proposal.get('duplicates'):
            lines.append(f"有 {proposal['duplicates']} 笔记录已经入账，不会重复记录。")
        return lines

    def _mark(self, proposal_id, owner):
        """Give a card proposal the lifetime of a 记一笔 preview, remember whose it is, and withdraw any other
        pending one of the same person (a preview made meanwhile on another card)."""
        key = _owner_key(owner)
        with self.adapter._db() as db:
            db.execute(_PREVIEWS_TABLE)
            db.execute('DELETE FROM ledger_previews WHERE created<?', (time.time() - 86400,))
            if key is not None:
                db.execute('INSERT OR REPLACE INTO ledger_previews VALUES (?,?,?)', (proposal_id, key, time.time()))
        expires = (datetime.now(timezone.utc) + PREVIEW_LIFETIME).isoformat()
        with Ledger(self.path).writing() as db:
            db.execute('CREATE TABLE IF NOT EXISTS proposal_expiry (proposal_id TEXT PRIMARY KEY, expires_at TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO proposal_expiry VALUES (?,?)', (proposal_id, expires))
        withdraw_pending(self.adapter, owner, keep=proposal_id)

    def _save(self, day, proposal, changes, summary, action, items, owner):
        preview_id = proposal['id'] if proposal else uuid.uuid4().hex
        payload = {'mode': 'ledger', 'id': preview_id, 'proposal_id': proposal['id'] if proposal else None,
                   'revision': proposal['revision'] if proposal else self.snapshot.revision, 'date': day,
                   'summary': summary, 'changes': changes, 'action': action, 'items': items,
                   'events': len(proposal['events']) if proposal else 0}
        with self.adapter._db() as db:
            db.execute('DELETE FROM previews WHERE expires<?', (time.time(),))
            db.execute('INSERT OR REPLACE INTO previews VALUES (?,?,?)',
                       (preview_id, time.time() + CARD_PREVIEW_SECONDS, _encode(payload)))
        return {'id': preview_id, 'summary': summary, 'date': day, 'changes': changes, 'ledger': True,
                'action': action, 'opaque_state': {'preview_id': preview_id}}

    # -- confirmation --------------------------------------------------------------------------

    def confirm(self, preview_id, operation_id):
        """Confirm the proposal of a card preview once; the caller holds the portfolio write lock.

        The operation is keyed on the proposal: another card, a second tap or a retry after a restart finds the
        operation of the first and returns its receipt. Nothing is booked from a preview the ledger moved past
        (``StalePreview``, the proposal withdrawn and the operation forgotten).
        """
        with self.adapter._db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM operations WHERE id=? OR preview_id=?', (operation_id, preview_id)).fetchall()
            if rows:
                if len(rows) != 1 or rows[0]['preview_id'] != preview_id:
                    raise PortfolioError('确认请求已用于另一项变更，请重新预览。')
                operation = dict(rows[0])
            else:
                row = db.execute('SELECT * FROM previews WHERE id=?', (preview_id,)).fetchone()
                if not row or row['expires'] < time.time():
                    raise PortfolioError('预览已过期，请刷新并重新确认。')
                payload = json.loads(row['payload'])
                if payload.get('mode') != 'ledger':
                    raise PortfolioError('这份预览是按旧持仓文件生成的；交易账本已启用，请重新核对全部变动。')
                receipt = {'reference': operation_id, 'operation_id': operation_id, 'mode': 'ledger',
                           'date': payload['date'], 'proposal_id': payload['proposal_id'],
                           'summary': payload['summary'], 'changes': payload['changes'], 'report_status': 'not_started'}
                db.execute("INSERT INTO operations(id,preview_id,status,payload,receipt) VALUES (?,?,'prepared',?,?)",
                           (operation_id, preview_id, _encode(payload), _encode(receipt)))
                operation = dict(db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone())
        if operation['status'] == 'committed':
            return self._as_now(json.loads(operation['receipt']))
        payload = json.loads(operation['payload'])
        if payload.get('mode') != 'ledger':
            raise PortfolioError('旧持仓文件的一次更新尚未完成，需要人工核对；交易账本已启用，不会再写入旧持仓文件。')
        return self._as_now(self._finish(operation, payload))

    def _as_now(self, receipt):
        """A receipt shown again says so when some of what it booked was reversed in the web app since."""
        proposal_id = receipt.get('proposal_id')
        if not proposal_id or not receipt.get('applied'):
            return receipt
        try:
            stored = Ledger(self.path).proposal(proposal_id) or {}
            ids = {event['id'] for event in stored.get('events', [])}
            self._reload()
            undone = ids & {event.get('target_id') for event in self.snapshot.history() if event['kind'] == 'reverse'}
        except Exception as exc:   # the receipt as stored is still true of the booking
            logger.warning('reversals of a card booking could not be read: %s', type(exc).__name__)
            return receipt
        if not undone:
            return receipt
        summary = REVERSED_LATER.format(day=receipt['date'], applied=receipt['applied'], count=len(undone))
        return {**receipt, 'summary': summary, 'reversed': len(undone),
                'changes': [summary + '；以下是入账时的预览，现金和持仓以网页 账本 为准。'] + list(receipt.get('changes', []))}

    def _forget(self, operation):
        with self.adapter._db() as db:
            db.execute("DELETE FROM operations WHERE id=? AND status='prepared'", (operation['id'],))

    def _finish(self, operation, payload):
        receipt = json.loads(operation['receipt'])
        proposal_id = payload.get('proposal_id')
        if proposal_id is None:
            # Nothing to book; a revision passed since the preview still makes the preview's text untrue.
            self._reload()
            if self.snapshot.revision != payload['revision']:
                self._forget(operation)
                raise StalePreview(STALE)
            booked = {'revision': self.snapshot.revision, 'applied': 0, 'duplicates': 0}
        else:
            ledger = Ledger(self.path)
            self._links(ledger, proposal_id, prepare=True)
            try:
                booked = ledger.confirm(proposal_id)
            except LedgerError as exc:
                stored = ledger.proposal(proposal_id)
                if stored is not None and stored.get('receipt'):
                    booked = stored['receipt']   # booked after all (a repeated confirmation)
                else:
                    try:
                        with ledger.writing() as db:
                            Ledger.withdraw(db, proposal_id)
                    except Exception as failure:   # a leftover proposal can never be booked: refusing is enough
                        logger.warning('a stale card proposal was not withdrawn: %s', type(failure).__name__)
                    self._forget(operation)
                    if isinstance(exc, Conflict) or stored is None:
                        raise StalePreview(STALE) from None
                    raise PortfolioError('账本拒绝了这份预览：' + ledger_reason(exc) + '。没有入账，请重新核对全部变动。') from None
            self._links(ledger, proposal_id, prepare=False)
        applied = booked.get('applied', 0)
        receipt.update(revision=booked['revision'], applied=applied, duplicates=booked.get('duplicates', 0))
        if proposal_id is None:
            receipt['summary'] = f"{payload['date']} · 账本没有新增记录"
        elif applied:
            receipt['summary'] = f"{payload['date']} · 已入账 {applied} 笔账本记录"
        else:
            receipt['summary'] = f"{payload['date']} · 清单中的变动已经入账，没有新增记录"
        with self.adapter._db() as db:
            db.execute("UPDATE operations SET status='committed', receipt=? WHERE id=?",
                       (_encode(receipt), operation['id']))
        return receipt

    @staticmethod
    def _links(ledger, proposal_id, prepare):
        """The trade-plan links of what the card books, as the confirm endpoint stores them; never raises."""
        try:
            from core import plans
            proposal = ledger.proposal(proposal_id)
            if prepare:
                if proposal is not None:
                    plans.prepare_links(proposal, ledger=ledger)
            else:
                plans.record_links(proposal, ledger=ledger)
        except Exception as exc:
            logger.warning('plan links of a card proposal were not %s: %s',
                           'prepared' if prepare else 'written', type(exc).__name__)


# -- the report -----------------------------------------------------------------------------------

def _checkpoint(adapter, day):
    """The day's pipeline checkpoint, or {} (read under the lock it is written under)."""
    try:
        state = json.loads((adapter.portfolio_dir / 'pipeline-state' / f'{day}.json').read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _pushed(adapter, day):
    """Whether the day's checkpoint records the report as pushed."""
    return _checkpoint(adapter, day).get('push') == 'complete'


def _day_done(adapter, day):
    """Whether the day's report is complete (made, and pushed where a push is configured)."""
    state = _checkpoint(adapter, day)
    return state.get('complete') is True and state.get('report') is True


def _waiting(adapter, reference, result, day, wait):
    """Keep the job for later: the card service runs it again at ``wait.until`` (``due_reports``)."""
    result['report_at'] = wait.until.astimezone(SHANGHAI).isoformat()
    result['report_markets'] = list(wait.markets)
    with adapter._db() as db:
        db.execute("UPDATE operations SET report_status='waiting', receipt=? WHERE id=?", (_encode(result), reference))
    return {**result, 'report_status': 'waiting', 'summary': _waiting_text(result, day)}


def _waiting_text(result, day):
    wait = Wait(datetime.fromisoformat(result['report_at']), tuple(result.get('report_markets') or ()))
    return WAITING.format(booked='账本已入账。' if result.get('applied') else '账本没有新增记录。', day=day,
                          reason=_reason(wait, day))


def _engine_wait(output, now):
    """The moment the engine asked to be run again at (``CLOSE_NOT_FINAL_UNTIL``, or its earlier ``CHECK_AT``)."""
    waits = CLOSE_WAIT.findall(output or '')
    if not waits:
        return None
    until, markets, check = waits[-1]
    try:
        moment = datetime.fromisoformat(check or until).astimezone(SHANGHAI)
    except ValueError:
        return None
    return Wait(max(moment, now + RECHECK), tuple(market for market in markets.split(',') if market))


def run_report(adapter, reference, now=None):
    """Make the day's snapshot and report after a ledger booking; the lock is released only while delivering.

    The pipeline values the ledger as it is now, which holds this booking. Its checkpoint names the holdings
    (with the ledger revision) a snapshot, report and push were made from, under the lock the backend's close
    snapshot takes too: the same holdings never get a second push, changed holdings get a new snapshot and
    report. As in the close pipeline (``action_auto_pipeline``), the report is pushed only when
    ``feishu_chat_id`` is configured; without it the saved report completes the run and the checkpoint is the
    close pipeline's "saved, not pushed" (``complete``, no push claimed). An entry dated before the previous
    day, or made after 09:00 the next morning, does not remake old snapshots (``report_window_open``).

    A confirmation that booked nothing (今日无变动, a list the ledger already holds) leaves a day whose report is
    complete as it is: the checkpoint names the ledger revision, which any later entry (even one of another day)
    moves, so running the pipeline would make and push the same report a second time.

    Before the day's own closes are final for the markets the ledger holds (``closes_final_at``), or when the
    snapshot engine says a close is not final yet, nothing is made: the job is kept ``waiting`` with the moment
    to run it, and the card service runs it then (``due_reports``); one still waiting when the day's window
    ends is ``expired``.
    """
    now = now or _now()
    with adapter._lock():
        with adapter._db() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (reference,)).fetchone()
        if not row or row['status'] != 'committed':
            raise PortfolioError('该更新尚未确认，不会生成或发送日报。')
        payload, result = json.loads(row['payload']), json.loads(row['receipt'])
        if payload.get('mode') != 'ledger':
            raise PortfolioError('缺少已确认的更新回执。')
        day, status = payload['date'], row['report_status']
        if status == 'complete':
            if result.get('report_delivery') == 'saved':
                return {**result, 'report_status': 'complete', 'summary': SAVED}
            if result.get('report_delivery') == 'unchanged':
                return {**result, 'report_status': 'complete', 'summary': UNCHANGED.format(day=day)}
            return {**result, 'report_status': 'complete', 'summary': '该次更新的日报已完成，无需重复发送。'}
        if status == 'expired' or (status == 'waiting' and not report_window_open(day, now)):
            with adapter._db() as db:
                db.execute("UPDATE operations SET report_status='expired' WHERE id=?", (reference,))
            return {**result, 'report_status': 'expired', 'summary': EXPIRED.format(day=day)}
        if status == 'skipped' or not report_window_open(day, now):
            with adapter._db() as db:
                db.execute("UPDATE operations SET report_status='skipped' WHERE id=?", (reference,))
            return {**result, 'report_status': 'skipped', 'summary': BACKDATED.format(day=day)}
        if not result.get('applied') and _day_done(adapter, day):
            result['report_delivery'] = 'unchanged'
            with adapter._db() as db:
                db.execute("UPDATE operations SET report_status='complete', receipt=? WHERE id=?",
                           (_encode(result), reference))
            return {**result, 'report_status': 'complete', 'summary': UNCHANGED.format(day=day)}
        until = waits_until(adapter, day, now)
        if until is not None:
            return _waiting(adapter, reference, result, day, until)
        with adapter._db() as db:
            db.execute("UPDATE operations SET report_status='running' WHERE id=?", (reference,))
        adapter._held.output = ''
        complete = adapter._execute_pipeline(day, False, push='configured')
        if not complete:
            until = _engine_wait(getattr(adapter._held, 'output', ''), now)
            if until is not None:
                return _waiting(adapter, reference, result, day, until)
            with adapter._db() as db:
                db.execute("UPDATE operations SET report_status='pending' WHERE id=?", (reference,))
            raise PortfolioError(PENDING)
        result['report_delivery'] = 'pushed' if _pushed(adapter, day) else 'saved'
        result.pop('report_at', None)
        with adapter._db() as db:
            db.execute("UPDATE operations SET report_status='complete', receipt=? WHERE id=?",
                       (_encode(result), reference))
        if result['report_delivery'] == 'saved':
            return {**result, 'report_status': 'complete', 'summary': SAVED}
        return {**result, 'report_status': 'complete', 'summary': '日报已完成。'}


def due_reports(adapter, now=None):
    """The operations whose report job waits for a moment that has come (stored, so a restart keeps them)."""
    now = now or _now()
    with adapter._db() as db:
        rows = db.execute("SELECT id, receipt FROM operations WHERE report_status='waiting'").fetchall()
    due = []
    for row in rows:
        try:
            moment = datetime.fromisoformat(json.loads(row['receipt'])['report_at'])
        except (ValueError, KeyError, TypeError):
            logger.warning('a waiting card report has no time to run at')
            continue
        if moment <= now:
            due.append(row['id'])
    return due


def report_state(adapter, receipt):
    """What the 日报 card shows for one card entry's report when the job's own status would not say it:
    ``{'label', 'summary', 'retry'}`` (waiting for the closes, expired, saved, unchanged), else None. Never
    raises."""
    reference = receipt.get('operation_id', receipt.get('reference')) if isinstance(receipt, dict) else None
    if not isinstance(reference, str):
        return None
    try:
        with adapter._db() as db:
            row = db.execute('SELECT report_status, payload, receipt FROM operations WHERE id=?', (reference,)).fetchone()
        if row is None:
            return None
        stored, day = json.loads(row['receipt']), json.loads(row['payload'])['date']
        if row['report_status'] == 'waiting':
            return {'label': '等待收盘', 'summary': _waiting_text(stored, day), 'retry': True}
        if row['report_status'] == 'expired':
            return {'label': '未生成', 'summary': EXPIRED.format(day=day), 'retry': False}
        if row['report_status'] == 'complete' and stored.get('report_delivery') == 'saved':
            return {'label': '已完成', 'summary': SAVED, 'retry': False}
        if row['report_status'] == 'complete' and stored.get('report_delivery') == 'unchanged':
            return {'label': '已完成', 'summary': UNCHANGED.format(day=day), 'retry': False}
    except Exception as exc:   # the reports card must still open
        logger.warning('the report state of a card entry could not be read: %s', type(exc).__name__)
    return None


def report_note(adapter, receipt):
    """The 日报 card's line for one card entry's report (``report_state``), or None."""
    state = report_state(adapter, receipt)
    return state['summary'] if state else None
