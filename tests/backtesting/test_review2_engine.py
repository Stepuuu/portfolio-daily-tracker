"""Second engine review: reproductions of the findings that live in engine.py.

Findings 1, 4, 6, 12 and 13 are the engine findings, each with the
reviewer's reproduction, plus the siblings found next to 1 and 4 in the
engine's day loop; the last tests are the engine wiring that findings 11,
14 and 15 need (their broker/analyzer/data parts are tested in
test_review2_*.py). The service review's engine findings (today's bar, one
stored series per load) are in test_review2_engine_data.py.
"""
import pandas as pd
import pytest

from backtesting.broker.fees import fees_assumption_text
from backtesting.broker.simulated import BrokerConfig
from backtesting.engine import BacktestEngine
from backtesting.strategies.base import Strategy

NO_FEES = BrokerConfig(slippage_pct=0.0, commission_buy=0.0, commission_sell=0.0, min_commission=0.0)


def bars(rows, start=None, days=None):
    """rows: price or (open, high, low, close) per bar, on business days from
    `start` or on the given `days`."""
    rows = [(r, r, r, r) if isinstance(r, (int, float)) else r for r in rows]
    index = pd.DatetimeIndex(pd.to_datetime(days)) if days is not None else pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index)
    df["volume"] = 1_000_000.0
    df["amount"] = df["close"] * 1_000_000.0
    df.index.name = "date"
    return df


def engine(cash=1_000.0):
    return BacktestEngine(initial_cash=cash, broker_config=NO_FEES, use_cache=False)


# ------------------------------------------------------------------ #
#  Finding 1: an order placed in a fill callback must not fill on the
#  same bar of a symbol processed later (look-ahead).
# ------------------------------------------------------------------ #

class BuyBOnAFill(Strategy):
    def on_init(self):
        self.sent = False
        self.seen_close = None

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="AAA", quantity=10)
            self.sent = True

    def on_trade(self, trade):
        if trade.symbol == "AAA" and trade.direction == "buy":
            self.seen_close = self.get_data("BBB").close()      # Jan 6's close, 20
            self.buy(symbol="BBB", quantity=10)


def test_fill_callback_cannot_trade_another_symbol_at_its_same_bar_open():
    # Reviewer's reproduction: AAA's buy fills on Jan 6; its on_trade sees BBB's
    # Jan 6 O=10/C=20 and buys 10, which filled at that morning's 10 (final 1,100).
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0] * 4, start="2026-01-05"), adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0, (10.0, 20.0, 10.0, 20.0), 20.0, 20.0], start="2026-01-05"),
               adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(BuyBOnAFill)
    result = e.run()

    bbb = [(str(t.timestamp.date()), t.price) for t in result.trades if t.symbol == "BBB" and t.direction == "buy"]
    assert bbb == [("2026-01-07", 20.0)]
    assert result.to_dict()["final_value"] == pytest.approx(1_000.0)


# ------------------------------------------------------------------ #
#  Finding 4: auxiliary symbols trade on days the primary has no bar
# ------------------------------------------------------------------ #

class BuyBWithAStop(Strategy):
    def on_init(self):
        self.sent = False

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="BBB", quantity=10)
            self.set_stop(9.0, symbol="BBB")
            self.sent = True


def test_auxiliary_stop_fires_on_a_day_the_primary_symbol_has_no_bar():
    # Reviewer's reproduction: AAA trades Jan 5/6/8/9, BBB also on Jan 7, when
    # it falls to 8 and the 9 stop must sell (final 990, not 1,000).
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0] * 4, days=["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"]),
               adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0, 10.0, (10.0, 10.0, 8.0, 8.5), 10.0, 10.0], start="2026-01-05"),
               adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(BuyBWithAStop)
    result = e.run()

    exits = [(str(t.timestamp.date()), t.price, t.reason) for t in result.trades
             if t.symbol == "BBB" and t.direction == "sell" and t.reason != "end_mark"]
    assert exits == [("2026-01-07", 9.0, "stop")]
    assert result.to_dict()["final_value"] == pytest.approx(990.0)


# ------------------------------------------------------------------ #
#  Siblings of findings 1 and 4 in the engine's day loop
# ------------------------------------------------------------------ #

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


class StopBFromAFill(Strategy):
    """Buys AAA and BBB; when AAA fills, reads BBB's bar of that day and sets a stop
    just above its low."""

    def on_init(self):
        self.sent = False

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="AAA", quantity=10)
            self.buy(symbol="BBB", quantity=10)
            self.sent = True

    def on_trade(self, trade):
        if trade.symbol == "AAA" and trade.direction == "buy":
            self.set_stop(self.get_data("BBB").low() + 0.5, symbol="BBB")


def test_a_stop_set_in_a_fill_callback_never_triggers_on_that_bar():
    # BBB on Jan 6: O=10 L=8 C=9. A stop at 8.5, chosen after seeing that low, used to
    # be evaluated on the same bar (AAA is matched first) and sold at 8.5.
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0] * 4, start="2026-01-05"), adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0, (10.0, 10.0, 8.0, 9.0), 9.0, 9.0], start="2026-01-05"),
               adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(StopBFromAFill)
    result = e.run()

    assert [(t.symbol, t.reason) for t in result.trades if t.direction == "sell" and t.reason != "end_mark"] == []
    # Cash 1,000 - 100 - 100, AAA 10 x 10, BBB 10 x 9 at the end.
    assert result.to_dict()["final_value"] == pytest.approx(990.0)


class CancelBFromAFill(Strategy):
    def on_init(self):
        self.sent = False
        self.cancelled = None

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="AAA", quantity=10)
            self.order_b = self.buy(symbol="BBB", quantity=10)
            self.sent = True

    def on_trade(self, trade):
        if trade.symbol == "AAA" and trade.direction == "buy" and self.get_data("BBB").close() < 10:
            self.cancelled = self.cancel_order(self.order_b)      # too late: it filled at the open


def test_a_fill_callback_cannot_cancel_another_symbols_order_with_that_bars_close():
    # BBB opens at 10 and closes at 5 on Jan 6. Its buy from Jan 5 fills at the open,
    # before any callback of Jan 6 runs, whatever the order the symbols were added in.
    for order in (["AAA", "BBB"], ["BBB", "AAA"]):
        e = engine()
        frames = {"AAA": bars([10.0] * 4, start="2026-01-05"),
                  "BBB": bars([10.0, (10.0, 10.0, 5.0, 5.0), 5.0, 5.0], start="2026-01-05")}
        for sym in order:
            e.add_data(sym, "", "", df=frames[sym], adjust="qfq", warmup=0, is_primary=sym == "AAA")
        e.add_strategy(CancelBFromAFill)
        result = e.run()

        assert result._strategy.cancelled is False
        assert [(str(t.timestamp.date()), t.price) for t in result.trades
                if t.symbol == "BBB" and t.direction == "buy"] == [("2026-01-06", 10.0)]


class BuyCOnAFill(Strategy):
    def on_init(self):
        self.sent = False

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="AAA", quantity=10)
            self.sent = True

    def on_trade(self, trade):
        if trade.symbol == "AAA" and trade.direction == "buy":
            self.buy(symbol="CCC", quantity=10)


def test_an_order_sent_on_a_day_its_symbol_has_no_bar_is_not_aged_that_day():
    # CCC has no bar on Jan 6 (suspended). A buy sent from Jan 6's fill callback used
    # to be counted as one suspended bar on Jan 6 and expired before CCC traded again.
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0] * 4, start="2026-01-05"), adjust="qfq", warmup=0)
    e.add_data("CCC", "", "", df=bars([10.0, 12.0, 12.0], days=["2026-01-05", "2026-01-07", "2026-01-08"]),
               adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(BuyCOnAFill)
    result = e.run()

    assert [(str(t.timestamp.date()), t.price) for t in result.trades
            if t.symbol == "CCC" and t.direction == "buy"] == [("2026-01-07", 12.0)]
    assert result.unfilled == []


class WatchTheValue(Strategy):
    def on_init(self):
        self.sent = False
        self.value_at_fill = None

    def on_bar(self):
        if not self.sent:
            self.buy(symbol="BBB", quantity=10)
            self.sent = True

    def on_trade(self, trade):
        if trade.direction == "buy":
            self.value_at_fill = self.portfolio_value


def test_fill_callbacks_see_the_day_valued_at_its_close():
    # Callbacks run at the close of the fill day: BBB filled at the 10 open and closed
    # at 12, so the portfolio is 900 cash + 10 x 12.
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0] * 3, start="2026-01-05"), adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0, (10.0, 12.0, 10.0, 12.0), 12.0], start="2026-01-05"),
               adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(WatchTheValue)
    assert e.run()._strategy.value_at_fill == pytest.approx(1_020.0)


class Recorder(Strategy):
    """Buys AAA once and records the days on_bar ran."""

    def on_init(self):
        self.days = []

    def on_bar(self):
        if not self.days:
            self.buy(symbol="AAA", quantity=10)
        self.days.append(str(self.data.current_bar.date.date()))


def test_a_day_without_a_primary_bar_is_valued_and_labelled_but_not_an_on_bar_day():
    # AAA (primary) has no bar on Jan 7; BBB does. The day is in the equity curve, the
    # AAA position is valued at its Jan 6 close and labelled, and on_bar does not run.
    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0, 11.0, 13.0], days=["2026-01-05", "2026-01-06", "2026-01-08"]),
               adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0] * 4, days=DAYS), adjust="qfq", warmup=0, is_primary=False)
    e.add_strategy(Recorder)
    result = e.run()

    assert result._strategy.days == ["2026-01-05", "2026-01-06", "2026-01-08"]
    equity = result.equity_df
    assert [str(d.date()) for d in equity.index] == DAYS
    # Bought 10 AAA at 11 on Jan 6; Jan 7 is valued at Jan 6's close (11), Jan 8 at 13.
    assert equity.loc["2026-01-07", "total_value"] == pytest.approx(1_000.0)
    assert equity.loc["2026-01-08", "total_value"] == pytest.approx(1_020.0)
    assert result.stale_valuations == [{"symbol": "AAA", "days": 1, "first": "2026-01-07", "last": "2026-01-07"}]
    assert result.to_dict()["stale_valuations"] == result.stale_valuations
    stale = next(c for c in result.caveats if c["code"] == "stale_price")
    assert "AAA" in stale["text"] and "2026-01-07" in stale["text"]


def test_another_markets_trading_day_does_not_age_orders():
    # SHA:600000 has no bar on Jan 7 while AAPL trades: a closed market, not a
    # suspension. The buy sent on Jan 6 waits and fills at Jan 8's open.
    e = engine(100_000)
    # (+2% on Jan 8: no price-limit or ST-pattern board blocks the buy)
    e.add_data("SHA:600000", "", "", df=bars([10.0, 10.0, 10.2], days=["2026-01-05", "2026-01-06", "2026-01-08"]),
               adjust="qfq", warmup=0)
    e.add_data("AAPL", "", "", df=bars([100.0] * 4, days=DAYS), adjust="qfq", warmup=0, is_primary=False)

    class BuyOnTheSecondDay(Strategy):
        def on_init(self):
            self.count = 0

        def on_bar(self):
            self.count += 1
            if self.count == 2:
                self.buy(quantity=100)

    e.add_strategy(BuyOnTheSecondDay)
    result = e.run()

    assert [(str(t.timestamp.date()), t.price) for t in result.trades if t.direction == "buy"] == [("2026-01-08", 10.2)]
    assert result.unfilled == []


def test_a_symbol_whose_data_ends_early_is_end_marked_at_its_own_last_close():
    # AAA (primary) stops after Jan 7, BBB trades on Jan 8 too: the run lasts through
    # Jan 8, BBB is valued at Jan 8's close, AAA's end mark is its Jan 7 close and is
    # flagged as not sellable on the last day.
    class BuyBoth(Strategy):
        def on_init(self):
            self.sent = False

        def on_bar(self):
            if not self.sent:
                self.buy(symbol="AAA", quantity=10)
                self.buy(symbol="BBB", quantity=10)
                self.sent = True

    e = engine()
    e.add_data("AAA", "", "", df=bars([10.0, 10.0, 12.0], days=DAYS[:3]), adjust="qfq", warmup=0)
    e.add_data("BBB", "", "", df=bars([10.0, 10.0, 10.0, 15.0], days=DAYS), adjust="qfq", warmup=0,
               is_primary=False)
    e.add_strategy(BuyBoth)
    result = e.run()

    marks = {mark["symbol"]: mark for mark in result.end_mark["positions"]}
    assert (marks["AAA"]["date"], marks["AAA"]["price"], marks["AAA"]["blocked"]) == ("2026-01-07", 12.0, "suspended")
    assert (marks["BBB"]["date"], marks["BBB"]["price"], marks["BBB"]["blocked"]) == ("2026-01-08", 15.0, None)
    # Cash 1,000 - 100 - 100 = 800, AAA 10 x 12, BBB 10 x 15.
    assert result.to_dict()["final_value"] == pytest.approx(1_070.0)
    assert result.period["last_date"] == "2026-01-08"


def test_a_market_closed_on_the_last_day_is_not_a_suspension_at_the_end():
    # SHA:600000's last bar is Jan 6; only AAPL trades on Jan 7 (the A-share market is
    # closed). The end mark at Jan 6's close is a normal valuation, not a blocked sale.
    # (The buy fills on Jan 5, so T+1 does not apply on Jan 6: test_review3_engine.)
    e = engine(100_000)
    e.add_data("SHA:600000", "", "", df=bars([10.0] * 3, days=["2026-01-02"] + DAYS[:2]), adjust="qfq", warmup=0)
    e.add_data("AAPL", "", "", df=bars([100.0] * 4, days=["2026-01-02"] + DAYS[:3]), adjust="qfq", warmup=0,
               is_primary=False)

    class BuyFirst(Strategy):
        def on_init(self):
            self.sent = False

        def on_bar(self):
            if not self.sent:
                self.buy(quantity=100)
                self.sent = True

    e.add_strategy(BuyFirst)
    result = e.run()

    (mark,) = result.end_mark["positions"]
    assert (mark["symbol"], mark["date"], mark["blocked"]) == ("SHA:600000", "2026-01-06", None)
    assert next(c for c in result.caveats if c["code"] == "end_mark")["level"] == "info"


# ------------------------------------------------------------------ #
#  Finding 6: warm-up bars must come from the same adjustment anchor
# ------------------------------------------------------------------ #

class Idle(Strategy):
    def on_bar(self):
        pass


def seamed_engine():
    """Jan-Jun qfq at 10 and Jul-Sep at 5 cached under 600000 as two separate
    coverage intervals (two downloads, anchors never compared)."""
    e = BacktestEngine(initial_cash=100_000, broker_config=NO_FEES)
    first = bars([10.0] * len(pd.bdate_range("2024-01-01", "2024-06-28")), start="2024-01-01")
    second = bars([5.0] * len(pd.bdate_range("2024-07-01", "2024-09-30")), start="2024-07-01")
    e._store.save_daily("600000", first, "qfq")
    e._store.record_download("600000", "qfq", "2024-01-01", "2024-06-30", fetched_at="2024-07-01T09:00:00")
    e._store.save_daily("600000", second, "qfq")
    e._store.record_download("600000", "qfq", "2024-07-01", "2024-09-30", fetched_at="2024-10-08T09:00:00")
    assert len(e._store.get_coverage("600000", "qfq")) == 2
    return e


def test_warmup_bars_are_not_taken_across_an_adjustment_seam(monkeypatch):
    # Reviewer's reproduction: Jan-Jun qfq at 10 and Jul-Sep at 5 are cached
    # under separate coverage; a Jul-Sep run with warmup=60 took its warm-up
    # from the old anchor (first-day return -50%, MA60 9.916667, no warning).
    e = seamed_engine()
    coherent = bars([5.0] * len(pd.bdate_range("2023-01-02", "2024-09-30")), start="2023-01-02")
    monkeypatch.setattr(e._loader, "get_daily", lambda symbol, start, end, adjust="qfq": coherent.loc[start:end].copy())

    e.add_data("600000", "2024-07-01", "2024-09-30", adjust="qfq", warmup=60)

    feed = e._data_feeds["600000"]
    first_day = feed._df.iloc[feed.warmup]
    assert str(feed._df.index[feed.warmup].date()) == "2024-07-01"
    assert first_day["returns"] == pytest.approx(0.0)
    assert first_day["ma_60"] == pytest.approx(5.0)


@pytest.mark.parametrize("allow_download", [True, False], ids=["download_fails", "no_download"])
def test_without_a_coherent_download_the_warmup_stops_at_the_seam(monkeypatch, allow_download):
    e = seamed_engine()
    calls = []

    def offline(*args):
        calls.append(args)
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(e._loader, "get_daily", offline)
    e.add_data("600000", "2024-07-01", "2024-09-30", adjust="qfq", warmup=60, allow_download=allow_download)
    e.add_strategy(Idle)
    result = e.run()

    assert len(calls) == (1 if allow_download else 0)
    feed = e._data_feeds["600000"]
    assert set(feed._df["close"]) == {5.0}                       # nothing from the old anchor
    assert str(feed._df.index[0].date()) == "2024-07-01"
    coverage = result.coverage["600000"]
    assert coverage.warmup_seam == "2024-07-01" and coverage.offline is False and coverage.warmup_rows == 0
    by_code = {c["code"]: c for c in result.caveats}
    assert by_code["warmup_seam"]["level"] == "warning" and "2024-07-01" in by_code["warmup_seam"]["text"]
    assert "只有 0 根" in by_code["warmup_short"]["text"]
    assert "offline_cache" not in by_code


# ------------------------------------------------------------------ #
#  Finding 12: a short warm-up history keeps every bar it has
# ------------------------------------------------------------------ #

def test_short_warmup_history_keeps_all_available_bars():
    # Reviewer's reproduction: 40 bars before the start and warmup=60 kept only
    # 20 (len(before) - warmup = -20), so trading began on Apr 27, not Mar 30,
    # and the warmup_short caveat reported 20 available bars.
    days = pd.bdate_range("2026-01-05", periods=120)
    start = days[40]
    e = engine(100_000)
    e.add_data("600000", start.strftime("%Y-%m-%d"), "", df=bars([10.0] * 120, start="2026-01-05"),
               adjust="qfq", warmup=60)
    e.add_strategy(Idle)

    info = e._warmup_info["600000"]
    assert info["available"] == 40
    assert info["first_date"] == days[60].strftime("%Y-%m-%d") == "2026-03-30"
    caveat = next(c for c in e.run().caveats if c["code"] == "warmup_short")
    assert "只有 40 根" in caveat["text"]


# ------------------------------------------------------------------ #
#  Finding 13: the engine finds a canonical cache through an alias
# ------------------------------------------------------------------ #

# Fixed when the engine started loading through BarRepository (canonical keys).
def test_engine_reads_the_canonical_cache_through_an_alias(monkeypatch):
    # Reviewer's reproduction: bars cached and covered under SHA:600519; the
    # repository serves load("600519") offline, the engine tried a download.
    e = BacktestEngine(initial_cash=100_000, broker_config=NO_FEES)
    cached = bars([10.0] * len(pd.bdate_range("2024-01-01", "2024-12-31")), start="2024-01-01")
    e._store.save_daily("SHA:600519", cached, "qfq")
    e._store.record_coverage("SHA:600519", "qfq", "2023-01-01", "2024-12-31")

    def offline(*args, **kwargs):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(e._loader, "get_daily", offline)
    e.add_data("600519", "2024-03-01", "2024-12-31", adjust="qfq", warmup=10)

    assert e._data_feeds["600519"].total_bars > 200


# ------------------------------------------------------------------ #
#  Engine wiring for findings 11, 14 and 15
# ------------------------------------------------------------------ #

def flat_run(adjust="qfq", symbol="600000"):
    e = engine(100_000)
    e.add_data(symbol, "", "", df=bars([10.0] * 70, start="2024-01-01"), adjust=adjust, warmup=0)
    e.add_strategy(Idle)
    return e.run()


def test_engine_result_reports_undefined_statistics_as_null_with_the_reason():
    result = flat_run()
    payload = result.to_dict()
    stats = result.stats.to_dict()
    assert payload["calmar_ratio"] is None and payload["win_rate"] is None
    assert payload["undefined"] == stats["undefined"]
    for key, value in stats.items():
        if key in payload and key != "undefined":
            assert payload[key] == value, key


def test_the_text_summary_shows_undefined_statistics_as_unavailable():
    lines = flat_run().summary().splitlines()
    for label in ("卡玛比率", "胜率", "夏普比率", "利润因子", "平均盈利"):
        line = next(line for line in lines if line.startswith(label))
        assert "不可用" in line and "0.00" not in line, line


def test_the_rule_based_reading_does_not_rate_a_missing_sharpe_as_zero():
    import asyncio

    from backtesting.reflection.llm_reflector import METRIC_KEYS, BacktestReflector, facts_from_result

    reflector = BacktestReflector(use_llm=False)
    payload = facts_from_result(flat_run())          # flat equity: the Sharpe denominator is 0
    assert payload["metrics"]["sharpe_ratio"] is None and "sharpe_ratio" in payload["metrics"]["undefined"]
    rating = next(line for line in reflector._rule_based_analysis(payload).splitlines() if "整体评级" in line)
    assert "不可用" in rating and "夏普比率" in rating and "⭐" not in rating
    assert payload["metrics"]["undefined"]["sharpe_ratio"] in rating

    # Every metric null (profit factor, win rate, drawdown ...): nothing formats a null.
    nulls = {"facts": {"strategy": {"name": "x"}}, "metrics": {key: None for key in METRIC_KEYS}}
    reading = asyncio.run(reflector.reflect_facts(nulls))
    assert "不可用" in reading["analysis"] and "年化收益" in reading["analysis"]
    assert reading["lessons"] == []


def test_the_headline_copes_with_statistics_that_have_no_value():
    from backtesting import summary

    # The engine never leaves the total return undefined (the headline needs it) ...
    assert flat_run().to_dict()["total_return"] == 0.0
    # ... every other headline input may be null and is then left out.
    facts = {"first_date": "2024-01-02", "last_date": "2024-12-31", "total_return": 0.05,
             "annualized_return": None, "same_stock_return": None, "index_name": "沪深300",
             "index_return": None, "index_reason": "沪深300 行情无法获取", "max_drawdown": None,
             "closed_trades": None, "winning_trades": None, "open_at_end": None, "fills": 3,
             "fees_total": None, "currency": "CNY"}
    text, missing = summary.headline(facts)
    assert "+5.0%" in text and "年化" not in text and "回撤" not in text and "沪深300" not in text
    assert {"same_stock_unavailable", "index_unavailable"} <= {c["code"] for c in missing}


def test_engine_result_of_an_unadjusted_run_carries_the_unadjusted_caveat():
    caveat = next(c for c in flat_run(adjust="").caveats if c["code"] == "unadjusted_prices")
    assert caveat["level"] == "warning"


def test_an_unadjusted_run_from_the_repository_states_it_once_as_a_warning(monkeypatch):
    # The repository path also produced summary.data_caveats' info-level line with the
    # same code; the warning is the one kept.
    e = BacktestEngine(initial_cash=100_000, broker_config=NO_FEES)
    raw = bars([10.0, 10.0, 9.5, 9.5], start="2024-01-02")       # a 0.50 dividend, unadjusted
    monkeypatch.setattr(e._loader, "get_daily", lambda symbol, start, end, adjust="qfq": raw.loc[start:end].copy())
    e.add_data("600000", "2024-01-02", "2024-01-05", adjust="", warmup=0)
    e.add_strategy(Idle)
    items = [c for c in e.run().caveats if c["code"] == "unadjusted_prices"]
    assert [c["level"] for c in items] == ["warning"]
    assert "600000" in items[0]["text"] and "分红" in items[0]["text"]


def test_engine_result_states_every_fee_item_that_is_not_modelled():
    result = flat_run()
    assumptions = result.to_dict()["assumptions"]
    assert assumptions["fees"] == fees_assumption_text()
    assert "HK ETF" in assumptions["fees"] and "TAF" in assumptions["fees"]
    # ... and the run's own market's limitation is a caveat.
    (item,) = [c for c in result.caveats if c["code"] == "fee_not_modelled"]
    assert item["text"].startswith("A股费用没有完全建模")
