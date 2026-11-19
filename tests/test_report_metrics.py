"""Snapshot metrics and the daily report, using fictional balances."""
import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import portfolio_report as report
import portfolio_snapshot as snapshot
from backend.api import portfolio_tracker as tracker


DAY = "2026-09-09"


def holdings(cash=1000, cost=1000, positions=()):
    return {"date": DAY, "groups": {"Account": {
        "positions": list(positions), "cash": cash, "fund": 0, "cost_basis": cost,
    }}}


def calculate(data, prices=None, history=None):
    return snapshot.calculate_snapshot(data, prices or {}, {}, {"CNY": 1}, None, [], history)


@pytest.fixture
def portfolio(tmp_path, monkeypatch):
    """A synthetic data directory shared by the snapshot CLI, the report and the tracker API."""
    for name in ("holdings", "snapshots"):
        (tmp_path / name).mkdir()
    (tmp_path / "config.json").write_text("{}")
    monkeypatch.setattr(snapshot, "PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(snapshot, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(report, "PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(tracker, "PORTFOLIO_DIR", str(tmp_path))
    monkeypatch.setattr(snapshot, "fetch_prices", lambda *args: ({"SHA:600000": 12}, {}))
    monkeypatch.setattr(snapshot, "fetch_fx_rates", lambda *args: {"CNY": 1.0, "USD": 7.0, "HKD": 0.9})
    return tmp_path


def run_snapshot_cli(monkeypatch, day=DAY):
    monkeypatch.setattr(sys, "argv", ["portfolio_snapshot.py", "--date", day])
    return snapshot.main()


def tracker_client():
    app = FastAPI()
    app.include_router(tracker.router, prefix="/api/tracker")
    return TestClient(app)


# ── Return on non-positive contributed capital ──

def test_return_is_unavailable_after_withdrawing_more_than_was_contributed():
    # Paid in 100k, grew to 300k, withdrew 150k: capital is -50k, assets 150k, profit 200k.
    result = calculate(holdings(cash=150000, cost=-50000))
    assert result["groups"]["Account"]["profit"] == 200000
    assert result["groups"]["Account"]["return_pct"] is None
    assert result["summary"]["total_profit"] == 200000
    assert result["summary"]["total_return_pct"] is None


def test_return_is_unavailable_without_contributed_capital_and_kept_when_positive():
    assert calculate(holdings(cash=500, cost=0))["summary"]["total_return_pct"] is None
    result = calculate(holdings(cash=1100, cost=1000))
    assert result["summary"]["total_return_pct"] == 10
    assert result["groups"]["Account"]["return_pct"] == 10


def test_one_account_without_capital_does_not_hide_the_total():
    data = holdings(cash=1100, cost=1000)
    data["groups"]["Gift"] = {"positions": [], "cash": 50, "fund": 0, "cost_basis": 0}
    result = calculate(data)
    assert result["groups"]["Gift"]["return_pct"] is None
    assert result["summary"]["total_return_pct"] == 15


def test_position_return_is_unavailable_for_non_positive_cost():
    position = {"name": "Example", "ticker": "SHA:600000", "quantity": 10}
    for cost_price in (0, -2):
        data = holdings(positions=[{**position, "cost_price": cost_price}])
        saved = calculate(data, {"SHA:600000": 12})["groups"]["Account"]["positions"][0]
        assert saved["profit_cny"] == 120 - cost_price * 10
        assert saved["profit_pct"] is None
    data = holdings(positions=[{**position, "cost_price": 10}])
    assert calculate(data, {"SHA:600000": 12})["groups"]["Account"]["positions"][0]["profit_pct"] == 20


def test_unavailable_returns_survive_the_cli_history_report_and_tracker_api(portfolio, monkeypatch, capsys):
    data = holdings(cash=150000, cost=-50000,
                    positions=[{"name": "Example", "ticker": "SHA:600000", "quantity": 10, "cost_price": 0}])
    (portfolio / "holdings" / f"{DAY}.json").write_text(json.dumps(data))
    (portfolio / "history.csv").write_text(
        "date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct,"
        "capital_change,market_daily_change,market_daily_change_pct\n"
        "2026-09-08,150000,-50000,200000,,0,0,0,0,0,0\n")
    run_snapshot_cli(monkeypatch)
    assert "None" not in capsys.readouterr().out
    saved = json.loads((portfolio / "snapshots" / f"{DAY}.json").read_text())
    assert saved["summary"]["total_return_pct"] is None
    rows = (portfolio / "history.csv").read_text().strip().split("\n")
    assert rows[-1].split(",")[:5] == [DAY, "150120.0", "-50000.0", "200120.0", ""]

    text = report.generate_report(saved, report.load_history_csv())
    assert "None" not in text
    assert "| 累计盈亏 | ¥20.01万 (—) |" in text
    assert "| Example | SHA:600000 | 10 | ¥12.00 | ¥120 | 0.1% | — |" in text

    client = tracker_client()
    history = client.get("/api/tracker/history").json()["history"]
    assert [row["return_pct"] for row in history] == [None, None]
    assert client.get("/api/tracker/summary").json()["total_return_pct"] is None
    (portfolio / "snapshots" / f"{DAY}.json").unlink()
    synthetic = client.get("/api/tracker/snapshot", params={"date": DAY}).json()
    assert synthetic["synthetic"] is True
    assert synthetic["summary"]["total_return_pct"] is None



# ── Percentage changes measured from a negative (net liability) value ──

PCT_KEYS = ("daily_change_pct", "market_daily_change_pct", "month_return_pct")


@pytest.mark.parametrize("previous", [-100000, 0])
def test_change_from_a_non_positive_value_has_no_percentage(previous):
    # Net liabilities shrink from `previous` to -90,000: a gain of 10,000 that a
    # ratio to a negative value would print as a loss.
    result = calculate(holdings(cash=-90000, cost=-50000),
                       history=[("2026-08-31", previous, -50000), ("2026-09-08", previous, -50000)])
    summary = result["summary"]
    assert summary["daily_change"] == summary["market_daily_change"] == -90000 - previous
    assert summary["month_market_change"] == -90000 - previous
    assert {key: summary[key] for key in PCT_KEYS} == dict.fromkeys(PCT_KEYS)


def test_change_from_a_positive_value_keeps_its_percentage():
    result = calculate(holdings(cash=110000, cost=100000),
                       history=[("2026-08-31", 100000, 100000), ("2026-09-08", 100000, 100000)])
    assert {key: result["summary"][key] for key in PCT_KEYS} == dict.fromkeys(PCT_KEYS, 10)


def test_unavailable_changes_survive_the_cli_history_report_and_tracker_api(portfolio, monkeypatch, capsys):
    (portfolio / "holdings" / f"{DAY}.json").write_text(json.dumps(holdings(cash=-90000, cost=-50000)))
    (portfolio / "history.csv").write_text(
        "date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct,"
        "capital_change,market_daily_change,market_daily_change_pct\n"
        "2026-09-08,-100000,-50000,-50000,,0,0,0,0,0,0\n")
    run_snapshot_cli(monkeypatch)
    assert "None" not in capsys.readouterr().out
    saved = json.loads((portfolio / "snapshots" / f"{DAY}.json").read_text())
    assert {key: saved["summary"][key] for key in PCT_KEYS} == dict.fromkeys(PCT_KEYS)
    header, _, row = (portfolio / "history.csv").read_text().strip().split("\n")
    fields = dict(zip(header.split(","), row.split(",")))
    assert (fields["daily_change"], fields["daily_change_pct"]) == ("10000.0", "")
    assert (fields["market_daily_change"], fields["market_daily_change_pct"]) == ("10000.0", "")

    text = report.generate_report(saved, report.load_history_csv())
    assert "None" not in text
    rows = overview(text)
    assert rows["日变动"] == "¥1.00万 (—)"
    assert rows["本月收益"] == "—"
    assert f"| {DAY} | ¥-9.00万 | — | — |" in text

    client = tracker_client()
    history = client.get("/api/tracker/history").json()["history"]
    assert (history[-1]["daily_change_pct"], history[-1]["market_daily_change_pct"]) == (None, None)
    summary = client.get("/api/tracker/summary").json()
    assert {key: summary[key] for key in PCT_KEYS} == dict.fromkeys(PCT_KEYS)
    (portfolio / "snapshots" / f"{DAY}.json").unlink()
    synthetic = client.get("/api/tracker/snapshot", params={"date": DAY}).json()["summary"]
    assert {key: synthetic[key] for key in PCT_KEYS} == dict.fromkeys(PCT_KEYS)


# ── Missing exchange rates in the report header ──

@pytest.mark.parametrize("rates, usd, hkd", [
    ({"CNY": 1}, "—", "—"),
    ({"CNY": 1, "USD": 7.1234567}, "7.1235", "—"),
    ({"CNY": 1, "USD": 7, "HKD": None}, "7.0000", "—"),
    (None, "—", "—"),
])
def test_report_header_tolerates_missing_exchange_rates(rates, usd, hkd):
    saved = calculate(holdings())
    if rates is None:
        del saved["fx_rates"]
    else:
        saved["fx_rates"] = rates
    text = report.generate_report(saved)
    assert f"**{DAY}**  |  汇率 USD/CNY={usd}  HKD/CNY={hkd}" in text


# ── Deposits and withdrawals are not market gains ──

def overview(text):
    """The rows of the report's first table, keyed by label."""
    rows = {}
    for line in text.split("\n## ")[1].split("\n"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 2:
            rows[cells[0]] = cells[1]
    return rows


def test_report_does_not_present_a_deposit_as_a_daily_gain():
    # A 1,000,000 portfolio receives a 100,000 deposit; prices do not move.
    saved = calculate(holdings(cash=1100000, cost=1100000), history=[("2026-09-08", 1000000, 1000000)])
    assert saved["summary"]["daily_change_pct"] == 10
    text = report.generate_report(saved)
    rows = overview(text)
    assert rows["日变动"] == "¥0.00万 (+0.00%)"
    assert rows["当日入金"] == "¥10.00万"
    assert "+10.00%" not in text
    assert text.split("\n## ")[1].startswith("📈")


def test_report_shows_a_market_loss_on_a_deposit_day_and_withdrawals_separately():
    loss = calculate(holdings(cash=1090000, cost=1100000), history=[("2026-09-08", 1000000, 1000000)])
    text = report.generate_report(loss)
    assert overview(text)["日变动"] == "¥-1.00万 (-1.00%)"
    assert overview(text)["当日入金"] == "¥10.00万"
    assert text.split("\n## ")[1].startswith("📉")
    withdrawal = calculate(holdings(cash=960000, cost=950000), history=[("2026-09-08", 1000000, 1000000)])
    rows = overview(report.generate_report(withdrawal))
    assert rows["日变动"] == "¥1.00万 (+1.00%)"
    assert rows["当日出金"] == "¥5.00万"


def test_report_has_no_flow_row_without_a_flow_and_reads_older_snapshots():
    saved = calculate(holdings(cash=1010000, cost=1000000), history=[("2026-09-08", 1000000, 1000000)])
    rows = overview(report.generate_report(saved))
    assert rows["日变动"] == "¥1.00万 (+1.00%)"
    assert "当日入金" not in rows and "当日出金" not in rows
    for key in ("market_daily_change", "market_daily_change_pct", "capital_change"):
        del saved["summary"][key]
    assert overview(report.generate_report(saved))["日变动"] == "¥1.00万 (+1.00%)"


def test_report_trend_uses_flow_adjusted_daily_changes():
    saved = calculate(holdings(cash=1100000, cost=1100000), history=[("2026-09-08", 1000000, 1000000)])
    history = [
        {"date": "2026-09-08", "total_value": "1000000", "return_pct": "0", "daily_change_pct": "0.5",
         "max_drawdown_pct": "0"},
        {"date": DAY, "total_value": "1100000", "return_pct": "0", "daily_change_pct": "10.0",
         "max_drawdown_pct": "0", "capital_change": "100000", "market_daily_change": "0", "market_daily_change_pct": "0.0"},
    ]
    text = report.generate_report(saved, history)
    assert "| 2026-09-08 | ¥100.00万 | +0.50% | +0.00% | 0.00% |" in text
    assert f"| {DAY} | ¥110.00万 | +0.00% | +0.00% | 0.00% |" in text
    assert "+10.00%" not in text


def test_snapshot_cli_summary_separates_the_deposit(portfolio, monkeypatch, capsys):
    (portfolio / "holdings" / f"{DAY}.json").write_text(json.dumps(holdings(cash=1100000, cost=1100000)))
    (portfolio / "history.csv").write_text(
        "date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct,"
        "capital_change,market_daily_change,market_daily_change_pct\n"
        "2026-09-08,1000000,1000000,0,0,0,0,0,0,0,0\n")
    run_snapshot_cli(monkeypatch)
    output = capsys.readouterr().out
    assert "日变动: ¥0.00万 (+0.00%)" in output
    assert "出入金: ¥10.00万" in output
    assert "+10.00%" not in output


# ── Month-to-date return with less than a month of history ──

MONTH_KEYS = ("month_start_value", "month_change", "month_market_change", "month_return_pct")


def synthetic_month(portfolio, rows):
    """The tracker API's month figures for the same history, from history.csv alone."""
    lines = ["date,total_value,total_cost,total_profit,return_pct,daily_change,daily_change_pct,max_drawdown_pct"]
    previous = None
    for day, value, cost in rows:
        change = value - previous if previous is not None else 0
        lines.append(f"{day},{value},{cost},{value - cost},0,{change},0,0")
        previous = value
    (portfolio / "history.csv").write_text("\n".join(lines) + "\n")
    summary = tracker._build_synthetic_snapshot(rows[-1][0])["summary"]
    return {key: summary[key] for key in MONTH_KEYS}


@pytest.mark.parametrize("history, today, expected", [
    # Tracking started this month: the month begins at the first recorded value, not yesterday's.
    ([("2026-09-01", 1000, 1000), ("2026-09-08", 2000, 1000)], (2200, 1000), (1000, 1200, 1200, 120)),
    # A mid-month deposit is not a return.
    ([("2026-09-01", 1000, 1000), ("2026-09-08", 2100, 2000)], (2310, 2000), (1000, 1310, 310, 31)),
    # With an earlier month on record nothing changes: the month begins at its last value.
    ([("2026-08-31", 1000, 1000), ("2026-09-08", 1100, 1000)], (1210, 1000), (1000, 210, 210, 21)),
    # The very first snapshot has no month-to-date change.
    ([], (1500, 1000), (1500, 0, 0, 0)),
])
def test_month_to_date_return_matches_the_definition_and_the_tracker_api(portfolio, history, today, expected):
    value, cost = today
    summary = calculate(holdings(cash=value, cost=cost), history=history)["summary"]
    assert tuple(summary[key] for key in MONTH_KEYS) == expected
    assert synthetic_month(portfolio, [*history, (DAY, value, cost)]) == dict(zip(MONTH_KEYS, expected))
