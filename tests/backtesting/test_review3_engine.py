"""Third review, part 2 (engine, broker and data): the findings that live in engine.py.

Matching order across symbols (engine.py:646), the unverified splice
(engine.py:314), the T+1 restriction on the end-of-data valuation
(engine.py:688) and today's bar in a DataFrame passed in (engine.py:246),
each with the reviewer's reproduction. Offline, hand-made frames.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from backtesting.broker.simulated import BrokerConfig
from backtesting.engine import BacktestEngine, MarketDataUnavailableError
from backtesting.strategies.base import Strategy

NO_FEES = BrokerConfig(slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0)
CHINA = timezone(timedelta(hours=8))


def bars(rows, start=None, days=None):
    """rows: price or (open, high, low, close) per bar, on business days from
    `start` or on the given `days`."""
    rows = [(r, r, r, r) if isinstance(r, (int, float)) else r for r in rows]
    index = (pd.DatetimeIndex([pd.Timestamp(day) for day in days]) if days is not None
             else pd.bdate_range(start, periods=len(rows)))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    df["volume"] = 1_000_000.0
    df["amount"] = df["close"] * 1_000_000.0
    df.index.name = "date"
    return df


class Idle(Strategy):
    def on_bar(self):
        pass


# ------------------------------------------------------------------ #
#  engine.py:646 - every symbol's opening fills come before any
#  symbol's fills inside the bar, whatever the order of the feeds
# ------------------------------------------------------------------ #

# CCC is the primary symbol and has no bar on Jan 7 (its market traded: it is
# suspended), so on_bar does not run that day but AAA and BBB are matched.
UNION_DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
FEEDS = {
    "AAA": bars([10.0, 10.0, (10.0, 11.0, 10.0, 10.0), 10.0], days=UNION_DAYS),
    "BBB": bars([10.0, 10.0, (10.0, 20.0, 10.0, 20.0), 20.0], days=UNION_DAYS),
    "CCC": bars([10.0, 10.0, 10.0], days=["2026-01-05", "2026-01-06", "2026-01-08"]),
}


def run_feeds(order, strategy):
    e = BacktestEngine(initial_cash=1_000.0, broker_config=NO_FEES, use_cache=False)
    for symbol in order:
        e.add_data(symbol, "", "", df=FEEDS[symbol].copy(), adjust="qfq", warmup=0, is_primary=symbol == "CCC")
    e.add_strategy(strategy)
    return e.run()


class SellAInsideTheBarAndBuyBAtTheOpen(Strategy):
    """Jan 5: buy 100 AAA (all the cash). Jan 6: a limit sale of AAA at 11 and a
    market buy of 100 BBB, both for Jan 7: the sale can only happen inside the
    bar (AAA opens at 10, trades up to 11), the buy at BBB's open."""

    def on_init(self):
        self.step = 0

    def on_bar(self):
        if self.step == 0:
            self.buy(symbol="AAA", quantity=100)
            self.step = 1
        elif self.step == 1 and self.get_position("AAA"):
            self.sell(symbol="AAA", quantity=100, price=11.0, order_type="limit")
            self.buy(symbol="BBB", quantity=100)
            self.step = 2


@pytest.mark.parametrize("order", list(itertools.permutations(["AAA", "BBB", "CCC"])), ids="-".join)
def test_cash_from_a_sale_inside_the_bar_never_pays_for_a_buy_at_that_open(order):
    # Reviewer's reproduction: with $1,000 in AAA, AAA's intraday $1,100 limit sale
    # funded BBB's purchase at that morning's open when AAA's feed came first (final
    # wealth $2,100); with the feeds reversed the buy failed ($1,100).
    result = run_feeds(order, SellAInsideTheBarAndBuyBAtTheOpen)

    fills = [(t.symbol, t.direction, str(t.timestamp.date()), round(t.price, 2)) for t in result.trades
             if t.reason != "end_mark"]
    assert fills == [("AAA", "buy", "2026-01-06", 10.0), ("AAA", "sell", "2026-01-07", 11.0)]
    (missed,) = [u for u in result.unfilled if u["symbol"] == "BBB"]
    assert missed["reason"] == "insufficient_cash" and missed["date"] == "2026-01-07"
    assert result.to_dict()["final_value"] == pytest.approx(1_100.0, abs=0.5)    # US sale levies only


class SellAAndBuyBAtTheOpen(Strategy):
    """Like above, but AAA is sold at the open (a market order sent first)."""

    def on_init(self):
        self.step = 0

    def on_bar(self):
        if self.step == 0:
            self.buy(symbol="AAA", quantity=100)
            self.step = 1
        elif self.step == 1 and self.get_position("AAA"):
            self.sell(symbol="AAA", quantity=100)
            self.buy(symbol="BBB", quantity=99)
            self.step = 2


@pytest.mark.parametrize("order", list(itertools.permutations(["AAA", "BBB", "CCC"])), ids="-".join)
def test_a_sale_at_the_open_sent_first_pays_for_a_buy_at_the_open_in_any_feed_order(order):
    # Both fill at Jan 7's open; the sale was sent first, so its proceeds are there
    # for the buy whichever symbol's data was added first.
    result = run_feeds(order, SellAAndBuyBAtTheOpen)

    fills = [(t.symbol, t.direction, str(t.timestamp.date())) for t in result.trades if t.reason != "end_mark"]
    assert fills == [("AAA", "buy", "2026-01-06"), ("AAA", "sell", "2026-01-07"), ("BBB", "buy", "2026-01-07")]
    assert result.to_dict()["final_value"] == pytest.approx(10.0 + 99 * 20.0, abs=0.5)


# ------------------------------------------------------------------ #
#  engine.py:688 - the end-of-data valuation of shares bought that day
# ------------------------------------------------------------------ #

class BuyOnTheSecondBar(Strategy):
    def on_init(self):
        self.count = 0

    def on_bar(self):
        self.count += 1
        if self.count == self.parameters.get("on", 2):
            self.buy(quantity=100)


@pytest.mark.parametrize("on,blocked", [(2, "t_plus_one"), (1, None)], ids=["bought_on_the_last_day", "earlier"])
def test_the_end_mark_names_the_t_plus_one_restriction(on, blocked):
    # Reviewer's reproduction: 100 A-shares bought on the final day are valued at
    # that close as if sold, with blocked=null although T+1 forbids selling them.
    e = BacktestEngine(initial_cash=10_000.0, broker_config=NO_FEES, use_cache=False)
    e.add_data("600000", "", "", df=bars([10.0, 10.0, 10.0], start="2024-01-02"), adjust="qfq", warmup=0)
    e.add_strategy(BuyOnTheSecondBar, on=on)
    result = e.run()

    (mark,) = result.end_mark["positions"]
    assert (mark["symbol"], mark["date"], mark["quantity"], mark["price"]) == ("600000", "2024-01-04", 100, 10.0)
    assert mark["blocked"] == blocked
    assert mark["blocked_quantity"] == (100 if blocked else 0)
    assert result.to_dict()["end_mark"]["positions"][0]["blocked"] == blocked
    item = next(c for c in result.caveats if c["code"] == "end_mark")
    if blocked:
        assert item["level"] == "warning" and "T+1" in item["text"] and "停牌" not in item["text"]
    else:
        assert item["level"] == "info"


# ------------------------------------------------------------------ #
#  engine.py:314 - old adjusted rows spliced to a new download
# ------------------------------------------------------------------ #

class Offline:
    def __init__(self, frame):
        self.frame = frame

    def get_daily(self, symbol, start_date, end_date, adjust="qfq"):
        return self.frame.loc[start_date:end_date].copy()


def spliced_engine(monkeypatch):
    """Cache: SHA:600000 qfq Jan 2-3 at 10 covering Jan 1-3. The provider now has
    only Jan 4-5, at 5 (another anchor, no shared date to compare)."""
    e = BacktestEngine(initial_cash=2_000.0, broker_config=NO_FEES)
    e._store.save_daily("SHA:600000", bars([10.0, 10.0], start="2024-01-02"), "qfq")
    e._store.record_coverage("SHA:600000", "qfq", "2024-01-01", "2024-01-03")
    monkeypatch.setattr(e._loader, "get_daily", Offline(bars([5.0, 5.0], start="2024-01-04")).get_daily)
    return e


def test_an_unverified_splice_is_refused_without_allow_partial(monkeypatch):
    # Reviewer's reproduction: old closes [10, 10] plus downloaded [5, 5] ran with
    # allow_partial=false (offline=false, complete=false): $2,000 became $1,499.99.
    e = spliced_engine(monkeypatch)
    with pytest.raises(MarketDataUnavailableError) as raised:
        e.add_data("600000", "2024-01-03", "2024-01-05", warmup=0)
    assert "2024-01-03" in str(raised.value) and "锚点" in str(raised.value)
    assert "600000" not in e._data_feeds


def test_an_allowed_unverified_splice_names_the_seam(monkeypatch):
    e = spliced_engine(monkeypatch)
    e.add_data("600000", "2024-01-03", "2024-01-05", warmup=0, allow_partial=True)
    e.add_strategy(Idle)
    result = e.run()

    coverage = result.coverage["600000"]
    assert coverage.complete is False
    assert coverage.unverified == ["2024-01-03"] and coverage.splice_seams == ["2024-01-04"]
    item = next(c for c in result.caveats if c["code"] == "unverified_splice")
    assert item["level"] == "warning" and "2024-01-04" in item["text"] and "2024-01-03" in item["text"]
    assert result.to_dict()["coverage"]["600000"]["splice_seams"] == ["2024-01-04"]


# ------------------------------------------------------------------ #
#  engine.py:246 - a DataFrame passed in follows the provisional-bar rule
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("stamp", ["2024-01-05", "2024-01-05 10:00"])
def test_todays_bar_in_a_dataframe_is_not_simulated_before_the_close(stamp):
    # Reviewer's reproduction: a frame passed in directly included today's 10:00 bar.
    morning = datetime(2024, 1, 5, 10, 0, tzinfo=CHINA)
    frame = bars([10.0, 10.0, 10.0, 20.0], days=["2024-01-02", "2024-01-03", "2024-01-04", stamp])
    e = BacktestEngine(initial_cash=100_000.0, broker_config=NO_FEES, use_cache=False, now=lambda: morning)
    e.add_data("600000", "", "", df=frame, adjust="qfq", warmup=0)
    e.add_strategy(Idle)
    result = e.run()

    assert str(e._data_feeds["600000"]._df.index[-1].date()) == "2024-01-04"
    coverage = result.coverage["600000"]
    assert coverage.provisional_last_bar is True and coverage.last == "2024-01-04"
    assert any(c["code"] == "provisional_bar" for c in result.caveats)


def test_a_dataframe_after_the_close_keeps_todays_bar():
    evening = datetime(2024, 1, 5, 20, 0, tzinfo=CHINA)
    frame = bars([10.0, 10.0, 10.0, 20.0], start="2024-01-02")
    e = BacktestEngine(initial_cash=100_000.0, broker_config=NO_FEES, use_cache=False, now=lambda: evening)
    e.add_data("600000", "", "", df=frame, adjust="qfq", warmup=0)
    assert str(e._data_feeds["600000"]._df.index[-1].date()) == "2024-01-05"
    assert e.coverage["600000"].provisional_last_bar is False


# ------------------------------------------------------------------ #
#  complete=false never comes with a silent run
# ------------------------------------------------------------------ #

def windowed_engine(monkeypatch, days):
    """SHA:600000 qfq cached and covered from 2024-01-10 to 2024-03-29; nothing earlier.
    The provider is unreachable."""
    e = BacktestEngine(initial_cash=100_000.0, broker_config=NO_FEES)
    e._store.save_daily("SHA:600000", bars([10.0] * len(days), days=days), "qfq")
    e._store.record_coverage("SHA:600000", "qfq", str(days[0].date()), "2024-03-29")

    def offline(*args, **kwargs):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(e._loader, "get_daily", offline)
    return e


@pytest.mark.parametrize("warmup,complete", [(10, True), (40, False)], ids=["warmup_held", "warmup_short"])
def test_a_window_served_from_its_interval_is_complete_only_with_its_whole_warmup(monkeypatch, warmup, complete):
    # The window Feb 1 - Mar 29 lies in one recorded interval that starts Jan 10, later
    # than the warm-up's calendar reach: the warm-up is taken from that interval (16
    # bars before Feb 1), without a download.
    days = pd.bdate_range("2024-01-10", "2024-03-29")
    e = windowed_engine(monkeypatch, days)
    e.add_data("600000", "2024-02-01", "2024-03-29", warmup=warmup)
    e.add_strategy(Idle)
    result = e.run()

    coverage = result.coverage["600000"]
    assert coverage.offline is False and coverage.complete is complete
    codes = {c["code"] for c in result.caveats}
    assert ("warmup_short" in codes) is (not complete)


# ------------------------------------------------------------------ #
#  The frame as loaded, frozen before the strategy runs
# ------------------------------------------------------------------ #

class ScribblesOnItsFrame(Strategy):
    def on_bar(self):
        self.data.add_factor("volume", self.data._df["volume"] * 0)

    def on_stop(self):
        self.data.add_factor("close", self.data._df["close"] * 2)


def test_the_engine_keeps_the_frame_it_handed_over():
    e = BacktestEngine(initial_cash=100_000.0, broker_config=NO_FEES, use_cache=False)
    e.add_data("600000", "", "", df=bars([10.0, 11.0, 12.0], start="2024-01-02"), adjust="qfq", warmup=0)
    handed = e.loaded_frame("600000")
    assert handed.equals(e._data_feeds["600000"]._df) and "ma_5" in handed.columns
    e.add_strategy(ScribblesOnItsFrame)
    e.run()

    after = e.loaded_frame("600000")
    assert after.equals(handed)
    assert after["volume"].tolist() == [1_000_000.0] * 3 and after["close"].tolist() == [10.0, 11.0, 12.0]
    after.loc[after.index[0], "close"] = -1.0                    # a copy each time
    assert e.loaded_frame("600000")["close"].iloc[0] == 10.0
    with pytest.raises(KeyError):
        e.loaded_frame("NOPE")
