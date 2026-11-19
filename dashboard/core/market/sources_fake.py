"""A scripted quote source for the demo and for tests. It never uses the network.

It re-reads ``PORTFOLIO_DIR/fake_quotes.json`` on every fetch, so a test or
a demo operator can change prices, timestamps and outages while the
application runs:

    {
      "now": "2026-09-01T10:00:00+08:00",        optional: pins the clock
      "outage": "数据源维护",                     optional: every quote fails
      "quotes": {
        "SHA:DEMO": {
          "name": "示例宽基", "price": "61.20", "prev_close": "60.80",
          "open": "60.9", "high": "61.5", "low": "60.7", "volume": "120000",
          "quote_time": "2026-09-01T09:59:57+08:00"   exchange time, or
          "age_seconds": 3                            ... relative to now, or
                                                      neither: time unknown
        },
        "NASDAQ:DEMO": {"unavailable": "停牌"}        a scripted failure
      },
      "fx": {"USD": {"rate": "7.05", "age_seconds": 5}, "HKD": {"rate": "0.90"}}
    }

A ticker or currency that is not in the file is unavailable. So is everything
when the file is missing or malformed: this source never makes a price up.

A scripted price is the regular session's price (``LiveQuote.regular_session``),
as the exchange's own would be; ``"extended_hours": true`` in an entry scripts
a New York price from extended-hours trading instead.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Union

from .instruments import InstrumentError, parse, quote_key
from .quotes import FxRate, LiveQuote, build_fx, build_quote, to_decimal, unavailable, utc_now

FILE_NAME = 'fake_quotes.json'
MAX_BYTES = 2_000_000


def _moment(value) -> Optional[datetime]:
    """An ISO-8601 time with an offset, or ``None``."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class FakeQuoteSource:
    """``QuoteSource`` and ``FxSource`` driven by a JSON file."""

    name = 'fake'

    def __init__(self, directory: Union[str, Path, Callable[[], Path]],
                 now_fn: Callable[[], datetime] = utc_now):
        self._directory = directory
        self._now_fn = now_fn

    @property
    def path(self) -> Path:
        directory = self._directory() if callable(self._directory) else self._directory
        return Path(directory) / FILE_NAME

    def _script(self):
        """``(script, problem)``: the parsed file, or why it cannot be used."""
        path = self.path
        try:
            if path.stat().st_size > MAX_BYTES:
                return {}, f'{FILE_NAME} 过大'
            script = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}, f'未找到 {FILE_NAME}'
        except (OSError, ValueError):
            return {}, f'{FILE_NAME} 无法读取'
        if not isinstance(script, dict):
            return {}, f'{FILE_NAME} 格式不正确'
        outage = script.get('outage')
        if outage:
            return script, outage if isinstance(outage, str) else '模拟数据源中断'
        return script, None

    def now(self) -> datetime:
        """The scripted ``now`` when the file pins one, otherwise real time."""
        script, _ = self._script()
        return _moment(script.get('now')) or self._now_fn()

    def names(self) -> Dict[str, str]:
        """``{ticker: name}`` of the scripted quotes that have a name (for the search in fake-quote mode)."""
        script, _ = self._script()
        quotes = script.get('quotes') if isinstance(script.get('quotes'), dict) else {}
        found: Dict[str, str] = {}
        for ticker, entry in quotes.items():
            if not isinstance(ticker, str) or not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
                continue
            try:
                key = parse(ticker.strip().upper()).ticker
            except InstrumentError:
                continue
            if entry['name'].strip():
                found.setdefault(key, entry['name'].strip()[:40])
        return found

    def _time(self, entry: dict, now: datetime) -> Optional[datetime]:
        if 'quote_time' in entry:
            return _moment(entry['quote_time'])
        age = to_decimal(entry.get('age_seconds'))
        return now - timedelta(seconds=float(age)) if age is not None else None

    def fetch(self, tickers: Sequence[str]) -> Dict[str, LiveQuote]:
        script, problem = self._script()
        now = _moment(script.get('now')) or self._now_fn()
        quotes = script.get('quotes') if isinstance(script.get('quotes'), dict) else {}
        # A Hong Kong code may be scripted as HKG:700 and asked for as HKG:00700.
        by_key = {}
        for key in quotes:
            by_key.setdefault(quote_key(key.strip().upper()) if isinstance(key, str) else key, key)
        results: Dict[str, LiveQuote] = {}
        for ticker in tickers:
            try:
                instrument = parse(ticker)
            except InstrumentError as exc:
                results[ticker] = unavailable(ticker, self.name, now, f'无法识别的代码：{exc}')
                continue
            currency = instrument.currency
            entry = quotes.get(ticker, quotes.get(by_key.get(instrument.quote_key)))
            if problem or not isinstance(entry, dict):
                results[ticker] = unavailable(ticker, self.name, now, problem or f'{FILE_NAME} 中没有该代码',
                                              currency=currency)
            elif entry.get('unavailable'):
                reason = entry['unavailable'] if isinstance(entry['unavailable'], str) else '模拟行情不可用'
                results[ticker] = unavailable(ticker, self.name, now, reason, currency=currency,
                                              name=entry.get('name'), prev_close=entry.get('prev_close'))
            else:
                results[ticker] = build_quote(
                    ticker, self.name, now, price=entry.get('price'), prev_close=entry.get('prev_close'),
                    name=entry.get('name'), open=entry.get('open'), high=entry.get('high'), low=entry.get('low'),
                    volume=entry.get('volume'), currency=currency, quote_time=self._time(entry, now),
                    regular_session=entry.get('extended_hours') is not True)
        return results

    def fetch_fx(self, currencies: Sequence[str]) -> Dict[str, FxRate]:
        script, problem = self._script()
        now = _moment(script.get('now')) or self._now_fn()
        rates = script.get('fx') if isinstance(script.get('fx'), dict) else {}
        results: Dict[str, FxRate] = {}
        for currency in currencies:
            entry = rates.get(currency)
            if problem or not isinstance(entry, dict):
                results[currency] = build_fx(currency, self.name, now,
                                             reason=problem or f'{FILE_NAME} 中没有该币种')
            elif entry.get('unavailable'):
                reason = entry['unavailable'] if isinstance(entry['unavailable'], str) else '模拟汇率不可用'
                results[currency] = build_fx(currency, self.name, now, reason=reason)
            else:
                results[currency] = build_fx(currency, self.name, now, rate=entry.get('rate'),
                                             quote_time=self._time(entry, now))
        return results
