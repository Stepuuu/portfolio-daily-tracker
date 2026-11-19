"""Opening balances from the v3.2 legacy files: read them, never write them (plan 1.2, EN-5).

Before the ledger, holdings lived in two files. Both are import sources now:

* ``PORTFOLIO_DIR/holdings/<YYYY-MM-DD>.json``: the engine's daily holdings
  (groups with ``cash`` / ``cash_balances``, ``cost_basis``, ``fund`` and
  ``positions``). The file is chosen as ``core.ledger.opening_events`` (the
  流水 page's 预览已有持仓) chooses it: the latest dated file on or before the
  opening date. A file whose every value can be read gives exactly the events
  ``opening_events`` gives (source ``legacy-opening``, ``external_id``
  ``<date>:<group>``, the same note), so the two paths can never book one
  account twice. Like that reader (and the engine's ``cash_balances``), a group
  without ``cash`` or ``fund`` has 0 of it: that is the file format's meaning.
* ``data/portfolio.json`` in the backend's working directory: the legacy 持仓
  page's store (``providers.portfolio.manual.ManualPortfolioProvider`` reads
  the same relative path). One account (default name 主账户, editable), cash
  in CNY, bare codes resolved with the importer's rule
  (``core.importers.codes.resolve``). It records no 累计净投入, so that is
  always asked for.

``draft()`` lists every value of one file. Each is either read from the file
or ``null`` with the reason (``problem``) and an entry in ``needs_input``:
a missing or unreadable quantity or cost, an unknown code, cash that disagrees
with itself. Nothing is estimated, rounded or defaulted beyond the format's
own zeros above. A position whose quantity is 0 was closed and is listed under
``skipped``, as ``opening_events`` drops it.

``propose()`` reads the file again, requires the fingerprint the draft showed,
takes the person's answers for exactly the values that needed input (a row
that needed input may be left out instead), and stores an ordinary ledger
proposal bound to the file's content (``core.ledger`` "Bound proposals", scope
``legacy_file``): it can be confirmed only while the file still holds that
content. Accounts the ledger already has are left out. The file itself is
never written, moved or renamed; after confirmation it is simply no longer read.

Messages are Chinese and safe to show. Values from the file appear in them as
short quoted text, never interpreted.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.ledger import (CURRENCIES, Ledger, LedgerError, LedgerUnavailable, amount, bookable, confirmed_state,
                         decimal_text, instrument, portfolio_directory, read_confirmed, register_binding, valid_date)

SCOPE = 'legacy_file'
HOLDINGS = 'holdings'
PORTFOLIO = 'portfolio'
HOLDINGS_SOURCE = 'legacy-opening'      # core.ledger.opening_events
PORTFOLIO_SOURCE = 'legacy-portfolio'
SOURCES = (HOLDINGS_SOURCE, PORTFOLIO_SOURCE)
PORTFOLIO_FILE = 'data/portfolio.json'
DEFAULT_ACCOUNT = '主账户'
MAX_FILE_BYTES = 5_000_000
MAX_POSITIONS = 1000
CURRENCY_ORDER = ('CNY', 'HKD', 'USD')
_HOLDINGS_SUBJECT = re.compile(r'holdings/(\d{4}-\d{2}-\d{2})\.json')


ALREADY_MIGRATED = '旧版持仓页（data/portfolio.json）已经迁移到账本，不能再迁移一次：再迁移会把同样的资产记两遍。'


class LegacyError(ValueError):
    """The file cannot be migrated as asked; the message is Chinese and safe to show."""


class LegacyStale(LegacyError):
    """The file changed (or went away) after the draft the request was made from."""


# -- where the files are --------------------------------------------------------

def portfolio_file() -> Path:
    """The legacy 持仓 page's file, relative to the working directory as its reader opens it."""
    return Path(PORTFOLIO_FILE)


def dated_holdings(directory: Optional[Path] = None) -> List[Path]:
    """``holdings/<YYYY-MM-DD>.json`` files, oldest first (other names are not holdings)."""
    folder = Path(directory or portfolio_directory()) / 'holdings'
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob('*.json'):
        try:
            valid_date(path.stem)
        except LedgerError:
            continue
        if path.is_file():
            found.append(path)
    return sorted(found)


def holdings_file(as_of: str, directory: Optional[Path] = None) -> Optional[Path]:
    """The latest dated holdings file on or before ``as_of`` (``opening_events``' rule)."""
    candidates = [path for path in dated_holdings(directory) if path.stem <= as_of]
    return max(candidates) if candidates else None


def find(directory: Optional[Path] = None) -> List[dict]:
    """The legacy files present: ``[{source, file, date}]`` (``file`` relative, never an absolute path)."""
    out = []
    holdings = dated_holdings(directory)
    if holdings:
        out.append({'source': HOLDINGS, 'file': f'holdings/{holdings[-1].name}', 'date': holdings[-1].stem,
                    'count': len(holdings)})
    if portfolio_file().is_file() and _portfolio_has_content():
        out.append({'source': PORTFOLIO, 'file': PORTFOLIO_FILE, 'date': None, 'count': 1})
    return out


def _portfolio_has_content() -> bool:
    """``portfolio.json`` with a position or nonzero cash; one that cannot be read counts too (it is shown)."""
    try:
        _raw, data = _read(portfolio_file())
    except LegacyError:
        return True
    positions = data.get('positions')
    cash, problem = _number(data.get('cash', 0), '现金')
    return bool(positions) or problem is not None or amount(cash) != 0


def _subject_path(subject: str) -> Optional[Path]:
    if subject == PORTFOLIO_FILE:
        return portfolio_file()
    match = _HOLDINGS_SUBJECT.fullmatch(subject or '')
    if match:
        return portfolio_directory() / 'holdings' / f'{match.group(1)}.json'
    return None


def _read(path: Path) -> Tuple[bytes, Any]:
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise LegacyError('旧持仓文件超过 5 MB，无法读取。')
        raw = path.read_bytes()
    except FileNotFoundError:
        raise LegacyStale('旧持仓文件已不存在。请重新预览。') from None
    except OSError:
        raise LegacyError('无法读取旧持仓文件（没有权限或文件损坏）。') from None
    try:
        data = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal)   # exact, as the ledger page reads it
    except UnicodeDecodeError:
        raise LegacyError('旧持仓文件不是 UTF-8 文本，无法读取。') from None
    except json.JSONDecodeError as exc:
        raise LegacyError(f'旧持仓文件不是有效的 JSON（第 {exc.lineno} 行附近），无法读取。') from None
    if not isinstance(data, dict):
        raise LegacyError('旧持仓文件的内容不是对象，无法读取。')
    return raw, data


def fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _binding_check(subject: str, token: str) -> Optional[str]:
    """``Ledger.propose`` / ``confirm``: None while the file still holds exactly the previewed content."""
    path = _subject_path(subject)
    if path is None:
        return '这个提案指向的旧持仓文件无法识别，不能确认。'
    try:
        current = fingerprint(path.read_bytes())
    except FileNotFoundError:
        return '旧持仓文件已不存在，这个提案不能再确认。请重新预览。'
    if current != token:
        return '旧持仓文件在预览之后有变化，这个提案已作废。请回到设置向导重新预览。'
    return None


register_binding(SCOPE, _binding_check)


# -- reading values -------------------------------------------------------------

def _json_text(raw: Any) -> str:
    """JSON as written, with the file's exact numbers (``Decimal``) unquoted."""
    if isinstance(raw, Decimal):
        return str(raw)
    if isinstance(raw, list):
        return '[' + ', '.join(_json_text(item) for item in raw) + ']'
    if isinstance(raw, dict):
        return '{' + ', '.join(f'{json.dumps(str(key), ensure_ascii=False)}: {_json_text(value)}'
                               for key, value in raw.items()) + '}'
    return json.dumps(raw, ensure_ascii=False)


def _shown(raw: Any) -> str:
    try:
        text = raw if isinstance(raw, str) else _json_text(raw)
    except RecursionError:
        text = '…'
    text = ' '.join(str(text).split())
    return f'“{text[:40]}{"…" if len(text) > 40 else ""}”'


def _number(raw: Any, what: str, *, positive: bool = False, nonnegative: bool = False) -> Tuple[Optional[str], Optional[str]]:
    """``(decimal text, None)``, or ``(None, problem)`` when the value cannot be read as it stands."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal, str)) or (isinstance(raw, str) and not raw.strip()):
        return None, f'{what}无法读取：{_shown(raw)}'
    try:
        value = amount(raw.strip() if isinstance(raw, str) else raw)
    except LedgerError:
        return None, f'{what}无法读取：{_shown(raw)}'
    if positive and value <= 0:
        return None, f'{what}必须大于 0（原文件为 {decimal_text(value)}）'
    if nonnegative and value < 0:
        return None, f'{what}不能为负数（原文件为 {decimal_text(value)}）'
    return decimal_text(value), None


def _value(key: str, value: Optional[str], problem: Optional[str], note: Optional[str] = None, **extra) -> dict:
    return {'key': key, 'value': value, 'problem': problem, 'note': note, **extra}


def _ticker(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(raw, str) or not raw.strip():
        return None, '原文件没有代码' if raw is None else f'代码无法读取：{_shown(raw)}'
    try:
        return bookable(raw), None
    except LedgerError as exc:
        try:
            instrument(raw)
        except LedgerError:
            return None, f'代码无法识别：{_shown(raw)}（需要带交易所前缀，如 SHA:600519）'
        return None, str(exc)    # a listing the ledger does not book (a B share): skip the row or type another


def _name(raw: Any) -> Optional[str]:
    return raw.strip()[:240] if isinstance(raw, str) and raw.strip() else None


def _position(account_key: str, index: int, raw: Any, ticker: Tuple[Optional[str], Optional[str]],
              quantity_raw: Any, cost_raw: Any, has_quantity: bool, has_cost: bool, choices: List[str],
              note: Optional[str]) -> dict:
    key = f'{account_key}.p{index}'
    if has_quantity:
        quantity, quantity_problem = _number(quantity_raw, '数量', positive=True)
    else:
        quantity, quantity_problem = None, '原文件没有数量'
    if has_cost:
        cost, cost_problem = _number(cost_raw, '成本价', nonnegative=True)
    else:
        cost, cost_problem = None, '原文件没有成本价'
    return {
        'key': key, 'index': index, 'name': _name(raw.get('name')) if isinstance(raw, dict) else None,
        'ticker': _value(f'{key}.ticker', ticker[0], ticker[1], note, choices=choices),
        'quantity': _value(f'{key}.quantity', quantity, quantity_problem),
        'cost_price': _value(f'{key}.cost_price', cost, cost_problem),
    }


def _closed(raw: Any) -> bool:
    """A row whose quantity reads as exactly 0: a closed position, dropped as ``opening_events`` drops it."""
    if not isinstance(raw, dict) or 'quantity' not in raw:
        return False
    value, problem = _number(raw['quantity'], '数量')
    return problem is None and amount(value) == 0


# -- the two formats --------------------------------------------------------------

def _cash_of_group(key: str, group: dict) -> List[dict]:
    if 'cash_balances' in group:
        balances = group['cash_balances']
        if not isinstance(balances, dict):
            return [_value(f'{key}.cash.CNY', None, f'现金（cash_balances）无法读取：{_shown(balances)}', currency='CNY')]
        unsupported = [str(unit) for unit in balances if unit not in CURRENCIES]
        if unsupported:
            raise LegacyError(f'账户的现金有账本不支持的币种（{"、".join(unsupported)}）。账本只支持 CNY、HKD、USD，'
                              '请先在旧文件中处理。')
        out = []
        for unit in sorted(balances, key=CURRENCY_ORDER.index):
            value, problem = _number(balances[unit], f'{unit} 现金')
            out.append(_value(f'{key}.cash.{unit}', value, problem, currency=unit))
        if 'cash' in group:
            legacy, legacy_problem = _number(group['cash'], 'CNY 现金')
            cny = next((item for item in out if item['currency'] == 'CNY'), None)
            stated = cny['value'] if cny else '0'
            if legacy_problem is None and stated is not None and amount(legacy) != amount(stated):
                problem = f'cash（{legacy}）与 cash_balances.CNY（{stated}）不一致，请填写实际的人民币现金'
                if cny:
                    cny.update(value=None, problem=problem)
                else:
                    out.insert(0, _value(f'{key}.cash.CNY', None, problem, currency='CNY'))
        return out
    if 'cash' in group:
        value, problem = _number(group['cash'], 'CNY 现金')
        return [_value(f'{key}.cash.CNY', value, problem, currency='CNY')]
    return [_value(f'{key}.cash.CNY', '0', None, '原文件没有现金字段，按 0 计（旧版也这样读）', currency='CNY')]


def _holdings_accounts(data: dict) -> Tuple[List[dict], List[dict]]:
    groups = data.get('groups')
    if not isinstance(groups, dict) or not groups:
        raise LegacyError('旧持仓文件里没有账户分组（groups），无法迁移。')
    accounts, skipped = [], []
    for number, (name, group) in enumerate(groups.items()):
        key = f'a{number}'
        if not isinstance(group, dict):
            raise LegacyError(f'账户 {_shown(name)} 的内容无法读取，无法迁移。')
        if 'cost_basis' in group:
            contributed, contributed_problem = _number(group['cost_basis'], '累计净投入（cost_basis）')
        else:
            contributed, contributed_problem = None, '原文件没有累计净投入（cost_basis）'
        if 'fund' in group:
            fund, fund_problem = _number(group['fund'], '基金估值', nonnegative=True)
        else:
            fund, fund_problem = '0', None
        raw_positions = group.get('positions', [])
        if not isinstance(raw_positions, list) or len(raw_positions) > MAX_POSITIONS:
            raise LegacyError(f'账户 {_shown(name)} 的持仓列表无法读取，无法迁移。')
        positions = []
        for index, raw in enumerate(raw_positions):
            if _closed(raw):
                skipped.append({'account': name, 'name': _name(raw.get('name')), 'ticker': raw.get('ticker')
                                if isinstance(raw.get('ticker'), str) else None, 'reason': '数量为 0（已清仓），不迁移'})
                continue
            row = raw if isinstance(raw, dict) else {}
            positions.append(_position(key, index, row, _ticker(row.get('ticker')), row.get('quantity'),
                                       row.get('cost_price'), 'quantity' in row, 'cost_price' in row, [], None))
        accounts.append({
            'key': key, 'account': name, 'account_editable': False,
            'cash': _cash_of_group(key, group),
            'contributed_cny': _value(f'{key}.contributed_cny', contributed, contributed_problem),
            'fund_cny': _value(f'{key}.fund_cny', fund, fund_problem),
            'positions': positions,
        })
    return accounts, skipped


MARKET_HINTS = {'a_share': {'short_code_market': 'A'}, 'hk_stock': {'hint': 'HKG'}, 'us_stock': {}}


def _portfolio_ticker(raw: dict) -> Tuple[Tuple[Optional[str], Optional[str]], List[str], Optional[str]]:
    from core.importers import codes
    symbol = raw.get('symbol')
    if not isinstance(symbol, str) or not symbol.strip():
        return (None, '原文件没有代码'), [], None
    market = raw.get('market', 'a_share')
    result = codes.resolve(symbol, **MARKET_HINTS.get(market if isinstance(market, str) else '', {}))
    if result.status == 'ok' and result.ticker:
        return (result.ticker, None), [], result.note
    return (None, result.message or f'代码无法识别：{_shown(symbol)}'), list(result.choices), None


def _portfolio_accounts(data: dict) -> Tuple[List[dict], List[dict]]:
    raw_positions = data.get('positions', [])
    if not isinstance(raw_positions, list) or len(raw_positions) > MAX_POSITIONS:
        raise LegacyError('旧持仓页文件的持仓列表无法读取，无法迁移。')
    key = 'a0'
    positions, skipped = [], []
    for index, raw in enumerate(raw_positions):
        if _closed(raw):
            skipped.append({'account': DEFAULT_ACCOUNT, 'name': _name(raw.get('name')), 'ticker': None,
                            'reason': '数量为 0（已清仓），不迁移'})
            continue
        row = raw if isinstance(raw, dict) else {}
        ticker, choices, note = _portfolio_ticker(row)
        positions.append(_position(key, index, row, ticker, row.get('quantity'), row.get('cost_price'),
                                   'quantity' in row, 'cost_price' in row, choices, note))
    if 'cash' in data:
        cash, cash_problem = _number(data['cash'], 'CNY 现金')
        cash_note = None
    else:
        cash, cash_problem, cash_note = '0', None, '原文件没有现金字段，按 0 计（旧版也这样读）'
    return [{
        'key': key, 'account': DEFAULT_ACCOUNT, 'account_editable': True,
        'cash': [_value(f'{key}.cash.CNY', cash, cash_problem, cash_note, currency='CNY')],
        'contributed_cny': _value(f'{key}.contributed_cny', None, '旧版持仓页没有记录累计净投入'),
        'fund_cny': _value(f'{key}.fund_cny', '0', None, '旧版持仓页不记录基金'),
        'positions': positions,
    }], skipped


def _flag_duplicates(accounts: List[dict]) -> None:
    for account in accounts:
        seen: Dict[str, int] = {}
        for row in account['positions']:
            ticker = row['ticker']['value']
            if ticker is None:
                continue
            if ticker in seen:
                row['ticker'].update(value=None, problem=f'与第 {seen[ticker] + 1} 行是同一标的 {ticker}：'
                                                          '请改成另一只标的，或不迁移这一行')
            else:
                seen[ticker] = row['index']


# -- the draft ------------------------------------------------------------------

LABELS = {'ticker': '代码', 'quantity': '数量', 'cost_price': '成本价', 'contributed_cny': '累计净投入（CNY）',
          'fund_cny': '基金估值（CNY）'}


def _needs_input(accounts: List[dict]) -> List[dict]:
    out = []

    def add(account, item, kind, label, row=None):
        if item['problem'] is not None:
            out.append({'key': item['key'], 'kind': kind, 'account': account['account'],
                        'label': f'{account["account"]} · {label}', 'problem': item['problem'],
                        'choices': item.get('choices') or [], 'row': row})

    for account in accounts:
        if account.get('exists'):
            continue
        for item in account['cash']:
            add(account, item, 'cash', f'{item["currency"]} 现金')
        add(account, account['contributed_cny'], 'contributed', LABELS['contributed_cny'])
        add(account, account['fund_cny'], 'fund', LABELS['fund_cny'])
        for row in account['positions']:
            subject = row['name'] or row['ticker']['value'] or f'第 {row["index"] + 1} 行'
            for field in ('ticker', 'quantity', 'cost_price'):
                add(account, row[field], field if field != 'cost_price' else 'cost', f'{subject} {LABELS[field]}', row['key'])
    return out


def migrated(timeout: float = 2) -> List[dict]:
    """Confirmed opening balances that came from a legacy file (either path) and were not reversed (冲销):
    ``[{account, source, date}]``. A reversed one counts as not migrated, so the file can be migrated again.

    Read with ``core.ledger.read_confirmed`` (never writes); no ledger or no such event is an empty list.
    Raises what that raises (``LedgerUnavailable`` when the ledger cannot be read now).
    """
    snapshot = read_confirmed(timeout=timeout)
    events = snapshot.history() if snapshot is not None else []
    reversed_ids = {event.get('target_id') for event in events if event.get('kind') == 'reverse'}
    return [{'account': event.get('account'), 'source': event['source'], 'date': event.get('date')}
            for event in events if event.get('kind') == 'opening' and event.get('source') in SOURCES
            and event.get('id') not in reversed_ids]


def pending(existing: set, moved: List[dict]) -> List[dict]:
    """The legacy sources that still hold something the ledger does not: ``[{source, file, ..., accounts}]``.

    For the dated holdings, ``accounts`` lists the latest file's accounts that are not in the ledger (by name,
    as ``draft`` marks ``exists``); ``portfolio.json`` names no account, so it is pending until an opening
    from it was confirmed (``moved``, from ``migrated()``). A file that cannot be read is pending, without
    names (its draft says what is wrong).
    """
    out = []
    for entry in find():
        if entry['source'] == PORTFOLIO:
            if not any(item['source'] == PORTFOLIO_SOURCE for item in moved):
                out.append({**entry, 'accounts': []})
            continue
        try:
            _raw, data = _read(dated_holdings()[-1])
        except LegacyError:
            out.append({**entry, 'accounts': []})
            continue
        groups = data.get('groups')
        if not isinstance(groups, dict) or not groups:
            out.append({**entry, 'accounts': []})
            continue
        left = [str(name) for name in groups if name not in existing]
        if left:
            out.append({**entry, 'accounts': left})
    return out


def _existing_accounts() -> set:
    try:
        state = confirmed_state(timeout=2)
    except LedgerUnavailable as exc:
        raise LegacyError(f'暂时读不到账本：{exc.reason}。') from None
    except Exception:
        raise LegacyError('暂时读不到账本，请稍后重试。') from None
    return set((state or {}).get('accounts', {}))


def draft(source: Optional[str] = None, as_of: Optional[str] = None) -> dict:
    """What migrating one legacy file would book, with every value that needs the person's input.

    ``source`` is ``holdings`` or ``portfolio`` (default: the first of them that still has something to
    migrate, see ``pending``; else the holdings files when there are any).
    ``as_of`` is the opening date: the holdings file is the latest one dated on or before it, and it
    defaults to that latest file's date; for ``portfolio.json`` it defaults to the file's ``updated_at``
    day and is required when the file has none.
    """
    available = find()
    if not available:
        raise LegacyError('没有发现旧版持仓文件。')
    if source is None:
        # The first source that still has something to migrate (after one was migrated, the other).
        source = available[0]['source']
        if len(available) > 1:
            try:
                left = pending(_existing_accounts(), migrated())
            except Exception:
                left = []
            source = left[0]['source'] if left else source
    if source not in (HOLDINGS, PORTFOLIO) or source not in {item['source'] for item in available}:
        raise LegacyError('没有这种旧版持仓文件。')
    if as_of is not None:
        try:
            valid_date(as_of)
        except LedgerError:
            raise LegacyError('期初日期格式应为 YYYY-MM-DD。') from None
    if source == HOLDINGS:
        if as_of is None:
            as_of = dated_holdings()[-1].stem
        path = holdings_file(as_of)
        if path is None:
            raise LegacyError(f'{as_of} 当天或之前没有旧持仓文件。请换一个期初日期。')
        relative, file_date = f'holdings/{path.name}', path.stem
    else:
        path, relative, file_date = portfolio_file(), PORTFOLIO_FILE, None
    raw, data = _read(path)
    accounts, skipped = (_holdings_accounts if source == HOLDINGS else _portfolio_accounts)(data)
    if source == PORTFOLIO:
        stamp = data.get('updated_at')
        if isinstance(stamp, str) and re.match(r'\d{4}-\d{2}-\d{2}', stamp):
            try:
                file_date = valid_date(stamp[:10])
            except LedgerError:
                file_date = None
        if as_of is None:
            as_of = file_date
        if as_of is None:
            raise LegacyError('旧版持仓页文件没有记录日期，请填写期初日期。')
    _flag_duplicates(accounts)
    existing = _existing_accounts()
    moved = _moved()
    left = {item['source'] for item in pending(existing, moved)}
    for account in accounts:
        # A dated file's account is in the ledger by its name. portfolio.json's one account, whose name the person
        # chooses, is in the ledger once an opening from that file was confirmed, under whatever name.
        account['exists'] = account['account'] in existing if not account['account_editable'] \
            else any(item['source'] == PORTFOLIO_SOURCE for item in moved)
    return {
        'source': source, 'file': relative, 'file_date': file_date, 'date': as_of, 'token': fingerprint(raw),
        'accounts': accounts, 'needs_input': _needs_input(accounts), 'skipped': skipped,
        'sources': [{**item, 'pending': item['source'] in left} for item in available],
    }


def _moved() -> List[dict]:
    try:
        return migrated()
    except LedgerUnavailable as exc:
        raise LegacyError(f'暂时读不到账本：{exc.reason}。') from None
    except Exception:
        raise LegacyError('暂时读不到账本，请稍后重试。') from None


# -- the proposal ---------------------------------------------------------------

def _answer(kind: str, text: Any) -> str:
    """A typed answer, checked the way the ledger will check it."""
    if not isinstance(text, str) or not text.strip() or len(text) > 128:
        raise LegacyError('请填写需要补充的每一项。')
    text = text.strip()
    if kind == 'ticker':
        from core.importers import codes
        try:
            instrument(text)
        except LedgerError:
            raise LegacyError(f'代码无法识别：{_shown(text)}（需要带交易所前缀，如 SHA:600519）') from None
        try:
            return bookable(codes.check_ticker(text))
        except LedgerError as exc:
            raise LegacyError(f'{_shown(text)} 不能入账：{exc}') from None
    if kind == 'account':
        if len(text) > 240:
            raise LegacyError('账户名最多 240 个字符。')
        return text
    what = {'quantity': '数量', 'cost': '成本价', 'cash': '现金', 'contributed': '累计净投入', 'fund': '基金估值'}[kind]
    value, problem = _number(text, what, positive=kind == 'quantity', nonnegative=kind in ('cost', 'fund'))
    if problem:
        raise LegacyError(problem)
    return value


def propose(source: str, as_of: str, token: str, fills: Optional[Dict[str, Any]] = None,
            skip: Optional[List[str]] = None, account_name: Optional[str] = None) -> dict:
    """Store the opening-balance proposal for one legacy file (see the module docstring).

    ``fills`` maps the ``key`` of each ``needs_input`` item to the typed value; ``skip`` lists position
    rows (``a0.p3``) that needed input and are left out instead; ``account_name`` renames the one account
    of ``portfolio.json``.
    """
    if source not in (HOLDINGS, PORTFOLIO):
        raise LegacyError('请选择要迁移的旧文件。')
    if not isinstance(as_of, str):
        raise LegacyError('请填写期初日期。')
    current = draft(source, as_of)
    if source == PORTFOLIO and current['accounts'][0]['exists']:
        raise LegacyError(ALREADY_MIGRATED)     # whatever the page asks: one copy of these assets
    if not isinstance(token, str) or current['token'] != token:
        raise LegacyStale('旧持仓文件在预览之后有变化。请重新预览后再生成入账预览。')
    fills = dict(fills or {})
    skip = list(skip or [])
    needed = {item['key']: item for item in current['needs_input']}
    unknown = [key for key in fills if key not in needed]
    if unknown:
        raise LegacyError('只能填写标为“需要填写”的项目；其余数值以旧文件为准。')
    skippable = {item['row'] for item in needed.values() if item['row']}
    if any(key not in skippable for key in skip):
        raise LegacyError('只有需要补充的持仓行可以不迁移。')
    answers = {}
    missing = []
    for key, item in needed.items():
        if item['row'] and item['row'] in skip:
            continue
        if key not in fills or fills[key] in (None, ''):
            missing.append(item['label'])
            continue
        answers[key] = _answer(item['kind'], fills[key])
    if missing:
        raise LegacyError(f'还有 {len(missing)} 项需要填写：{"、".join(missing[:5])}{"等" if len(missing) > 5 else ""}。')

    def resolved(item):
        return answers.get(item['key'], item['value'])

    existing = _existing_accounts()
    events = []
    for account in current['accounts']:
        name = account['account']
        if account['account_editable'] and account_name not in (None, ''):
            name = _answer('account', account_name)
        if account['exists']:
            continue
        if name in existing:
            raise LegacyError(f'账本中已有账户 {_shown(name)}，请换一个名称。')
        positions = []
        seen = set()
        for row in account['positions']:
            if row['key'] in skip:
                continue
            ticker = resolved(row['ticker'])
            if ticker in seen:
                raise LegacyError(f'{ticker} 出现了两次。同一账户的同一标的只能有一行。')
            seen.add(ticker)
            position = {'ticker': ticker, 'quantity': resolved(row['quantity']), 'cost_price': resolved(row['cost_price'])}
            if row['name']:
                position['name'] = row['name']
            positions.append(position)
        if source == HOLDINGS:
            origin = {'source': HOLDINGS_SOURCE, 'external_id': f'{as_of}:{name}',
                      'note': f'Opening balance from holdings dated {current["file_date"]}'}
        else:
            origin = {'source': PORTFOLIO_SOURCE, 'external_id': f'{as_of}:{name}',
                      'note': 'Opening balance from the legacy portfolio.json'}
        events.append({'kind': 'opening', 'date': as_of, 'account': name,
                       'cash_balances': {item['currency']: resolved(item) for item in account['cash']},
                       'positions': positions, 'contributed_cny': resolved(account['contributed_cny']),
                       'fund_cny': resolved(account['fund_cny']), **origin})
    if not events:
        raise LegacyError('没有可以迁移的账户：旧文件中的账户都已在账本中。')
    binding = {'scope': SCOPE, 'subject': current['file'], 'token': token,
               'info': {'source': source, 'file': current['file'], 'date': as_of}}
    ledger = Ledger(portfolio_directory() / 'ledger.sqlite3')
    return ledger.propose(events, binding=binding)
