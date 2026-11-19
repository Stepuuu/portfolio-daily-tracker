"""Golden values for the dated fee schedules the simulated broker charges.

Turnovers are chosen so that every component is easy to check by hand. The
rates themselves come from broker/fee_schedules.json (see the source cited on
each row there).
"""
from types import SimpleNamespace

import pytest

from backtesting.broker.fees import compute_fees, default_schedule
from backtesting.broker.simulated import BrokerConfig, SimulatedBroker
from backtesting.strategies.base import Order


def fee(symbol, side, qty, price, when, **kwargs):
    return compute_fees(symbol, side, qty, price, when, **kwargs)


# ------------------------------------------------------------------ #
#  A-share stocks: commission (with its own minimum) + transfer fee,
#  stamp duty on sells only
# ------------------------------------------------------------------ #

def test_a_share_buy_is_commission_plus_transfer_fee():
    f = fee("600519", "buy", 100, 1680.0, "2024-01-02")   # turnover 168,000
    assert f.commission == 42.00          # 0.025% default
    assert f.transfer_fee == 1.68         # 0.001%
    assert f.stamp_duty == 0.0
    assert f.total == pytest.approx(43.68)
    assert f.assumed is False


@pytest.mark.parametrize("when,duty", [
    ("2023-08-25", 168.00),   # last trading day at 0.1%
    ("2023-08-28", 84.00),    # 0.05% from this day
    ("2008-09-19", 168.00),
])
def test_a_share_sell_stamp_duty_by_date(when, duty):
    f = fee("SHA:600519", "sell", 100, 1680.0, when)
    assert f.stamp_duty == duty


@pytest.mark.parametrize("when,transfer", [
    ("2022-04-28", 3.36),     # 0.002%
    ("2022-04-29", 1.68),     # 0.001% from this day
    ("2015-08-03", 3.36),
])
def test_a_share_transfer_fee_by_date(when, transfer):
    assert fee("600519", "buy", 100, 1680.0, when).transfer_fee == transfer


def test_a_share_sell_after_the_stamp_duty_cut_golden_total():
    f = fee("600519", "sell", 100, 1680.0, "2024-01-02")
    assert (f.commission, f.transfer_fee, f.stamp_duty) == (42.00, 1.68, 84.00)
    assert f.total == pytest.approx(127.68)


def test_minimum_commission_applies_to_the_commission_only():
    # Turnover 1,000: commission 0.25 -> minimum 5.00; stamp duty and transfer fee
    # are added on top instead of disappearing inside the minimum.
    f = fee("600000", "sell", 100, 10.0, "2024-01-02")
    assert f.commission == 5.00
    assert f.stamp_duty == 0.50
    assert f.transfer_fee == 0.01
    assert f.total == pytest.approx(5.51)


def test_commission_rate_and_minimum_come_from_the_caller():
    f = fee("600519", "buy", 100, 1680.0, "2024-01-02", commission_rate=0.0003, min_commission=0.0)
    assert f.commission == 50.40
    assert f.transfer_fee == 1.68
    f = fee("600000", "buy", 100, 10.0, "2024-01-02", commission_rate=0.0003, min_commission=0.0)
    assert f.commission == 0.30


@pytest.mark.parametrize("symbol", ["510300", "ETF:510300", "SHE:159915", "SHA:588000", "161725.SZ", "LOF:501018"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_exchange_traded_funds_pay_commission_only(symbol, side):
    f = fee(symbol, side, 10_000, 4.5, "2024-01-02")        # turnover 45,000
    assert f.commission == 11.25
    assert f.stamp_duty == 0.0 and f.transfer_fee == 0.0
    assert f.total == pytest.approx(11.25)


def test_before_the_earliest_row_uses_it_and_is_flagged():
    f = fee("600519", "sell", 100, 1680.0, "2015-07-31")    # before the transfer-fee table
    assert f.transfer_fee == 3.36                           # earliest row, 0.002%
    assert f.stamp_duty == 168.00
    assert f.assumed is True
    f = fee("600519", "sell", 100, 1680.0, "2008-09-18")    # before the stamp-duty table
    assert f.stamp_duty == 168.00 and f.assumed is True
    assert fee("510300", "sell", 1000, 4.5, "2001-01-02").assumed is False   # commission only


# ------------------------------------------------------------------ #
#  Hong Kong: stamp duty on both sides (rounded up to the dollar),
#  trading fee, SFC levy and AFRC levy on both sides
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("side", ["buy", "sell"])
def test_hk_golden_total(side):
    f = fee("HKG:00700", side, 100, 300.0, "2024-01-02")    # turnover 30,000 HKD
    assert f.commission == 7.50                             # 0.025% default
    assert f.stamp_duty == 30.00                            # 0.1%
    assert f.trading_fee == 1.70                            # 0.00565% = 1.695
    assert f.sfc_levy == 0.81                               # 0.0027%
    assert f.afrc_levy == 0.05                              # 0.00015% = 0.045
    assert f.total == pytest.approx(40.06)


@pytest.mark.parametrize("when,duty", [
    ("2021-07-30", 30.00),    # 0.1%
    ("2021-08-02", 39.00),    # 0.13% from 2021-08-01
    ("2023-11-16", 39.00),
    ("2023-11-17", 30.00),    # 0.1% again from 2023-11-17
])
def test_hk_stamp_duty_by_date(when, duty):
    assert fee("00700", "buy", 100, 300.0, when).stamp_duty == duty


def test_hk_stamp_duty_is_rounded_up_to_the_dollar():
    assert fee("00700", "buy", 100, 305.5, "2024-01-02").stamp_duty == 31.00   # 30.55


def test_hk_trading_fee_and_afrc_levy_by_date():
    assert fee("00700", "buy", 100, 300.0, "2022-12-30").trading_fee == 1.50  # 0.005%
    assert fee("00700", "buy", 100, 300.0, "2023-01-03").trading_fee == 1.70
    assert fee("00700", "buy", 100, 300.0, "2021-12-31").afrc_levy == 0.0     # no levy yet
    assert fee("00700", "buy", 100, 300.0, "2022-01-03").afrc_levy == 0.05


def test_hk_before_the_earliest_row_is_flagged():
    assert fee("00700", "buy", 100, 300.0, "2014-06-03").assumed is True
    assert fee("00700", "buy", 100, 300.0, "2015-06-03").assumed is False


# ------------------------------------------------------------------ #
#  US: commission default 0, SEC fee on sells (rounded up to the cent)
# ------------------------------------------------------------------ #

def test_us_buy_costs_nothing_by_default():
    f = fee("NASDAQ:AAPL", "buy", 100, 200.0, "2024-06-03")
    assert f.total == 0.0 and f.commission == 0.0


@pytest.mark.parametrize("when,sec", [
    ("2024-05-21", 0.16),     # $8.00 per million
    ("2024-06-03", 0.56),     # $27.80 per million: 0.556 rounded up
    ("2025-05-14", 0.00),     # $0.00 per million
])
def test_us_sec_fee_on_sells_by_date(when, sec):
    f = fee("AAPL", "sell", 100, 200.0, when)
    assert f.sec_fee == sec
    assert f.total == pytest.approx(sec)


def test_us_before_the_earliest_row_is_flagged():
    f = fee("AAPL", "sell", 100, 200.0, "2020-06-01")
    assert f.sec_fee == 0.11                                 # earliest row, $5.10 per million
    assert f.assumed is True


def test_schedule_is_versioned_and_every_row_cites_a_source():
    schedule = default_schedule()
    assert schedule.version
    rows = [row for market in schedule.data["markets"].values()
            for component in market["components"].values() for row in component["rows"]]
    assert rows and all(row.get("source") and "verify" in row["source"] for row in rows)


# ------------------------------------------------------------------ #
#  The broker charges the schedule, not one A-share profile
# ------------------------------------------------------------------ #

def bar(date, price, volume=1_000_000):
    return SimpleNamespace(date=date, open=price, high=price * 1.01, low=price * 0.99,
                           close=price, volume=volume, prev_close=price)


def order(symbol, direction, qty):
    return Order(order_id=f"{direction}-{symbol}", symbol=symbol, direction=direction,
                 order_type="market", quantity=qty, price=0.0, timestamp="2024-01-01")


def test_broker_charges_dated_a_share_fees_on_a_round_trip():
    broker = SimulatedBroker(initial_cash=200_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(order("600519", "buy", 100))
    trades, _ = broker.process_bar("600519", bar("2024-01-02", 1680.0))
    assert trades[0].commission == pytest.approx(43.68)
    assert broker.cash == pytest.approx(200_000 - 168_000 - 43.68)

    broker.submit_order(order("600519", "sell", 100))
    trades, _ = broker.process_bar("600519", bar("2024-01-03", 1680.0))
    assert trades[0].commission == pytest.approx(127.68)
    assert trades[0].fees["stamp_duty"] == pytest.approx(84.00)
    assert broker.cash == pytest.approx(200_000 - 43.68 - 127.68)


def test_existing_commission_fields_set_the_commission_part_only():
    config = BrokerConfig(slippage_pct=0.0, commission_buy=0.0003, commission_sell=0.0003, min_commission=5.0)
    broker = SimulatedBroker(initial_cash=200_000, config=config)
    broker.submit_order(order("600519", "buy", 100))
    broker.process_bar("600519", bar("2024-01-02", 1680.0))
    broker.submit_order(order("600519", "sell", 100))
    trades, _ = broker.process_bar("600519", bar("2024-01-03", 1680.0))
    # 50.40 commission + 84.00 stamp duty + 1.68 transfer fee
    assert trades[0].commission == pytest.approx(136.08)


def test_broker_us_sell_pays_only_the_sec_fee_by_default():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(order("AAPL", "buy", 100))
    trades, _ = broker.process_bar("AAPL", bar("2024-06-03", 200.0))
    assert trades[0].commission == 0.0
    broker.submit_order(order("AAPL", "sell", 100))
    trades, _ = broker.process_bar("AAPL", bar("2024-06-04", 200.0))
    assert trades[0].commission == pytest.approx(0.56)
    assert broker.cash == pytest.approx(100_000 - 0.56)


def test_broker_hk_buy_pays_stamp_duty():
    broker = SimulatedBroker(initial_cash=100_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(order("HKG:00700", "buy", 100))
    trades, _ = broker.process_bar("HKG:00700", bar("2024-01-02", 300.0))
    assert trades[0].fees["stamp_duty"] == pytest.approx(30.00)
    assert trades[0].commission == pytest.approx(40.06)


def test_forced_final_close_pays_the_dated_sell_fees():
    broker = SimulatedBroker(initial_cash=200_000, config=BrokerConfig(slippage_pct=0.0))
    broker.submit_order(order("600519", "buy", 100))
    broker.process_bar("600519", bar("2024-01-02", 1680.0))
    trade = broker.force_close("600519", 1680.0, "2024-01-03")
    assert trade.commission == pytest.approx(127.68)
