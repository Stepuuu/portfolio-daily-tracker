"""Identity of imported rows and their relation to what is already in the ledger.

* ``source = import:{account}``. ``external_id`` is ``t:{date}:{code}:{成交编号}`` when
  the 成交编号 is present and unique for that date in the file, ``s:{date}:{流水号}``
  for a unique cash-flow serial, otherwise
  ``h:{sha1(date|time|code|action|qty|price|turnover|cash)}:{occurrence}``. Two
  genuinely identical fills get occurrences 1 and 2 and both import; importing the
  same file again gives the same ids. Order-level numbers (合同编号, 委托编号) are not
  used: one order can have several fills.
* Ids are assigned over the whole corrected row set on every analysis (preview
  and propose alike). Rows dated in the file are numbered first, in file order,
  from the file's values only; rows whose missing date the person filled in come
  after them and continue the same counters. So two identical rows stay distinct
  also after their dates are filled in, and a filled-in date never shifts the id
  of a row dated in the file.
* A row whose events already exist with identical content is 已导入 and excluded.
  Tickers compare by listing (``identity_key``: ``HKG:700`` = ``HKG:00700``), since
  the ledger books a listing under the spelling its account holds it.
  Differences only in ``name`` / ``note`` (a renamed security, another vendor's
  wording) still count as imported, and so does a trade booked under the rules of
  ``INTERPRETATION_VERSION`` 2 when its events are exactly the ones those rules gave
  for this row (``Row.earlier_events``: a derived price rounded to 10 places, a fee
  from fee columns a cent away from the cash amount, the 港股通 换汇 amounts that
  follow from that price). Any other difference is 与已入账记录不同.
* Same id, different content → 与已入账记录不同: excluded, with the differing
  fields. This replaces the whole-import ``Conflict`` of the strict CSV template.
* A row that matches an event this file cannot have produced (another source, or
  an earlier import of an overlapping export whose rows had other ids, e.g. without
  成交编号): same account, date, kind, ticker and quantity (whatever the price), or the
  same currency and cash amount, is 可能重复 and unchecked by default. Each existing
  event explains at most one row, and an event with a row's own price, fee and amount
  explains that row before any looser match is tried (two same-size fills of a day
  pair with the right booked one whatever the order of the rows). A row with its own
  成交编号 (``t:`` id) never matches an event booked with a different 成交编号: two
  numbered fills are two fills. ``duplicate_of`` summarises that event for the page:
  its id, source, date, kind, ticker, quantity, price, fee, currency, amount and note
  as booked, plus ``name`` (the event's, else the listing's name in the ledger),
  ``source_label`` (手工, 交割单导入, 助手 …) and, for a split, ``added_quantity``.
* A split (送转股) row has no event until the rows are checked against the ledger
  (its ratio depends on the shares held then), so it is compared by what it adds: the
  booked split of the same id (or, without one, any split of the same account, date and
  ticker) added held-before × (ratio − 1) shares. Equal: 已导入 (same id) or 可能重复;
  different: 与已入账记录不同 (same id) or 需处理.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache
import hashlib
import re
from typing import Dict, Iterable, List, Optional

from core.ledger import LedgerError, encode, replay
from core.importers.values import money, text
from core.market.search import identity_key

SUFFIXES = ('', ':fx', ':trade', ':repo')
# Where a ledger event came from, in the page's words (frontend: features/ledger/events.ts ``sourceLabel``).
SOURCE_LABELS = {'manual': '手工', 'demo': '演示', 'legacy-opening': '旧持仓迁移', 'agent': '助手', 'agent:chat': '助手',
                 'assistant': '助手', 'corporate-action': '公司行为', 'screenshot': '截图', 'today': '手工', 'alert': '手工',
                 'plan': '手工'}


def source_label(source) -> str:
    if not source or not isinstance(source, str):
        return '手工'
    if source in SOURCE_LABELS:
        return SOURCE_LABELS[source]
    if source.startswith('import:'):
        return '交割单导入'
    if source.startswith('agent'):
        return '助手'
    return 'CSV 导入'


def listing_names(existing: Iterable[dict]) -> Dict[str, str]:
    """{ticker: name} from the ledger's events (opening positions, trades), the latest name winning."""
    names: Dict[str, str] = {}
    for event in existing:
        for position in event.get('positions') or []:
            if isinstance(position, dict) and position.get('ticker') and position.get('name'):
                names[position['ticker']] = position['name']
        if event.get('ticker') and event.get('name'):
            names[event['ticker']] = event['name']
    return names
_IGNORED = {'name', 'note', '_sequence'}
FIELD_LABELS = {'kind': '类型', 'date': '日期', 'ticker': '代码', 'currency': '币种', 'quantity': '数量',
                'price': '价格', 'fee': '费用', 'amount': '金额', 'received_amount': '到账金额', 'to_currency': '目标币种',
                'fx_rate': '汇率', 'ratio': '比例', 'allow_margin': '允许融资', 'account': '账户'}


def _usable(identifier: str) -> bool:
    return bool(identifier) and len(identifier) <= 64 and bool(re.fullmatch(r'[\w\-./]+', identifier)) \
        and not set(identifier) <= {'0'}


def _hash_key(row) -> str:
    parts = [row.date or '', row.time or '', row.code.upper(), row.action,
             text(row.quantity) or '', text(row.price) or '', text(row.turnover) or '', text(row.cash_change) or '']
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()[:20]


def assign_ids(rows, filled=()) -> None:
    """Set ``external_id`` on every dated row (file order matters for occurrences).

    ``filled``: rows whose date the person filled in. They are numbered after all
    rows dated in the file, so they never change those rows' ids.
    """
    later = {id(row) for row in filled}
    first = [row for row in rows if id(row) not in later]
    second = [row for row in rows if id(row) in later]
    trade_ids = Counter((r.date, r.trade_id) for r in first if r.date and _usable(r.trade_id))
    serials = Counter((r.date, r.serial) for r in first if r.date and _usable(r.serial))
    occurrences: Counter = Counter()
    _assign(first, trade_ids, serials, occurrences)
    trade_ids.update((r.date, r.trade_id) for r in second if r.date and _usable(r.trade_id))
    serials.update((r.date, r.serial) for r in second if r.date and _usable(r.serial))
    _assign(second, trade_ids, serials, occurrences)


def _assign(rows, trade_ids: Counter, serials: Counter, occurrences: Counter) -> None:
    for row in rows:
        if not row.date:
            row.external_id = None
            continue
        if _usable(row.trade_id) and trade_ids[(row.date, row.trade_id)] == 1:
            row.external_id = f't:{row.date}:{row.code.upper()[:20]}:{row.trade_id}'
        elif _usable(row.serial) and serials[(row.date, row.serial)] == 1:
            row.external_id = f's:{row.date}:{row.serial}'
        else:
            digest = _hash_key(row)
            occurrences[digest] += 1
            row.external_id = f'h:{digest}:{occurrences[digest]}'


def ledger_id(source: str, external_id: str) -> str:
    return hashlib.sha256(encode([source, external_id]).encode()).hexdigest()


def differences(old: dict, new: dict) -> List[str]:
    keys = (set(old) | set(new)) - _IGNORED - {'id'}
    out = []
    for key in sorted(keys):
        if old.get(key) != new.get(key):
            label = FIELD_LABELS.get(key, key)
            out.append(f'{label} 已入账 {_show(old.get(key))} / 文件 {_show(new.get(key))}')
    return out


_listing = lru_cache(maxsize=4096)(identity_key)


def same_listing(first, second) -> bool:
    """Two tickers of one listing, whatever the spelling (``identity_key``); two missing tickers are the same."""
    return first == second or bool(first and second) and _listing(first) == _listing(second)


def _as_booked(event: dict, old: dict) -> dict:
    """``event`` with the booked spelling of its listing, so a spelling alone never makes it differ."""
    if event.get('ticker') != old.get('ticker') and same_listing(event.get('ticker'), old.get('ticker')):
        return {**event, 'ticker': old['ticker']}
    return event


def _show(value) -> str:
    if value is None:
        return '无'
    try:
        return money(Decimal(str(value)))
    except Exception:
        return str(value)


def compare(rows, existing: Iterable[dict], source: str) -> None:
    """Mark rows already in the ledger (``imported``) or booked differently (``changed``)."""
    existing = list(existing)
    by_id = {event['id']: event for event in existing}
    reversed_ids = {e['target_id'] for e in by_id.values() if e['kind'] == 'reverse'}
    for row in rows:
        if not row.external_id:
            continue
        if row.status == 'new' and row.category == 'split' and not row.events and row.split_added is not None:
            old = by_id.get(ledger_id(source, row.external_id))
            if old is not None:
                _compare_split(row, old, existing, reversed_ids)
            continue
        if row.status not in ('new',) or not row.events:
            if row.status in ('review', 'merged') or row.category == 'split':
                present = [ledger_id(source, row.external_id + s) for s in SUFFIXES
                           if ledger_id(source, row.external_id + s) in by_id]
                if present and row.status != 'merged':
                    row.status, row.include, row.events = 'imported', False, []
                    row.messages = ['这一行以前已经导入（当时补充过信息）']
                    row.needs, row.choices = [], {}
            continue
        found = [by_id.get(event['id']) for event in row.events]
        if not any(found):
            continue
        problems = []
        renamed = earlier = False
        alternatives = {event['id']: event for event in row.earlier_events}
        for event, old in zip(row.events, found):
            if old is None:
                problems.append('只有部分记录已入账')
                continue
            clean_old = {k: v for k, v in old.items() if k != '_sequence'}
            event = _as_booked(event, clean_old)
            if encode(clean_old) == encode(event):
                continue
            diff = differences(clean_old, event)
            if diff and event['id'] in alternatives and \
                    not differences(clean_old, _as_booked(alternatives[event['id']], clean_old)):
                earlier = True   # booked by rules version 2 from this very row
            elif diff:
                problems.extend(diff)
            else:
                renamed = True
        if problems:
            row.existing = {k: v for k, v in (next(o for o in found if o) or {}).items() if k != '_sequence'}
            row.set('changed', '与已入账的同一编号记录不同：' + '；'.join(problems) +
                    '。这一行不会导入；如需更正，请先冲销原记录。')
        else:
            row.set('imported', '已经导入过' + ('（名称或备注不同，已忽略）' if renamed else ''))
            if earlier:
                row.messages.append('账本里的这条记录是按旧规则推算的成交价或费用（成交价保留 10 位小数，'
                                    '或费用取费用列合计），现金可能与券商相差不到 1 分；不会重复入账。')
            if any(event['id'] in reversed_ids for event in row.events if event):
                row.messages.append('注意：这条记录已入账，但后来被冲销了')


def _held_before(existing: List[dict], target: dict) -> Optional[Decimal]:
    """Shares of ``target``'s ticker its account held just before the ledger applied ``target`` (None: unknown)."""
    key = (target['date'], target.get('_sequence', 0))
    try:
        state = replay([e for e in existing if (e['date'], e.get('_sequence', 0)) < key])
    except LedgerError:
        return None
    position = state['accounts'].get(target.get('account'), {}).get('positions', {}).get(target.get('ticker'))
    return Decimal(position['quantity']) if position else None


def split_added(existing: List[dict], event: dict) -> Optional[Decimal]:
    """The shares a booked split event added: held before × (ratio − 1); None when that cannot be known."""
    held = _held_before(existing, event)
    if held is None or event.get('ratio') is None:
        return None
    return held * (Decimal(event['ratio']) - 1)


def _compare_split(row, old: dict, existing: List[dict], reversed_ids: set) -> None:
    added = split_added(existing, old) if old['kind'] == 'split' else None
    same = same_listing(old.get('ticker'), row.ticker)
    if old['kind'] == 'split' and same and old['date'] == row.date and added == row.split_added:
        row.set('imported', '已经导入过（送转股）')
        if old['id'] in reversed_ids:
            row.messages.append('注意：这条记录已入账，但后来被冲销了')
        return
    row.existing = {k: v for k, v in old.items() if k != '_sequence'}
    problems = [f'{FIELD_LABELS[key]} 已入账 {_show(old.get(key))} / 文件 {_show(value)}'
                for key, value in (('kind', 'split'), ('date', row.date), ('ticker', row.ticker))
                if old.get(key) != value and not (key == 'ticker' and same)]
    if added != row.split_added:
        problems.append(f'到账数量 已入账 {_show(added) if added is not None else "无法确定"} / 文件 {_show(row.split_added)}')
    row.set('changed', '与已入账的同一编号记录不同：' + '；'.join(problems) + '。这一行不会导入；如需更正，请先冲销原记录。')


def _matches(new: dict, old: dict) -> bool:
    """Same kind, listing and quantity (a trade, whatever its price), or the same currency and cash amount."""
    if new['kind'] in ('buy', 'sell'):
        return same_listing(new.get('ticker'), old.get('ticker')) and \
            Decimal(new['quantity']) == Decimal(old.get('quantity', 'NaN'))
    if new['kind'] == 'split':
        return same_listing(new.get('ticker'), old.get('ticker'))
    if 'amount' in new and 'amount' in old:
        if new.get('currency') != old.get('currency'):
            return False
        a1, a2 = Decimal(new['amount']), Decimal(old['amount'])
        return abs(a1 - a2) <= max(a1, a2) * Decimal('0.0001')
    return False


def fuzzy(rows, existing: Iterable[dict], account: str, claimed_ids: Iterable[str]) -> None:
    """Flag new rows that look like an event entered another way (manual, agent, screenshot)."""
    existing = list(existing)
    names = listing_names(existing)
    reversed_ids = {e['target_id'] for e in existing if e['kind'] == 'reverse'}
    skip = set(claimed_ids) | reversed_ids
    pool: Dict[tuple, List[dict]] = defaultdict(list)
    for event in existing:
        if event['kind'] != 'reverse' and event.get('account') == account and event['id'] not in skip:
            pool[(event['date'], event['kind'])].append(event)
    used = set()
    ordered = sorted(rows, key=lambda r: r.order)
    for row in ordered:
        if row.status == 'new' and row.category == 'split' and not row.events and row.split_added is not None:
            _fuzzy_split(row, pool.get((row.date, 'split'), []), existing, used, names)
    # Two passes: an event with the row's very price and fee explains that row before any looser match is tried, so
    # which of two same-size fills of a day is the one already booked never depends on the order of the rows.
    for exact in (True, False):
        for row in ordered:
            if row.status == 'new' and row.events:
                _fuzzy_row(row, pool, used, names, exact)


def _same_terms(new: dict, old: dict) -> bool:
    return all(str(new.get(key)) == str(old.get(key)) for key in ('price', 'fee', 'amount', 'currency'))


def _fill_number(event: dict) -> Optional[tuple]:
    """(date, 成交编号) of the fill an event was booked from: its ``t:{date}:{code}:{成交编号}`` id, without the
    event's suffix and whatever the spelling of the code. A number is compared without leading zeros or a zero
    fraction: a workbook stores the text ``000123`` as the number 123."""
    external = event.get('external_id')
    if not isinstance(external, str) or not external.startswith('t:'):
        return None
    external = next((external[:-len(s)] for s in SUFFIXES if s and external.endswith(s)), external)
    number = external.rsplit(':', 1)[-1]
    digits = re.fullmatch(r'0*(\d+?)(\.0*)?', number)
    return external[2:12], digits.group(1) if digits else number


def _other_fill(new: dict, old: dict) -> bool:
    """Both events come from fills with their own 成交编号, and the numbers differ."""
    numbers = _fill_number(new), _fill_number(old)
    return None not in numbers and numbers[0] != numbers[1]


def _fuzzy_row(row, pool: Dict[tuple, List[dict]], used: set, names: Dict[str, str], exact: bool) -> None:
    for event in row.events:
        match = next((old for old in pool.get((event['date'], event['kind']), [])
                      if old['id'] not in used and _matches(event, old) and not _other_fill(event, old)
                      and (not exact or _same_terms(event, old))),
                     None)
        if match is not None:
            used.add(match['id'])
            row.status = 'possible_duplicate'
            row.include = False
            row.duplicate_of = _summary(match, names)
            name = row.duplicate_of.get('name')
            what = (f"{name + ' ' if name else ''}{match.get('ticker')} {match.get('quantity')} @ {match.get('price')}"
                    if 'quantity' in match else f"{match.get('currency', '')} {match.get('amount', '')}")
            row.messages.append(f"与 {match['date']} 的{source_label(match.get('source'))}记录相同（{what}），"
                                '可能已经记过。确认不是同一笔再勾选。')
            break


def _summary(event: dict, names: Dict[str, str], extra=()) -> dict:
    """``duplicate_of``: the booked event's fields the page shows (see the module docstring)."""
    out = {k: event.get(k) for k in ('id', 'source', 'date', 'kind', 'ticker', 'quantity', 'price', 'fee', 'currency',
                                     'amount', 'ratio', 'note') if event.get(k) is not None}
    name = event.get('name') or names.get(event.get('ticker'))
    if name:
        out['name'] = name
    out['source_label'] = source_label(event.get('source'))
    out.update(extra)
    return out


def _fuzzy_split(row, candidates: List[dict], existing: List[dict], used: set, names: Dict[str, str]) -> None:
    """A split row against the account's other splits of that date and ticker (see the module docstring)."""
    for old in candidates:
        if old['id'] in used or not same_listing(old.get('ticker'), row.ticker):
            continue
        used.add(old['id'])
        added = split_added(existing, old)
        if added is not None and added == row.split_added:
            row.status, row.include = 'possible_duplicate', False
            row.duplicate_of = _summary(old, names, {'added_quantity': text(added)})
            row.messages.append(f"与 {old['date']} 的{source_label(old.get('source'))}送转股记录（{row.ticker} 到账 "
                                f"{text(added)}）相同，可能已经记过。确认不是同一笔再勾选。")
        else:
            row.set('review', f"账本里 {old['date']} 已有 {row.ticker} 的{source_label(old.get('source'))}送转股记录（到账 "
                              f"{text(added) if added is not None else '无法确定'}），与这一行的 {text(row.split_added)} "
                              '不同。同一天不会有两次送转股：请核对到账数量；如果这一行与账本的记录是同一次，请把它设为跳过。',
                    needs=['quantity', 'kind'])
        return
