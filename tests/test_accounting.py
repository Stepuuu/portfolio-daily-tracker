import copy
import json
from types import SimpleNamespace

import pytest
import portfolio_snapshot as snapshot
from portfolio_accounting import cash_balances, set_cash_balance, value_cash


def holdings(cash=1000, cost=1000):
    return {"date": "2026-09-09", "groups": {"Account": {
        "positions": [], "cash": cash, "fund": 0, "cost_basis": cost,
    }}}


def calculate(data, prices=None, rates=None, previous=None, history=None):
    return snapshot.calculate_snapshot(data, prices or {}, {}, rates or {"CNY": 1}, previous, [], history)


def test_multicurrency_cash_and_legacy_compatibility():
    data = holdings()
    group = data["groups"]["Account"]
    set_cash_balance(group, 100, "USD")
    set_cash_balance(group, -200, "HKD")
    result = calculate(data, rates={"USD": 7, "HKD": .9})
    assert result["summary"]["total_value"] == 1520
    assert result["groups"]["Account"]["cash"] == 1520
    assert result["groups"]["Account"]["cash_balances"] == {"CNY": 1000, "USD": 100, "HKD": -200}
    assert group["cash"] == 1000  # holdings retain native CNY, not the converted total
    assert calculate(holdings())["summary"]["total_value"] == 1000


def test_cny_update_preserves_foreign_cash():
    group = {"cash": 100}
    set_cash_balance(group, 20, "USD")
    set_cash_balance(group, 300)
    assert cash_balances(group) == {"CNY": 300, "USD": 20}


def test_fx_changes_affect_returns_not_external_flows():
    data = holdings(cash=0, cost=700)
    set_cash_balance(data["groups"]["Account"], 100, "USD")
    previous = {"date": "2026-09-08", "summary": {"total_value": 700, "total_cost": 700}}
    result = calculate(data, rates={"USD": 7.2}, previous=previous)["summary"]
    assert result["market_daily_change"] == 20
    assert result["capital_change"] == 0


@pytest.mark.parametrize("amount", [float('nan'), float('inf'), True, "10"])
def test_invalid_cash_is_rejected(amount):
    with pytest.raises(ValueError):
        value_cash({"cash": amount}, {"CNY": 1})


def test_missing_fx_and_ambiguous_cash_are_rejected():
    with pytest.raises(ValueError):
        value_cash({"cash_balances": {"USD": 20}}, {})
    with pytest.raises(ValueError):
        value_cash({"cash": 200, "cash_balances": {"CNY": 100}}, {"CNY": 1})


@pytest.mark.parametrize("price", [None, 0, -1, float('nan'), float('inf')])
def test_missing_or_invalid_price_cannot_zero_out_position(price):
    data = holdings()
    data["groups"]["Account"]["positions"] = [
        {"name": "Example", "ticker": "SHA:600000", "quantity": 10, "cost_price": 10}
    ]
    with pytest.raises(ValueError):
        calculate(data, {"SHA:600000": price})


def test_buying_stock_is_not_external_cash_withdrawal():
    previous = calculate(holdings())
    previous["date"] = "2026-09-08"
    data = holdings(cash=500)
    data["groups"]["Account"]["positions"] = [
        {"name": "Example", "ticker": "SHA:600000", "quantity": 50, "cost_price": 10}
    ]
    summary = calculate(data, {"SHA:600000": 10}, previous=previous)["summary"]
    assert summary["capital_change"] == 0
    assert summary["market_daily_change"] == 0


def test_drawdown_survives_recovery_and_ignores_deposits():
    summary = calculate(holdings(cash=2400, cost=2000), history=[
        ("2026-09-07", 1000, 1000), ("2026-09-08", 800, 1000)
    ])["summary"]
    assert summary["max_drawdown_pct"] == -20
    assert summary["current_drawdown_pct"] == 0
    deposit = calculate(holdings(cash=2000, cost=2000), history=[
        ("2026-09-08", 1000, 1000)
    ])["summary"]
    assert deposit["market_daily_change"] == 0
    assert deposit["max_drawdown_pct"] == 0


def test_out_of_order_history_has_correct_previous_day():
    summary = calculate(holdings(cash=1100), history=[
        ("2026-09-08", 1050, 1000), ("2026-09-07", 1000, 1000),
        ("2026-09-10", 9000, 9000), ("2026-09-09", 9999, 1000),
    ])["summary"]
    assert summary["prev_date"] == "2026-09-08"
    assert summary["market_daily_change"] == 50


def test_dry_run_loading_does_not_create_holdings(tmp_path, monkeypatch):
    folder = tmp_path / "holdings"
    folder.mkdir()
    (folder / "2026-09-08.json").write_text(json.dumps(holdings()))
    monkeypatch.setattr(snapshot, "PORTFOLIO_DIR", str(tmp_path))
    data = snapshot.load_holdings("2026-09-09", {}, persist=False)
    assert data["date"] == "2026-09-09"
    assert not (folder / "2026-09-09.json").exists()


def test_sync_uses_weighted_cost_and_available_quantity(tmp_path):
    first = {"ticker": "SHA:600000", "name": "Example", "quantity": 100, "cost_price": 10}
    second = {**first, "quantity": 200, "cost_price": 16}
    source = {"generated_at": "2026-09-09", "groups": {
        "First": {"positions": [first], "cash": 1},
        "Second": {"positions": [second], "cash": 2},
    }}
    path = tmp_path / "portfolio.json"
    snapshot.sync_to_qr(source, {"qr_portfolio_path": str(path)})
    saved = json.loads(path.read_text())
    assert saved["positions"][0]["cost_price"] == 14
    assert saved["positions"][0]["quantity"] == 300
    assert saved["positions"][0]["available_qty"] == 300


def test_cash_cli_round_trip(tmp_path, monkeypatch):
    import portfolio_manager as manager
    folder = tmp_path / "holdings"
    folder.mkdir()
    path = folder / "2026-09-09.json"
    path.write_text(json.dumps(holdings()))
    monkeypatch.setattr(manager, "PORTFOLIO_DIR", str(tmp_path))
    manager.cmd_set_cash(SimpleNamespace(date="2026-09-09", group="Account", currency="USD", value=25))
    manager.cmd_set_cash(SimpleNamespace(date="2026-09-09", group="Account", currency="CNY", value=900))
    data = json.loads(path.read_text())
    assert calculate(data, rates={"USD": 7})["groups"]["Account"]["cash"] == 1075
