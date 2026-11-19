"""Read an uploaded broker file into a table of text cells.

* The file type comes from its first bytes, not its name: ``PK`` is xlsx
  (openpyxl), ``D0 CF 11 E0`` is a real xls (xlrd), ``<html`` / ``<table`` is a
  web page saved as .xls (parsed with the standard library, so codes keep their
  leading zeros), anything else is text. Broker ".xls" files are very often GBK
  tab-separated text.
* Text is decoded as UTF-8 (with or without BOM), then GB18030 (a superset of
  GBK/GB2312); UTF-16 with a BOM is accepted too.
* The delimiter is the one (tab, comma, semicolon, pipe, whitespace) that gives
  the best header row.
* Title lines above the header are kept (``title_rows``) and the header is the row
  among the first 60 with the most known column names, at least 3.
* Limits: 5 MB, 20,000 data rows, 100 columns, 500 characters per cell. An .xlsx is
  a zip: before openpyxl sees it, every member is inflated in 64 KB steps and the
  bytes it really produces are counted (at most ``MAX_ZIP_MEMBERS`` members,
  ``MAX_MEMBER_BYTES`` each and ``MAX_EXPANDED_BYTES`` in all, and no member above
  1 MB that expands more than ``MAX_RATIO`` times); a member that expands to more
  or less than it declares, or is not stored or deflated, is refused. So what
  openpyxl later reads is bounded by these limits. It reads in read-only mode with
  the row and column caps.

Workbook numbers arrive as exact decimal text (``values.workbook_number``: an .xlsx
number from the text the file stores, an .xls one from its double); ``numeric_columns``
remembers which columns held numbers, because a numeric code cell has lost its leading
zeros, and ``numeric_cells`` which cells of each row did: those are exact decimal text
and never read by the comma convention the file's text cells use (``values.cell_comma``). A merged cell's value applies to every cell of the merged range (.xlsx, .xls, and
``rowspan`` in a web page). A formula without a cached result is kept as its formula
text (``=2+3``), which no number or date parser accepts, so its row needs review
instead of reading the cell as empty.

Sheets (workbooks, and the tables of a web page): a sheet "looks like data" when
it has a header row and at least one row below it. Hidden sheets are ignored by
default. The visible data sheet with the best header is used; when more than one
visible sheet looks like data, or only hidden ones do, the table is marked
``sheet_ambiguous`` and the session asks the person to choose (``sheet``).
``sniff_all`` returns every data sheet so the choice needs no new upload.
"""
from __future__ import annotations

import codecs
import csv
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
import io
import re
from typing import List, Optional, Tuple

from core.importers import ImportFailure
from core.importers.mapping import header_matches
from core.importers.values import cell_text, clean, workbook_number

MAX_BYTES = 5 * 1024 * 1024
MAX_ZIP_MEMBERS = 500
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100
MAX_ROWS = 20_000
MAX_COLUMNS = 100
MAX_CELL = 500
HEADER_SCAN = 60
MIN_HEADER_MATCHES = 3
_READ_CAP = HEADER_SCAN + MAX_ROWS + 200

MISSING_LIBRARY = '服务器缺少 {library}，无法读取 {kind} 文件。请在券商软件里另存为 CSV 或文本后再导入。'
NO_HEADER = ('没有找到表头：前 60 行里没有一行包含至少 3 个可识别的列名（例如 成交日期、证券代码、成交数量）。'
             '请确认这是交割单、对账单或持仓导出文件。')


@dataclass
class Table:
    kind: str                      # text | xlsx | xls | html
    rows: List[List[str]]          # every row of the chosen sheet, title lines included
    header_index: int              # index into rows
    label: str = ''
    encoding: Optional[str] = None
    delimiter: Optional[str] = None
    sheet: Optional[str] = None
    sheets: List[str] = field(default_factory=list)
    numeric_columns: List[int] = field(default_factory=list)
    numeric_cells: List[List[int]] = field(default_factory=list)   # per row: the columns holding native numbers
    extension: str = ''
    sheet_options: List[dict] = field(default_factory=list)   # data sheets: name, hidden, data_rows
    sheet_ambiguous: bool = False                              # the person must choose the sheet

    @property
    def header(self) -> List[str]:
        return self.rows[self.header_index]

    @property
    def title_rows(self) -> List[List[str]]:
        return self.rows[:self.header_index]

    def native(self, number: int) -> frozenset:
        """The columns of row ``number`` (1-based) that held a native workbook number (``values.cell_comma``)."""
        if 0 < number <= len(self.numeric_cells):
            return frozenset(self.numeric_cells[number - 1])
        return frozenset()

    def data(self):
        """(1-based row number in the file or sheet, cells) for every non-blank row after the header."""
        for index in range(self.header_index + 1, len(self.rows)):
            cells = self.rows[index]
            if any(clean(cell) for cell in cells):
                yield index + 1, cells

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> 'Table':
        return cls(**data)


@dataclass
class _Sheet:
    name: Optional[str]
    rows: List[List[str]]
    numeric: List[frozenset]
    hidden: bool = False


def _trim(cells: List[str]) -> List[str]:
    cells = [cell[:MAX_CELL] for cell in cells[:MAX_COLUMNS]]
    while cells and not cells[-1]:
        cells.pop()
    return cells


def _best_header(rows: List[List[str]]) -> Tuple[int, int]:
    best_index, best_score = -1, 0
    for index, cells in enumerate(rows[:HEADER_SCAN]):
        score = header_matches(cells)
        if score > best_score:
            best_index, best_score = index, score
    return best_index, best_score


# -- text --------------------------------------------------------------------

def decode(data: bytes) -> Tuple[str, str]:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode('utf-16'), 'UTF-16'
        except UnicodeDecodeError:
            raise ImportFailure('文件以 UTF-16 标记开头，但内容无法解码。') from None
    try:
        return data.decode('utf-8-sig'), 'UTF-8' + (' (BOM)' if data.startswith(codecs.BOM_UTF8) else '')
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('gb18030'), 'GBK'
    except UnicodeDecodeError:
        raise ImportFailure('无法识别文件编码：既不是 UTF-8，也不是 GBK/GB18030。') from None


_DELIMITERS = [('\t', '制表符'), (',', '逗号'), (';', '分号'), ('|', '竖线'), (None, '空格')]


def _split(text: str, delimiter: Optional[str], limit: Optional[int] = None) -> List[List[str]]:
    rows: List[List[str]] = []
    if delimiter is None:
        for line in text.splitlines():
            rows.append(_trim([clean(cell) for cell in line.split()]))
            if limit is not None and len(rows) >= limit:
                break
        return rows
    try:
        for cells in csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=False):
            rows.append(_trim([clean(cell) for cell in cells]))
            if limit is not None and len(rows) >= limit:
                break
    except csv.Error:
        raise ImportFailure('文本文件的格式无法解析（引号或字段过长）。') from None
    return rows


def _text(data: bytes) -> Tuple[_Sheet, str, str]:
    text, encoding = decode(data)
    if '\x00' in text:
        raise ImportFailure('这不是可识别的表格文件（含有二进制内容）。', 415)
    best = None
    for delimiter, name in _DELIMITERS:
        index, score = _best_header(_split(text, delimiter, HEADER_SCAN))
        if best is None or score > best[0]:
            best = (score, delimiter, name)
    _score, delimiter, name = best
    rows = _split(text, delimiter, _READ_CAP)
    return _Sheet(None, rows, [frozenset()] * len(rows)), encoding, name


# -- html ("xls" saved as a web page) ------------------------------------------

class _TableParser(HTMLParser):
    """Collects <table> rows as text cells; tolerant of missing end tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: List[List[List[str]]] = []
        self._stack: List[dict] = []

    @staticmethod
    def _carry_into(ctx, until=None):
        """Put the cells of earlier rows' ``rowspan`` into this row, at their columns (all of them when ``until``
        is None, else those before the next cell of this row)."""
        row, carry = ctx['row'], ctx['carry']
        while True:
            pending = sorted(column for column in carry if column >= len(row))
            if not pending or until is not None and pending[0] > len(row):
                break
            row.extend([''] * (pending[0] - len(row)))
            left, text = carry[pending[0]]
            row.append(text)
            if left <= 1:
                del carry[pending[0]]
            else:
                carry[pending[0]] = (left - 1, text)

    def _close_cell(self, ctx):
        if ctx['cell'] is not None:
            text = re.sub(r'\s+', ' ', ''.join(ctx['cell'])).strip()
            if ctx['row'] is None:
                ctx['row'] = []
            self._carry_into(ctx, until=len(ctx['row']))
            position = len(ctx['row'])
            ctx['row'].append(text)
            ctx['row'].extend([''] * (ctx['span'] - 1))
            if ctx['rowspan'] > 1:   # a merged cell: its value applies to every row it spans
                ctx['carry'][position] = (ctx['rowspan'] - 1, text)
            ctx['cell'] = None

    def _close_row(self, ctx):
        self._close_cell(ctx)
        if ctx['row'] is None and ctx['carry'] and ctx['started']:
            ctx['row'] = []
        if ctx['row'] is not None:
            self._carry_into(ctx)
            ctx['rows'].append(ctx['row'])
            ctx['row'] = None
        ctx['started'] = False

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._stack.append({'rows': [], 'row': None, 'cell': None, 'span': 1, 'rowspan': 1, 'carry': {},
                                'started': False})
            return
        if not self._stack:
            return
        ctx = self._stack[-1]
        if tag == 'tr':
            self._close_row(ctx)
            ctx['row'] = []
            ctx['started'] = True
        elif tag in ('td', 'th'):
            self._close_cell(ctx)
            span = dict(attrs).get('colspan') or '1'
            ctx['span'] = min(int(span), MAX_COLUMNS) if span.isdigit() and int(span) > 0 else 1
            rows = dict(attrs).get('rowspan') or '1'
            ctx['rowspan'] = min(int(rows), _READ_CAP) if rows.isdigit() and int(rows) > 0 else 1
            ctx['cell'] = []
        elif tag == 'br' and ctx['cell'] is not None:
            ctx['cell'].append(' ')

    def handle_endtag(self, tag):
        if not self._stack:
            return
        ctx = self._stack[-1]
        if tag in ('td', 'th'):
            self._close_cell(ctx)
        elif tag == 'tr':
            self._close_row(ctx)
        elif tag == 'table':
            self._close_row(ctx)
            self.tables.append(self._stack.pop()['rows'])

    def handle_data(self, data):
        if self._stack and self._stack[-1]['cell'] is not None:
            self._stack[-1]['cell'].append(data)

    def finish(self):
        self.close()
        while self._stack:
            ctx = self._stack[-1]
            self._close_row(ctx)
            self.tables.append(self._stack.pop()['rows'])
        return self.tables


def _html_text(data: bytes) -> Tuple[str, str]:
    declared = re.search(rb'charset\s*=\s*["\']?\s*([A-Za-z0-9_-]+)', data[:4096], re.I)
    if declared:
        name = declared.group(1).decode('ascii').lower()
        codec = 'gb18030' if name in {'gb2312', 'gbk', 'gb18030', 'x-gbk', 'cp936'} else name
        try:
            return data.decode(codec), 'GBK' if codec == 'gb18030' else name.upper()
        except (LookupError, UnicodeDecodeError):
            pass
    return decode(data)


def _html(data: bytes) -> Tuple[List[_Sheet], str]:
    text, encoding = _html_text(data)
    parser = _TableParser()
    try:
        parser.feed(text)
        tables = parser.finish()
    except Exception:
        raise ImportFailure('网页格式的表格无法解析。') from None
    sheets = [_Sheet(f'表格 {i + 1}', [_trim([clean(c) for c in row]) for row in rows[:_READ_CAP]],
                     [frozenset()] * min(len(rows), _READ_CAP))
              for i, rows in enumerate(tables) if rows]
    if not sheets:
        raise ImportFailure('网页文件里没有表格。', 415)
    return sheets, encoding


# -- workbooks -----------------------------------------------------------------

def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_INFLATE_STEP = 64 * 1024
_UNREADABLE = '无法打开这个 .xlsx 文件：它可能已损坏、加密，或不是表格。'


def _expanded_size(data: bytes, member, limit: int) -> int:
    """How many bytes ``member`` really expands to, inflated 64 KB at a time (nothing is kept); counting
    stops as soon as it passes ``limit``. The sizes a zip declares are not trusted: zipfile inflates a
    member in one call when asked for all of it, and only then cuts it to the declared size."""
    import struct
    import zipfile
    import zlib
    offset = member.header_offset
    if data[offset:offset + 4] != b'PK\x03\x04':
        raise ImportFailure(_UNREADABLE)
    name_length, extra_length = struct.unpack_from('<HH', data, offset + 26)
    start = offset + 30 + name_length + extra_length
    raw = memoryview(data)[start:start + member.compress_size]
    if len(raw) != member.compress_size:
        raise ImportFailure(_UNREADABLE)
    if member.compress_type == zipfile.ZIP_STORED:
        return len(raw)
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    produced, position, pending = 0, 0, b''
    try:
        while not inflater.eof and produced <= limit:
            if not pending:
                if position >= len(raw):
                    break
                pending, position = raw[position:position + _INFLATE_STEP], position + _INFLATE_STEP
            produced += len(inflater.decompress(pending, _INFLATE_STEP))
            pending = inflater.unconsumed_tail
    except zlib.error:
        raise ImportFailure(_UNREADABLE) from None
    return produced


def _check_zip(data: bytes) -> None:
    """Refuse a workbook whose members would expand beyond the limits, before anything parses it.

    Every member is inflated here first, counting the bytes it really produces, so the limits hold for
    the actual expansion; only stored and deflated members are accepted (BZIP2 / LZMA cannot be inflated
    in bounded steps), and a member that expands to more or less than it declares is refused."""
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ImportFailure(_UNREADABLE) from None
    too_large = ImportFailure('这个 .xlsx 文件解压后过大（或压缩比异常），无法安全读取。'
                              '请在 Excel 中另存为 CSV，或缩短导出的日期范围后再试。', 413)
    if len(members) > MAX_ZIP_MEMBERS:
        raise ImportFailure(f'这个 .xlsx 文件包含 {len(members)} 个内部文件，不像券商导出的表格。'
                            '请在 Excel 中另存为 CSV 后再导入。', 415)
    total = 0
    for member in members:
        if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ImportFailure('这个 .xlsx 文件使用了 Excel 不会使用的压缩方式，无法安全读取。'
                                '请在 Excel 中重新保存或另存为 CSV 后再导入。', 415)
        if member.flag_bits & 0x1:
            raise ImportFailure(_UNREADABLE)
        if member.file_size > MAX_MEMBER_BYTES or total + member.file_size > MAX_EXPANDED_BYTES:
            raise too_large
        expanded = _expanded_size(data, member, member.file_size)
        if expanded > member.file_size:
            raise too_large
        if expanded < member.file_size:
            raise ImportFailure(_UNREADABLE)
        total += expanded
        if expanded > 1024 * 1024 and expanded > MAX_RATIO * max(member.compress_size, 1):
            raise too_large


_REFERENCE = re.compile(r'([A-Z]{1,3})(\d+)')


def _reference(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """``B3`` → (3, 2), 1-based; None when it is not a cell reference."""
    match = _REFERENCE.fullmatch(text or '')
    if not match:
        return None
    column = 0
    for letter in match.group(1):
        column = column * 26 + ord(letter) - 64
    return int(match.group(2)), column


def _sheet_part(data: bytes, worksheet) -> Tuple[dict, dict, list]:
    """What openpyxl's values do not say, read from the worksheet's XML (bounded by ``_check_zip``):
    ({(row, column): the stored text of a number}, {(row, column): '=formula' without a cached result},
    [merged ranges (first row, first column, last row, last column)]), all 1-based."""
    import zipfile
    from xml.etree.ElementTree import iterparse
    path = getattr(worksheet, '_worksheet_path', None)
    if not isinstance(path, str):
        raise ImportFailure('无法安全读取这个 .xlsx 文件的工作表。请在 Excel 中另存为 CSV 后再导入。', 415)
    numbers, formulas, merges = {}, {}, []
    row_number = column = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive, archive.open(path.lstrip('/')) as part:
        for event, element in iterparse(part, events=('start', 'end')):
            tag = element.tag.rsplit('}', 1)[-1]
            if event == 'start':
                if tag == 'row':
                    stated = element.get('r')
                    row_number = int(stated) if stated and stated.isdigit() else row_number + 1
                    column = 0
                continue
            if tag == 'c':
                place = _reference(element.get('r'))
                if place is not None:
                    row_number, column = place
                else:
                    column += 1
                if row_number <= _READ_CAP and column <= MAX_COLUMNS:
                    children = {child.tag.rsplit('}', 1)[-1]: child for child in element}
                    value = children['v'].text if 'v' in children else None
                    if 'f' in children and not value:
                        formulas[(row_number, column)] = '=' + (children['f'].text or '')[:MAX_CELL - 1]
                    elif element.get('t', 'n') == 'n' and value:
                        numbers[(row_number, column)] = value.strip()
                element.clear()
            elif tag == 'row':
                element.clear()
            elif tag == 'mergeCell':
                first, _sep, last = (element.get('ref') or '').partition(':')
                start, end = _reference(first), _reference(last or first)
                if start and end:
                    merges.append((start[0], start[1], end[0], end[1]))
    return numbers, formulas, merges


def _apply_merges(rows: List[List[str]], numeric: List[set], merges) -> None:
    """Give every cell of a merged range the value of its first cell (in place; 1-based ranges)."""
    for first_row, first_column, last_row, last_column in merges:
        if first_row > len(rows):
            continue
        source = rows[first_row - 1]
        value = source[first_column - 1] if first_column - 1 < len(source) else ''
        is_number = first_column - 1 in numeric[first_row - 1]
        for r in range(first_row, min(last_row, len(rows)) + 1):
            cells = rows[r - 1]
            for c in range(first_column, min(last_column, MAX_COLUMNS) + 1):
                if len(cells) < c:
                    cells.extend([''] * (c - len(cells)))
                cells[c - 1] = value
                if is_number:
                    numeric[r - 1].add(c - 1)


def _xlsx(data: bytes) -> List[_Sheet]:
    try:
        import openpyxl
    except ImportError:
        raise ImportFailure(MISSING_LIBRARY.format(library='openpyxl', kind='.xlsx'), 415) from None
    _check_zip(data)
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFailure('无法打开这个 .xlsx 文件：它可能已损坏、加密，或不是表格。') from None
    sheets = []
    try:
        for worksheet in book.worksheets:
            numbers, formulas, merges = _sheet_part(data, worksheet)
            rows, numeric = [], []
            for r, values in enumerate(worksheet.iter_rows(values_only=True, max_row=_READ_CAP, max_col=MAX_COLUMNS), 1):
                cells, found = [], set()
                for c, value in enumerate(values[:MAX_COLUMNS], 1):
                    if (r, c) in formulas:
                        cells.append(formulas[(r, c)])
                    elif _numeric(value):
                        found.add(c - 1)
                        cells.append(workbook_number(numbers.get((r, c), value)))
                    else:
                        cells.append(cell_text(value))
                rows.append(cells)
                numeric.append(found)
                if len(rows) >= _READ_CAP:
                    break
            _apply_merges(rows, numeric, merges)
            sheets.append(_Sheet(worksheet.title, [_trim(cells) for cells in rows], [frozenset(n) for n in numeric],
                                 getattr(worksheet, 'sheet_state', 'visible') != 'visible'))
    except ImportFailure:
        raise
    except Exception:
        raise ImportFailure('读取 .xlsx 文件时出错：文件可能已损坏。') from None
    finally:
        book.close()
    return sheets


def _xls(data: bytes) -> List[_Sheet]:
    try:
        import xlrd
    except ImportError:
        raise ImportFailure(MISSING_LIBRARY.format(library='xlrd', kind='.xls'), 415) from None
    try:
        # formatting_info: xlrd reports merged cells only with it.
        book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=True,
                                  logfile=io.StringIO(), verbosity=0)
    except Exception as exc:
        if 'encrypt' in str(exc).lower():
            raise ImportFailure('这个 .xls 文件已加密，请先在 Excel 中取消密码。') from None
        raise ImportFailure('无法打开这个 .xls 文件：它可能已损坏，或不是 Excel 表格。') from None
    sheets = []
    try:
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            rows, numeric = [], []
            for r in range(min(sheet.nrows, _READ_CAP)):
                cells, numbers = [], set()
                for c, cell in enumerate(sheet.row(r)[:MAX_COLUMNS]):
                    if cell.ctype == xlrd.XL_CELL_NUMBER:
                        numbers.add(c)
                        cells.append(cell_text(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            cells.append(cell_text(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)))
                        except Exception:
                            cells.append(cell_text(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_TEXT:
                        cells.append(clean(cell.value))
                    else:
                        cells.append('')
                rows.append(cells)
                numeric.append(set(numbers))
            _apply_merges(rows, numeric, [(top + 1, left + 1, bottom, right) for top, bottom, left, right
                                          in getattr(sheet, 'merged_cells', [])])
            sheets.append(_Sheet(sheet.name, [_trim(cells) for cells in rows], [frozenset(n) for n in numeric],
                                 bool(getattr(sheet, 'visibility', 0))))
            book.unload_sheet(index)
    except Exception:
        raise ImportFailure('读取 .xls 文件时出错：文件可能已损坏。') from None
    finally:
        book.release_resources()
    return sheets


# -- entry point ---------------------------------------------------------------

def _looks_html(data: bytes) -> bool:
    probe = data[:4096].lstrip(codecs.BOM_UTF8).lstrip().lower()
    return probe.startswith((b'<!doctype html', b'<html', b'<table', b'<meta', b'<head', b'<body', b'<style')) or (
        probe.startswith(b'<') and (b'<table' in probe or b'<html' in probe))


def sniff(data: bytes, filename: str = '') -> Table:
    """The table to import: the best visible data sheet (see the module docstring)."""
    return sniff_all(data, filename)[0]


def sniff_all(data: bytes, filename: str = '') -> Tuple[Table, List[Table]]:
    """(the table to import, every data sheet's table including that one)."""
    if not data:
        raise ImportFailure('文件是空的。')
    if len(data) > MAX_BYTES:
        raise ImportFailure('文件超过 5 MB。请缩短导出的日期范围后再试。', 413)
    extension = ('.' + filename.rsplit('.', 1)[-1].lower()) if '.' in (filename or '') else ''
    encoding = delimiter = None
    if data.startswith(b'PK\x03\x04'):
        kind, sheets, label = 'xlsx', _xlsx(data), 'Excel 工作簿 (.xlsx)'
    elif data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        kind, sheets, label = 'xls', _xls(data), 'Excel 97-2003 工作簿 (.xls)'
    elif _looks_html(data):
        sheets, encoding = _html(data)
        kind, label = 'html', f'网页表格（{encoding}）'
    elif data[:256].lstrip(codecs.BOM_UTF8).lstrip().startswith(b'<?xml'):
        raise ImportFailure('这是 XML 格式的表格，暂不支持。请在 Excel 中另存为 .xlsx 或 CSV 后再导入。', 415)
    else:
        sheet, encoding, delimiter_name = _text(data)
        sheets, kind = [sheet], 'text'
        delimiter = {'制表符': 'tab', '逗号': 'comma', '分号': 'semicolon', '竖线': 'pipe', '空格': 'whitespace'}[delimiter_name]
        label = f'{encoding} {delimiter_name}分隔文本'
    if extension in ('.xls', '.xlsx') and kind != extension[1:]:
        label += f'（扩展名是 {extension}）'

    found = []   # (sheet, header index, score, data rows)
    best_any = None
    for sheet in sheets:
        index, score = _best_header(sheet.rows)
        if best_any is None or score > best_any[2]:
            best_any = (sheet, index, score)
        if score >= MIN_HEADER_MATCHES:
            data_rows = [i for i in range(index + 1, len(sheet.rows)) if any(sheet.rows[i])]
            if data_rows:
                found.append((sheet, index, score, data_rows))
    if not found:
        if best_any is None or best_any[2] < MIN_HEADER_MATCHES:
            raise ImportFailure(NO_HEADER)
        sheet, index, score = best_any
        found = [(sheet, index, score, [])]   # a header without rows: the session shows 0 rows
    visible = [item for item in found if not item[0].hidden]
    pool = visible or found
    chosen = max(pool, key=lambda item: item[2])   # the first one on a tie
    ambiguous = len(pool) > 1 or not visible
    options = [{'name': item[0].name, 'hidden': item[0].hidden, 'data_rows': len(item[3])} for item in found
               if item[0].name is not None]
    names = [s.name for s in sheets if s.name]
    table, tables = None, []
    for sheet, header_index, _score, data_rows in found:
        rows = sheet.rows
        if len(data_rows) > MAX_ROWS or len(rows) >= _READ_CAP:
            if sheet is chosen[0]:
                raise ImportFailure(f'表格超过 {MAX_ROWS:,} 行。请分段导出（例如按月）后分别导入。', 413)
            continue   # too large to offer as an alternative
        numeric = sorted(set().union(*[sheet.numeric[i] for i in data_rows])) if data_rows else []
        tables.append(Table(kind=kind, rows=rows, header_index=header_index, label=label, encoding=encoding,
                            delimiter=delimiter, sheet=sheet.name, sheets=names, numeric_columns=numeric,
                            numeric_cells=[sorted(n) for n in sheet.numeric] if any(sheet.numeric) else [],
                            extension=extension, sheet_options=options, sheet_ambiguous=ambiguous))
        if sheet is chosen[0]:
            table = tables[-1]
    return table, tables
