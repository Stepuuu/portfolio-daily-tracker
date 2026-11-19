"""A holdings statement (持仓): an opening proposal for a new account, or a
reconciliation table for an existing one.

* New account → one ``opening`` event with the file's positions. The opening date,
  the cash per currency and 累计净投入 are not in a holdings export, so the person
  supplies them (``opening`` in the session); the importer only suggests
  累计净投入 = cash + Σ quantity × cost, labelled as an estimate. The event note
  warns that a broker's 参考 / 摊薄成本 includes realized gains and may differ
  from this ledger's weighted average cost.
* Existing account → a table of file quantity vs ledger quantity. Each difference
  carries a prefilled entry (kind and quantity, price left empty) for the 记一笔
  dialog. Corrective trades are never proposed. The code is resolved before the
  numbers are checked: a row with an unusable cost keeps its quantity in the
  table, a row with an unusable quantity makes its position 未核对, and so does a
  position the file may hold under an unresolved code (also ``SHA:600036`` or
  ``600036.SH``). None of them is ever reported as missing.

Quantity is the total held (股票余额 / 持仓数量), not 可用余额. A negative 摊薄成本
cannot be booked and needs review; so does the same ticker twice.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import re
from typing import Dict, List, Optional

from core.importers import accounts, codes
from core.importers.mapping import SUPPORTED_CURRENCIES, apply_unit, column_units
from core.importers.transactions import (BOOKABLE, NOT_HERE, UNCOMPUTABLE, Context, Row, _currency, _is_footer,
                                         checked_edits, refuse_stored_edits)
from core.importers.values import LIMITS, cell_comma, cell_currency, clean, decimal, money, text

COST_NOTE = '券商“参考/摊薄成本”含已实现盈亏，可能与本系统加权平均成本不同'
POSITION_FIELDS = ('hold_quantity', 'available', 'cost_price', 'market_price', 'market_value', 'pnl')


MONEY_FIELDS = ('cost_price', 'market_price', 'market_value', 'pnl')


def _read(row: Row, header, mapping, comma: str = 'thousands', native=frozenset()) -> dict:
    cells = row.cells
    get = lambda name: clean(cells[mapping[name]]) if mapping.get(name) is not None and mapping[name] < len(cells) else ''
    values = {name: get(name) for name in ('code', 'name', 'market', 'shareholder', 'fund_account', 'currency')}
    row.account_ids = accounts.identity(values['fund_account'], values['shareholder'])
    units = column_units(header, mapping)
    values['lots'] = set()
    for name in POSITION_FIELDS:
        try:
            index = mapping.get(name)
            number = decimal(get(name), cell_comma(comma, native, index))
            unit = units.get(index) if index is not None else None
            kind = 'quantity' if name in ('hold_quantity', 'available') else 'money'
            if unit is not None and unit.lot:
                if kind != 'quantity' or unit.scale != 1:
                    raise ValueError(f'表头“{header[index]}”的单位“{unit.text}”无法用于这一列')
                values['lots'].add(name)
            elif unit is not None:
                number = apply_unit(number, unit, kind, header[index],
                                    Decimal(1) if kind == 'quantity' else Decimal('0.001'))
            values[name] = number
        except ValueError as exc:
            values[name] = None
            row.parse_errors[name] = str(exc)
    evidence = {}
    stated = _currency(values['currency'])
    if stated:
        evidence[stated] = f'币种列“{values["currency"][:20]}”'
    for name in MONEY_FIELDS:
        index = mapping.get(name)
        if index is None:
            continue
        unit = units.get(index)
        if unit is not None and unit.currency:
            evidence.setdefault(unit.currency, f'表头“{header[index]}”')
        symbol = cell_currency(cells[index]) if index < len(cells) else None
        if symbol:
            evidence.setdefault(symbol, f'金额“{clean(cells[index])[:30]}”')
    values['evidence'] = evidence
    row.raw = {header[i] if i < len(header) else str(i): cells[i] for i in range(len(cells))}
    return values


def _stated(values: dict, market: Optional[str] = None):
    """(currency, problem) of a holdings row from its 币种 column, header units and cell symbols. A bare ``$`` is
    the dollar the row names, else the listing's own (a cost of NASDAQ:AAPL in ``$`` is USD)."""
    evidence = dict(values['evidence'])
    if '$' in evidence and len(evidence) == 2 and set(evidence) - {'$'} <= {'USD', 'HKD'}:
        evidence.pop('$')
    if '$' in evidence and len(evidence) == 1 and market in ('USD', 'HKD'):
        evidence = {market: evidence['$']}
    if '$' in evidence and len(evidence) == 1:
        return None, f'{evidence["$"]}带“$”符号，无法确定是美元还是港币。请核对币种。'
    unsupported = [code for code in evidence if code not in SUPPORTED_CURRENCIES]
    if unsupported:
        return None, '；'.join(f'{evidence[c]}的币种 {c} 不受支持（账本只支持人民币、港币和美元）' for c in unsupported) + '。'
    if len(evidence) > 1:
        return None, '币种不一致：' + '，'.join(f'{where}是 {code}' for code, where in evidence.items()) + '。请核对。'
    return next(iter(evidence), None), None


def interpret(table, mapping: dict, ctx: Context, edits: Dict[str, dict]) -> List[Row]:
    header = table.header
    rows: List[Row] = []
    file_has_a_codes = False
    parsed = []
    for index, (number, cells) in enumerate(table.data()):
        valid, problems = checked_edits(number, edits.get(str(number), {}), 'holdings')
        row = Row(row=number, cells=list(cells), index=index, order=index, category='position', edits=valid,
                  edit_problems=problems)
        rows.append(row)
        if _is_footer(cells):
            row.category = 'footer'
            row.set('skipped', '合计 / 汇总行')
            continue
        values = _read(row, header, mapping, ctx.comma, table.native(number))
        row.code, row.name = values['code'], values['name']
        parsed.append((row, values))
        bare = codes.bare(row.code)
        file_has_a_codes = file_has_a_codes or (len(bare) == 6 and bare.isdigit())
    ctx.accounts = accounts.decide(rows, ctx.account_choice, ctx.account, ctx.identities)
    for row, values in parsed:
        if row.status == 'new':
            try:
                _position(row, values, ctx, mapping, file_has_a_codes)
            except ArithmeticError:   # never a 500, never a guess
                row.quantity = None
                row.set('review', UNCOMPUTABLE, needs=['quantity', 'price'])
    tickers = Counter(r.ticker for r in rows if r.status == 'new' and r.ticker)
    for row in rows:
        if row.status == 'new' and tickers[row.ticker] > 1:
            row.set('review', f'{row.ticker} 在文件里出现了不止一次。请只保留一行（另一行设为跳过），或核对代码。',
                    needs=['ticker'])
    return rows


def _position(row: Row, values: dict, ctx: Context, mapping: dict, file_has_a_codes: bool):
    edit = row.edits
    if refuse_stored_edits(row):
        return
    if edit.get('kind') == 'skip':
        row.set('skipped', '你选择了跳过这一行')
        return
    hint = codes.market_hint(market=values['market'], currency=values['currency'], shareholder=values['shareholder'])
    row.resolution = codes.resolve(row.code, hint=hint, numeric_cell=mapping.get('code') in ctx.numeric_columns,
                                   file_has_a_codes=file_has_a_codes, short_code_market=ctx.short_code_market,
                                   known=ctx.known_tickers, aliases=ctx.aliases)
    # The ticker first: a row whose numbers cannot be used still says which position it is about, so the
    # comparison marks that position 未核对 instead of reporting it missing.
    if edit.get('ticker'):
        row.ticker = codes.held_spelling(edit['ticker'], ctx.known_tickers)
        row.notes.append('代码由你选择')
    elif row.resolution.status == 'ok':
        row.ticker = row.resolution.ticker
        if row.resolution.note:
            row.notes.append(row.resolution.note)
    if row.parse_errors.get('hold_quantity') and not edit.get('quantity'):   # an edit replaces the cell
        row.set('review', f'持仓数量：{row.parse_errors["hold_quantity"]}', needs=['quantity'])
        return
    quantity = decimal(edit['quantity']) if edit.get('quantity') else values['hold_quantity']
    if quantity is None:
        row.set('review', '缺少持仓数量。', needs=['quantity'])
        return
    if quantity == 0:
        row.set('skipped', '持仓数量为 0')
        return
    if quantity < 0:
        row.set('review', '持仓数量为负数，请核对。', needs=['quantity'])
        return
    row.quantity = quantity
    if row.resolution.status == 'repo' and not edit.get('ticker'):
        row.set('skipped', '逆回购持仓不是证券持仓：这部分资金请计入期初现金。')
        return
    if not row.ticker:
        if row.resolution.status == 'unsupported':
            row.set('unsupported', row.resolution.message)
            return
        if row.resolution.choices:
            row.choices['ticker'] = row.resolution.choices
        row.set('review', row.resolution.message or '请选择正式代码。', needs=['ticker'])
        return
    if 'hold_quantity' in values['lots'] and not edit.get('quantity'):
        lot = codes.lot_size(row.ticker)
        if lot is None:
            row.quantity = None   # a count of lots, not of shares: never compared
            row.set('review', f'持仓数量的单位是“手”，但无法确定 {row.ticker} 每手多少股 / 张（只有 A 股股票和 ETF 确定是 100）。'
                              '请填写以股或张为单位的数量。', needs=['quantity'])
            return
        row.notes.append(f'持仓数量 {text(quantity)} 手 × {lot} = {text(quantity * lot)} 股')
        quantity = row.quantity = quantity * lot
    if codes.whole_units(row.ticker) and quantity != quantity.to_integral_value():
        row.set('review', f'持仓数量 {text(quantity)} 不是整数：{row.ticker} 以整股（张、份）持有，这个数字可能读错了。请核对。',
                needs=['quantity'])
        return
    row.currency = codes.MARKETS[row.ticker.split(':')[0]]
    stated, problem = _stated(values, row.currency)
    if problem:
        row.set('review', problem, needs=['price'])
        return
    if stated and stated != row.currency and not (row.ticker.startswith('HKG:') and stated == 'CNY'):
        row.set('review', f'{row.ticker} 以 {row.currency} 计价，但这一行的币种是 {stated}。如果代码选错了，请重新选择代码；'
                          f'否则{NOT_HERE}，请核对文件，或把它排除。', needs=['ticker'])
        return
    if row.parse_errors.get('cost_price') and not edit.get('price'):
        row.set('review', f'成本价：{row.parse_errors["cost_price"]}', needs=['price'])
        return
    cost = decimal(edit['price']) if edit.get('price') else values['cost_price']
    if cost is None:
        row.set('review', '缺少成本价。请填写每股成本。', needs=['price'])
        return
    if cost < 0:
        row.set('review', f'成本价为负数（{money(cost)}）：券商的摊薄成本已扣除盈利，账本不接受负成本。请填写成本价。',
                needs=['price'])
        return
    if row.ticker.startswith('HKG:') and stated != 'HKD' and not edit.get('price'):
        # 港股通 statements often show a CNY cost; the ledger needs the HKD cost.
        row.set('review', '港股持仓的成本价可能是人民币，账本需要港币成本。请核对并填写港币成本价。', needs=['price'])
        return
    for value, limit, label in ((quantity, LIMITS['quantity'], '持仓数量'), (cost, LIMITS['price'], '成本价')):
        if value > limit:
            row.set('review', f'{label} {text(value)} 超出可处理的范围（最大 {text(limit)}）。请核对。',
                    needs=['quantity' if label == '持仓数量' else 'price'])
            return
    row.price = cost
    row.events = []  # positions are carried in one opening event, see opening_event
    row.notes.append(f'持仓 {text(quantity)}，成本价 {text(cost)}')


def positions(rows: List[Row]) -> List[dict]:
    out = []
    for row in rows:
        if row.status in BOOKABLE and row.include and row.ticker:
            item = {'ticker': row.ticker, 'quantity': text(row.quantity), 'cost_price': text(row.price)}
            if row.name:
                item['name'] = row.name[:240]
            out.append(item)
    return out


def summary(title_rows: List[List[str]]) -> List[dict]:
    """Label / number pairs found above the header (资金余额, 可用, 总资产 ...), for display only."""
    found = []
    for cells in title_rows:
        joined = ' '.join(clean(c) for c in cells if clean(c))
        for label, number in re.findall(r'([一-龥A-Za-z]{2,12})\s*[:：]\s*(-?[\d,]+(?:\.\d+)?)', joined):
            found.append({'label': label, 'value': number.replace(',', '')})
        for left, right in zip(cells, cells[1:]):
            left, right = clean(left).rstrip(':：'), clean(right)
            if re.fullmatch(r'[一-龥]{2,12}', left) and re.fullmatch(r'-?[\d,]+(\.\d+)?', right):
                item = {'label': left, 'value': right.replace(',', '')}
                if item not in found:
                    found.append(item)
    return [item for item in found if not any(word in item['label'] for word in ('账号', '帐号', '代码', '账户', '帐户'))][:20]


# The setup wizard's words for the same estimate (frontend features/setup/opening.ts ``ESTIMATE_LABEL``).
ESTIMATE_LABEL = '按现金＋持仓成本＋基金估值估算，只是参考：请改成实际累计转入的本金'


def suggested_contributions(rows: List[Row], opening: dict) -> dict:
    """An estimate of 累计净投入 from everything the opening books, at cost: CNY cash + Σ quantity × cost + the fund
    value. Only offered (the person enters the real net deposits), and only when every amount is in CNY."""
    opening = opening or {}
    cash = opening.get('cash_balances') or {}
    held = [r for r in rows if r.status in BOOKABLE and r.include and r.ticker]
    if not cash:
        return {'value': None, 'label': '先填写期初现金，才能估算累计净投入'}
    foreign = any(unit != 'CNY' and Decimal(value) != 0 for unit, value in cash.items())
    if foreign or any(r.currency != 'CNY' for r in held):
        return {'value': None, 'label': '有外币现金或持仓，无法按成本估算累计净投入，请直接填写'}
    total = (Decimal(cash.get('CNY', '0')) + sum((r.quantity * r.price for r in held), Decimal(0))
             + Decimal(opening.get('fund_cny') or '0'))
    return {'value': text(total), 'label': ESTIMATE_LABEL}


def opening_event(rows: List[Row], opening: dict, account: str, source: str) -> dict:
    return {'kind': 'opening', 'date': opening['date'], 'account': account, 'source': source,
            'external_id': f"opening:{opening['date']}", 'cash_balances': opening['cash_balances'],
            'contributed_cny': opening['contributed_cny'], 'fund_cny': opening.get('fund_cny') or '0',
            'positions': positions(rows), 'note': COST_NOTE}


def _symbol(code: str) -> str:
    """The security code without an exchange prefix or suffix: SHA:600036, 600036.SH, SH600036 → 600036."""
    raw = re.sub(r'^(SHA|SHE|HKG|NASDAQ|NYSE):', '', codes.bare(code))
    raw = re.sub(r'\.(SH|SS|SZ|HK|BJ|US|O|OQ|N)$', '', raw)
    return re.sub(r'^(SH|SZ|HK|BJ)(?=\d)', '', raw)


def _may_be(row: Row, ticker: str) -> bool:
    """Whether a row whose code is not resolved could be ``ticker``."""
    if ticker in (row.choices.get('ticker') or []):
        return True
    symbol, code = _symbol(row.code), ticker.split(':', 1)[1]
    if symbol.isdigit() and code.isdigit():
        return int(symbol) == int(code)
    return bool(symbol) and symbol == code


def compare(rows: List[Row], state: Optional[dict], account: str) -> List[dict]:
    """File quantity vs ledger quantity for an existing account; never proposes trades.

    A row that needs review for its cost or currency still has a known quantity
    and is compared (``file_cost`` is then None, ``cost_note`` says why). A ticker
    listed twice, and a ledger position that a row with an unresolved code may
    be, are ``未核对`` without a prefilled entry: they are never reported missing."""
    held = (state or {}).get('positions', {})
    seen = set()
    table = []
    counted = Counter(r.ticker for r in rows if r.ticker and r.status in BOOKABLE + ('review',))
    unresolved = [r for r in rows if r.status == 'review' and not r.ticker and r.code and 'account' not in r.needs]
    for row in rows:
        if not row.ticker or row.status not in BOOKABLE + ('review',):
            continue
        if row.ticker in seen:
            continue
        seen.add(row.ticker)
        ledger_position = held.get(row.ticker)
        ledger_quantity = Decimal(ledger_position['quantity']) if ledger_position else Decimal(0)
        ledger_cost = Decimal(ledger_position['cost_price']) if ledger_position else None
        if counted[row.ticker] > 1:
            table.append(_unchecked(row.ticker, row.name, ledger_quantity, ledger_cost, row.row,
                                    f'{row.ticker} 在文件里出现了不止一次，未核对'))
            continue
        if row.quantity is None:
            table.append(_unchecked(row.ticker, row.name, ledger_quantity, ledger_cost, row.row,
                                    f'文件第 {row.row} 行的持仓数量无法使用，未核对'))
            continue
        line = _line(row.ticker, row.name, row.quantity, ledger_quantity,
                     row.price if row.status in BOOKABLE else None, ledger_cost, account, row.row)
        if row.status == 'review':
            line['cost_note'] = '这一行的成本价或币种需处理，只核对了数量：' + (row.messages[0] if row.messages else '')
        table.append(line)
    for ticker, position in sorted(held.items()):
        if ticker in seen:
            continue
        maybe = next((r for r in unresolved if _may_be(r, ticker)), None)
        if maybe is not None:
            table.append(_unchecked(ticker, position.get('name'), Decimal(position['quantity']),
                                    Decimal(position['cost_price']), maybe.row,
                                    f'文件第 {maybe.row} 行的代码 {maybe.code} 还没有确定，可能就是这只证券，未核对'))
            continue
        table.append(_line(ticker, position.get('name'), Decimal(0), Decimal(position['quantity']), None,
                           Decimal(position['cost_price']), account, None))
    return table


def _unchecked(ticker, name, ledger_quantity, ledger_cost, row_number, label) -> dict:
    return {'ticker': ticker, 'name': name, 'row': row_number, 'file_quantity': None,
            'ledger_quantity': text(ledger_quantity), 'difference': None, 'status': '未核对', 'label': label,
            'file_cost': None, 'ledger_cost': text(ledger_cost), 'entry': None, 'cost_note': None}


def _line(ticker, name, file_quantity, ledger_quantity, file_cost, ledger_cost, account, row_number):
    difference = file_quantity - ledger_quantity
    entry = None
    if difference:
        entry = {'kind': 'buy' if difference > 0 else 'sell', 'account': account, 'ticker': ticker,
                 'quantity': text(abs(difference)), 'price': None, 'source': 'import'}
    return {'ticker': ticker, 'name': name, 'row': row_number, 'file_quantity': text(file_quantity),
            'ledger_quantity': text(ledger_quantity), 'difference': text(difference),
            'status': '一致' if difference == 0 else '差额',
            'label': '一致' if difference == 0 else (f'券商多 {text(difference)}' if difference > 0
                                                    else f'账本多 {text(-difference)}'),
            'file_cost': text(file_cost), 'ledger_cost': text(ledger_cost), 'entry': entry,
            'cost_note': COST_NOTE if file_cost is not None and ledger_cost is not None and file_cost != ledger_cost else None}
