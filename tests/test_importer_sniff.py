"""Reading broker files: type from magic bytes, encodings, delimiters, header rows, limits."""
import builtins
from decimal import Decimal

import pytest

from core.importers import ImportFailure, sniff
from core.importers.values import clean, decimal, number_text, parse_date, parse_time
from tests.importer_support import fixture, table_rows, xls_bytes, xlsx_bytes


def test_gbk_tab_text_named_xls_is_read_as_text():
    table = sniff.sniff(fixture('ths_trades_gbk.xls'), '交割单.xls')
    assert (table.kind, table.encoding, table.delimiter) == ('text', 'GBK', 'tab')
    assert '扩展名是 .xls' in table.label
    assert table.header_index == 1 and table.header[:3] == ['成交日期', '成交时间', '证券代码']
    assert table.title_rows[0][0].startswith('同花顺')
    first = next(table.data())
    assert first[0] == 3 and first[1][2] == '600519' and first[1][3] == '贵州茅台'


def test_real_xls_workbook_keeps_exact_numbers():
    rows = table_rows(fixture('ths_trades_gbk.xls'))[1:-1]
    numeric = {5, 6, 7, 8, 9, 10, 11, 12, 13, 14}
    converted = [rows[0]] + [[float(c) if i in numeric and c else c for i, c in enumerate(r)] for r in rows[1:]]
    table = sniff.sniff(xls_bytes(converted), 'jgd.xls')
    assert table.kind == 'xls' and table.label.startswith('Excel 97-2003') and table.sheet == '交割单'
    _number, cells = next(table.data())
    assert cells[2] == '600519' and cells[7] == '168000' and cells[8] == '42' and cells[12] == '-168043.68'
    assert 2 not in table.numeric_columns and {7, 12} <= set(table.numeric_columns)


def test_xlsx_numbers_and_dates_become_exact_text():
    from datetime import datetime
    rows = [['成交日期', '证券代码', '证券名称', '成交数量', '成交金额', '手续费'],
            [datetime(2026, 11, 3), 600519, '贵州茅台', 100, 168000.0, 43.680000000000007],
            [datetime(2026, 11, 4, 9, 31, 25), 1, '平安银行', 1000, 0.1 + 0.2, 5]]
    table = sniff.sniff(xlsx_bytes(rows), 'a.xlsx')
    assert table.kind == 'xlsx' and table.extension == '.xlsx'
    assert table.rows[1] == ['2026-11-03', '600519', '贵州茅台', '100', '168000', '43.68']
    assert table.rows[2][0] == '2026-11-04 09:31:25' and table.rows[2][4] == '0.3'
    assert 1 in table.numeric_columns, 'a numeric code column has lost its leading zeros'


def test_html_saved_as_xls_keeps_leading_zeros():
    table = sniff.sniff(fixture('eastmoney_trades_html.xls'), 'x.xls')
    assert table.kind == 'html' and table.encoding == 'GBK'
    assert table.header_index == 1
    _number, cells = next(table.data())
    assert cells[2] == '000001' and cells[3] == '平安银行'


def test_bom_csv_and_plain_utf8_and_utf16_text():
    table = sniff.sniff(fixture('eastmoney_statement_bom.csv'), 'a.csv')
    assert (table.encoding, table.delimiter) == ('UTF-8 (BOM)', 'comma') and table.header[0] == '发生日期'
    plain = '成交日期,证券代码,成交数量,成交价格\n2026-11-03,600519,100,1680\n'
    assert sniff.sniff(plain.encode('utf-8'), 'a.csv').encoding == 'UTF-8'
    utf16 = sniff.sniff(plain.replace(',', '\t').encode('utf-16'), 'a.txt')
    assert (utf16.encoding, utf16.delimiter) == ('UTF-16', 'tab') and utf16.rows[1][1] == '600519'


def test_formula_wrapped_cells_and_newest_first_text():
    table = sniff.sniff(fixture('tdx_trades_gbk.txt'), 'a.txt')
    _number, cells = next(table.data())
    assert cells[2] == '000001' and cells[16] == '700003', '="..." wrappers are removed'


def test_whitespace_aligned_text():
    text = '证券代码  证券名称  成交数量  成交价格  成交日期\n600519    贵州茅台   100      1680.00   20261103\n'
    table = sniff.sniff(text.encode('gb18030'), 'a.txt')
    assert table.delimiter == 'whitespace' and table.rows[1] == ['600519', '贵州茅台', '100', '1680.00', '20261103']


def test_header_needs_three_known_columns():
    with pytest.raises(ImportFailure, match='没有找到表头') as caught:
        sniff.sniff('名字,年龄\n张三,3\n'.encode(), 'a.csv')
    assert caught.value.status == 422


@pytest.mark.parametrize('data, status, message', [
    (b'', 422, '空的'),
    (b'x' * (sniff.MAX_BYTES + 1), 413, '5 MB'),
    (b'PK\x03\x04 not really a zip', 422, 'xlsx'),
    (bytes.fromhex('D0CF11E0A1B11AE1') + b'\0' * 600, 422, 'xls'),
    (b'\x00\x01\x02\x03' * 50, 415, '二进制'),
    (b'<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"/>', 415, 'XML'),
    (b'<html><body>no table here</body></html>', 415, '没有表格'),
    (b'\xff\xfe\x00\xd8', 422, 'UTF-16'),
])
def test_bad_files_are_4xx_with_a_chinese_message(data, status, message):
    with pytest.raises(ImportFailure) as caught:
        sniff.sniff(data, 'file.xls')
    assert caught.value.status == status and message in str(caught.value)


def test_row_limit():
    lines = ['成交日期,证券代码,成交数量,成交价格'] + ['2026-11-03,600519,100,1680'] * (sniff.MAX_ROWS + 1)
    with pytest.raises(ImportFailure, match='20,000') as caught:
        sniff.sniff('\n'.join(lines).encode(), 'big.csv')
    assert caught.value.status == 413


def test_missing_parser_library_degrades_with_a_message(monkeypatch):
    workbook = xlsx_bytes([['成交日期', '证券代码', '成交数量']])
    real_import = builtins.__import__

    def no_openpyxl(name, *args, **kwargs):
        if name == 'openpyxl':
            raise ImportError(name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_openpyxl)
    with pytest.raises(ImportFailure, match='openpyxl') as caught:
        sniff.sniff(workbook, 'a.xlsx')
    assert caught.value.status == 415


def test_cell_values_are_exact_or_refused():
    assert clean(' ="000001" ') == '000001' and clean("'600519") == '600519'
    assert decimal('1,680.00') == Decimal('1680.00') and decimal('(100.50)') == Decimal('-100.50')
    assert decimal('¥12.30') == Decimal('12.30') and decimal('－－'.replace('－', '-')) is None
    assert decimal('--') is None and decimal('') is None and decimal(None) is None
    for bad in ('1.2万', '3%', 'abc', '1e400'):
        with pytest.raises(ValueError):
            decimal(bad)
    assert number_text(43.680000000000007) == '43.68' and number_text(168000.0) == '168000'
    assert number_text(Decimal('1.50')) == '1.5' and number_text(-0.1) == '-0.1'
    assert [parse_date(v) for v in ('20261103', '2026-11-03', '2026/11/3', '2026.11.03', '2026年11月3日',
                                     '2026-11-03 09:31:25')] == ['2026-11-03'] * 6
    for bad in ('2026-02-30', '46329', '11/03/2026'):
        with pytest.raises(ValueError):
            parse_date(bad)
    assert [parse_time(v) for v in ('09:31:25', '9:31', '093125', '93125')] == ['09:31:25', '09:31:00', '09:31:25', '09:31:25']


def rezip(data: bytes, replace=None, add=()) -> bytes:
    """The workbook ``data`` with members replaced or added, all deflated."""
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, (replace or {}).get(info.filename, source.read(info.filename)))
        for name, content in add:
            target.writestr(name, content)
    return out.getvalue()


WORKBOOK = [['成交日期', '证券代码', '证券名称', '成交数量', '成交金额', '手续费'], ['2026-11-03', '600519', '贵州茅台', 100, 1, 1]]


def test_workbook_expansion_is_bounded_before_parsing(monkeypatch):
    # Review finding 14: a 13 KB workbook with an 8.4 MB sharedStrings member was accepted.
    import openpyxl
    calls = []
    real = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, 'load_workbook', lambda *a, **k: calls.append(1) or real(*a, **k))
    # openpyxl writes inline strings, so the shared strings member is added as the reviewer's file had it.
    bomb = rezip(xlsx_bytes(WORKBOOK), add=[('xl/sharedStrings.xml', b'<sst>' + b'a' * (8 * 1024 * 1024) + b'</sst>')])
    assert len(bomb) < 100_000
    with pytest.raises(ImportFailure, match='解压') as caught:
        sniff.sniff(bomb, 'inflated.xlsx')
    assert caught.value.status == 413 and calls == [], 'refused before openpyxl reads it'
    spread = rezip(xlsx_bytes(WORKBOOK), add=[(f'xl/media/pad{i}.bin', b'\0' * 1_000_000) for i in range(60)])
    with pytest.raises(ImportFailure, match='解压') as caught:
        sniff.sniff(spread, 'spread.xlsx')
    assert caught.value.status == 413
    many = rezip(xlsx_bytes(WORKBOOK), add=[(f'xl/x/{i}.xml', b'<a/>') for i in range(sniff.MAX_ZIP_MEMBERS)])
    with pytest.raises(ImportFailure, match='文件') as caught:
        sniff.sniff(many, 'many.xlsx')
    assert caught.value.status == 415
    assert sniff.sniff(xlsx_bytes(WORKBOOK), 'fine.xlsx').rows[1][1] == '600519' and calls


def test_workbook_rows_and_columns_are_capped_while_reading():
    wide = [['成交日期', '证券代码', '证券名称'] + [f'c{i}' for i in range(200)], ['2026-11-03', '600519', '贵州茅台'] + ['x'] * 200]
    table = sniff.sniff(xlsx_bytes(wide), 'wide.xlsx')
    assert max(len(row) for row in table.rows) <= sniff.MAX_COLUMNS


def falsify(data: bytes, member: str, declared: int) -> bytes:
    """``data`` with ``member``'s declared expanded size set to ``declared`` (and its CRC to that of the first
    ``declared`` expanded bytes), in the local header and in the central directory."""
    import io
    import struct
    import zipfile
    import zlib
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(member)
        crc = zlib.crc32(archive.read(member)[:declared])
    out = bytearray(data)
    struct.pack_into('<II', out, info.header_offset + 14, crc, info.compress_size)
    struct.pack_into('<I', out, info.header_offset + 22, declared)
    end = data.rfind(b'PK\x05\x06')
    position = struct.unpack_from('<I', data, end + 16)[0]
    while data[position:position + 4] == b'PK\x01\x02':
        name_length, extra_length, comment_length = struct.unpack_from('<HHH', data, position + 28)
        if data[position + 46:position + 46 + name_length].decode() == member:
            struct.pack_into('<I', out, position + 16, crc)
            struct.pack_into('<I', out, position + 24, declared)
        position += 46 + name_length + extra_length + comment_length
    return bytes(out)


def test_workbook_limits_hold_for_the_bytes_actually_expanded(monkeypatch):
    # Review round 2, finding 8: a member declaring 550 bytes expanded to 8 MB in one call (BZIP2 and LZMA too).
    import io
    import zipfile
    import openpyxl
    calls = []
    real = openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, 'load_workbook', lambda *a, **k: calls.append(1) or real(*a, **k))
    bomb = rezip(xlsx_bytes(WORKBOOK), add=[('xl/sharedStrings.xml', b'<sst>' + b'a' * (8 * 1024 * 1024) + b'</sst>')])
    lying = falsify(bomb, 'xl/sharedStrings.xml', 550)
    with zipfile.ZipFile(io.BytesIO(lying)) as archive:
        assert archive.getinfo('xl/sharedStrings.xml').file_size == 550, 'the declared size passes the old checks'
    with pytest.raises(ImportFailure, match='解压') as caught:
        sniff.sniff(lying, 'lying.xlsx')
    assert caught.value.status == 413 and calls == []
    for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes(WORKBOOK))) as source, zipfile.ZipFile(out, 'w', method) as target:
            for info in source.infolist():
                target.writestr(info.filename, source.read(info.filename))
        with pytest.raises(ImportFailure, match='压缩') as caught:
            sniff.sniff(out.getvalue(), 'method.xlsx')
        assert caught.value.status == 415 and calls == []
    stored = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes(WORKBOOK))) as source, zipfile.ZipFile(stored, 'w') as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info.filename))
    assert sniff.sniff(stored.getvalue(), 'stored.xlsx').rows[1][1] == '600519', 'stored members are read'
    short = falsify(rezip(xlsx_bytes(WORKBOOK)), 'xl/worksheets/sheet1.xml', 10)
    with pytest.raises(ImportFailure) as caught:
        sniff.sniff(short, 'short.xlsx')
    assert caught.value.status in (413, 422)
