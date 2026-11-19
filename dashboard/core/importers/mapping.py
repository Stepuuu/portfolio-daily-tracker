"""Which column means what: synonyms, vendor presets and saved mappings.

A mapping is ``{field: column index}`` plus ``"fees": [column indices]``; every
fee column is summed (see ``transactions.fee_rule`` for the overlap rules).
Headers are compared after ``normalize_header``: no spaces, no unit suffix such as
``(元)``, 帐 → 账. The unit itself is not dropped: ``header_unit`` reads it (手, 股,
万元, 美元 ...), ``apply_unit`` converts the numbers, and units are part of the
saved-mapping ``signature``.

Presets are header signatures over the same synonyms. They only name the format
(``识别为: 同花顺交割单``) and are marked unverified: they were written from
documented column names, not from real exports, so the synonym mapper is what
actually reads the file. A mapping the person corrects is saved per (account,
header signature) and applied automatically the next time.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
import unicodedata
from typing import Callable, Dict, List, Optional, Sequence

from core import desk_store
from core.importers import ImportFailure
from core.importers.values import cell_comma, clean, decimal, parse_date

# field: (label, synonyms in priority order). The first synonym found wins.
TRANSACTION_FIELDS: Dict[str, tuple] = {
    'date': ('日期', ['成交日期', '发生日期', '业务日期', '交易日期', '日期', '清算日期', '交收日期', '交割日期', '记账日期']),
    'time': ('时间', ['成交时间', '发生时间', '时间', '委托时间']),
    'code': ('代码', ['证券代码', '股票代码', '代码', '证券编码', '基金代码', '产品代码']),
    'name': ('名称', ['证券名称', '股票名称', '证券简称', '名称', '股票简称', '基金名称', '产品名称']),
    'action': ('业务', ['业务名称', '操作', '摘要', '交易类别', '委托类别', '买卖标志', '业务类型', '买卖方向',
                       '委托方向', '业务标志', '交易类型', '买卖类别', '业务摘要']),
    'quantity': ('成交数量', ['成交数量', '成交股数', '发生数量', '数量', '成交量']),
    'price': ('成交价格', ['成交均价', '成交价格', '成交价', '价格', '成交单价']),
    'turnover': ('成交金额', ['成交金额', '成交额']),
    'cash_change': ('发生金额', ['发生金额', '清算金额', '收付金额', '资金发生数', '变动金额', '发生额', '交收金额']),
    'income': ('收入金额', ['收入金额', '存入金额', '收入']),
    'expense': ('支出金额', ['支出金额', '付出金额', '取出金额', '支出']),
    # A bare 余额 is not mapped: some exports use it for shares, others for cash.
    'cash_balance': ('资金余额', ['资金余额', '剩余金额', '本次资金余额', '资金本次余额']),
    'share_balance': ('股份余额', ['股份余额', '剩余数量', '本次股票余额', '股票余额']),
    # Identity columns. A plain row counter (序号) is deliberately not one: it changes between exports.
    'trade_id': ('成交编号', ['成交编号', '成交号码']),
    'serial': ('流水号', ['流水号', '资金流水号']),
    'contract': ('合同编号', ['合同编号', '委托编号', '委托序号', '合同序号', '申请编号']),
    'market': ('交易市场', ['交易市场', '市场', '交易所', '市场名称', '交易板块']),
    'shareholder': ('股东代码', ['股东代码', '股东账户', '股东账号', '证券账户', '股东代号']),
    'fund_account': ('资金账号', ['资金账号', '资金账户', '资产账号', '资产账户', '客户号', '客户代码', '账户', '账号']),
    'currency': ('币种', ['币种', '结算币种', '币别', '货币', '货币类型', '币种名称']),
    'fx_rate': ('汇率', ['结算汇率', '参考汇率', '汇率', '成交汇率']),
}

HOLDING_FIELDS: Dict[str, tuple] = {
    'code': TRANSACTION_FIELDS['code'],
    'name': TRANSACTION_FIELDS['name'],
    'hold_quantity': ('持仓数量', ['股票余额', '持仓数量', '实际数量', '证券数量', '持股数量', '当前持仓', '股份余额',
                                  '参考持股', '当前数量', '持有数量']),
    'available': ('可用数量', ['可用余额', '可用数量', '可卖数量', '可用股份']),
    'cost_price': ('成本价', ['成本价', '参考成本价', '摊薄成本价', '买入均价', '成本价格', '持仓均价', '平均成本']),
    'market_price': ('市价', ['市价', '最新价', '当前价', '现价', '收盘价']),
    'market_value': ('市值', ['市值', '最新市值', '参考市值', '证券市值']),
    'pnl': ('浮动盈亏', ['浮动盈亏', '参考盈亏', '持仓盈亏', '盈亏']),
    'market': TRANSACTION_FIELDS['market'],
    'shareholder': TRANSACTION_FIELDS['shareholder'],
    'fund_account': TRANSACTION_FIELDS['fund_account'],
    'currency': TRANSACTION_FIELDS['currency'],
}

# Summed into the fee. Overlapping groups (佣金 = 净佣金 + 规费, 规费 = 经手费 + 证管费)
# are resolved in transactions.trade_fee.
FEE_HEADERS = ['手续费', '佣金', '净佣金', '印花税', '过户费', '其他杂费', '交易规费', '规费', '经手费', '证管费',
               '结算费', '其他费', '其他费用', '交易费', '交易征费', '交易系统使用费', '股份交收费', '交收费',
               '附加费', '杂费', '前台费用', '财汇局交易征费']

TEMPLATE_COLUMNS = ['external_id', 'date', 'kind', 'account', 'ticker', 'currency', 'quantity', 'price', 'fee',
                    'amount', 'fx_rate', 'to_account', 'to_currency', 'received_amount', 'ratio', 'note']

TEMPLATE_LABELS = {'external_id': '编号', 'date': '日期', 'kind': '类型', 'account': '账户', 'ticker': '代码',
                   'currency': '币种', 'quantity': '数量', 'price': '价格', 'fee': '费用', 'amount': '金额',
                   'fx_rate': '汇率', 'to_account': '转入账户', 'to_currency': '目标币种', 'received_amount': '到账金额',
                   'ratio': '比例', 'note': '备注'}
STATEMENTS = ('transactions', 'holdings', 'template')

PRESETS = [
    # id, label, statement, headers that must all be present (normalized)
    {'id': 'ths_trades', 'label': '同花顺交割单', 'statement': 'transactions',
     'headers': ['成交日期', '证券代码', '证券名称', '操作', '成交数量', '成交均价', '成交金额']},
    {'id': 'eastmoney_trades', 'label': '东方财富交割单', 'statement': 'transactions',
     'headers': ['业务名称', '证券代码', '证券名称', '成交数量', '成交价格', '成交金额', '发生金额']},
    {'id': 'tdx_trades', 'label': '通达信交割单', 'statement': 'transactions',
     'headers': ['成交日期', '业务名称', '证券代码', '成交价格', '成交数量', '清算金额', '剩余金额']},
    {'id': 'statement_cash', 'label': '资金对账单', 'statement': 'transactions',
     'headers': ['发生日期', '业务名称', '发生金额', '资金余额']},
    {'id': 'ths_holdings', 'label': '同花顺持仓', 'statement': 'holdings',
     'headers': ['证券代码', '证券名称', '股票余额', '可用余额', '成本价', '市值']},
    {'id': 'tdx_holdings', 'label': '通达信持仓', 'statement': 'holdings',
     'headers': ['证券代码', '证券名称', '证券数量', '成本价', '当前价', '最新市值']},
    {'id': 'template', 'label': '本系统模板', 'statement': 'template',
     'headers': ['external_id', 'date', 'kind', 'account']},
]
GENERIC = {'id': 'generic', 'label': '通用表格', 'statement': None, 'headers': []}
UNVERIFIED = '预设未经你的券商文件验证'

_SUFFIX = re.compile(r'[(（\[【][^)）\]】]*[)）\]】]$')


def normalize_header(cell) -> str:
    text = unicodedata.normalize('NFKC', clean(cell))
    text = re.sub(r'\s+', '', text).strip(':：')
    while _SUFFIX.search(text) and _SUFFIX.sub('', text):
        text = _SUFFIX.sub('', text)
    return text.replace('帐', '账').lower()


# -- units in headers ------------------------------------------------------------
# ``成交金额(万元)``, ``成交数量(手)``, ``发生金额(美元)``: the bracketed suffix that
# normalize_header drops is read here, so it is never lost.

CURRENCY_WORDS = {'人民币': 'CNY', '人民币元': 'CNY', 'rmb': 'CNY', 'cny': 'CNY', '¥': 'CNY',
                  '港元': 'HKD', '港币': 'HKD', 'hkd': 'HKD', 'hk$': 'HKD',
                  '美元': 'USD', '美金': 'USD', 'usd': 'USD', 'us$': 'USD', '$': '$'}
OTHER_CURRENCIES = {'欧元': 'EUR', 'eur': 'EUR', '€': 'EUR', '日元': 'JPY', 'jpy': 'JPY', '英镑': 'GBP', 'gbp': 'GBP',
                    '£': 'GBP', '新加坡元': 'SGD', '新元': 'SGD', 'sgd': 'SGD', '澳元': 'AUD', 'aud': 'AUD', '加元': 'CAD',
                    'cad': 'CAD', '新台币': 'TWD', '台币': 'TWD', 'twd': 'TWD', '澳门元': 'MOP', 'mop': 'MOP',
                    '瑞士法郎': 'CHF', 'chf': 'CHF', '韩元': 'KRW', 'krw': 'KRW'}
MAGNITUDES = {'': 1, '十': 10, '百': 100, '千': 1000, '万': 10000, '十万': 100000, '百万': 1000000, '千万': 10000000,
              '亿': 100000000}
QUANTITY_WORDS = ('股', '张', '份', '手')
SUPPORTED_CURRENCIES = ('CNY', 'HKD', 'USD')


@dataclass(frozen=True)
class Unit:
    """What a header suffix says about its column's numbers."""
    text: str                          # the suffix as written, without brackets
    kind: str                          # money | quantity | percent | scale (a bare 万: either)
    scale: int = 1                     # multiply the cell by this
    currency: Optional[str] = None     # CNY / HKD / USD, '$' (ambiguous) or another code or name (unsupported)
    lot: bool = False                  # 手: multiply by the instrument's lot size


def _parse_unit(text: str) -> Optional[Unit]:
    raw = text
    t = unicodedata.normalize('NFKC', text).replace(' ', '').lower()
    if t in ('%', '百分比', '百分点'):
        return Unit(raw, 'percent')
    per_share = re.sub(r'/(股|张|份)$', '', t)
    magnitude = next((m for m in sorted(MAGNITUDES, key=len, reverse=True) if m and per_share.startswith(m)), '')
    rest = per_share[len(magnitude):]
    scale = MAGNITUDES[magnitude]
    if rest in QUANTITY_WORDS and per_share == t:
        return Unit(raw, 'quantity', scale, lot=rest == '手')
    if rest == '' and magnitude:
        return Unit(raw, 'scale', scale)
    if rest == '元':
        return Unit(raw, 'money', scale)
    if rest in CURRENCY_WORDS:
        return Unit(raw, 'money', scale, CURRENCY_WORDS[rest])
    if rest in OTHER_CURRENCIES:
        return Unit(raw, 'money', scale, OTHER_CURRENCIES[rest])
    # Any other currency is still a currency: an ISO code (NZD) or a name ending in 元 / 币 (新西兰元) is kept as
    # such, so the amount is never read as CNY; it is not supported, so the row needs review.
    if re.fullmatch(r'[a-z]{3}', rest):
        return Unit(raw, 'money', scale, rest.upper())
    if len(rest) >= 2 and rest[-1] in '元币' and re.fullmatch(r'[\u4e00-\u9fff]+', rest):
        return Unit(raw, 'money', scale, rest)
    return None    # a description such as (含费) or (YYYYMMDD), not a unit


def header_unit(cell) -> Optional[Unit]:
    """The unit a header names in its last bracketed suffix that is a unit, or None."""
    text = unicodedata.normalize('NFKC', clean(cell))
    text = re.sub(r'\s+', '', text).strip(':：')
    while _SUFFIX.search(text) and _SUFFIX.sub('', text):
        suffix = _SUFFIX.search(text).group(0)[1:-1]
        unit = _parse_unit(suffix)
        if unit is not None:
            return unit
        text = _SUFFIX.sub('', text)
    return None


def apply_unit(value, unit: Optional[Unit], kind: str, header: str, resolution):
    """``value`` (a Decimal or None) in the column's base unit; raises ValueError when it cannot be.

    ``kind``: money | quantity | rate. A magnitude (万元, 万股) is applied only when
    the result stays exact to ``resolution`` (0.01 元, 1 股): ``16.8`` 万元 could be a
    rounded 168,043.68, so it is refused. A lot (手) is not applied here: it depends
    on the instrument (see ``codes.lot_size``)."""
    from core.importers.values import MAX_ABS
    if value is None or unit is None:
        return value
    if unit.kind == 'percent':
        raise ValueError(f'表头“{header}”的单位是百分比，不是{"数量" if kind == "quantity" else "金额"}')
    if kind == 'quantity' and unit.kind == 'money':
        raise ValueError(f'表头“{header}”的单位是金额，不是数量')
    if kind != 'quantity' and unit.kind == 'quantity':
        raise ValueError(f'表头“{header}”的单位是“{unit.text}”，不是金额')
    if kind == 'rate' and unit.scale != 1:
        raise ValueError(f'表头“{header}”的单位“{unit.text}”无法用于汇率')
    if unit.scale == 1:
        return value
    exponent = value.as_tuple().exponent
    step = (type(value)(1).scaleb(exponent) if isinstance(exponent, int) else value) * unit.scale
    if step > resolution:
        raise ValueError(f'表头“{header}”的单位是“{unit.text}”，换算后只精确到 {format(step, ",f")} '
                         f'{"股" if kind == "quantity" else "元"}，可能是四舍五入后的数字')
    scaled = value * unit.scale
    if abs(scaled) > MAX_ABS:
        raise ValueError(f'表头“{header}”的单位是“{unit.text}”，换算后超出可处理的范围')
    return scaled


def column_units(headers: Sequence[str], mapping: dict) -> Dict[int, Unit]:
    """Column index → unit, for the columns of ``mapping`` whose header names one."""
    indices = [index for field, index in mapping.items() if field != 'fees' and isinstance(index, int)]
    indices += list(mapping.get('fees') or [])
    out = {}
    for index in indices:
        if 0 <= index < len(headers):
            unit = header_unit(headers[index])
            if unit is not None:
                out[index] = unit
    return out


def _norm_all(words: Sequence[str]) -> List[str]:
    return [normalize_header(word) for word in words]


_KNOWN = set(_norm_all(FEE_HEADERS) + TEMPLATE_COLUMNS)
for _label, _words in list(TRANSACTION_FIELDS.values()) + list(HOLDING_FIELDS.values()):
    _KNOWN.update(_norm_all(_words))


def header_matches(cells: Sequence[str]) -> int:
    """How many distinct cells of a row are known column names (header detection)."""
    return len({normalize_header(cell) for cell in cells} & _KNOWN)


def signature(headers: Sequence[str]) -> str:
    """Stable id of a header layout, used to remember a corrected mapping.

    Units are part of it: ``成交数量(手)`` and ``成交数量(股)`` are different layouts.
    A header without a unit gives the same signature as before units were read."""
    parts = []
    for header in headers:
        unit = header_unit(header)
        name = normalize_header(header)
        parts.append(name + (f'({unicodedata.normalize("NFKC", unit.text).lower()})' if unit else ''))
    return hashlib.sha1('\x1f'.join(parts).encode()).hexdigest()


def fields_for(statement: str) -> Dict[str, tuple]:
    if statement == 'holdings':
        return HOLDING_FIELDS
    if statement == 'template':
        return {name: (TEMPLATE_LABELS[name], [name]) for name in TEMPLATE_COLUMNS}
    return TRANSACTION_FIELDS


def detect_statement(headers: Sequence[str]) -> str:
    names = set(_norm_all(headers))
    if {'external_id', 'date', 'kind', 'account'} <= names:
        return 'template'
    holding = set(_norm_all(HOLDING_FIELDS['hold_quantity'][1])) & names
    has_cost = set(_norm_all(HOLDING_FIELDS['cost_price'][1] + HOLDING_FIELDS['market_value'][1])) & names
    has_action = set(_norm_all(TRANSACTION_FIELDS['action'][1])) & names
    has_fill = set(_norm_all(TRANSACTION_FIELDS['quantity'][1] + TRANSACTION_FIELDS['turnover'][1])) & names
    if holding and has_cost and not has_action and not (has_fill - {'数量'}):
        return 'holdings'
    return 'transactions'


def detect(headers: Sequence[str], statement: str) -> dict:
    """Map columns by synonym priority; each column is used at most once."""
    names = [normalize_header(h) for h in headers]
    taken, mapping = set(), {}
    for field, (_label, words) in fields_for(statement).items():
        for word in _norm_all(words):
            matches = [i for i, name in enumerate(names) if name == word and i not in taken]
            if matches:
                mapping[field] = matches[0]
                taken.add(matches[0])
                break
    if statement == 'transactions':
        fees = set(_norm_all(FEE_HEADERS))
        mapping['fees'] = [i for i, name in enumerate(names) if name in fees and i not in taken]
    return mapping


def identify(headers: Sequence[str], statement: str) -> dict:
    names = set(_norm_all(headers))
    best = None
    for preset in PRESETS:
        if preset['statement'] != statement:
            continue
        if set(_norm_all(preset['headers'])) <= names and (best is None or len(preset['headers']) > len(best['headers'])):
            best = preset
    chosen = best or GENERIC
    return {'id': chosen['id'], 'label': chosen['label'], 'verified': False,
            'note': UNVERIFIED if best and chosen['id'] != 'template' else None}


def validate(mapping, column_count: int, statement: str) -> dict:
    """A mapping from the client: known fields, indices in range, no column twice."""
    if not isinstance(mapping, dict):
        raise ImportFailure('列对应必须是一个对象')
    known = set(fields_for(statement))
    result, used = {}, set()
    for field, value in mapping.items():
        if field == 'fees' and statement == 'transactions':
            if value is None:
                value = []
            if not isinstance(value, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in value):
                raise ImportFailure('费用列必须是列序号的数组')
            indices = list(dict.fromkeys(value))
            for index in indices:
                if not 0 <= index < column_count:
                    raise ImportFailure(f'费用列序号 {index} 超出范围')
                if index in used:
                    raise ImportFailure(f'第 {index + 1} 列被对应了两次')
                used.add(index)
            result['fees'] = indices
            continue
        if field not in known:
            raise ImportFailure(f'未知的字段：{field}')
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < column_count:
            raise ImportFailure(f'字段 {field} 的列序号无效')
        if value in used:
            raise ImportFailure(f'第 {value + 1} 列被对应了两次')
        used.add(value)
        result[field] = value
    if statement == 'transactions':
        result.setdefault('fees', [])
    return result


# Fields whose value goes into an event: two candidate columns with different values must be chosen.
# ``action`` decides the direction of a row; its columns are compared by the business type they state
# (``column_conflicts(canonical=...)``), so 证券买入 and 买入 agree and an empty cell states nothing.
# ``market`` decides the exchange of a code, compared by the exchange it names (``codes.market_hint``).
GUARDED = {'transactions': ('date', 'code', 'action', 'quantity', 'price', 'turnover', 'cash_change', 'income',
                            'expense', 'currency', 'fx_rate', 'market', 'fund_account', 'shareholder'),
           'holdings': ('code', 'hold_quantity', 'cost_price', 'currency', 'market', 'fund_account', 'shareholder')}
_NUMERIC = {'quantity', 'price', 'turnover', 'cash_change', 'income', 'expense', 'fx_rate', 'hold_quantity',
            'cost_price', 'amount', 'fee', 'received_amount', 'ratio'}


def _canonical(field: str, cell, comma: str = 'thousands'):
    text = clean(cell)
    try:
        if field == 'date':
            return parse_date(text)
        if field in _NUMERIC:
            return decimal(text, comma)
    except ValueError:
        return text
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).upper()


def _in_base_unit(field: str, cell, unit: Optional[Unit], lot: Optional[int], comma: str = 'thousands'):
    """A numeric cell as (currency named by its header or None, value in the column's base unit) for comparing
    two columns; a 手 column whose lot is not known gives ('手', value), comparable only with another such."""
    value = _canonical(field, cell, comma)
    if not isinstance(value, Decimal) or unit is None:
        return None, value
    if unit.lot:
        return (None, value * lot) if lot else ('手', value)
    if unit.kind in ('money', 'quantity', 'scale') and unit.scale != 1:
        value = value * unit.scale
    return unit.currency, value


def column_conflicts(headers: Sequence[str], data, mapping: dict, statement: str, chosen=(),
                     canonical: Optional[Dict[str, Callable]] = None,
                     lot: Optional[Callable[[Sequence[str]], Optional[int]]] = None,
                     comma: str = 'thousands', native: Callable[[int], frozenset] = lambda _number: frozenset()
                     ) -> List[dict]:
    """Columns that could feed the same field but disagree, so the mapping must be chosen. ``native(number)``: the
    columns of a row that held native workbook numbers (read exactly, whatever ``comma`` says).

    ``data``: (row number, cells) pairs. For each guarded field, every other unmapped
    column whose header is one of the field's synonyms is compared with the mapped
    one; any row with a different value makes a conflict. Numbers are compared in the
    columns' base units (``成交数量(手)`` × the row's lot from ``lot(cells)``, ``万元`` × 10000),
    and two currencies named by the headers differ. ``canonical`` gives a field its own
    reading (``{'action': transactions.direction}``); an other column whose reading is
    None states nothing and agrees, but one that states something where the mapped
    column states nothing (blank, or a reading of None) is a conflict: a blank action
    must not hide an explicit one. A fee column whose name appears twice among the fee
    columns is a conflict whatever its values (it would be counted twice).
    ``resolved`` is true once the person has chosen the field (``chosen``)."""
    names = [normalize_header(h) for h in headers]
    units = {index: header_unit(header) for index, header in enumerate(headers)}
    data = list(data)
    used = {index for field, index in mapping.items() if field != 'fees' and isinstance(index, int)}
    used.update(mapping.get('fees') or [])
    guarded = GUARDED.get(statement)
    out = []
    for field, (label, words) in fields_for(statement).items():
        index = mapping.get(field)
        if index is None or guarded is not None and field not in guarded:
            continue
        synonyms = set(_norm_all(words))
        columns, rows = [], set()
        reading = (canonical or {}).get(field)
        for other, name in enumerate(names):
            if other == index or other in used or name not in synonyms:
                continue
            if reading is not None:
                pairs = [(number, reading(clean(cells[index]) if index < len(cells) else ''),
                          reading(clean(cells[other]) if other < len(cells) else '')) for number, cells in data]
                differ = [number for number, a, b in pairs if b is not None and a != b]
            elif field in _NUMERIC:
                differ = []
                for number, cells in data:
                    size = lot(cells) if lot is not None and (units[index] or units[other]) else None
                    cell_rule = lambda column: cell_comma(comma, native(number), column)
                    a = _in_base_unit(field, cells[index] if index < len(cells) else '', units[index], size,
                                      cell_rule(index))
                    b = _in_base_unit(field, cells[other] if other < len(cells) else '', units[other], size,
                                      cell_rule(other))
                    if a[1] != b[1] or (a[0] != b[0] and a[0] is not None and b[0] is not None) or \
                            (a[0] == '手') != (b[0] == '手') and a[1] is not None and b[1] is not None:
                        differ.append(number)
            else:
                differ = [number for number, cells in data
                          if _canonical(field, cells[index] if index < len(cells) else '',
                                        cell_comma(comma, native(number), index))
                          != _canonical(field, cells[other] if other < len(cells) else '',
                                        cell_comma(comma, native(number), other))]
            if differ:
                columns.append(other)
                rows.update(differ)
        if columns:
            out.append({'field': field, 'label': label, 'columns': sorted([index] + columns), 'chosen': index,
                        'rows': sorted(rows)[:20], 'resolved': field in chosen})
    fees = mapping.get('fees') or []
    by_name: Dict[str, List[int]] = {}
    for index in fees:
        if index < len(names):
            by_name.setdefault(names[index], []).append(index)
    repeated = sorted(i for group in by_name.values() if len(group) > 1 for i in group)
    if repeated:
        out.append({'field': 'fees', 'label': '费用', 'columns': repeated, 'chosen': None, 'rows': [],
                    'resolved': 'fees' in chosen})
    return out


def conflict_message(conflict: dict, headers: Sequence[str]) -> str:
    columns = '、'.join(f'“{headers[i]}”（第 {i + 1} 列）' for i in conflict['columns'])
    if conflict['field'] == 'fees':
        return (f'费用列 {columns} 名称相同，合计时会重复计算。请在列对应里选择要计入的费用列。')
    rows = conflict['rows']
    return (f'{columns} 都可以作为“{conflict["label"]}”，而且数值不同（例如第 {rows[0]} 行）。'
            f'现在用的是第 {conflict["chosen"] + 1} 列；请在列对应里确认或改选“{conflict["label"]}”的列。')


def describe(headers: Sequence[str], mapping: dict, statement: str) -> List[dict]:
    """Per column: header text and the field it feeds (for the 核对列 step)."""
    labels = {index: field for field, index in mapping.items() if field != 'fees'}
    for index in mapping.get('fees', []):
        labels[index] = 'fees'
    catalog = fields_for(statement)
    out = []
    for index, header in enumerate(headers):
        field = labels.get(index)
        label = '费用' if field == 'fees' else (catalog[field][0] if field in catalog else None)
        out.append({'index': index, 'header': header, 'field': field, 'field_label': label})
    return out


def catalog(statement: str) -> List[dict]:
    required = {'transactions': {'date', 'code', 'action'}, 'holdings': {'code', 'hold_quantity', 'cost_price'},
                'template': {'external_id', 'date', 'kind', 'account'}}[statement]
    items = [{'field': field, 'label': label, 'required': field in required}
             for field, (label, _words) in fields_for(statement).items()]
    if statement == 'transactions':
        items.append({'field': 'fees', 'label': '费用（可多列，合计）', 'required': False})
    return items


# -- saved mappings ----------------------------------------------------------

MIGRATIONS = [[
    """CREATE TABLE import_mappings (
        account TEXT NOT NULL,
        signature TEXT NOT NULL,
        statement TEXT NOT NULL,
        mapping TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (account, signature))""",
], [
    # Choices that belong with the mapping: {"chosen": [fields the person picked], "account_filter": ...}.
    "ALTER TABLE import_mappings ADD COLUMN options TEXT",
]]
SCHEMA = 'import_mappings'
desk_store.register_schema(SCHEMA, MIGRATIONS)


def saved(account: str, header_signature: str) -> Optional[dict]:
    if not desk_store.ready(SCHEMA, MIGRATIONS):
        return None
    with desk_store.connect() as db:
        row = db.execute('SELECT statement, mapping, options FROM import_mappings WHERE account=? AND signature=?',
                         (account, header_signature)).fetchone()
    if row is None:
        return None
    try:
        options = json.loads(row['options']) if row['options'] else {}
    except ValueError:
        options = {}
    return {'statement': row['statement'], 'mapping': json.loads(row['mapping']),
            'options': options if isinstance(options, dict) else {}}


def save(account: str, header_signature: str, statement: str, mapping: dict, options: Optional[dict] = None) -> None:
    desk_store.ensure_schema(SCHEMA, MIGRATIONS)
    with desk_store.transaction() as db:
        db.execute('INSERT INTO import_mappings(account, signature, statement, mapping, updated_at, options) '
                   'VALUES (?,?,?,?,?,?) ON CONFLICT(account, signature) DO UPDATE SET statement=excluded.statement, '
                   'mapping=excluded.mapping, updated_at=excluded.updated_at, options=excluded.options',
                   (account, header_signature, statement, desk_store.encode(mapping), desk_store.now(),
                    desk_store.encode(options or {})))
