"""Account identity columns in a broker file: 资金账号 and 股东代码.

One ledger account stands for one broker account, but a file can hold rows of
several (an export of all of a person's accounts, two files pasted together).
Identity dimensions:

* ``fund_account``: the 资金账号 column, one value per broker account.
* ``shareholder:SH`` / ``shareholder:SZ`` / ``shareholder:other``: the 股东代码
  column by its shape (a letter and 9 digits is Shanghai, 10 digits Shenzhen).
  An account has one Shanghai and one Shenzhen code, so only two codes of the
  same market mean two accounts.

A dimension with two or more values makes the file *mixed*. The accepted values
are, in this order: the person's choice (PATCH ``account_filter``), the choice
saved with the mapping for this ledger account and header layout, otherwise
every dimension's only value plus the most frequent value of each mixed
dimension (none on a tie). A mixed file without an explicit or saved choice
cannot be proposed (a blocker), so the dominant identity is only a preview.

Which rows a choice (explicit or saved) accepts: only those that positively match
it. A row needs review (``needs: ["account"]``) when

* one of its values lies in a chosen dimension, or in one with several values in
  this file, but is not chosen (another account);
* it has values but none of them is chosen (an identity never accepted, for example
  a 股东代码 when only 资金账号 111 was chosen and this file's 资金账号 is blank);
* it has no value at all, unless ``""`` is chosen: a choice names identities, and a row
  that states none cannot be shown to be one of them.

Without a choice, a row with a value other than the accepted one of its dimension
needs review, and in a mixed file so does a row without any account value.

Identities are remembered per ledger account (``memory``): the values of the rows
confirmed into it by earlier imports (kept with their proposals in the ledger) and
the person's explicit choices (``import_identities`` in ``desk.sqlite3``: the chosen
values ``accepted``, the other values of the chosen or mixed dimensions ``excluded``).
Unless the person chose this file's identities explicitly, a row needs review when its
value was excluded for the account before, or when the account has accepted values in
that dimension and this is not one of them, even in a file with a single identity.

``Session.accounts`` reports the decision applied: a value is ``accepted`` when it is
one of the accepted values and the memory does not refuse it (so sending the accepted
values back as ``account_filter`` makes the same choice explicit), and ``rows_kept_out``
counts the rows this decision makes 需处理 (``needs: ["account"]``).
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Dict, Iterable, List, Optional, Tuple

from core import desk_store
from core.importers import ImportFailure, codes
from core.importers.values import clean, is_empty

LABELS = {'fund_account': '资金账号', 'shareholder:SH': '沪市股东代码', 'shareholder:SZ': '深市股东代码',
          'shareholder:other': '股东代码'}
ORDER = list(LABELS)
MAX_VALUES = 100

MIGRATIONS = [[
    """CREATE TABLE import_identities (
        account TEXT NOT NULL,
        dimension TEXT NOT NULL,
        value TEXT NOT NULL,
        state TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account, dimension, value))""",
]]
SCHEMA = 'import_identities'
desk_store.register_schema(SCHEMA, MIGRATIONS)


def chosen_identities(account: str) -> List[list]:
    """``[[dimension, value, 'accepted' | 'excluded'], ...]``: the person's explicit choices for a ledger account.
    A read (nothing is created); sorted, so it can be part of a fingerprint."""
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return []
    with desk_store.connect() as db:
        return [[row['dimension'], row['value'], row['state']] for row in db.execute(
            'SELECT dimension, value, state FROM import_identities WHERE account=? ORDER BY dimension, value',
            (account,))]


def remember_choice(account: str, table, mapping: dict, choice: dict) -> None:
    """Record an explicit ``account_filter`` for the ledger account: its values accepted, the file's other values
    of the chosen dimensions and of the mixed ones excluded. A write (joins an open desk transaction)."""
    present = table_identities(table, mapping)
    accepted = {tuple(pair) for pair in choice.get('pairs') or []}
    counts: Dict[str, set] = {}
    for dimension, value in present:
        counts.setdefault(dimension, set()).add(value)
    constrained = {d for d, _v in accepted} | {d for d, values in counts.items() if len(values) > 1}
    excluded = {(d, v) for d, v in present if d in constrained and (d, v) not in accepted}
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        stamp = desk_store.now()
        for (dimension, value), state in [*((pair, 'accepted') for pair in accepted),
                                          *((pair, 'excluded') for pair in excluded)]:
            db.execute('INSERT INTO import_identities(account, dimension, value, state, updated_at) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(account, dimension, value) DO UPDATE SET state=excluded.state, '
                       'updated_at=excluded.updated_at', (account, dimension, value, state, stamp))


def memory(confirmed: Iterable, chosen: Iterable) -> Dict[Tuple[str, str], str]:
    """{(dimension, value): 'accepted' | 'excluded'} for a ledger account: the identities of rows confirmed by
    earlier imports (accepted), overridden by the person's explicit choices (``chosen_identities``)."""
    out: Dict[Tuple[str, str], str] = {}
    for pair in confirmed:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] in LABELS:
            out[(pair[0], str(pair[1]))] = 'accepted'
    for dimension, value, state in chosen:
        if dimension in LABELS and state in ('accepted', 'excluded'):
            out[(dimension, value)] = state
    return out


def refused(dimension: str, value: str, remembered: Dict[Tuple[str, str], str], account: str) -> Optional[str]:
    """Why the account's memory refuses one identity, or None."""
    state = remembered.get((dimension, value))
    if state == 'excluded':
        return (f'{LABELS[dimension]} {value} 以前导入账户“{account}”时被排除了（不属于这个账户）。它不会入账；'
                '如果它确实属于这个账户，请在“账户识别”里选择它。')
    known = sorted(v for (d, v), s in remembered.items() if d == dimension and s == 'accepted')
    if known and state != 'accepted':
        return (f'账户“{account}”以前导入的{LABELS[dimension]}是 {"、".join(known[:5])}，这一行是 {value}。'
                '它不会入账；如果它也属于这个账户，请在“账户识别”里选择它。')
    return None


def _remembered(row, remembered: Dict[Tuple[str, str], str], account: str) -> Optional[str]:
    """Why the account's memory refuses one of the row's identities, or None."""
    for dimension, value in row.account_ids:
        reason = refused(dimension, value, remembered, account)
        if reason:
            return reason
    return None


def holder_dimension(value: str) -> str:
    if re.fullmatch(r'[A-Z]\d{9}', value):
        return 'shareholder:SH'
    if re.fullmatch(r'\d{10}', value):
        return 'shareholder:SZ'
    return 'shareholder:other'


def identity(fund_account: str, shareholder: str) -> List[Tuple[str, str]]:
    """The row's (dimension, value) pairs."""
    out = []
    if fund_account and not is_empty(fund_account):
        out.append(('fund_account', codes.bare(fund_account)[:64]))
    if shareholder and not is_empty(shareholder):
        value = codes.bare(shareholder)[:64]
        out.append((holder_dimension(value), value))
    return out


def table_identities(table, mapping: dict) -> Counter:
    """(dimension, value) → rows, read straight from the mapped columns (for validating a choice)."""
    found: Counter = Counter()
    fund, holder = mapping.get('fund_account'), mapping.get('shareholder')
    for _number, cells in table.data():
        cell = lambda index: clean(cells[index]) if index is not None and index < len(cells) else ''
        found.update(identity(cell(fund), cell(holder)))
    return found


def choice(table, mapping: dict, requested) -> dict:
    """``account_filter`` from a PATCH: a list of values shown in ``Session.accounts``; ``""`` accepts rows
    without account values. Every other value must occur in the file."""
    if not isinstance(requested, list) or len(requested) > MAX_VALUES or not all(isinstance(v, str) for v in requested):
        raise ImportFailure('账户识别的选择必须是文件里的账户值列表。')
    present = table_identities(table, mapping)
    pairs, blank = set(), False
    for value in requested:
        if not value.strip():
            blank = True
            continue
        wanted = codes.bare(value)[:64]
        matches = {(dimension, found) for dimension, found in present if found == wanted}
        if not matches:
            raise ImportFailure(f'文件里没有账户“{value[:64]}”。请从“账户识别”列出的账户中选择。')
        pairs |= matches
    return {'pairs': sorted([list(p) for p in pairs]), 'blank': blank, 'source': 'chosen'}


def saved_choice(options: dict) -> Optional[dict]:
    raw = (options or {}).get('account_filter')
    if not isinstance(raw, dict) or not isinstance(raw.get('pairs'), list):
        return None
    pairs = [list(p) for p in raw['pairs'][:MAX_VALUES]
             if isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p) and p[0] in LABELS]
    return {'pairs': pairs, 'blank': raw.get('blank') is True, 'source': 'saved'}


def decide(rows, chosen: Optional[dict], account: str,
           remembered: Optional[Dict[Tuple[str, str], str]] = None) -> Optional[dict]:
    """Mark rows outside the accepted identities as review; return ``Session.accounts`` (None: no such columns).
    ``remembered``: the ledger account's memory (``memory``); it applies unless the choice is explicit."""
    remembered = remembered or {}
    counts: Dict[str, Counter] = {}
    for row in rows:
        for dimension, value in row.account_ids:
            counts.setdefault(dimension, Counter())[value] += 1
    if not counts and not chosen:
        return None
    mixed = [d for d, c in counts.items() if len(c) > 1]
    if chosen:
        pairs = {tuple(p) for p in chosen.get('pairs') or []}
        blank, source = bool(chosen.get('blank')), chosen.get('source', 'chosen')
    else:
        pairs, blank, source = set(), False, 'dominant' if mixed else 'single'
        for dimension, counter in counts.items():
            top = counter.most_common(2)
            if len(top) == 1 or top[0][1] > top[1][1]:
                pairs.add((dimension, top[0][0]))
    chosen_dimensions = {d for d, _v in pairs}
    constrained = (set(mixed) | chosen_dimensions) & set(counts)
    explicit = bool(chosen) and source == 'chosen'
    without = 0
    for row in rows:
        if row.status == 'skipped' and row.category == 'footer':
            continue
        if chosen:
            without += _check_chosen(row, pairs, chosen_dimensions | set(mixed), blank, account, bool(mixed))
        else:
            relevant = [(d, v) for d, v in row.account_ids if d in constrained]
            outside = [(d, v) for d, v in relevant if (d, v) not in pairs]
            if outside:
                _reject(row, _other_account(outside[0], pairs, account))
            elif not relevant and mixed:
                without += 1
                if not blank:
                    _reject(row, f'文件里有多个账户（{_summary(counts, mixed)}），这一行没有资金账号或股东代码，无法判断属于哪一个。'
                                 '它不会入账；如确属这个账户，请在“账户识别”里选择“没有账号的行”。')
        if not explicit and row.status != 'review':
            refusal = _remembered(row, remembered, account)
            if refusal:
                _reject(row, refusal)
    # The decision applied: the accepted values the memory does not refuse (it does not apply to an explicit choice).
    applied = {(d, v) for d, v in pairs if explicit or not refused(d, v, remembered, account)}
    columns = []
    for dimension in sorted(counts, key=ORDER.index):
        values = sorted(counts[dimension].items(), key=lambda item: (-item[1], item[0]))
        columns.append({'dimension': dimension, 'label': LABELS[dimension],
                        'values': [{'value': v, 'rows': n, 'accepted': (dimension, v) in applied} for v, n in values]})
    return {'mixed': bool(mixed), 'source': source, 'columns': columns,
            'accepted': sorted({v for d, v in applied if d in counts}), 'accept_rows_without_account': blank,
            'rows_without_account': without, 'rows_kept_out': sum(1 for row in rows if row.other_account),
            'summary': _summary(counts, mixed) if mixed else None,
            'remembered': [{'dimension': d, 'label': LABELS[d], 'value': v, 'state': state}
                           for (d, v), state in sorted(remembered.items())]}


def _other_account(found: Tuple[str, str], pairs: set, account: str) -> str:
    dimension, value = found
    accepted = '、'.join(sorted(v for d, v in pairs if d == dimension)) or '（还没有选择）'
    return (f'这一行属于{LABELS[dimension]} {value}，不是为账户“{account}”选择的{LABELS[dimension]} {accepted}。'
            '它不会入账；如果它也属于这个账户，请在“账户识别”里选择它。')


def _check_chosen(row, pairs: set, constrained: set, blank: bool, account: str, mixed: bool) -> int:
    """Apply an explicit or saved choice to one row; returns 1 when the row has no account value.
    ``constrained``: the chosen dimensions and those with several values in this file (``mixed``)."""
    ids = row.account_ids
    dimensions = {d for d, _v in pairs}
    outside = [(d, v) for d, v in ids if d in constrained and (d, v) not in pairs]
    if outside:
        _reject(row, _other_account(outside[0], pairs, account))
        return 0
    if ids and not any((d, v) in pairs for d, v in ids):
        names = '、'.join(f'{LABELS[d]} {v}' for d, v in ids)
        chosen = '、'.join(f'{LABELS[d]} {v}' for d, v in sorted(pairs)) or '只有“没有账户信息的行”'
        _reject(row, f'这一行的{names}没有被选为账户“{account}”的账户（已选择：{chosen}）。它不会入账；'
                     '如果它也属于这个账户，请在“账户识别”里选择它。')
        return 0
    if ids:
        return 0
    if not blank:   # a choice names identities: a row that states none is not shown to be one of them
        what = '资金账号' if 'fund_account' in dimensions else '股东代码' if dimensions else '资金账号或股东代码'
        _reject(row, f'这一行没有{what}，无法确认它属于为账户“{account}”选择的账户。它不会入账；如确属这个账户，'
                     '请在“账户识别”里选择“没有账号的行”。')
    return 1


def blocker(view: Optional[dict], account: str) -> Optional[str]:
    if not view or not view['mixed'] or view['source'] != 'dominant':
        return None
    return (f'文件里有多个账户：{view["summary"]}。预览只导入行数最多的那一个，其余的行需处理。'
            f'请确认哪些对应账户“{account}”（在“账户识别”里选择），确认后会记住。')


def _summary(counts: Dict[str, Counter], dimensions: Iterable[str]) -> str:
    parts = []
    for dimension in sorted(dimensions, key=ORDER.index):
        values = sorted(counts[dimension].items(), key=lambda item: (-item[1], item[0]))
        parts.append(LABELS[dimension] + ' ' + '、'.join(f'{v}（{n} 行）' for v, n in values[:5]))
    return '；'.join(parts)


def _reject(row, message: str) -> None:
    if row.status in ('new', 'possible_duplicate', 'review'):
        row.messages.insert(0, message)
        row.other_account = True   # its balances are not this account's (reconcile.balances)
        row.set('review', needs=['account'])
