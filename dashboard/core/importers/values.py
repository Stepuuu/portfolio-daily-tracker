"""Cell values from broker exports: text cleaning, decimals, dates and times.

Every parser returns ``None`` for an empty cell and raises ``ValueError`` (with a
Chinese message) for a cell that has content but cannot be read exactly. Nothing
is rounded and abbreviated values such as ``1.2万`` are refused.

Workbook numbers (``workbook_number``): an .xlsx cell is read from the text the file
stores, an .xls cell from its binary double, exactly; never through a float rounded
to fewer digits. Excel keeps 15 significant digits: a stored value with more digits
that lies within a few binary steps (``NOISE``, relative 2⁻⁵⁰) of its 15-digit form is
that form (``0.69999999999999996`` and ``43.680000000000007`` are 0.7 and 43.68, what
Excel shows); any other value (``123456789012345.5``) is kept exactly as stored, and
the bounds below then decide.

Separators: a comma is a thousands separator only in its grouping positions
(``1,234.56``). A file whose numbers use the decimal comma (``12,50``, ``1.234,56``,
typical of semicolon-separated exports) is read with that convention throughout
(``comma_convention``); a file that shows both, or a semicolon file whose only commas
could be either (``1,250``), is ``ambiguous`` and every number with a comma needs
review. A comma in any other position is refused, never dropped.

Bounds: a number has at most ``MAX_PLACES`` decimal places (counted on the digits
as written, before anything is normalised) and an absolute value of at most
``MAX_ABS``; scientific notation (``1.68E+05``, what a spreadsheet shows for a
rounded wide number) is refused. So every value that reaches the arithmetic is
small enough to be computed exactly.
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
import math
import re
import unicodedata
from typing import Iterable, Optional

EMPTY = {'', '-', '--', '---', '—', '——', '－', 'null', 'none', 'nan', 'n/a', '/', '无'}
_FORMULA_TEXT = re.compile(r'^=\s*"(.*)"$', re.S)
_NUMBER = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)$')
_SCIENTIFIC = re.compile(r'^[+-]?(\d+(\.\d*)?|\.\d+)[eE][+-]?\d+$')
MAX_PLACES = 10
MAX_ABS = Decimal('1e15')
# The largest absolute value of each event field (quantity in shares / units, money in its currency), for
# a broker row, a row edit and a template row alike (``bounded``, ``transactions.number_problem``).
LIMITS = {'quantity': Decimal('1e12'), 'price': Decimal('1e9'), 'fee': Decimal('1e12'), 'amount': MAX_ABS,
          'received_amount': MAX_ABS, 'fx_rate': Decimal('1000'), 'ratio': Decimal('1000')}
_ZERO_WIDTH = dict.fromkeys(map(ord, '​‌‍﻿'), None)


def clean(cell) -> str:
    """The visible text of a cell: no ``="..."`` wrapper, padding or invisible characters."""
    if cell is None:
        return ''
    text = str(cell).translate(_ZERO_WIDTH).replace('　', ' ').replace('\xa0', ' ').strip()
    match = _FORMULA_TEXT.match(text)
    if match:
        text = match.group(1).strip()
    if text.startswith("'") and len(text) > 1:
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text


def is_empty(cell) -> bool:
    return clean(cell).lower() in EMPTY


EXCEL_DIGITS = 15
NOISE = Decimal(2) ** -50   # a few units in the last place of a double: binary arithmetic, not a written digit


def _significant(number: Decimal) -> int:
    digits = ''.join(map(str, number.as_tuple().digits)).strip('0')
    return len(digits)


def _plain(number: Decimal) -> str:
    text_value = format(number, 'f')
    if '.' in text_value:
        text_value = text_value.rstrip('0').rstrip('.')
    return '0' if text_value in ('', '-0') else text_value


def workbook_number(value) -> str:
    """Exact text for a number a workbook stores: an int, a float (an .xls double), a Decimal, or the text of an
    .xlsx ``<v>`` element (see the module docstring). Never rounded through a shorter float."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    try:
        if isinstance(value, float):
            if not math.isfinite(value):
                return str(value)
            exact = Decimal(value)            # the binary value, every digit of it
        else:
            exact = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except InvalidOperation:
        return str(value)
    if not exact.is_finite():
        return str(value)
    if exact and _significant(exact) > EXCEL_DIGITS:
        with localcontext() as context:
            context.prec = EXCEL_DIGITS
            short = +exact
        if abs(exact - short) <= abs(short) * NOISE:
            exact = short
    return _plain(exact)


number_text = workbook_number


COMMA_CONVENTIONS = ('thousands', 'decimal', 'ambiguous')
_GROUPED = re.compile(r'[+-]?\d{1,3}(,\d{3})+(\.\d*)?')
_GROUPED_DOTS = re.compile(r'[+-]?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?')


def cell_comma(comma: str, native, index) -> str:
    """The convention for one cell: a native workbook number (``index`` among the row's ``native`` columns,
    ``sniff.Table.native``) is exact decimal text and never read by the convention of the file's text cells."""
    return 'thousands' if index is not None and index in native else comma


class UnsettledPoint(ValueError):
    """3.912 in a file whose text writes 5,00: a grouping point (3912) or a decimal one, the cell does not say which.
    ``decimal(..., grouping_point=True)`` reads it as the file's grouping, for a row whose own arithmetic settles it
    (``transactions.read``)."""


def _separators(text_value: str, comma: str, shown: str, grouping_point: bool = False) -> str:
    """``text_value`` with its grouping removed and a decimal comma made a point, by the file's convention."""
    if comma == 'decimal':
        if not grouping_point and '.' in text_value and ',' not in text_value and text_value.count('.') == 1:
            raise UnsettledPoint(f'“{shown}”只有一个点，而这个文件用逗号作小数点：无法确定点是千位分隔还是小数点')
        if '.' in text_value or ',' in text_value:
            if not _GROUPED_DOTS.fullmatch(text_value) or text_value.count(',') > 1:
                raise ValueError(f'“{shown}”与这个文件的数字格式（逗号是小数点）不符，无法确定数值')
            return text_value.replace('.', '').replace(',', '.')
        return text_value
    if ',' not in text_value:
        return text_value
    if comma == 'ambiguous':
        raise ValueError(f'“{shown}”里的逗号无法确定是千位分隔符还是小数点（这个文件两种写法都有，或用分号分隔）')
    if not _GROUPED.fullmatch(text_value):
        raise ValueError(f'“{shown}”里的逗号不在千位分隔的位置，可能是小数点，无法确定数值')
    return text_value.replace(',', '')


def _bare_number(cell) -> str:
    text_value = unicodedata.normalize('NFKC', clean(cell))
    for token in (' ', '元', '¥', '￥', 'HK$', 'US$', '$', '股', '张', '份'):
        text_value = text_value.replace(token, '')
    if text_value.startswith('(') and text_value.endswith(')'):
        text_value = text_value[1:-1]
    return text_value


def comma_convention(rows: Iterable[Iterable], delimiter: Optional[str] = None) -> str:
    """How a file writes commas in numbers: 'thousands' (``1,234.56``), 'decimal' (``12,50`` / ``1.234,56``) or
    'ambiguous' (both kinds appear, or a semicolon-separated file whose only commas could be either)."""
    decimal_comma = grouping = undecided = False
    for cells in rows:
        for cell in cells:
            if ',' not in str(cell or ''):
                continue
            number = _bare_number(cell).lstrip('+-')
            if re.fullmatch(r'\d{1,3}(,\d{3})+\.\d+', number):
                grouping = True
            elif re.fullmatch(r'\d{1,3}(\.\d{3})+,\d+', number) or re.fullmatch(r'\d+,(\d{1,2}|\d{4,})', number) \
                    or re.fullmatch(r'\d{4,},\d+', number):
                decimal_comma = True
            elif re.fullmatch(r'\d{1,3}(,\d{3})+', number):
                undecided = True
    if decimal_comma and grouping:
        return 'ambiguous'
    if decimal_comma:
        return 'decimal'
    if grouping:
        return 'thousands'
    return 'ambiguous' if undecided and delimiter == 'semicolon' else 'thousands'


def _places(number: Decimal) -> int:
    """Decimal places of ``number`` as written, ignoring trailing zeros; never rounds (no ``normalize()``)."""
    sign, digits, exponent = number.as_tuple()
    if not isinstance(exponent, int) or exponent >= 0:
        return 0
    trailing = len(digits) - len(''.join(map(str, digits)).rstrip('0'))
    return max(0, -exponent - trailing)


def decimal(cell, comma: str = 'thousands', grouping_point: bool = False) -> Optional[Decimal]:
    """A decimal from a cell, or ``None`` when the cell is empty. ``comma``: the file's ``comma_convention``;
    ``grouping_point``: read a lone point of a decimal-comma file as grouping (``UnsettledPoint``)."""
    text = clean(cell)
    if text.lower() in EMPTY:
        return None
    text = unicodedata.normalize('NFKC', text)
    if re.search(r'\d\s+[\d.,]|[.,]\s+\d', text):
        raise ValueError(f'“{clean(cell)}”的数字中间有空格，无法确定是千位分隔还是两个数字')
    for token in (' ', '元', '¥', '￥', 'HK$', 'US$', '$', '股', '张', '份'):
        text = text.replace(token, '')
    negative = False
    if text.startswith('(') and text.endswith(')'):
        negative, text = True, text[1:-1]
    if '万' not in text and '亿' not in text and '%' not in text and not _SCIENTIFIC.match(text.replace(',', '')):
        text = _separators(text, comma if comma in COMMA_CONVENTIONS else 'ambiguous', clean(cell), grouping_point)
    if '万' in text or '亿' in text or '%' in text:
        raise ValueError(f'“{clean(cell)}”是缩写或百分比，不是精确金额')
    if _SCIENTIFIC.match(text):
        raise ValueError(f'“{clean(cell)}”是科学计数法，可能已被四舍五入，不是精确数字')
    if not _NUMBER.match(text) or len(text) > 64:
        raise ValueError(f'“{clean(cell)}”不是数字')
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f'“{clean(cell)}”不是数字') from None
    if not number.is_finite() or abs(number) > MAX_ABS:
        raise ValueError(f'“{clean(cell)}”超出可处理的范围')
    if _places(number) > MAX_PLACES:
        raise ValueError(f'“{clean(cell)}”的小数位数超过 {MAX_PLACES} 位')
    return -number if negative else number


def bounded(cell, field: str, comma: str = 'thousands') -> Optional[Decimal]:
    """``decimal(cell)`` that also stays within ``LIMITS[field]``; ValueError otherwise."""
    number = decimal(cell, comma)
    limit = LIMITS.get(field)
    if number is not None and limit is not None and abs(number) > limit:
        raise ValueError(f'“{clean(cell)}”超出可处理的范围（最大 {text(limit)}）')
    return number


def cell_currency(cell) -> Optional[str]:
    """The currency a cell's own symbol names (``HK$100`` → HKD), ``'$'`` when ambiguous, else None.

    ``decimal`` removes these symbols; this keeps what they said."""
    text = unicodedata.normalize('NFKC', clean(cell)).upper()
    if 'HK$' in text:
        return 'HKD'
    if 'US$' in text:
        return 'USD'
    if '$' in text:
        return '$'
    if '¥' in text:
        return 'CNY'
    return None


_DATE_PATTERNS = [
    re.compile(r'^(\d{4})(\d{2})(\d{2})$'),
    re.compile(r'^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$'),
    re.compile(r'^(\d{4})年(\d{1,2})月(\d{1,2})日?(?:\s.*)?$'),
]


def parse_date(cell) -> Optional[str]:
    """``YYYY-MM-DD`` from 20261103, 2026-11-03, 2026/11/3, 2026年11月3日 (time part ignored)."""
    text = clean(cell)
    if text.lower() in EMPTY:
        return None
    text = unicodedata.normalize('NFKC', text)
    for pattern in _DATE_PATTERNS:
        match = pattern.match(text)
        if match:
            year, month, day = (int(part) for part in match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                break
    raise ValueError(f'“{clean(cell)}”不是有效日期')


_TIME = re.compile(r'(\d{1,2}):(\d{2})(?::(\d{2}))?')


def parse_time(cell) -> Optional[str]:
    """``HH:MM:SS`` from 09:31:25, 9:31, 093125 or 93125; ``None`` when empty."""
    text = clean(cell)
    if text.lower() in EMPTY:
        return None
    text = unicodedata.normalize('NFKC', text)
    if re.fullmatch(r'\d{5,6}', text):
        text = text.zfill(6)
        hour, minute, second = int(text[:2]), int(text[2:4]), int(text[4:])
    else:
        match = _TIME.search(text)
        if not match:
            raise ValueError(f'“{clean(cell)}”不是有效时间')
        hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    try:
        return time(hour, minute, second).isoformat()
    except ValueError:
        raise ValueError(f'“{clean(cell)}”不是有效时间') from None


def cell_text(value) -> str:
    """Text for any value a workbook reader returns."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        if value.time() == time(0):
            return value.date().isoformat()
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return workbook_number(value)
    return clean(value)


def text(number: Optional[Decimal]) -> Optional[str]:
    """Canonical decimal text (same rule as the ledger), ``None`` stays ``None``."""
    if number is None:
        return None
    if number == 0:
        return '0'
    out = format(number, 'f')
    return out.rstrip('0').rstrip('.') if '.' in out else out


def money(number: Optional[Decimal]) -> str:
    """Display form for messages: thousands separators, at least two decimals."""
    if number is None:
        return '无'
    exponent = number.as_tuple().exponent
    digits = max(2, -exponent if isinstance(exponent, int) and exponent < 0 else 0)
    return f'{number:,.{digits}f}'
