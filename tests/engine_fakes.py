"""Offline stand-ins for running the real engine scripts. Fictional data only.

    with price_server({'SHA:600001': 12.5}) as base:   # every other ticker fails
        portfolio = engine_portfolio(tmp_path / 'portfolio', base, positions=[...])
        env = engine_env(portfolio)
        subprocess.run([sys.executable, SCRIPTS / 'portfolio_snapshot.py', ...], env=env)

``price_server`` answers the Yahoo chart requests that ``portfolio_snapshot``
makes, and the Eastmoney rate requests it makes when Yahoo has no current
rate, on a loopback port; the snapshot reaches it through ``yahoo_base`` and
``eastmoney_url`` in the portfolio's ``config.json``. Nothing here touches
the network.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Dict, Iterable, Iterator, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'engine' / 'scripts'


def yahoo_symbol(ticker: str) -> str:
    """The symbol the snapshot asks for: Today's (``core.market.instruments``); ``FX:<symbol>`` for a rate."""
    from core.market.instruments import InstrumentError, yahoo_symbol as todays
    exchange, code = ticker.split(':')
    try:
        return (todays(ticker) if exchange != 'FX' else None) or code
    except InstrumentError:
        return code


# The exchange time ``price_server`` gives every quote unless told otherwise: 15:00 Shanghai on
# Tuesday 2026-11-17, the day most tests snapshot (a quote may not be from a later session).
QUOTE_TIME = 1794898800
CURRENCIES = {'HKG': 'HKD', 'NASDAQ': 'USD', 'NYSE': 'USD'}


EASTMONEY_PATH = '/api/qt/ulist.np/get'


def eastmoney_url(yahoo_base: str) -> str:
    """The Eastmoney endpoint ``price_server`` serves beside the Yahoo base URL it yields."""
    return yahoo_base.rsplit('/chart', 1)[0] + EASTMONEY_PATH


@contextmanager
def price_server(prices: Dict[str, float], currencies: Optional[Dict[str, Optional[str]]] = None,
                 times: Optional[Dict[str, Optional[int]]] = None,
                 eastmoney: Optional[Dict[str, Tuple[float, Optional[int]]]] = None) -> Iterator[str]:
    """Serve ``prices`` (by ticker) as Yahoo chart responses; yields the base URL.

    A ticker that is not listed gets HTTP 404, as for a failed download. Each
    quote carries ``currency`` (its exchange's: HKD, USD, else CNY) and
    ``regularMarketTime`` (``QUOTE_TIME``); ``currencies`` and ``times`` override
    them per ticker, ``None`` leaves the field out. ``eastmoney`` (secid ->
    ``(rate, Unix seconds or None)``, such as ``'133.USDCNH'``) are the rows
    Eastmoney's ``ulist.np`` answers at ``eastmoney_url(base)``; it knows no
    other (``rc`` 102), as by default.
    """
    def field(table, ticker, default):
        return (table or {}).get(ticker, default)

    by_symbol = {yahoo_symbol(ticker): (price, field(currencies, ticker, CURRENCIES.get(ticker.split(':')[0], 'CNY')),
                                        field(times, ticker, QUOTE_TIME))
                 for ticker, price in prices.items()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server naming)
            if urlsplit(self.path).path == EASTMONEY_PATH:
                self.answer(eastmoney_rows(parse_qs(urlsplit(self.path).query).get('secids', [''])[0]))
                return
            symbol = self.path.split('?')[0].rsplit('/', 1)[-1]
            if symbol not in by_symbol:
                self.send_error(404)
                return
            price, currency, moment = by_symbol[symbol]
            meta = {'regularMarketPrice': price}
            if currency is not None:
                meta['currency'] = currency
            if moment is not None:
                meta['regularMarketTime'] = moment
            self.answer({'chart': {'result': [{'meta': meta}]}})

        def answer(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def eastmoney_rows(secids):
        rows = []
        for secid in filter(None, secids.split(',')):
            if secid in (eastmoney or {}):
                rate, moment = eastmoney[secid]
                market, code = secid.split('.')
                rows.append({'f12': code, 'f13': int(market), 'f2': rate,
                             **({'f124': moment} if moment is not None else {})})
        return {'rc': 0, 'data': {'total': len(rows), 'diff': rows}} if rows else {'rc': 102, 'data': None}

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/chart'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def engine_portfolio(directory: Path, yahoo_base: str, *, day: str = '2026-11-16',
                     positions: Iterable[dict] = (), cash: float = 1000, cost_basis: float = 2000) -> Path:
    """A legacy-holdings portfolio priced only through ``yahoo_base`` (and the Eastmoney endpoint beside it);
    no exchange rates."""
    (directory / 'holdings').mkdir(parents=True)
    (directory / 'config.json').write_text(json.dumps({'fx_tickers': {}, 'yahoo_base': yahoo_base,
                                                       'eastmoney_url': eastmoney_url(yahoo_base)}))
    (directory / 'holdings' / f'{day}.json').write_text(json.dumps({'date': day, 'groups': {
        'Example': {'cash': cash, 'fund': 0, 'cost_basis': cost_basis, 'positions': list(positions)}}}))
    return directory


def position(ticker: str, quantity: float = 100, cost_price: float = 10, name: str = 'Example') -> dict:
    return {'name': name, 'ticker': ticker, 'quantity': quantity, 'cost_price': cost_price}


def engine_env(portfolio: Path, **extra: str) -> dict:
    """Environment for an engine child: this portfolio, no lock claim, loopback without a proxy."""
    env = {**os.environ, 'PORTFOLIO_DIR': str(portfolio), 'PYTHONWARNINGS': 'ignore',
           'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost', **extra}
    for name in ('PORTFOLIO_WRITE_LOCK_INHERITED', 'CONDA_ENV'):
        env.pop(name, None)
    return env


def engine_copy(directory: Path) -> Path:
    """A private copy of ``engine/scripts`` (the pipeline writes reports next to it).

    The copy finds the dashboard package, which ``ledger_bridge`` imports, through
    a symbolic link beside it. Returns the copied ``scripts`` directory.
    """
    scripts = directory / 'engine' / 'scripts'
    shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns('__pycache__'))
    (directory / 'dashboard').symlink_to(ROOT / 'dashboard', target_is_directory=True)
    return scripts
