"""Shared helpers for the broker importer tests: fictional ledger setup and workbook writers.

The binary workbooks (.xlsx, real BIFF8 .xls) are written at test time instead of
being committed: the public artifact check treats tracked .xlsx files as private
data, and xlwt is not a dependency. ``xls_bytes`` writes a minimal OLE2 compound
document with a BIFF8 ``Workbook`` stream (LABEL and NUMBER cells), which is what
xlrd reads from a real Excel 97-2003 file.
"""
from decimal import Decimal
import io
from pathlib import Path
import struct

from core.ledger import Ledger

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'broker'
ACCOUNT = '示例账户'
OPENING = {'kind': 'opening', 'date': '2026-11-01', 'account': ACCOUNT, 'cash_balances': {'CNY': '500000.00'},
           'contributed_cny': '551000',
           'positions': [{'ticker': 'SHA:600036', 'name': '招商银行', 'quantity': '1000', 'cost_price': '30'},
                         {'ticker': 'SHE:000001', 'name': '平安银行', 'quantity': '2000', 'cost_price': '10.5'}]}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def ledger_at(directory) -> Ledger:
    return Ledger(Path(directory) / 'ledger.sqlite3')


def book(ledger: Ledger, events) -> dict:
    return ledger.confirm(ledger.propose(events)['id'])


def open_account(ledger: Ledger, **changes) -> dict:
    return book(ledger, [{**OPENING, **changes}])


def table_rows(data: bytes, encoding='gb18030', delimiter='\t'):
    """Rows of a text fixture, for re-writing it as a workbook."""
    return [line.split(delimiter) for line in data.decode(encoding).splitlines() if line.strip()]


# -- xlsx --------------------------------------------------------------------------

def xlsx_bytes(rows, numeric=lambda r, c, v: False) -> bytes:
    import openpyxl
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = '交割单'
    for r, row in enumerate(rows):
        sheet.append([float(Decimal(v)) if numeric(r, c, v) else v for c, v in enumerate(row)])
    out = io.BytesIO()
    workbook.save(out)
    return out.getvalue()


# -- xls (BIFF8 in an OLE2 compound document) --------------------------------------

ENDOFCHAIN, FREESECT, FATSECT, NOSTREAM = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF


def _record(kind, data=b''):
    return struct.pack('<HH', kind, len(data)) + data


def _text(value, length_bytes):
    raw = value.encode('utf-16-le')
    return struct.pack('<B' if length_bytes == 1 else '<H', len(raw) // 2) + b'\x01' + raw


def _bof(stream_type):
    return _record(0x0809, struct.pack('<HHHHII', 0x0600, stream_type, 0x0DBB, 0x07CC, 0, 0x06))


def _workbook_stream(rows, sheet_name):
    xf = lambda type_par: _record(0x00E0, struct.pack('<HHHBBBBIiH', 0, 0, type_par, 0x20, 0, 0, 0, 0, 0, 0x20C0))
    font = _record(0x0031, struct.pack('<HHHHHBBBB', 200, 0, 0x7FFF, 400, 0, 0, 0, 0, 0) + _text('Arial', 1))
    head = _bof(0x0005) + _record(0x0042, struct.pack('<H', 1200)) + font + b''.join(xf(0xFFF5) for _ in range(16)) + xf(1)
    boundsheet_size = len(_record(0x0085, struct.pack('<IBB', 0, 0, 0) + _text(sheet_name, 1)))
    offset = len(head) + boundsheet_size + 4
    globals_ = head + _record(0x0085, struct.pack('<IBB', offset, 0, 0) + _text(sheet_name, 1)) + _record(0x000A)
    cells = []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value is None or value == '':
                continue
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                cells.append(_record(0x0203, struct.pack('<HHHd', r, c, 16, float(value))))
            else:
                cells.append(_record(0x0204, struct.pack('<HHH', r, c, 16) + _text(str(value), 2)))
    width = max((len(row) for row in rows), default=0)
    sheet = _bof(0x0010) + _record(0x0200, struct.pack('<IIHHH', 0, len(rows), 0, width, 0)) + b''.join(cells) + _record(0x000A)
    assert len(globals_) == offset
    return globals_ + sheet


def _entry(name, kind, *, child=NOSTREAM, start=ENDOFCHAIN, size=0):
    raw = name.encode('utf-16-le') + b'\0\0' if name else b''
    return (raw.ljust(64, b'\0') + struct.pack('<HBBIII', len(raw), kind, 1, NOSTREAM, NOSTREAM, child) +
            b'\0' * 16 + struct.pack('<I', 0) + b'\0' * 16 + struct.pack('<IQ', start, size))


def xls_bytes(rows, sheet_name='交割单') -> bytes:
    """``rows``: lists of str / int / float / Decimal; numbers become NUMBER cells."""
    stream = _workbook_stream(rows, sheet_name)
    stream += b'\0' * max(0, 4096 - len(stream))  # stay out of the mini stream
    size = len(stream)
    stream += b'\0' * (-len(stream) % 512)
    sectors = len(stream) // 512
    fat_sectors = 1
    while sectors + 1 + fat_sectors > 128 * fat_sectors:
        fat_sectors += 1
    fat = [i + 1 for i in range(sectors - 1)] + [ENDOFCHAIN, ENDOFCHAIN] + [FATSECT] * fat_sectors
    fat += [FREESECT] * (128 * fat_sectors - len(fat))
    header = (bytes.fromhex('D0CF11E0A1B11AE1') + b'\0' * 16 + struct.pack('<HHHHH', 0x3E, 3, 0xFFFE, 9, 6) + b'\0' * 6 +
              struct.pack('<IIIIIIIII', 0, fat_sectors, sectors, 0, 4096, ENDOFCHAIN, 0, ENDOFCHAIN, 0) +
              struct.pack('<109I', *([sectors + 1 + i for i in range(fat_sectors)] + [FREESECT] * (109 - fat_sectors))))
    directory = (_entry('Root Entry', 5, child=1) + _entry('Workbook', 2, start=0, size=size) +
                 _entry('', 0) + _entry('', 0))
    assert len(header) == 512 and len(directory) == 512
    return header + stream + directory + struct.pack(f'<{len(fat)}I', *fat)


# -- running an import -------------------------------------------------------------

def run(data: bytes, filename: str, account: str = ACCOUNT, statement=None):
    """Create a session; returns (session, view, {row number: row})."""
    from core.importers import session
    created = session.create(data, filename, account, statement)
    view = session.view(created)
    return created, view, {row['row']: row for row in view['rows']}


def patch(session_id: str, **body):
    from core.importers import session
    saved, analysis = session.patch(session_id, body)
    view = session.view(saved, analysis)
    return view, {row['row']: row for row in view['rows']}


def csv(header, *rows) -> bytes:
    return ('\n'.join([','.join(header)] + [','.join(str(c) for c in row) for row in rows]) + '\n').encode('utf-8')
