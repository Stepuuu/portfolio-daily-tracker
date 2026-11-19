"""The snapshot asks Yahoo for the symbol Today's quote sources ask for: one table, ``Instrument.yahoo_symbol``.

The reviewer's case: HKG:00700 was requested as 00700.HK (Yahoo knows 0700.HK), so every retry failed and
the day got no history row; NYSE:BRK.B was requested as BRK.B (Yahoo knows BRK-B). Tencent, asked for what
Yahoo did not price, is asked for Today's Tencent symbol (``Instrument.tencent_symbol``). Stubbed fetches only.
"""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import portfolio_snapshot as snapshot
from tests.engine_fakes import position

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-17'
UTC = timezone.utc
NOW = datetime(2026, 11, 17, 8, 30, tzinfo=UTC)   # 16:30 Shanghai: every market of the day has closed

# The shared vectors: what Today's sources ask Yahoo and Tencent for, and so what the snapshot must ask for.
VECTORS = [
    ('SHA:600519', '600519.SS', 'sh600519'),
    ('SHE:300750', '300750.SZ', 'sz300750'),
    ('HKG:00700', '0700.HK', 'hk00700'),
    ('HKG:700', '0700.HK', 'hk00700'),
    ('HKG:09988', '9988.HK', 'hk09988'),
    ('HKG:00005', '0005.HK', 'hk00005'),
    ('NASDAQ:AAPL', 'AAPL', 'usAAPL'),
    ('NYSE:BRK.B', 'BRK-B', 'usBRK.B'),
]


class Response:
    def __init__(self, meta):
        self.meta = meta

    def raise_for_status(self):
        pass

    def json(self):
        return {'chart': {'result': [{'meta': self.meta}]}}


def asked_symbols(monkeypatch, tickers, config=None):
    asked = []

    def get(url, **options):
        asked.append(f"tencent:{options['params']['q']}" if url == snapshot.TENCENT_URL else url.rsplit('/', 1)[-1])
        raise OSError('this test only records what is asked for')
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: NOW)
    holdings = {'groups': {'Example': {'positions': [position(ticker) for ticker in tickers]}}}
    snapshot.fetch_prices(holdings, config or {}, DAY)
    return asked


@pytest.mark.parametrize('ticker, symbol, tencent', VECTORS)
def test_the_snapshot_asks_for_the_symbol_todays_sources_ask_for(monkeypatch, ticker, symbol, tencent):
    from core.market.instruments import tencent_symbol, yahoo_symbol
    assert (yahoo_symbol(ticker), tencent_symbol(ticker)) == (symbol, tencent)      # the one table
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    assert asked_symbols(monkeypatch, [ticker]) == [symbol, f'tencent:{tencent}']


def test_a_configured_symbol_still_wins(monkeypatch):
    monkeypatch.delenv('TRACKER_QUOTE_SOURCE', raising=False)
    assert asked_symbols(monkeypatch, ['HKG:00700'], {'ticker_map': {'HKG:00700': '0700.HK'}}) == [
        '0700.HK', 'tencent:hk00700']


def test_a_hong_kong_close_is_booked_under_its_yahoo_symbol(monkeypatch):
    def get(url, **options):
        assert url.endswith('/0700.HK'), url
        return Response({'regularMarketPrice': 375.2, 'currency': 'HKD',
                         'regularMarketTime': int(datetime(2026, 11, 17, 8, 8, 2, tzinfo=UTC).timestamp())})
    monkeypatch.setattr(snapshot.requests, 'get', get)
    monkeypatch.setattr(snapshot, '_now', lambda: NOW)
    holdings = {'groups': {'Example': {'positions': [position('HKG:00700')]}}}
    assert snapshot.fetch_prices(holdings, {}, DAY)[0] == {'HKG:00700': 375.2}


def test_the_engine_image_has_the_symbol_table_the_engine_uses(tmp_path):
    """Lay out the files engine/Dockerfile copies and ask the engine there for HKG:00700 and BRK.B."""
    app = tmp_path / 'image'
    for line in (ROOT / 'engine' / 'Dockerfile').read_text().splitlines():
        words = line.split()
        if not words or words[0] != 'COPY':
            continue
        source, target = ROOT / words[1], app / words[2]
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    probe = (
        'import sys; sys.path.insert(0, sys.argv[1])\n'
        'import portfolio_snapshot as s\n'
        'asked = []\n'
        'def get(url, **options):\n'
        '    asked.append(options["params"]["q"] if url == s.TENCENT_URL else url.rsplit("/", 1)[-1])\n'
        '    raise OSError("offline")\n'
        's.requests.get = get\n'
        's.fetch_prices({"groups": {"E": {"positions": [\n'
        '    {"name": "E", "ticker": "HKG:00700", "quantity": 1, "cost_price": 1},\n'
        '    {"name": "E", "ticker": "NYSE:BRK.B", "quantity": 1, "cost_price": 1}]}}}, {}, "2026-11-17")\n'
        'print(" ".join(sorted(asked)))\n'
    )
    result = subprocess.run([sys.executable, '-I', '-c', probe, str(app / 'scripts')], cwd=app,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['0700.HK', 'BRK-B', 'hk00700,usBRK.B']
