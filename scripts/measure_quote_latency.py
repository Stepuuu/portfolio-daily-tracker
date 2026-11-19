#!/usr/bin/env python3
"""Measure how old live quotes are when they reach this machine.

Manual tool, not part of the test suite: it calls the real quote sources.

    python scripts/measure_quote_latency.py
    python scripts/measure_quote_latency.py --tickers SHA:600519 HKG:00700 --samples 10

For each source and ticker it prints the exchange's own timestamp, the time
the response arrived here, and the difference. Run it during a trading
session: outside one the difference is just the time since the close. The
figures include this machine's clock error, so check the clock first.

It also asks Eastmoney's single-quote endpoint for ``f86`` and compares it
with ``f124`` from the batch endpoint the application uses; both should be the
same exchange-side timestamp.

Measured on 2026-11-17 around 13:53 CST (mainland and Hong Kong in session,
New York closed), six samples per source:

    eastmoney  mainland shares and ETFs  0.4-3.5 s, median about 2 s
    eastmoney  Hong Kong                 0.6-3.2 s, median about 1.4 s
    eastmoney  US (closed)               timestamp fixed at 16:00 New York
    eastmoney  f86 and f124              identical for all eight tickers
    yahoo      mainland and Hong Kong    901-909 s (a 15-minute delayed feed)
    yahoo      USDCNY=X / HKDCNY=X       8-11 s
"""
import argparse
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dashboard'))

import httpx  # noqa: E402

from core.market.calendar import SHANGHAI, SessionClock  # noqa: E402
from core.market.http import open_client  # noqa: E402
from core.market.instruments import parse  # noqa: E402
from core.market.quotes import SourceError, derive_fx_status, derive_status  # noqa: E402
from core.market.sources_eastmoney import HEADERS, EastmoneySource  # noqa: E402
from core.market.sources_yahoo import YahooSource  # noqa: E402

DEFAULT_TICKERS = ['SHA:600519', 'SHE:000001', 'SHA:510300', 'IDX:000001',
                   'HKG:00700', 'IDX:HSI', 'NASDAQ:AAPL', 'NYSE:SPY']
SINGLE_URL = 'https://push2.eastmoney.com/api/qt/stock/get'


def clock_text(moment):
    return moment.astimezone(SHANGHAI).strftime('%m-%d %H:%M:%S') if moment else '-'


def sample(source, tickers, clock, rounds, interval):
    print(f'\n== {source.name}: {rounds} samples, {interval:g}s apart')
    delays = {ticker: [] for ticker in tickers}
    for index in range(rounds):
        started = time.monotonic()
        try:
            quotes = source.fetch(tickers)
        except SourceError as exc:
            print(f'  source unreachable: {exc}')
            return
        elapsed = time.monotonic() - started
        received = datetime.now(timezone.utc)
        for ticker in tickers:
            quote = quotes.get(ticker)
            if quote is None or quote.price is None:
                print(f'  {ticker:<14} unavailable: {quote.reason if quote else "no answer"}')
                continue
            status, _ = derive_status(quote, clock)
            delay = (received - quote.quote_time).total_seconds() if quote.quote_time else None
            if delay is not None:
                delays[ticker].append(delay)
            print(f'  {ticker:<14} price {str(quote.price):>10}  exchange {clock_text(quote.quote_time)}  '
                  f'received {clock_text(received)}  age {delay if delay is None else round(delay, 1)}s  '
                  f'status {status}  basis {quote.price_basis}')
        print(f'  -- request took {elapsed:.2f}s')
        if index + 1 < rounds:
            time.sleep(interval)
    print(f'  summary for {source.name} (seconds between exchange time and arrival):')
    for ticker, values in delays.items():
        if values:
            print(f'    {ticker:<14} min {min(values):8.1f}  median {statistics.median(values):8.1f}  '
                  f'max {max(values):8.1f}  market {parse(ticker).market} {clock.phase(parse(ticker).market)}')


def sample_fx(source, clock):
    print(f'\n== {source.name}: currency rates')
    try:
        rates = source.fetch_fx(['USD', 'HKD'])
    except SourceError as exc:
        print(f'  source unreachable: {exc}')
        return
    received = datetime.now(timezone.utc)
    for currency, rate in rates.items():
        status, _ = derive_fx_status(rate, clock.now())
        delay = round((received - rate.quote_time).total_seconds(), 1) if rate.quote_time else None
        print(f'  {currency}/CNY {rate.rate}  source {rate.source}  exchange {clock_text(rate.quote_time)}  '
              f'age {delay}s  status {status}  {rate.reason or ""}')


def compare_eastmoney_fields(tickers, use_env_proxy):
    print('\n== eastmoney: single-quote f86 against batch f124')
    source = EastmoneySource(use_env_proxy=use_env_proxy)
    try:
        batch = source.fetch(tickers)
    except SourceError as exc:
        print(f'  source unreachable: {exc}')
        return
    with open_client(timeout=httpx.Timeout(5.0), headers=HEADERS, use_env_proxy=use_env_proxy) as client:
        for ticker in tickers:
            secids = parse(ticker).eastmoney_secids
            data = None
            for secid in secids:
                try:
                    data = client.get(SINGLE_URL, params={'secid': secid, 'fields': 'f43,f60,f86', 'fltt': '2',
                                                          'invt': '2'}).json().get('data')
                except (httpx.HTTPError, ValueError) as exc:
                    print(f'  {ticker:<14} single-quote request failed: {type(exc).__name__}')
                if data:
                    break
            quote = batch.get(ticker)
            f124 = int(quote.quote_time.timestamp()) if quote is not None and quote.quote_time else None
            f86 = data.get('f86') if data else None
            verdict = 'n/a' if f86 is None or f124 is None else f'differ by {abs(int(f86) - f124)}s'
            print(f'  {ticker:<14} f124 {f124}  f86 {f86}  {verdict}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tickers', nargs='+', default=DEFAULT_TICKERS)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--interval', type=float, default=3.0)
    parser.add_argument('--eastmoney-proxy', action='store_true',
                        help='reach Eastmoney through the proxy in the environment instead of directly')
    parser.add_argument('--yahoo-proxy', default=None, help='proxy URL for Yahoo (default: environment)')
    parser.add_argument('--skip', nargs='*', default=[], choices=['eastmoney', 'yahoo'])
    args = parser.parse_args()
    clock = SessionClock()
    now = clock.now()
    print(f'local time {now.isoformat(timespec="seconds")}; sessions: '
          + ', '.join(f'{market} {clock.phase(market)}' for market in ('CN', 'HK', 'US')))
    if 'eastmoney' not in args.skip:
        eastmoney = EastmoneySource(use_env_proxy=args.eastmoney_proxy)
        sample(eastmoney, args.tickers, clock, args.samples, args.interval)
        sample_fx(eastmoney, clock)
        compare_eastmoney_fields(args.tickers, args.eastmoney_proxy)
    if 'yahoo' not in args.skip:
        yahoo = YahooSource(proxy=args.yahoo_proxy)
        sample(yahoo, args.tickers, clock, args.samples, args.interval)
        sample_fx(yahoo, clock)


if __name__ == '__main__':
    main()
