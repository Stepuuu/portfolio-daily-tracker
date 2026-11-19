"""Golden values for the fee estimator."""
from datetime import date
from decimal import ROUND_FLOOR, Decimal as D, localcontext

import pytest

from core import fees

PRESET = {"is_default": False, "broker": "",
          "a_share": {"commission_rate": "0.00025", "commission_min": "5", "waive_minimum": False},
          "hk": None, "us": None}


def profile(**changes):
    out = {**PRESET, "a_share": dict(PRESET["a_share"])}
    for key, value in changes.items():
        if key == "a_share":
            out["a_share"].update(value)
        else:
            out[key] = value
    return out


def parts(result):
    return {p["label"]: p["amount"] for p in result.parts}


def est(ticker="SHA:600519", side="buy", qty="100", price="1680.00", when="2026-11-14", **kw):
    return fees.estimate(profile(**kw), ticker, side, D(qty), D(price), when)


def test_buy_golden():
    r = est()
    assert parts(r) == {"佣金": D("42.00"), "过户费": D("1.68")}
    assert r.total == D("43.68") and r.basis == "按账户费率估算" and r.reason is None


def test_sell_adds_stamp_duty():
    r = est(side="sell")
    assert parts(r) == {"佣金": D("42.00"), "过户费": D("1.68"), "印花税": D("84.00")}
    assert r.total == D("127.68")


def test_minimum_commission_five_yuan():
    r = est(qty="100", price="10")          # turnover 1000 -> 0.25 -> 5.00
    assert parts(r)["佣金"] == D("5.00")


def test_waive_five_removes_minimum():
    r = est(qty="100", price="10", a_share={"waive_minimum": True})
    assert parts(r)["佣金"] == D("0.25")


def test_half_up_at_the_cent_boundary():
    r = est(qty="100", price="0.2", a_share={"waive_minimum": True})   # 20 x 0.00025 = 0.005
    assert parts(r)["佣金"] == D("0.01")
    assert parts(r)["过户费"] == D("0.00")   # 20 x 0.00001 = 0.0002
    r = est(qty="1000", price="1.5", side="sell", a_share={"waive_minimum": True})
    assert parts(r)["印花税"] == D("0.75")


def test_each_part_rounded_before_summing():
    r = est(qty="100", price="1.01", a_share={"waive_minimum": True})   # 101: .02525 -> .03, .00101 -> .00
    assert parts(r) == {"佣金": D("0.03"), "过户费": D("0.00")} and r.total == D("0.03")


@pytest.mark.parametrize("when,duty", [
    ("2023-08-27", "168.00"),   # 0.1%
    ("2023-08-28", "84.00"),    # 0.05% from this day
    ("2015-08-01", "168.00"),
])
def test_stamp_duty_date_boundaries(when, duty):
    assert parts(est(side="sell", when=when))["印花税"] == D(duty)


@pytest.mark.parametrize("when,transfer", [
    ("2022-04-28", "3.36"),
    ("2022-04-29", "1.68"),
    ("2015-08-01", "3.36"),
])
def test_transfer_fee_date_boundaries(when, transfer):
    assert parts(est(when=when))["过户费"] == D(transfer)


def test_before_rate_tables_no_estimate():
    assert est(when="2015-07-31").total is None
    r = est(side="sell", when="2008-09-18", ticker="SHA:600519")
    assert r.total is None and r.reason


def test_date_accepts_date_object():
    assert fees.estimate(profile(), "SHA:600519", "buy", 100, "1680", date(2026, 11, 14)).total == D("43.68")


@pytest.mark.parametrize("ticker", ["SHA:510300", "SHE:159915", "SHA:588000", "SHE:161725", "SHA:501018"])
@pytest.mark.parametrize("side", ["buy", "sell"])
def test_funds_pay_commission_only(ticker, side):
    r = est(ticker=ticker, side=side, qty="1000", price="4.5")      # 4500 -> 1.125 -> min 5
    assert parts(r) == {"佣金": D("5.00")}
    r = est(ticker=ticker, side=side, qty="100000", price="4.5", a_share={"waive_minimum": True})
    assert parts(r) == {"佣金": D("112.50")}


@pytest.mark.parametrize("ticker,kind", [
    ("SHA:600519", "stock"), ("SHA:688981", "stock"), ("SHE:000001", "stock"), ("SHE:300750", "stock"),
    ("SHA:510300", "fund"), ("SHE:159915", "fund"), ("SHE:163402", "fund"),
    ("SHA:113050", None), ("SHE:127045", None), ("SHA:900901", None), ("SHE:200002", None),
    ("SHA:508000", None), ("SHE:399001", None), ("SHA:60051", None), ("SHA:ABCDEF", None),
])
def test_classification(ticker, kind):
    assert fees.classify(ticker)[0] == kind


@pytest.mark.parametrize("ticker", ["SHA:520990", "SHA:520600", "SHE:158001",   # ETFs the reviewer listed
                                    "SHA:530000", "SHA:551000", "SHE:150001", "SHE:169999"])
def test_every_exchange_traded_fund_range_pays_commission_only(ticker):
    assert fees.classify(ticker)[0] == "fund"
    r = est(ticker=ticker, side="sell", qty="1000", price="4.5")
    assert parts(r) == {"佣金": D("5.00")} and r.total == D("5.00")


@pytest.mark.parametrize("ticker, what", [
    ("SHA:508000", "REIT"), ("SHA:500001", "封闭式基金"), ("SHA:505888", "封闭式基金"),
    ("SHE:180101", "REIT"), ("SHE:184801", "封闭式基金"),
])
def test_funds_with_other_rules_keep_no_estimate(ticker, what):
    kind, note = fees.classify(ticker)
    assert kind is None and what in note
    assert est(ticker=ticker).total is None


@pytest.mark.parametrize("ticker", ["SHA:700001", "SHA:400001", "SHE:900001", "SHE:500001", "SHE:600001",
                                    "SHA:159915", "SHA:5", "SHA:5000001"])
def test_unknown_codes_have_no_estimate(ticker):
    kind, note = fees.classify(ticker)
    assert kind is None and note
    assert est(ticker=ticker).total is None


def test_classification_table_is_explicit_and_longest_prefix_wins():
    rows = fees.CLASSIFICATION
    assert all(exchange in ("SHA", "SHE") and prefix.isdigit() and 1 <= len(prefix) <= 3
               and kind in (None, "stock", "fund") and what for exchange, prefix, kind, what in rows)
    assert len({(exchange, prefix) for exchange, prefix, _kind, _what in rows}) == len(rows)
    assert ("SHA", "5", "fund") in [row[:3] for row in rows] and ("SHA", "508", None) in [row[:3] for row in rows]


@pytest.mark.parametrize("ticker", ["SHA:113050", "SHE:127045", "SHA:900901", "SHA:508000"])
def test_unclassifiable_gives_none_with_reason(ticker):
    r = est(ticker=ticker)
    assert r.total is None and r.parts == [] and r.reason


HK = {"hk": {"buy": {"rate": "0.0003", "min": "3", "fixed": "15"},
             "sell": {"rate": "0.0003", "min": "3", "fixed": "15"}}}


def test_hk_without_profile_has_no_estimate():
    r = est(ticker="HKG:0700", qty="100", price="300")
    assert r.total is None and r.reason == "未设置该市场费率"
    assert est(ticker="NASDAQ:AAPL").reason == "未设置该市场费率"
    assert est(ticker="NYSE:KO", **{"hk": HK["hk"]}).total is None


def test_hk_with_profile():
    r = est(ticker="HKG:0700", qty="100", price="300", **HK)           # 30000 x .0003 = 9, + 15
    assert parts(r) == {"佣金": D("9.00"), "固定费用": D("15.00")} and r.total == D("24.00")
    r = est(ticker="HKG:0700", qty="10", price="30", **HK)             # 300 -> .09 -> min 3
    assert r.total == D("18.00")


def test_us_with_profile_per_side():
    us = {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": {"rate": "0.001", "min": "1", "fixed": "0"}}
    assert est(ticker="NASDAQ:AAPL", qty="10", price="200", us=us).total == D("1.00")
    r = est(ticker="NASDAQ:AAPL", side="sell", qty="10", price="200", us=us)
    assert r.total == D("2.00") and parts(r) == {"佣金": D("2.00")}


@pytest.mark.parametrize("us", [
    {"buy": {"rate": "0", "min": "0", "fixed": "1"}},                    # no sell side
    {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": None},
    {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": {}},
    {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": {"fixed": "1"}},  # stored by hand, incomplete
    {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": {"rate": "0.001", "min": "1", "fixed": None}},
    {},
])
def test_side_without_a_complete_model_has_no_estimate(us):
    r = est(ticker="NASDAQ:AAPL", side="sell", qty="10", price="200", us=us)
    assert r.total is None and r.parts == [] and r.reason


def test_one_sided_model_still_estimates_its_side():
    us = {"buy": {"rate": "0", "min": "0", "fixed": "1"}, "sell": None}
    assert est(ticker="NASDAQ:AAPL", side="buy", qty="10", price="200", us=us).total == D("1.00")


def test_turnover_is_multiplied_exactly():
    # turnover = 1500000010149.9999999999999999 has 29 digits; rounding it to the
    # 28 digits of the default context gives ...0150.000 and a commission one cent too high.
    r = est(ticker="SHA:510300", qty="15000000099.99999999", price="100.00000001",
            a_share={"commission_rate": "0.0001", "waive_minimum": True})
    assert parts(r) == {"佣金": D("150000001.01")}


def test_estimate_ignores_the_ambient_decimal_context():
    expected = [est(qty="12345", price="1680.37", side=side).to_dict() for side in ("buy", "sell")]
    expected.append(est(ticker="HKG:0700", qty="12345", price="301.7", **HK).to_dict())
    with localcontext() as context:
        context.prec, context.rounding = 5, ROUND_FLOOR
        seen = [est(qty="12345", price="1680.37", side=side).to_dict() for side in ("buy", "sell")]
        seen.append(est(ticker="HKG:0700", qty="12345", price="301.7", **HK).to_dict())
    assert seen == expected


def test_default_profile_basis_label():
    r = fees.estimate({**PRESET, "is_default": True}, "SHA:600519", "buy", 100, "1680", "2026-11-14")
    assert r.basis == "按默认费率估算"


@pytest.mark.parametrize("args", [("SHA:600519", "hold", 1, 1, "2026-01-01"), ("SHA:600519", "buy", 0, 1, "2026-01-01"),
                                  ("SHA:600519", "buy", 1, -1, "2026-01-01"), ("SHA:600519", "buy", "x", 1, "2026-01-01"),
                                  ("SHA:600519", "buy", 1, 1, "2026-13-01")])
def test_bad_arguments_raise_value_error(args):
    with pytest.raises(ValueError):
        fees.estimate(profile(), *args)


@pytest.mark.parametrize("qty, price", [
    (D("1e30"), "10"), (D("1e1000000"), "10"), (D("1e-1000000"), "10"), ("1" * 100, "10"),
    ("100", D("1e-1000000")), ("100", D("1e30")), (10 ** 40, "10"), (1.5, "10"),
])
def test_quantity_and_price_are_bounded(qty, price):
    with pytest.raises(ValueError):
        fees.estimate(profile(), "SHA:600519", "buy", qty, price, "2026-11-14")


@pytest.mark.parametrize("a_share", [
    {"commission_rate": "1e-1000000"}, {"commission_rate": "0." + "0" * 60 + "1"},
    {"commission_rate": "1e30"}, {"commission_min": "1e1000000"}, {"commission_min": "-1"},
])
def test_profile_values_given_to_the_estimator_are_bounded(a_share):
    with pytest.raises(ValueError):
        est(a_share=a_share)


@pytest.mark.parametrize("hk", [{"buy": {"rate": "1e-1000000", "min": "0", "fixed": "0"}},
                                {"buy": {"rate": "0", "min": "0", "fixed": "1e1000000"}}])
def test_hk_model_values_given_to_the_estimator_are_bounded(hk):
    with pytest.raises(ValueError):
        est(ticker="HKG:0700", hk=hk)


def test_largest_accepted_trade_is_exact():
    r = est(ticker="SHA:600519", side="sell", qty="999999999999.99999999", price="999999999.99999999",
            a_share={"waive_minimum": True})
    assert r.total is not None and all(part["amount"].as_tuple().exponent == -2 for part in r.parts)


def test_to_dict_uses_decimal_strings():
    d = est().to_dict()
    assert d["total"] == "43.68" and d["parts"][0] == {"label": "佣金", "amount": "42.00"}
