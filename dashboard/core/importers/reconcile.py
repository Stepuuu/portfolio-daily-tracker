"""What the ledger would say about each row, and how the result compares with the file.

``simulate`` replays the included rows on top of the confirmed ledger, in date
order, with the ledger's own ``replay``. A row the ledger would refuse (a sell of
more shares than held, a buy without enough cash, a split without a position)
needs review with the reason, instead of failing the whole proposal later. It
also computes split ratios: ratio = (held + added) / held at that point, and the
split needs review when that is not exact or the held quantity is unknown.

To stay fast, rows are replayed in chunks on top of a synthetic opening that
carries the account's state (cash, positions, contributions, fund value); a chunk
the ledger refuses is retried row by row. When confirmed events of the account
are dated after the first imported row, the full ledger is replayed instead and
a failing row is found by bisection.

``balances`` compares the resulting cash per currency and share counts with the
file's last 资金余额 / 股份余额 of this ledger account's own rows (a row of another
资金账号 / 股东代码, or one the account choice does not accept, reports another
account's balances and is left out), share counts by listing (the ledger books a
listing under the spelling its account holds it): 一致, or the difference with
likely causes. A balance the ledger meets while rows that move that cash (or those
shares) are still 需处理, 不支持 or unchecked is 未核对 with the number of rows left
out, never 一致; a cash-only row (dividend, interest, fee …) counts for the cash
line only. The state compared is always the ledger as of the file's last date
plus the file's rows: events confirmed with a later date (also after this file was
imported) never change the comparison.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext
from typing import Dict, List, Optional

from core.ledger import LedgerError, replay, replay_as_of
from core.importers.dedupe import same_listing
from core.importers.transactions import _CASH_SIGN, BOOKABLE, NOT_HERE, Row, _event, ledger_reason
from core.importers.values import money, text

CHUNK = 200
MAX_BISECT = 12
TOLERANCE = Decimal('0.005')


def _listing_quantity(state: dict, ticker) -> Decimal:
    """Shares of ``ticker``'s listing in ``state``, under every spelling (``identity_key``): the ledger books a
    listing under the spelling its account holds it, and a ledger from before that rule may hold it under two."""
    return sum((Decimal(position['quantity']) for spelling, position in state['positions'].items()
                if same_listing(spelling, ticker)), Decimal(0))


def _opening(account: str, state: dict, day: str) -> dict:
    return {'kind': 'opening', 'date': day, 'account': account, 'id': '__import_check__', 'source': 'import-check',
            'cash_balances': dict(state['cash_balances']), 'contributed_cny': state['contributed_cny'],
            'fund_cny': state['fund_cny'], '_sequence': 0,
            'positions': [{'ticker': t, 'name': p['name'], 'quantity': p['quantity'], 'cost_price': p['cost_price']}
                          for t, p in state['positions'].items()]}


def _apply(account: str, state: dict, events: List[dict]) -> dict:
    day = min(event['date'] for event in events)
    sequenced = [{**event, '_sequence': index + 1} for index, event in enumerate(events)]
    return replay([_opening(account, state, day)] + sequenced)['accounts'][account]


def explain(exc: Exception, row: Row, state: Optional[dict]) -> str:
    message = str(exc)
    event = next((e for e in row.events if e['kind'] in ('buy', 'sell', 'split')), row.events[0] if row.events else {})
    unit = event.get('currency')
    if 'Insufficient native cash' in message:
        need = Decimal(event['quantity']) * Decimal(event['price']) + Decimal(event.get('fee', '0'))
        have = Decimal(state['cash_balances'].get(unit, '0')) if state else None
        return (f'现金不足：这笔买入需要 {unit} {money(need)}' + (f'，账本当时只有 {money(have)}' if have is not None else '') +
                '。可能缺少更早的银行转证券或卖出记录；如确为融资买入，可对这一行选择“允许融资”。')
    if 'Cannot sell more shares than held' in message:
        held = (state['positions'].get(event.get('ticker'), {}).get('quantity', '0') if state else None)
        return (f'卖出 {event.get("quantity")} 股，但账本当时' + (f'只持有 {held} 股' if held is not None else '持仓不足') +
                '。请核对数量；如果数量没错，请先在账本里补录期初持仓或更早的买入。')
    if 'Split requires an existing position' in message:
        return f'送转股需要已有持仓，但账本当时没有这只证券。{NOT_HERE}：请先在账本里补录这只证券的持仓或买入。'
    if 'Create an opening balance' in message:
        return f'账户还没有期初余额。{NOT_HERE}：请先建立期初。'
    return f'账本无法接受这一行：{ledger_reason(exc)}。{NOT_HERE}：请核对后在账本里手工记录，或把它设为跳过。'


def opening_date(existing: List[dict], account: str) -> Optional[str]:
    reversed_ids = {e['target_id'] for e in existing if e['kind'] == 'reverse'}
    dates = [e['date'] for e in existing if e['kind'] == 'opening' and e.get('account') == account
             and e['id'] not in reversed_ids]
    return min(dates) if dates else None


def _split_event(row: Row, ctx, state: dict) -> bool:
    held = state['positions'].get(row.ticker)
    if not held:
        row.set('review', '送转股：账本当时没有这只证券的持仓，无法计算比例。请核对。', needs=['kind'])
        return False
    held_quantity = Decimal(held['quantity'])
    total = held_quantity + row.split_added
    if row.share_balance is not None and row.share_balance != total:
        row.set('review', f'送转股：账本持有 {text(held_quantity)} + 到账 {text(row.split_added)} = {text(total)}，'
                          f'但文件的股份余额是 {text(row.share_balance)}。请核对到账数量。', needs=['quantity'])
        return False
    with localcontext() as context:
        context.prec = 60
        ratio = total / held_quantity
        exact = ratio.quantize(Decimal('1e-12'))
    if exact * held_quantity != total:
        row.set('review', f'送转股比例 {text(total)}/{text(held_quantity)} 不能用有限小数精确表示，账本按比例记送转股，'
                          f'会产生零碎股数。{NOT_HERE}：请把它设为跳过，再在账本里手工调整这只证券的持仓。')
        return False
    row.notes.append(f'送转股比例 = ({text(held_quantity)} + {text(row.split_added)}) / {text(held_quantity)} = {text(exact)}')
    event = _event(row, ctx, '', kind='split', ticker=row.ticker, ratio=exact)
    if event is None:
        return False
    row.events = [event]
    return True


def simulate(rows: List[Row], existing: List[dict], account: str, ctx) -> Optional[dict]:
    """Mark rows the ledger would refuse; return the account's resulting state (None if unknown)."""
    start = opening_date(existing, account)
    candidates = sorted((r for r in rows if r.status in BOOKABLE and r.include and (r.events or r.category == 'split')),
                        key=lambda r: r.order)
    kept = []
    for row in candidates:
        if start and row.date < start:
            row.set('skipped', f'早于账户期初（{start}）：期初余额应已包含这一笔。')
            continue
        if start and row.date == start:
            row.notes.append(f'与账户期初同一天（{start}）：如果期初余额已经包含这一笔，请取消勾选。')
        kept.append(row)
    candidates = kept
    try:
        base = replay(existing)
    except LedgerError:
        return None
    if account not in base['accounts']:
        return None
    if not candidates:
        # Nothing left to add (all imported, excluded or needing review): the ledger as of the file's last
        # date, so events confirmed later never change this file's comparison.
        dates = [r.date for r in rows if r.date]
        if not dates:
            return base['accounts'][account]
        try:
            return replay_as_of(existing, max(dates))['accounts'].get(account)
        except LedgerError:
            return None
    first = candidates[0].date
    touches = [e for e in existing if e['kind'] != 'reverse' and e['date'] > first
               and (e.get('account') == account or e.get('to_account') == account)]
    later_reversal = any(e['kind'] == 'reverse' and e['date'] > first for e in existing)
    transfers = any(e['kind'] == 'transfer' for r in candidates for e in r.events)
    if touches or later_reversal or transfers:
        return _full(candidates, existing, account, max(r.date for r in rows if r.date))
    state = base['accounts'][account]
    index = 0
    while index < len(candidates):
        row = candidates[index]
        if row.category == 'split' and not row.events:
            if _split_event(row, ctx, state):
                state = _one(row, account, state)
            index += 1
            continue
        end = index
        while end < len(candidates) and end - index < CHUNK and not (
                candidates[end].category == 'split' and not candidates[end].events):
            end += 1
        chunk = candidates[index:end]
        try:
            state = _apply(account, state, [e for r in chunk for e in r.events])
        except LedgerError:
            for item in chunk:
                state = _one(item, account, state)
        index = end
    return state


def refusal_needs(exc: Exception) -> List[str]:
    """The row edit that can settle a refusal ``explain`` describes (none: its text says what to do instead)."""
    message = str(exc)
    if 'Insufficient native cash' in message:
        return ['allow_margin']
    if 'Cannot sell more shares than held' in message:
        return ['quantity']
    return []


def _one(row: Row, account: str, state: dict) -> dict:
    try:
        return _apply(account, state, row.events)
    except LedgerError as exc:
        row.set('review', explain(exc, row, state), needs=refusal_needs(exc))
        return state


def _sequenced(existing, rows):
    start = len(existing)
    events = [e for r in rows for e in r.events]
    return existing + [{**e, '_sequence': start + i + 1} for i, e in enumerate(events)]


def _full(candidates: List[Row], existing: List[dict], account: str, as_of: str) -> Optional[dict]:
    """Interleaved with confirmed events: replay everything, bisect to find refused rows."""
    for row in candidates:
        if row.category == 'split' and not row.events:
            row.set('review', '账本里已有日期在这一行之后的记录，导入无法确定送转股前的持股数量，也就算不出送转比例。'
                              f'{NOT_HERE}：请把它设为跳过，再在账本里按比例记录这次送转股。')
    rows = [r for r in candidates if r.status in BOOKABLE]
    for attempt in range(MAX_BISECT + 1):
        try:
            replay(_sequenced(existing, rows))
            break
        except LedgerError:
            pass
        if attempt == MAX_BISECT:
            for row in rows:
                row.set('review', f'与账本里较晚的记录交错，无法逐行校验。{NOT_HERE}：请缩小导入的日期范围后重新导入。')
            rows = []
            break
        low, high = 1, len(rows)
        while low < high:
            middle = (low + high) // 2
            try:
                replay(_sequenced(existing, rows[:middle]))
                low = middle + 1
            except LedgerError:
                high = middle
        culprit = rows[low - 1]
        needs: List[str] = []
        try:
            replay(_sequenced(existing, rows[:low]))
            reason = f'与账本里较晚的记录冲突。{NOT_HERE}：请核对账本里日期更晚的记录'
        except LedgerError as exc:
            reason, needs = explain(exc, culprit, None), refusal_needs(exc)
        culprit.set('review', reason + '（账本里有日期更晚的记录，请核对）', needs=needs)
        rows.remove(culprit)
    try:
        final = replay_as_of(_sequenced(existing, rows), as_of)  # the file's balances are as of its last date
    except LedgerError:
        return None
    return final['accounts'].get(account)


# -- balances ------------------------------------------------------------------

def balances(rows: List[Row], state: Optional[dict], existing: List[dict], account: str) -> dict:
    ordered = sorted((r for r in rows if r.date and not r.other_account), key=lambda r: r.order)
    cash_rows = [r for r in ordered if r.cash_balance is not None]
    # A row without a ticker is listed only when it still needs review and moved shares (e.g. 新股入账).
    share_rows = [r for r in ordered if r.share_balance is not None and r.code and r.category != 'repo'
                  and (r.ticker or r.status == 'review' and r.share_balance != 0)]
    if not cash_rows and not share_rows:
        return {'available': False, 'reason': '文件没有资金余额或股份余额列，无法对账。'}
    if state is None:
        return {'available': False, 'reason': '账户还没有期初余额，或账本无法计算导入后的余额，暂时无法对账。'}
    last_date = ordered[-1].date if ordered else None
    cash = {}
    for row in cash_rows:
        cash[row.cash_currency] = row   # chronological, so the last balance per currency wins
    # Rows of this account that move cash or shares but are not booked: while any is left out, an equal balance is
    # not a reconciliation (它们没有入账), so the line says 未核对 and how many rows are left out.
    left_out = [r for r in rows if not r.other_account and (r.status in ('review', 'unsupported')
                                                            or r.status == 'new' and not r.include)]
    cash_items = []
    for unit, row in sorted(cash.items()):
        ledger_value = Decimal(state['cash_balances'].get(unit, '0'))
        difference = ledger_value - row.cash_balance
        pending = [r for r in left_out if r.cash_change and r.cash_currency == unit]
        status = '差额' if abs(difference) >= TOLERANCE else '未核对' if pending else '一致'
        cash_items.append({'currency': unit, 'file': text(row.cash_balance), 'ledger': text(ledger_value),
                           'difference': text(difference), 'row': row.row, 'status': status,
                           'label': {'一致': '一致', '差额': f'差 {unit} {_cents(text(difference))}',
                                     '未核对': f'未核对：还有 {len(pending)} 行没有入账'}[status]})
    latest: Dict[str, Row] = {}
    for row in share_rows:
        latest[row.ticker or row.code] = row
    positions = []
    for key, row in sorted(latest.items()):
        if not row.ticker:
            positions.append({'ticker': None, 'code': row.code, 'name': row.name, 'file': text(row.share_balance),
                              'ledger': None, 'difference': None, 'row': row.row, 'status': '未核对',
                              'label': '代码未确定，未核对'})
            continue
        ledger_value = _listing_quantity(state, row.ticker)
        difference = ledger_value - row.share_balance
        # A cash-only row (a dividend and its 成交数量 of eligible shares) moves no shares: its cash is the cash line's.
        pending = [r for r in left_out if r.quantity and r.category not in _CASH_SIGN
                   and (r.ticker == row.ticker or not r.ticker and r.code == row.code)]
        status = '差额' if difference != 0 else '未核对' if pending else '一致'
        positions.append({'ticker': row.ticker, 'code': row.code, 'name': row.name, 'file': text(row.share_balance),
                          'ledger': text(ledger_value), 'difference': text(difference), 'row': row.row,
                          'status': status, 'label': {'一致': '一致', '差额': f'差 {text(difference)} 股',
                                                      '未核对': f'未核对：还有 {len(pending)} 行没有入账'}[status]})
    causes = _causes(rows, cash_items, positions, existing, last_date, account)
    agreed = sum(1 for p in positions if p['status'] == '一致')
    summary = ' · '.join(
        [f"导入后 {c['currency']} 现金 {_cents(c['ledger'])}，券商资金余额 {_cents(c['file'])}，{c['label']}"
         for c in cash_items] + ([f'股份余额 {agreed}/{len(positions)} 一致'] if positions else []))
    return {'available': True, 'as_of': last_date, 'cash': cash_items, 'positions': positions, 'causes': causes,
            'summary': summary,
            'consistent': all(c['status'] == '一致' for c in cash_items) and all(p['status'] == '一致' for p in positions)}


def _cents(value: str) -> str:
    return money(Decimal(value).quantize(Decimal('0.01')))


def _causes(rows, cash_items, positions, existing, last_date, account) -> List[str]:
    causes = []
    for item in positions:
        if item['status'] in ('差额', '未核对'):
            pending = [r.row for r in rows if r.ticker == item['ticker'] and not (r.status in BOOKABLE and r.include)
                       and r.status not in ('imported', 'skipped')]
            if pending:
                causes.append(f"{item['ticker']} 有 {len(pending)} 行没有入账（第 {'、'.join(map(str, pending))} 行）")
    if all(c['status'] == '一致' for c in cash_items):
        return causes
    excluded = defaultdict(lambda: [0, Decimal(0)])
    for row in rows:
        bookable_and_in = row.status in BOOKABLE and row.include or row.status in ('imported', 'merged')
        if not bookable_and_in and row.cash_change and not row.other_account:
            excluded[row.status][0] += 1
            excluded[row.status][1] += row.cash_change
    total = sum((value for _count, value in excluded.values()), Decimal(0))
    from core.importers.transactions import STATUS_LABELS
    for status, (count, value) in sorted(excluded.items()):
        causes.append(f'{STATUS_LABELS[status]} {count} 行没有入账，资金变动合计 {money(value)}')
    for item in cash_items:
        if item['status'] == '差额' and total and abs(Decimal(item['difference']) + total) < TOLERANCE:
            causes.append('差额正好等于这些未入账行的资金变动合计')
    later = [e for e in existing if e.get('account') == account and last_date and e['date'] > last_date]
    if later:
        causes.append(f'账本里还有 {len(later)} 笔日期晚于 {last_date} 的记录，文件余额不包含它们')
    causes.append('也可能是期初现金、更早的记录或手工录入的金额与券商不一致')
    return causes
